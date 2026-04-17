"""
This module is the bridge between raw RSSI measurements from BLE receivers and
the smoothed signal values consumed by the UserTracker for zone decisions.
"""

import logging
import threading
from dataclasses import dataclass
from collections import deque
from typing import Dict, Optional, Tuple

from controller.kalman_filter import KalmanFilter

logger = logging.getLogger(__name__)

# Custom type used for (uuid, receiver_id) dictionary keys.
_PairKey = Tuple[str, str]


@dataclass(frozen=True)
class RSSISample:
    """Snapshot of one RSSI observation and its processed values."""

    uuid: str
    receiver_id: str
    raw_rssi: float
    filtered_rssi: float
    average_rssi: float
    raw_mode: bool


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
        raw_mode: bool = False,
    ):
        """
        Initialise the RSSI processor.

        Args:
            process_noise: Kalman filter Q (non-negative).
            measurement_noise: Kalman filter R (positive).
            window_size: Rolling average window size. Must be >= 1.
            raw_mode: When True, skip filtering entirely and use raw RSSI values directly.
        """
        # Make sure the window size is valid!
        if window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {window_size}")

        # Validate Kalman parameters via a throwaway filter.
        # The KalmanFilter instance will raise errors if there is an issue
        KalmanFilter(process_noise, measurement_noise)

        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.window_size = window_size
        self.raw_mode = raw_mode

        self._lock = threading.Lock()

        # Set up one KalmanFilter per (uuid, receiver_id) pair.
        self._filters: Dict[_PairKey, KalmanFilter] = {}

        # Rolling window of filtered RSSI samples per pair.
        self._windows: Dict[_PairKey, deque] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def ingest(self, uuid: str, receiver_id: str, raw_rssi: float) -> float:
        """
        Process a raw RSSI reading: filter it, append to the window, and return the current rolling average.

        Args:
            uuid: BLE advertiser UUID identifying the user.
            receiver_id: ID of the BLE receiver that captured the advertisement.
            raw_rssi: Raw RSSI value (dBm).
        """
        return self.observe(uuid, receiver_id, raw_rssi).average_rssi

    def observe(self, uuid: str, receiver_id: str, raw_rssi: float) -> RSSISample:
        """
        Process a raw RSSI reading and return its raw, filtered, and averaged values.

        Args:
            uuid: BLE advertiser UUID identifying the user.
            receiver_id: ID of the BLE receiver that captured the advertisement.
            raw_rssi: Raw RSSI value (dBm).
        """
        key = (uuid, receiver_id)

        with self._lock:
            # In raw mode, bypass Kalman filtering and rolling averaging entirely.
            if self.raw_mode:
                if key not in self._windows:
                    self._windows[key] = deque(maxlen=1)
                    logger.debug(
                        "RSSIProcessor: created raw window for uuid=%s receiver=%s",
                        uuid, receiver_id,
                    )
                self._windows[key].append(raw_rssi)
                logger.debug(
                    "RSSIProcessor: uuid=%s receiver=%s raw=%.1f (raw mode)",
                    uuid, receiver_id, raw_rssi,
                )
                return RSSISample(
                    uuid=uuid,
                    receiver_id=receiver_id,
                    raw_rssi=raw_rssi,
                    filtered_rssi=raw_rssi,
                    average_rssi=raw_rssi,
                    raw_mode=True,
                )

            # Create filter/window on first reading, configuring the filter appropriately.
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

            # Retrieve the filtered reading and add it to the current UUID/Receiver window
            filtered = self._filters[key].update(raw_rssi)
            self._windows[key].append(filtered)

            # Compute and return the average value of the filtered values for the UUID/Receiver pair
            avg = self._compute_average(key)
            logger.debug(
                "RSSIProcessor: uuid=%s receiver=%s raw=%.1f filtered=%.2f avg=%.2f",
                uuid, receiver_id, raw_rssi, filtered, avg,
            )
            return RSSISample(
                uuid=uuid,
                receiver_id=receiver_id,
                raw_rssi=raw_rssi,
                filtered_rssi=filtered,
                average_rssi=avg,
                raw_mode=False,
            )

    def get_average(self, uuid: str, receiver_id: str) -> Optional[float]:
        """
        Return the current rolling average of filtered RSSI for a pair.

        Args:
            uuid: BLE advertiser UUID.
            receiver_id: BLE receiver ID.
        """
        key = (uuid, receiver_id)
        with self._lock:
            if key not in self._windows or not self._windows[key]:
                return None
            return self._compute_average(key)

    def get_all_averages_for_uuid(self, uuid: str) -> Dict[str, Optional[float]]:
        """
        Return the rolling average RSSI for a UUID across all receivers it was heard at.

        Args:
            uuid: BLE advertiser UUID.
        """
        with self._lock:
            result: Dict[str, Optional[float]] = {}
            for (u, r), window in self._windows.items():
                if u == uuid:
                    result[r] = self._compute_average((u, r)) if window else None
            return result

    def remove_uuid(self, uuid: str):
        """
        Discard all filter and window state for a given UUID.

        Args:
            uuid: BLE advertiser UUID to purge.
        """
        with self._lock:
            keys_to_remove = [k for k in self._windows if k[0] == uuid]
            for key in keys_to_remove:
                self._filters.pop(key, None)
                del self._windows[key]
            if keys_to_remove:
                logger.debug("RSSIProcessor: removed all state for uuid=%s", uuid)

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_average(self, key: _PairKey) -> float:
        """Compute the mean of the rolling window for a given key."""
        window = self._windows[key]
        return sum(window) / len(window)
