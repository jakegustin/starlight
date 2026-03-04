from __future__ import annotations
from typing import Any
from .rssi import RSSIBuffer


class User:
    """
    An instance of a user in the system that may or may not be in the queue

    Attributes
    ----------
    uuid : str
        The BLE UUID identifier for the user.
    priority : int
        Priority level of a user. Used to determine zone activation.
    current_zone : Optional[str]
        The zone the user currently resides in. Defaults to `None`.
    zone_history : list[str]
        The zones in the queue the user entered so far.
    last_seen : float
        The timestamp of the last known advertisement from the user's device.
    last_strong_signal_at : float
        The timestamp of the last known advertisement with a strong signal.
    _buffers : dict[str, RSSIBuffer]
        The buffer of the user's RSSI signals for each zone in the queue.
    """

    def __init__(self, uuid: str, priority: int = 0) -> None:
        """
        Creates a new user instance
        """
        self.uuid = uuid
        self.priority = priority
        self.current_zone: str | None = None
        self.zone_history: list[str] = []
        self.last_seen: float = 0.0
        self.last_strong_signal_at: float = 0.0
        self._buffers: dict[str, RSSIBuffer] = {}

    def get_buffer(self, zone_id: str, **kwargs: Any) -> RSSIBuffer:
        """
        Retrieves the buffer for a user within a specified zone
        """
        buf = self._buffers.get(zone_id)
        if buf is None:
            buf = RSSIBuffer(**kwargs)
            self._buffers[zone_id] = buf
        return buf

    def smoothed_rssi(self, zone_id: str) -> float | None:
        """
        Returns a smoothed RSSI value (via averaging), if the buffer exists
        """
        buf = self._buffers.get(zone_id)
        return buf.average if buf else None

    def reset(self) -> None:
        """
        Clears out the user's zone-related information and buffers
        """
        self.current_zone = None
        self.zone_history.clear()
        for buf in self._buffers.values():
            buf.clear()

    def __repr__(self) -> str:
        """
        Sets how a User instance should be printed to stdout
        """
        return (
            f"User(uuid={self.uuid}, priority={self.priority}, "
            f"zone={self.current_zone})"
        )