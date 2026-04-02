from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEX_",
        case_sensitive=False,
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    project_name: str = "Nex App Backend"
    api_prefix: str = "/api/v1"
    debug: bool | None = None
    api_docs_enabled: bool | None = None
    json_logs: bool | None = None
    error_include_details_in_response: bool | None = None
    database_url: str = "sqlite+aiosqlite:///./nex_app_backend.db"
    database_echo: bool = False
    auto_create_schema: bool = True
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 30
    database_pool_recycle_seconds: int = 1800
    request_log_level: str = "INFO"
    gzip_minimum_size_bytes: int = 1024

    cors_allow_origins: str = (
        "http://localhost:3000,"
        "http://127.0.0.1:3000,"
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )
    cors_allow_origin_regex: str = r"https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    trusted_hosts: str = "localhost,127.0.0.1,::1,[::1],testserver"

    jwt_secret_key: str = "change-me-super-secret-key"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "nex-app-backend"
    jwt_access_audience: str = "nex-app-api"
    jwt_refresh_audience: str = "nex-app-refresh"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    password_min_length: int = 10

    admin_panel_enabled: bool = True
    admin_panel_username: str = "admin"
    admin_panel_password: str = "change-me-admin-password"
    admin_panel_secret_key: str = "change-me-very-long-admin-secret-key"
    admin_email: str = "admin@nex.app"
    admin_password: str = "ChangeMe123!"

    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1,::1"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            value.strip()
            for value in str(self.cors_allow_origins or "").split(",")
            if value.strip()
        ]

    @property
    def trusted_hosts_list(self) -> list[str]:
        return [
            value.strip() for value in str(self.trusted_hosts or "").split(",") if value.strip()
        ]

    @property
    def forwarded_allow_ips_list(self) -> list[str]:
        return [
            value.strip()
            for value in str(self.forwarded_allow_ips or "").split(",")
            if value.strip()
        ]

    @model_validator(mode="after")
    def apply_runtime_defaults(self) -> "Settings":
        self.environment = str(self.environment or "development").strip().lower()
        if self.environment not in {"development", "staging", "production", "test"}:
            raise ValueError("environment must be one of: development, staging, production, test.")

        if self.debug is None:
            self.debug = self.environment in {"development", "test"}
        if self.api_docs_enabled is None:
            self.api_docs_enabled = self.environment != "production"
        if self.json_logs is None:
            self.json_logs = self.environment in {"staging", "production"}
        if self.error_include_details_in_response is None:
            self.error_include_details_in_response = self.environment in {"development", "test"}

        if self.password_min_length < 8:
            raise ValueError("password_min_length must be at least 8.")

        if self.is_production:
            insecure_defaults = {
                "change-me-super-secret-key",
                "change-me-admin-password",
                "change-me-very-long-admin-secret-key",
            }
            if self.auto_create_schema:
                raise ValueError("auto_create_schema must be disabled in production.")
            if self.jwt_secret_key in insecure_defaults or len(self.jwt_secret_key) < 32:
                raise ValueError(
                    "jwt_secret_key must be a non-default secret with at least 32 characters in production."
                )
            if self.admin_panel_enabled:
                if (
                    self.admin_panel_password in insecure_defaults
                    or len(self.admin_panel_password) < 12
                ):
                    raise ValueError(
                        "admin_panel_password must be a strong non-default secret in production."
                    )
                if (
                    self.admin_panel_secret_key in insecure_defaults
                    or len(self.admin_panel_secret_key) < 32
                ):
                    raise ValueError(
                        "admin_panel_secret_key must be a strong non-default secret in production."
                    )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
