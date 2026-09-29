# 🖐️ Fingerspot Real-Time ADMS Middleware (MySQL)

Middleware mandiri berbasis **Python + FastAPI + MySQL** untuk menerima dan memproses data absensi dari mesin **Fingerspot Revo series** secara **Real-Time** melalui protokol **ADMS / Push HTTP** — **100% lokal on-premise tanpa bergantung pada cloud pihak ketiga**.

---

## 📑 Daftar Isi
- [Arsitektur Sistem](#-arsitektur-sistem)
- [Struktur Proyek](#-struktur-proyek)
- [Konfigurasi Lingkungan (`.env`)](#-konfigurasi-lingkungan-env)
- [Panduan Pengaturan Mesin Fisik](#-panduan-pengaturan-mesin-fisik-fingerspot-revo)
- [Cara Menjalankan](#-cara-menjalankan)
- [Referensi Endpoint ADMS & REST API](#-referensi-endpoint-adms--rest-api)
- [Skema Database Lokal](#-skema-database-lokal-presensi_local)
- [Pengujian & Simulasi](#-pengujian--simulasi-simulator)
- [Langkah Integrasi ke Staging RSUP](#-langkah-integrasi-ke-database-staging-rsup)
- [Troubleshooting & FAQ](#-troubleshooting--faq)

---

## 📐 Arsitektur Sistem

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Jaringan LAN / Intranet                         │
│                                                                        │
│  ┌──────────────────────┐  HTTP POST/GET (ADMS)  ┌──────────────────┐  │
│  │   Fingerspot Revo    │───────────────────────►│     FastAPI      │  │
│  │    (Client ADMS)     │       Port: 5005       │ ADMS Middleware  │  │
│  │  (Tap Sidik Jari)    │                        └────────┬─────────┘  │
│  └──────────────────────┘                                 │            │
│                                                   aiomysql│ Connection │
│                                                   Pool    │            │
│                                                  ┌────────▼─────────┐  │
│                                                  │    MySQL 8.0     │  │
│                                                  │ (presensi_local) │  │
│                                                  └──────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘

Dokumentasi Swagger : http://localhost:5005/docs (atau http://localhost:8000/docs)
ADMS Receiver Base  : http://<IP_KOMPUTER_SERVER>:5005/iclock/cdata
```

### Alur Kerja Real-Time:
1. **Handshake (`GET /iclock/cdata`)**: Mesin pertama kali menyapa server. Server membalas konfigurasi `Realtime=1` (memerintahkan mesin agar langsung mengirim data seketika setiap kali ada tap).
2. **Ingest Tap (`POST /iclock/cdata?table=ATTLOG`)**: Begitu ada pegawai menempelkan jari/kartu/wajah, mesin langsung mengirimkan HTTP POST.
3. **Penyimpanan Dua Lapis**:
   * **Lapisan 1 (Audit Mentah)**: Disimpan ke `raw_attendance` (anti-duplikasi via unique scan key).
   * **Lapisan 2 (Sistem Presensi)**: Dipetakan via `pin_employee_map` atau `users` dan langsung dicatat ke tabel `attendances` (Tap pertama = Check-in, Tap kedua = Check-out).
4. **Konfirmasi (`OK: 1`)**: Server mengembalikan respon `OK: 1` agar log di antrean memori mesin ditandai sukses.

---

## 📁 Struktur Proyek

```
Finger/
├── app/
│   ├── api/
│   │   ├── __init__.py
│   │   └── adms.py          # Handler protokol ADMS (/iclock/cdata, /getrequest, /devicecmd)
│   ├── __init__.py
│   ├── config.py            # Konfigurasi aplikasi via Pydantic Settings
│   ├── database.py          # aiomysql asynchronous connection pool
│   └── main.py              # Entry point FastAPI, lifecycle, & API monitoring
├── .dockerignore
├── .env                     # Konfigurasi aktif lingkungan Anda
├── .env.example             # Template konfigurasi
├── .gitignore
├── docker-compose.yml       # Stack Docker: MySQL 8.0 + FastAPI App (Port 5005 & 8000)
├── Dockerfile               # Multi-stage Docker build untuk image yang ringan
├── init_mysql.sql           # Skema tabel MySQL lokal & mock seed data
├── requirements.txt         # Dependensi Python
├── test_adms.py             # Script simulator pengujian ADMS tanpa mesin fisik
└── README.md                # Dokumentasi lengkap
```

---

## ⚙️ Konfigurasi Lingkungan (`.env`)

File `.env` mengatur parameter database dan port server:

```env
# --- Database MySQL Lokal ---
DB_HOST=db                     # 'db' untuk Docker Compose, '127.0.0.1' untuk jalan lokal
DB_PORT=3306
DB_USER=adms_user
DB_PASSWORD=adms_password
DB_NAME=presensi_local

# --- Enkripsi PDP Template Biometrik (Fernet 32-byte Base64) ---
ENCRYPTION_KEY=g-zVvD9_d2z-2Bv9GkY2tZ8Sj7XoBwO_c6L_x5Jm3rE=

# --- Server Port ---
API_HOST=0.0.0.0
API_PORT=5005                  # Port sesuai pengaturan Cloud Server di mesin
DEBUG=false
AUTO_REGISTER_DEVICE=true      # Otomatis daftarkan mesin baru yang melakukan handshake
```

---

## 📟 Panduan Pengaturan Mesin Fisik Fingerspot Revo

Untuk mengarahkan mesin Fingerspot Revo ke server lokal ini:

1. Nyalakan mesin dan tekan tombol **Menu**.
2. Masuk ke submenu: **Komunikasi** (Communication) → **Cloud Server** (atau **ADMS / Server PC / Web Server**).
3. Atur parameter berikut:
   | Parameter | Nilai Pengaturan | Keterangan |
   |---|---|---|
   | **Server IP (Alamat Server)** | `10.10.165.118` | Masukkan IP Komputer Server tempat aplikasi berjalan |
   | **Server Port** | `5005` | Port yang didengarkan oleh FastAPI ADMS |
   | **Domain Name (Nama Domain)** | **Off (Nonaktif)** | Pastikan dinonaktifkan jika menggunakan IP lokal |
   | **Aktifkan Server Cloud / ADMS** | **Ya / On** | Mengaktifkan fitur push client mesin |
4. Pastikan juga IP Ethernet mesin berada di subnet yang sama dengan server (misal `10.10.165.xxx`).
5. Simpan pengaturan dan restart mesin jika diminta.

> Saat mesin selesai booting dan tersambung ke jaringan, mesin akan otomatis mengirim request handshake ke `http://10.10.165.118:5005/iclock/cdata`.

---

## 🚀 Cara Menjalankan

### Opsi 1: Menggunakan Docker Compose (Direkomendasikan)

Semua dependensi dan MySQL 8.0 akan otomatis diinstal dan dijalankan di dalam container terisolasi:

```bash
# 1. Jalankan stack (MySQL + FastAPI)
docker compose up -d --build

# 2. Pantau log server real-time
docker compose logs -f app

# 3. Hentikan container (jika ingin berhenti)
docker compose down
```

Layanan yang berjalan:
| Layanan | Container Name | Port Host | Fungsi |
|---|---|---|---|
| **FastAPI ADMS** | `finger_adms_app` | `5005` & `8000` | Menerima push dari mesin & REST API |
| **MySQL 8.0** | `finger_mysql` | `3307` | Database lokal (`presensi_local`, port 3307 di host agar tidak bentrok dengan Laragon/XAMPP) |

---

### Opsi 2: Tanpa Docker (Local Python)

Jika Anda ingin menjalankan langsung di Python host (memerlukan MySQL lokal di Windows):

```bash
# 1. Buat dan aktifkan virtual environment
python -m venv venv
venv\Scripts\activate

# 2. Install dependensi
pip install -r requirements.txt

# 3. Impor skema tabel ke MySQL Anda
mysql -u root -p < init_mysql.sql

# 4. Sesuaikan DB_HOST=127.0.0.1 di .env lalu jalankan
uvicorn app.main:app --host 0.0.0.0 --port 5005 --reload
```

---

## 📡 Referensi Endpoint ADMS & REST API

### 1. Endpoint Protokol ADMS (Dikonsumsi Mesin Fingerspot)

| Method | Endpoint | Deskripsi |
|---|---|---|
| `GET` | `/iclock/cdata` (atau `/cdata`) | **Handshake**: Mesin menyapa server; server membalas dengan `Realtime=1`. |
| `POST` | `/iclock/cdata` (atau `/cdata`) | **Ingest Data**: Menerima tap absensi (`table=ATTLOG`) atau template sidik jari (`table=FINGERTMP`). |
| `GET` | `/iclock/getrequest` (atau `/getrequest`) | **Polling Perintah**: Mesin mengambil antrean perintah (misal sinkronisasi sidik jari antar mesin). |
| `POST` | `/iclock/devicecmd` (atau `/devicecmd`) | **Konfirmasi Perintah**: Mesin melaporkan status keberhasilan eksekusi perintah (`SUCCESS`/`FAILED`). |

### 2. Endpoint Monitoring & REST API (Dikonsumsi Pengembang/Sistem)

| Method | Endpoint | Deskripsi |
|---|---|---|
| `GET` | `/health` | Memeriksa status kesehatan server dan koneksi database MySQL. |
| `GET` | `/docs` | Antarmuka interaktif Swagger UI untuk eksplorasi API. |
| `GET` | `/api/v1/raw-attendance` | Melihat daftar tap absensi mentah terbaru dari seluruh mesin. |
| `GET` | `/api/v1/devices` | Melihat daftar mesin yang terdaftar, status Master/Slave, dan waktu terakhir online. |

---

## 🗄️ Skema Database Lokal (`presensi_local`)

Database diinisialisasi otomatis dari file [init_mysql.sql](file:///d:/RSUP%20IT/Finger/init_mysql.sql):

1. **`devices`**:
   * Menyimpan serial number mesin (`device_sn`), lokasi penempatan, status `is_master`, dan `last_seen_at`.
2. **`pin_employee_map`**:
   * Memetakan nomor PIN pada mesin Fingerspot ke ID Pegawai di sistem presensi.
3. **`raw_attendance`**:
   * Log audit mentah untuk setiap tap (`device_sn`, `pin`, `timestamp`, `status`, `verify_mode`). Dilengkapi `UNIQUE KEY (device_sn, pin, timestamp)` sehingga aman dari duplikasi saat upload ulang (*bulk upload*).
4. **`attendance_errors`**:
   * Menampung baris payload korup/gagal parsing untuk keperluan audit IT.
5. **`biometric_templates`**:
   * Menyimpan template sidik jari terenkripsi (AES/Fernet) sesuai ketentuan UU Perlindungan Data Pribadi (PDP).
6. **`adms_commands`**:
   * Antrean tugas sinkronisasi dari server ke mesin (misal menyalin sidik jari baru dari mesin Master HRD ke mesin Slave IGD/Poli).
7. **`users`** & **`attendances`**:
   * Tabel simulasi presensi (meniru struktur `presensi_staging` RSUP). Tap pertama pada tanggal berjalan otomatis dicatat sebagai `checkin_time`, dan tap berikutnya dicatat sebagai `checkout_time`.

---

## 🧪 Pengujian & Simulasi (Simulator)

Anda dapat menguji seluruh alur kerja server tanpa harus menunggu mesin fisik terhubung:

```bash
python test_adms.py
```

Skrip ini akan otomatis melakukan:
1. Pengecekan `/health`.
2. Simulasi Handshake mesin (`GET /iclock/cdata?SN=DEMO_DEVICE_01`).
3. Simulasi Tap Check-in pegawai PIN 1 (`POST /iclock/cdata?table=ATTLOG`).
4. Simulasi Tap Check-out pegawai PIN 1.
5. Query data masuk melalui `/api/v1/raw-attendance`.

---

## 🔄 Langkah Integrasi ke Database Staging RSUP

Saat Anda siap mengalihkan koneksi dari MySQL lokal ke database staging RSUP (`presensi_staging` dari file `stagging-database.sql`):

1. Buka file [.env](file:///d:/RSUP%20IT/Finger/.env).
2. Ubah parameter database ke server staging RSUP:
   ```env
   DB_HOST=10.10.165.xxx       # IP Server Database RSUP
   DB_PORT=3306
   DB_USER=username_db_rsup
   DB_PASSWORD=password_db_rsup
   DB_NAME=presensi_staging
   ```
3. Eksekusi file DDL tabel pendukung ADMS di database staging RSUP:
   * Tabel `devices`, `pin_employee_map`, `raw_attendance`, `attendance_errors`, `biometric_templates`, dan `adms_commands`.
4. Restart container:
   ```bash
   docker compose restart app
   ```

---

## 🔧 Troubleshooting & FAQ

### 1. Mesin tidak mau terhubung ke server
* Pastikan port `5005` tidak diblokir oleh Windows Firewall:
  ```powershell
  # Izinkan port 5005 di Windows Firewall (jalankan via PowerShell Admin jika perlu)
  New-NetFirewallRule -DisplayName "Fingerspot ADMS Port 5005" -Direction Inbound -LocalPort 5005 -Protocol TCP -Action Allow
  ```
* Pastikan komputer server dan mesin berada dalam satu subnet jaringan atau routing antar VLAN terbuka.
* Coba ping IP mesin dari komputer server: `ping 10.10.165.xxx`.

### 2. Log tap masuk ke `raw_attendance` tapi tidak masuk ke `attendances`
* Periksa apakah nomor PIN di mesin sudah terdaftar di tabel `pin_employee_map` atau cocok dengan `employee_id_number` / `user_id` di tabel `users`.
* Data tap mentah tetap aman 100% tersimpan di `raw_attendance` dan bisa dipetakan sewaktu-waktu.

### 3. Cara melihat isi database MySQL di Docker secara langsung
```bash
docker exec -it finger_mysql mysql -u adms_user -padms_password presensi_local -e "SELECT * FROM raw_attendance ORDER BY id DESC LIMIT 10;"
```

---

## 📄 Lisensi
Internal RSUP — Bebas digunakan dan disesuaikan untuk keperluan sistem presensi rumah sakit.