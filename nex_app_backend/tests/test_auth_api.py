from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_DB = Path(__file__).resolve().parent / "test_backend.db"
os.environ["NEX_DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["NEX_AUTO_CREATE_SCHEMA"] = "true"

from app.core.config import get_settings

get_settings.cache_clear()

from app.main import app


def test_register_login_and_me_flow() -> None:
    if TEST_DB.exists():
        TEST_DB.unlink()

    with TestClient(app) as client:
        register_response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "user@nex.app",
                "full_name": "Nex User",
                "password": "ChangeMe123!",
            },
        )
        assert register_response.status_code == 200
        register_payload = register_response.json()
        access_token = register_payload["tokens"]["accessToken"]
        refresh_token = register_payload["tokens"]["refreshToken"]

        me_response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_response.status_code == 200
        assert me_response.json()["email"] == "user@nex.app"

        refresh_response = client.post(
            "/api/v1/auth/refresh",
            json={"refreshToken": refresh_token},
        )
        assert refresh_response.status_code == 200
        assert "accessToken" in refresh_response.json()["tokens"]


def test_preview_bootstrap_flow() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/profile/preview-bootstrap")
        assert response.status_code == 200
        payload = response.json()

        assert payload["wallet_card"]["owner_name"] == "Nex Preview"
        assert payload["portfolio"]["total_value"] == 27_800_000
        assert payload["battle_lobby"]["registered_players"] == 8
        assert len(payload["market_signals"]) == 3


def test_register_rejects_weak_password() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "weak@nex.app",
                "full_name": "Weak Password",
                "password": "weakpass",
            },
        )

        assert response.status_code == 400
        payload = response.json()
        assert "Password must" in payload["detail"]
        assert payload["requestId"]
