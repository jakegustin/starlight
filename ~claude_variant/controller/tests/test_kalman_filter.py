"""
Unit tests for controller.kalman_filter.KalmanFilter
"""

import pytest
from controller.kalman_filter import KalmanFilter


# ── Construction ──────────────────────────────────────────────────────────────

class TestKalmanFilterConstruction:
    def test_valid_parameters_no_error(self):
        kf = KalmanFilter(process_noise=0.01, measurement_noise=2.0)
        assert kf.process_noise == 0.01
        assert kf.measurement_noise == 2.0

    def test_zero_process_noise_is_valid(self):
        """Q=0 means perfectly static model — allowed."""
        kf = KalmanFilter(process_noise=0.0, measurement_noise=1.0)
        assert kf.process_noise == 0.0

    def test_negative_process_noise_raises(self):
        with pytest.raises(ValueError, match="process_noise"):
            KalmanFilter(process_noise=-0.1, measurement_noise=2.0)

    def test_zero_measurement_noise_raises(self):
        """R=0 would cause division by zero in the gain formula."""
        with pytest.raises(ValueError, match="measurement_noise"):
            KalmanFilter(process_noise=0.01, measurement_noise=0.0)

    def test_negative_measurement_noise_raises(self):
        with pytest.raises(ValueError, match="measurement_noise"):
            KalmanFilter(process_noise=0.01, measurement_noise=-1.0)

    def test_custom_initial_estimate_stored(self):
        kf = KalmanFilter(0.01, 2.0, initial_estimate=-55.0)
        assert kf.estimate == -55.0

    def test_custom_initial_covariance_stored(self):
        kf = KalmanFilter(0.01, 2.0, initial_covariance=5.0)
        assert kf.covariance == 5.0


# ── Update behaviour ──────────────────────────────────────────────────────────

class TestKalmanFilterUpdate:
    def test_update_returns_float(self):
        kf = KalmanFilter(0.01, 2.0)
        result = kf.update(-65.0)
        assert isinstance(result, float)

    def test_single_update_moves_estimate_toward_measurement(self):
        kf = KalmanFilter(0.01, 1.0, initial_estimate=-70.0)
        result = kf.update(-50.0)
        # Must be between start and measurement
        assert -70.0 < result < -50.0

    def test_repeated_identical_measurements_converge(self):
        kf = KalmanFilter(0.1, 1.0, initial_estimate=-70.0)
        target = -60.0
        for _ in range(60):
            kf.update(target)
        assert abs(kf.estimate - target) < 0.5

    def test_high_measurement_noise_conserves_estimate(self):
        """Higher R → smaller Kalman gain → estimate moves less per update."""
        kf_low = KalmanFilter(0.01, 0.1, initial_estimate=-70.0)
        kf_high = KalmanFilter(0.01, 20.0, initial_estimate=-70.0)
        measurement = -40.0
        result_low = kf_low.update(measurement)
        result_high = kf_high.update(measurement)
        # Both move toward -40, but high-R moves less (stays closer to -70)
        assert result_high < result_low

    def test_covariance_decreases_after_updates(self):
        """Estimate uncertainty should fall as more observations arrive."""
        kf = KalmanFilter(0.01, 2.0, initial_covariance=5.0)
        for _ in range(15):
            kf.update(-65.0)
        assert kf.covariance < 5.0

    def test_covariance_stays_positive(self):
        """Covariance must never go negative."""
        kf = KalmanFilter(0.0, 1.0, initial_covariance=1.0)
        for _ in range(100):
            kf.update(-65.0)
        assert kf.covariance >= 0.0

    def test_estimate_tracks_step_change(self):
        """Filter should eventually follow a sustained step change in signal."""
        kf = KalmanFilter(0.1, 1.0, initial_estimate=-70.0)
        for _ in range(30):
            kf.update(-70.0)
        for _ in range(60):
            kf.update(-50.0)
        assert abs(kf.estimate - (-50.0)) < 1.0


# ── Reset ─────────────────────────────────────────────────────────────────────

class TestKalmanFilterReset:
    def test_reset_restores_estimate(self):
        kf = KalmanFilter(0.01, 2.0, initial_estimate=-70.0)
        for _ in range(20):
            kf.update(-50.0)
        kf.reset(initial_estimate=-70.0)
        assert kf.estimate == -70.0

    def test_reset_restores_covariance(self):
        kf = KalmanFilter(0.01, 2.0, initial_covariance=1.0)
        for _ in range(20):
            kf.update(-65.0)
        kf.reset(initial_covariance=1.0)
        assert kf.covariance == 1.0

    def test_filter_works_normally_after_reset(self):
        """A filter should behave identically after a reset as after a fresh init."""
        fresh = KalmanFilter(0.05, 1.0, initial_estimate=-70.0)
        reused = KalmanFilter(0.05, 1.0, initial_estimate=-50.0)
        reused.reset(initial_estimate=-70.0)
        measurement = -60.0
        assert fresh.update(measurement) == pytest.approx(reused.update(measurement))
