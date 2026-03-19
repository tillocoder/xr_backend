from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(slots=True, frozen=True)
class DependencyStatus:
    name: str
    ok: bool
    latency_ms: float
    detail: str = ""


@dataclass(slots=True, frozen=True)
class ReadinessReport:
    ok: bool
    checked_at: datetime
    dependencies: tuple[DependencyStatus, ...]

    @classmethod
    def from_dependencies(cls, dependencies: tuple[DependencyStatus, ...]) -> "ReadinessReport":
        checked_at = datetime.now(timezone.utc)
        return cls(
            ok=all(item.ok for item in dependencies),
            checked_at=checked_at,
            dependencies=dependencies,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checkedAt": self.checked_at.isoformat(),
            "dependencies": [
                {
                    "name": dependency.name,
                    "ok": dependency.ok,
                    "latencyMs": round(dependency.latency_ms, 2),
                    "detail": dependency.detail,
                }
                for dependency in self.dependencies
            ],
        }
