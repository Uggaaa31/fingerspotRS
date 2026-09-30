"""
app/main.py — Entry point FastAPI application (ADMS Middleware & Attendance API)
"""
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db_pool, close_db_pool, get_db_pool
from app.api import adms
from app.services.sync_service import queue_revoke_fingerprint, cleanup_old_commands

# ── Logging Setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Lifecycle ─────────────────────────────────────────────────────────────────
async def _periodic_cleanup():
    """
    [SYNC-06] Background task: bersihkan command lama setiap jam.
    - SENT yang tidak ter-ACK > 24 jam → FAILED_TIMEOUT
    - SUCCESS/FAILED/EXPIRED > 30 hari → dihapus
    """
    await asyncio.sleep(300)  # Tunggu 5 menit setelah startup
    while True:
        pool = get_db_pool()
        if pool:
            result = await cleanup_old_commands(pool)
            logger.info(f"[SYNC-06] Cleanup periodik: {result}")
        await asyncio.sleep(3600)  # Ulangi setiap 1 jam


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup & shutdown lifecycle.
    - Startup: Hubungkan connection pool ke MySQL, mulai background cleanup task
    - Shutdown: Tutup pool koneksi
    """
    logger.info("=" * 65)
    logger.info("  Fingerspot Real-Time ADMS Middleware — Starting Up")
    logger.info(f"  Database : MySQL ({settings.db_host}:{settings.db_port}/{settings.db_name})")
    logger.info(f"  Port     : {settings.api_port}")
    logger.info("=" * 65)

    try:
        await init_db_pool()
        logger.info("[App] Database connection pool initialized successfully")
    except Exception as e:
        logger.error(f"[App] Startup warning: Gagal inisialisasi DB: {e}")

    # [SYNC-06] Jalankan cleanup periodik sebagai background task
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    logger.info("[App] Periodic command cleanup task dimulai (interval: 1 jam).")

    yield

    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    await close_db_pool()
    logger.info("[App] Fingerspot ADMS Middleware shutdown complete")


# ── FastAPI App ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="Fingerspot ADMS Real-Time Middleware",
    description=(
        "Middleware lokal berbasis **FastAPI + MySQL** untuk menerima data absensi "
        "secara **real-time** dari mesin **Fingerspot Revo series** via protokol ADMS / Push.\n\n"
        "**Fitur:**\n"
        "- Receiver HTTP ADMS (`/iclock/cdata`, `/cdata`)\n"
        "- Real-time attendance ingestion & auto-mapping ke presensi\n"
        "- Audit log lengkap di tabel `raw_attendance`\n"
        "- Sinkronisasi sidik jari terpusat (Master ke Slave)\n"
        "- 100% On-Premise / Lokal tanpa Cloud pihak ketiga\n"
    ),
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Daftarkan Router ADMS ─────────────────────────────────────────────────────
app.include_router(adms.router)


# ── Helper Formatter ──────────────────────────────────────────────────────────
def format_datetime(val):
    if not val:
        return None
    if isinstance(val, str):
        return val
    try:
        return val.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


# ── REST API: Monitor Log Absensi & Status Mesin ───────────────────────────────
@app.get("/api/v1/raw-attendance", tags=["Monitoring"], summary="Lihat raw scan tap absensi")
async def get_raw_attendance(
    limit: int = Query(50, ge=1, le=500),
    device_sn: str = Query(None, description="Filter SN mesin"),
):
    """Melihat log tap mentah terbaru yang masuk dari mesin Fingerspot."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if device_sn:
                    await cur.execute(
                        "SELECT id, device_sn, pin, timestamp, status, verify_mode, received_at "
                        "FROM raw_attendance WHERE device_sn = %s ORDER BY id DESC LIMIT %s",
                        (device_sn, limit),
                    )
                else:
                    await cur.execute(
                        "SELECT id, device_sn, pin, timestamp, status, verify_mode, received_at "
                        "FROM raw_attendance ORDER BY id DESC LIMIT %s",
                        (limit,),
                    )
                rows = await cur.fetchall()

                data = [
                    {
                        "id": r[0],
                        "device_sn": r[1],
                        "pin": str(r[2]).strip(),
                        "timestamp": format_datetime(r[3]),
                        "status": r[4],
                        "verify_mode": r[5],
                        "received_at": format_datetime(r[6]),
                    }
                    for r in rows
                ]
                return {"total": len(data), "data": data}
    except Exception as e:
        logger.error(f"[API ERROR raw-attendance] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/v1/devices", tags=["Monitoring"], summary="Daftar mesin Fingerspot")
