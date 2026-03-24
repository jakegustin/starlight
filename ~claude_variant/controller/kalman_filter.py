"""
Starlight System - 1D Kalman Filter
====================================

Provides a one-dimensional Kalman filter for smoothing noisy RSSI measurements.
Each (UUID, receiver) pair maintains its own independent KalmanFilter instance so
that signal estimates for different users and receivers never cross-contaminate.

Background
----------
RSSI readings in a real environment are highly volatile due to multipath
interference, body shielding, and environmental reflections. A Kalman filter is
well-suited here because it optimally combines a simple motion model (in our
case, "the signal strength is not expected to change much between samples") with
noisy observations, producing a smoother estimate without introducing excessive
lag.

The filter used here assumes a *static* state model, i.e. the predicted state
equals the previous state. This is appropriate for slowly-moving users relative
to the advertisement frequency.
"""

import logging

logger = logging.getLogger(__name__)


class KalmanFilter:
    """
    A one-dimensional Kalman filter for smoothing RSSI measurements.

    Model
    -----
    State transition:  x_k = x_{k-1}          (static — no motion model)
    Observation:       z_k = x_k + noise       (direct RSSI measurement)

    Predict step
    ~~~~~~~~~~~~
    x_k|k-1  = x_{k-1}                         (state prediction)
    P_k|k-1  = P_{k-1} + Q                     (covariance prediction)

    Update step
    ~~~~~~~~~~~
    K        = P_k|k-1 / (P_k|k-1 + R)         (Kalman gain)
    x_k      = x_k|k-1 + K * (z_k - x_k|k-1)  (state update)
    P_k      = (1 - K) * P_k|k-1               (covariance update)

    Attributes:
        process_noise (float): Process noise covariance Q.
        measurement_noise (float): Measurement noise covariance R.
        estimate (float): Current filtered RSSI estimate.
        covariance (float): Current estimate uncertainty.
    """

    def __init__(
        self,
        process_noise: float,
        measurement_noise: float,
        initial_estimate: float = -70.0,
        initial_covariance: float = 1.0,
    ):
        """
        Initialise the Kalman filter.

        Args:
            process_noise: Q parameter. Must be >= 0.
            measurement_noise: R parameter. Must be > 0.
            initial_estimate: Starting RSSI estimate (dBm). Using the first raw
                measurement as the initial estimate is recommended to avoid a
                long convergence tail.
            initial_covariance: Starting uncertainty. Larger values cause the
                filter to weight early measurements more heavily.

        Raises:
            ValueError: If process_noise < 0 or measurement_noise <= 0.
        """
        if process_noise < 0:
            raise ValueError(
                f"process_noise must be non-negative, got {process_noise}"
            )
        if measurement_noise <= 0:
            raise ValueError(
                f"measurement_noise must be positive, got {measurement_noise}"
            )

        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.estimate = initial_estimate
        self.covariance = initial_covariance

        logger.debug(
            "KalmanFilter created: Q=%.4f R=%.4f x0=%.2f P0=%.4f",
            process_noise, measurement_noise, initial_estimate, initial_covariance,
        )

    def update(self, measurement: float) -> float:
        """
        Run one predict-update cycle of the Kalman filter.

        Args:
            measurement: New raw RSSI reading (dBm).

        Returns:
            Updated filtered RSSI estimate (dBm).
        """
        # ── Prediction ────────────────────────────────────────────────────────
        # x_k|k-1 = x_{k-1}  (static model: no expected change)
        predicted_estimate = self.estimate

        # P_k|k-1 = P_{k-1} + Q
        predicted_covariance = self.covariance + self.process_noise

        # ── Update ────────────────────────────────────────────────────────────
        # Kalman gain: how much to trust the new measurement vs. our prediction
        kalman_gain = predicted_covariance / (predicted_covariance + self.measurement_noise)

        # Blend prediction with observation
        self.estimate = predicted_estimate + kalman_gain * (measurement - predicted_estimate)

        # Shrink covariance — we are now more certain about the state
        self.covariance = (1.0 - kalman_gain) * predicted_covariance

        logger.debug(
            "KalmanFilter update: z=%.2f → x=%.2f (K=%.4f P=%.4f)",
            measurement, self.estimate, kalman_gain, self.covariance,
        )
        return self.estimate

    def reset(self, initial_estimate: float = -70.0, initial_covariance: float = 1.0):
        """
        Reset the filter to a fresh initial state.

        Useful when a user re-enters the system after eviction and a stale
        filter from a previous session should not influence the new estimate.

        Args:
            initial_estimate: New starting estimate (dBm).
            initial_covariance: New starting uncertainty.
        """
        self.estimate = initial_estimate
        self.covariance = initial_covariance
        logger.debug("KalmanFilter reset: x0=%.2f P0=%.4f", initial_estimate, initial_covariance)
