from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ReminderKind = Literal["once", "daily"]


@dataclass(slots=True, frozen=True)
class ReminderRecord:
    reminder_id: int
    chat_id: int
    recipient_chat_id: int
    timezone: str
    text: str
    kind: ReminderKind
    recurring: bool
    run_at: datetime | None
    daily_time: str | None
    voice: bool
    voice_file_id: str | None
    last_completed_at: datetime | None
    is_active: bool
    created_at: datetime
