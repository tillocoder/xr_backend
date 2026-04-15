import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_mode_rejects_insecure_demo_auth() -> None:
    with pytest.raises(ValidationError, match="XR_ALLOW_INSECURE_DEMO_AUTH=true"):
        Settings(
            production_mode=True,
            allow_insecure_demo_auth=True,
        )


def test_production_mode_rejects_non_https_public_url() -> None:
    with pytest.raises(ValidationError, match="XR_PUBLIC_BASE_URL must use https"):
        Settings(
            production_mode=True,
            public_base_url="http://api.example.com",
        )


def test_admin_panel_requires_secure_credentials_when_enabled() -> None:
    with pytest.raises(
        ValidationError,
        match="XR_ADMIN_PANEL_ENABLED=true requires a strong admin password and secret key",
    ):
        Settings(
            admin_panel_enabled=True,
            admin_panel_password="change-me-admin",
            admin_panel_secret_key="change-me-admin-secret",
        )


def test_docs_paths_are_disabled_when_api_docs_are_disabled() -> None:
    settings = Settings(api_docs_enabled=False)

    assert settings.openapi_url_path is None
    assert settings.docs_url_path is None
    assert settings.redoc_url_path is None
