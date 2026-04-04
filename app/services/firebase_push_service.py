from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from pathlib import Path

from app.core.config import Settings

LOGGER = logging.getLogger(__name__)

_FCM_RESERVED_DATA_KEYS = frozenset({
    "collapse_key",
    "from",
    "google",
    "message_type",
})

_FCM_RESERVED_DATA_KEY_ALIASES = {
    "collapse_key": "collapseKey",
    "from": "fromValue",
    "message_type": "messageType",
}


class FirebasePushService:
    _android_channel_id = "xr_hodl_news_channel"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._app = None
        self._messaging = None
        self._credential = None
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
        payload = self._sanitize_data_payload(data or {})
        if not normalized_tokens or not await self._ensure_initialized():
            return []

        async with self._send_semaphore:
            return await asyncio.to_thread(
                self._send_to_tokens_blocking,
                normalized_tokens,
                title,
                body,
                payload,
            )

    async def probe_configuration(self) -> bool:
        if not self.is_configured:
            return False
        if not await self._ensure_initialized():
            return False
        credential = self._credential
        if credential is None:
            return False
        try:
            await asyncio.to_thread(self._refresh_access_token_blocking, credential)
        except Exception:
            LOGGER.exception("firebase_push_credentials_probe_failed")
            return False
        return True

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
                LOGGER.exception("firebase_push_sdk_import_failed")
                return False

            credential = self._build_credential(credentials)
            if credential is None:
                LOGGER.error("firebase_push_credentials_unavailable")
                return False
            self._credential = credential

            try:
                self._app = firebase_admin.get_app("xr_backend")
            except ValueError:
                try:
                    self._app = firebase_admin.initialize_app(credential, name="xr_backend")
                except Exception:
                    self._app = None
                    LOGGER.exception("firebase_push_initialization_failed")
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
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id=self._android_channel_id,
                        sound="default",
                        image=image,
                    ),
                ),
                apns=messaging.APNSConfig(
                    headers={
                        "apns-priority": "10",
                        "apns-push-type": "alert",
                    },
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            sound="default",
                            mutable_content=True,
                            content_available=True,
                        )
                    ),
                ),
            )
            try:
                messaging.send(message, app=app)
            except Exception as exc:
                if self._is_invalid_token_error(exc):
                    return token
                LOGGER.warning(
                    "firebase_push_send_failed token_suffix=%s error=%s",
                    token[-8:],
                    str(exc),
                )
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
            or "notregistered" in error
            or "invalid-registration-token" in error
            or "requested entity was not found" in error
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
            LOGGER.error(
                "firebase_push_credentials_file_missing",
                extra={"path": str(path)},
            )
            return None
        try:
            return credentials_module.Certificate(str(path))
        except Exception:
            LOGGER.exception(
                "firebase_push_credentials_load_failed",
                extra={"path": str(path)},
            )
            return None

    def _refresh_access_token_blocking(self, credential) -> None:
        from google.auth.transport.requests import Request

        google_credential = credential.get_credential()
        google_credential.refresh(Request())

    def _sanitize_data_payload(self, data: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for raw_key, raw_value in data.items():
            key = str(raw_key).strip()
            if not key:
                continue
            lowered_key = key.lower()
            if lowered_key.startswith("google.") or lowered_key.startswith("gcm."):
                continue
            key = _FCM_RESERVED_DATA_KEY_ALIASES.get(lowered_key, key)
            lowered_key = key.lower()
            if lowered_key in _FCM_RESERVED_DATA_KEYS:
                continue
            value = str(raw_value or "").strip()
            if value:
                normalized[key] = value
        return normalized