async def get_registered_devices():
    """Melihat status mesin yang terdaftar dan waktu terakhir terhubung."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT device_sn, location, comm_key, is_master, last_seen_at FROM devices")
                rows = await cur.fetchall()
                devices = [
                    {
                        "device_sn": r[0],
                        "location": r[1],
                        "comm_key": r[2],
                        "is_master": bool(r[3]),
                        "last_seen_at": format_datetime(r[4]),
                    }
                    for r in rows
                ]
                return {"devices": devices}
    except Exception as e:
        logger.error(f"[API ERROR devices] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── REST API: Live Attendance Feed Lengkap dengan Nama Pegawai ─────────────────
@app.get("/api/v1/live-feed", tags=["Monitoring"], summary="Live attendance feed dengan nama pegawai")
async def get_live_feed(
    limit: int = Query(100, ge=1, le=500),
    deduplicate: bool = Query(False, description="Gabungkan tap berulang dalam selang 5 menit"),
):
    """
    Menyajikan data tap absensi real-time yang sudah dipetakan dengan:
    Nama Pegawai, NIP, Lokasi Mesin, Status (Masuk/Pulang), dan Statistik Hari Ini.
    Mendukung normalisasi PIN (02 -> 2) dan filter tap berulang (deduplicate).
    """
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT 
                        r.id,
                        r.device_sn,
                        COALESCE(d.location, 'Fingerspot Mesin') AS location,
                        r.pin,
                        COALESCE(u.display_name, 'Belum Terpetakan') AS employee_name,
                        COALESCE(u.employee_id_number, '-') AS employee_nip,
                        r.timestamp,
                        r.status,
                        r.verify_mode,
                        r.received_at
                    FROM raw_attendance r
                    LEFT JOIN devices d ON r.device_sn = d.device_sn
                    LEFT JOIN pin_employee_map pem ON (
                        r.pin = pem.pin 
                        OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM pem.pin) COLLATE utf8mb4_unicode_ci)
                    )
                    LEFT JOIN users u ON (
                        pem.employee_id = u.user_id 
                        OR (
                            pem.employee_id IS NULL AND (
                                r.pin = u.employee_id_number 
                                OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM u.employee_id_number) COLLATE utf8mb4_unicode_ci)
                                OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = CAST(u.user_id AS CHAR)
                            )
                        )
                    )
                    ORDER BY r.id DESC
                    LIMIT %s
                """
                await cur.execute(query, (limit,))
                rows = await cur.fetchall()

                # Statistik ringkas: hitung berdasarkan hari ini
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

                # Jumlah mesin yang aktif (online) dalam 15 menit terakhir
                await cur.execute("SELECT COUNT(*) FROM devices WHERE last_seen_at >= NOW() - INTERVAL 15 MINUTE")
                online_row = await cur.fetchone()
                online_devices = int(online_row[0]) if online_row and online_row[0] else 0

                # Ambil waktu tap pertama hari ini untuk setiap PIN (Smart Status Logic)
                await cur.execute("SELECT pin, MIN(timestamp) FROM raw_attendance WHERE DATE(timestamp) = CURRENT_DATE() GROUP BY pin")
                first_taps_today = {str(r[0]).strip(): r[1] for r in await cur.fetchall()}

                data = []
                last_seen_times = {}

                for r in rows:
                    pin_str = str(r[3]).strip()
                    ts = r[6] # timestamp
                    
                    # Smart Status: Abaikan tombol fisik mesin, hitung otomatis
                    status_code = r[7]
                    if ts and ts.date() == datetime.today().date():
                        first_ts = first_taps_today.get(pin_str)
                        # Jika ada tap pertama hari ini dan waktu tap ini lebih baru dari tap pertama -> Pulang
                        if first_ts and ts > first_ts:
                            status_code = 1
                        else:
                            status_code = 0

                    status_label = "Masuk" if status_code == 0 else ("Pulang" if status_code == 1 else f"Status ({status_code})")

                    v_code = r[8]
                    if v_code == 1:
                        v_label = "Sidik Jari"
                    elif v_code == 2:
                        v_label = "Wajah"
                    elif v_code == 15:
                        v_label = "Kartu RFID"
                    else:
                        v_label = "Password/PIN"

                    pin_str = str(r[3]).strip()
                    ts_str = format_datetime(r[6])

                    # Filter tap berulang (deduplicate) jika diminta: minimal 5 menit selisih per PIN
                    if deduplicate and ts_str:
                        norm_p = pin_str.lstrip("0") or "0"
                        try:
                            curr_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                            if norm_p in last_seen_times:
                                diff_sec = abs((last_seen_times[norm_p] - curr_dt).total_seconds())
                                if diff_sec < 300:
                                    continue
                            last_seen_times[norm_p] = curr_dt
                        except Exception:
                            pass

                    data.append({
                        "id": r[0],
                        "device_sn": r[1],
                        "location": r[2],
                        "pin": pin_str,
                        "employee_name": r[4],
                        "employee_nip": r[5],
                        "timestamp": ts_str,
                        "status_code": status_code,
                        "status_label": status_label,
                        "verify_code": v_code,
                        "verify_label": v_label,
                        "received_at": format_datetime(r[9]),
                    })

                return {
                    "stats": {
                        "total_today": total_today,
                        "checkin_today": checkin_today,
                        "checkout_today": checkout_today,
                        "online_devices": online_devices,
                    },
                    "total": len(data),
                    "data": data,
                }
    except Exception as e:
        logger.error(f"[API ERROR live-feed] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── REST API: Rekap Presensi Harian Terkonsolidasi (SatuTalenta RSUP) ─────────
@app.get("/api/v1/daily-attendance", tags=["Monitoring"], summary="Rekap presensi harian per pegawai")
async def get_daily_attendance(
    target_date: Optional[str] = Query(None, description="Format YYYY-MM-DD, default hari ini"),
):
    """
    Menyajikan daftar presensi terstruktur per pegawai (1 baris per pegawai):
    Nama, NIP, Jam Masuk (Check-In), Jam Pulang (Check-Out), dan Durasi Kerja.
    Tidak ada duplikasi baris.
    """
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT 
                        a.attendance_id,
                        a.user_id,
                        COALESCE(u.display_name, 'Belum Terpetakan') AS employee_name,
                        COALESCE(u.employee_id_number, '-') AS employee_nip,
                        COALESCE(MIN(pem.pin), CAST(u.user_id AS CHAR)) AS pin,
                        a.attendance_date,
                        a.checkin_time,
                        a.checkout_time,
                        a.attendance_method,
                        a.attendance_status
                    FROM attendances a
                    JOIN users u ON a.user_id = u.user_id
                    LEFT JOIN pin_employee_map pem ON a.user_id = pem.employee_id
                    WHERE a.attendance_date = %s
                    GROUP BY a.attendance_id, a.user_id, u.display_name, u.employee_id_number, a.attendance_date, a.checkin_time, a.checkout_time, a.attendance_method, a.attendance_status

                    UNION ALL

                    SELECT 
                        NULL AS attendance_id,
                        NULL AS user_id,
                        CONCAT('Belum Terpetakan (PIN ', r.pin, ')') AS employee_name,
                        '-' AS employee_nip,
                        r.pin,
                        DATE(r.timestamp) AS attendance_date,
                        MIN(r.timestamp) AS checkin_time,
                        CASE WHEN COUNT(*) > 1 AND MAX(r.timestamp) > MIN(r.timestamp) THEN MAX(r.timestamp) ELSE NULL END AS checkout_time,
                        'FINGER' AS attendance_method,
                        'Belum Dipetakan' AS attendance_status
                    FROM raw_attendance r
                    LEFT JOIN pin_employee_map pem ON (
                        r.pin = pem.pin 
                        OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM pem.pin) COLLATE utf8mb4_unicode_ci)
                    )
                    WHERE pem.employee_id IS NULL AND DATE(r.timestamp) = %s
                    GROUP BY r.pin, DATE(r.timestamp)

                    ORDER BY checkin_time DESC
                """
                await cur.execute(query, (target_date, target_date))
                rows = await cur.fetchall()

                data = []
                for r in rows:
                    cin = format_datetime(r[6])
                    cout = format_datetime(r[7])

                    duration_str = "-"
                    if r[6] and r[7]:
                        try:
                            # Parse datetime if needed
                            d_in = r[6] if isinstance(r[6], datetime) else datetime.strptime(str(r[6])[:19], "%Y-%m-%d %H:%M:%S")
                            d_out = r[7] if isinstance(r[7], datetime) else datetime.strptime(str(r[7])[:19], "%Y-%m-%d %H:%M:%S")
                            diff = d_out - d_in
                            hours, remainder = divmod(diff.total_seconds(), 3600)
                            minutes = remainder // 60
                            duration_str = f"{int(hours)}j {int(minutes)}m"
                        except Exception:
                            pass

                    data.append({
                        "attendance_id": r[0],
                        "user_id": r[1],
                        "employee_name": r[2],
                        "employee_nip": r[3],
                        "pin": str(r[4]).strip() if r[4] else "-",
                        "date": str(r[5]),
                        "checkin_time": cin,
                        "checkout_time": cout,
                        "duration": duration_str,
                        "method": r[8],
                        "status": r[9],
                    })

                return {"date": target_date, "total": len(data), "data": data}
    except Exception as e:
        logger.error(f"[API ERROR daily-attendance] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/v1/export-attendance", tags=["Monitoring"], summary="Export presensi harian ke Excel")
async def export_daily_attendance(
    target_date: Optional[str] = Query(None, description="Format YYYY-MM-DD, default hari ini"),
):
    import io
    from fastapi.responses import StreamingResponse
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    except ImportError:
        return JSONResponse(status_code=500, content={"error": "Package openpyxl belum diinstal di server."})

    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # Gunakan fungsi get_daily_attendance yang sudah ada untuk mengambil data
    response = await get_daily_attendance(target_date)
    if isinstance(response, JSONResponse):
        return response
    
    data = response.get("data", [])

    # Buat file Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Rekap {target_date}"

    # Styling Dasar
    header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))

    # Title Header
    ws.merge_cells('A1:H1')
    ws['A1'] = f"REKAP PRESENSI HARIAN RSUP - TANGGAL: {target_date}"
    ws['A1'].font = Font(bold=True, size=14)
    ws['A1'].alignment = center_align

    # Table Headers
    headers = ["No", "NIP / NIK", "Nama Pegawai", "Jam Masuk", "Jam Pulang", "Durasi Kerja", "Metode", "Status"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border

    # Isi Data
    for row_idx, row_data in enumerate(data, 4):
        ws.cell(row=row_idx, column=1, value=row_idx - 3).alignment = center_align
        ws.cell(row=row_idx, column=2, value=row_data.get("employee_nip", "-")).alignment = center_align
        ws.cell(row=row_idx, column=3, value=row_data.get("employee_name", "-")).alignment = left_align
        ws.cell(row=row_idx, column=4, value=row_data.get("checkin_time", "-")).alignment = center_align
        ws.cell(row=row_idx, column=5, value=row_data.get("checkout_time", "-")).alignment = center_align
        ws.cell(row=row_idx, column=6, value=row_data.get("duration", "-")).alignment = center_align
        ws.cell(row=row_idx, column=7, value=row_data.get("method", "-")).alignment = center_align
        ws.cell(row=row_idx, column=8, value=row_data.get("status", "-")).alignment = center_align

        # Apply border to all cells in the row
        for col in range(1, 9):
            ws.cell(row=row_idx, column=col).border = thin_border

    # Atur Lebar Kolom
    column_widths = {'A': 5, 'B': 22, 'C': 35, 'D': 22, 'E': 22, 'F': 15, 'G': 15, 'H': 20}
    for col, width in column_widths.items():
        ws.column_dimensions[col].width = width

    # -- SHEET 2: LOG LENGKAP (SEMUA TAP) --
    pool = get_db_pool()
    raw_logs = []
    if pool:
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    query_logs = """
                        SELECT 
                            r.timestamp, 
                            COALESCE(u.employee_id_number, '-') AS employee_nip, 
                            COALESCE(u.display_name, 'Belum Terpetakan') AS employee_name,
                            r.status, 
                            COALESCE(d.location, 'Mesin Absensi') AS location
                        FROM raw_attendance r
                        LEFT JOIN pin_employee_map pem ON (
                            r.pin = pem.pin 
                            OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM pem.pin) COLLATE utf8mb4_unicode_ci)
                        )
                        LEFT JOIN users u ON (
                            pem.employee_id = u.user_id 
                            OR (
                                pem.employee_id IS NULL AND (
                                    r.pin = u.employee_id_number 
                                    OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM u.employee_id_number) COLLATE utf8mb4_unicode_ci)
                                )
                            )
                        )
                        LEFT JOIN devices d ON r.device_sn = d.device_sn
                        WHERE DATE(r.timestamp) = %s
                        ORDER BY r.timestamp ASC
                    """
                    await cur.execute(query_logs, (target_date,))
                    raw_logs = await cur.fetchall()
        except Exception as e:
            logger.error(f"[API ERROR export_raw_logs] {e}", exc_info=True)

    ws2 = wb.create_sheet(title="Log Lengkap Semua Tap")
    ws2.merge_cells('A1:F1')
    ws2['A1'] = f"LOG LENGKAP SEMUA TAP MESIN - TANGGAL: {target_date}"
    ws2['A1'].font = Font(bold=True, size=14)
    ws2['A1'].alignment = center_align

    headers2 = ["No", "Waktu Tap", "NIP / NIK", "Nama Pegawai", "Status (Masuk/Pulang)", "Lokasi Mesin"]
    for col, h in enumerate(headers2, 1):
        cell = ws2.cell(row=3, column=col, value=h)
        cell.fill = PatternFill(start_color="3B82F6", end_color="3B82F6", fill_type="solid") # Warna Biru
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border

    for row_idx, r_log in enumerate(raw_logs, 4):
        ws2.cell(row=row_idx, column=1, value=row_idx - 3).alignment = center_align
        ws2.cell(row=row_idx, column=2, value=format_datetime(r_log[0])).alignment = center_align
        ws2.cell(row=row_idx, column=3, value=r_log[1]).alignment = center_align
        ws2.cell(row=row_idx, column=4, value=r_log[2]).alignment = left_align
        
        # Status code mapping
        st_code = r_log[3]
        st_val = "Masuk" if st_code == 0 else ("Pulang" if st_code == 1 else f"Code {st_code}")
        ws2.cell(row=row_idx, column=5, value=st_val).alignment = center_align
        
        ws2.cell(row=row_idx, column=6, value=r_log[4]).alignment = center_align
        
        for col in range(1, 7):
            ws2.cell(row=row_idx, column=col).border = thin_border

    col_widths2 = {'A': 5, 'B': 22, 'C': 20, 'D': 35, 'E': 25, 'F': 30}
    for col, width in col_widths2.items():
        ws2.column_dimensions[col].width = width

    # Simpan ke memori (BytesIO)
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    return StreamingResponse(
        stream, 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="Rekap_Presensi_RSUP_{target_date}.xlsx"'}
    )



# ── REST API: Manajemen Pemetaan PIN ke Pegawai RSUP ──────────────────────────
from pydantic import BaseModel

class PinMappingBody(BaseModel):
    pin: str
    employee_name: str
    employee_nip: str

@app.get("/api/v1/pin-mapping", tags=["Monitoring"], summary="Daftar semua PIN dan status pemetaan")
async def get_pin_mappings():
    """Melihat daftar seluruh PIN yang terdeteksi dari mesin dan pegawai yang dipetakan."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT 
                        r.pin,
                        TRIM(LEADING '0' FROM r.pin) AS norm_pin,
                        COALESCE(u.display_name, 'Belum Terpetakan') AS employee_name,
                        COALESCE(u.employee_id_number, '-') AS employee_nip,
                        COUNT(*) AS total_taps,
                        MAX(r.timestamp) AS last_tap
                    FROM raw_attendance r
                    LEFT JOIN pin_employee_map pem ON (
                        r.pin = pem.pin 
                        OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM pem.pin) COLLATE utf8mb4_unicode_ci)
                    )
                    LEFT JOIN users u ON (
                        pem.employee_id = u.user_id 
                        OR (
                            pem.employee_id IS NULL AND (
                                r.pin = u.employee_id_number 
                                OR (TRIM(LEADING '0' FROM r.pin) COLLATE utf8mb4_unicode_ci) = (TRIM(LEADING '0' FROM u.employee_id_number) COLLATE utf8mb4_unicode_ci)
                            )
                        )
                    )
                    GROUP BY r.pin, norm_pin, employee_name, employee_nip
                    ORDER BY CAST(norm_pin AS UNSIGNED) ASC
                """
                await cur.execute(query)
                rows = await cur.fetchall()

                data = [
                    {
                        "pin": str(r[0]).strip(),
                        "norm_pin": str(r[1]).strip(),
                        "employee_name": r[2],
                        "employee_nip": r[3],
                        "is_mapped": r[2] != "Belum Terpetakan",
                        "total_taps": r[4],
                        "last_tap": format_datetime(r[5]),
                    }
                    for r in rows
                ]
                return {"total": len(data), "data": data}
    except Exception as e:
        logger.error(f"[API ERROR get_pin_mappings] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/pin-mapping", tags=["Monitoring"], summary="Petakan PIN mesin ke Pegawai RSUP")
async def save_pin_mapping(body: PinMappingBody):
    """Menyimpan atau memperbarui pemetaan PIN mesin ke Nama & NIP Pegawai RSUP."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    clean_pin = body.pin.strip()
    norm_pin = clean_pin.lstrip("0") or "0"

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Periksa apakah user dengan NIP ini sudah ada di tabel users
                await cur.execute("SELECT user_id FROM users WHERE employee_id_number = %s", (body.employee_nip,))
                row = await cur.fetchone()
                if row:
                    user_id = row[0]
                    await cur.execute("UPDATE users SET display_name = %s WHERE user_id = %s", (body.employee_name, user_id))
                else:
                    await cur.execute(
                        "INSERT INTO users (employee_id_number, display_name, department_id, is_active) VALUES (%s, %s, 1, 1)",
                        (body.employee_nip, body.employee_name)
                    )
                    user_id = cur.lastrowid

                # 2. Masukkan ke pin_employee_map (baik pin asli maupun tanpa nol)
                await cur.execute(
                    "INSERT INTO pin_employee_map (pin, employee_id) VALUES (%s, %s) ON DUPLICATE KEY UPDATE employee_id = %s",
                    (clean_pin, user_id, user_id)
                )
                if norm_pin != clean_pin:
                    await cur.execute(
                        "INSERT INTO pin_employee_map (pin, employee_id) VALUES (%s, %s) ON DUPLICATE KEY UPDATE employee_id = %s",
                        (norm_pin, user_id, user_id)
                    )

                logger.info(f"[PIN MAPPED] PIN={clean_pin}/{norm_pin} -> {body.employee_name} ({body.employee_nip})")
                return {"status": "success", "message": f"PIN {clean_pin} berhasil dipetakan ke {body.employee_name}"}
    except Exception as e:
        logger.error(f"[API ERROR save_pin_mapping] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── Mount Folder Monitoring (Static Web Dashboard) ────────────────────────────
import os
from fastapi.staticfiles import StaticFiles

monitoring_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "monitoring")
if os.path.exists(monitoring_dir):
    app.mount("/monitoring", StaticFiles(directory=monitoring_dir, html=True), name="monitoring")


