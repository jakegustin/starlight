from __future__ import annotations

import pytest

from controller import KalmanFilter


class TestKalmanFilter:
    """Unit tests for the one-dimensional Kalman filter."""

    def test_first_measurement_initializes(self):
        """
        Ensure the first measurement passed into the KalmanFilter initializes the filter
        """
        kf = KalmanFilter()
        est = kf.update(-50.0)
        assert est == -50.0
        assert kf.initialized is True

    def test_not_initialized_before_first_update(self):
        """
        Ensure that a KalmanFilter with no updates remains uninitialized
        """
        kf = KalmanFilter()
        assert kf.initialized is False

    def test_converges_to_stable_signal(self):
        """
        Ensure that the KalmanFilter with identical updates/inputs produces a stable signal
        """
        kf = KalmanFilter(process_noise=0.5, measurement_noise=5.0)
        for _ in range(50):
            kf.update(-60.0)
        assert abs(kf.x - (-60.0)) < 0.5

    def test_tracks_changing_signal(self):
        """
        Ensure the KalmanFilter properly tracks changes in the readings and is not a simple average
        """
        kf = KalmanFilter(process_noise=2.0, measurement_noise=5.0)
        for _ in range(20):
            kf.update(40.0)
        for _ in range(40):
            kf.update(80.0)
        assert kf.x > 70.0

    def test_detects_outlier(self):
        """
        Verify that outlier values are appropriately detected
        """
        kf = KalmanFilter(process_noise=0.5, measurement_noise=5.0)
        for _ in range(20):
            kf.update(-60.0)
        assert kf.is_outlier(-10.0, gate=2.0) is True

    def test_does_not_flag_close_value(self):
        """
        Verify that values near the average are not deemed outliers
        """
        kf = KalmanFilter(process_noise=0.5, measurement_noise=5.0)
        for _ in range(20):
            kf.update(-60.0)
        assert kf.is_outlier(-59.0, gate=3.0) is False

    def test_first_measurement_never_outlier(self):
        """
        Edge case: Ensure the first value is never an outlier
        """
        kf = KalmanFilter()
        assert kf.is_outlier(999.0) is False

    def test_reset(self):
        """
        Verify that the reset functionality of the KalmanFilter works as intended
        """
        kf = KalmanFilter()
        kf.update(-50.0)
        assert kf.initialized is True
        kf.reset()
        assert kf.initialized is False

    def test_predict_returns_state_and_covariance(self):
        """
        Confirm the initial state matches the sole value and the predicted error is computed correctly
        """
        kf = KalmanFilter(process_noise=2.0)
        kf.update(10.0)
        x_pred, p_pred = kf.predict()
        assert x_pred == 10.0
        assert p_pred == kf.p + kf.q
