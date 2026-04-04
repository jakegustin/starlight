"""
Unit tests for controller.user_tracker.UserTracker

Because UserTracker.process_rssi() does time-based eviction, tests that
exercise the eviction path use very short timeout durations (0.1 s) and
a small sleep to let the timer elapse.
"""

import time
import pytest

from controller.rssi_processor import RSSIProcessor
from controller.user_tracker import UserTracker
from controller.zone_manager import ZoneManager


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tracker(
    hysteresis: float = 3.0,
    threshold: float = -85.0,
    timeout: float = 10.0,
    receivers: list = None,
):
    """
    Build a UserTracker backed by a near-passthrough RSSIProcessor (very low
    Kalman noise) so that test assertions can reason about raw RSSI values.
    """
    if receivers is None:
        receivers = ["rec-A", "rec-B", "rec-C"]

    proc = RSSIProcessor(process_noise=0.0, measurement_noise=0.0001, window_size=1)
    zm = ZoneManager()
    for r in receivers:
        zm.register_receiver(r)

    tracker = UserTracker(
        rssi_processor=proc,
        zone_manager=zm,
        hysteresis=hysteresis,
        rssi_timeout_threshold=threshold,
        rssi_timeout_duration=timeout,
    )
    return tracker, proc, zm


def _feed(tracker, uuid, receiver, rssi, count=1):
    """Convenience: send *count* identical RSSI observations."""
    for _ in range(count):
        tracker.process_rssi(uuid, receiver, rssi)


# ── Zone assignment ───────────────────────────────────────────────────────────

class TestUserTrackerZoneAssignment:
    def test_new_user_assigned_to_first_zone(self):
        tracker, _, _ = _make_tracker()
        _feed(tracker, "uuid-1", "rec-A", -65.0)
        assert tracker.get_all_users()["uuid-1"] == "rec-A"

    def test_new_user_seen_first_on_second_receiver_goes_to_zone_0(self):
        """First data from rec-B should still land user at zone 0 (rec-A)."""
        tracker, _, _ = _make_tracker()
        _feed(tracker, "uuid-1", "rec-B", -65.0)
        assert tracker.get_all_users()["uuid-1"] == "rec-A"

    def test_no_zones_registered_does_not_crash(self):
        tracker, _, _ = _make_tracker(receivers=[])
        tracker.process_rssi("uuid-1", "rec-X", -65.0)  # Should not raise
        # User exists but has no zone (no zones available)
        users = tracker.get_all_users()
        assert users.get("uuid-1") is None

    def test_multiple_users_assigned_independently(self):
        tracker, _, _ = _make_tracker()
        _feed(tracker, "uuid-1", "rec-A", -65.0)
        _feed(tracker, "uuid-2", "rec-A", -70.0)
        users = tracker.get_all_users()
        assert "uuid-1" in users
        assert "uuid-2" in users


# ── Zone advancement ──────────────────────────────────────────────────────────