# ── Health Check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"], summary="Health check endpoint")
async def health_check():
    pool = get_db_pool()
    db_status = "connected" if pool else "disconnected"
    return {
        "status": "healthy",
        "service": "fingerspot-adms-middleware",
        "database": db_status,
        "mode": "real-time-adms",
    }


# ── REST API: Manajemen Shift ─────────────────────────────────────────────────

class ShiftBody(BaseModel):
    shift_code: str
    shift_name: str
    shift_type: str = "Reguler"
    checkin_time: str  # Format HH:MM
    checkout_time: str  # Format HH:MM
    is_next_day: bool = False
    late_tolerance_minutes: int = 15
    is_general: bool = False
    requires_attendance: bool = True

@app.get("/api/v1/shifts", tags=["Manajemen Shift"], summary="Daftar semua shift kerja")
async def get_shifts():
    """Melihat seluruh definisi shift kerja yang terdaftar di sistem."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT shift_id, shift_code, shift_name, shift_type,
                           checkin_time, checkout_time, is_next_day,
                           late_tolerance_minutes, is_general, requires_attendance
                    FROM shifts ORDER BY shift_id ASC
                    """
                )
                rows = await cur.fetchall()
                data = [
                    {
                        "shift_id": r[0],
                        "shift_code": r[1],
                        "shift_name": r[2],
                        "shift_type": r[3],
                        "checkin_time": r[4],
                        "checkout_time": r[5],
                        "is_next_day": bool(r[6]),
                        "late_tolerance_minutes": r[7],
                        "is_general": bool(r[8]),
                        "requires_attendance": bool(r[9]),
                    }
                    for r in rows
                ]
                return {"total": len(data), "data": data}
    except Exception as e:
        logger.error(f"[API ERROR get_shifts] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/shifts", tags=["Manajemen Shift"], summary="Tambah shift kerja baru")
