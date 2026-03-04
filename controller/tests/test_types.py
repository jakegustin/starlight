from __future__ import annotations

import pytest

from controller import ZoneEvent


def test_zone_event_is_frozen():
    """
    Ensure ZoneEvent instances are immutable/frozen
    """
    event = ZoneEvent("user_entered", "z1", "u1", 0.0)
    with pytest.raises(AttributeError):
        event.zone_id = "z2"
