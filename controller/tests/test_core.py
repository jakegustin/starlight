"""
Unit tests for the CentralController class of the Starlight controller
"""
from __future__ import annotations

import json
import pytest

from controller import CentralController

# pylint: disable=too-many-public-methods
# We're disabling this linter warning here since a unit test file typically does not
# need to worry about the number of public methods unlike a proper class

class TestCentralController:
    """
    Integration tests for the full ratcheting pipeline
    """

    ZONES = ["zone-1", "zone-2"]

    def _create_controller(self, **kw):
        """
        Create a controller with known defaults
        """
        defaults = {
            "zone_order": self.ZONES,
            "timeout_seconds": 5.0,
            "window_size": 3,
        }
        defaults.update(kw)
        return CentralController(**defaults) # Note the usage of ** for kwargs

    @staticmethod
    def _json(receiver: str, ts: float, uuid: str, rssi: float) -> str:
        """
        Produces a JSON with expected values from BLE receivers
        """
        return json.dumps(
            {
                "id": receiver,
                "ts": ts,
                "uuid": uuid,
                "rssi": rssi,
            }
        )

    def test_ingest_json_does_not_create_user(self):
        """
        Ensure JSON ingestion does not create a new User instance
        """
        ctrl = self._create_controller()
        ctrl.ingest(self._json("zone-1", 1.0, "u1", 50))
        user = ctrl.get_user("u1")
        assert user is None

    def test_ingest_reading_direct(self):
        """
        Ensure entry/reading ingestion creates a User instance for a previously unknown user
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 2.0, "u1", 50)
        assert ctrl.get_user("u1") is not None

    def test_enter_first_zone(self):
        """
        Ensure the user_entered event is correctly logged after the appropriate reading
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        events = ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        entered = [e for e in events if e.event_type == "user_entered"]
        assert len(entered) == 1
        assert entered[0].zone_id == "zone-1"
        assert entered[0].user_uuid == "u1"

    def test_user_current_zone_set_on_entry(self):
        """
        Ensure the user's current zone is updated after entering the queue/zone
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        assert ctrl.get_user("u1").current_zone == "zone-1"

    def test_below_threshold_no_entry(self):
        """
        Ensure that RSSI transmissions below the minimum threshold do not update the user's zone
        """
        ctrl = self._create_controller() # Default RSSI entry threshold is 30
        ctrl.register_user("u1")
        events = []
        for i in range(5):
            events.extend(
                ctrl.ingest_reading("zone-1", float(i), "u1", 10)
            )
        assert all(e.event_type != "user_entered" for e in events)

    def test_advance_to_second_zone(self):
        """
        Ensure the system advances the user to the upcoming zone (and leaves the existing zone)
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        events = ctrl.ingest_reading("zone-2", 1.0, "u1", 60)

        types = [e.event_type for e in events]
        assert "user_exited" in types
        assert "user_entered" in types
        assert ctrl.get_user("u1").current_zone == "zone-2"

    def test_exit_event_on_advancement(self):
        """
        Ensure only one exit event is created when the user exits a zone
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        events = ctrl.ingest_reading("zone-2", 1.0, "u1", 60)

        exited = [
            e for e in events
            if e.event_type == "user_exited" and e.zone_id == "zone-1"
        ]
        assert len(exited) == 1

    def test_cannot_skip_zone(self):
        """
        Ensure the user cannot skip ahead of a zone
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        events = []
        for i in range(5):
            events.extend(
                ctrl.ingest_reading("zone-2", float(i), "u1", 80)
            )
        entered = [e for e in events if e.event_type == "user_entered"]
        assert len(entered) == 0

    def test_three_zone_ratchet(self):
        """
        Verify basic forward-facing ratchet functionality with 3 zones triggers zone entrances
        """
        zones = ["z1", "z2", "z3"]
        ctrl = self._create_controller(zone_order=zones)
        ctrl.register_user("u1")

        ctrl.ingest_reading("z1", 0.0, "u1", 50)
        ctrl.ingest_reading("z2", 1.0, "u1", 60)
        ctrl.ingest_reading("z3", 2.0, "u1", 70)

        assert ctrl.get_user("u1").current_zone == "z3"
        entered = [
            e for e in ctrl.event_log if e.event_type == "user_entered"
        ]
        assert [e.zone_id for e in entered] == ["z1", "z2", "z3"]

    def test_no_duplicate_entry_events(self):
        """
        Ensure the user staying in a zone does not trigger re-entrances
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        all_events = []
        for i in range(5):
            all_events.extend(
                ctrl.ingest_reading("zone-1", float(i), "u1", 50)
            )
        entered = [e for e in all_events if e.event_type == "user_entered"]
        assert len(entered) == 1

    def test_exit_last_zone_resets_user(self):
        """
        Ensure that a user exiting the queue at the last zone reset's the user's attributes
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        ctrl.ingest_reading("zone-2", 1.0, "u1", 60)
        assert ctrl.get_user("u1").current_zone == "zone-2"

        # User is in zone 2, let's weaken the signal to force an exit
        events = []
        for i in range(3):
            events.extend(
                ctrl.ingest_reading("zone-2", 2.0 + i, "u1", 5)
            )

        exited = [
            e for e in events
            if e.event_type == "user_exited" and e.zone_id == "zone-2"
        ]
        assert len(exited) >= 1
        assert ctrl.get_user("u1").current_zone is None

    def test_single_zone_enter_and_exit(self):
        """
        Ensure the system advances correctly with a one-zone configuration
        """
        ctrl = self._create_controller(zone_order=["only-zone"])
        ctrl.register_user("u1")
        ctrl.ingest_reading("only-zone", 0.0, "u1", 50)
        assert ctrl.get_user("u1").current_zone == "only-zone"

        for i in range(3):
            ctrl.ingest_reading("only-zone", 1.0 + i, "u1", 2)
        assert ctrl.get_user("u1").current_zone is None

    def test_timeout_evicts_user(self):
        """
        Ensure the timeout removes a long-gone user from the queue
        """
        ctrl = self._create_controller(timeout_seconds=5.0)
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 1.0, "u1", 50)
        assert ctrl.get_user("u1").current_zone == "zone-1"

        events = ctrl.ingest_reading("zone-1", 20.0, "u1", 1)
        timeout = [e for e in events if e.event_type == "user_timeout"]
        assert len(timeout) == 1
        assert ctrl.get_user("u1").current_zone is None

    def test_no_timeout_while_signal_strong(self):
        """
        Ensure that a user remains in the queue whilst their signal strength meets the minimum
        """
        ctrl = self._create_controller(timeout_seconds=5.0)
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)

        events = ctrl.ingest_reading("zone-1", 20.0, "u1", 50)
        timeout = [e for e in events if e.event_type == "user_timeout"]
        assert len(timeout) == 0

    def test_timeout_only_affects_zoned_users(self):
        """
        Ensure that timeouts only occur to users that are currently in a zone
        """
        ctrl = self._create_controller(timeout_seconds=5.0)
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 5)
        events = ctrl.ingest_reading("zone-1", 20.0, "u1", 5) # RSSI of 5 is too weak!
        timeout = [e for e in events if e.event_type == "user_timeout"]
        assert len(timeout) == 0

    def test_rejoin_after_timeout(self):
        """
        Ensure a user that timed out can rejoin at the start of the queue
        """
        ctrl = self._create_controller(timeout_seconds=5.0)
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        ctrl.ingest_reading("zone-1", 20.0, "u1", 1)  # Triggers timeout!
        assert ctrl.get_user("u1").current_zone is None

        events = ctrl.ingest_reading("zone-1", 25.0, "u1", 50)
        entered = [
            e for e in events
            if e.event_type == "user_entered" and e.zone_id == "zone-1"
        ]
        assert len(entered) == 1

    def test_priority_user_active(self):
        """
        Ensure that a user with high priority has the zone activate for them in particular
        """
        ctrl = self._create_controller()
        ctrl.register_user("priority", priority=10)
        ctrl.register_user("standard", priority=0)

        ctrl.ingest_reading("zone-1", 0.0, "standard", 50)
        ctrl.ingest_reading("zone-1", 1.0, "priority", 50)
        assert ctrl.active_user_at("zone-1") == "priority"

    def test_standard_active_after_priority_advances(self):
        """
        Ensure that a zone returns to a lower priority user's config after a priority user leaves
        """
        ctrl = self._create_controller()
        ctrl.register_user("priority", priority=10)
        ctrl.register_user("standard", priority=0)
        assert ctrl.get_user("priority").priority == 10
        assert ctrl.get_user("standard").priority == 0

        ctrl.ingest_reading("zone-1", 0.0, "standard", 50)
        ctrl.ingest_reading("zone-1", 1.0, "priority", 50)
        assert ctrl.active_user_at("zone-1") == "priority"

        ctrl.ingest_reading("zone-2", 2.0, "priority", 60)
        assert ctrl.active_user_at("zone-1") == "standard"
        assert ctrl.active_user_at("zone-2") == "priority"

    def test_register_user_updates_existing(self):
        """
        Ensure that priority overrides are reflected in the system state
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        assert ctrl.get_user("u1").priority == 0

        ctrl.register_user("u1", priority=5)
        assert ctrl.get_user("u1").priority == 5

    def test_independent_users(self):
        """
        Ensure that one standing user and one moving user are depicted correctly
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.register_user("u2")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        ctrl.ingest_reading("zone-1", 0.5, "u2", 50)
        assert ctrl.get_user("u1").current_zone == "zone-1"
        assert ctrl.get_user("u2").current_zone == "zone-1"

        ctrl.ingest_reading("zone-2", 1.0, "u1", 60)
        assert ctrl.get_user("u1").current_zone == "zone-2"
        assert ctrl.get_user("u2").current_zone == "zone-1"

    def test_users_in_zone(self):
        """
        Ensure that two simultaneous users can be placed in the same zone
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.register_user("u2")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        ctrl.ingest_reading("zone-1", 0.5, "u2", 50)
        users = ctrl.users_in_zone("zone-1")
        uuids = {u.uuid for u in users}
        assert uuids == {"u1", "u2"}

    def test_automatic_registration_single(self):
        """
        Ensure that automatic registration adds one user successfully
        """
        ctrl = self._create_controller()
        ctrl.automatic_registration = True
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        assert ctrl.get_user("u1") is not None

    def test_automatic_registration_multiple(self):
        """
        Ensure that automatic registration adds multiple users successfully
        """
        ctrl = self._create_controller()
        ctrl.automatic_registration = True
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        ctrl.ingest_reading("zone-1", 0.5, "u2", 50)
        assert ctrl.get_user("u1") is not None
        assert ctrl.get_user("u2") is not None

    def test_empty_zone_order_raises(self):
        """
        Ensure that a Central Controller cannot be created without any zones
        """
        with pytest.raises(ValueError, match="at least one zone"):
            CentralController([])

    def test_zone_active_none_initially(self):
        """
        Ensure that the active user of a zone is, by default, None
        """
        ctrl = self._create_controller()
        assert ctrl.active_user_at("zone-1") is None
        assert ctrl.active_user_at("zone-2") is None

    def test_event_log_accumulates(self):
        """
        Ensure that log updates are tracked appropriately for a moving user
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1")
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        ctrl.ingest_reading("zone-2", 1.0, "u1", 60)
        assert len(ctrl.event_log) >= 3  # Enter zone-1, exit zone-1, enter zone-2

    def test_event_log_is_defensive_copy(self):
        """
        Ensure accessing the event log provides a copy of the log to avoid internal state changes
        """
        ctrl = self._create_controller()
        ctrl.register_user("u1", 1)
        ctrl.ingest_reading("zone-1", 0.0, "u1", 50)
        log1 = ctrl.event_log
        log2 = ctrl.event_log
        assert log1 is not log2
        assert log1 == log2

    def test_get_user_returns_none_for_unknown(self):
        """
        Ensure attempts to retrieve a user unknown to the system yields None for error-checking
        """
        ctrl = self._create_controller()
        assert ctrl.get_user("nonexistent") is None
