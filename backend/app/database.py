"""
app/database.py — Koneksi MySQL Asynchronous menggunakan aiomysql Connection Pool
"""
import logging
import asyncio
from typing import AsyncGenerator
import aiomysql
from app.config import settings

logger = logging.getLogger(__name__)

# Global connection pool
pool: aiomysql.Pool = None


async def init_db_pool(max_retries: int = 5, retry_delay: int = 3):
    """
    Inisialisasi aiomysql connection pool dengan retry logic saat startup.
    """
    global pool
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[DB] Menghubungkan ke MySQL di {settings.db_host}:{settings.db_port}/{settings.db_name} "
                f"(Percobaan {attempt}/{max_retries})..."
            )
            pool = await aiomysql.create_pool(
                host=settings.db_host,
                port=settings.db_port,
                user=settings.db_user,
                password=settings.db_password,
                db=settings.db_name,
                charset="utf8mb4",
                init_command="SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci",
                autocommit=True,
                minsize=2,
                maxsize=20,
                connect_timeout=10,
            )
            logger.info("[DB] Koneksi ke MySQL berhasil dibuat.")
            await ensure_adms_tables(pool)
            return pool
        except Exception as e:
            logger.warning(f"[DB] Gagal terhubung ke MySQL: {e}")
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
            else:
                logger.error("[DB] Mencapai batas percobaan koneksi MySQL.")
                raise e


async def ensure_adms_tables(pool_instance):
    """
    Memastikan 6 tabel ADMS & Sinkronisasi tersedia di database (aman untuk staging).
    Tidak menghapus atau mengubah tabel pegawai/kehadiran yang sudah ada.
    """
    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS `devices` (
            `device_sn` VARCHAR(50) PRIMARY KEY,
            `location` VARCHAR(100),
            `comm_key` VARCHAR(50),
            `is_master` BOOLEAN DEFAULT FALSE,
            `last_seen_at` DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        """
        CREATE TABLE IF NOT EXISTS `pin_employee_map` (
            `pin` VARCHAR(20) PRIMARY KEY,
            `employee_id` BIGINT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        """
        CREATE TABLE IF NOT EXISTS `raw_attendance` (
            `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
            `device_sn` VARCHAR(50),
            `pin` VARCHAR(20),
            `timestamp` DATETIME,
            `status` INT DEFAULT 0,
            `verify_mode` INT DEFAULT 1,
            `received_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY `unique_scan` (`device_sn`, `pin`, `timestamp`),
            KEY `idx_pin` (`pin`),
            KEY `idx_timestamp` (`timestamp`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        """
        CREATE TABLE IF NOT EXISTS `attendance_errors` (
            `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
            `device_sn` VARCHAR(50),
            `raw_payload` TEXT,
            `reason` VARCHAR(255),
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        """
        CREATE TABLE IF NOT EXISTS `biometric_templates` (
            `pin` VARCHAR(20),
            `finger_id` INT,
            `template_type` VARCHAR(20) DEFAULT 'FP',
            `valid` INT DEFAULT 1,
            `template_data` LONGTEXT,
            `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`pin`, `finger_id`, `template_type`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """,
        """
        CREATE TABLE IF NOT EXISTS `adms_commands` (
            `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
            `device_sn` VARCHAR(50),
            `cmd_code` VARCHAR(50) DEFAULT NULL,
            `payload` LONGTEXT,
            `payload_type` VARCHAR(20) DEFAULT 'JSON',
            `trans_id` VARCHAR(50),
            `command_text` TEXT,
            `status` VARCHAR(20) DEFAULT 'PENDING',
            `return_code` VARCHAR(50) DEFAULT NULL,
            `result_data` TEXT,
            `retry_count` INT DEFAULT 0,
            `expires_at` DATETIME,
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
            `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            KEY `idx_device_status` (`device_sn`, `status`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
    ]
    try:
        async with pool_instance.acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in ddl_statements:
                    await cur.execute(ddl)
        logger.info("[DB] Verifikasi tabel pendukung ADMS selesai (semua tabel siap).")
    except Exception as e:
        logger.warning(f"[DB] Catatan saat verifikasi tabel ADMS: {e}")


async def close_db_pool():
    """Tutup pool koneksi MySQL saat shutdown."""
    global pool
    if pool:
        pool.close()
        await pool.wait_closed()
        logger.info("[DB] MySQL connection pool telah ditutup.")


def get_db_pool() -> aiomysql.Pool:
    """Ambil global pool instance."""
    return pool


async def get_db_conn() -> AsyncGenerator[aiomysql.Connection, None]:
    """
    FastAPI dependency untuk mendapatkan koneksi dari pool.
    """
    global pool
    if not pool:
        raise RuntimeError("Database pool belum diinisialisasi")

    async with pool.acquire() as conn:
        yield conn
