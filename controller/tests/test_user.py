"""
Unit tests for the User class of the Starlight controller
"""
from __future__ import annotations

import pytest

from controller import User


class TestUser:
    """Unit tests for per-UUID tracking state."""

    def test_defaults(self):
        """
        Ensure default values when creating a User instance are correct
        """
        u = User("uuid-1")
        assert u.uuid == "uuid-1"
        assert u.priority == 0
        assert u.current_zone is None
        assert not u.zone_history

    def test_priority_assignment(self):
        """
        Ensure custom priority setting is reflected in the instance attribute
        """
        u = User("uuid-1", priority=5)
        assert u.priority == 5

    def test_buffer_created_once(self):
        """
        Ensure an existing buffer is referenced, and not recreated, with get_buffer
        """
        u = User("uuid-1")
        b1 = u.get_buffer("zone-a", window_size=5)
        b2 = u.get_buffer("zone-a", window_size=99)
        assert b1 is b2

    def test_smoothed_rssi_none_without_data(self):
        """
        Ensure a User instance with no RSSI values yields None for smoothed_rssi for error checking
        """
        u = User("uuid-1")
        assert u.smoothed_rssi("zone-x") is None

    def test_smoothed_rssi_with_data(self):
        """
        Ensure a User instance with RSSI values yields the appropriate average value
        """
        u = User("uuid-1")
        buf = u.get_buffer("zone-a", window_size=5, gate_threshold=100.0)
        buf.add(40.0)
        buf.add(60.0)
        assert u.smoothed_rssi("zone-a") == pytest.approx(50.0)

    def test_reset_clears_state(self):
        """
        Ensure that resetting a User instance appropriately resets its attributes
        """
        u = User("uuid-1")
        u.current_zone = "zone-a"
        u.zone_history.append("zone-a")
        buf = u.get_buffer("zone-a", window_size=5)
        buf.add(60.0)

        u.reset()

        assert u.current_zone is None
        assert not u.zone_history
        assert buf.count == 0

    def test_repr(self):
        """
        Ensure appopriate elements of the instance are printed correctly
        """
        u = User("uuid-1", priority=3)
        u.current_zone = "zone-a"
        r = repr(u)
        assert "uuid-1" in r
        assert "zone-a" in r
