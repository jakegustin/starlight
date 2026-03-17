"""Simple web configuration UI for receiver ordering and online status."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .core import CentralController
from .serial_mux import MultiSerialIngester


_PAGE = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Starlight Receiver Config</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }
      .wrap { max-width: 760px; margin: 1.5rem auto; padding: 0 1rem; }
      h1 { margin: 0 0 .5rem; }
      .muted { color: #94a3b8; margin-bottom: 1rem; }
      .card { background: #111827; border: 1px solid #334155; border-radius: 10px; padding: 1rem; }
      ul { list-style: none; margin: 0; padding: 0; }
      li { display: grid; grid-template-columns: auto 1fr auto auto auto; gap: .5rem; align-items: center; padding: .55rem 0; border-bottom: 1px solid #1e293b; }
      li:last-child { border-bottom: 0; }
      .dot { width: .65rem; height: .65rem; border-radius: 999px; display: inline-block; }
      .on { background: #22c55e; }
      .off { background: #ef4444; }
      button { background: #1e293b; color: #e2e8f0; border: 1px solid #475569; border-radius: 8px; padding: .4rem .7rem; cursor: pointer; }
      button:hover { background: #334155; }
      .save { margin-top: .8rem; background: #2563eb; border-color: #3b82f6; }
      .save:hover { background: #1d4ed8; }
      .tiny { font-size: .85rem; color: #94a3b8; }
      .ok { color: #22c55e; }
      .err { color: #f87171; }
    </style>
  </head>
  <body>
    <div class=\"wrap\">
      <h1>Receiver Configuration</h1>
      <div class=\"muted\">Online status updates automatically. Reorder updates are saved immediately.</div>
      <div class=\"card\">
        <ul id=\"list\"></ul>
        <div id=\"msg\" class=\"tiny\" style=\"margin-top:.5rem\"></div>
      </div>
    </div>
    <script>
      let zoneOrder = [];
      let statusById = {};
      let saveTimer = null;
      let saveInFlight = false;

      function move(idx, delta) {
        const j = idx + delta;
        if (j < 0 || j >= zoneOrder.length) return;
        [zoneOrder[idx], zoneOrder[j]] = [zoneOrder[j], zoneOrder[idx]];
        render();
        scheduleSave();
      }

      function scheduleSave() {
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(() => {
          save().catch(() => {});
        }, 150);
      }

      function render() {
        const list = document.getElementById('list');
        list.innerHTML = '';
        zoneOrder.forEach((id, idx) => {
          const s = statusById[id] || { online: false, port: 'unknown' };
          const li = document.createElement('li');
          li.innerHTML = `
            <span class=\"dot ${s.online ? 'on' : 'off'}\"></span>
            <div>
              <div>${id}</div>
              <div class=\"tiny\">${s.online ? 'online' : 'offline'} · port: ${s.port ?? 'unknown'}</div>
            </div>
            <button ${!s.online ? 'disabled' : ''}>Blink</button>
            <button ${idx === 0 ? 'disabled' : ''}>↑</button>
            <button ${idx === zoneOrder.length - 1 ? 'disabled' : ''}>↓</button>
            <span class=\"tiny\">${idx + 1}</span>
          `;
          const buttons = li.querySelectorAll('button');
          buttons[0].onclick = () => blink(id);
          buttons[1].onclick = () => move(idx, -1);
          buttons[2].onclick = () => move(idx, +1);
          list.appendChild(li);
        });
      }

      async function blink(receiverId) {
        const msg = document.getElementById('msg');
        const res = await fetch('/api/blink', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ receiverId })
        });
        if (!res.ok) {
          msg.className = 'tiny err';
          msg.textContent = `Blink request failed for ${receiverId}`;
          return;
        }
        msg.className = 'tiny ok';
        msg.textContent = `Blink request sent to ${receiverId}`;
      }

      async function refresh() {
        const res = await fetch('/api/status');
        const data = await res.json();
        statusById = {};
        (data.receivers || []).forEach(r => statusById[r.id] = r);

        const merged = [...(data.zoneOrder || [])];
        Object.keys(statusById).forEach(id => {
          if (!merged.includes(id)) merged.push(id);
        });
        zoneOrder = merged;
        render();
      }

      async function save() {
        const msg = document.getElementById('msg');
        if (saveInFlight) return;
        saveInFlight = true;
        try {
          const res = await fetch('/api/zone-order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ zoneOrder })
          });
          if (!res.ok) {
            msg.className = 'tiny err';
            msg.textContent = 'Failed to save order (will retry on next change)';
            return;
          }
          const data = await res.json();
          zoneOrder = data.zoneOrder || zoneOrder;
          msg.className = 'tiny ok';
          msg.textContent = 'Order saved automatically';
          render();
        } finally {
          saveInFlight = false;
        }
      }

      refresh();
      setInterval(() => refresh().catch(() => {}), 1000);
    </script>
  </body>
</html>
"""


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

            def do_GET(self) -> None:  # pylint: disable=invalid-name
                if self.path in ("/", "/index.html"):
                    body = _PAGE.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