async def create_shift(body: ShiftBody):
    """Menambahkan definisi shift kerja baru ke sistem."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO shifts 
                        (shift_code, shift_name, shift_type, checkin_time, checkout_time, 
                         is_next_day, late_tolerance_minutes, is_general, requires_attendance)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        body.shift_code, body.shift_name, body.shift_type,
                        body.checkin_time, body.checkout_time,
                        int(body.is_next_day), body.late_tolerance_minutes,
                        int(body.is_general), int(body.requires_attendance),
                    ),
                )
                return {"status": "success", "shift_id": cur.lastrowid, "message": f"Shift '{body.shift_name}' berhasil dibuat."}
    except Exception as e:
        logger.error(f"[API ERROR create_shift] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.put("/api/v1/shifts/{shift_id}", tags=["Manajemen Shift"], summary="Update shift kerja")
async def update_shift(shift_id: int, body: ShiftBody):
    """Memperbarui definisi shift kerja yang sudah ada."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE shifts SET
                        shift_code=%s, shift_name=%s, shift_type=%s,
                        checkin_time=%s, checkout_time=%s, is_next_day=%s,
                        late_tolerance_minutes=%s, is_general=%s, requires_attendance=%s
                    WHERE shift_id = %s
                    """,
                    (
                        body.shift_code, body.shift_name, body.shift_type,
                        body.checkin_time, body.checkout_time, int(body.is_next_day),
                        body.late_tolerance_minutes, int(body.is_general),
                        int(body.requires_attendance), shift_id,
                    ),
                )
                return {"status": "success", "message": f"Shift ID {shift_id} berhasil diperbarui."}
    except Exception as e:
        logger.error(f"[API ERROR update_shift] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── REST API: Jadwal Shift Per-Pegawai ────────────────────────────────────────

class ScheduleBody(BaseModel):
    user_id: int
    shift_id: int
    schedule_date: str  # Format YYYY-MM-DD

class BulkScheduleBody(BaseModel):
    user_id: int
    shift_id: int
    date_from: str  # Format YYYY-MM-DD
    date_to: str    # Format YYYY-MM-DD
    skip_weekends: bool = False  # True = skip Sabtu & Minggu

@app.get("/api/v1/shift-schedule", tags=["Manajemen Shift"], summary="Jadwal shift pegawai")
async def get_shift_schedule(
    user_id: Optional[int] = Query(None, description="Filter per pegawai"),
    date_from: Optional[str] = Query(None, description="Format YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="Format YYYY-MM-DD"),
):
    """Melihat jadwal shift pegawai dalam rentang tanggal tertentu."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    if not date_from:
        date_from = datetime.now().strftime("%Y-%m-%d")
    if not date_to:
        date_to = date_from
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                query = """
                    SELECT uss.id, uss.user_id, u.display_name, u.employee_id_number,
                           uss.shift_id, s.shift_name, s.checkin_time, s.checkout_time,
                           s.is_next_day, uss.schedule_date
                    FROM user_shift_schedules uss
                    JOIN users u ON uss.user_id = u.user_id
                    JOIN shifts s ON uss.shift_id = s.shift_id
                    WHERE uss.schedule_date BETWEEN %s AND %s
                """
                params = [date_from, date_to]
                if user_id:
                    query += " AND uss.user_id = %s"
                    params.append(user_id)
                query += " ORDER BY uss.schedule_date ASC, u.display_name ASC"
                await cur.execute(query, params)
                rows = await cur.fetchall()
                data = [
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "employee_name": r[2],
                        "employee_nip": r[3],
                        "shift_id": r[4],
                        "shift_name": r[5],
                        "checkin_time": r[6],
                        "checkout_time": r[7],
                        "is_next_day": bool(r[8]),
                        "schedule_date": str(r[9]),
                    }
                    for r in rows
                ]
                return {"total": len(data), "data": data}
    except Exception as e:
        logger.error(f"[API ERROR get_shift_schedule] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/shift-schedule", tags=["Manajemen Shift"], summary="Tetapkan jadwal shift pegawai")
