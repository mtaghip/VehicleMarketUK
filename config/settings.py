from pydantic_settings import BaseSettings
from pathlib import Path
import os


# Railway mounts persistent volumes at /data by default.
# Locally the data folder sits next to the project root.
_default_data_dir = "/data" if Path("/data").exists() else str(Path(__file__).parent.parent / "data")
_default_db_url = f"sqlite+aiosqlite:///{_default_data_dir}/vehiclemarket.db"


class Settings(BaseSettings):
    dvla_api_key: str = ""
    database_url: str = _default_db_url
    scrape_interval_minutes: int = 60
    max_pages_per_run: int = 10
    request_delay_seconds: float = 3.0
    headless: bool = True
    api_host: str = "0.0.0.0"
    # Railway injects PORT; fall back to 8000 locally
    api_port: int = int(os.environ.get("PORT", 8000))
    secret_key: str = "dev-secret-change-in-production"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    alert_email: str = ""
    # Dashboard basic auth — set both to enable password protection
    dashboard_user: str = ""
    dashboard_password: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = Path(_default_data_dir)
DATA_DIR.mkdir(parents=True, exist_ok=True)
