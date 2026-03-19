from __future__ import annotations

from typing import Protocol

from app.domain.system.models import DependencyStatus


class AsyncDependencyProbe(Protocol):
    name: str

    async def probe(self) -> DependencyStatus: ...
