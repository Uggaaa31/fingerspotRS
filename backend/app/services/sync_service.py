"""
app/services/sync_service.py — Layanan Sinkronisasi Biometrik & Pengguna Antar-Mesin Fingerspot

Mendukung protokol EBKN FkWeb (FKDataHS103):
1. Parsing hybrid payload (4-byte LE length prefix + JSON + binary template)
2. Intersepsi 'realtime_enroll_data' saat ada pendaftaran sidik jari/wajah/user baru di Hardware 1
3. Enkripsi template biometrik menggunakan Fernet (kepatuhan UU PDP)
4. Replikasi otomatis ke seluruh mesin Fingerspot lain (Hardware 2, 3, dst) melalui antrean perintah
5. Pengiriman perintah saat mesin polling 'receive_cmd'
6. Konfirmasi hasil eksekusi melalui 'send_cmd_result'
"""
import struct
import json
import base64
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List

from cryptography.fernet import Fernet
from app.config import settings

logger = logging.getLogger(__name__)

# Enkripsi biometrik sesuai standar UU PDP
cipher_suite = Fernet(settings.encryption_key.encode())


def extract_json_and_binary(raw_bytes: bytes) -> Tuple[Optional[Dict[str, Any]], bytes]:
    """
    Mengekstrak JSON metadata dan sisa binary blob dari payload EBKN FkWeb / FKDataHS103.
    Format payload: [4-byte uint32 LE length] + [JSON string] + [binary data / null terminator]
    """
    if not raw_bytes:
        return None, b""

    # Metode 1: 4-byte Little Endian length prefix
    if len(raw_bytes) >= 4:
        try:
            json_len = struct.unpack("<I", raw_bytes[:4])[0]
            if 0 < json_len <= len(raw_bytes) - 4:
                json_bytes = raw_bytes[4 : 4 + json_len]
                parsed = json.loads(json_bytes.decode("utf-8", errors="replace"))
                binary_tail = raw_bytes[4 + json_len :]
                return parsed, binary_tail
        except Exception:
            pass

    # Metode 2: Tracking Brace Depth (menghindari kesalahan jika '}' ada di dalam binary template tail)
    try:
        decoded_latin = raw_bytes.decode("latin-1", errors="ignore")
        start = decoded_latin.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escaped = False
            json_end_idx = -1

            for i in range(start, len(decoded_latin)):
                ch = decoded_latin[i]
                if escaped:
                    escaped = False
                    continue
                if in_string:
                    if ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_end_idx = i
                        break

            if json_end_idx != -1:
                json_str = decoded_latin[start : json_end_idx + 1]
                parsed = json.loads(json_str)
                binary_tail = raw_bytes[json_end_idx + 1 :]
                return parsed, binary_tail
    except Exception as e:
        logger.warning(f"[SYNC] Gagal parse JSON payload: {e}")

    return None, b""


def format_command_response_body(body_value: Any) -> bytes:
    """
    Format payload respon perintah untuk mesin Fingerspot EBKN FkWeb:
    [4-byte Little-Endian uint32 length] + [JSON bytes] + [null terminator b'\\x00']
    """
    if body_value is None:
        return b""
    if isinstance(body_value, bytes):
        return body_value

    if isinstance(body_value, (dict, list)):
        json_str = json.dumps(body_value, ensure_ascii=False)
    elif isinstance(body_value, str):
        json_str = body_value
    else:
        json_str = str(body_value)

    json_bytes = json_str.encode("utf-8") + b"\n\x00"
    prefix = struct.pack("<I", len(json_bytes))
    return prefix + json_bytes


def rewrite_enroll_payload_for_slave(raw_body: bytes) -> bytes:
    parsed_json, binary_tail = extract_json_and_binary(raw_body)
    if not parsed_json:
        return raw_body
    
    parsed_json["cmd_code"] = "SET_ENROLL_DATA"
    
    # Revo WFV-208BNC dan firmware FkWeb BNC mewajibkan string "USER" atau "ADMIN"
    priv_val = parsed_json.get("user_privilege", "USER")
    parsed_json["user_privilege"] = "ADMIN" if str(priv_val).upper() in ["ADMIN", "14"] else "USER"
    
    new_json_bytes = json.dumps(parsed_json, ensure_ascii=False).encode("utf-8")
    new_prefix = struct.pack("<I", len(new_json_bytes))
    return new_prefix + new_json_bytes + binary_tail

