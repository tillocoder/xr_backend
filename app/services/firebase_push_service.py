from __future__ import annotations

import json
from pathlib import Path

from app.core.config import Settings


class FirebasePushService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app = None
        self._messaging = None
        self._initialized = False

    @property
    def is_configured(self) -> bool:
        return bool(
            self._settings.firebase_service_account_path
            or self._settings.firebase_service_account_json
        )

    def send_to_tokens(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> list[str]:
        normalized_tokens = [token.strip() for token in tokens if token.strip()]
        if not normalized_tokens or not self._ensure_initialized():
            return []

        messaging = self._messaging
        if messaging is None:
            return []
        image = (
            (data or {}).get("sender_avatar_url")
            or (data or {}).get("senderAvatarUrl")
            or None
        )

        message = messaging.MulticastMessage(
            tokens=normalized_tokens,
            notification=messaging.Notification(
                title=title,
                body=body,
                image=image,
            ),
            data={key: value for key, value in (data or {}).items() if value},
            android=messaging.AndroidConfig(priority="high"),
        )
        invalid_tokens: list[str] = []
        response = messaging.send_each_for_multicast(message, app=self._app)
        for index, result in enumerate(response.responses):
            if result.success:
                continue
            error = str(result.exception or "").lower()
            if (
                "registration-token-not-registered" in error
                or "unregistered" in error
                or "invalid-registration-token" in error
            ):
                invalid_tokens.append(normalized_tokens[index])
        return invalid_tokens

    def _ensure_initialized(self) -> bool:
        if self._initialized:
            return self._app is not None and self._messaging is not None
        self._initialized = True
        try:
            import firebase_admin
            from firebase_admin import credentials, messaging
        except Exception:
            return False

        credential = self._build_credential(credentials)
        if credential is None:
            return False

        try:
            self._app = firebase_admin.get_app("xr_backend")
        except ValueError:
            try:
                self._app = firebase_admin.initialize_app(credential, name="xr_backend")
            except Exception:
                self._app = None
                return False
        self._messaging = messaging
        return True

    def _build_credential(self, credentials_module):
        if self._settings.firebase_service_account_json:
            try:
                return credentials_module.Certificate(
                    json.loads(self._settings.firebase_service_account_json)
                )
            except Exception:
                return None

        raw_path = self._settings.firebase_service_account_path
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / raw_path
        if not path.exists():
            return None
        try:
            return credentials_module.Certificate(str(path))
        except Exception:
            return None
