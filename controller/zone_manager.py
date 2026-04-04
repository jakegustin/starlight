"""
Maintains the ordered list of BLE receiver IDs that define the logical queue zones (0 = start)
"""

import logging
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)


class ZoneManager:
    """
    Manager for the ordered zone (receiver) list.

    Attributes:
        _zones (List[str]): Ordered list of receiver IDs (protected by _lock).
        _lock (threading.RLock): Re-entrant lock for thread safety.
    """

    def __init__(self):
        """Initialise an empty zone ordering."""
        self._zones: List[str] = []
        self._lock = threading.RLock()

    # ──────────────────────────────────────────────────────────────────────────
    # Receiver Registration
    # ──────────────────────────────────────────────────────────────────────────

    def register_receiver(self, receiver_id: str):
        """
        Register a BLE receiver. Appends to the end if not already present.

        Args:
            receiver_id: Unique string ID reported by the receiver in its messages.
        """
        with self._lock:
            if receiver_id not in self._zones:
                self._zones.append(receiver_id)
                logger.info(
                    "ZoneManager: registered '%s' at zone index %d",
                    receiver_id, len(self._zones) - 1,
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Ordering
    # ──────────────────────────────────────────────────────────────────────────

    def set_order(self, ordered_ids: List[str]):
        """
        Replace the zone ordering with the provided list.

        Args:
            ordered_ids: Desired new ordering of receiver IDs.
        """
        with self._lock:
            # Keep only registered IDs in the specified order
            valid = [rid for rid in ordered_ids if rid in self._zones]

            # Preserve receivers that were not mentioned by appending them to the end
            missing = [rid for rid in self._zones if rid not in valid]
            self._zones = valid + missing
            logger.info("ZoneManager: zone order updated → %s", self._zones)

    def get_zones(self) -> List[str]:
        """
        Return a snapshot of the current zone ordering.

        Returns:
            New list containing receiver IDs in zone order
        """
        with self._lock:
            return list(self._zones)

    # ──────────────────────────────────────────────────────────────────────────
    # Lookups
    # ──────────────────────────────────────────────────────────────────────────

    def get_zone_index(self, receiver_id: str) -> Optional[int]:
        """
        Return the zone index of a receiver.

        Args:
            receiver_id: Receiver to look up.
        """
        with self._lock:
            try:
                return self._zones.index(receiver_id)
            except ValueError:
                return None

    def get_receiver_at_zone(self, zone_index: int) -> Optional[str]:
        """
        Return the receiver ID at the given zone index.

        Args:
            zone_index: Zero-based zone position.
        """
        with self._lock:
            if 0 <= zone_index < len(self._zones):
                return self._zones[zone_index]
            return None

    def get_next_zone_receiver(self, current_receiver_id: str) -> Optional[str]:
        """
        Return the receiver ID immediately after the given receiver in zone order.

        Args:
            current_receiver_id: The current zone's receiver ID.
        """
        with self._lock:
            try:
                idx = self._zones.index(current_receiver_id)
                if idx + 1 < len(self._zones):
                    return self._zones[idx + 1]
                return None
            except ValueError:
                return None

    def zone_count(self) -> int:
        """Return the number of registered zones."""
        with self._lock:
            return len(self._zones)
