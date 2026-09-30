"""
app/config.py — Konfigurasi terpusat via environment variables
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database MySQL
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "adms_user"
    db_password: str = "adms_password"
    db_name: str = "presensi_local"

    # Enkripsi Biometrik (Fernet 32-byte base64)
    encryption_key: str = "g-zVvD9_d2z-2Bv9GkY2tZ8Sj7XoBwO_c6L_x5Jm3rE="

    # API & ADMS Server
    api_host: str = "0.0.0.0"
    api_port: int = 5005
    debug: bool = False

    # Auto-register mesin baru saat pertama kali kirim handshake (sangat mempermudah setup)
    auto_register_device: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
