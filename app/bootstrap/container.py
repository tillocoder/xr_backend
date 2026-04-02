from __future__ import annotations

from dataclasses import dataclass

from app.application.system.services import SystemStatusService
from app.core.config import Settings
from app.infrastructure.rate_limit.service import RedisRateLimiter
from app.services.auth_session_service import AuthSessionService
from app.services.cache import RedisCache
from app.services.firebase_push_service import FirebasePushService
from app.services.google_identity_service import GoogleIdentityService
from app.services.market_runtime_service import MarketRuntimeService
from app.services.news_runtime_service import NewsRuntimeService
from app.services.notification_service import NotificationService
from app.services.presence_service import PresenceService
from app.services.presence_runtime_service import PresenceRuntimeService
from app.services.push_token_service import PushTokenService
from app.services.runtime_lease_service import RuntimeLeaseService
from app.ws.bus import RedisEventBus
from app.ws.manager import ConnectionManager


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    cache: RedisCache
    bus: RedisEventBus
    ws_manager: ConnectionManager
    push_token_service: PushTokenService
    auth_session_service: AuthSessionService
    firebase_push_service: FirebasePushService
    google_identity_service: GoogleIdentityService
    news_runtime_service: NewsRuntimeService
    market_runtime_service: MarketRuntimeService
    notification_service: NotificationService
    presence_service: PresenceService
    presence_runtime_service: PresenceRuntimeService
    runtime_lease_service: RuntimeLeaseService
    system_status_service: SystemStatusService
    rate_limiter: RedisRateLimiter | None = None

    def attach_to_app(self, app) -> None:
        app.state.container = self
        app.state.settings = self.settings
        app.state.cache = self.cache
        app.state.bus = self.bus
        app.state.ws_manager = self.ws_manager
        app.state.push_token_service = self.push_token_service
        app.state.auth_session_service = self.auth_session_service
        app.state.firebase_push_service = self.firebase_push_service
        app.state.google_identity_service = self.google_identity_service
        app.state.news_runtime_service = self.news_runtime_service
        app.state.market_runtime_service = self.market_runtime_service
        app.state.notification_service = self.notification_service
        app.state.presence_service = self.presence_service
        app.state.presence_runtime_service = self.presence_runtime_service
        app.state.runtime_lease_service = self.runtime_lease_service
