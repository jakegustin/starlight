"""Tests for the receiver configuration UI server."""

from __future__ import annotations

import json
from urllib import request

from controller import CentralController
from controller.src.config_ui import ReceiverConfigServer
from controller.src.serial_mux import MultiSerialIngester


def test_config_ui_status_and_reorder_endpoint() -> None:
    """Ensure config UI reports status and can update zone ordering."""
    controller = CentralController(["r1", "r2"], allow_dynamic_zones=True)
    ingester = MultiSerialIngester(controller=controller, scan_ports=False)

    # Seed one discovered receiver state.
    ingester._receiver_state = {
        "r1": {
            "id": "r1",
            "port": "/dev/cu.1",
            "online": True,
            "last_seen": 123.0,
        }
    }

    server = ReceiverConfigServer(controller=controller, ingester=ingester, port=0)
    server.start()
    host, port = server.address()

    try:
        with request.urlopen(f"http://{host}:{port}/api/status", timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["zoneOrder"] == ["r1", "r2"]
            assert payload["receivers"][0]["id"] == "r1"

        data = json.dumps({"zoneOrder": ["r2", "r1"]}).encode("utf-8")
        req = request.Request(
            f"http://{host}:{port}/api/zone-order",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["zoneOrder"] == ["r2", "r1"]

        assert controller.zone_order == ["r2", "r1"]
    finally:
        server.stop()


def test_config_ui_blink_endpoint() -> None:
    """Ensure blink endpoint forwards command requests to ingester."""
    controller = CentralController(["r1"], allow_dynamic_zones=True)
    ingester = MultiSerialIngester(controller=controller, scan_ports=False)

    seen: list[tuple[str, int]] = []

    def _fake_blink(receiver_id: str) -> bool:
        seen.append(receiver_id)
        return True

    ingester.request_receiver_blink = _fake_blink  # type: ignore[method-assign]

    server = ReceiverConfigServer(controller=controller, ingester=ingester, port=0)
    server.start()
    host, port = server.address()

    try:
        data = json.dumps({"receiverId": "r1"}).encode("utf-8")
        req = request.Request(
            f"http://{host}:{port}/api/blink",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["status"] == "ok"

        assert seen == ["r1"]
    finally:
        server.stop()
