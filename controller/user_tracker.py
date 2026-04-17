"""
Tracks every BLE advertiser (user) seen by the system and manages their zone
assignment within the logical queue.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from controller.rssi_processor import RSSIProcessor, RSSISample
from controller.zone_manager import ZoneManager

if TYPE_CHECKING:
    from controller.controller import Controller

logger = logging.getLogger(__name__)

DEFAULT_ENTRY_BUFFER_SECONDS = 1.0


@dataclass
class UserState:
    """
    State record for a single user (BLE advertiser) in the queue.

    Attributes:
        uuid: Unique BLE advertiser UUID (our user identifier).
        zone_receiver_id: Receiver ID of the user's current zone (None if not currently assigned)
        below_threshold_since: Timestamp where the user's
            RSSI at their current zone went below the eviction threshold.
            None if the RSSI is currently at or above the threshold.
        last_seen: Timestamp of the most recent RSSI measurement for this user.
    """
    uuid: str
    zone_receiver_id: Optional[str] = None
    priority: int = 0
    assigned_at: float = field(default_factory=time.monotonic)
    below_threshold_since: Optional[float] = None
    entry_since: Optional[float] = None
    entry_seen_receivers: set[str] = field(default_factory=set)
    lighting_active: bool = False
    last_seen: float = field(default_factory=time.monotonic)


class UserTracker:
    """
    Manages user state, zone assignments, advancement, and eviction.

    Attributes:
        rssi_processor: RSSI Filtering/Averaging subsystem instance.
        zone_manager: Zone ordering subsystem instance.
        controller: Central Controller instance
        hysteresis: RSSI advancement threshold (dBm).
        rssi_timeout_threshold: Eviction RSSI floor (dBm).
        rssi_timeout_duration: Eviction timer duration (seconds).
    """

    def __init__(
        self,
        rssi_processor: RSSIProcessor,
        zone_manager: ZoneManager,
        controller: "Controller",
        hysteresis: float,
        rssi_timeout_threshold: float,
        rssi_timeout_duration: float,
        no_ratchet: bool = False,
        entry_buffer_seconds: float = DEFAULT_ENTRY_BUFFER_SECONDS,
    ):
        """
        Initialise the user tracker.

        Args:
            rssi_processor: Handles Kalman filtering and rolling averages.
            zone_manager: Provides the ordered zone list and lookup helpers.
            controller: Provides ability to send lighting requests
            hysteresis: dBm advantage the next zone needs to trigger advancement.
            rssi_timeout_threshold: RSSI (dBm) below which a user is absent.
            rssi_timeout_duration: Seconds below threshold before eviction.
            no_ratchet: When True, users can move to any zone (not just forward).
        """
        self.rssi_processor = rssi_processor
        self.zone_manager = zone_manager
        self.controller = controller
        self.hysteresis = hysteresis
        self.rssi_timeout_threshold = rssi_timeout_threshold
        self.rssi_timeout_duration = rssi_timeout_duration
        self.no_ratchet = no_ratchet
        self.entry_buffer_seconds = entry_buffer_seconds

        self._users: Dict[str, UserState] = {}
        self._lock = threading.RLock()

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    def process_rssi(
        self,
        uuid: str,
        receiver_id: str,
        raw_rssi: float,
        priority: int = 0,
    ) -> RSSISample:
        """
        Handle a new RSSI observation for a user from a specific receiver.

        Args:
            uuid: BLE advertiser UUID (= user identifier).
            receiver_id: Receiver that heard the advertisement.
            raw_rssi: Raw RSSI measurement (dBm).
            priority: Optional user priority. Higher values win lighting conflicts.
        """
        # Update filter & rolling average now
        sample = self.rssi_processor.observe(uuid, receiver_id, raw_rssi)

        with self._lock:
            # Retrieve the user instance associated with the UUID
            user = self._get_or_create_user(uuid)
            user.last_seen = time.monotonic()
            user.priority = priority

            if user.zone_receiver_id is None:
                self._record_entry_sighting(user, receiver_id)
                self._try_activate_entry(user)
                return sample

            # Check to see if the user should be evicted from the queue due to timeout
            if self._check_eviction(user):
                return sample

            # Evaluate whether the user qualifies to move to the next zone.
            self._evaluate_advancement(user)

        return sample

    # ──────────────────────────────────────────────────────────────────────────
    # Read-only queries
    # ──────────────────────────────────────────────────────────────────────────

    def get_users_by_zone(self) -> Dict[str, List[str]]:
        """
        Return a mapping of (receiver_id, UUIDs) assigned to that zone.
        """
        with self._lock:
            result: Dict[str, List[str]] = {}
            for uuid, user in self._users.items():
                if user.zone_receiver_id:
                    result.setdefault(user.zone_receiver_id, []).append(uuid)
            return result

    def get_all_users(self) -> Dict[str, Optional[str]]:
        """
        Return all tracked users in the system and their current zone receiver ID.
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

    def sweep_stale_users(self, timeout: float):
        """
        Evict any user who hasn't been heard by any receiver in the last ``timeout`` seconds.

        Intended to be called periodically (e.g. from the heartbeat monitor thread) to
        handle the case where a user walks out of range of all receivers and no further
        RSSI data arrives, which would otherwise prevent the eviction timer from firing.

        Args:
            timeout: Seconds of silence before a user is considered gone.
        """
        now = time.monotonic()
        with self._lock:
            stale = [
                uuid for uuid, user in self._users.items()
                if now - user.last_seen >= timeout
            ]
            for uuid in stale:
                logger.info(
                    "UserTracker: uuid=%s evicted — no signal from any receiver for %.1f s",
                    uuid, timeout,
                )
                self._evict(uuid)
        if stale:
            self.controller._broadcast_state()

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _get_or_create_user(self, uuid: str) -> UserState:
        """Retrieve or create a UserState for the given UUID. Lock must be held."""
        if uuid not in self._users:
            self._users[uuid] = UserState(uuid=uuid)
            logger.info("UserTracker: new user detected uuid=%s", uuid)
        return self._users[uuid]

    def _record_entry_sighting(self, user: UserState, receiver_id: str):
        """Record a receiver sighting for a new or returning user."""
        if user.entry_since is None:
            user.entry_since = time.monotonic()
            user.entry_seen_receivers.clear()
            user.lighting_active = False

        user.entry_seen_receivers.add(receiver_id)

    def _try_activate_entry(self, user: UserState) -> bool:
        """Activate a newly entered user after the buffer window has elapsed."""
        if user.zone_receiver_id is not None or user.entry_since is None:
            return False

        if time.monotonic() - user.entry_since < self.entry_buffer_seconds:
            return False

        self._assign_nearest_zone(user)
        user.entry_since = None
        user.entry_seen_receivers.clear()
        user.lighting_active = True
        return True

    def _assign_nearest_zone(self, user: UserState):
        """
        Place user at the earliest zone known to have seen them.
        Falls back to zone 0 if no signal data exists yet.

        REQUIRES LOCK TO BE HELD!
        """
        zones = self.zone_manager.get_zones()
        if not zones:
            logger.warning(
                "UserTracker: no zones registered — cannot assign uuid=%s", user.uuid
            )
            return

        averages = self.rssi_processor.get_all_averages_for_uuid(user.uuid)
        best_zone = None
        if user.entry_seen_receivers:
            for zone in zones:
                if zone in user.entry_seen_receivers:
                    best_zone = zone
                    break
        else:
            for zone in zones:
                if averages.get(zone) is not None:
                    best_zone = zone
                    break

        if best_zone is None:
            best_zone = zones[0]

        previous_target = self._get_zone_lighting_target(best_zone)

        user.zone_receiver_id = best_zone
        user.assigned_at = time.monotonic()
        user.below_threshold_since = None
        zone_idx = zones.index(best_zone)
        logger.info(
            "UserTracker: uuid=%s → assigned to zone %d (receiver='%s')",
            user.uuid, zone_idx, best_zone,
        )

        if previous_target is None or user.priority > self._users[previous_target].priority:
            self.controller._send_lighting(best_zone, user.uuid)

    def _get_zone_lighting_target(self, receiver_id: str) -> Optional[str]:
        """Return the highest-priority lighting target for a zone."""
        zone_users = self.get_users_by_zone().get(receiver_id, [])
        if not zone_users:
            return None

        best = None
        for uuid in zone_users:
            candidate = self._users[uuid]
            if best is None:
                best = candidate
            elif candidate.priority > best.priority:
                best = candidate
            elif candidate.priority == best.priority:
                if candidate.assigned_at < best.assigned_at:
                    best = candidate
        return best.uuid

    def _check_eviction(self, user: UserState) -> bool:
        """
        Determine if a given user should be evicted from the queue

        REQUIRES LOCK TO BE HELD!
        """
        # Get the average RSSI for a user at a given zone
        avg = self.rssi_processor.get_average(user.uuid, user.zone_receiver_id)
        if avg is None:
            return False

        # Determine if the current average is below the stated floor for eviction
        if avg < self.rssi_timeout_threshold:

            # If the user hasn't dropped below the eviction floor yet, start the eviction timer
            if user.below_threshold_since is None:
                user.below_threshold_since = time.monotonic()
                logger.debug(
                    "UserTracker: uuid=%s RSSI %.1f dBm below threshold %.1f — timer started",
                    user.uuid, avg, self.rssi_timeout_threshold,
                )

            # If the user is still below the floor and the timer elapsed, evict them!
            elif time.monotonic() - user.below_threshold_since >= self.rssi_timeout_duration:
                logger.info(
                    "UserTracker: uuid=%s evicted — RSSI %.1f dBm below %.1f for %.1f s",
                    user.uuid, avg, self.rssi_timeout_threshold, self.rssi_timeout_duration,
                )
                self._evict(user.uuid)
                return True
        
        # Signal is above the eviction floor, cancel any pending evictions for the user!
        else:
            if user.below_threshold_since is not None:
                logger.debug(
                    "UserTracker: uuid=%s RSSI recovered (%.1f dBm) — timer reset",
                    user.uuid, avg,
                )
            user.below_threshold_since = None

        return False

    def _evaluate_advancement(self, user: UserState):
        """
        Check whether the user should move to a different zone. Lock must be held.

        In normal (ratchet) mode, only forward movement is considered.
        In no-ratchet mode, the user moves to whichever zone has the strongest signal.
        Hysteresis applies in both modes to prevent thrashing.
        """
        if self.no_ratchet:
            self._evaluate_best_zone(user)
        else:
            self._evaluate_forward(user)

    def _evaluate_forward(self, user: UserState):
        """
        Advance the user to the next zone if its signal is stronger by the hysteresis margin.
        Lock must be held.
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
            self._move_user(user, next_receiver)

    def _evaluate_best_zone(self, user: UserState):
        """
        Move the user to whichever zone has the strongest signal, in either direction.
        Lock must be held.
        """
        averages = self.rssi_processor.get_all_averages_for_uuid(user.uuid)
        current_avg = averages.get(user.zone_receiver_id)
        if current_avg is None:
            return

        threshold = current_avg + self.hysteresis
        best_zone = user.zone_receiver_id
        best_avg = current_avg
        for zone in self.zone_manager.get_zones():
            avg = averages.get(zone)
            if avg is not None and avg > threshold and avg > best_avg:
                best_avg = avg
                best_zone = zone

        if best_zone != user.zone_receiver_id:
            logger.info(
                "UserTracker: uuid=%s moving '%s' → '%s' "
                "(best=%.1f > current=%.1f + hyst=%.1f)",
                user.uuid, user.zone_receiver_id, best_zone,
                best_avg, current_avg, self.hysteresis,
            )
            self._move_user(user, best_zone)

    def _move_user(self, user: UserState, new_receiver: str):
        """
        Assign a user to a new zone, update lighting, and reset the eviction timer.
        Lock must be held.
        """
        old_receiver = user.zone_receiver_id
        user.zone_receiver_id = new_receiver
        user.assigned_at = time.monotonic()
        user.below_threshold_since = None

        old_target = self._get_zone_lighting_target(old_receiver)
        if old_target is None:
            self.controller._send_lighting(old_receiver, "")
        else:
            self.controller._send_lighting(old_receiver, old_target)

        # Reevaluate lighting after the move and send the highest-priority target.
        target = self._get_zone_lighting_target(new_receiver)
        if target is not None:
            self.controller._send_lighting(new_receiver, target)



    def _evict(self, uuid: str):
        """Remove a user and purge their RSSI state. Lock must be held."""
        user = self._users.pop(uuid, None)
        self.rssi_processor.remove_uuid(uuid)
        if user and user.zone_receiver_id is not None:
            target = self._get_zone_lighting_target(user.zone_receiver_id)
            if target is not None:
                self.controller._send_lighting(user.zone_receiver_id, target)
            else:
                self.controller._send_lighting(user.zone_receiver_id, "")
        logger.debug("UserTracker: uuid=%s evicted and state purged", uuid)
