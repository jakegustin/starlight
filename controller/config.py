"""
Defines the ControllerConfig dataclass with all tunable parameters for the central controller. 
Every subsystem receives a reference to one shared config instance.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ControllerConfig:
    """
    Central Controller Configuration

    Attributes:
        baud_rate:
            Serial baud rate for communicating with BLE receivers. Ignored for Wifi communications.
        heartbeat_timeout:
            Seconds without a heartbeat before a receiver is considered inactive.
        kalman_process_noise:
            Kalman filter process noise covariance (Q). Higher = more responsive to sudden RSSI changes.
        kalman_measurement_noise:
            Kalman filter measurement noise covariance (R). Higher = smoother, but slower estimate.
        rolling_window_size:
            Number of Kalman-filtered RSSI samples in rolling average per (UUID, receiver) pair. Must be >= 1.
        hysteresis:
            RSSI delta (dBm) the next zone must exceed over the current zone before advancing the user.
        raw_mode:
            When True, skip Kalman filtering and rolling averaging — raw RSSI is used directly.
            Useful for demonstrations showing signal noise.
        no_ratchet:
            When True, users can move to any zone (not just forward) based on which has the strongest signal.
            Hysteresis still applies to prevent thrashing.
        live_plot:
            When True, stream RSSI samples to the UI so receiver plots can be drawn live.
        rssi_timeout_threshold:
            RSSI floor (dBm) where if a user's average drops below this value, the eviction timer starts.
        rssi_timeout_duration:
            Seconds the RSSI average must remain below rssi_timeout_threshold before the removal from queue.
        ws_host:
            WebSocket hostname / address to bind to.
        ws_port:
            Port the WebSocket server listens on. Cannot be the same as ui_port.
        ui_port:
            Port the HTTP server that serves the UI listens on. Cannot be the same as ws_port.
        uuid_whitelist:
            Path to list of BLE UUIDs the system tracks.
    """

    baud_rate: int = 115200
    heartbeat_timeout: float = 5.0
    kalman_process_noise: float = 5.0
    kalman_measurement_noise: float = 2.0
    rolling_window_size: int = 5
    hysteresis: float = 3.0
    rssi_timeout_threshold: float = -85.0
    rssi_timeout_duration: float = 10.0
    ws_host: str = "localhost"
    ws_port: int = 8765
    ui_port: int = 8080
    uuid_whitelist: List[str] = field(default_factory=list)
    raw_mode: bool = False
    no_ratchet: bool = False
    live_plot: bool = False
    rssi_csv_log: Optional[str] = None
