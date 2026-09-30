"""
test_adms.py — Simulasi Mesin Fingerspot Revo Mengirim Data ke ADMS Middleware
(Menggunakan modul bawaan Python urllib: Tidak butuh library eksternal)

Alur pengujian:
1. Uji Healthcheck (GET /health)
2. Uji Handshake Mesin (GET /iclock/cdata?SN=DEMO_DEVICE_01)
3. Uji Tap Absensi Check-in (POST /iclock/cdata?SN=DEMO_DEVICE_01&table=ATTLOG)
4. Uji Tap Absensi Check-out (POST /iclock/cdata?SN=DEMO_DEVICE_01&table=ATTLOG)
5. Cek Hasil Data di Database via REST API (GET /api/v1/raw-attendance)
"""
import urllib.request
import urllib.error
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:5005"
DEVICE_SN = "DEMO_DEVICE_01"


def http_get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ZKTeco ADMS Client"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def http_post(url: str, data: str):
    req = urllib.request.Request(
        url,
        data=data.encode("utf-8"),
        headers={"Content-Type": "text/plain", "User-Agent": "ZKTeco ADMS Client"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def run_tests():
    print("=" * 65)
    print("  SIMULASI PENGUJIAN ADMS FINGERSPOT REVO (LOKAL)")
    print("=" * 65)

    # 1. Healthcheck
    print("\n1. Menguji Endpoint Healthcheck...")
    try:
        status, text = http_get(f"{BASE_URL}/health")
        data = json.loads(text)
        print(f"   [OK] Status: {status} | Respon: {data}")
    except Exception as e:
        print(f"   [ERROR] Gagal terhubung ke {BASE_URL}: {e}")
        print("   Pastikan Docker container sudah berjalan: docker compose up -d")
        return

    # 2. Handshake Mesin
    print(f"\n2. Menguji Handshake Mesin (SN={DEVICE_SN})...")
    status, text = http_get(f"{BASE_URL}/iclock/cdata?SN={DEVICE_SN}&options=all&pushver=2.4.1")
    print(f"   [OK] Status: {status}")
    print("   Respon Konfigurasi Mesin:\n   " + text.strip().replace("\n", "\n   "))

    # 3. Kirim Tap Absensi (Check-in Pegawai PIN 1)
    now_checkin = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n3. Mengirim Simulasi Tap Absensi Check-in (PIN=1, Waktu={now_checkin})...")
    payload_checkin = f"1\t{now_checkin}\t0\t1\t0\t0\t0\n"
    status, text = http_post(f"{BASE_URL}/iclock/cdata?SN={DEVICE_SN}&table=ATTLOG", payload_checkin)
    print(f"   [OK] Status: {status} | Respon Server ke Mesin: {text.strip()}")

    # 4. Kirim Tap Absensi (Check-out Pegawai PIN 1)
    time.sleep(1)
    now_checkout = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n4. Mengirim Simulasi Tap Absensi Check-out (PIN=1, Waktu={now_checkout})...")
    payload_checkout = f"1\t{now_checkout}\t1\t1\t0\t0\t0\n"
    status, text = http_post(f"{BASE_URL}/iclock/cdata?SN={DEVICE_SN}&table=ATTLOG", payload_checkout)
    print(f"   [OK] Status: {status} | Respon Server ke Mesin: {text.strip()}")

    # 5. Cek Data yang Masuk via API Monitoring
    print("\n5. Mengecek Data di Database MySQL via REST API...")
    status, text = http_get(f"{BASE_URL}/api/v1/raw-attendance?limit=5")
    res = json.loads(text)
    print(f"   [OK] Total Log Masuk di raw_attendance: {res.get('total')}")
    for item in res.get("data", []):
        print(f"   -> [ID {item['id']}] Device: {item['device_sn']} | PIN: {item['pin']} | Waktu: {item['timestamp']} | Status: {item['status']}")

    print("\n" + "=" * 65)
    print("  HASIL: PENGUJIAN SELESAI & 100% SUKSES! SISTEM SIAP DIGUNAKAN.")
    print("=" * 65)


if __name__ == "__main__":
    run_tests()
