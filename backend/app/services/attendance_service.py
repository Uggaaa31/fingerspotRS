"""
app/services/attendance_service.py — Layanan Pemrosesan Presensi & Shift RSUP (SatuTalenta HRIS)

Menyediakan logika bisnis tunggal (Single Source of Truth) untuk:
1. Deduplikasi & pencatatan log mentah (raw_attendance)
2. Pemetaan PIN mesin fisik ke pegawai (pin_employee_map & users)
3. Penentuan shift kerja (Jadwal Terdaftar vs Shift Malam / Lintas Hari vs Fallback Reguler)
4. Kalkulasi keterlambatan masuk (late_minutes, is_late, late_level: TL1/TL2/TL3)
5. Kalkulasi pulang cepat (early_leave_minutes, is_early_leave, early_leave_level: PSW1/PSW2/PSW3/PSW4)
6. Penghitungan durasi kerja efektif (working_minutes)
7. Anti-spam tap debounce & update checkout cerdas
"""

import logging
from datetime import datetime, date, timedelta, time
from typing import Optional, Dict, Any, Tuple

from app.services.broadcaster import broadcaster

logger = logging.getLogger(__name__)


def calculate_late_level(late_minutes: int) -> Optional[str]:
    """
    Menghitung tingkatan keterlambatan (TL) sesuai standar SatuTalenta / Kemenkes / ASN:
    - TL1 : Terlambat 1 s.d. 30 menit
    - TL2 : Terlambat 31 s.d. 60 menit
    - TL3 : Terlambat > 60 menit
    """
    if late_minutes <= 0:
        return None
    if late_minutes <= 30:
        return "TL1"
    if late_minutes <= 60:
        return "TL2"
    return "TL3"


def calculate_early_leave_level(early_minutes: int) -> Optional[str]:
    """
    Menghitung tingkatan pulang sebelum waktunya (PSW) sesuai standar SatuTalenta:
    - PSW1 : Pulang cepat 1 s.d. 30 menit
    - PSW2 : Pulang cepat 31 s.d. 60 menit
    - PSW3 : Pulang cepat 61 s.d. 90 menit
    - PSW4 : Pulang cepat > 90 menit
    """
    if early_minutes <= 0:
        return None
    if early_minutes <= 30:
        return "PSW1"
    if early_minutes <= 60:
        return "PSW2"
    if early_minutes <= 90:
        return "PSW3"
    return "PSW4"


