# Product Requirements Document (PRD)

## 1. Document Control
| Version | Date | Author | Description of Change |
|---------|------|--------|-----------------------|
| 1.0     | 25-09-2026 | Antigravity AI | Inisialisasi PRD Integrasi ADMS Fingerspot |
| 1.1     | 25-09-2026 | IT Team & AI | Penambahan skema database, Idempotency, Docker Deployment, E2E Testing, dan Device Setting |
| 1.2     | 27-09-2026 | IT Team & AI | Penambahan Arsitektur Epic 4 (Master-Slave Biometric Sync & Command Queueing) |
| 1.3     | 27-09-2026 | IT Team & AI | Proteksi data biometrik (enkripsi & flag `is_master`), alur update/hapus sidik jari, siklus hidup command queue, visibilitas kegagalan parsial, verifikasi endpoint FINGERTMP vs fdata, kejelasan strategi dedup, tambahan skenario testing |

## 2. Product Overview
### 2.1. Product Name
**Sistem Middleware ADMS Fingerspot (SatuTalenta HRIS)**

### 2.2. Product Vision & Objective
Sistem ini berfungsi sebagai jembatan (*middleware*) *real-time* yang menghubungkan perangkat keras absensi (Fingerspot Revo W Series) di Rumah Sakit secara langsung ke Database Staging utama. Tujuannya adalah memastikan data absensi dari mesin masuk ke aplikasi karyawan (SatuTalentaApp) **tanpa delay (zero-delay)** dan tahan terhadap gangguan koneksi jaringan (Auto-Sync saat server mati).
Dengan rilis Epic 4, sistem juga berfungsi sebagai **Command Center** untuk mendistribusikan, memperbarui, dan mencabut sidik jari di seluruh mesin rumah sakit secara otomatis dan aman.

### 2.3. Scope
PRD ini mendefinisikan *requirement* untuk:
1. Layanan backend ingestion (FastAPI) yang meniru peran server ADMS menggunakan protokol iclock.
2. Command Queueing System untuk penyebaran, pembaruan, dan pencabutan biometrik dari mesin Master ke mesin Slave (Epic 4), dengan perlindungan data biometrik yang memadai.
3. Lapisan API terpisah yang mengekspos data absensi ke SatuTalentaApp.

## 3. User Personas
| Persona | Deskripsi & Goals |
|---------|-------------------|
| **Pegawai RS (Dokter/Perawat)** | Goal: Melakukan absensi di mesin fisik dan melihat riwayatnya muncul seketika di *SatuTalentaApp*, serta cukup mendaftarkan jari 1 kali untuk semua mesin. |
| **HR / IT Rumah Sakit** | Goal: Memastikan tidak ada data absen yang hilang, tidak perlu berkeliling mendaftarkan jari karyawan ke tiap mesin secara manual, **dan yakin data sidik jari eks-karyawan benar-benar tercabut dari seluruh mesin**. |

## 4. Functional Requirements

### Epic 1: Konektivitas & Handshake
| ID | Feature | Description | Priority |
|---|---|---|---|
| CON-01 | Machine Handshake | `GET /iclock/cdata?SN=<serial>&options=all` — merespon inisialisasi awal. | Must-have |
| CON-02 | Polling Bebas Waktu | Mengirim konfig `TransTimes=00:00;23:59` ke mesin. | Must-have |
| CON-03 | Device Authentication | Verifikasi `SN` dan `comm_key` mesin terhadap database. Menolak mesin asing (HTTP 403). Berlaku untuk **semua** endpoint device-facing (`cdata`, `getrequest`, `devicecmd`, dan `fdata` jika dipakai — lihat SYNC-01), bukan cuma `cdata`. | Must-have |
| CON-04 | Device Role Flag | Kolom `devices.is_master` (boolean) menandai mesin mana yang berhak menjadi sumber pendaftaran/perubahan sidik jari baru. Default `false` untuk semua mesin baru; diaktifkan manual oleh IT untuk mesin di Ruang HRD. | Must-have |

### Epic 2: Tarik Data Absensi (Zero-Delay Push)
| ID | Feature | Description | Priority |
|---|---|---|---|
| ATT-01 | Real-time Catch | `POST /iclock/cdata?SN=<serial>&table=ATTLOG` menangkap data log absensi. | Must-have |
| ATT-02 | Format Parsing | Memecah payload TSV. | Must-have |
| ATT-03 | Database Ingestion | Menulis data ke `raw_attendance` dengan menyertakan `device_sn`. | Must-have |
| ATT-04 | Deduplication | Constraint unik `(device_sn, pin, timestamp)` (InnoDB). Implementasi menggunakan **upsert** (`INSERT ... ON DUPLICATE KEY UPDATE received_at = received_at`) — bukan `INSERT IGNORE`, supaya error selain duplikat (mis. tipe data salah, kolom wajib kosong) tetap terlihat dan tidak ikut ter-silence. | Must-have |
| ATT-05 | Success ACK | Membalas `OK: <jumlah_baris_diterima>` ke mesin. | Must-have |
| ATT-06 | Error Handling | Baris TSV korup dicatat ke tabel `attendance_errors`. | Should-have |

