"""
Unit tests for serial ingestion using a mocked serial port.

These tests exercise the Controller's message dispatch logic (heartbeat and data
handling) without requiring physical hardware. The serial layer is replaced by
directly enqueuing pre-built messages onto the shared queue, simulating what
SerialConnection._process_line() would produce after parsing.
"""

import logging
import queue
import threading
import time
import pytest

from controller.config import ControllerConfig
from controller.controller import Controller
from controller.serial_connection import SerialConnection


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def cfg():
    return ControllerConfig(
        uuid_whitelist=["uuid-1", "uuid-2"],
        heartbeat_timeout=5.0,
        kalman_process_noise=0.0,
        kalman_measurement_noise=0.0001,
        rolling_window_size=1,
        hysteresis=3.0,
        rssi_timeout_threshold=-85.0,
        rssi_timeout_duration=10.0,
    )


@pytest.fixture()
def controller_and_queue(cfg):
    """
    Build a Controller whose subsystems are patched so it never opens real
    serial ports or WebSocket servers, then inject messages via its internal queue.
    """
    ctrl = Controller(cfg)
    ctrl._user_tracker.entry_buffer_seconds = 0.0

    # Prevent SerialManager from scanning real ports.
    ctrl._serial_manager.start = lambda: None
    ctrl._serial_manager.stop = lambda: None

    # Prevent WebSocket/HTTP servers from binding real sockets.
    ctrl._ws_server.start = lambda: None

    return ctrl, ctrl._queue


def _heartbeat(receiver_id: str, port: str = "/dev/fake0") -> dict:
    return {"type": "heartbeat", "id": receiver_id, "port": port, "timestamp": time.time()}


def _data(receiver_id: str, uuid: str, rssi: float) -> dict:
    return {"type": "data", "id": receiver_id, "uuid": uuid, "rssi": rssi,
            "port": "/dev/fake0", "timestamp": time.time()}


def _pump(ctrl: Controller, q: queue.Queue, count: int):
    """Drain exactly *count* messages from the queue via the controller dispatcher."""
    for _ in range(count):
        msg = q.get(timeout=1.0)
        ctrl._dispatch_message(msg)


# ── Heartbeat handling ────────────────────────────────────────────────────────

