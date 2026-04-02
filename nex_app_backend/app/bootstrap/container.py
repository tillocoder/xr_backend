from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(slots=True)
class AppContainer:
    settings: Settings