### Epic 3: Ekspos Data ke SatuTalentaApp
| ID | Feature | Description | Priority |
|---|---|---|---|
| API-01 | Employee Endpoint | REST endpoint yang dikonsumsi aplikasi mobile. | Must-have |
| API-02 | PIN Mapping | Tabel `pin_employee_map` untuk menerjemahkan PIN mesin ke User ID database utama. | Must-have |
| API-03 | Sync Status Dashboard | Endpoint/tampilan bagi HR/IT yang menunjukkan status broadcast biometrik per pegawai per mesin (`SUCCESS`/`FAILED`/`PENDING`), supaya kegagalan parsial (mis. berhasil di 8 dari 10 mesin) langsung terlihat, bukan diam-diam terlewat. | Must-have |

### Epic 4: Sinkronisasi Sidik Jari Terpusat (Master-Slave) 🚀
> Catatan implementasi: verifikasi terlebih dahulu ke device fisik apakah template sidik jari benar-benar dikirim lewat `POST /iclock/cdata?table=FINGERTMP`, atau lewat endpoint terpisah `POST /iclock/fdata` (beberapa firmware ADMS memisahkan upload template/foto dari `cdata`). Sesuaikan SYNC-01 begitu terverifikasi — jangan diasumsikan sebelum diuji ke mesin Fingerspot Revo W yang sebenarnya.

| ID | Feature | Description | Priority |
|---|---|---|---|
| SYNC-01 | Catch Fingerprint | Menangkap template jari dari mesin saat HRD mendaftarkan/memperbarui karyawan. Payload **hanya diterima** jika `device_sn` pengirim memiliki `is_master = true` (CON-04) — request dari device lain ditolak (HTTP 403) meski `comm_key`-nya valid, untuk mencegah pendaftaran biometrik dari mesin yang salah/tidak berwenang. | Must-have |
| SYNC-02 | Broadcast Logic | Membuat perintah distribusi (broadcast) sidik jari baru/update ke *seluruh* mesin Slave yang terdaftar. | Must-have |
| SYNC-03 | Command Queueing | `GET /iclock/getrequest` untuk menyuapkan perintah ke mesin Slave yang bertanya. | Must-have |
| SYNC-04 | Command ACK | `POST /iclock/devicecmd` untuk menandai antrean tugas sebagai `SUCCESS` atau `FAILED`. | Must-have |
| SYNC-05 | Update & Revoke Fingerprint | Saat template pegawai diperbarui (registrasi ulang) atau pegawai dinonaktifkan/resign di sistem HR, sistem membuat perintah `UPDATE`/`DELETE FINGERTMP` dan broadcast ke seluruh mesin Slave — bukan cuma menghapus dari `biometric_templates` di database. Tanpa ini, eks-karyawan tetap bisa absen secara fisik di mesin yang belum di-update. | Must-have |
| SYNC-06 | Command Lifecycle | Setiap baris `adms_commands` punya `expires_at` dan `retry_count`. Command yang tidak ter-ACK dalam waktu tertentu (mis. 24 jam) ditandai `FAILED` (bukan menumpuk `PENDING` selamanya), dan mesin yang tidak pernah online lagi untuk mengambil command-nya bisa ditandai HR/IT sebagai "device bermasalah". Baris `SUCCESS`/`FAILED` lama diarsipkan/dibersihkan secara berkala agar tabel tidak tumbuh tanpa batas. | Should-have |

## 5. Non-Functional Requirements (NFR)
| Kategori | Requirement |
|----------|-------------|
| **Kinerja** | API harus stateless (async, driver DB non-blocking), merespons POST < 1 detik. |
| **Resiliensi** | API sanggup menerima *bulk upload* saat mesin online kembali tanpa duplikasi. |
| **Keamanan** | Database middleware dibatasi (least privilege: `SELECT, INSERT, UPDATE`). Kredensial di `.env`. Kolom `biometric_templates.template_data` **wajib dienkripsi at-rest** (mis. `AES_ENCRYPT`/aplikasi-level encryption), karena termasuk data pribadi spesifik (biometrik) di bawah UU PDP — bukan cukup diandalkan ke keamanan level server MySQL saja. Akses ke tabel `biometric_templates` dan `adms_commands` dicatat di audit log terpisah (siapa/apa yang membaca, kapan). |
| **Observability** | Log dicatat ke Docker logs dengan rotasi (max-size). |

## 6. Technical Specifications
- **Framework Backend:** FastAPI & Uvicorn (Asynchronous)
- **Database:** MySQL (Instance Existing).
  - **Driver:** `aiomysql` (Async driver).
  - **Storage Engine:** `ENGINE=InnoDB` untuk *row-level locking*.
  - **Tipe Timestamp:** `DATETIME` (Mencegah konversi timezone dan Y2K38 bug).