class TestUserTrackerAdvancement:
    def test_user_advances_when_next_zone_clearly_stronger(self):
        tracker, _, _ = _make_tracker(hysteresis=3.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        # rec-B is 10 dBm stronger → well above hysteresis
        _feed(tracker, "uuid-1", "rec-B", -55.0, count=5)
        assert tracker.get_all_users()["uuid-1"] == "rec-B"

    def test_user_does_not_advance_below_hysteresis(self):
        tracker, _, _ = _make_tracker(hysteresis=10.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        # rec-B only 4 dBm stronger — not enough for 10 dBm hysteresis
        _feed(tracker, "uuid-1", "rec-B", -61.0, count=5)
        assert tracker.get_all_users()["uuid-1"] == "rec-A"

    def test_user_advances_through_multiple_zones(self):
        tracker, _, _ = _make_tracker(hysteresis=3.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-1", "rec-B", -55.0, count=10)
        _feed(tracker, "uuid-1", "rec-C", -45.0, count=10)
        assert tracker.get_all_users()["uuid-1"] == "rec-C"

    def test_user_at_last_zone_does_not_advance(self):
        tracker, _, _ = _make_tracker(hysteresis=3.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-1", "rec-B", -55.0, count=10)
        _feed(tracker, "uuid-1", "rec-C", -45.0, count=10)
        # Confirm at final zone, then throw more data at it — should stay
        _feed(tracker, "uuid-1", "rec-C", -30.0, count=10)
        assert tracker.get_all_users()["uuid-1"] == "rec-C"

    def test_monotonic_no_backward_movement(self):
        """
        Once a user has advanced to rec-B, a stronger rec-A signal must not
        move them back. The user can only move forward (to rec-C).
        """
        tracker, _, _ = _make_tracker(hysteresis=3.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-1", "rec-B", -55.0, count=10)
        assert tracker.get_all_users()["uuid-1"] == "rec-B"

        # Simulate very strong rec-A signal — no backward movement allowed
        _feed(tracker, "uuid-1", "rec-A", -30.0, count=10)
        zone = tracker.get_all_users()["uuid-1"]
        assert zone in ("rec-B", "rec-C")  # Forward only

    def test_insufficient_data_prevents_advancement(self):
        """Without data at the next zone, advancement should not occur."""
        tracker, _, _ = _make_tracker(hysteresis=3.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        # No rec-B data — user should stay at rec-A
        assert tracker.get_all_users()["uuid-1"] == "rec-A"


# ── Eviction / timeout ────────────────────────────────────────────────────────

class TestUserTrackerEviction:
    def test_user_evicted_after_timeout(self):
        tracker, _, _ = _make_tracker(threshold=-70.0, timeout=0.05)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        assert "uuid-1" in tracker.get_all_users()

        # Drive RSSI below threshold and wait for timer to expire
        _feed(tracker, "uuid-1", "rec-A", -90.0, count=3)
        time.sleep(0.15)
        _feed(tracker, "uuid-1", "rec-A", -90.0)  # Trigger the eviction check

        assert "uuid-1" not in tracker.get_all_users()

    def test_user_not_evicted_before_timeout_elapses(self):
        tracker, _, _ = _make_tracker(threshold=-70.0, timeout=5.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-1", "rec-A", -90.0, count=3)
        # Timer has started but 5 s have not passed
        assert "uuid-1" in tracker.get_all_users()

    def test_user_not_evicted_if_rssi_recovers(self):
        tracker, _, _ = _make_tracker(threshold=-70.0, timeout=0.2)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        # Brief dip below threshold
        _feed(tracker, "uuid-1", "rec-A", -90.0, count=2)
        # Recovery before timeout
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        time.sleep(0.25)  # Wait past timeout — but RSSI has recovered
        _feed(tracker, "uuid-1", "rec-A", -65.0)
        assert "uuid-1" in tracker.get_all_users()

    def test_eviction_clears_rssi_state(self):
        """After eviction, get_average for the evicted UUID should return None."""
        tracker, proc, _ = _make_tracker(threshold=-70.0, timeout=0.05)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-1", "rec-A", -90.0, count=3)
        time.sleep(0.15)
        _feed(tracker, "uuid-1", "rec-A", -90.0)
        assert proc.get_average("uuid-1", "rec-A") is None

    def test_eviction_of_one_user_does_not_affect_another(self):
        tracker, _, _ = _make_tracker(threshold=-70.0, timeout=0.05)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-2", "rec-A", -65.0, count=5)

        _feed(tracker, "uuid-1", "rec-A", -90.0, count=3)
        time.sleep(0.15)
        _feed(tracker, "uuid-1", "rec-A", -90.0)

        assert "uuid-1" not in tracker.get_all_users()
        assert "uuid-2" in tracker.get_all_users()


# ── Query methods ─────────────────────────────────────────────────────────────

class TestUserTrackerQueries:
    def test_get_users_by_zone_groups_correctly(self):
        tracker, _, _ = _make_tracker(hysteresis=3.0)
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        _feed(tracker, "uuid-2", "rec-A", -65.0, count=5)
        by_zone = tracker.get_users_by_zone()
        assert "uuid-1" in by_zone.get("rec-A", [])
        assert "uuid-2" in by_zone.get("rec-A", [])

    def test_remove_user_manual(self):
        tracker, _, _ = _make_tracker()
        _feed(tracker, "uuid-1", "rec-A", -65.0, count=5)
        tracker.remove_user("uuid-1")
        assert "uuid-1" not in tracker.get_all_users()

    def test_remove_nonexistent_user_no_error(self):
        tracker, _, _ = _make_tracker()
        tracker.remove_user("nobody")  # Must not raise
