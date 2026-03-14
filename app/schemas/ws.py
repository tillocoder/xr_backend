from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WsEnvelope(BaseModel):
    type: str
    topic: str
    seq: int | None = None
    ts: datetime = Field(default_factory=datetime.utcnow)
    data: dict[str, Any]