- **Skema Data Minimal (Ditambah Epic 4):**
  1. `devices (device_sn PK, location, comm_key, is_master, last_seen_at)`
  2. `raw_attendance (id PK, device_sn FK, pin, timestamp DATETIME, status, ...)`
  3. `pin_employee_map (pin, employee_id FK)`
  4. `attendance_errors (id, device_sn, raw_payload, reason, created_at)`
  5. **`biometric_templates (pin, finger_id, valid, template_data ENCRYPTED, updated_at)`**
  6. **`adms_commands (id PK, device_sn FK, command_text, status, retry_count, expires_at, created_at)`**

## 7. Arsitektur Alur Sistem Absensi (Epic 2)
1. Dr. Budi absen di **Mesin Lobi**.
2. Mesin menembakkan HTTP POST ke Port 8000.
3. FastAPI memverifikasi `SN` + `comm_key`, lalu melakukan *upsert* (ATT-04) ke database Staging.
4. FastAPI membalas `OK: 1` agar log dihapus dari memori antrean mesin.

## 8. Arsitektur Sinkronisasi Biometrik (Epic 4)
1. Karyawan baru mendaftarkan jari di **Mesin Master (Ruang HRD)**.
2. Mesin Master mem-push data template ke Server FastAPI; server memverifikasi `SN`+`comm_key` **dan** `is_master = true` (CON-04) sebelum memproses.
3. Server menyimpan template terenkripsi ke `biometric_templates`.
4. Server mengecek tabel `devices` dan membuat baris perintah di `adms_commands` untuk setiap mesin Slave (IGD, Poli, Farmasi, dll), masing-masing dengan `expires_at`.
5. Semenit kemudian, **Mesin IGD** (Slave) melakukan polling `GET /iclock/getrequest`.
6. Server memberikan perintah: `C:101:DATA UPDATE FINGERTMP...`
7. Mesin IGD menelan data jari tersebut dan melapor sukses ke `POST /iclock/devicecmd`. Status di database berubah jadi `SUCCESS`, terlihat di Sync Status Dashboard (API-03).
8. Saat karyawan resign atau perlu registrasi ulang, HR memicu SYNC-05: server membuat perintah `DELETE`/`UPDATE FINGERTMP` baru dan broadcast ulang ke semua mesin lewat alur yang sama.

## 9. Deployment & Containerization
1. **Isolasi Proses:** Berjalan di dalam container `python:3.11-slim`.
2. **Auto-Restart Container:** `restart: always`. OS Host juga harus diset `systemctl enable docker`.
3. **Healthcheck:** Dilengkapi dengan `HEALTHCHECK` via `curl` di Dockerfile.
4. **Log Rotation:** Di-set `max-size: 10m` agar SSD tidak penuh.

## 10. Panduan Konfigurasi Perangkat Keras
1. Atur IP Statis di mesin (atau DHCP Reservation).
2. **Sinkronkan RTC (Waktu) mesin** agar `timestamp` akurat.
3. Set menu **Cloud Server / ADMS**:
   - Server Address: IP Host Docker.
   - Port: 8000.
   - Domain Name: OFF.
4. Daftarkan SN mesin ke tabel `devices`. Untuk mesin di Ruang HRD, set `is_master = true` (CON-04) — hanya dilakukan untuk mesin yang benar-benar berwenang mendaftarkan biometrik baru.

## 11. Strategi Pengujian (UAT & Testing Phases)
- **Fase 1: Unit Testing API (Postman)** — Tes POST ke `/iclock/cdata` dengan baris identik, payload cacat, atau salah `comm_key`. Cek respon dan database.
- **Fase 2: UAT Mesin Fisik Lokal** — Hubungkan 1 mesin fisik, pantau log Docker secara *real-time*. Verifikasi ke device fisik apakah FINGERTMP lewat `cdata` atau `fdata` (lihat catatan Epic 4).
- **Fase 3: Uji Bulk Upload** — Simulasikan mesin offline (cabut LAN), absen berkali-kali, lalu online-kan kembali. Pastikan log masuk serentak tanpa duplikasi.
- **Fase 4: Uji Sinkronisasi Master-Slave** — Daftarkan jari di Mesin 1 (Master). Pastikan muncul antrean perintah di database. Nyalakan Mesin 2 (Slave) dan pastikan jari tersebut berhasil disalin otomatis ke Mesin 2. Tambahan skenario negatif:
  - Kirim payload `FINGERTMP` dari mesin dengan `is_master = false` → harus ditolak (HTTP 403).
  - Hapus/nonaktifkan pegawai di sistem HR → pastikan perintah `DELETE FINGERTMP` terbentuk dan berhasil di-broadcast (SYNC-05).
  - Biarkan satu mesin Slave tetap offline melewati `expires_at` → pastikan command-nya ditandai `FAILED`, bukan menumpuk `PENDING` selamanya (SYNC-06).