async def _broadcast_punch_event(
    cur,
    device_sn: str,
    raw_pin: str,
    dt_obj: datetime,
    status: int,
    verify_mode: int,
    display_name: str,
    nip: str,
    s_code: Optional[str] = None,
    s_name: Optional[str] = None,
    late_lvl: Optional[str] = None,
    early_lvl: Optional[str] = None,
    att_status: Optional[str] = None,
):
    """Menyiarkan event tap kehadiran secara real-time via SSE Broadcaster."""
    try:
        location = "Fingerspot Mesin"
        if device_sn:
            await cur.execute("SELECT location FROM devices WHERE device_sn = %s LIMIT 1", (device_sn,))
            d_row = await cur.fetchone()
            if d_row and d_row[0]:
                location = d_row[0]

        if verify_mode == 1:
            v_label = "Sidik Jari"
        elif verify_mode == 2:
            v_label = "Wajah"
        elif verify_mode in (8, 9, 5, 2147483648):
            v_label = "Vena Telapak Tangan"
        elif verify_mode == 4:
            v_label = "Kartu RFID"
        elif verify_mode == 15:
            v_label = "Wajah + FP"
        else:
            v_label = "Password/PIN"

        await cur.execute(
            "SELECT id FROM raw_attendance WHERE device_sn = %s AND pin = %s AND timestamp = %s LIMIT 1",
            (device_sn, raw_pin, dt_obj),
        )
        raw_row = await cur.fetchone()
        raw_id = raw_row[0] if raw_row else int(dt_obj.timestamp())

        # Statistik hari ini untuk pembaruan instan kartu statistik UI
        await cur.execute(
            """
            SELECT 
                COUNT(*), 
                COALESCE(SUM(CASE WHEN status = 0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END), 0)
            FROM raw_attendance 
            WHERE DATE(received_at) = CURRENT_DATE() OR DATE(timestamp) = CURRENT_DATE()
            """
        )
        stats_row = await cur.fetchone()
        total_today = int(stats_row[0]) if stats_row and stats_row[0] else 0
        checkin_today = int(stats_row[1]) if stats_row and stats_row[1] else 0
        checkout_today = int(stats_row[2]) if stats_row and stats_row[2] else 0

        await cur.execute("SELECT COUNT(*) FROM devices WHERE last_seen_at >= NOW() - INTERVAL 15 MINUTE")
        online_row = await cur.fetchone()
        online_devices = int(online_row[0]) if online_row and online_row[0] else 0

        status_label = "Masuk" if status == 0 else ("Pulang" if status == 1 else f"Status ({status})")

        payload = {
            "id": raw_id,
            "device_sn": device_sn,
            "location": location,
            "pin": raw_pin,
            "employee_name": display_name,
            "employee_nip": nip,
            "employee_nik": nip,
            "national_id_number": nip,
            "timestamp": dt_obj.strftime("%Y-%m-%d %H:%M:%S"),
            "status_code": status,
            "status_label": status_label,
            "verify_code": verify_mode,
            "verify_label": v_label,
            "shift_code": s_code,
            "shift_name": s_name,
            "late_level": late_lvl,
            "early_leave_level": early_lvl,
            "attendance_status": att_status or status_label,
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        stats_payload = {
            "total_today": total_today,
            "checkin_today": checkin_today,
            "checkout_today": checkout_today,
            "online_devices": online_devices,
        }

        await broadcaster.broadcast("punch", data=payload, stats=stats_payload)
        logger.info(f"[SSE BROADCAST] Punch PIN={raw_pin} ({display_name}) disiarkan ke {broadcaster.count()} klien SSE.")
    except Exception as e_bc:
        logger.warning(f"[SSE BROADCAST WARNING] Gagal broadcast punch: {e_bc}")


async def process_attendance_record(
    cur,
    device_sn: str,
    pin: str,
    timestamp_str_or_dt: Any,
    status: int = 0,
    verify_mode: int = 1,
    attendance_method: str = "FINGER",
) -> Dict[str, Any]:
    """
    Memproses satu tap absensi dari mesin fisik (baik protokol Fingerspot BNC maupun ZKTeco ADMS).

    Parameter:
    - cur: Cursor database aktif (aiomysql)
    - device_sn: Nomor seri mesin pengirim
    - pin: ID user / PIN yang terbaca di mesin
    - timestamp_str_or_dt: String 'YYYY-MM-DD HH:MM:SS' atau objek datetime
    - status: Status tap mesin (0 = Checkin/Auto, 1 = Checkout, dll)
    - verify_mode: Mode verifikasi (1=FP, 2=Password, 3=Card, 4=Face, 15=Face+FP)
    - attendance_method: 'FINGER', 'FACE', 'IDCARD', 'PASSWORD'
    """
    # 1. Parsing Datetime
    if isinstance(timestamp_str_or_dt, datetime):
        dt_obj = timestamp_str_or_dt
    else:
        try:
            dt_obj = datetime.strptime(str(timestamp_str_or_dt).strip(), "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                dt_obj = datetime.fromisoformat(str(timestamp_str_or_dt).replace("Z", ""))
            except Exception as e:
                logger.error(f"[ATTENDANCE] Gagal parsing timestamp '{timestamp_str_or_dt}': {e}")
                return {"status": "error", "message": f"Invalid timestamp format: {e}"}

    raw_pin = str(pin).strip()
    clean_pin = raw_pin.lstrip("0") or "0"

    # 2. Simpan Log Mentah ke raw_attendance (Idempotent Deduplication)
    await cur.execute(
        """
        INSERT INTO raw_attendance (device_sn, pin, timestamp, status, verify_mode, received_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE received_at = NOW()
        """,
        (device_sn, raw_pin, dt_obj, status, verify_mode),
    )

    # 3. Cari Profil Pegawai (users) & Mapping PIN
    # Langkah 3A: Cek tabel pin_employee_map
    await cur.execute(
        "SELECT employee_id FROM pin_employee_map WHERE pin = %s OR pin = %s LIMIT 1",
        (raw_pin, clean_pin),
    )
    pem = await cur.fetchone()

    user_row = None
    if pem:
        emp_id = pem[0]
        await cur.execute(
            """
            SELECT user_id, display_name, department_id, sub_department_id, location_id, national_id_number 
            FROM users WHERE user_id = %s LIMIT 1
            """,
            (emp_id,),
        )
        user_row = await cur.fetchone()

    # Langkah 3B: Fallback cari di tabel users jika belum ada di pin_employee_map
    if not user_row:
        await cur.execute(
            """
            SELECT user_id, display_name, department_id, sub_department_id, location_id, national_id_number 
            FROM users 
            WHERE national_id_number = %s 
               OR national_id_number = %s 
               OR employee_id_number = %s 
               OR user_id = %s 
            LIMIT 1
            """,
            (
                raw_pin,
                clean_pin,
                raw_pin,
                clean_pin if clean_pin.isdigit() else -1,
            ),
        )
        user_row = await cur.fetchone()

        # Otomatis daftarkan ke pin_employee_map agar pencarian berikutnya instan
        if user_row:
            await cur.execute(
                """
                INSERT INTO pin_employee_map (pin, employee_id) 
                VALUES (%s, %s) 
                ON DUPLICATE KEY UPDATE employee_id = %s
                """,
                (raw_pin, user_row[0], user_row[0]),
            )

    if not user_row:
        logger.info(f"[ATTENDANCE] PIN={raw_pin} belum terpetakan ke pegawai SatuTalenta. Menunggu pemetaan di Dashboard.")
        # Tetap siarkan ke SSE agar live feed operator langsung mendeteksi tap baru
        await _broadcast_punch_event(
            cur=cur,
            device_sn=device_sn,
            raw_pin=raw_pin,
            dt_obj=dt_obj,
            status=status,
            verify_mode=verify_mode,
            display_name="Belum Terpetakan",
            nip="-",
            s_code=None,
            s_name=None,
            late_lvl=None,
            early_lvl=None,
            att_status=None,
        )
        return {"status": "unmapped", "pin": raw_pin, "timestamp": dt_obj}

    user_id = user_row[0]
    display_name = user_row[1] or f"User {user_id}"
    dept_id = user_row[2]
    sub_dept_id = user_row[3]
    loc_id = user_row[4]
    nip_str = str(user_row[5]) if len(user_row) > 5 and user_row[5] else "-"

    # Ambil location_id alternatif dari tabel devices jika user belum punya location_id
    if not loc_id and device_sn:
        await cur.execute("SELECT location FROM devices WHERE device_sn = %s", (device_sn,))
        d_row = await cur.fetchone()
        # Jika kolom devices.location berisi id atau deskripsi

    current_date = dt_obj.date()
    yesterday = current_date - timedelta(days=1)

    # 4. Logika Penentuan Shift & Tanggal Presensi (attendance_date)
    # ── [KASUS KHUSUS: SHIFT MALAM KEMARIN / OVERNIGHT SHIFT] ──────────────────
    # Jika pegawai terjadwal Shift Malam kemarin (is_next_day = 1) dan tap pagi ini
    # terjadi sebelum jam 14:00, maka tap pagi ini dihitung sebagai CHECKOUT kemarin.
    att_date = current_date
    shift_info = None
    is_overnight_checkout = False

    # A. Periksa apakah kemarin ada jadwal shift malam (is_next_day = 1)
    await cur.execute(
        """
        SELECT uss.shift_id, s.shift_code, s.shift_name, s.shift_type, s.checkin_time, s.checkout_time,
               s.is_next_day, s.late_tolerance_minutes, s.early_leave_tolerance_minutes, s.requires_attendance
        FROM user_shift_schedules uss
        JOIN shifts s ON uss.shift_id = s.shift_id
        WHERE uss.user_id = %s AND uss.schedule_date = %s AND s.is_next_day = 1
        LIMIT 1
        """,
        (user_id, yesterday),
    )
    yday_night_shift = await cur.fetchone()

    if yday_night_shift and dt_obj.hour < 14:
        # Cek apakah dia sudah memiliki baris checkin di tanggal kemarin
        await cur.execute(
            "SELECT attendance_id, checkin_time, checkout_time FROM attendances WHERE user_id = %s AND attendance_date = %s",
            (user_id, yesterday),
        )
        yday_att = await cur.fetchone()
        if yday_att and yday_att[1]:
            # Dia checkin kemarin malam dan tap sekarang adalah checkout shift malam!
            att_date = yesterday
            shift_info = yday_night_shift
            is_overnight_checkout = True
            logger.info(
                f"[OVERNIGHT SHIFT DETECTED] Pegawai {display_name} (PIN={raw_pin}) tap {dt_obj.strftime('%H:%M')} "
                f"diakui sebagai Checkout Shift Malam tanggal {yesterday} (Shift={yday_night_shift[1]})"
            )

    # Fallback Shift Malam Tidak Terjadwal:
    # Jika tidak ada jadwal shift malam kemarin, tetapi dia tap pagi sebelum jam 10:00,
    # dan dia memiliki baris checkin kemarin yang BELUM checkout, kaitkan sebagai checkout kemarin.
    if not is_overnight_checkout and dt_obj.hour < 10:
        await cur.execute(
            """
            SELECT attendance_id, checkin_time, checkout_time, shift_id 
            FROM attendances 
            WHERE user_id = %s AND attendance_date = %s
            """,
            (user_id, yesterday),
        )
        unclosed_yday = await cur.fetchone()
        if unclosed_yday and unclosed_yday[1] and not unclosed_yday[2]:
            # Cek apakah dia sudah checkin untuk hari ini
            await cur.execute(
                "SELECT attendance_id FROM attendances WHERE user_id = %s AND attendance_date = %s",
                (user_id, current_date),
            )
            has_today = await cur.fetchone()
            if not has_today:
                att_date = yesterday
                is_overnight_checkout = True
                # Ambil info shift kemarin jika ada
                if unclosed_yday[3]:
                    await cur.execute(
                        """
                        SELECT shift_id, shift_code, shift_name, shift_type, checkin_time, checkout_time,
                               is_next_day, late_tolerance_minutes, early_leave_tolerance_minutes, requires_attendance
                        FROM shifts WHERE shift_id = %s
                        """,
                        (unclosed_yday[3],),
                    )
                    shift_info = await cur.fetchone()
                logger.info(
                    f"[OVERNIGHT FALLBACK] Pegawai {display_name} (PIN={raw_pin}) tap {dt_obj.strftime('%H:%M')} "
                    f"ditautkan ke check-in kemarin yang belum checkout (Tanggal={yesterday})"
                )

    # B. Jika bukan checkout shift malam kemarin, cari jadwal shift HARI INI
    if not shift_info:
        await cur.execute(
            """
            SELECT uss.shift_id, s.shift_code, s.shift_name, s.shift_type, s.checkin_time, s.checkout_time,
                   s.is_next_day, s.late_tolerance_minutes, s.early_leave_tolerance_minutes, s.requires_attendance
            FROM user_shift_schedules uss
            JOIN shifts s ON uss.shift_id = s.shift_id
            WHERE uss.user_id = %s AND uss.schedule_date = %s
            LIMIT 1
            """,
            (user_id, att_date),
        )
        shift_info = await cur.fetchone()

    # C. Fallback Shift Reguler Standar jika belum dijadwalkan
    if not shift_info:
        # Tentukan kode shift reguler (Jumat beda jam pulang dengan Senin-Kamis)
        weekday = att_date.weekday()  # 4 = Friday
        reg_code = "REG-JMT" if weekday == 4 else "REG"

        await cur.execute(
            """
            SELECT shift_id, shift_code, shift_name, shift_type, checkin_time, checkout_time,
                   is_next_day, late_tolerance_minutes, early_leave_tolerance_minutes, requires_attendance
            FROM shifts WHERE shift_code = %s LIMIT 1
            """,
            (reg_code,),
        )
        shift_info = await cur.fetchone()

        # Fallback terakhir jika REG tidak ditemukan, ambil shift apapun yang aktif
        if not shift_info:
            await cur.execute(
                """
                SELECT shift_id, shift_code, shift_name, shift_type, checkin_time, checkout_time,
                       is_next_day, late_tolerance_minutes, early_leave_tolerance_minutes, requires_attendance
                FROM shifts ORDER BY shift_id ASC LIMIT 1
                """
            )
            shift_info = await cur.fetchone()

    # Ekstraksi komponen shift
    if shift_info:
        s_id = shift_info[0]
        s_code = shift_info[1]
        s_name = shift_info[2]
        s_type = shift_info[3]
        s_cin_str = shift_info[4]
        s_cout_str = shift_info[5]
        s_is_next_day = bool(shift_info[6])
        s_late_tol = shift_info[7] if shift_info[7] is not None else 15
        s_early_tol = shift_info[8] if shift_info[8] is not None else 0
        s_req_att = bool(shift_info[9]) if shift_info[9] is not None else True
        is_reguler_flag = 1 if "REG" in s_code.upper() or s_type.lower() == "reguler" else 0
    else:
        s_id = None
        s_code = "UNKNOWN"
        s_cin_str = "07:30"
        s_cout_str = "16:00"
        s_is_next_day = False
        s_late_tol = 15
        s_early_tol = 0
        s_req_att = True
        is_reguler_flag = 1

    # 5. Cek Baris Kehadiran di Database untuk user_id & att_date
    await cur.execute(
        """
        SELECT attendance_id, checkin_time, checkout_time, late_minutes, late_level, is_late,
               early_leave_minutes, early_leave_level, is_early_leave, working_minutes, attendance_status
        FROM attendances 
        WHERE user_id = %s AND attendance_date = %s
        LIMIT 1
        """,
        (user_id, att_date),
    )
    existing_att = await cur.fetchone()

    # ── KASUS 1: BELUM ADA BARIS KEHADIRAN (CHECK-IN) ──────────────────────────
    if not existing_att:
        # Hitung Keterlambatan Checkin
        late_min = 0
        late_lvl = None
        is_late_flag = 0

        if s_cin_str:
            try:
                # Waktu jadwal masuk
                h, m = [int(x) for x in s_cin_str.split(":")[:2]]
                sched_cin_dt = datetime.combine(att_date, time(h, m, 0))

                diff_seconds = (dt_obj - sched_cin_dt).total_seconds()
                diff_minutes = int(diff_seconds / 60)

                # Jika tap masuk setelah jadwal + toleransi
                if diff_minutes > s_late_tol:
                    late_min = diff_minutes - s_late_tol
                    is_late_flag = 1
                    late_lvl = calculate_late_level(late_min)
                elif diff_minutes > 0:
                    # Masih dalam rentang toleransi (misal telat 5 menit tapi toleransi 15)
                    late_min = 0
                    is_late_flag = 0
                    late_lvl = None
            except Exception as e_cin:
                logger.warning(f"[ATTENDANCE CALC] Gagal parsing jam masuk shift '{s_cin_str}': {e_cin}")

        # Tentukan status kehadiran
        att_status = late_lvl if late_lvl else "HADIR"

        await cur.execute(
            """
            INSERT INTO attendances 
                (user_id, shift_id, attendance_date, checkin_time, checkout_time,
                 attendance_method, attendance_status, is_reguler,
                 late_minutes, late_level, is_late,
                 early_leave_minutes, early_leave_level, is_early_leave,
                 working_minutes, department_id, sub_department_id, location_id, updated_at)
            VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, 0, NULL, 0, 0, %s, %s, %s, NOW())
            """,
            (
                user_id,
                s_id,
                att_date,
                dt_obj,
                attendance_method,
                att_status,
                is_reguler_flag,
                late_min,
                late_lvl,
                is_late_flag,
                dept_id,
                sub_dept_id,
                loc_id,
            ),
        )
        att_id = cur.lastrowid
        logger.info(
            f"[CHECKIN SUCCESS] Pegawai={display_name} (PIN={raw_pin}) | Tanggal={att_date} | "
            f"Shift={s_code} ({s_cin_str}-{s_cout_str}) | Jam Masuk={dt_obj.strftime('%H:%M:%S')} | "
            f"Keterlambatan={late_min}m ({late_lvl or 'Tepat Waktu'}) | Status={att_status}"
        )
        # Siarkan event checkin secara real-time via SSE
        await _broadcast_punch_event(
            cur=cur,
            device_sn=device_sn,
            raw_pin=raw_pin,
            dt_obj=dt_obj,
            status=0,
            verify_mode=verify_mode,
            display_name=display_name,
            nip=nip_str,
            s_code=s_code,
            s_name=s_name,
            late_lvl=late_lvl,
            early_lvl=None,
            att_status=att_status,
        )
        return {
            "status": "checkin",
            "attendance_id": att_id,
            "user_id": user_id,
            "employee_name": display_name,
            "attendance_date": str(att_date),
            "checkin_time": str(dt_obj),
            "late_minutes": late_min,
            "late_level": late_lvl,
        }

    # ── KASUS 2: SUDAH ADA BARIS KEHADIRAN (CHECK-OUT / UPDATE JAM PULANG) ─────
    att_id = existing_att[0]
    first_cin = existing_att[1]
    prev_cout = existing_att[2]

    # Debounce Anti-Spam:
    # Jika pegawai menempelkan jari berkali-kali dalam rentang < 60 detik dari checkin
    if first_cin:
        gap_sec = (dt_obj - first_cin).total_seconds()
        if gap_sec < 60:
            logger.info(f"[DEBOUNCE TAP] Pegawai {display_name} (PIN={raw_pin}) tap berulang dalam {int(gap_sec)}s. Diabaikan.")
            return {"status": "debounced", "attendance_id": att_id, "user_id": user_id}

    # Hitung Durasi Kerja (working_minutes)
    working_min = 0
    if first_cin and dt_obj > first_cin:
        working_min = max(0, int((dt_obj - first_cin).total_seconds() / 60))

    # Hitung Pulang Sebelum Waktunya (early_leave)
    early_min = 0
    early_lvl = None
    is_early_flag = 0

    if s_cout_str:
        try:
            h_out, m_out = [int(x) for x in s_cout_str.split(":")[:2]]
            # Jika shift malam lintas hari, jadwal pulang adalah tanggal berikutnya
            cout_date = att_date + timedelta(days=1) if s_is_next_day else att_date
            sched_cout_dt = datetime.combine(cout_date, time(h_out, m_out, 0))

            diff_early_sec = (sched_cout_dt - dt_obj).total_seconds()
            diff_early_min = int(diff_early_sec / 60)

            # Jika tap pulang lebih cepat dari jadwal + toleransi pulang cepat
            if diff_early_min > s_early_tol:
                early_min = diff_early_min - s_early_tol
                is_early_flag = 1
                early_lvl = calculate_early_leave_level(early_min)
        except Exception as e_cout:
            logger.warning(f"[ATTENDANCE CALC] Gagal parsing jam pulang shift '{s_cout_str}': {e_cout}")

    # Pertahankan status keterlambatan yang sudah tercatat saat checkin
    existing_late_min = existing_att[3] or 0
    existing_late_lvl = existing_att[4]
    existing_is_late = existing_att[5] or 0

    # Status kehadiran: jika ada early leave atau late, tandai
    curr_status = existing_att[10] or "HADIR"
    if early_lvl and curr_status == "HADIR":
        curr_status = early_lvl

    # Update baris attendances dengan jam pulang terbaru (MAX)
    await cur.execute(
        """
        UPDATE attendances 
        SET checkout_time = %s,
            working_minutes = %s,
            early_leave_minutes = %s,
            early_leave_level = %s,
            is_early_leave = %s,
            attendance_status = %s,
            updated_at = NOW()
        WHERE attendance_id = %s
        """,
        (dt_obj, working_min, early_min, early_lvl, is_early_flag, curr_status, att_id),
    )

    logger.info(
        f"[CHECKOUT SUCCESS] Pegawai={display_name} (PIN={raw_pin}) | Tanggal={att_date} | "
        f"Jam Pulang={dt_obj.strftime('%H:%M:%S')} | Durasi Kerja={working_min} menit ({working_min // 60}j {working_min % 60}m) | "
        f"Pulang Cepat={early_min}m ({early_lvl or 'Sesuai Jadwal'})"
    )

    # Siarkan event checkout secara real-time via SSE
    await _broadcast_punch_event(
        cur=cur,
        device_sn=device_sn,
        raw_pin=raw_pin,
        dt_obj=dt_obj,
        status=1,
        verify_mode=verify_mode,
        display_name=display_name,
        nip=nip_str,
        s_code=s_code,
        s_name=s_name,
        late_lvl=existing_late_lvl,
        early_lvl=early_lvl,
        att_status=curr_status,
    )

    return {
        "status": "checkout",
        "attendance_id": att_id,
        "user_id": user_id,
        "employee_name": display_name,
        "attendance_date": str(att_date),
        "checkin_time": str(first_cin),
        "checkout_time": str(dt_obj),
        "working_minutes": working_min,
        "early_leave_minutes": early_min,
        "early_leave_level": early_lvl,
    }
