from pathlib import Path
from functools import lru_cache
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XR_",
        case_sensitive=False,
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
    )

    project_name: str = "XR Invest Backend"
    api_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/xrhodl"
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 1800
    process_worker_count: int = 1
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_required_for_runtime: bool = False
    redis_socket_connect_timeout_seconds: float = 0.35
    redis_socket_timeout_seconds: float = 0.35
    redis_retry_after_seconds: float = 15.0
    redis_retry_after_max_seconds: float = 300.0
    redis_pubsub_reconnect_delay_seconds: float = 1.0
    redis_pubsub_reconnect_max_delay_seconds: float = 30.0
    request_log_level: str = "INFO"
    gzip_minimum_size_bytes: int = 1024
    metrics_enabled: bool = True
    security_headers_enabled: bool = True
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests_per_ip: int = 120
    rate_limit_max_requests_per_user: int = 240
    rate_limit_read_max_requests_per_ip: int = 600
    rate_limit_read_max_requests_per_user: int = 1200
    websocket_rate_limit_enabled: bool = True
    websocket_rate_limit_max_connects_per_ip: int = 20
    websocket_rate_limit_max_messages_per_ip: int = 180
    websocket_rate_limit_max_messages_per_user: int = 240
    websocket_send_timeout_seconds: float = 5.0
    websocket_max_pending_messages_per_connection: int = 32
    websocket_max_rooms_per_connection: int = 64
    websocket_max_topics_per_connection: int = 256
    websocket_presence_ttl_seconds: int = 90
    websocket_presence_refresh_interval_seconds: int = 20
    cors_allow_origins: str = (
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )
    cors_allow_origin_regex: str = r"^https://[a-z0-9-]+\.trycloudflare\.com$"
    trusted_hosts: str = "localhost,127.0.0.1,::1,[::1],*.trycloudflare.com"
    allow_insecure_demo_auth: bool = False
    allow_insecure_demo_ws_user_id_auth: bool = False
    feed_default_limit: int = 20
    feed_max_limit: int = 50
    news_cache_ttl_seconds: int = 120
    news_related_cache_ttl_seconds: int = 300
    news_revision_cache_ttl_seconds: int = 30
    cryptopanic_api_token: str = ""
    gemini_api_key: str = ""
    gemini_api_key_portfolio: str = ""
    gemini_model: str = ""
    gemini_model_portfolio: str = ""
    ws_heartbeat_seconds: int = 25
    auto_create_schema: bool = False
    public_base_url: str = ""
    admin_panel_enabled: bool = False
    admin_panel_username: str = "admin"
    admin_panel_password: str = "change-me-admin"
    admin_panel_secret_key: str = "change-me-admin-secret"
    firebase_service_account_path: str = "credentials/firebase-admin.json"
    firebase_service_account_json: str = ""
    firebase_push_max_workers: int = 4
    firebase_push_max_concurrent_batches: int = 2
    google_oauth_allowed_client_ids: str = (
        "654847065414-99ghvqeg1hn6jbr5oo8vus4pabdjthsg.apps.googleusercontent.com,"
        "654847065414-k58go2de7e1t3u4ales99gnhr2vtpchg.apps.googleusercontent.com"
    )
    google_oauth_require_verified_email: bool = True
    market_poll_interval_seconds: int = 45
    market_cache_ttl_seconds: int = 75
    market_tracked_limit: int = 20
    community_public_cache_ttl_seconds: int = 45
    r2_endpoint_url: str = ""
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_public_base_url: str = ""
    r2_region: str = "auto"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        origins = [
            value.strip()
            for value in str(self.cors_allow_origins or "").split(",")
            if value.strip()
        ]
        public_origin = self.public_origin
        if public_origin:
            origins.append(public_origin)
        return list(dict.fromkeys(origins))

    @property
    def trusted_hosts_list(self) -> list[str]:
        hosts = [
            value.strip()
            for value in str(self.trusted_hosts or "").split(",")
            if value.strip()
        ]
        public_host = urlparse(self.public_base_url).hostname or ""
        if public_host:
            hosts.append(public_host)
        return list(dict.fromkeys(hosts))

    @property
    def public_origin(self) -> str:
        parsed = urlparse(self.public_base_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def admin_panel_has_secure_credentials(self) -> bool:
        password = self.admin_panel_password.strip()
        secret_key = self.admin_panel_secret_key.strip()
        weak_passwords = {
            "",
            "admin",
            "password",
            "change-me-admin",
        }
        weak_secret_keys = {
            "",
            "dev-admin-secret",
            "change-me-admin-secret",
            "secret",
        }
        return (
            len(password) >= 12
            and password.lower() not in weak_passwords
            and len(secret_key) >= 24
            and secret_key.lower() not in weak_secret_keys
        )

    @property
    def admin_features_enabled(self) -> bool:
        return self.admin_panel_enabled and self.admin_panel_has_secure_credentials

    @property
    def coordinated_runtime_services_enabled(self) -> bool:
        return self.redis_required_for_runtime or self.process_worker_count <= 1

    @property
    def google_oauth_allowed_client_ids_list(self) -> list[str]:
        return list(
            dict.fromkeys(
                value.strip()
                for value in str(self.google_oauth_allowed_client_ids or "").split(",")
                if value.strip()
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
