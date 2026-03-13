"""
Unit tests for the RSSIBuffer class of the Starlight controller
"""
from __future__ import annotations

import pytest

from controller import RSSIBuffer


class TestRSSIBuffer:
    """Unit tests for the Kalman-filtered rolling RSSI buffer."""

    def test_add_returns_true_when_accepted(self):
        """
        Ensure a normal buffer addition gets accepted
        """
        buf = RSSIBuffer(window_size=5)
        assert buf.add(50.0) is True

    def test_average_of_single_value(self):
        """
        Ensure the average value of a buffer with a single value is the value itself
        """
        buf = RSSIBuffer(window_size=5)
        buf.add(42.0)
        assert buf.average == pytest.approx(42.0)

    def test_average_of_multiple_values(self):
        """
        Ensure the average value of a buffer with multiple values is correct
        """
        buf = RSSIBuffer(window_size=5)
        for v in [40, 42, 38, 41, 39]: # sum=200, 200/5=40
            buf.add(v)
        assert buf.average == pytest.approx(40.0)

    def test_rolling_window_evicts_oldest(self):
        """
        Ensure only the most recent window_size elements are in the buffer
        """
        buf = RSSIBuffer(window_size=3, gate_threshold=100.0)
        for v in [10, 20, 30, 40, 50]: # 10, 20 are the oldest and should be evicted
            buf.add(v)
        assert buf.count == 3
        # Only the last 3 accepted values remain. sum=120, 120/3=40
        assert buf.average == pytest.approx(40.0)

    def test_rejects_outlier(self):
        """
        Ensure outlier values are not added to the buffer
        """
        buf = RSSIBuffer(
            window_size=10,
            process_noise=0.5,
            measurement_noise=2.0,
            gate_threshold=2.0,
        )
        for _ in range(15):
            buf.add(50.0)
        count_before = buf.count
        accepted = buf.add(200.0)
        assert accepted is False
        assert buf.count == count_before

    def test_empty_average_is_none(self):
        """
        Edge case: Ensure an empty buffer has "None" average to avoid division by zero
        """
        buf = RSSIBuffer()
        assert buf.average is None

    def test_count_starts_at_zero(self):
        """
        Ensure the buffer starts with an element count of 0
        """
        buf = RSSIBuffer()
        assert buf.count == 0

    def test_clear(self):
        """
        Ensure clearing the buffer resets the appropriate attributes
        """
        buf = RSSIBuffer()
        buf.add(50.0)
        buf.clear()
        assert buf.count == 0
        assert buf.average is None

    def test_kalman_estimate_tracks_input(self):
        """
        Ensure the KalmanFilter's current state/estimate is in line with added values
        """
        buf = RSSIBuffer(window_size=5, gate_threshold=100.0)
        for _ in range(20):
            buf.add(75.0)
        assert abs(buf.kalman_estimate - 75.0) < 1.0
