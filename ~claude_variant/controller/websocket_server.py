"""
Starlight System - WebSocket & HTTP Server
==========================================

Provides two servers that power the configuration UI:

1. **WebSocket server** (default: ws://localhost:8765)
   - Broadcasts real-time system state to all connected browser clients.
   - Handles incoming commands from the UI (zone reordering, blink requests).

2. **HTTP file server** (default: http://localhost:8080)
   - Serves the static files in the ``ui/`` directory so the developer can
     open a browser to the configuration UI without a separate web server.

Both servers run as daemon threads with their own event loops so the main
controller thread is never blocked.

Thread-safety note
------------------
``broadcast()`` is called from the controller's main thread. It uses
``asyncio.run_coroutine_threadsafe`` to schedule the async broadcast coroutine
onto the WebSocket server's dedicated event loop — the standard pattern for
bridging synchronous and asyncio code across threads.
"""

import asyncio
import http.server
import json
import logging
import os
import threading
from functools import partial
from typing import TYPE_CHECKING, Set

import websockets
import websockets.legacy.server
import websockets.exceptions

if TYPE_CHECKING:
    from controller.controller import Controller

logger = logging.getLogger(__name__)

# Absolute path to the ui/ directory (one level up from this file's package dir).
_UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")


class WebSocketServer:
    """
    Runs a WebSocket server and an HTTP static-file server for the config UI.

    Attributes:
        controller: Reference to the Controller for state reads and command dispatch.
        host (str): Bind hostname for both servers.
        port (int): WebSocket server port.
        ui_port (int): HTTP server port.
    """

    def __init__(
        self,
        controller: "Controller",
        host: str,
        port: int,
        ui_port: int,
    ):
        """
        Initialise the server pair.

        Args:
            controller: Main Controller instance.
            host: Hostname to bind to (e.g. "localhost").
            port: WebSocket port.
            ui_port: HTTP file-server port.
        """
        self.controller = controller
        self.host = host
        self.port = port
        self.ui_port = ui_port

        # Set of currently connected WebSocket clients.
        # Accessed only within the WS event loop → no extra locking needed.
        self._clients: Set = set()

        self._loop: asyncio.AbstractEventLoop = None
        self._ws_thread: threading.Thread = None
        self._http_thread: threading.Thread = None

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def start(self):
        """Launch the WebSocket and HTTP servers in daemon threads."""
        self._ws_thread = threading.Thread(
            target=self._run_ws,
            name="ws-server",
            daemon=True,
        )
        self._ws_thread.start()

        self._http_thread = threading.Thread(
            target=self._run_http,
            name="http-ui-server",
            daemon=True,
        )
        self._http_thread.start()

        logger.info(
            "WebSocketServer: WS  → ws://%s:%d | UI → http://%s:%d",
            self.host, self.port, self.host, self.ui_port,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API (called from the controller's thread)
    # ──────────────────────────────────────────────────────────────────────────

    def broadcast(self, state: dict):
        """
        Thread-safe push of *state* to all connected WebSocket clients.

        Safe to call from any thread. If no clients are connected or the server
        has not started yet, this is a no-op.

        Args:
            state: State dictionary to serialise and send as JSON.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_broadcast(state), self._loop
            )

    # ──────────────────────────────────────────────────────────────────────────
    # WebSocket server (asyncio)
    # ──────────────────────────────────────────────────────────────────────────

    def _run_ws(self):
        """Entry point for the WebSocket daemon thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve_ws())

    async def _serve_ws(self):
        """Async coroutine: start the WebSocket server and run forever."""
        async with websockets.serve(self._handle_client, self.host, self.port):
            logger.debug("WebSocketServer: WebSocket listening on ws://%s:%d", self.host, self.port)
            await asyncio.Future()  # Block forever until the event loop is stopped

    async def _handle_client(self, websocket):
        """
        Handle the full lifetime of a single WebSocket client connection.

        Sends the initial state snapshot immediately on connection, then listens
        for commands from the browser until the client disconnects.

        Args:
            websocket: The newly connected client.
        """
        self._clients.add(websocket)
        logger.info(
            "WebSocketServer: client connected (total=%d)", len(self._clients)
        )

        try:
            # Immediately push the current state so the UI doesn't start blank.
            await websocket.send(json.dumps(self.controller.get_state()))

            async for raw in websocket:
                try:
                    data = json.loads(raw)
                    self._dispatch_ui_command(data)
                except json.JSONDecodeError:
                    logger.warning("WebSocketServer: received malformed command from UI")

        except websockets.exceptions.ConnectionClosed:
            pass  # Normal disconnection
        finally:
            self._clients.discard(websocket)
            logger.info(
                "WebSocketServer: client disconnected (total=%d)", len(self._clients)
            )

    async def _async_broadcast(self, state: dict):
        """
        Send *state* to all connected clients. Runs inside the WS event loop.

        Dead clients (those that raise ConnectionClosed) are removed from the
        client set automatically.

        Args:
            state: State dict to broadcast.
        """
        if not self._clients:
            return

        message = json.dumps(state)
        dead: Set = set()

        for client in list(self._clients):
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.add(client)

        self._clients -= dead

    def _dispatch_ui_command(self, data: dict):
        """
        Route a command received from the configuration UI to the controller.

        Supported command types:
            ``reorder`` — Update zone ordering.
                Required field: ``order`` (list of receiver IDs).
            ``blink``   — Send a blink command to a receiver.
                Required field: ``receiver_id`` (string).

        Args:
            data: Parsed command dict from the browser.
        """
        cmd_type = data.get("type")

        if cmd_type == "reorder":
            order = data.get("order", [])
            logger.info("WebSocketServer: reorder command — %s", order)
            self.controller.reorder_zones(order)

        elif cmd_type == "blink":
            receiver_id = data.get("receiver_id")
            if receiver_id:
                logger.info("WebSocketServer: blink command — receiver '%s'", receiver_id)
                self.controller.send_blink(receiver_id)
            else:
                logger.warning("WebSocketServer: blink command missing receiver_id")

        else:
            logger.warning("WebSocketServer: unknown UI command type '%s'", cmd_type)

    # ──────────────────────────────────────────────────────────────────────────
    # HTTP file server (stdlib)
    # ──────────────────────────────────────────────────────────────────────────

    def _run_http(self):
        """Entry point for the HTTP UI daemon thread."""
        # Suppress per-request log lines from the default HTTP handler.
        class _QuietHandler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, fmt, *args):  # pylint: disable=arguments-differ
                pass  # Silence request logs; errors still go to logger

            def log_error(self, fmt, *args):
                logger.error("HTTP: " + fmt, *args)

        handler = partial(_QuietHandler, directory=_UI_DIR)
        with http.server.HTTPServer((self.host, self.ui_port), handler) as httpd:
            logger.debug(
                "WebSocketServer: HTTP serving '%s' on port %d", _UI_DIR, self.ui_port
            )
            httpd.serve_forever()
