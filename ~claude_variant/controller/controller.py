"""
Starlight System - Central Controller
=======================================

The Controller class is the top-level orchestrator that wires all subsystems
together:

    SerialManager   — discovers serial ports and ingests messages from receivers
    RSSIProcessor   — Kalman-filters and rolling-averages raw RSSI readings
    UserTracker     — assigns users to zones, handles advancement and eviction
    ZoneManager     — maintains the ordered zone (receiver) list
    WebSocketServer — serves the configuration UI and broadcasts live state

Message flow
------------
    ESP32 firmware
        ↓ Serial JSON (heartbeat / data)
    SerialConnection (per port, threaded)
        ↓ parsed + timestamped dict
    shared queue.Queue
        ↓ consumed by _process_loop (main thread)
    Controller._dispatch_message
        ├─ heartbeat → _handle_heartbeat → ZoneManager, UUID whitelist push
        └─ data      → _handle_data      → UserTracker → RSSIProcessor

State is broadcast to the UI after every state-changing event.
"""

import logging
import queue
import threading
import time
from typing import Dict, List, Optional

from controller.config import ControllerConfig
from controller.rssi_processor import RSSIProcessor
from controller.serial_manager import SerialManager
from controller.user_tracker import UserTracker
from controller.websocket_server import WebSocketServer
from controller.zone_manager import ZoneManager

logger = logging.getLogger(__name__)

# Expected receiver heartbeat cadence (seconds). Used to calculate whether a
# receiver has missed at least 2 consecutive heartbeats before marking inactive.
_HEARTBEAT_CADENCE = 2.0


