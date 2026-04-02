from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.services.google_identity_service import GoogleIdentityService


def test_verify_id_token_rejects_invalid_plain_string(monkeypatch) -> None:
    service = GoogleIdentityService(Settings())

    import google.oauth2.id_token as google_id_token

    def fake_verify_oauth2_token(token, request, audience=None):
        raise ValueError("Token used too early")

    monkeypatch.setattr(
        google_id_token,
        "verify_oauth2_token",
        fake_verify_oauth2_token,
    )

    with pytest.raises(ValueError, match="Invalid Google ID token."):
        asyncio.run(service.verify_id_token("string"))


def test_verify_id_token_accepts_allowed_google_claims(monkeypatch) -> None:
    settings = Settings(
        google_oauth_allowed_client_ids=(
            "allowed-client.apps.googleusercontent.com,"
            "other-client.apps.googleusercontent.com"
        )
    )
    service = GoogleIdentityService(settings)

    import google.oauth2.id_token as google_id_token

    def fake_verify_oauth2_token(token, request, audience=None):
        assert token == "valid-token"
        return {
            "aud": "allowed-client.apps.googleusercontent.com",
            "iss": "https://accounts.google.com",
            "sub": "google-user-123",
            "email": "user@example.com",
            "email_verified": True,
            "name": "XR User",
            "picture": "https://example.com/avatar.png",
        }

    monkeypatch.setattr(
        google_id_token,
        "verify_oauth2_token",
        fake_verify_oauth2_token,
    )

    identity = asyncio.run(service.verify_id_token("valid-token"))

    assert identity.user_id == "google-user-123"
    assert identity.email == "user@example.com"
    assert identity.display_name == "XR User"
    assert identity.photo_url == "https://example.com/avatar.png"
    assert identity.email_verified is True


def test_verify_id_token_rejects_untrusted_audience(monkeypatch) -> None:
    settings = Settings(
        google_oauth_allowed_client_ids="allowed-client.apps.googleusercontent.com"
    )
    service = GoogleIdentityService(settings)

    import google.oauth2.id_token as google_id_token

    def fake_verify_oauth2_token(token, request, audience=None):
        return {
            "aud": "wrong-client.apps.googleusercontent.com",
            "iss": "https://accounts.google.com",
            "sub": "google-user-123",
            "email": "user@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(
        google_id_token,
        "verify_oauth2_token",
        fake_verify_oauth2_token,
    )

    with pytest.raises(ValueError, match="audience is not allowed"):
        asyncio.run(service.verify_id_token("valid-token"))
