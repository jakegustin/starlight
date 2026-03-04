from __future__ import annotations

from .kalman import KalmanFilter


class RSSIBuffer:
    """
    Rolling window of RSSI samples that passed Kalman outlier rejection.
    
    Attributes
    ----------
    window_size : int
        The size of the buffer with respect to number of entries stored
    gate_threshold : float
        The number of standard deviations before considering a sample an outlier
    _values : list[float]
        The list of RSSI values in the buffer
    _kalman : KalmanFilter
        The KalmanFilter instance associated with the buffer
    """

    def __init__(
        self,
        window_size: int = 10,
        process_noise: float = 1.0,
        measurement_noise: float = 10.0,
        gate_threshold: float = 3.0,
    ) -> None:
        """
        Creates a RSSIBuffer instance

        Parameters
        ----------
        window_size : Optional[int]
            The size of the buffer with respect to number of entries stored
        process_noise : Optional[float]
            For Kalman Filter: formally Q, dictates how much to trust new data vs the existing state
        measurement_noise : Optional[float]
            For Kalman Filter: formally R, dictates how frequently measurements are filtered out
        gate_threshold : Optional[float]
            The number of standard deviations before considering a sample an outlier
        """
        self.window_size = window_size
        self.gate_threshold = gate_threshold
        self._values: list[float] = []
        self._kalman = KalmanFilter(
            process_noise=process_noise,
            measurement_noise=measurement_noise,
        )

    def add(self, rssi: float) -> bool:
        """
        Attempt to add the RSSI value to the buffer, returning False if an outlier and rejected
        """
        outlier = self._kalman.is_outlier(rssi, self.gate_threshold)
        self._kalman.update(rssi)  # We need to always advance the filter!

        if outlier:
            return False

        self._values.append(rssi)
        if len(self._values) > self.window_size:
            self._values.pop(0)
        return True

    @property # This little sucker converts the method into an attribute, how cool!
    def average(self) -> float | None:
        """
        Provides the average (mean) of the values in the buffer
        """
        if not self._values:
            return None
        return sum(self._values) / len(self._values)

    @property
    def count(self) -> int:
        """
        Provides the number of values in the buffer
        """
        return len(self._values)

    @property
    def kalman_estimate(self) -> float:
        """
        Provides the initial estimate of the associated KalmanFilter instance
        """
        return self._kalman.x

    def clear(self) -> None:
        """
        Clears out the values in the buffer and removes the KalmanFilter instance
        """
        self._values.clear()
        self._kalman.reset()
