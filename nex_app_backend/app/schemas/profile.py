from __future__ import annotations

from pydantic import BaseModel


class ProfileOverviewResponse(BaseModel):
    id: str
    full_name: str
    email: str
    role: str
    membership_label: str
    city: str
    verified: bool

