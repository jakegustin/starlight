"""
Multiplexer to receive multiple serial data streams as one data stream in the controller
"""

from __future__ import annotations

import glob
import json
import os
import queue
import threading
import time
from typing import Iterable, Optional

import serial

from .core import CentralController

def _default_usb_patterns() -> list[str]:
    """
    Return the typical serial port glob patterns
    """
    return ["/dev/tty.*", "/dev/cu.usb*"]

class MultiSerialIngester:
    """
    Scan for and ingest data from multiple serial receivers.

    Attributes
    ----------
    controller : CentralController
        The central controller instance to ingest data into.
    ports : Optional[Iterable[str]]
        A starting list of ports (devices) to read serial data from.
    baudrate : int
        The baudrate to establish a serial connection at.
    scan_ports : bool
        Enable/disable discovery of new or removed serial connections.
    scan_patterns : Optional[Iterable[str]]
        Glob patterns of devices to scan to detect serial connections.
    scan_interval : float
        The interval at which to rescan for new/removed serial connections.
    verbose : bool
        Enable verbose logging of what ports are discovered and what is read.

    Internal Attributes
    -------------------
    _static_ports : set[str]
        The set of known ports to scan for serial data from, if any.
    _queue : queue.Queue[str]
        A queue to hold the serial data from each receiver.
    _stop_event : threading.Event
        An event flag to indicate whether or not to stop ingesting data.
    _port_threads : dict[str, tuple[threading.Thread, threading.Event]]
        A map of ports/devices to the appropriate thread-related info.
    _scanner_thread : Optional[threading.Thread]
        The thread that scans for new ports/devices, if scan_ports is enabled.
    """

    def __init__(
        self,
        controller: CentralController,
        ports: Optional[Iterable[str]] = None,
        baudrate: int = 115200,
        scan_ports: bool = True,
        scan_patterns: Optional[Iterable[str]] = None,
        scan_interval: float = 2.0,
        verbose: bool = False,
    ) -> None:
        self.controller = controller
        self.baudrate = baudrate
        self.scan_ports = scan_ports
        self.scan_patterns = list(scan_patterns) if scan_patterns else _default_usb_patterns()
        self.scan_interval = scan_interval
        self.verbose = verbose

        # If explicit ports provided, include them
        self._static_ports = set(ports or [])

        # Managed state
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()

        # Define a map from port to a tuple of (thread, stop_event)
        self._port_threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._scanner_thread: Optional[threading.Thread] = None

        # Track which ports have emitted a heartbeat message
        self._valid_ports: set[str] = set()

    def get_ports(self) -> list[str]:
        """Return the set of currently-monitored ports."""
        return sorted(self._port_threads.keys())

    def get_valid_ports(self) -> list[str]:
        """Return the set of ports that have emitted a heartbeat."""
        return sorted(self._valid_ports)

    def _is_allowed_port(self, port: str) -> bool:
        """
        Return True if this port should be monitored
        """
        # First, check if the port/device even exists!
        if not os.path.exists(port):
            return False

        return True

    def _normalize_ports(self, ports: set[str]) -> set[str]:
        """Normalize ports so duplicates (tty/cu) are not opened twice."""
        canonical: dict[str, str] = {}
        for port in ports:
            base = os.path.basename(port)
            # strip leading tty. or cu. to compare devices
            if base.startswith("tty."):
                key = base[4:]
            elif base.startswith("cu."):
                key = base[3:]
            else:
                key = base

            # prefer /dev/cu.* if both exist
            if key in canonical:
                existing = canonical[key]
                if "/dev/cu." in port and "/dev/tty." in existing:
                    canonical[key] = port
                # keep existing otherwise
            else:
                canonical[key] = port
        return set(canonical.values())

    def _scan_ports(self) -> None:
        """Periodically rescan available serial ports and adjust active readers."""
        # Ensure that we should still be scanning
        while not self._stop_event.is_set():

            # Start setting up the list of ports to scan data from with our known ports
            expanded: set[str] = set(self._static_ports)

            # If discovery is enabled, iterate through each matching pattern and add it to the set
            if self.scan_ports:
                for pattern in self.scan_patterns:
                    for match in glob.glob(pattern):

                        # Make sure the match is an actual port and isn't blacklisted!
                        if self._is_allowed_port(match):
                            expanded.add(match)

            # Normalize duplicates (tty/cu pairs) so we don't open the same device twice
            expanded = self._normalize_ports(expanded)

            # Start readers for any newly discovered ports
            for port in expanded - set(self._port_threads):
                if self.verbose:
                    print(f"[MultiSerialIngester] starting reader for {port}")
                stop_ev = threading.Event()
                thread = threading.Thread(target=self._read_port, args=(port, stop_ev), daemon=True)
                thread.start()
                self._port_threads[port] = (thread, stop_ev)

            # Clean up any threads that died (e.g. due to port errors), so we can retry later
            for port, (thread, stop_ev) in list(self._port_threads.items()):
                if not thread.is_alive():
                    if self.verbose:
                        print(f"[MultiSerialIngester] reader thread for {port} has stopped")
                    self._port_threads.pop(port, None)

            if self.verbose:
                print(f"[MultiSerialIngester] monitoring ports: {sorted(self._port_threads)}")

            # Stop readers for ports that no longer exist
            for port in list(self._port_threads):
                if port not in expanded:
                    if self.verbose:
                        print(f"[MultiSerialIngester] stopping reader for {port}")
                    thread, stop_ev = self._port_threads.pop(port)
                    stop_ev.set()
                    thread.join(timeout=1)

            # Pause to allow the interval to elapse
            time.sleep(self.scan_interval)

    def _read_port(self, port: str, stop_event: threading.Event) -> None:
        """Reads data from a serial connection specified by a port parameter."""
        while not self._stop_event.is_set() and not stop_event.is_set():
            try:
                with serial.Serial(port, self.baudrate, timeout=1) as ser:
                    buffer = b""
                    while not self._stop_event.is_set() and not stop_event.is_set():
                        # Read whatever is available (or at least 1 byte). This avoids
                        # partial-line issues that arise from relying solely on readline().
                        data = ser.read(ser.in_waiting or 1)
                        if not data:
                            continue

                        buffer += data
                        parts = buffer.split(b"\n")
                        buffer = parts.pop()  # remainder after the last newline

                        for raw in parts:
                            line = raw.decode("utf-8", errors="replace").strip()
                            if not line:
                                continue

                            # Parse the JSON once so we can detect heartbeat messages.
                            try:
                                payload = json.loads(line)
                            except Exception:
                                if self.verbose:
                                    print(f"[MultiSerialIngester] dropping invalid line: {line}")
                                continue

                            # Only treat the port as valid after it sends a heartbeat.
                            if payload.get("type") == "heartbeat":
                                self._valid_ports.add(port)

                                # If the controller is set to allow dynamic zones, add this
                                # receiver (by its logical id) to the zone order.
                                if getattr(self.controller, "allow_dynamic_zones", False):
                                    receiver_id = payload.get("id")
                                    if isinstance(receiver_id, str):
                                        self.controller.add_zone(receiver_id)

                            if port not in self._valid_ports:
                                continue

                            self._queue.put(line)

                    # Exit if stop was requested
                    return

            except Exception as e:
                msg = str(e)

                # When the device is unplugged, the OS may return "Device not configured".
                # Treat this as a terminal error so the thread can stop and be restarted later.
                if "Device not configured" in msg:
                    if self.verbose:
                        print(f"[MultiSerialIngester] port={port} disappeared")
                    return

                # Any other error is treated as fatal for this thread.
                if self.verbose:
                    print(f"[MultiSerialIngester] port={port} error: {e}")
                return

    def run_forever(self) -> None:
        """
        Start reading from all configured ports until interrupted
        """
        # Start the scanner thread if we're supposed to be discovering new ports
        if self.scan_ports:
            self._scanner_thread = threading.Thread(target=self._scan_ports, daemon=True)
            self._scanner_thread.start()
        else:
            # If scanning is disabled, just set up the known ports
            for port in list(self._static_ports):
                stop_ev = threading.Event()
                thread = threading.Thread(target=self._read_port, args=(port, stop_ev), daemon=True)
                thread.start()
                self._port_threads[port] = (thread, stop_ev)

        try:
            # For each entry in the queue, allow the controller to ingest it.
            # Continue until the global stop flag is set.
            while not self._stop_event.is_set():
                try:
                    line = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self.verbose:
                    print(f"[MultiSerialIngester] {line}")
                self.controller.ingest(line)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        """
        Stop reading from all ports and terminate scanner
        """
        # This sets the global stop event flag, which our logic uses to stop execution
        self._stop_event.set()
        if self._scanner_thread is not None:
            self._scanner_thread.join(timeout=1)

        # Set the stop flag for individual threads as well
        for thread, stop_ev in list(self._port_threads.values()):
            stop_ev.set()
            thread.join(timeout=1)

        self._port_threads.clear()