class Controller:
    """
    Central orchestrator for the Starlight system.

    Responsibilities
    ----------------
    - Dequeue messages from SerialManager and dispatch by type.
    - Register receivers (heartbeat) and send UUID whitelist to new/reconnecting ones.
    - Forward RSSI data to the UserTracker pipeline.
    - Expose command API for the WebSocket server (blink, reorder).
    - Broadcast live state to the configuration UI after every meaningful event.

    Attributes:
        config (ControllerConfig): Immutable system configuration.
    """

    def __init__(self, config: ControllerConfig):
        """
        Initialise the controller and all subsystems.

        Subsystems are constructed here but not started — call ``start()``
        to begin processing.

        Args:
            config: Fully-populated controller configuration.
        """
        self.config = config

        # Shared message queue: all SerialConnection reader threads deposit here;
        # the main thread consumes from here.
        self._queue: queue.Queue = queue.Queue()

        # ── Subsystems ────────────────────────────────────────────────────────
        self._zone_manager = ZoneManager()

        self._rssi_processor = RSSIProcessor(
            process_noise=config.kalman_process_noise,
            measurement_noise=config.kalman_measurement_noise,
            window_size=config.rolling_window_size,
        )

        self._user_tracker = UserTracker(
            rssi_processor=self._rssi_processor,
            zone_manager=self._zone_manager,
            hysteresis=config.hysteresis,
            rssi_timeout_threshold=config.rssi_timeout_threshold,
            rssi_timeout_duration=config.rssi_timeout_duration,
        )

        self._serial_manager = SerialManager(
            baud_rate=config.baud_rate,
            shared_queue=self._queue,
        )

        self._ws_server = WebSocketServer(
            controller=self,
            host=config.ws_host,
            port=config.ws_port,
            ui_port=config.ui_port,
        )

        # ── Receiver registry ─────────────────────────────────────────────────
        # receiver_id → { port, last_heartbeat, active }
        self._receivers: Dict[str, dict] = {}
        self._receivers_lock = threading.RLock()
        # port → receiver_id reverse-lookup
        self._port_to_receiver: Dict[str, str] = {}

        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None

    # ──────────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def start(self):
        """
        Start all subsystems and block in the message processing loop.

        This method does not return until ``stop()`` is called (or the process
        is interrupted).
        """
        self._running = True

        self._serial_manager.start()
        self._ws_server.start()

        # Background thread: checks for receivers that have stopped heartbeating.
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_monitor,
            name="heartbeat-monitor",
            daemon=True,
        )
        self._heartbeat_thread.start()

        logger.info("Controller: all subsystems started — entering processing loop")
        self._process_loop()

    def stop(self):
        """Signal all subsystems to shut down gracefully."""
        self._running = False
        self._serial_manager.stop()
        logger.info("Controller: stopped")

    # ──────────────────────────────────────────────────────────────────────────
    # Message processing
    # ──────────────────────────────────────────────────────────────────────────

    def _process_loop(self):
        """
        Main blocking loop: dequeue and dispatch messages until stopped.

        Uses a timeout on queue.get() so the loop can check self._running
        periodically even when no messages are arriving.
        """
        while self._running:
            try:
                message = self._queue.get(timeout=0.5)
                self._dispatch_message(message)
            except queue.Empty:
                continue
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Controller: unhandled error in process loop — %s", exc, exc_info=True
                )

    def _dispatch_message(self, message: dict):
        """
        Route an incoming message to its handler based on the ``type`` field.

        Messages without both ``type`` and ``id`` fields are silently discarded.

        Args:
            message: Parsed, timestamped message dict from a receiver.
        """
        msg_type = message.get("type")
        receiver_id = message.get("id")

        if not msg_type or not receiver_id:
            logger.debug("Controller: discarding message — missing type or id: %s", message)
            return

        if msg_type == "heartbeat":
            self._handle_heartbeat(receiver_id, message)
        elif msg_type == "data":
            self._handle_data(receiver_id, message)
        else:
            logger.debug(
                "Controller: unknown message type '%s' from '%s'", msg_type, receiver_id
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Message handlers
    # ──────────────────────────────────────────────────────────────────────────

    def _handle_heartbeat(self, receiver_id: str, message: dict):
        """
        Process a heartbeat message from a BLE receiver.

        - Registers new receivers with the ZoneManager.
        - Updates last-seen timestamp.
        - Sends the UUID whitelist to new or reconnecting receivers.
        - Broadcasts updated state to the UI.

        Args:
            receiver_id: Receiver's self-reported ID string.
            message: Full annotated message dict (includes ``port``, ``timestamp``).
        """
        port = message.get("port")
        now = message.get("timestamp", time.time())

        with self._receivers_lock:
            is_new = receiver_id not in self._receivers
            was_inactive = (
                not is_new
                and not self._receivers[receiver_id].get("active", True)
            )

            self._receivers[receiver_id] = {
                "port": port,
                "last_heartbeat": now,
                "active": True,
            }
            if port:
                self._port_to_receiver[port] = receiver_id

        if is_new:
            self._zone_manager.register_receiver(receiver_id)
            logger.info(
                "Controller: new receiver registered — id='%s' port=%s",
                receiver_id, port,
            )

        # Send whitelist to any receiver that is joining (or re-joining) the system.
        if is_new or was_inactive:
            self._send_uuid_whitelist(receiver_id)

        self._broadcast_state()

    def _handle_data(self, receiver_id: str, message: dict):
        """
        Process a BLE advertisement data message.

        Forwards the UUID + RSSI to the UserTracker pipeline and broadcasts
        updated state to the UI.

        Args:
            receiver_id: Receiver that captured the advertisement.
            message: Full annotated message dict (must include ``uuid`` and ``rssi``).
        """
        uuid = message.get("uuid")
        rssi = message.get("rssi")

        if uuid is None or rssi is None:
            logger.debug(
                "Controller: data message from '%s' missing uuid/rssi — discarding",
                receiver_id,
            )
            return

        try:
            rssi = float(rssi)
        except (ValueError, TypeError):
            logger.warning(
                "Controller: invalid rssi '%s' from '%s' — discarding", rssi, receiver_id
            )
            return

        self._user_tracker.process_rssi(uuid, receiver_id, rssi)
        self._broadcast_state()

    # ──────────────────────────────────────────────────────────────────────────
    # Heartbeat monitor
    # ──────────────────────────────────────────────────────────────────────────

    def _heartbeat_monitor(self):
        """
        Background thread: marks receivers inactive when heartbeats stop arriving.

        A receiver is only marked inactive after it has missed at least 2 expected
        heartbeat intervals (config.heartbeat_timeout should be > 2 × cadence).
        """
        while self._running:
            time.sleep(_HEARTBEAT_CADENCE)
            now = time.time()
            changed = False

            with self._receivers_lock:
                for receiver_id, state in self._receivers.items():
                    if not state.get("active", False):
                        continue
                    elapsed = now - state.get("last_heartbeat", 0)
                    if elapsed > self.config.heartbeat_timeout:
                        state["active"] = False
                        changed = True
                        logger.warning(
                            "Controller: receiver '%s' inactive — no heartbeat for %.1f s",
                            receiver_id, elapsed,
                        )

            if changed:
                self._broadcast_state()

    # ──────────────────────────────────────────────────────────────────────────
    # Outbound commands to receivers
    # ──────────────────────────────────────────────────────────────────────────

    def send_blink(self, receiver_id: str):
        """
        Send a blink command to a specific receiver.

        Args:
            receiver_id: Target receiver ID.
        """
        port = self._get_port(receiver_id)
        if port:
            self._serial_manager.send_to_port(
                port, {"type": "command", "command": "blink"}
            )
            logger.info(
                "Controller: blink sent to receiver '%s' on port %s", receiver_id, port
            )
        else:
            logger.warning(
                "Controller: blink failed — receiver '%s' not found or no port", receiver_id
            )

    def _send_uuid_whitelist(self, receiver_id: str):
        """
        Push the configured UUID whitelist to a specific receiver.

        Args:
            receiver_id: Target receiver ID.
        """
        port = self._get_port(receiver_id)
        if port:
            self._serial_manager.send_to_port(port, {
                "type": "uuid",
                "uuids": self.config.uuid_whitelist,
            })
            logger.info(
                "Controller: whitelist (%d UUIDs) sent to receiver '%s'",
                len(self.config.uuid_whitelist), receiver_id,
            )

    def _get_port(self, receiver_id: str) -> Optional[str]:
        """Look up the serial port path for a receiver ID."""
        with self._receivers_lock:
            return self._receivers.get(receiver_id, {}).get("port")

    # ──────────────────────────────────────────────────────────────────────────
    # Configuration UI interface
    # ──────────────────────────────────────────────────────────────────────────

    def reorder_zones(self, ordered_receiver_ids: List[str]):
        """
        Update zone ordering (called by WebSocket server on UI reorder command).

        Args:
            ordered_receiver_ids: New desired zone ordering.
        """
        self._zone_manager.set_order(ordered_receiver_ids)
        logger.info("Controller: zone order updated → %s", ordered_receiver_ids)
        self._broadcast_state()

    def get_state(self) -> dict:
        """
        Build a complete, serialisable state snapshot for the configuration UI.

        Returns:
            Dict with keys: ``type``, ``receivers``, ``zones``, ``users_by_zone``.
        """
        with self._receivers_lock:
            receivers_snapshot = {
                rid: {
                    "id": rid,
                    "port": state.get("port"),
                    "active": state.get("active", False),
                    "last_heartbeat": state.get("last_heartbeat"),
                }
                for rid, state in self._receivers.items()
            }

        return {
            "type": "state",
            "receivers": receivers_snapshot,
            "zones": self._zone_manager.get_zones(),
            "users_by_zone": self._user_tracker.get_users_by_zone(),
        }

    def _broadcast_state(self):
        """Push the current state to all connected configuration UI clients."""
        self._ws_server.broadcast(self.get_state())
