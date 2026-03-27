from __future__ import annotations

from app.services.presence_service import PresenceService
from app.services.periodic_runtime_service import PeriodicRuntimeService
from app.ws.manager import ConnectionManager


class PresenceRuntimeService(PeriodicRuntimeService):
    def __init__(
        self,
        *,
        manager: ConnectionManager,
        presence_service: PresenceService,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._presence_service = presence_service

    @property
    def poll_interval_seconds(self) -> int:
        return self._presence_service.refresh_interval_seconds

    @property
    def initial_delay_seconds(self) -> int:
        return self.poll_interval_seconds

    async def run_cycle(self) -> None:
        await self._manager.reap_stale_connections()
        await self._presence_service.refresh_connections_by_user(
            self._manager.snapshot_connections_by_user()
        )
