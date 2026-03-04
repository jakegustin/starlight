from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ZoneEvent:
    event_type: str
    zone_id: str
    user_uuid: str
    timestamp: float

ZoneEvents = List["ZoneEvent"]