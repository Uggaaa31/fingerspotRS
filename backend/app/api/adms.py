"""
app/api/adms.py — ADMS / Push Protocol Receiver untuk Mesin Fingerspot Revo

Mendukung protokol ZKTeco IClock Push:
- GET  /iclock/cdata       : Handshake & inisialisasi parameter (Realtime=1)
- POST /iclock/cdata       : Penerimaan log absensi (ATTLOG) & template sidik jari (FINGERTMP)
- GET  /iclock/getrequest  : Antrean perintah ke mesin (sinkronisasi sidik jari)
- POST /iclock/devicecmd   : Konfirmasi eksekusi perintah dari mesin
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from cryptography.fernet import Fernet
from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.database import get_db_pool
from app.services.attendance_service import process_attendance_record

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ADMS Protocol"])

# Inisialisasi Cipher untuk enkripsi data biometrik (UU PDP)
cipher_suite = Fernet(settings.encryption_key.encode())


async def verify_and_touch_device(
    sn: Optional[str],
    comm_key: Optional[str] = None,
) -> Tuple[bool, bool]:
    """
    [CON-03] Verifikasi SN mesin + validasi comm_key terhadap database.
    - Jika comm_key tersimpan bukan '0' (sudah diatur IT), maka WAJIB cocok.
    - Jika comm_key tersimpan masih '0' (default auto-register), validasi dilewati.
    Jika auto_register_device aktif dan SN belum ada, daftarkan otomatis.
    Kembalikan: (is_valid, is_master)
    """
    if not sn:
        return False, False

    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT is_master, comm_key FROM devices WHERE device_sn = %s",
                (sn,)
            )
            row = await cur.fetchone()

            if row is not None:
                is_master = bool(row[0])
                stored_key = str(row[1] or "0").strip()

                # Validasi comm_key jika sudah dikonfigurasi IT (bukan default '0')
                if stored_key != "0" and comm_key is not None:
                    if str(comm_key).strip() != stored_key:
                        logger.warning(
                            f"[SECURITY] comm_key tidak cocok untuk SN={sn} "
                            f"(dikirim='{comm_key}', tersimpan='{stored_key}')"
                        )
                        return False, False

                await cur.execute(
                    "UPDATE devices SET last_seen_at = NOW() WHERE device_sn = %s",
                    (sn,)
                )
                return True, is_master

            # Jika belum terdaftar tapi auto-register aktif
            if settings.auto_register_device:
                logger.info(f"[ADMS] Mendaftarkan mesin baru secara otomatis: SN={sn}")
                await cur.execute(
                    """
                    INSERT INTO devices (device_sn, location, comm_key, is_master, last_seen_at)
                    VALUES (%s, 'Auto-Registered Device', '0', FALSE, NOW())
                    ON DUPLICATE KEY UPDATE last_seen_at = NOW()
                    """,
                    (sn,)
                )
                return True, False

    return False, False


# ── 1. HANDSHAKE (Inisialisasi Mesin) ───────────────────────────────────────────

@router.get("/iclock/cdata")
@router.get("/cdata")
async def adms_handshake(
    SN: Optional[str] = Query(None, description="Serial Number mesin"),
    options: Optional[str] = Query(None),
    pushver: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    comm_key: Optional[str] = Query(None, description="[CON-03] Kunci autentikasi mesin"),
):
    """
    Handshake saat mesin pertama kali booting atau terkoneksi.
    Membalas parameter konfigurasi agar mesin mengaktifkan push Realtime=1.
    """
    is_valid, is_master = await verify_and_touch_device(SN, comm_key)
    if not is_valid:
        logger.warning(f"[ADMS] Handshake ditolak: SN={SN} (comm_key salah atau device asing)")
        return PlainTextResponse("UNKNOWN DEVICE", status_code=403)

    logger.info(f"[ADMS] Handshake sukses dari mesin SN={SN} (Master: {is_master})")

    # Realtime=1 memerintahkan mesin untuk langsung push data setiap kali ada tap
    response_config = (
        f"GET OPTION FROM: {SN}\n"
        "Stamp=9999\n"
        "OpStamp=9999\n"
        "ErrorDelay=60\n"
        "Delay=10\n"
        "TransTimes=00:00;23:59\n"
        "TransInterval=1\n"
        "Realtime=1\n"
        "Encrypt=0\n"
    )
    return PlainTextResponse(content=response_config, media_type="text/plain")


# ── 2. RECEIVE DATA (Log Absensi & Biometrik) ───────────────────────────────────

@router.post("/iclock/cdata")
@router.post("/cdata")
async def adms_receive_data(
    request: Request,
    SN: Optional[str] = Query(None, description="Serial Number mesin"),
    table: Optional[str] = Query("ATTLOG", description="Tabel data (ATTLOG / FINGERTMP / OPERLOG)"),
    comm_key: Optional[str] = Query(None, description="[CON-03] Kunci autentikasi mesin"),
):
    """
    Menerima kiriman log absensi (ATTLOG) atau data sidik jari (FINGERTMP) secara real-time.
    """
    is_valid, is_master = await verify_and_touch_device(SN, comm_key)
    if not is_valid:
        return PlainTextResponse("UNKNOWN DEVICE", status_code=403)

    raw_body = await request.body()
    try:
        decoded_body = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        decoded_body = raw_body.decode("latin-1", errors="ignore")

    lines = [line.strip() for line in decoded_body.strip().split("\n") if line.strip()]
    if not lines:
        return PlainTextResponse("OK")

    pool = get_db_pool()
    saved_count = 0

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:

            # ── Kasus A: Log Absensi (ATTLOG) ──────────────────────────────────
            if table is None or table.upper() == "ATTLOG":
                for line in lines:
                    # Format standar ZKTeco: PIN \t TIMESTAMP \t STATUS \t VERIFY_MODE
                    parts = line.split("\t")
                    if len(parts) < 2:
                        parts = line.split()  # fallback pemisah spasi

                    if len(parts) >= 2:
                        pin = parts[0].strip()
                        timestamp_str = parts[1].strip()
                        status = int(parts[2].strip()) if len(parts) > 2 and parts[2].strip().isdigit() else 0
                        verify_mode = int(parts[3].strip()) if len(parts) > 3 and parts[3].strip().isdigit() else 1

                        try:
                            # 1. Simpan ke raw_attendance (audit log mentah)
                            # [ATT-04] ON DUPLICATE KEY UPDATE agar error non-duplikat tetap terlihat
                            await cur.execute(
                                """
                                INSERT INTO raw_attendance 
                                    (device_sn, pin, timestamp, status, verify_mode)
                                VALUES (%s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE received_at = received_at
                                """,
                                (SN, pin, timestamp_str, status, verify_mode),
                            )
                            saved_count += 1

                            # 2. Otomatisasi update ke tabel attendances (SatuTalenta / Presensi)
                            await _process_attendance_record(cur, pin, timestamp_str, status, device_sn=SN)

                            logger.info(
                                f"[TAP REALTIME] SN={SN} | PIN={pin} | Jam={timestamp_str} | Status={status}"
                            )

                        except Exception as e:
                            logger.error(f"[ADMS] Gagal memproses baris tap: {line} - Error: {e}")
                            await cur.execute(
                                "INSERT INTO attendance_errors (device_sn, raw_payload, reason) VALUES (%s, %s, %s)",
                                (SN, line, str(e)),
                            )

                return PlainTextResponse(f"OK: {saved_count}", media_type="text/plain")

            # ── Kasus B: Template Sidik Jari (FINGERTMP) ───────────────────────
            elif table.upper() == "FINGERTMP":
                if not is_master:
                    logger.warning(f"[SECURITY] Mesin non-master {SN} mencoba push sidik jari!")
                    return PlainTextResponse("UNAUTHORIZED_MASTER_ONLY", status_code=403)

                for line in lines:
                    parts = line.split("\t")
                    if len(parts) >= 5:
                        pin, finger_id, size, valid, template = parts[0], parts[1], parts[2], parts[3], parts[4]
                        try:
                            # Enkripsi template sesuai UU PDP
                            encrypted_data = cipher_suite.encrypt(template.encode()).decode("utf-8")

                            await cur.execute(
                                """
                                INSERT INTO biometric_templates (pin, finger_id, valid, template_data)
                                VALUES (%s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE template_data = VALUES(template_data), valid = VALUES(valid)
                                """,
                                (pin, finger_id, valid, encrypted_data),
                            )

                            # Siapkan perintah broadcast ke seluruh mesin Slave
                            expires_at = datetime.now() + timedelta(hours=24)
                            await cur.execute("SELECT device_sn FROM devices WHERE is_master = FALSE")
                            slave_devices = await cur.fetchall()

                            for (slave_sn,) in slave_devices:
                                # 1. Cari nama pegawai jika sudah terpetakan
                                await cur.execute(
                                    "SELECT u.display_name FROM pin_employee_map p JOIN users u ON p.employee_id = u.user_id WHERE p.pin = %s", 
                                    (pin,)
                                )
                                user_row = await cur.fetchone()
                                display_name = user_row[0] if user_row else f"Pegawai {pin}"

                                # 2. Buat slot UserInfo terlebih dahulu di mesin slave
                                cmd_user = f"DATA UPDATE USERINFO PIN={pin}\tName={display_name[:24]}\tPri=0\tGrp=1\tTZ=0001000100000000\tPIN2=0"
                                await cur.execute(
                                    """
                                    INSERT INTO adms_commands (device_sn, command_text, expires_at)
                                    VALUES (%s, %s, %s)
                                    """,
                                    (slave_sn, cmd_user, expires_at),
                                )

                                # 3. Baru kirimkan template sidik jarinya
                                cmd_finger = f"DATA UPDATE FINGERTMP PIN={pin}\tFID={finger_id}\tSize={size}\tValid={valid}\tTMP={template}"
                                await cur.execute(
                                    """
                                    INSERT INTO adms_commands (device_sn, command_text, expires_at)
                                    VALUES (%s, %s, %s)
                                    """,
                                    (slave_sn, cmd_finger, expires_at),
                                )

                            saved_count += 1
                            logger.info(f"[BIOMETRIC] Template jari PIN={pin} FID={finger_id} berhasil di-enkripsi & disinkronkan.")

                        except Exception as e:
                            logger.error(f"[ADMS] Gagal simpan template sidik jari: {e}")
                            await cur.execute(
                                "INSERT INTO attendance_errors (device_sn, raw_payload, reason) VALUES (%s, %s, %s)",
                                (SN, line, str(e)),
                            )

                return PlainTextResponse(f"OK: {saved_count}", media_type="text/plain")

    return PlainTextResponse("OK", media_type="text/plain")


async def _process_attendance_record(cur, pin: str, timestamp_str: str, status: int, device_sn: str = ""):
    """
    Memetakan PIN mesin ke pegawai dan memproses presensi terpadu via attendance_service (Single Source of Truth).
    """
    try:
        return await process_attendance_record(
            cur=cur,
            device_sn=device_sn,
            pin=pin,
            timestamp_str_or_dt=timestamp_str,
            status=status,
            attendance_method="FINGER",
        )
    except Exception as e:
        logger.error(f"[ADMS ATTENDANCE PROCESS ERROR] PIN={pin}: {e}", exc_info=True)
        return None


# ── 3. COMMAND POLLING (Perintah ke Mesin) ─────────────────────────────────────

@router.get("/iclock/getrequest")
@router.get("/getrequest")
async def adms_get_request(
    SN: Optional[str] = Query(None, description="Serial Number mesin"),
    comm_key: Optional[str] = Query(None, description="[CON-03] Kunci autentikasi mesin"),
):
    """
    Mesin secara periodik bertanya ke server apakah ada perintah baru
    (misal: instruksi update data jari karyawan dari server).
    """
    is_valid, _ = await verify_and_touch_device(SN, comm_key)
    if not is_valid:
        return PlainTextResponse("UNKNOWN DEVICE", status_code=403)

    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            # Bersihkan command yang sudah kedaluwarsa
            await cur.execute(
                """
                UPDATE adms_commands 
                SET status = 'FAILED', retry_count = retry_count + 1 
                WHERE status = 'PENDING' AND expires_at < NOW()
                """
            )

            # Ambil hingga 10 perintah pending untuk mesin ini
            await cur.execute(
                """
                SELECT id, command_text 
                FROM adms_commands 
                WHERE device_sn = %s AND status = 'PENDING' 
                ORDER BY id ASC 
                LIMIT 10
                """,
                (SN,),
            )
            commands = await cur.fetchall()

            if not commands:
                return PlainTextResponse("OK", media_type="text/plain")

            # Format ZKTeco: C:<ID>:<COMMAND_TEXT>\n
            response_text = "".join([f"C:{cmd_id}:{cmd_text}\n" for cmd_id, cmd_text in commands])
            logger.info(f"[ADMS] Mengirim {len(commands)} perintah ke mesin SN={SN}")
            return PlainTextResponse(content=response_text, media_type="text/plain")


# ── 4. COMMAND ACK (Konfirmasi Perintah) ────────────────────────────────────────

@router.post("/iclock/devicecmd")
@router.post("/devicecmd")
async def adms_device_cmd_result(
    request: Request,
    SN: Optional[str] = Query(None, description="Serial Number mesin"),
    comm_key: Optional[str] = Query(None, description="[CON-03] Kunci autentikasi mesin"),
):
    """
    Mesin mengonfirmasi hasil eksekusi perintah (SUCCESS / FAILED).
    """
    is_valid, _ = await verify_and_touch_device(SN, comm_key)
    if not is_valid:
        return PlainTextResponse("UNKNOWN DEVICE", status_code=403)

    raw_body = await request.body()
    decoded = raw_body.decode("utf-8", errors="ignore")

    pool = get_db_pool()
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            for line in decoded.strip().split("\n"):
                if "ID=" in line and "Return=" in line:
                    # Parse ID=xxx&Return=0
                    parts = dict(item.split("=") for item in line.split("&") if "=" in item)
                    cmd_id = parts.get("ID")
                    ret_code = parts.get("Return")

                    status = "SUCCESS" if ret_code == "0" else "FAILED"
                    await cur.execute(
                        "UPDATE adms_commands SET status = %s, retry_count = retry_count + 1 WHERE id = %s",
                        (status, cmd_id),
                    )
                    logger.info(f"[ADMS] Mesin SN={SN} ACK command ID={cmd_id} status={status}")

    return PlainTextResponse("OK", media_type="text/plain")
