from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import Settings


def test_production_settings_require_hardened_values() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_production_settings_apply_safe_defaults() -> None:
    settings = Settings(
        environment="production",
        auto_create_schema=False,
        jwt_secret_key="x" * 48,
        admin_panel_password="StrongerAdmin#123",
        admin_panel_secret_key="y" * 48,
    )

    assert settings.api_docs_enabled is False
    assert settings.json_logs is True
    assert settings.error_include_details_in_response is False
