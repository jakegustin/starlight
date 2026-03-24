"""
Starlight System - User Tracker
=================================

Tracks every BLE advertiser (user) seen by the system and manages their zone
assignment within the logical queue. This module encapsulates all business logic
around zone advancement and user eviction.

Zone assignment rules
---------------------
1. **Entry**: A user with no assigned zone is immediately placed into zone 0
   (the "upcoming" zone), regardless of which receiver first heard them.

2. **Advancement**: A user advances from their current zone (receiver C) to the
   next zone (receiver N) only if:
       avg_rssi[N] > avg_rssi[C] + hysteresis
   Movement is strictly monotonically increasing — no backwards steps.

3. **Eviction**: If a user's rolling-average RSSI at their current zone falls
   below *rssi_timeout_threshold* for longer than *rssi_timeout_duration*
   seconds, they are removed from the queue entirely and their RSSI state is
   purged.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from controller.rssi_processor import RSSIProcessor
from controller.zone_manager import ZoneManager

logger = logging.getLogger(__name__)


@dataclass
class UserState:
    """
    State record for a single user (BLE advertiser) in the queue.

    Attributes:
        uuid: Unique BLE advertiser UUID — the user's identifier.
        zone_receiver_id: Receiver ID of the user's current zone, or None if
            they have not yet been assigned to any zone.
        below_threshold_since: Monotonic timestamp (seconds) at which the user's
            RSSI at their current zone first dropped below the eviction threshold.
            None if the RSSI is currently at or above the threshold.
        last_seen: Monotonic timestamp of the most recent RSSI measurement for
            this user (across any receiver).
    """
    uuid: str
    zone_receiver_id: Optional[str] = None
    below_threshold_since: Optional[float] = None
    last_seen: float = field(default_factory=time.monotonic)


class UserTracker:
    """
    Manages user state, zone assignments, advancement, and eviction.

    All state is protected by a single re-entrant lock so the tracker can be
    called safely from both the main processing thread and, in future, any
    background maintenance threads.

    Attributes:
        rssi_processor (RSSIProcessor): Shared filter/average subsystem.
        zone_manager (ZoneManager): Shared zone ordering subsystem.
        hysteresis (float): RSSI advancement threshold (dBm).
        rssi_timeout_threshold (float): Eviction RSSI floor (dBm).
        rssi_timeout_duration (float): Eviction timer duration (seconds).
    """

    def __init__(
        self,
        rssi_processor: RSSIProcessor,
        zone_manager: ZoneManager,
        hysteresis: float,
        rssi_timeout_threshold: float,
        rssi_timeout_duration: float,
    ):
        """
        Initialise the user tracker.

        Args:
            rssi_processor: Handles Kalman filtering and rolling averages.
            zone_manager: Provides the ordered zone list and lookup helpers.
            hysteresis: dBm advantage the next zone needs to trigger advancement.
            rssi_timeout_threshold: RSSI (dBm) below which a user is absent.
            rssi_timeout_duration: Seconds below threshold before eviction.
        """
        self.rssi_processor = rssi_processor
        self.zone_manager = zone_manager
        self.hysteresis = hysteresis
        self.rssi_timeout_threshold = rssi_timeout_threshold
        self.rssi_timeout_duration = rssi_timeout_duration

        self._users: Dict[str, UserState] = {}
        self._lock = threading.RLock()

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def process_rssi(self, uuid: str, receiver_id: str, raw_rssi: float):
        """
        Handle a new RSSI observation for a user from a specific receiver.

        This is the primary entry point called for every incoming BLE data message.
        It updates the filter/average, creates the user if unseen, and runs the
        advancement and eviction checks.

        Args:
            uuid: BLE advertiser UUID (= user identifier).
            receiver_id: Receiver that heard the advertisement.
            raw_rssi: Raw RSSI measurement (dBm).
        """
        # Update filter + rolling average BEFORE acquiring the user lock so that
        # the latest average is ready when we evaluate advancement/eviction.
        self.rssi_processor.ingest(uuid, receiver_id, raw_rssi)

        with self._lock:
            user = self._get_or_create_user(uuid)
            user.last_seen = time.monotonic()

            # First assignment: place unassigned user at zone 0 immediately.
            if user.zone_receiver_id is None:
                self._assign_first_zone(user)
                return

            # Eviction check runs before advancement; if evicted we stop here.
            if self._check_eviction(user):
                return

            # Evaluate whether the user qualifies to move to the next zone.
            self._evaluate_advancement(user)

    # ──────────────────────────────────────────────────────────────────────────
    # Read-only queries (used by Controller / WebSocket state broadcasts)
    # ──────────────────────────────────────────────────────────────────────────

    def get_users_by_zone(self) -> Dict[str, List[str]]:
        """
        Return a mapping of receiver_id → list of UUIDs assigned to that zone.

        Returns:
            Dict where keys are receiver IDs and values are UUID lists.
        """
        with self._lock:
            result: Dict[str, List[str]] = {}
            for uuid, user in self._users.items():
                if user.zone_receiver_id:
                    result.setdefault(user.zone_receiver_id, []).append(uuid)
            return result

    def get_all_users(self) -> Dict[str, Optional[str]]:
        """
        Return all tracked users and their current zone receiver ID.

        Returns:
            Dict mapping uuid -> zone receiver ID (or None).
        """
        with self._lock:
            return {uuid: user.zone_receiver_id for uuid, user in self._users.items()}

    def remove_user(self, uuid: str):
        """
        Manually evict a user from the queue.

        Args:
            uuid: UUID of the user to remove.
        """
        with self._lock:
            self._evict(uuid)

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get_or_create_user(self, uuid: str) -> UserState:
        """Retrieve or create a UserState for the given UUID. Lock must be held."""
        if uuid not in self._users:
            self._users[uuid] = UserState(uuid=uuid)
            logger.info("UserTracker: new user detected uuid=%s", uuid)
        return self._users[uuid]

    def _assign_first_zone(self, user: UserState):
        """Place user at zone 0 (upcoming zone). Lock must be held."""
        zones = self.zone_manager.get_zones()
        if not zones:
            logger.warning(
                "UserTracker: no zones registered — cannot assign uuid=%s", user.uuid
            )
            return
        user.zone_receiver_id = zones[0]
        user.below_threshold_since = None
        logger.info(
            "UserTracker: uuid=%s → assigned to zone 0 (receiver='%s')",
            user.uuid, user.zone_receiver_id,
        )

    def _check_eviction(self, user: UserState) -> bool:
        """
        Evaluate and apply the RSSI timeout eviction condition. Lock must be held.

        Returns:
            True if the user was evicted (caller should stop further processing).
        """
        avg = self.rssi_processor.get_average(user.uuid, user.zone_receiver_id)
        if avg is None:
            return False

        if avg < self.rssi_timeout_threshold:
            # Signal is below the floor — start or continue the eviction countdown.
            if user.below_threshold_since is None:
                user.below_threshold_since = time.monotonic()
                logger.debug(
                    "UserTracker: uuid=%s RSSI %.1f dBm below threshold %.1f — timer started",
                    user.uuid, avg, self.rssi_timeout_threshold,
                )
            elif time.monotonic() - user.below_threshold_since >= self.rssi_timeout_duration:
                logger.info(
                    "UserTracker: uuid=%s evicted — RSSI %.1f dBm below %.1f for %.1f s",
                    user.uuid, avg, self.rssi_timeout_threshold, self.rssi_timeout_duration,
                )
                self._evict(user.uuid)
                return True
        else:
            # Signal has recovered — cancel the eviction countdown.
            if user.below_threshold_since is not None:
                logger.debug(
                    "UserTracker: uuid=%s RSSI recovered (%.1f dBm) — timer reset",
                    user.uuid, avg,
                )
            user.below_threshold_since = None

        return False

    def _evaluate_advancement(self, user: UserState):
        """
        Check whether the user should advance to the next zone. Lock must be held.

        Advancement condition:
            avg_rssi[next_zone] > avg_rssi[current_zone] + hysteresis
        """
        next_receiver = self.zone_manager.get_next_zone_receiver(user.zone_receiver_id)
        if next_receiver is None:
            # Already at the final zone — no further advancement possible.
            return

        current_avg = self.rssi_processor.get_average(user.uuid, user.zone_receiver_id)
        next_avg = self.rssi_processor.get_average(user.uuid, next_receiver)

        if current_avg is None or next_avg is None:
            # Insufficient data for one or both zones — wait for more samples.
            return

        if next_avg > current_avg + self.hysteresis:
            logger.info(
                "UserTracker: uuid=%s advancing '%s' → '%s' "
                "(next=%.1f > current=%.1f + hyst=%.1f)",
                user.uuid, user.zone_receiver_id, next_receiver,
                next_avg, current_avg, self.hysteresis,
            )
            user.zone_receiver_id = next_receiver
            user.below_threshold_since = None  # Reset timer after advancing

    def _evict(self, uuid: str):
        """Remove a user and purge their RSSI state. Lock must be held."""
        self._users.pop(uuid, None)
        self.rssi_processor.remove_uuid(uuid)
        logger.debug("UserTracker: uuid=%s evicted and state purged", uuid)
