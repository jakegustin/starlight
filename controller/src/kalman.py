from __future__ import annotations


class KalmanFilter:
    """
    A 1-dimensional Kalman filter to smooth out perceived RSSI values.
    It is lazily initialized on the first call to `update`

    Attributes
    ----------
    q : float
        Also known as process noise, or how much to trust new data over existing data. Lower = trust existing values more.
    r : float
        Also known as measurement noise, or how sensitive the filter is before assuming an outlier. Lower = more likely to deem as outlier
    x : float
        Also known as the current state estimate, holding the smooth RSSI value
    p : float
        Also known as the covariance, or uncertainty of `x`. Higher values suggest less confidence
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
        """
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
        k = p_pred / (p_pred + self.r)
        self.x = x_pred + k * (measurement - x_pred)
        self.p = (1.0 - k) * p_pred
        return self.x

    def is_outlier(self, measurement: float, gate: float = 3.0) -> bool:
        """
        Determine if the measurement is an outlier by `gate` standard deviations. First measurement is never an outlier
        """
        if not self._initialized:
            return False
        x_pred, p_pred = self.predict()
        std = (p_pred + self.r) ** 0.5
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
