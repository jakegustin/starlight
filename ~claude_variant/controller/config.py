"""
Starlight System - Controller Configuration
==========================================

Defines the ControllerConfig dataclass holding all tunable parameters for the
Starlight central controller. Every subsystem receives a reference to one shared
config instance, making the system straightforward to tune without editing
individual module files.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ControllerConfig:
    """
    Immutable (by convention) configuration for the Starlight central controller.

    Attributes:
        baud_rate:
            Serial baud rate for communicating with BLE receivers.
        heartbeat_timeout:
            Seconds without a heartbeat before a receiver is considered inactive.
            Should be greater than 2x the receiver heartbeat cadence (satisfies the
            "at least 2 missed heartbeats" requirement from the spec).
        kalman_process_noise:
            Kalman filter process noise covariance (Q). Higher values make the
            filter more responsive to sudden RSSI changes.
        kalman_measurement_noise:
            Kalman filter measurement noise covariance (R). Higher values produce
            a smoother (but slower-reacting) estimate.
        rolling_window_size:
            Number of Kalman-filtered RSSI samples in the rolling average per
            (UUID, receiver) pair. Must be >= 1.
        hysteresis:
            RSSI delta (dBm) the next zone must exceed over the current zone before
            the user is advanced. Prevents flapping at zone boundaries.
        rssi_timeout_threshold:
            RSSI floor (dBm). When a user's averaged RSSI at their current zone
            drops below this value the eviction timer starts.
        rssi_timeout_duration:
            Seconds the RSSI must remain below rssi_timeout_threshold before the
            user is removed from the queue.
        ws_host:
            Hostname / address the WebSocket server binds to.
        ws_port:
            Port the WebSocket server listens on.
        ui_port:
            Port the HTTP server that serves the static configuration UI listens on.
        uuid_whitelist:
            List of BLE UUIDs the system tracks. Sent to each receiver so it can
            filter out all other advertisements.
    """

    baud_rate: int = 115200
    heartbeat_timeout: float = 5.0
    kalman_process_noise: float = 0.01
    kalman_measurement_noise: float = 2.0
    rolling_window_size: int = 5
    hysteresis: float = 3.0
    rssi_timeout_threshold: float = -85.0
    rssi_timeout_duration: float = 10.0
    ws_host: str = "localhost"
    ws_port: int = 8765
    ui_port: int = 8080
    uuid_whitelist: List[str] = field(default_factory=list)
