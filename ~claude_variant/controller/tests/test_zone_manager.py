"""
Unit tests for controller.zone_manager.ZoneManager
"""

import pytest
from controller.zone_manager import ZoneManager


# ── Registration ──────────────────────────────────────────────────────────────

class TestZoneManagerRegistration:
    def test_register_single_receiver(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        assert zm.get_zones() == ["rec-A"]

    def test_register_multiple_receivers_preserves_order(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.register_receiver("rec-C")
        assert zm.get_zones() == ["rec-A", "rec-B", "rec-C"]

    def test_duplicate_registration_is_ignored(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-A")
        assert zm.get_zones() == ["rec-A"]
        assert zm.zone_count() == 1

    def test_zone_count_reflects_unique_registrations(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        assert zm.zone_count() == 2


# ── Index lookups ─────────────────────────────────────────────────────────────

class TestZoneManagerIndexLookup:
    def test_get_zone_index_first(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        assert zm.get_zone_index("rec-A") == 0

    def test_get_zone_index_second(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        assert zm.get_zone_index("rec-B") == 1

    def test_get_zone_index_unknown_returns_none(self):
        zm = ZoneManager()
        assert zm.get_zone_index("unknown") is None

    def test_get_receiver_at_zone_valid(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        assert zm.get_receiver_at_zone(0) == "rec-A"
        assert zm.get_receiver_at_zone(1) == "rec-B"

    def test_get_receiver_at_zone_out_of_bounds_returns_none(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        assert zm.get_receiver_at_zone(5) is None

    def test_get_receiver_at_zone_negative_returns_none(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        assert zm.get_receiver_at_zone(-1) is None

    def test_empty_manager_returns_none(self):
        zm = ZoneManager()
        assert zm.get_zone_index("rec-A") is None
        assert zm.get_receiver_at_zone(0) is None


# ── Next zone lookups ─────────────────────────────────────────────────────────

class TestZoneManagerNextZone:
    def test_next_zone_from_first(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        assert zm.get_next_zone_receiver("rec-A") == "rec-B"

    def test_next_zone_from_middle(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.register_receiver("rec-C")
        assert zm.get_next_zone_receiver("rec-B") == "rec-C"

    def test_next_zone_from_last_returns_none(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        assert zm.get_next_zone_receiver("rec-B") is None

    def test_next_zone_unknown_receiver_returns_none(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        assert zm.get_next_zone_receiver("unknown") is None

    def test_next_zone_single_receiver_returns_none(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        assert zm.get_next_zone_receiver("rec-A") is None

    def test_next_zone_empty_manager_returns_none(self):
        zm = ZoneManager()
        assert zm.get_next_zone_receiver("rec-A") is None


# ── Reordering ────────────────────────────────────────────────────────────────

class TestZoneManagerReorder:
    def test_reorder_updates_zone_list(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.register_receiver("rec-C")
        zm.set_order(["rec-C", "rec-A", "rec-B"])
        assert zm.get_zones() == ["rec-C", "rec-A", "rec-B"]

    def test_reorder_unknown_ids_are_ignored(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.set_order(["rec-A", "ghost", "rec-B"])
        zones = zm.get_zones()
        assert "ghost" not in zones
        assert "rec-A" in zones
        assert "rec-B" in zones

    def test_partial_reorder_appends_missing_receivers(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.register_receiver("rec-C")
        zm.set_order(["rec-C", "rec-A"])  # rec-B omitted
        zones = zm.get_zones()
        assert zones[0] == "rec-C"
        assert zones[1] == "rec-A"
        assert "rec-B" in zones  # Appended at end

    def test_reorder_then_index_lookup_is_consistent(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.set_order(["rec-B", "rec-A"])
        assert zm.get_zone_index("rec-B") == 0
        assert zm.get_zone_index("rec-A") == 1

    def test_reorder_then_next_zone_is_consistent(self):
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zm.register_receiver("rec-B")
        zm.set_order(["rec-B", "rec-A"])
        # After reversal, next after rec-B is rec-A
        assert zm.get_next_zone_receiver("rec-B") == "rec-A"
        assert zm.get_next_zone_receiver("rec-A") is None

    def test_get_zones_returns_copy(self):
        """Mutating the returned list must not affect internal state."""
        zm = ZoneManager()
        zm.register_receiver("rec-A")
        zones = zm.get_zones()
        zones.append("injected")
        assert "injected" not in zm.get_zones()
