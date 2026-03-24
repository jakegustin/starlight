"""
Unit tests for controller.rssi_processor.RSSIProcessor
"""

import pytest
from controller.rssi_processor import RSSIProcessor


# ── Construction ──────────────────────────────────────────────────────────────

class TestRSSIProcessorConstruction:
    def test_valid_construction(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        assert proc.window_size == 5

    def test_window_size_zero_raises(self):
        with pytest.raises(ValueError, match="window_size"):
            RSSIProcessor(0.01, 2.0, window_size=0)

    def test_window_size_negative_raises(self):
        with pytest.raises(ValueError, match="window_size"):
            RSSIProcessor(0.01, 2.0, window_size=-3)

    def test_window_size_one_is_valid(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=1)
        assert proc.window_size == 1

    def test_invalid_kalman_params_raise(self):
        """Invalid Kalman parameters should raise at construction time."""
        with pytest.raises(ValueError):
            RSSIProcessor(-0.1, 2.0, window_size=5)
        with pytest.raises(ValueError):
            RSSIProcessor(0.01, 0.0, window_size=5)


# ── Ingest and averaging ──────────────────────────────────────────────────────

class TestRSSIProcessorIngest:
    def test_ingest_returns_float(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        result = proc.ingest("uuid-1", "rec-A", -65.0)
        assert isinstance(result, float)

    def test_single_ingest_returns_same_as_get_average(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        result = proc.ingest("uuid-1", "rec-A", -65.0)
        avg = proc.get_average("uuid-1", "rec-A")
        assert result == avg

    def test_rolling_window_respects_max_size(self):
        """
        Window of size 3 should drop old samples.  We verify the average AFTER
        only feeding -60 values is higher (closer to -60) than after only
        feeding -90 values, proving the window evicted the old data.

        Note: we use a realistic Q>0 here so the Kalman gain doesn't collapse
        to zero (Q=0 causes gain → 0 after the first update, starving future
        samples of influence).
        """
        proc = RSSIProcessor(0.1, 1.0, window_size=3)
        for _ in range(10):
            proc.ingest("uuid-1", "rec-A", -90.0)
        avg_low = proc.get_average("uuid-1", "rec-A")

        for _ in range(10):
            proc.ingest("uuid-1", "rec-A", -60.0)
        avg_high = proc.get_average("uuid-1", "rec-A")

        # After shifting to -60 readings, the window average must rise.
        assert avg_high > avg_low

    def test_different_uuids_are_isolated(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.ingest("uuid-1", "rec-A", -65.0)
        proc.ingest("uuid-2", "rec-A", -80.0)
        assert proc.get_average("uuid-1", "rec-A") != proc.get_average("uuid-2", "rec-A")

    def test_different_receivers_are_isolated(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.ingest("uuid-1", "rec-A", -65.0)
        proc.ingest("uuid-1", "rec-B", -80.0)
        avg_a = proc.get_average("uuid-1", "rec-A")
        avg_b = proc.get_average("uuid-1", "rec-B")
        assert avg_a != avg_b

    def test_stable_measurement_converges_near_value(self):
        """After many identical readings the average should track the signal."""
        proc = RSSIProcessor(0.01, 2.0, window_size=10)
        for _ in range(20):
            proc.ingest("uuid-1", "rec-A", -70.0)
        avg = proc.get_average("uuid-1", "rec-A")
        assert abs(avg - (-70.0)) < 2.0


# ── get_average ───────────────────────────────────────────────────────────────

class TestRSSIProcessorGetAverage:
    def test_returns_none_for_unknown_uuid(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        assert proc.get_average("ghost-uuid", "rec-A") is None

    def test_returns_none_for_unknown_receiver(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.ingest("uuid-1", "rec-A", -65.0)
        assert proc.get_average("uuid-1", "rec-B") is None


# ── get_all_averages_for_uuid ─────────────────────────────────────────────────

class TestRSSIProcessorGetAll:
    def test_returns_all_receivers_for_uuid(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.ingest("uuid-1", "rec-A", -65.0)
        proc.ingest("uuid-1", "rec-B", -75.0)
        all_avgs = proc.get_all_averages_for_uuid("uuid-1")
        assert "rec-A" in all_avgs
        assert "rec-B" in all_avgs

    def test_returns_empty_dict_for_unknown_uuid(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        assert proc.get_all_averages_for_uuid("nobody") == {}


# ── remove_uuid ───────────────────────────────────────────────────────────────

class TestRSSIProcessorRemove:
    def test_remove_clears_all_receiver_state(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.ingest("uuid-1", "rec-A", -65.0)
        proc.ingest("uuid-1", "rec-B", -70.0)
        proc.remove_uuid("uuid-1")
        assert proc.get_average("uuid-1", "rec-A") is None
        assert proc.get_average("uuid-1", "rec-B") is None

    def test_remove_nonexistent_uuid_no_error(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.remove_uuid("does-not-exist")  # Must not raise

    def test_remove_does_not_affect_other_uuids(self):
        proc = RSSIProcessor(0.01, 2.0, window_size=5)
        proc.ingest("uuid-1", "rec-A", -65.0)
        proc.ingest("uuid-2", "rec-A", -70.0)
        proc.remove_uuid("uuid-1")
        assert proc.get_average("uuid-2", "rec-A") is not None
