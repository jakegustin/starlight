"""
Implementation of a 1-D Kalman Filter for the Starlight controller
"""
from __future__ import annotations

class KalmanFilter:
    """
    A 1-dimensional Kalman filter to smooth out perceived RSSI values.
    It is lazily initialized on the first call to `update`

    Attributes
    ----------
    q : float
        Aka process noise: how much to trust new data. Lower = trust existing values more.
    r : float
        Aka measurement noise: filter's sensitivity for outlier detection. Lower = more outliers
    x : float
        Aka current state estimate, holding the smooth RSSI value
    p : float
        Aka covariance, or uncertainty of `x`. Higher values suggest less confidence
    _initialized : bool
        Indicates whether the KalmanFilter is ready for operation
    """

    def __init__(
        self,
        process_noise: float = 1.0,
        measurement_noise: float = 10.0,
        initial_estimate: float = 0.0,
        initial_error: float = 100.0,
    ) -> None:
        self.q = process_noise
        self.r = measurement_noise
        self.x = initial_estimate
        self.p = initial_error
        self._initialized = False

    def predict(self) -> tuple[float, float]:
        """
        Provides the current state and the uncertainty tied to that state
        For uncertainty, utilize covariance extrapolation equation to compute estimate variance
        """
        # Covariance extrapolation formula derived from https://kalmanfilter.net/kalman1d_pn.html
        return self.x, self.p + self.q

    def update(self, measurement: float) -> float:
        """
        Update the state to reflect the new measurement
        """
        if not self._initialized:
            self.x = measurement
            self._initialized = True
            return self.x

        x_pred, p_pred = self.predict()

        # Formulas derived from https://kalmanfilter.net/kalman1d_pn.html
        # Compute Kalman Gain from process noise & uncertainty, along with measurement variance
        k = p_pred / (p_pred + self.r)

        # Compute new system state from existing system state, Kalman Gain, and the measured state
        self.x = x_pred + k * (measurement - x_pred)

        # Compute the new covariance from the Kalman Gain and the existing estimate variance
        self.p = (1.0 - k) * p_pred

        return self.x

    def is_outlier(self, measurement: float, gate: float = 3.0) -> bool:
        """
        A simpler method to determine if the measurement is an outlier by `gate` standard deviations
        """
        # First measurement is never considered an outlier
        if not self._initialized:
            return False

        x_pred, p_pred = self.predict()

        # Get standard deviation of one entry of combined process/measurement noise and covariance
        std = (p_pred + self.r) ** 0.5

        # Determine if the measurement exceeds our outlier threshold
        return abs(measurement - x_pred) / std > gate

    def reset(self) -> None:
        """
        Resets the filter to its original, uninitialized state
        """
        self._initialized = False
        self.x = 0.0
        self.p = 100.0

    @property
    def initialized(self) -> bool:
        """
        Indicates if the KalmanFilter instance is initialized or not
        """
        return self._initialized
