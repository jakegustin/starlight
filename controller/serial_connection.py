"""
Manages a single serial port connection to one BLE receiver.
"""

import json
import logging
import queue
import threading
import time
from typing import Optional

import serial

logger = logging.getLogger(__name__)


class SerialConnection:
    """
    Handles a single serial port connection to a BLE receiver.

    Attributes:
        port (str): Serial device path (e.g. "/dev/cu.usbserial-0001").
        baud_rate (int): Serial baud rate.
        shared_queue (queue.Queue): Global message queue shared with the manager.
    """

    def __init__(self, port: str, baud_rate: int, shared_queue: queue.Queue):
        """
        Initialize a serial connection.

        Args:
            port: Device path of the serial port.
            baud_rate: Baud rate (must match the firmware setting).
            shared_queue: Queue onto which parsed messages are placed.
        """
        self.port = port
        self.baud_rate = baud_rate
        self.shared_queue = shared_queue

        self._serial: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def start(self):
        """Open the serial port and launch the reader daemon thread."""
        with self._lock:
            if self._running:
                logger.warning(
                    "SerialConnection: already running on port %s — ignoring start()", self.port
                )
                return

            # Attempt to create the Serial connection, logging errors that come up in the process
            try:
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baud_rate,
                    timeout=1.0,
                )
                logger.info(
                    "SerialConnection: opened %s at %d baud", self.port, self.baud_rate
                )
            except (serial.SerialException, OSError, ValueError) as exc:
                logger.error(
                    "SerialConnection: failed to open %s — %s", self.port, exc
                )
                return

            # Once a Serial connection is established, set up a new thread to listen on that port
            self._running = True
            self._thread = threading.Thread(
                target=self._reader_loop,
                name=f"serial-{self.port}",
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        """Signal the reader thread to stop and close the serial port."""
        with self._lock:
            self._running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
                logger.info("SerialConnection: closed port %s", self.port)

    # ──────────────────────────────────────────────────────────────────────────
    # Outbound messaging
    # ──────────────────────────────────────────────────────────────────────────

    def send(self, message: dict):
        """
        Serialise *message* as JSON and write it to the serial port.

        Args:
            message: Dictionary to send. Automatically converted to JSON with a newline
        """
        with self._lock:
            # Make sure the port actually exists and is open
            if not self._serial or not self._serial.is_open:
                logger.warning(
                    "SerialConnection: cannot send — port %s is not open", self.port
                )
                return
            
            # Attempt to construct and write the payload to the serial destination
            try:
                payload = json.dumps(message) + "\n"
                self._serial.write(payload.encode("utf-8"))
                logger.debug("SerialConnection: → %s: %s", self.port, payload.strip())
            except serial.SerialException as exc:
                logger.error(
                    "SerialConnection: send error on %s — %s", self.port, exc
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Properties
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True if the reader thread is active and the port is open."""
        return self._running

    # ──────────────────────────────────────────────────────────────────────────
    # Internal Reader Loop
    # ──────────────────────────────────────────────────────────────────────────

    def _reader_loop(self):
        """
        Continuously reads lines from the serial port.
        """
        logger.debug("SerialConnection: reader loop started for %s", self.port)
        while self._running:
            try:
                ser = self._serial
                if not ser:
                    break
                raw = ser.readline().decode("utf-8", errors="replace").strip()
                if raw:
                    self._process_line(raw)
            except serial.SerialException as exc:
                logger.error(
                    "SerialConnection: read error on %s — %s (stopping)", self.port, exc
                )
                break
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "SerialConnection: unexpected error on %s — %s", self.port, exc
                )
                break

        self._running = False

        with self._lock:
            if self._serial and self._serial.is_open:
                self._serial.close()
                self._serial = None
                logger.info("SerialConnection: closed port %s after reader exit", self.port)
                
        logger.debug("SerialConnection: reader loop exited for %s", self.port)

    def _process_line(self, raw_line: str):
        """
        Attempt to parse raw_line as JSON and enqueue the result.

        Args:
            raw_line: Raw string read from the serial port (stripped of whitespace).
        """
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            # Invalid JSON, so just forget it
            logger.debug(
                "SerialConnection: discarded malformed message on %s: %r",
                self.port, raw_line[:80],
            )
            return

        # Annotate with ingestion metadata with controller-side timestamp to establish ordering.
        message["timestamp"] = time.time()
        message["port"] = self.port

        # Put the message in the global queue
        self.shared_queue.put(message)
        logger.debug("SerialConnection: ← %s: %s", self.port, message)
