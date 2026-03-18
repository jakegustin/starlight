"""
Core functionality of Starlight controller, updating state and registering users
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from .types import ZoneEvent, ZoneEvents
from .user import User

class CentralController:
    """
    The core of the system: manages all registered users and all zones in a queue
    
    Attributes
    ----------
    zone_order : list[str]
        The order of the zones in the queue
    automatic_registration : bool
        Determine if any and all BLE UUIDs can automatically be registered into the queue system
    timeout_seconds : float
        How long to wait before removing a user from the queue
    window_size : int
        How many RSSI readings to contain in a rolling window when averaging
    rssi_entry_threshold : float
        Minimum RSSI difference for a user to be eligible to move into the next zone
    rssi_exit_threshold : float
        The RSSI value that an advertiser must drop below to be considered as having left the queue
    process_noise : float
        For KalmanFilter: how much to trust new data. Lower = trust existing values more
    measurement_noise : float
        For KalmanFilter: filter's sensitivity for outlier detection. Lower = more outliers
    gate_threshold : float
        For KalmanFilter: Number of standard deviations difference consider a data point an outlier
    allow_dynamic_zones : bool
        Indicates whether the zone_order should be updated dynamically as receivers are detected
    _users : dict[str, User]
        A mapping of user UUIDs to User instances
    _zone_active : dict[str, Optional[str]]
        A mapping of the user settings applied to each zone, if any
    _event_log : list[ZoneEvent]
        A log of zone transition/timeout events
    """

    def __init__(
        self,
        zone_order: list[str],
        automatic_registration: bool = False,
        *,
        timeout_seconds: float = 10.0,
        window_size: int = 10,
        rssi_entry_threshold: float = 30.0,
        rssi_exit_threshold: float = 30.0,
        process_noise: float = 1.0,
        measurement_noise: float = 10.0,
        gate_threshold: float = 3.0,
        allow_dynamic_zones: bool = False,
    ) -> None:
        """
        Creates a CentralController instance
        """
        if not zone_order and not allow_dynamic_zones:
            raise ValueError("zone_order must contain at least one zone")

        self.zone_order = list(zone_order)
        self.automatic_registration = automatic_registration
        self.timeout_seconds = timeout_seconds
        self.window_size = window_size
        self.rssi_entry_threshold = rssi_entry_threshold
        self.rssi_exit_threshold = rssi_exit_threshold
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.gate_threshold = gate_threshold
        self.allow_dynamic_zones = allow_dynamic_zones
        self._lock = threading.RLock()

        self._users: dict[str, User] = {}
        self._zone_active: dict[str, Optional[str]] = {z: None for z in zone_order}
        self._event_log: list[ZoneEvent] = []

        self.on_user_registered: Optional[Callable[[str], None]] = None

    def register_user(self, uuid: str, priority: int = 0) -> None:
        """
        Adds/Registers a user into the system who can enable zone effects
        """
        is_new = uuid not in self._users
        if is_new:
            self._users[uuid] = User(uuid, priority)
        else:
            self._users[uuid].priority = priority

        if is_new and self.on_user_registered:
            self.on_user_registered(uuid)

    def add_zone(self, zone_id: str) -> None:
        """
        Add a zone to the controller if it does not already exist
        """
        with self._lock:
            if zone_id not in self.zone_order:
                self.zone_order.append(zone_id)
                self._zone_active[zone_id] = None

    def set_zone_order(self, zone_order: list[str], *, keep_unlisted: bool = True) -> list[str]:
        """
        Manually reorder zones and return the updated order.
        """
        with self._lock:
            # Initialize variables
            seen: set[str] = set()
            requested = [z for z in zone_order if z and not (z in seen or seen.add(z))]

            # Get the existing receivers and the desired receiver order
            existing = set(self.zone_order)
            reordered = [z for z in requested if z in existing]

            # If no position specified, just append it to the end
            if keep_unlisted:
                reordered.extend(z for z in self.zone_order if z not in reordered)

            # If dynamic scanning not enabled and no zones identified, issue an error
            if not reordered and not self.allow_dynamic_zones:
                raise ValueError("zone_order must contain at least one known zone")

            # Update the zone order, including active zones
            self.zone_order = reordered
            self._zone_active = {z: self._zone_active.get(z) for z in self.zone_order}

            return list(self.zone_order)

    def snapshot(self) -> dict[str, object]:
        """
        Return a thread-safe snapshot of controller state for UIs
        """
        with self._lock:
            return {
                "zone_order": list(self.zone_order),
                "zone_active": dict(self._zone_active),
            }

    def ingest(self, raw_json: str) -> ZoneEvents | None:
        """
        Ingests a raw JSON output provided by a BLE receiver
        """
        data = json.loads(raw_json)

        # We can ignore heartbeat messages for now
        if data.get("type") == "heartbeat":
            print("[CentralController] Discarding heartbeat message")
            return None

        if data.get("type") != "data":
            print("[CentralController] Got unknown message type", data.get("type"))
            return None

        receiver_id = data.get("id")
        uuid = data.get("uuid")
        rssi = data.get("rssi")

        if receiver_id is None or uuid is None or rssi is None:
            # If required fields are missing, just forget about it
            return None

        # Use the controller's local time as our ground truth
        timestamp = time.time()

        return self.ingest_reading(
            receiver_id=str(receiver_id),
            timestamp=timestamp,
            uuid=str(uuid),
            rssi=float(rssi),
        )

    def ingest_reading(
        self,
        receiver_id: str,
        timestamp: float,
        uuid: str,
        rssi: float,
    ) -> ZoneEvents | None:
        """
        Updates controller buffers and event logs based upon reading data
        """
        events: ZoneEvents = []
        user = self._ensure_user(uuid)
        if not user:
            return None
        user.last_seen = timestamp

        # Retrieve the buffer corresponding to a given zone
        buf = user.get_buffer(
            receiver_id,
            window_size=self.window_size,
            process_noise=self.process_noise,
            measurement_noise=self.measurement_noise,
            gate_threshold=self.gate_threshold,
        )
        buf.add(rssi)

        # If the user's current RSSI is above the minimum, indicate they are still in the queue
        if rssi >= self.rssi_exit_threshold:
            user.last_strong_signal_at = max(user.last_strong_signal_at, timestamp)

        # Append progression and timeout events to the log
        events.extend(self._evaluate_progression(user, timestamp))
        events.extend(self._check_timeouts(timestamp))
        self._event_log.extend(events)
        return events

    def get_user(self, uuid: str) -> Optional[User]:
        """
        Get the User instance based upon a given device UUID
        """
        return self._users.get(uuid)

    def get_registered_uuids(self) -> list[str]:
        """Return a list of all UUIDs registered with the controller."""
        return list(self._users.keys())

    def active_user_at(self, zone_id: str) -> Optional[str]:
        """
        Get the highest priority user's UUID within a given zone
        """
        return self._zone_active.get(zone_id)

    def users_in_zone(self, zone_id: str) -> list[User]:
        """
        Get a list of all users in a given zone
        """
        return [u for u in self._users.values() if u.current_zone == zone_id]

    @property
    def event_log(self) -> list[ZoneEvent]:
        """
        Fetch the controller's event log
        """
        return list(self._event_log)

    def _ensure_user(self, uuid: str) -> User | None:
        """
        Fetch the User instance for a given UUID, or indicate that the user does not exist!
        """
        # Unregistered users in a manual registration system should not manipulate the zones!
        if uuid not in self._users and not self.automatic_registration:
            return None

        # Unregistered users in an automatic registration should be registered at default priority
        if uuid not in self._users:
            self._users[uuid] = User(uuid, priority=self._users.get(uuid, User(uuid)).priority)

        return self._users[uuid]

    def _zone_index(self, zone_id: str) -> int:
        """
        Get the index within zone_order corresponding to a given zone ID
        """
        # If dynamic zones are allowed, add the zone to the list
        if zone_id not in self.zone_order:
            if not self.allow_dynamic_zones:
                raise ValueError(f"Unknown zone '{zone_id}'")
            self.add_zone(zone_id)

        return self.zone_order.index(zone_id)

    def _evaluate_progression(self, user: User, ts: float) -> ZoneEvents:
        """
        Check if the user advanced into the next zone or exited the queue
        """
        events: ZoneEvents = []
        events.extend(self._advance_zone(user, ts))
        events.extend(self._check_exit_last_zone(user, ts))
        return events

    def _advance_zone(self, user: User, ts: float) -> ZoneEvents:
        """
        Attempts to move the user forward into the next zone
        """
        events: ZoneEvents = []
        cur = user.current_zone
        cur_idx = self._zone_index(cur) if cur is not None else -1
        next_idx = cur_idx + 1

        # First, confirm there is in fact a "next zone" to go to
        if next_idx < len(self.zone_order):
            next_zone = self.zone_order[next_idx]

            # Get the average/smoothed RSSI value for the user in the next zone
            avg = user.smoothed_rssi(next_zone)

            # If that smoothed RSSI exceeds the threshold, move the user forward a zone
            if avg is not None and avg >= self.rssi_entry_threshold:
                old = user.current_zone
                user.current_zone = next_zone
                user.zone_history.append(next_zone)

                # If the user moves forward a zone, they will naturally need to leave their old zone
                if old is not None:
                    events.append(ZoneEvent("user_exited", old, user.uuid, ts))
                    self._refresh_active(old)

                events.append(ZoneEvent("user_entered", next_zone, user.uuid, ts))
                self._refresh_active(next_zone)
        return events

    def _check_exit_last_zone(self, user: User, ts: float) -> ZoneEvents:
        """
        Remove a user from the queue they were in the last zone and signal strength is low
        """
        events: ZoneEvents = []
        if user.current_zone is None:
            return events

        idx = self._zone_index(user.current_zone)

        # Confirm the user is in the last zone
        if idx == len(self.zone_order) - 1:
            avg = user.smoothed_rssi(user.current_zone)

            # If the smoothed RSSI is below the exit threshold, remove them from the queue
            if avg is not None and avg < self.rssi_exit_threshold:
                zone = user.current_zone
                events.append(ZoneEvent("user_exited", zone, user.uuid, ts))
                user.reset()
                self._refresh_active(zone)

        return events

    def _check_timeouts(self, now: float) -> ZoneEvents:
        """
        Check the timeouts of each user to see if any of them left the queue
        """
        events: ZoneEvents = []
        for user in list(self._users.values()):
            if user.current_zone is None:
                continue

            # Last strong signal was over timeout_seconds ago. Drop the user from the queue
            if now - user.last_strong_signal_at >= self.timeout_seconds:
                zone = user.current_zone
                events.append(ZoneEvent("user_timeout", zone, user.uuid, now))
                user.reset()
                self._refresh_active(zone)

        return events

    def _refresh_active(self, zone_id: str) -> None:
        """
        Update the zone status to reflect the highest priority user
        """
        candidates = self.users_in_zone(zone_id)

        # If there are users in the zone, choose the highest priority user
        if candidates:
            self._zone_active[zone_id] = max(
                candidates, key=lambda u: u.priority
            ).uuid

        # No users in the zone, disable the zone effects for now
        else:
            self._zone_active[zone_id] = None