class TestHeartbeatHandling:
    def test_first_heartbeat_registers_receiver(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        assert "rec-A" in ctrl._receivers

    def test_first_heartbeat_registers_zone(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        assert "rec-A" in ctrl._zone_manager.get_zones()

    def test_second_heartbeat_updates_timestamp(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        t1 = ctrl._receivers["rec-A"]["last_heartbeat"]

        time.sleep(0.05)
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        t2 = ctrl._receivers["rec-A"]["last_heartbeat"]
        assert t2 > t1

    def test_multiple_receivers_all_registered(self, controller_and_queue):
        ctrl, q = controller_and_queue
        for rid in ("rec-A", "rec-B", "rec-C"):
            q.put(_heartbeat(rid))
        _pump(ctrl, q, 3)
        assert all(r in ctrl._receivers for r in ("rec-A", "rec-B", "rec-C"))

    def test_heartbeat_marks_receiver_active(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        assert ctrl._receivers["rec-A"]["active"] is True


# ── Data handling ─────────────────────────────────────────────────────────────

class TestDataHandling:
    def test_data_message_creates_user(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        q.put(_data("rec-A", "uuid-1", -65.0))
        _pump(ctrl, q, 2)
        assert "uuid-1" in ctrl._user_tracker.get_all_users()

    def test_data_message_assigns_zone_0(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        q.put(_heartbeat("rec-B"))
        q.put(_data("rec-A", "uuid-1", -65.0))
        _pump(ctrl, q, 3)
        zone = ctrl._user_tracker.get_all_users()["uuid-1"]
        assert zone == ctrl._zone_manager.get_zones()[0]

    def test_data_missing_uuid_discarded(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        bad_msg = {"type": "data", "id": "rec-A", "rssi": -65.0,
                   "port": "/dev/fake0", "timestamp": time.time()}
        q.put(bad_msg)
        _pump(ctrl, q, 2)
        assert len(ctrl._user_tracker.get_all_users()) == 0

    def test_data_missing_rssi_discarded(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        bad_msg = {"type": "data", "id": "rec-A", "uuid": "uuid-1",
                   "port": "/dev/fake0", "timestamp": time.time()}
        q.put(bad_msg)
        _pump(ctrl, q, 2)
        assert len(ctrl._user_tracker.get_all_users()) == 0

    def test_data_invalid_rssi_type_discarded(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        bad_msg = {"type": "data", "id": "rec-A", "uuid": "uuid-1",
                   "rssi": "not-a-number", "port": "/dev/fake0",
                   "timestamp": time.time()}
        q.put(bad_msg)
        _pump(ctrl, q, 2)
        assert len(ctrl._user_tracker.get_all_users()) == 0

    def test_message_without_type_discarded(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put({"id": "rec-A", "port": "/dev/fake0", "timestamp": time.time()})
        _pump(ctrl, q, 1)
        assert len(ctrl._receivers) == 0

    def test_message_without_id_discarded(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put({"type": "heartbeat", "port": "/dev/fake0", "timestamp": time.time()})
        _pump(ctrl, q, 1)
        assert len(ctrl._receivers) == 0

    def test_unknown_message_type_is_silently_ignored(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put({"type": "unknown", "id": "rec-A", "port": "/dev/fake0",
               "timestamp": time.time()})
        _pump(ctrl, q, 1)
        # Should not raise and should not register anything
        assert len(ctrl._receivers) == 0

    def test_whitelist_order_priority_used_when_message_has_no_priority(self, controller_and_queue):
        ctrl, q = controller_and_queue
        ctrl._send_lighting = lambda *args, **kwargs: None

        # uuid-1 is earlier in whitelist than uuid-2, so it should outrank uuid-2
        q.put(_heartbeat("rec-A"))
        q.put(_heartbeat("rec-B"))
        q.put(_data("rec-B", "uuid-2", -65.0))
        q.put(_data("rec-A", "uuid-1", -65.0))
        q.put(_data("rec-B", "uuid-1", -55.0))
        _pump(ctrl, q, 5)

        target = ctrl._user_tracker._get_zone_lighting_target("rec-B")
        assert target == "uuid-1"


# ── Receiver heartbeat monitor ────────────────────────────────────────────────

class TestHeartbeatMonitor:
    def test_receiver_marked_inactive_after_timeout(self, controller_and_queue):
        ctrl, q = controller_and_queue
        ctrl.config = ctrl.config.__class__(
            **{**ctrl.config.__dict__, "heartbeat_timeout": 0.1}
        )
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        assert ctrl._receivers["rec-A"]["active"] is True

        # Manually age the heartbeat timestamp
        ctrl._receivers["rec-A"]["last_heartbeat"] -= 1.0

        # Run the monitor directly (not in a thread, to keep the test deterministic)
        now = time.time()
        with ctrl._receivers_lock:
            for rid, state in ctrl._receivers.items():
                if now - state["last_heartbeat"] > ctrl.config.heartbeat_timeout:
                    state["active"] = False

        assert ctrl._receivers["rec-A"]["active"] is False


# ── get_state ─────────────────────────────────────────────────────────────────

class TestGetState:
    def test_get_state_structure(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        state = ctrl.get_state()
        assert state["type"] == "state"
        assert "receivers" in state
        assert "zones" in state
        assert "users_by_zone" in state

    def test_get_state_reflects_registered_receiver(self, controller_and_queue):
        ctrl, q = controller_and_queue
        q.put(_heartbeat("rec-A"))
        _pump(ctrl, q, 1)
        state = ctrl.get_state()
        assert "rec-A" in state["receivers"]

    def test_get_state_reflects_zone_order(self, controller_and_queue):
        ctrl, q = controller_and_queue
        for rid in ("rec-A", "rec-B"):
            q.put(_heartbeat(rid))
        _pump(ctrl, q, 2)
        state = ctrl.get_state()
        assert state["zones"] == ["rec-A", "rec-B"]


def test_serial_connection_start_handles_oserror(monkeypatch, caplog):
    """Invalid port open errors should be caught and not crash the caller."""
    caplog.set_level(logging.ERROR)

    def fake_serial_constructor(*args, **kwargs):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("controller.serial_connection.serial.Serial", fake_serial_constructor)

    q = queue.Queue()
    conn = SerialConnection("/dev/fake", 115200, q)
    conn.start()

    assert "failed to open /dev/fake" in caplog.text
    assert not conn.is_running
