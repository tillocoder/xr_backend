from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.application.system.interfaces import AsyncDependencyProbe
from app.domain.system.models import ReadinessReport


class SystemStatusService:
    def __init__(self, probes: Sequence[AsyncDependencyProbe]) -> None:
        self._probes = tuple(probes)

    def health_payload(self) -> dict[str, bool]:
        return {"ok": True}

    async def readiness_report(self) -> ReadinessReport:
        dependencies = tuple(await asyncio.gather(*(probe.probe() for probe in self._probes)))
        return ReadinessReport.from_dependencies(dependencies)
