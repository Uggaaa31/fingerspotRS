# 📘 Panduan Penggunaan Sistem Absensi Fingerspot & SatuTalenta (Untuk Tim SDM/HRD)

Selamat datang di sistem absensi terintegrasi baru! 

Sistem ini dirancang untuk menyatukan mesin fisik absensi (Fingerspot) dengan sistem utama kepegawaian Anda (SatuTalenta). Dengan sistem ini, Anda **tidak perlu lagi repot mengetik nama pegawai satu per satu di mesin**.

Berikut adalah panduan langkah demi langkah cara kerja sistem ini menggunakan bahasa yang sederhana.

---

## 🎯 Konsep Utama yang Perlu Diketahui
1. **SatuTalenta adalah "Bos" (Pusat Data):** Semua nama dan NIP pegawai harus diurus di aplikasi SatuTalenta. Mesin Fingerspot hanya bertugas sebagai alat *perekam sidik jari* dan *alat absen (tap)*.
2. **Nomor PIN Mesin:** Saat Anda mendaftarkan jari pegawai di mesin, mesin akan memberikan nomor urut (misal: PIN 1, PIN 2). PIN ini yang nantinya kita jodohkan (mapping) dengan NIP pegawai di website.
3. **Pendaftaran Cukup 1 Kali:** Data sidik jari pegawai hanya perlu didaftarkan di **salah satu mesin saja**. Sistem akan otomatis menyalin (mengirim) sidik jari tersebut ke semua mesin absensi lain yang ada di gedung Anda!

---

## 📝 SOP: Cara Mendaftarkan Pegawai Baru

Jika ada pegawai baru yang masuk, mohon ikuti 4 langkah berurutan di bawah ini:

### Langkah 1: Daftarkan Pegawai di Aplikasi SatuTalenta
Sebelum menyentuh mesin absensi, pastikan pegawai tersebut **sudah Anda daftarkan di aplikasi SatuTalenta**.
* Pastikan NIP/NIK dan Namanya sudah benar.
* Langkah ini sangat penting agar sistem absensi kita bisa mengenali NIP tersebut nantinya.

### Langkah 2: Rekam Sidik Jari di Mesin Fisik
Bawa pegawai baru ke **salah satu** mesin absensi (mesin apa saja boleh).
1. Masuk ke **Menu Utama** di mesin.
2. Pilih **User / Pengguna Baru**.
3. Di bagian **User ID / PIN**, biarkan angka yang muncul (misal: `55`), atau masukkan angka bebas yang belum dipakai. **Catat angka PIN ini di kertas!**
4. Bagian **Nama** *kosongkan saja* (tidak usah repot mengetik nama di mesin).
5. Pilih **Daftar Jari** dan minta pegawai menempelkan jarinya 3 kali sampai berhasil.
6. Tekan **OK / Simpan**.

### Langkah 3: Minta Pegawai Melakukan "Test Absen" (Tap 1 Kali)
Agar server mendeteksi ada PIN baru yang aktif, minta pegawai tersebut untuk langsung melakukan absen (menempelkan sidik jarinya ke mesin) **satu kali saja**.
* Mesin akan berbunyi *"Terima Kasih"*, dan diam-diam mesin telah mengirimkan angka PIN tadi ke server kita.

### Langkah 4: "Jodohkan" PIN dengan Nama di Website (Pin Mapping)
Sekarang kembali ke komputer Anda, dan jodohkan PIN tadi dengan Nama Asli pegawai:
1. Buka browser dan buka **Dashboard Absensi** (Website).
2. Klik menu **Pemetaan PIN** (atau *Pin Mapping*).
3. Cari angka PIN yang tadi Anda catat (misal: `55`). Statusnya pasti tertulis *"Belum Terpetakan"*.
4. Klik/Edit PIN tersebut, lalu **ketik NIP/NIK pegawai tersebut**.
5. Klik **Simpan**.

**🎉 Selesai!** 
Sistem akan otomatis mengecek NIP tersebut ke SatuTalenta, menarik nama aslinya, dan menghubungkannya secara permanen. 
Besok paginya saat pegawai absen di mesin mana pun, laporan kehadirannya sudah otomatis muncul atas nama pegawai yang bersangkutan!

---

## ❓ Pertanyaan yang Sering Muncul (FAQ)

**1. Kenapa namanya tidak saya ketik di mesin saja?**
Karena pengetikan di mesin sering terjadi *typo* (salah ketik) dan tidak tersambung ke aplikasi SatuTalenta. Dengan memetakannya lewat NIP di website, data dijamin 100% akurat sesuai dengan database kepegawaian (SatuTalenta).

**2. Apakah saya harus mendaftarkan sidik jari pegawai di setiap lantai/mesin?**
**TIDAK PERLU.** Daftarkan saja di 1 mesin (misal di mesin lobi). Sistem kami akan menyalin data sidik jari pegawai tersebut ke mesin lantai 2, lantai 3, dst secara diam-diam. Pegawai bisa langsung absen di mesin mana saja.

**3. Kalau muncul *error* "NIP tidak ditemukan di database SatuTalenta" saat mapping, harus bagaimana?**
Ini artinya Anda belum mendaftarkan pegawai tersebut di aplikasi SatuTalenta, atau Anda salah mengetik angka NIP. Silakan periksa kembali aplikasi SatuTalenta Anda.

**4. Jika ada pegawai yang *resign*, bagaimana cara menghapusnya?**
Anda cukup menonaktifkan atau menghapus status pegawai tersebut dari aplikasi SatuTalenta. Namun, jika Anda ingin agar jarinya tidak bisa lagi dipakai untuk absen di mesin, Anda bisa menghapus *User* tersebut dari salah satu mesin fisik (nantinya akan terhapus otomatis di mesin lain).