async def handle_realtime_enroll_data(
    cur,
    source_device_sn: str,
    raw_body: bytes,
    parsed_json: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Dipanggil saat ada pendaftaran user / sidik jari / wajah baru pada sebuah mesin fisik (source_device_sn).
    
    Langkah:
    1. Simpan user ke tabel `users` dan `pin_employee_map`.
    2. Simpan template biometrik terenkripsi ke `biometric_templates`.
    3. REPLIKASI OTOMATIS: Buat antrean perintah ke SELURUH mesin lain (WHERE device_sn != source_device_sn)
       agar data langsung terinstal di seluruh hardware finger!
    """
    if not parsed_json:
        parsed_json, _ = extract_json_and_binary(raw_body)

    if not parsed_json:
        logger.warning(f"[SYNC] Pendaftaran enroll dari {source_device_sn} tanpa JSON valid!")
        return {"status": "error", "message": "No JSON found"}

    raw_pin = str(parsed_json.get("user_id") or "").strip()
    if not raw_pin:
        logger.warning(f"[SYNC] Pendaftaran enroll dari {source_device_sn} tanpa user_id!")
        return {"status": "error", "message": "No user_id"}

    norm_pin = raw_pin.lstrip("0") or "0"
    user_name = str(parsed_json.get("user_name") or "").strip()
    privilege = parsed_json.get("user_privilege", 0)
    backup_num = parsed_json.get("backup_number", 0)  # ID sidik jari (0-9) atau wajah (20-21)
    enroll_type = str(parsed_json.get("enroll_data_type") or "FP").upper()

    logger.info(
        f"[SYNC ENROLL DETECTED] Sumber={source_device_sn} | PIN={raw_pin} (norm={norm_pin}) | "
        f"Nama={user_name or '(belum diisi)'} | Tipe={enroll_type} | BackupNum={backup_num}"
    )

    # 1. Simpan / Perbarui Data Pegawai di DB Lokal
    # Periksa apakah user sudah ada
    await cur.execute(
        "SELECT user_id, display_name FROM users WHERE national_id_number = %s OR national_id_number = %s LIMIT 1",
        (raw_pin, norm_pin),
    )
    user_row = await cur.fetchone()

    user_id = None
    if user_row:
        user_id = user_row[0]
        # TIDAK MELAKUKAN UPDATE ATAU INSERT KE TABEL users KARENA MILIK SATUTALENTA

    if user_id:
        # Mapping PIN ke employee HANYA JIKA user_id ditemukan di database SatuTalenta
        await cur.execute(
            "INSERT INTO pin_employee_map (pin, employee_id) VALUES (%s, %s) ON DUPLICATE KEY UPDATE employee_id = %s",
            (raw_pin, user_id, user_id),
        )
        if norm_pin != raw_pin:
            await cur.execute(
                "INSERT INTO pin_employee_map (pin, employee_id) VALUES (%s, %s) ON DUPLICATE KEY UPDATE employee_id = %s",
                (norm_pin, user_id, user_id),
            )

    # 2. Simpan Template Biometrik Terenkripsi (UU PDP)
    if raw_body and len(raw_body) > 10:
        try:
            # Enkripsi template mentah dengan Fernet
            encrypted_payload = cipher_suite.encrypt(raw_body).decode("utf-8")
            await cur.execute(
                """
                INSERT INTO biometric_templates 
                    (pin, finger_id, template_type, valid, template_data)
                VALUES (%s, %s, %s, 1, %s)
                ON DUPLICATE KEY UPDATE 
                    template_data = VALUES(template_data), 
                    valid = 1, 
                    updated_at = NOW()
                """,
                (norm_pin, backup_num, enroll_type, encrypted_payload),
            )
            logger.info(f"[SYNC] Template biometrik PIN={norm_pin} (Tipe={enroll_type}) berhasil dienkripsi dan disimpan.")
        except Exception as e:
            logger.error(f"[SYNC] Gagal mengenkripsi template biometrik: {e}")

    # 3. REPLIKASI OTOMATIS: Cari seluruh mesin lain yang terdaftar
    await cur.execute("SELECT device_sn, location FROM devices WHERE device_sn != %s", (source_device_sn,))
    target_devices = await cur.fetchall()

    queued_count = 0
    expires_at = datetime.now() + timedelta(days=7)

    for (target_sn, loc) in target_devices:
        # A. Perintah SET_USER_INFO: Mendaftarkan PIN, Nama, dan Privilege ke mesin target
        bnc_priv = "ADMIN" if str(privilege).upper() in ["ADMIN", "14"] else "USER"
        user_info_payload = {
            "cmd_code": "SET_USER_INFO",
            "user_id": raw_pin,
            "user_name": user_name if user_name else f"Pegawai {norm_pin}",
            "user_privilege": bnc_priv,
        }
        # Format ADMS standard untuk command_text (dibaca oleh adms.py / iclock)
        # Mesin ZKTeco ADMS mewajibkan privilege berupa angka (0 = User, 14 = Admin)
        adms_pri = 14 if str(privilege).upper() in ["ADMIN", "14"] else 0
        adms_user_cmd = f"DATA UPDATE USERINFO PIN={raw_pin}\tName={user_info_payload['user_name'][:24]}\tPri={adms_pri}\tGrp=1\tTZ=0001000100000000\tPIN2=0"
        
        await cur.execute(
            """
            INSERT INTO adms_commands 
                (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
            VALUES (%s, 'SET_USER_INFO', %s, 'JSON', %s, 'PENDING', %s)
            """,
            (
                target_sn,
                json.dumps(user_info_payload),
                adms_user_cmd,
                expires_at,
            ),
        )
        queued_count += 1

        # B. Perintah SET_ENROLL_DATA: Mengirim template biometrik mentah ke mesin target
        # Mesin Fingerspot menerima format paket enroll yang sama persis seperti yang dikirim mesin sumber
        if raw_body:
            rewritten_body = rewrite_enroll_payload_for_slave(raw_body)
            b64_raw = base64.b64encode(rewritten_body).decode("ascii")
            
            # Ekstrak HANYA binary template murni tanpa header JSON untuk dikirim ke ADMS
            _, binary_tail = extract_json_and_binary(raw_body)
            
            # Format ADMS standard untuk FINGERTMP
            adms_finger_tmp = base64.b64encode(binary_tail).decode("ascii") if binary_tail else ""
            adms_finger_cmd = f"DATA UPDATE FINGERTMP PIN={raw_pin}\tFID={backup_num}\tSize={len(binary_tail)}\tValid=1\tTMP={adms_finger_tmp}"

            await cur.execute(
                """
                INSERT INTO adms_commands 
                    (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
                VALUES (%s, 'SET_ENROLL_DATA', %s, 'RAW_BINARY', %s, 'PENDING', %s)
                """,
                (
                    target_sn,
                    b64_raw,
                    adms_finger_cmd,
                    expires_at,

                ),
            )
            queued_count += 1

        logger.info(f"[AUTO-REPLICATION QUEUED] Diantrekan ke mesin target '{target_sn}' ({loc})")

    return {
        "status": "success",
        "pin": norm_pin,
        "user_name": user_name,
        "replicated_to_devices": len(target_devices),
        "commands_queued": queued_count,
    }


async def get_next_pending_command(cur, device_sn: str) -> Optional[Dict[str, Any]]:
    """
    Mengambil 1 perintah antrean terlama berstatus 'PENDING' untuk mesin tertentu.
    Jika ada, ubah status menjadi 'SENT'.
    """
    # Batalkan perintah kedaluwarsa
    await cur.execute(
        "UPDATE adms_commands SET status = 'EXPIRED' WHERE status = 'PENDING' AND expires_at IS NOT NULL AND expires_at < NOW()"
    )

    await cur.execute(
        """
        SELECT id, cmd_code, payload, payload_type 
        FROM adms_commands 
        WHERE device_sn = %s AND status = 'PENDING' 
        ORDER BY id ASC 
        LIMIT 1
        """,
        (device_sn,),
    )
    row = await cur.fetchone()
    if not row:
        return None

    cmd_id, cmd_code, payload_raw, payload_type = row
    trans_id = f"cmd_{cmd_id}"

    # Update status menjadi SENT
    await cur.execute(
        "UPDATE adms_commands SET status = 'SENT', trans_id = %s, retry_count = retry_count + 1 WHERE id = %s",
        (trans_id, cmd_id),
    )

    # Format body respon
    if payload_type == "RAW_BINARY" and payload_raw:
        try:
            body_bytes = base64.b64decode(payload_raw)
        except Exception:
            body_bytes = payload_raw.encode("latin-1")
    else:
        if payload_raw:
            try:
                json_obj = json.loads(payload_raw)
            except Exception:
                json_obj = payload_raw
        else:
            json_obj = None
        body_bytes = format_command_response_body(json_obj)

    logger.info(
        f"[COMMAND DISPATCHED] ID={cmd_id} | Code={cmd_code} | Target={device_sn} | BodyBytes={len(body_bytes)}"
    )

    return {
        "cmd_id": cmd_id,
        "trans_id": trans_id,
        "cmd_code": cmd_code,
        "body_bytes": body_bytes,
    }


async def handle_command_result(
    cur,
    device_sn: str,
    trans_id: str,
    cmd_return_code: str,
    raw_body: bytes,
) -> None:
    """
    Mencatat konfirmasi hasil eksekusi perintah dari mesin ('send_cmd_result').
    """
    logger.info(
        f"[CMD RESULT ACK] Device={device_sn} | trans_id={trans_id} | ret_code={cmd_return_code}"
    )

    cmd_id = None
    if trans_id and trans_id.startswith("cmd_"):
        try:
            cmd_id = int(trans_id.replace("cmd_", ""))
        except ValueError:
            pass

    status_str = "SUCCESS" if str(cmd_return_code).strip() in ("0", "OK") else f"FAILED_{cmd_return_code}"
    status = status_str[:20]

    body_preview = ""
    if raw_body:
        try:
            body_preview = raw_body.decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass

    if cmd_id:
        await cur.execute(
            """
            UPDATE adms_commands 
            SET status = %s, return_code = %s, result_data = %s 
            WHERE id = %s
            """,
            (status, cmd_return_code, body_preview, cmd_id),
        )
    else:
        # Fallback cari berdasarkan trans_id
        await cur.execute(
            """
            UPDATE adms_commands 
            SET status = %s, return_code = %s, result_data = %s 
            WHERE trans_id = %s AND device_sn = %s
            """,
            (status, cmd_return_code, body_preview, trans_id, device_sn),
        )


async def queue_sync_all_users_to_all_devices(cur) -> Dict[str, Any]:
    """
    Fitur Sinkronisasi Penuh (Manual Trigger via UI / API):
    Mendorong seluruh data pegawai yang terpetakan beserta template sidik jari/wajah
    ke SEMUA mesin fisik yang terdaftar.
    """
    # 1. Ambil seluruh device aktif
    await cur.execute("SELECT device_sn, location FROM devices")
    devices = await cur.fetchall()
    if not devices:
        return {"status": "error", "message": "Tidak ada mesin yang terdaftar"}

    # 2. Ambil seluruh mapping pegawai (PIN & Nama)
    await cur.execute(
        """
        SELECT 
            DISTINCT pem.pin, 
            COALESCE(u.display_name, CONCAT('Pegawai ', pem.pin)) AS name
        FROM pin_employee_map pem
        LEFT JOIN users u ON pem.employee_id = u.user_id
        """
    )
    employees = await cur.fetchall()

    # 3. Ambil seluruh template biometrik
    await cur.execute(
        "SELECT pin, finger_id, template_type, template_data FROM biometric_templates WHERE valid = 1"
    )
    templates = await cur.fetchall()

    expires_at = datetime.now() + timedelta(days=7)
    queued_count = 0

    for (device_sn, loc) in devices:
        # A. Masukkan perintah SET_USER_INFO untuk setiap pegawai
        for (pin, name) in employees:
            clean_p = str(pin).strip()
            user_info = {
                "cmd_code": "SET_USER_INFO",
                "user_id": clean_p,
                "user_name": name,
                "user_privilege": "USER",
            }
            await cur.execute(
                """
                INSERT INTO adms_commands 
                    (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
                VALUES (%s, 'SET_USER_INFO', %s, 'JSON', %s, 'PENDING', %s)
                """,
                (
                    device_sn,
                    json.dumps(user_info),
                    f"BULK SYNC: SET_USER_INFO PIN={clean_p} Name={name}",
                    expires_at,
                ),
            )
            queued_count += 1

        # B. Masukkan perintah SET_ENROLL_DATA untuk template biometrik yang ada
        for (t_pin, f_id, t_type, enc_data) in templates:
            try:
                # Dekripsi template
                decrypted_raw = cipher_suite.decrypt(enc_data.encode("utf-8"))
                rewritten_body = rewrite_enroll_payload_for_slave(decrypted_raw)
                b64_raw = base64.b64encode(rewritten_body).decode("ascii")
                await cur.execute(
                    """
                    INSERT INTO adms_commands 
                        (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
                    VALUES (%s, 'SET_ENROLL_DATA', %s, 'RAW_BINARY', %s, 'PENDING', %s)
                    """,
                    (
                        device_sn,
                        b64_raw,
                        f"BULK SYNC: SET_ENROLL_DATA PIN={t_pin} Type={t_type} FID={f_id}",
                        expires_at,
                    ),
                )
                queued_count += 1
            except Exception as e:
                logger.warning(f"[BULK SYNC] Gagal decrypt template PIN={t_pin}: {e}")

    logger.info(
        f"[MASS SYNC QUEUED] Berhasil mengantrekan {queued_count} perintah ke {len(devices)} mesin."
    )
    return {
        "status": "success",
        "total_devices": len(devices),
        "total_employees": len(employees),
        "total_templates": len(templates),
        "commands_queued": queued_count,
    }


async def queue_time_sync(cur, target_device_sn: Optional[str] = None) -> int:
    """
    Mengantrekan perintah sinkronisasi waktu (SET_TIME) agar jam di seluruh fisik mesin tepat.
    """
    now_str = datetime.now().strftime("%Y%m%d%H%M%S")
    time_payload = {"time": now_str}
    expires_at = datetime.now() + timedelta(hours=2)

    if target_device_sn:
        devices = [(target_device_sn,)]
    else:
        await cur.execute("SELECT device_sn FROM devices")
        devices = await cur.fetchall()

    count = 0
    for (dev_sn,) in devices:
        await cur.execute(
            """
            INSERT INTO adms_commands 
                (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
            VALUES (%s, 'SET_TIME', %s, 'JSON', %s, 'PENDING', %s)
            """,
            (dev_sn, json.dumps(time_payload), f"SET_TIME {now_str}", expires_at),
        )
        count += 1

    return count


async def queue_sync_all_users_to_device(cur, target_device_sn: str) -> int:
    """
    Fitur Auto-Provisioning Mesin Baru:
    Mengantrekan seluruh data pegawai dan template sidik jari ke 1 mesin tertentu
    (misal saat ada penambahan mesin baru Mesin 3, Mesin 4, dst).
    """
    await cur.execute(
        """
        SELECT 
            DISTINCT pem.pin, 
            COALESCE(u.display_name, CONCAT('Pegawai ', pem.pin)) AS name
        FROM pin_employee_map pem
        LEFT JOIN users u ON pem.employee_id = u.user_id
        """
    )
    employees = await cur.fetchall()

    await cur.execute(
        "SELECT pin, finger_id, template_type, template_data FROM biometric_templates WHERE valid = 1"
    )
    templates = await cur.fetchall()

    expires_at = datetime.now() + timedelta(days=7)
    queued_count = 0

    for (pin, name) in employees:
        clean_p = str(pin).strip()
        user_info = {
            "cmd_code": "SET_USER_INFO",
            "user_id": clean_p,
            "user_name": name,
            "user_privilege": "USER",
        }
        await cur.execute(
            """
            INSERT INTO adms_commands 
                (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
            VALUES (%s, 'SET_USER_INFO', %s, 'JSON', %s, 'PENDING', %s)
            """,
            (
                target_device_sn,
                json.dumps(user_info),
                f"NEW DEVICE SYNC: SET_USER_INFO PIN={clean_p} Name={name}",
                expires_at,
            ),
        )
        queued_count += 1

    for (t_pin, f_id, t_type, enc_data) in templates:
        try:
            decrypted_raw = cipher_suite.decrypt(enc_data.encode("utf-8"))
            rewritten_body = rewrite_enroll_payload_for_slave(decrypted_raw)
            b64_raw = base64.b64encode(rewritten_body).decode("ascii")
            await cur.execute(
                """
                INSERT INTO adms_commands 
                    (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
                VALUES (%s, 'SET_ENROLL_DATA', %s, 'RAW_BINARY', %s, 'PENDING', %s)
                """,
                (
                    target_device_sn,
                    b64_raw,
                    f"NEW DEVICE SYNC: SET_ENROLL_DATA PIN={t_pin} Type={t_type} FID={f_id}",
                    expires_at,
                ),
            )
            queued_count += 1
        except Exception as e:
            logger.warning(f"[NEW DEVICE SYNC] Gagal decrypt template PIN={t_pin}: {e}")

    logger.info(
        f"[NEW DEVICE PROVISIONING] Berhasil mengantrekan {queued_count} data awal ke mesin '{target_device_sn}'."
    )
    return queued_count



async def queue_revoke_fingerprint(cur, pin: str):
    """[SYNC-05] Cabut biometrik karyawan dari semua mesin."""
    norm_pin = str(pin).strip().lstrip("0") or "0"

    await cur.execute(
        "UPDATE biometric_templates SET valid = 0 WHERE pin = %s OR pin = %s",
        (pin, norm_pin),
    )
    invalidated = cur.rowcount

    await cur.execute("SELECT device_sn, location FROM devices")
    devices = await cur.fetchall()

    if not devices:
        return {
            "status": "warning",
            "message": "Tidak ada mesin terdaftar.",
            "pin": pin,
            "templates_invalidated": invalidated,
            "devices_queued": 0,
            "commands_queued": 0,
        }

    expires_at = datetime.now() + timedelta(hours=48)
    queued_count = 0

    for (device_sn, loc) in devices:
        payload = json.dumps({"user_id": pin})
        await cur.execute(
            """
            INSERT INTO adms_commands
                (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (device_sn, "DELETE_USER", payload, "JSON",
             f"REVOKE: DELETE_USER PIN={pin}", "PENDING", expires_at),
        )
        queued_count += 1

    return {
        "status": "success",
        "pin": pin,
        "templates_invalidated": invalidated,
        "devices_queued": len(devices),
        "commands_queued": queued_count,
    }


async def cleanup_old_commands(pool_instance):
    """[SYNC-06] Bersihkan perintah lama: timeout SENT >24j, hapus SUCCESS/FAILED >30 hari."""
    try:
        async with pool_instance.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE adms_commands SET status = %s, retry_count = retry_count + 1 "
                    "WHERE status = %s AND updated_at < NOW() - INTERVAL 24 HOUR",
                    ("FAILED_TIMEOUT", "SENT"),
                )
                timed_out = cur.rowcount

                await cur.execute(
                    "DELETE FROM adms_commands WHERE status IN %s "
                    "AND created_at < NOW() - INTERVAL 30 DAY",
                    (("SUCCESS", "EXPIRED", "FAILED", "FAILED_TIMEOUT"),),
                )
                deleted = cur.rowcount

        return {"timed_out": timed_out, "deleted": deleted}
    except Exception as e:
        return {"error": str(e)}
