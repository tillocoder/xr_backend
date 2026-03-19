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
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 1800
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_socket_connect_timeout_seconds: float = 0.35
    redis_socket_timeout_seconds: float = 0.35
    request_log_level: str = "INFO"
    gzip_minimum_size_bytes: int = 1024
    metrics_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests_per_ip: int = 240
    rate_limit_max_requests_per_user: int = 480
    feed_default_limit: int = 20
    feed_max_limit: int = 50
    news_cache_ttl_seconds: int = 120
    cryptopanic_api_token: str = ""
    ws_heartbeat_seconds: int = 25
    auto_create_schema: bool = False
    public_base_url: str = ""
    admin_panel_username: str = "admin"
    admin_panel_password: str = "change-me-admin"
    admin_panel_secret_key: str = "change-me-admin-secret"
    firebase_service_account_path: str = "credentials/firebase-admin.json"
    firebase_service_account_json: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
