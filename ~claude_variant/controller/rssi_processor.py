"""
Starlight System - RSSI Processor
===================================

Manages per-(UUID, receiver) Kalman filtering and rolling-average computation.
This module is the bridge between raw RSSI measurements from BLE receivers and
the smoothed signal values consumed by the UserTracker for zone decisions.

Design notes
------------
- A separate KalmanFilter is maintained for every (UUID, receiver_id) pair so
  that the filter state for one advertiser on one receiver never affects another.
- A fixed-length deque (the rolling window) holds the last N *filtered* samples.
  The rolling average is computed on demand rather than stored to keep the state
  minimal and avoid drift from stale cached values.
- Thread-safety is the responsibility of the caller (Controller / UserTracker
  both use their own locks before calling into this module).
"""

import logging
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

from controller.kalman_filter import KalmanFilter

logger = logging.getLogger(__name__)

# Type alias used internally for (uuid, receiver_id) dictionary keys.
_PairKey = Tuple[str, str]


class RSSIProcessor:
    """
    Tracks Kalman-filtered and rolling-averaged RSSI for each (UUID, receiver) pair.

    Attributes:
        process_noise (float): Kalman Q parameter forwarded to each filter.
        measurement_noise (float): Kalman R parameter forwarded to each filter.
        window_size (int): Rolling average window depth (number of samples).
    """

    def __init__(
        self,
        process_noise: float,
        measurement_noise: float,
        window_size: int,
    ):
        """
        Initialise the RSSI processor.

        Args:
            process_noise: Kalman filter Q (non-negative).
            measurement_noise: Kalman filter R (positive).
            window_size: Rolling average window size. Must be >= 1.

        Raises:
            ValueError: If window_size < 1, or if the Kalman parameters are invalid
                (validation is delegated to KalmanFilter).
        """
        if window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {window_size}")

        # Validate Kalman parameters early by constructing a throw-away filter.
        # This surfaces bad parameters at construction time rather than later.
        KalmanFilter(process_noise, measurement_noise)

        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.window_size = window_size

        # One KalmanFilter per (uuid, receiver_id) pair.
        self._filters: Dict[_PairKey, KalmanFilter] = {}

        # Rolling window of *filtered* RSSI samples per pair.
        # deque(maxlen=N) automatically discards oldest values when full.
        self._windows: Dict[_PairKey, deque] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def ingest(self, uuid: str, receiver_id: str, raw_rssi: float) -> float:
        """
        Process a raw RSSI reading: filter it, append to the rolling window,
        and return the current rolling average.

        Args:
            uuid: BLE advertiser UUID identifying the user.
            receiver_id: ID of the BLE receiver that captured the advertisement.
            raw_rssi: Raw RSSI value (dBm).

        Returns:
            Current rolling average of filtered RSSI for this (uuid, receiver_id)
            pair after incorporating the new measurement.
        """
        key = (uuid, receiver_id)

        # Create filter and window on first encounter, seeding the filter with
        # the first measurement so it doesn't start from an arbitrary default.
        if key not in self._filters:
            self._filters[key] = KalmanFilter(
                process_noise=self.process_noise,
                measurement_noise=self.measurement_noise,
                initial_estimate=raw_rssi,
            )
            self._windows[key] = deque(maxlen=self.window_size)
            logger.debug(
                "RSSIProcessor: created filter+window for uuid=%s receiver=%s",
                uuid, receiver_id,
            )

        filtered = self._filters[key].update(raw_rssi)
        self._windows[key].append(filtered)

        avg = self._compute_average(key)
        logger.debug(
            "RSSIProcessor: uuid=%s receiver=%s raw=%.1f filtered=%.2f avg=%.2f",
            uuid, receiver_id, raw_rssi, filtered, avg,
        )
        return avg

    def get_average(self, uuid: str, receiver_id: str) -> Optional[float]:
        """
        Return the current rolling average of filtered RSSI for a pair.

        Args:
            uuid: BLE advertiser UUID.
            receiver_id: BLE receiver ID.

        Returns:
            Rolling average as a float, or None if no data exists for this pair.
        """
        key = (uuid, receiver_id)
        if key not in self._windows or not self._windows[key]:
            return None
        return self._compute_average(key)

    def get_all_averages_for_uuid(self, uuid: str) -> Dict[str, Optional[float]]:
        """
        Return the rolling average RSSI for a UUID across all receivers it has
        been heard on.

        Args:
            uuid: BLE advertiser UUID.

        Returns:
            Dict mapping receiver_id -> rolling average (or None).
        """
        result: Dict[str, Optional[float]] = {}
        for (u, r), window in self._windows.items():
            if u == uuid:
                result[r] = self._compute_average((u, r)) if window else None
        return result

    def remove_uuid(self, uuid: str):
        """
        Discard all filter and window state for a given UUID.

        Called when a user is evicted from the queue to prevent stale data from
        influencing a future re-entry.

        Args:
            uuid: BLE advertiser UUID to purge.
        """
        keys_to_remove = [k for k in self._filters if k[0] == uuid]
        for key in keys_to_remove:
            del self._filters[key]
            self._windows.pop(key, None)
        if keys_to_remove:
            logger.debug("RSSIProcessor: removed all state for uuid=%s", uuid)

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_average(self, key: _PairKey) -> float:
        """Compute the mean of the rolling window for a given key."""
        window = self._windows[key]
        return sum(window) / len(window)
