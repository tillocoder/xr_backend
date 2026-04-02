from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(slots=True)
class VerifiedGoogleIdentity:
    user_id: str
    email: str | None
    display_name: str | None
    photo_url: str | None
    email_verified: bool


class GoogleIdentityService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._allowed_client_ids = settings.google_oauth_allowed_client_ids_list

    @property
    def is_configured(self) -> bool:
        return bool(self._allowed_client_ids)

    async def verify_id_token(self, id_token: str) -> VerifiedGoogleIdentity:
        normalized = id_token.strip()
        if not normalized:
            raise ValueError("Google ID token is required.")
        if not self.is_configured:
            raise RuntimeError("Google OAuth client ids are not configured.")

        try:
            from google.auth.transport.requests import Request as GoogleRequest
            from google.oauth2 import id_token as google_id_token
        except Exception as exc:  # pragma: no cover - dependency is installed in runtime
            raise RuntimeError("Google auth verification dependency is unavailable.") from exc

        request = GoogleRequest()
        try:
            claims = google_id_token.verify_oauth2_token(
                normalized,
                request,
                audience=None,
            )
        except Exception as exc:
            raise ValueError("Invalid Google ID token.") from exc

        audience = str(claims.get("aud") or "").strip()
        if audience not in self._allowed_client_ids:
            raise ValueError("Google token audience is not allowed.")

        issuer = str(claims.get("iss") or "").strip().lower()
        if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("Google token issuer is invalid.")

        user_id = str(claims.get("sub") or "").strip()
        if not user_id:
            raise ValueError("Google token subject is missing.")

        email = str(claims.get("email") or "").strip() or None
        email_verified = bool(claims.get("email_verified"))
        if self._settings.google_oauth_require_verified_email and not email_verified:
            raise ValueError("Google account email is not verified.")

        return VerifiedGoogleIdentity(
            user_id=user_id,
            email=email,
            display_name=str(claims.get("name") or "").strip() or None,
            photo_url=str(claims.get("picture") or "").strip() or None,
            email_verified=email_verified,
        )
