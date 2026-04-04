"""
Discovers serial ports that match the naming patterns used by USB-connected
ESP32 devices on macOS and opens a SerialConnection for each one.
"""

import glob
import logging
import queue
import threading
import time
from typing import Dict, List

from controller.serial_connection import SerialConnection

logger = logging.getLogger(__name__)

# Glob patterns matching ESP32 USB serial ports on macOS.
_MACOS_PORT_PATTERNS: List[str] = [
   # "/dev/cu.usbserial*",
   # "/dev/cu.SLAB_USBtoUART*",
   # "/dev/cu.wchusbserial*",
   # "/dev/cu.usbmodem*",
    "/dev/tty.usbserial*",
]

# How often (seconds) the scanner checks for newly connected ports.
_SCAN_INTERVAL = 2.0


class SerialManager:
    """
    Manages a pool of SerialConnection instances — one per detected serial port.

    Attributes:
        baud_rate (int): Baud rate applied to every connection.
        shared_queue (queue.Queue): Queue where parsed messages are deposited.
    """

    def __init__(self, baud_rate: int, shared_queue: queue.Queue):
        """
        Initialise the serial manager.

        Args:
            baud_rate: Serial baud rate forwarded to each SerialConnection.
            shared_queue: Shared inter-thread message queue.
        """
        self.baud_rate = baud_rate
        self.shared_queue = shared_queue

        # _connections establishes a link between port and SerialConnection instance
        self._connections: Dict[str, SerialConnection] = {}
        self._lock = threading.Lock()

        self._running = False
        self._scan_thread: threading.Thread = None

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def start(self):
        """Start the background port scanner thread."""
        self._running = True
        self._scan_thread = threading.Thread(
            target=self._scan_loop,
            name="serial-manager-scanner",
            daemon=True,
        )
        self._scan_thread.start()
        logger.info("SerialManager: port scanner started (interval=%.1fs)", _SCAN_INTERVAL)

    def stop(self):
        """Stop the scanner and close all open serial connections."""
        self._running = False
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=5.0)

        with self._lock:
            for conn in self._connections.values():
                conn.stop()
            self._connections.clear()

        logger.info("SerialManager: all connections closed")

    # ──────────────────────────────────────────────────────────────────────────
    # Outbound messaging
    # ──────────────────────────────────────────────────────────────────────────

    def send_to_port(self, port: str, message: dict):
        """
        Send a message to the receiver connected on the given port.

        Args:
            port: Serial port path.
            message: Message dict to JSON-encode and transmit.
        """
        with self._lock:
            conn = self._connections.get(port)
        if conn:
            conn.send(message)
        else:
            logger.warning(
                "SerialManager: cannot send — no active connection on %s", port
            )

    def get_active_ports(self) -> List[str]:
        """Return a list of currently open serial port paths."""
        with self._lock:
            return [p for p, c in self._connections.items() if c.is_running]

    # ──────────────────────────────────────────────────────────────────────────
    # Background scanner
    # ──────────────────────────────────────────────────────────────────────────

    def _scan_loop(self):
        """Background thread: periodically discover and connect to new ports."""
        logger.debug("SerialManager: scan loop started")
        while self._running:
            self._discover_and_connect()
            time.sleep(_SCAN_INTERVAL)
        logger.debug("SerialManager: scan loop exited")

    def _discover_and_connect(self):
        """
        Get ports matching the ESP32 patterns and open connections for currently untracked ports.
        """
        # Get all Serial devices connected to the controller
        discovered: set = set()
        for pattern in _MACOS_PORT_PATTERNS:
            discovered.update(glob.glob(pattern))

        with self._lock:
            # If a discovered port is newly discovered, establish a new SerialConnection instance
            for port in discovered:
                if port not in self._connections:
                    logger.info("SerialManager: new port discovered — %s", port)
                    conn = SerialConnection(port, self.baud_rate, self.shared_queue)
                    conn.start()
                    self._connections[port] = conn
