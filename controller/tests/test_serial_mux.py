"""Unit tests for the serial multiplexer (MultiSerialIngester)."""

from __future__ import annotations

import threading
import time

import pytest

from controller.src.serial_mux import MultiSerialIngester


class _FakeController:
    """A minimal controller substitute that records ingested lines."""

    def __init__(self) -> None:
        self.ingested: list[str] = []

    def ingest(self, line: str) -> None:
        self.ingested.append(line)


class _DummySerial:
    """A simple fake `serial.Serial` context manager."""

    def __init__(self, port: str, baudrate: int, timeout: float) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._lines = [b"{" + b'"id":"' + port.encode() + b'","ts":1,"uuid":"u","rssi":-50}\n']
        self._buffer = b""
        self.closed = False

    def __enter__(self) -> "_DummySerial":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        # No more data, sleep a tiny amount to avoid busy-looping
        time.sleep(0.01)
        return b""

    def read(self, size: int = 1) -> bytes:
        # Emulate non-blocking read behavior.
        if not self._buffer and self._lines:
            self._buffer = self._lines.pop(0)
        if not self._buffer:
            time.sleep(0.01)
            return b""
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)


@pytest.fixture(autouse=True)
def patch_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch serial.Serial and os.path.exists to support fake ports."""

    class DummySerialModule:
        Serial = _DummySerial

    monkeypatch.setattr("controller.src.serial_mux.serial", DummySerialModule)
    monkeypatch.setattr("controller.src.serial_mux.os.path.exists", lambda path: True)


def test_only_ingests_from_heartbeat_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure only ports that emit a heartbeat message are ingested from."""
    fake = _FakeController()

    monkeypatch.setattr("controller.src.serial_mux.glob.glob", lambda pattern: ["/dev/tty.A"])

    class HeartbeatSerial(_DummySerial):
        def __init__(self, port: str, baudrate: int, timeout: float) -> None:
            super().__init__(port, baudrate, timeout)
            # first message is non-heartbeat, second is heartbeat
            self._lines = [
                b"{\"id\":\"/dev/tty.A\", \"type\": \"data\"}\n",
                b"{\"id\":\"/dev/tty.A\", \"type\": \"heartbeat\"}\n",
            ]

    monkeypatch.setattr("controller.src.serial_mux.serial.Serial", HeartbeatSerial)

    ingester = MultiSerialIngester(
        controller=fake,
        ports=[],
        scan_ports=True,
        scan_patterns=["/dev/tty.*"],
        scan_interval=0.01,
    )

    t = threading.Thread(target=ingester.run_forever, daemon=True)
    t.start()

    time.sleep(0.1)
    ingester.stop()
    t.join(timeout=1)

    # Only the heartbeat line should have made it through
    assert any('"type": "heartbeat"' in line for line in fake.ingested)
    assert all('"type": "data"' not in line for line in fake.ingested)


def test_scan_detects_port_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the scanner detaches from ports that disappear."""
    fake = _FakeController()

    # Glob will return a port once, then stop returning it to simulate unplug.
    call_count = {"n": 0}

    def _glob(pattern: str) -> list[str]:
        call_count["n"] += 1
        if call_count["n"] < 4:
            return ["/dev/tty.D"]
        return []

    monkeypatch.setattr("controller.src.serial_mux.glob.glob", _glob)

    ingester = MultiSerialIngester(
        controller=fake,
        ports=[],
        scan_ports=True,
        scan_patterns=["/dev/tty.*"],
        scan_interval=0.01,
    )

    t = threading.Thread(target=ingester.run_forever, daemon=True)
    t.start()

    # Wait for the scanner to discover the port
    deadline = time.time() + 1.0
    while time.time() < deadline and "/dev/tty.D" not in ingester._port_threads:
        time.sleep(0.01)
    assert "/dev/tty.D" in ingester._port_threads

    # Wait until the port should be removed by the scanner
    deadline = time.time() + 1.0
    while time.time() < deadline and "/dev/tty.D" in ingester._port_threads:
        time.sleep(0.01)

    assert "/dev/tty.D" not in ingester._port_threads

    ingester.stop()
    t.join(timeout=1)


def test_static_ports_read_and_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure non-scanning mode reads from explicitly provided ports."""
    fake = _FakeController()

    class HeartbeatSerial(_DummySerial):
        def __init__(self, port: str, baudrate: int, timeout: float) -> None:
            super().__init__(port, baudrate, timeout)
            self._lines = [b"{\"id\":\"/dev/tty.C\", \"type\": \"heartbeat\"}\n"]

    monkeypatch.setattr("controller.src.serial_mux.serial.Serial", HeartbeatSerial)

    ingester = MultiSerialIngester(
        controller=fake,
        ports=["/dev/tty.C"],
        scan_ports=False,
        scan_interval=0.01,
    )

    t = threading.Thread(target=ingester.run_forever, daemon=True)
    t.start()

    time.sleep(0.1)
    ingester.stop()
    t.join(timeout=1)

    assert any('"type": "heartbeat"' in line for line in fake.ingested)


def test_duplicate_tty_cu_ports_are_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tty/cu duplicates only result in one reader thread."""
    fake = _FakeController()

    monkeypatch.setattr(
        "controller.src.serial_mux.glob.glob",
        lambda pattern: ["/dev/tty.usbserial-3", "/dev/cu.usbserial-3"],
    )

    ingester = MultiSerialIngester(
        controller=fake,
        ports=[],
        scan_ports=True,
        scan_patterns=["/dev/*"],
        scan_interval=0.01,
    )

    t = threading.Thread(target=ingester.run_forever, daemon=True)
    t.start()

    deadline = time.time() + 1.0
    while time.time() < deadline and not ingester._port_threads:
        time.sleep(0.01)

    assert len(ingester._port_threads) == 1
    assert any("/dev/cu.usbserial-3" in p for p in ingester._port_threads)

    ingester.stop()
    t.join(timeout=1)


def test_invalid_json_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure invalid JSON lines are discarded and valid ones are ingested."""
    fake = _FakeController()

    monkeypatch.setattr("controller.src.serial_mux.glob.glob", lambda pattern: ["/dev/tty.X"])

    class BadSerial(_DummySerial):
        def __init__(self, port: str, baudrate: int, timeout: float) -> None:
            super().__init__(port, baudrate, timeout)
            self._lines = [
                b"not json\n",
                b"{\"id\":\"/dev/tty.X\", \"type\": \"heartbeat\"}\n",
            ]

    monkeypatch.setattr("controller.src.serial_mux.serial.Serial", BadSerial)

    ingester = MultiSerialIngester(
        controller=fake,
        ports=[],
        scan_ports=True,
        scan_patterns=["/dev/tty.*"],
        scan_interval=0.01,
    )

    t = threading.Thread(target=ingester.run_forever, daemon=True)
    t.start()

    time.sleep(0.1)
    ingester.stop()
    t.join(timeout=1)

    assert any("/dev/tty.X" in line for line in fake.ingested)
    assert all("not json" not in line for line in fake.ingested)
