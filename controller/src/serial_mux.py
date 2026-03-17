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

        # Define a map from ports to thread/command attributes
        self._port_threads: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._port_commands: dict[str, "queue.Queue[str]"] = {}
        self._scanner_thread: Optional[threading.Thread] = None
        self._state_lock = threading.RLock()

        # Additional port/receiver info
        self._valid_ports: set[str] = set()
        self._port_receiver_ids: dict[str, set[str]] = {}
        self._receiver_state: dict[str, dict[str, object]] = {}

    def get_ports(self) -> list[str]:
        """Return the set of currently-monitored ports."""
        with self._state_lock:
            return sorted(self._port_threads.keys())

    def get_valid_ports(self) -> list[str]:
        """Return the set of ports that have emitted a heartbeat."""
        with self._state_lock:
            return sorted(self._valid_ports)

    def get_receiver_statuses(self) -> list[dict[str, object]]:
        """Return discovered receiver states for UIs and diagnostics."""
        with self._state_lock:
            statuses: list[dict[str, object]] = []
            for receiver_id in sorted(self._receiver_state):
                statuses.append(dict(self._receiver_state[receiver_id]))
            return statuses

    def send_receiver_command(self, receiver_id: str, payload: dict[str, object]) -> bool:
        """
        Queue a JSON command for a known receiver (as long as it is connected to the controller)
        """
        with self._state_lock:
            # Check if the receiver state is available at all so we can send a message
            state = self._receiver_state.get(receiver_id)
            if state is None:
                if self.verbose:
                    print(f"[MSI] no receiver state for {receiver_id}")
                return False

            # Get the appropriate port/device for the receiver if possible
            port = state.get("port")
            if not isinstance(port, str):
                if self.verbose:
                    print(f"[MSI] receiver {receiver_id} has no valid port")
                return False

            # Try getting the commands queue for the port
            commands = self._port_commands.get(port)

            # If the commands queue was not found, attempt a one-off transient write
            if commands is None:
                if self.verbose:
                    print(f"[MSI] no command queue for {port}; attempting transient write")
                try:
                    with serial.Serial(port, self.baudrate, timeout=1) as ser:
                        ser.write(json.dumps(payload).encode("utf-8") + b"\n")
                    if self.verbose:
                        print(f"[MSI] transient command written to {port}: {payload}")
                    return True
                except Exception as exc:
                    if self.verbose:
                        print(f"[MSI] transient command to {port} failed: {exc}")
                    return False

            # Queue was identified: put the payload into the queue instead
            if self.verbose:
                print(f"[MSI] queueing command for {port}: {payload}")
            commands.put(json.dumps(payload) + "\n")
            return True

    def request_receiver_blink(self, receiver_id: str) -> bool:
        """
        Request a receiver to blink its LED for placement identification
        """
        payload = {
            "type": "command",
            "command": "blink",
        }
        if self.verbose:
            print(f"[MSI] blink request for {receiver_id}: {payload}")
        return self.send_receiver_command(receiver_id, payload)

    def _mark_port_offline(self, port: str) -> None:
        """
        Mark any receiver ids seen on this port as offline. Useful if no heartbeat detected
        """
        with self._state_lock:
            self._valid_ports.discard(port)
            for receiver_id in self._port_receiver_ids.pop(port, set()):
                state = self._receiver_state.get(receiver_id)
                if state is not None and state.get("port") == port:
                    state["online"] = False

    def _record_receiver_seen(self, *, port: str, receiver_id: str, online: bool) -> None:
        """
        Record receiver metadata and most-recent visibility timestamp
        """
        now = time.time()
        with self._state_lock:
            self._port_receiver_ids.setdefault(port, set()).add(receiver_id)
            self._receiver_state[receiver_id] = {
                "id": receiver_id,
                "port": port,
                "online": online,
                "last_seen": now,
            }

    def _is_allowed_port(self, port: str) -> bool:
        """
        Return True if this port should be monitored
        """
        # First, check if the port/device even exists!
        if not os.path.exists(port):
            return False

        return True

    def _normalize_ports(self, ports: set[str]) -> set[str]:
        """
        Normalize ports so duplicates (tty/cu) are not opened twice
        """
        canonical: dict[str, str] = {}
        for port in ports:
            # Gets the device/port name without the leading "/dev", for instance
            base = os.path.basename(port)

            # Remove leading tty. or cu. if applicable
            if base.startswith("tty."):
                key = base[4:]
            elif base.startswith("cu."):
                key = base[3:]
            else:
                key = base

            # prefer /dev/cu.* if both exist as a tiebreaker condition
            if key in canonical:
                existing = canonical[key]
                if "/dev/cu." in port and "/dev/tty." in existing:
                    canonical[key] = port

            # Otherwise, can just use the port as-is
            else:
                canonical[key] = port

        return set(canonical.values())

    def _scan_ports(self) -> None:
        """
        Periodically rescan available serial ports and adjust active readers
        """
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
                    print(f"[MSI] starting reader for {port}")
                stop_ev = threading.Event()
                command_queue: "queue.Queue[str]" = queue.Queue()
                thread = threading.Thread(
                    target=self._read_port,
                    args=(port, stop_ev, command_queue),
                    daemon=True,
                )
                thread.start()
                self._port_threads[port] = (thread, stop_ev)
                self._port_commands[port] = command_queue

            # Clean up any threads that died (e.g. due to port errors), so we can retry later
            for port, (thread, stop_ev) in list(self._port_threads.items()):
                if not thread.is_alive():
                    if self.verbose:
                        print(f"[MSI] reader thread for {port} has stopped")
                    self._port_threads.pop(port, None)
                    self._port_commands.pop(port, None)
                    self._mark_port_offline(port)

            if self.verbose:
                print(f"[MSI] monitoring ports: {sorted(self._port_threads)}")

            # Stop readers for ports that no longer exist
            for port in list(self._port_threads):
                if port not in expanded:
                    if self.verbose:
                        print(f"[MSI] stopping reader for {port}")
                    thread, stop_ev = self._port_threads.pop(port)
                    self._port_commands.pop(port, None)
                    stop_ev.set()
                    thread.join(timeout=1)
                    self._mark_port_offline(port)

            # Pause to allow the interval to elapse
            time.sleep(self.scan_interval)

    def _drain_command_queue(
        self,
        port: str,
        command_queue: "queue.Queue[str]",
        ser: serial.Serial,
    ) -> None:
        """Write any queued outbound commands to the open serial connection."""
        while True:
            try:
                outbound = command_queue.get_nowait()
            except queue.Empty:
                return

            if self.verbose:
                print(f"[MSI] writing command to {port}: {outbound.strip()}")
            ser.write(outbound.encode("utf-8"))

    def _handle_read_line(self, port: str, line: str) -> None:
        """Parse and dispatch one decoded serial line."""
        if not line:
            return

        if self.verbose:
            print(f"[MultiSerialIngester:{port}] recv: {line}")

        # If the line isn't a valid JSON, no point in processing it
        try:
            payload = json.loads(line)
        except Exception:
            if self.verbose:
                print(f"[MultiSerialIngester:{port}] dropping invalid line: {line}")
            return

        # If it's a heartbeat, we can add the device as a receiver
        if payload.get("type") == "heartbeat":
            self._valid_ports.add(port)

            # If dynamic scanning is enabled, we can assign the device to a zone too
            if getattr(self.controller, "allow_dynamic_zones", False):
                receiver_id = payload.get("id")
                if isinstance(receiver_id, str):
                    self.controller.add_zone(receiver_id)

            # Update internal data structures to indicate the receiver is online
            receiver_id = payload.get("id")
            if isinstance(receiver_id, str):
                self._record_receiver_seen(
                    port=port,
                    receiver_id=receiver_id,
                    online=True,
                )

        # For valid, non-heartbeat messages, we can still acknowledge the device is online
        receiver_id = payload.get("id")
        if isinstance(receiver_id, str) and port in self._valid_ports:
            self._record_receiver_seen(
                port=port,
                receiver_id=receiver_id,
                online=True,
            )

        # If not in dynamic scanning mode and the device is ineligible, don't process the message
        if port not in self._valid_ports:
            return

        # Valid receiver: enqueue the message received
        self._queue.put(line)

    def _read_port(
        self,
        port: str,
        stop_event: threading.Event,
        command_queue: "queue.Queue[str]",
    ) -> None:
        """Read all the messages from a given serial connection"""
        # If the controller is done, stop iterating now
        while not self._stop_event.is_set() and not stop_event.is_set():
            try:
                # Open the serial connection and set up a buffer
                with serial.Serial(port, self.baudrate, timeout=1) as ser:
                    buffer = b""
                    while not self._stop_event.is_set() and not stop_event.is_set():

                        # Make sure outbound messages go out before receiving new ones
                        self._drain_command_queue(port, command_queue, ser)

                        # Read some bytes of serial data if possible
                        data = ser.read(ser.in_waiting or 1)
                        if not data:
                            continue

                        # Add the data received to the buffer, splitting by newlines
                        buffer += data
                        parts = buffer.split(b"\n")
                        buffer = parts.pop()

                        # For each line identified, process it
                        for raw in parts:
                            line = raw.decode("utf-8", errors="replace").strip()
                            self._handle_read_line(port, line)

                    return

            # In case the device disconnects or otherwise gets messed up: handle it
            except Exception as exc:
                message = str(exc)

                # Device unplugged or otherwise offline: mark it as such
                if "Device not configured" in message:
                    if self.verbose:
                        print(f"[MSI] port={port} disappeared")
                    self._mark_port_offline(port)
                    return

                # Some other error happened, but we still need to mark it as offline
                if self.verbose:
                    print(f"[MSI] port={port} error: {exc}")
                self._mark_port_offline(port)
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
                command_queue: "queue.Queue[str]" = queue.Queue()
                thread = threading.Thread(
                    target=self._read_port,
                    args=(port, stop_ev, command_queue),
                    daemon=True,
                )
                thread.start()
                self._port_threads[port] = (thread, stop_ev)
                self._port_commands[port] = command_queue

        try:
            # For each entry in the queue, allow the controller to ingest it.
            # Continue until the global stop flag is set.
            while not self._stop_event.is_set():
                try:
                    line = self._queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self.verbose:
                    print(f"[MSI] {line}")
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

        # Update the configuration UI to reflect an offline receiver
        for port in list(self._port_threads.keys()):
            self._mark_port_offline(port)

        self._port_threads.clear()
        self._port_commands.clear()