async def set_shift_schedule(body: ScheduleBody):
    """Menetapkan shift untuk seorang pegawai pada tanggal tertentu."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO user_shift_schedules (user_id, shift_id, schedule_date)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE shift_id = %s
                    """,
                    (body.user_id, body.shift_id, body.schedule_date, body.shift_id),
                )
                return {"status": "success", "message": f"Jadwal shift untuk user {body.user_id} pada {body.schedule_date} berhasil ditetapkan."}
    except Exception as e:
        logger.error(f"[API ERROR set_shift_schedule] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/shift-schedule/bulk", tags=["Manajemen Shift"], summary="Tetapkan jadwal shift massal (rentang tanggal)")
async def bulk_set_shift_schedule(body: BulkScheduleBody):
    """Menetapkan shift yang sama untuk satu pegawai dalam rentang tanggal (bulk assign)."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    try:
        from datetime import date as date_type
        d_from = datetime.strptime(body.date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(body.date_to, "%Y-%m-%d").date()
        if d_to < d_from:
            return JSONResponse(status_code=400, content={"error": "date_to harus >= date_from"})
        
        inserted = 0
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                current = d_from
                while current <= d_to:
                    # Skip weekend jika diminta
                    if body.skip_weekends and current.weekday() >= 5:
                        current += timedelta(days=1)
                        continue
                    await cur.execute(
                        """
                        INSERT INTO user_shift_schedules (user_id, shift_id, schedule_date)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE shift_id = %s
                        """,
                        (body.user_id, body.shift_id, current, body.shift_id),
                    )
                    inserted += 1
                    current += timedelta(days=1)
        return {"status": "success", "inserted": inserted, "message": f"Berhasil menetapkan {inserted} jadwal shift."}
    except Exception as e:
        logger.error(f"[API ERROR bulk_set_shift_schedule] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})




# ── Universal ADMS & Fingerspot BNC Receiver (Root POST /) ────────────────────
from fastapi.responses import PlainTextResponse, Response
import json
from app.services.sync_service import (
    extract_json_and_binary,
    format_command_response_body,
    handle_realtime_enroll_data,
    get_next_pending_command,
    handle_command_result,
    queue_sync_all_users_to_all_devices,
    queue_sync_all_users_to_device,
    queue_time_sync,
)

@app.api_route("/", methods=["GET", "POST", "HEAD"], tags=["ADMS Protocol"], include_in_schema=False)
@app.api_route("/hdata.aspx", methods=["GET", "POST", "HEAD"], tags=["ADMS Protocol"], include_in_schema=False)
async def root_universal_handler(request: Request):
    """
    Menangani request root (POST / atau POST /hdata.aspx) protokol EBKN / FkWeb (FKDataHS103)
    dari seluruh mesin Fingerspot Revo WFV-208BNC.
    
    Mendukung komunikasi 2 arah secara real-time:
    - Tap absensi (realtime_glog / attendance push)
    - Pendaftaran sidik jari/wajah/user baru (realtime_enroll_data) -> REPLIKASI OTOMATIS KE MESIN LAIN
    - Polling perintah antrean ke mesin (receive_cmd)
    - Konfirmasi eksekusi perintah (send_cmd_result)
    """
    query_params = dict(request.query_params)
    headers = {k.lower(): v for k, v in request.headers.items()}

    # Jika GET browser tanpa header ADMS
    if request.method == "GET" and not query_params and "request_code" not in headers:
        return {
            "message": "Fingerspot ADMS Real-Time Middleware",
            "monitoring_dashboard": "/monitoring",
            "docs": "/docs",
            "health": "/health",
        }

    raw_body = await request.body()
    try:
        decoded = raw_body.decode("utf-8")
    except Exception:
        decoded = raw_body.decode("latin-1", errors="ignore")

    req_code = headers.get("request_code") or ""
    trans_id = headers.get("trans_id") or "RTLogSendAction"
    dev_id = headers.get("dev_id") or query_params.get("SN") or query_params.get("sn") or "C2657C94530C2D25"

    if req_code != "receive_cmd":
        logger.info("=" * 65)
        logger.info(f"[BNC INCOMING {request.method}] req_code={req_code} | trans_id={trans_id} | dev_id={dev_id}")
        if raw_body:
            logger.info(f"[BNC BODY] {decoded.strip()[:300]}")
        logger.info("=" * 65)

    pool = get_db_pool()
    if pool:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Catat status mesin aktif di tabel devices
                await cur.execute("SELECT device_sn FROM devices WHERE device_sn = %s", (dev_id,))
                existing_dev = await cur.fetchone()
                if not existing_dev:
                    logger.info(f"[NEW DEVICE DETECTED] Mesin baru terhubung: {dev_id}. Mendaftarkan & mengantrekan seluruh data pegawai...")
                    await cur.execute(
                        """
                        INSERT INTO devices (device_sn, location, comm_key, is_master, last_seen_at)
                        VALUES (%s, CONCAT('Fingerspot Mesin (', RIGHT(%s, 6), ')'), '0', FALSE, NOW())
                        """,
                        (dev_id, dev_id),
                    )
                    # Otomatis isi mesin baru ini dengan seluruh pegawai & sidik jari yang sudah ada di database
                    await queue_sync_all_users_to_device(cur, dev_id)
                else:
                    await cur.execute("UPDATE devices SET last_seen_at = NOW() WHERE device_sn = %s", (dev_id,))

                # 2. Polling Perintah Mesin: receive_cmd
                if req_code == "receive_cmd":
                    pending = await get_next_pending_command(cur, dev_id)
                    if pending:
                        logger.info(
                            f"[DISPATCH CMD] SN={dev_id} | Code={pending['cmd_code']} | Trans={pending['trans_id']} | Bytes={len(pending['body_bytes'])}"
                        )
                        return Response(
                            content=pending["body_bytes"],
                            status_code=200,
                            headers={
                                "response_code": "OK",
                                "trans_id": pending["trans_id"],
                                "cmd_code": pending["cmd_code"],
                                "dev_id": dev_id,
                                "Content-Length": str(len(pending["body_bytes"])),
                                "Connection": "close",
                            },
                            media_type="application/octet-stream",
                        )
                    # Tidak ada perintah: kembalikan idle ACK (0-byte body)
                    return Response(
                        content=b"",
                        status_code=200,
                        headers={
                            "response_code": "OK",
                            "trans_id": trans_id,
                            "dev_id": dev_id,
                            "Connection": "close",
                        },
                        media_type="text/plain",
                    )

                # 3. Laporan Hasil Eksekusi Perintah: send_cmd_result
                if req_code == "send_cmd_result":
                    ret_code = headers.get("cmd_return_code") or "0"
                    await handle_command_result(cur, dev_id, trans_id, ret_code, raw_body)
                    return Response(
                        content=b"",
                        status_code=200,
                        headers={
                            "response_code": "OK",
                            "trans_id": trans_id,
                            "dev_id": dev_id,
                            "Connection": "close",
                        },
                        media_type="text/plain",
                    )

                # 4. Pendaftaran Baru pada Mesin Fisik: realtime_enroll_data
                # Otomatis mereplikasi user/template ke seluruh mesin finger lainnya
                if req_code == "realtime_enroll_data":
                    parsed, _ = extract_json_and_binary(raw_body)
                    res = await handle_realtime_enroll_data(cur, dev_id, raw_body, parsed)
                    logger.info(f"[AUTO ENROLL REPLICATION] Hasil replikasi dari {dev_id}: {res}")
                    return Response(
                        content=b"",
                        status_code=200,
                        headers={
                            "response_code": "OK",
                            "trans_id": trans_id,
                            "dev_id": dev_id,
                            "Connection": "close",
                        },
                        media_type="text/plain",
                    )

                # 5. Parse data absensi (realtime_glog atau JSON push)
                pin = None
                ts_str = None
                status = 0
                verify_mode = 1

                parsed, _ = extract_json_and_binary(raw_body)
                if parsed and isinstance(parsed, dict):
                    pin = str(parsed.get("user_id") or "").strip()
                    io_time = str(parsed.get("io_time") or "").strip()

                    # Format io_time "20260925124509" (YYYYMMDDHHmmss)
                    if len(io_time) == 14 and io_time.isdigit():
                        dt = datetime.strptime(io_time, "%Y%m%d%H%M%S")
                        ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    elif io_time:
                        ts_str = io_time

                    # Status: 16777216 = In (0), 33554432 = Out (1)
                    io_mode = parsed.get("io_mode", 0)
                    mode_hi = (io_mode >> 24) & 0xFF
                    if mode_hi in (2, 4, 6) or io_mode in (33554432, 67108864, 100663296):
                        status = 1
                    else:
                        status = 0

                    # Verifikasi: 1879048192 (0x70000000) atau 2 = Wajah, 1 = Sidik Jari
                    v_mode = parsed.get("verify_mode", 1)
                    if v_mode in (2, 1879048192):
                        verify_mode = 2
                    elif v_mode == 15:
                        verify_mode = 15
                    else:
                        verify_mode = 1

                # 6. Simpan tap absensi ke database jika pin & ts_str valid
                if pin and ts_str:
                    logger.info(f"[SUCCESS PARSED TAP] Mesin={dev_id} | PIN={pin} | Waktu={ts_str} | Status={'Masuk' if status==0 else 'Pulang'} | Verif={verify_mode}")
                    await cur.execute(
                        """
                        INSERT INTO raw_attendance 
                            (device_sn, pin, timestamp, status, verify_mode)
                        VALUES (%s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE received_at = received_at
                        """,
                        (dev_id, pin, ts_str, status, verify_mode),
                    )

                    # ── Smart Attendance Upsert dengan Logika Shift ────────────────────
                    try:
                        dt_obj = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        clean_pin = pin.lstrip("0") or "0"

                        # [GRACE-PERIOD] Cek apakah tap ini adalah duplikat dalam 5 detik dari mesin lain
                        await cur.execute(
                            """
                            SELECT id FROM raw_attendance 
                            WHERE pin = %s AND device_sn != %s
                              AND ABS(TIMESTAMPDIFF(SECOND, timestamp, %s)) <= 5
                            LIMIT 1
                            """,
                            (pin, dev_id, ts_str),
                        )
                        is_duplicate = await cur.fetchone()
                        if is_duplicate:
                            logger.info(f"[GRACE-PERIOD] Tap duplikat dalam 5 detik diabaikan: PIN={pin}")
                        else:
                            # Cari user_id berdasarkan PIN
                            await cur.execute(
                                "SELECT employee_id FROM pin_employee_map WHERE pin = %s OR TRIM(LEADING '0' FROM pin) = %s LIMIT 1",
                                (pin, clean_pin),
                            )
                            map_row = await cur.fetchone()
                            user_id = map_row[0] if map_row else None

                            if not user_id:
                                await cur.execute(
                                    """
                                    SELECT user_id FROM users 
                                    WHERE employee_id_number = %s 
                                       OR employee_id_number = %s 
                                       OR user_id = %s 
                                    LIMIT 1
                                    """,
                                    (pin, clean_pin, clean_pin if clean_pin.isdigit() else -1),
                                )
                                u_row = await cur.fetchone()
                                user_id = u_row[0] if u_row else None

                            if user_id:
                                # [SHIFT-NIGHT] Tentukan attendance_date yang benar
                                att_date = dt_obj.date()
                                shift_id = None
                                shift_checkin_str = None
                                shift_is_next_day = False
                                shift_row = None

                                # Cek jadwal shift untuk HARI INI
                                await cur.execute(
                                    """
                                    SELECT uss.shift_id, s.checkin_time, s.checkout_time, 
                                           s.is_next_day, s.late_tolerance_minutes, s.requires_attendance
                                    FROM user_shift_schedules uss
                                    JOIN shifts s ON uss.shift_id = s.shift_id
                                    WHERE uss.user_id = %s AND uss.schedule_date = %s
                                    LIMIT 1
                                    """,
                                    (user_id, att_date),
                                )
                                shift_row = await cur.fetchone()

                                if shift_row:
                                    shift_id = shift_row[0]
                                    shift_checkin_str = shift_row[1]
                                    shift_is_next_day = bool(shift_row[3])

                                # Cek jadwal KEMARIN — untuk tap pulang shift malam (is_next_day=1)
                                if not shift_row or (shift_row and not shift_row[5]):
                                    yesterday = att_date - timedelta(days=1)
                                    await cur.execute(
                                        """
                                        SELECT uss.shift_id, s.checkin_time, s.checkout_time,
                                               s.is_next_day, s.late_tolerance_minutes, s.requires_attendance
                                        FROM user_shift_schedules uss
                                        JOIN shifts s ON uss.shift_id = s.shift_id
                                        WHERE uss.user_id = %s AND uss.schedule_date = %s AND s.is_next_day = 1
                                        LIMIT 1
                                        """,
                                        (user_id, yesterday),
                                    )
                                    yday_shift = await cur.fetchone()
                                    if yday_shift and yday_shift[5] and dt_obj.hour < 14:
                                        att_date = yesterday
                                        shift_id = yday_shift[0]
                                        shift_checkin_str = yday_shift[1]
                                        shift_is_next_day = True
                                        shift_row = yday_shift
                                        logger.info(f"[SHIFT-MALAM] PIN={pin} tap {dt_obj.strftime('%H:%M')} = bagian shift malam {yesterday}")

                                # [LATE-CALC] Hitung keterlambatan dari jadwal shift
                                late_minutes = 0
                                is_late = 0
                                attendance_status = 'HADIR'
                                tolerance = shift_row[4] if shift_row else 15

                                if shift_checkin_str and not shift_is_next_day:
                                    await cur.execute(
                                        "SELECT checkin_time FROM attendances WHERE user_id = %s AND attendance_date = %s",
                                        (user_id, att_date),
                                    )
                                    existing_att = await cur.fetchone()
                                    if not existing_att:  # Tap pertama = check-in
                                        try:
                                            sched_cin = datetime.strptime(
                                                f"{att_date} {shift_checkin_str}:00", "%Y-%m-%d %H:%M:%S"
                                            )
                                            diff_min = int((dt_obj - sched_cin).total_seconds() / 60)
                                            if diff_min > tolerance:
                                                late_minutes = diff_min - tolerance
                                                is_late = 1
                                                attendance_status = 'TL1' if late_minutes <= 30 else ('TL2' if late_minutes <= 60 else 'TL3')
                                            logger.info(f"[LATE-CALC] PIN={pin} | Jadwal={shift_checkin_str} | Tap={dt_obj.strftime('%H:%M')} | Terlambat={late_minutes}m | Status={attendance_status}")
                                        except Exception as e_late:
                                            logger.warning(f"[LATE-CALC] Gagal hitung: {e_late}")

                                # [UPSERT] Simpan atau update tabel attendances
                                await cur.execute(
                                    "SELECT attendance_id, checkin_time, checkout_time FROM attendances WHERE user_id = %s AND attendance_date = %s",
                                    (user_id, att_date),
                                )
                                att_ex = await cur.fetchone()
                                if not att_ex:
                                    await cur.execute(
                                        """
                                        INSERT INTO attendances 
                                            (user_id, shift_id, attendance_date, checkin_time, checkout_time,
                                             attendance_method, attendance_status, late_minutes, is_late, updated_at)
                                        VALUES (%s, %s, %s, %s, %s, 'FINGER', %s, %s, %s, NOW())
                                        """,
                                        (
                                            user_id, shift_id, att_date, dt_obj,
                                            dt_obj if status == 1 else None,
                                            attendance_status, late_minutes, is_late,
                                        ),
                                    )
                                else:
                                    att_id, cin_time, cout_time = att_ex
                                    if cin_time:
                                        diff_sec = (dt_obj - cin_time).total_seconds()
                                        if diff_sec >= 30:
                                            if not cout_time or dt_obj >= cout_time:
                                                working_min = max(0, int((dt_obj - cin_time).total_seconds() / 60))
                                                await cur.execute(
                                                    """
                                                    UPDATE attendances 
                                                    SET checkout_time = %s, working_minutes = %s, updated_at = NOW()
                                                    WHERE attendance_id = %s
                                                    """,
                                                    (dt_obj, working_min, att_id),
                                                )
                                                logger.info(f"[CHECKOUT] PIN={pin} | Pulang={dt_obj.strftime('%H:%M')} | Durasi={working_min}m")

                    except Exception as e:
                        logger.error(f"[AUTO SYNC ATTENDANCE ERROR] {e}", exc_info=True)


                        # Cari user_id berdasarkan PIN
                        await cur.execute(
                            "SELECT employee_id FROM pin_employee_map WHERE pin = %s OR TRIM(LEADING '0' FROM pin) = %s LIMIT 1",
                            (pin, clean_pin),
                        )
                        map_row = await cur.fetchone()
                        user_id = map_row[0] if map_row else None

                        if not user_id:
                            await cur.execute(
                                """
                                SELECT user_id FROM users 
                                WHERE employee_id_number = %s 
                                   OR employee_id_number = %s 
                                   OR user_id = %s 
                                LIMIT 1
                                """,
                                (pin, clean_pin, clean_pin if clean_pin.isdigit() else -1),
                            )
                            u_row = await cur.fetchone()
                            user_id = u_row[0] if u_row else None

                        if user_id:
                            # [SHIFT-NIGHT] Tentukan attendance_date yang benar
                            # Cek apakah pegawai ini punya jadwal shift malam kemarin
                            # yang jam pulangnya hari ini (is_next_day=1)
                            att_date = dt_obj.date()
                            shift_id = None
                            shift_checkin_str = None
                            shift_is_next_day = False

                            # Cek jadwal untuk HARI INI
                            await cur.execute(
                                """
                                SELECT uss.shift_id, s.checkin_time, s.checkout_time, 
                                       s.is_next_day, s.late_tolerance_minutes, s.requires_attendance
                                FROM user_shift_schedules uss
                                JOIN shifts s ON uss.shift_id = s.shift_id
                                WHERE uss.user_id = %s AND uss.schedule_date = %s
                                LIMIT 1
                                """,
                                (user_id, att_date),
                            )
                            shift_row = await cur.fetchone()

                            if shift_row:
                                shift_id = shift_row[0]
                                shift_checkin_str = shift_row[1]
                                shift_is_next_day = bool(shift_row[3])

                            # Jika tidak ada jadwal hari ini, cek jadwal KEMARIN 
                            # (untuk kasus tap pulang shift malam yang is_next_day=1)
                            if not shift_row or (shift_row and not shift_row[5]):  # requires_attendance = 0
                                yesterday = att_date - timedelta(days=1)
                                await cur.execute(
                                    """
                                    SELECT uss.shift_id, s.checkin_time, s.checkout_time,
                                           s.is_next_day, s.late_tolerance_minutes, s.requires_attendance
                                    FROM user_shift_schedules uss
                                    JOIN shifts s ON uss.shift_id = s.shift_id
                                    WHERE uss.user_id = %s AND uss.schedule_date = %s AND s.is_next_day = 1
                                    LIMIT 1
                                    """,
                                    (user_id, yesterday),
                                )
                                yday_shift = await cur.fetchone()
                                if yday_shift and yday_shift[5]:  # requires_attendance = 1
                                    # Tap pagi ini adalah bagian dari shift malam kemarin
                                    # Periksa apakah jam tap masih dalam window shift malam (sebelum jam 14:00)
                                    if dt_obj.hour < 14:
                                        att_date = yesterday  # Attendance date = kemarin
                                        shift_id = yday_shift[0]
                                        shift_checkin_str = yday_shift[1]
                                        shift_is_next_day = True
                                        shift_row = yday_shift
                                        logger.info(f"[SHIFT-MALAM] PIN={pin} tap jam {dt_obj.hour}:xx dianggap bagian dari shift malam {yesterday}")

                            # [LATE-CALC] Hitung keterlambatan jika ada jadwal shift
                            late_minutes = 0
                            is_late = 0
                            attendance_status = 'HADIR'
                            tolerance = shift_row[4] if shift_row else 15

                            if shift_checkin_str and not shift_is_next_day:
                                # Hanya hitung keterlambatan untuk tap PERTAMA (check-in)
                                await cur.execute(
                                    "SELECT checkin_time FROM attendances WHERE user_id = %s AND attendance_date = %s",
                                    (user_id, att_date),
                                )
                                existing_att = await cur.fetchone()
                                if not existing_att:  # Ini adalah tap pertama (check-in)
                                    try:
                                        sched_cin = datetime.strptime(
                                            f"{att_date} {shift_checkin_str}:00", "%Y-%m-%d %H:%M:%S"
                                        )
                                        diff_min = int((dt_obj - sched_cin).total_seconds() / 60)
                                        if diff_min > tolerance:
                                            late_minutes = diff_min - tolerance
                                            is_late = 1
                                            attendance_status = 'TL1' if late_minutes <= 30 else ('TL2' if late_minutes <= 60 else 'TL3')
                                        logger.info(f"[LATE-CALC] PIN={pin} | Jadwal={shift_checkin_str} | Tap={dt_obj.strftime('%H:%M')} | Terlambat={late_minutes}m")
                                    except Exception as e_late:
                                        logger.warning(f"[LATE-CALC] Gagal hitung keterlambatan: {e_late}")

                            # [UPSERT] Simpan / update tabel attendances
                            await cur.execute(
                                "SELECT attendance_id, checkin_time, checkout_time FROM attendances WHERE user_id = %s AND attendance_date = %s",
                                (user_id, att_date),
                            )
                            att_ex = await cur.fetchone()

                            if not att_ex:
                                # Baris baru: tap ini adalah CHECK-IN
                                await cur.execute(
                                    """
                                    INSERT INTO attendances 
                                        (user_id, shift_id, attendance_date, checkin_time, checkout_time,
                                         attendance_method, attendance_status, late_minutes, is_late, updated_at)
                                    VALUES (%s, %s, %s, %s, %s, 'FINGER', %s, %s, %s, NOW())
                                    """,
                                    (
                                        user_id, shift_id, att_date, dt_obj,
                                        dt_obj if status == 1 else None,
                                        attendance_status, late_minutes, is_late,
                                    ),
                                )
                            else:
                                att_id, cin_time, cout_time = att_ex
                                if cin_time:
                                    diff_sec = (dt_obj - cin_time).total_seconds()
                                    # Update checkout_time jika tap ini lebih baru (Tap Terakhir = Pulang)
                                    if diff_sec >= 30:  # grace period 30 detik min
                                        if not cout_time or dt_obj >= cout_time:
                                            working_min = max(0, int((dt_obj - cin_time).total_seconds() / 60))
                                            await cur.execute(
                                                """
                                                UPDATE attendances 
                                                SET checkout_time = %s, working_minutes = %s, updated_at = NOW()
                                                WHERE attendance_id = %s
                                                """,
                                                (dt_obj, working_min, att_id),
                                            )
                                            logger.info(f"[CHECKOUT] PIN={pin} | Pulang={dt_obj.strftime('%H:%M')} | Durasi={working_min}m")

                    except Exception as e:
                        logger.error(f"[AUTO SYNC ATTENDANCE ERROR] {e}", exc_info=True)


    # Respon ACK standar Protokol EBKN / FkWeb:
    response_headers = {
        "response_code": "OK",
        "trans_id": trans_id,
        "dev_id": dev_id,
        "Connection": "close",
    }
    return Response(
        content=b"",
        status_code=200,
        headers=response_headers,
        media_type="text/plain",
    )


# ── REST API: Manajemen Sinkronisasi Antar-Mesin ───────────────────────────────
@app.get("/api/v1/sync/status", tags=["Sync Middleware"], summary="Status sinkronisasi antar mesin")
async def get_sync_status():
    """Melihat status antrean perintah sinkronisasi dan daftar mesin aktif."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 1. Daftar Mesin
                await cur.execute("SELECT device_sn, location, is_master, last_seen_at FROM devices")
                devices_rows = await cur.fetchall()
                devices = [
                    {
                        "device_sn": r[0],
                        "location": r[1],
                        "is_master": bool(r[2]),
                        "last_seen_at": format_datetime(r[3]),
                    }
                    for r in devices_rows
                ]

                # 2. Statistik Antrean Perintah
                await cur.execute(
                    """
                    SELECT 
                        COUNT(*),
                        COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN status = 'SENT' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END), 0),
                        COALESCE(SUM(CASE WHEN status LIKE 'FAILED%%' THEN 1 ELSE 0 END), 0)
                    FROM adms_commands
                    """
                )
                stats_row = await cur.fetchone()

                # 3. 15 Perintah Terakhir
                await cur.execute(
                    """
                    SELECT id, device_sn, cmd_code, status, command_text, return_code, created_at, updated_at
                    FROM adms_commands
                    ORDER BY id DESC
                    LIMIT 15
                    """
                )
                cmd_rows = await cur.fetchall()
                recent_commands = [
                    {
                        "id": r[0],
                        "device_sn": r[1],
                        "cmd_code": r[2],
                        "status": r[3],
                        "command_text": r[4],
                        "return_code": r[5],
                        "created_at": format_datetime(r[6]),
                        "updated_at": format_datetime(r[7]),
                    }
                    for r in cmd_rows
                ]

                return {
                    "devices": devices,
                    "queue_summary": {
                        "total_commands": int(stats_row[0]),
                        "pending": int(stats_row[1]),
                        "sent": int(stats_row[2]),
                        "success": int(stats_row[3]),
                        "failed": int(stats_row[4]),
                    },
                    "recent_commands": recent_commands,
                }
    except Exception as e:
        logger.error(f"[API ERROR get_sync_status] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/sync/push-all", tags=["Sync Middleware"], summary="Sinkronkan seluruh pegawai ke semua mesin")
async def trigger_push_all_sync():
    """Mengantrekan perintah sinkronisasi seluruh pegawai dan sidik jari ke seluruh mesin fisik."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                result = await queue_sync_all_users_to_all_devices(cur)
                return result
    except Exception as e:
        logger.error(f"[API ERROR push-all] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


class TimeSyncBody(BaseModel):
    target_device_sn: Optional[str] = None

@app.post("/api/v1/sync/set-time", tags=["Sync Middleware"], summary="Sinkronkan jam mesin ke waktu server")
async def trigger_time_sync(body: Optional[TimeSyncBody] = None):
    """Mengirim perintah penyesuaian waktu (SET_TIME) ke mesin agar presisi."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    target_sn = body.target_device_sn if body else None
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                count = await queue_time_sync(cur, target_sn)
                return {"status": "success", "message": f"Perintah sinkronisasi jam diantrekan ke {count} mesin."}
    except Exception as e:
        logger.error(f"[API ERROR set-time] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


class PullUserBody(BaseModel):
    device_sn: str

@app.post("/api/v1/sync/pull-from-device", tags=["Sync Middleware"], summary="Tarik daftar user dari mesin fisik")
async def trigger_pull_from_device(body: PullUserBody):
    """Mengantrekan perintah GET_USER_ID_LIST ke mesin tertentu untuk menarik seluruh user."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                expires_at = datetime.now() + timedelta(hours=2)
                await cur.execute(
                    """
                    INSERT INTO adms_commands 
                        (device_sn, cmd_code, payload, payload_type, command_text, status, expires_at)
                    VALUES (%s, 'GET_USER_ID_LIST', NULL, 'JSON', 'GET_USER_ID_LIST', 'PENDING', %s)
                    """,
                    (body.device_sn, expires_at),
                )
                return {
                    "status": "success",
                    "message": f"Perintah penarikan user (GET_USER_ID_LIST) berhasil diantrekan ke mesin {body.device_sn}.",
                }
    except Exception as e:
        logger.error(f"[API ERROR pull-from-device] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/sync/retry-failed", tags=["Sync Middleware"], summary="Ulangi perintah sinkronisasi yang gagal")
async def retry_failed_commands():
    """Mengembalikan status perintah FAILED atau EXPIRED kembali ke PENDING."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})

    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE adms_commands SET status = 'PENDING' WHERE status LIKE 'FAILED%%' OR status = 'EXPIRED'"
                )
                return {"status": "success", "message": f"Berhasil mereset perintah gagal menjadi PENDING ({cur.rowcount} perintah)."}
    except Exception as e:
        logger.error(f"[API ERROR retry-failed] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})





# ── REST API: Revoke Fingerprint Karyawan (SYNC-05) ───────────────────────────
class _RevokeBody:
    pin: str
    reason: str = "Pencabutan biometrik oleh HR/IT"

from pydantic import BaseModel as _RevokeModel

class RevokeBody(_RevokeModel):
    pin: str
    reason: str = "Pencabutan biometrik oleh HR/IT"

@app.post("/api/v1/sync/revoke-fingerprint", tags=["Sync Middleware"], summary="[SYNC-05] Cabut sidik jari karyawan dari semua mesin")
async def revoke_fingerprint(body: RevokeBody):
    """[SYNC-05] Cabut biometrik karyawan resign dari seluruh mesin fisik."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                result = await queue_revoke_fingerprint(cur, body.pin)
                logger.warning(f"[SYNC-05 REVOKE] PIN={body.pin} | Alasan: {body.reason} | Hasil: {result}")
                return result
    except Exception as e:
        logger.error(f"[API ERROR revoke-fingerprint] {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/v1/sync/cleanup-commands", tags=["Sync Middleware"], summary="[SYNC-06] Bersihkan perintah lama")
async def trigger_cleanup_commands():
    """[SYNC-06] Bersihkan perintah command lama: timeout SENT >24j, hapus SUCCESS/FAILED >30 hari."""
    pool = get_db_pool()
    if not pool:
        return JSONResponse(status_code=503, content={"error": "Database belum terhubung"})
    result = await cleanup_old_commands(pool)
    return {"status": "success", "result": result}
