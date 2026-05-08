from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    dvla_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/vehiclemarket.db"
    scrape_interval_minutes: int = 60
    max_pages_per_run: int = 10
    request_delay_seconds: float = 3.0
    headless: bool = True
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    secret_key: str = "dev-secret-change-in-production"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    alert_email: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
