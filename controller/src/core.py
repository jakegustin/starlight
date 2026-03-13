"""
Core functionality of Starlight controller, updating state and registering users
"""
from __future__ import annotations

import json
from typing import Optional

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
    rssi_entry_threshold : float
        The minimum RSSI to be eligible to advance to the next zone
    rssi_exit_threshold : float
        The RSSI to fall below to indicate a user left the queue
    timeout_seconds : float
        How long to wait before removing a user from the queue
    window_size : int
        How many RSSI readings to contain in a rolling window when averaging
    kalman_process_noise : float
        How much to trust new data over existing data. Lower = trust existing values more
    kalman_measurement_noise : float
        The filter's sensitivity for identifying outliers. Lower = more likely to deem as outlier
    kalman_gate_threshold : float
        The number of standard deviations difference needed for measurements to be deemed outliers
    _users : dict[str, User]
        A mapping of user UUIDs to User instances
    _zone_active : dict[str, Optional[str]]
        A mapping of the user settings applied to each zone, if any
    _priority_map : dict[str, int]
        A mapping of user UUIDs to priority levels
    _event_log : list[ZoneEvent]
        A log of zone transition/timeout events
    """

    def __init__(
        self,
        zone_order: list[str],
        automatic_registration: bool = False,
        *,
        rssi_entry_threshold: float = 30.0,
        rssi_exit_threshold: float = 10.0,
        timeout_seconds: float = 10.0,
        window_size: int = 10,
        kalman_process_noise: float = 1.0,
        kalman_measurement_noise: float = 10.0,
        kalman_gate_threshold: float = 3.0,
    ) -> None:
        """
        Creates a CentralController instance
        """
        if not zone_order:
            raise ValueError("zone_order must contain at least one zone")

        self.zone_order = list(zone_order)
        self.automatic_registration = automatic_registration
        self.rssi_entry_threshold = rssi_entry_threshold
        self.rssi_exit_threshold = rssi_exit_threshold
        self.timeout_seconds = timeout_seconds
        self.window_size = window_size
        self.kalman_process_noise = kalman_process_noise
        self.kalman_measurement_noise = kalman_measurement_noise
        self.kalman_gate_threshold = kalman_gate_threshold

        self._users: dict[str, User] = {}
        self._zone_active: dict[str, Optional[str]] = {z: None for z in zone_order}
        self._priority_map: dict[str, int] = {}
        self._event_log: list[ZoneEvent] = []

    def register_user(self, uuid: str, priority: int = 0) -> None:
        """
        Adds/Registers a user into the system who can enable zone effects
        """
        if uuid not in self._users:
            self._users[uuid] = User(uuid, priority)
        else:
            self._users[uuid].priority = priority
        self._priority_map[uuid] = priority

    def ingest(self, raw_json: str) -> ZoneEvents | None:
        """
        Ingests a raw JSON output provided by a BLE receiver
        """
        data = json.loads(raw_json)
        return self.ingest_reading(
            receiver_id=data["BLE_RECEIVER_ID"],
            timestamp=data["TIMESTAMP"],
            uuid=data["BLE_UUID"],
            rssi=data["RSSI"],
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
            process_noise=self.kalman_process_noise,
            measurement_noise=self.kalman_measurement_noise,
            gate_threshold=self.kalman_gate_threshold,
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
            self._users[uuid] = User(uuid, priority=self._priority_map.get(uuid, 0))

        return self._users[uuid]

    def _zone_index(self, zone_id: str) -> int:
        """
        Get the index within zone_order corresponding to a given zone ID
        """
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
