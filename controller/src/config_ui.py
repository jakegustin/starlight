"""Simple web configuration UI for receiver ordering and online status."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core import CentralController
from .serial_mux import MultiSerialIngester


_WEB_DIR = Path(__file__).with_name("config_ui_web")


def _asset_path(name: str) -> Path:
    """Return the absolute path to a bundled web asset."""
    return _WEB_DIR / name


def _read_asset(name: str) -> bytes:
    """Read a bundled web asset as bytes."""
    return _asset_path(name).read_bytes()


def _content_type_for(path: str) -> str:
    """Return the response content type for a static asset."""
    if path.endswith(".html"):
        return "text/html; charset=utf-8"
    if path.endswith(".css"):
        return "text/css; charset=utf-8"
    if path.endswith(".js"):
        return "application/javascript; charset=utf-8"
    return "application/octet-stream"


class ReceiverConfigServer:
    """HTTP server for receiver discovery and ordering controls."""

    def __init__(
        self,
        controller: CentralController,
        ingester: MultiSerialIngester,
        host: str = "127.0.0.1",
        port: int = 5253,
    ) -> None:
        self._controller = controller
        self._ingester = ingester
        self._host = host
        self._port = port
        self._thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None

    def _status_payload(self) -> dict[str, Any]:
        snap = self._controller.snapshot()
        return {
            "zoneOrder": snap["zone_order"],
            "active": snap["zone_active"],
            "receivers": self._ingester.get_receiver_statuses(),
        }

    def start(self) -> None:
        """Start the UI HTTP server in a background thread."""
        if self._thread is not None:
            return

        parent = self

        class Handler(BaseHTTPRequestHandler):
            def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_asset(self, asset_name: str) -> None:
                body = _read_asset(asset_name)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", _content_type_for(asset_name))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # pylint: disable=invalid-name
                if self.path in ("/", "/index.html"):
                    self._send_asset("index.html")
                    return

                if self.path == "/styles.css":
                    self._send_asset("styles.css")
                    return

                if self.path == "/app.js":
                    self._send_asset("app.js")
                    return

                if self.path == "/api/status":
                    self._send_json(parent._status_payload())
                    return

                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # pylint: disable=invalid-name
                if self.path == "/api/blink":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        body = self.rfile.read(length)
                        payload = json.loads(body.decode("utf-8")) if body else {}
                        receiver_id = payload.get("receiverId")
                        if not isinstance(receiver_id, str) or not receiver_id:
                            raise ValueError("receiverId is required")

                        ok = parent._ingester.request_receiver_blink(receiver_id=receiver_id)
                        if not ok:
                            self._send_json(
                                {"error": f"Receiver '{receiver_id}' is not online"},
                                status=HTTPStatus.BAD_REQUEST,
                            )
                            return
                        self._send_json({"status": "ok"}, status=HTTPStatus.OK)
                    except Exception as exc:
                        self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return

                if self.path != "/api/zone-order":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length)
                    payload = json.loads(body.decode("utf-8")) if body else {}
                    zone_order = payload.get("zoneOrder") or []
                    if not isinstance(zone_order, list):
                        raise ValueError("zoneOrder must be a list")
                    updated = parent._controller.set_zone_order([str(z) for z in zone_order])
                except Exception as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return

                self._send_json({"zoneOrder": updated}, status=HTTPStatus.OK)

            def log_message(self, _format: str, *args: object) -> None:
                # Keep CLI output focused on controller data.
                return

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def address(self) -> tuple[str, int]:
        """Return the currently bound host/port pair."""
        if self._httpd is not None:
            host, port = self._httpd.server_address[:2]
            return str(host), int(port)
        return self._host, self._port

    def stop(self) -> None:
        """Stop the UI HTTP server."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
