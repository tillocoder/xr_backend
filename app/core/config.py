from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XR_",
        case_sensitive=False,
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
    )

    project_name: str = "XR Hodl Backend"
    api_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/xrhodl"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_socket_connect_timeout_seconds: float = 0.35
    redis_socket_timeout_seconds: float = 0.35
    feed_default_limit: int = 20
    feed_max_limit: int = 50
    ws_heartbeat_seconds: int = 25
    auto_create_schema: bool = False
    public_base_url: str = ""
    admin_panel_username: str = "admin"
    admin_panel_password: str = "change-me-admin"
    firebase_service_account_path: str = "credentials/firebase-admin.json"
    firebase_service_account_json: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
