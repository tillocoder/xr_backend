from __future__ import annotations

import asyncio
import concurrent.futures
import json
from pathlib import Path

from app.core.config import Settings


class FirebasePushService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app = None
        self._messaging = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._send_semaphore = asyncio.Semaphore(
            max(1, int(self._settings.firebase_push_max_concurrent_batches))
        )
        self._max_workers = max(1, int(self._settings.firebase_push_max_workers))

    @property
    def is_configured(self) -> bool:
        return bool(
            self._settings.firebase_service_account_path
            or self._settings.firebase_service_account_json
        )

    async def send_to_tokens(
        self,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str] | None = None,
    ) -> list[str]:
        normalized_tokens = [token.strip() for token in tokens if token.strip()]
        if not normalized_tokens or not await self._ensure_initialized():
            return []

        async with self._send_semaphore:
            return await asyncio.to_thread(
                self._send_to_tokens_blocking,
                normalized_tokens,
                title,
                body,
                data or {},
            )

    async def _ensure_initialized(self) -> bool:
        if self._app is not None and self._messaging is not None:
            return True
        async with self._init_lock:
            if self._app is not None and self._messaging is not None:
                return True
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

    def _send_to_tokens_blocking(
        self,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str],
    ) -> list[str]:
        messaging = self._messaging
        app = self._app
        if messaging is None or app is None:
            return []

        image = (
            data.get("sender_avatar_url")
            or data.get("senderAvatarUrl")
            or None
        )
        payload = {key: value for key, value in data.items() if value}
        max_workers = min(self._max_workers, len(tokens))
        if max_workers <= 0:
            return []

        def send_single(token: str) -> str | None:
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(
                    title=title,
                    body=body,
                    image=image,
                ),
                data=payload,
                android=messaging.AndroidConfig(priority="high"),
            )
            try:
                messaging.send(message, app=app)
            except Exception as exc:
                if self._is_invalid_token_error(exc):
                    return token
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            invalid_tokens = [
                invalid_token
                for invalid_token in executor.map(send_single, tokens)
                if invalid_token is not None
            ]
        return invalid_tokens

    def _is_invalid_token_error(self, exc: Exception) -> bool:
        error = str(exc or "").lower()
        return (
            "registration-token-not-registered" in error
            or "unregistered" in error
            or "invalid-registration-token" in error
        )

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
