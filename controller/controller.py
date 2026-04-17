"""
The root of the Starlight Central Controller, orchestrating activites between submodules as needed
"""

import logging
import queue
import threading
import time
from dataclasses import asdict
from typing import Dict, List, Optional

from controller.config import ControllerConfig
from controller.rssi_processor import RSSIProcessor
from controller.serial_manager import SerialManager
from controller.user_tracker import UserTracker
from controller.websocket_server import WebSocketServer
from controller.zone_manager import ZoneManager

logger = logging.getLogger(__name__)

# Interval at which to issue heartbeat (liveliness) requests
_HEARTBEAT_CADENCE = 2.0


class Controller:
    """
    Central orchestrator for the Starlight system
    Handles inbound messages, receiver registration, RSSI forwarding, and UI state updates

    Responsibilities
    ----------------
    - Dequeue messages from SerialManager and dispatch by type.
    - Register receivers (heartbeat) and send UUID whitelist to new/reconnecting ones.
    - Forward RSSI data to the UserTracker pipeline.
    - Expose command API for the WebSocket server (blink, reorder).
    - Broadcast live state to the configuration UI after every meaningful event.

    Attributes:
        config (ControllerConfig): System configuration.
    """

    def __init__(self, config: ControllerConfig):
        """
        Initialise the controller and all subsystems.

        Args:
            config: System configuration.
        """
        self.config = config

        # Priority fallback for inbound data messages that do not explicitly carry
        # a numeric priority field. Earlier UUIDs in the whitelist have higher
        # priority than later UUIDs.
        self._uuid_priority: Dict[str, int] = {
            self._normalise_uuid(uuid): len(config.uuid_whitelist) - idx
            for idx, uuid in enumerate(config.uuid_whitelist)
        }

        # Shared message queue for all SerialConnection reader threads
        self._queue: queue.Queue = queue.Queue()

        # Create central controller susbystem instances with provided config info
        self._zone_manager = ZoneManager()

        self._rssi_processor = RSSIProcessor(
            process_noise=config.kalman_process_noise,
            measurement_noise=config.kalman_measurement_noise,
            window_size=config.rolling_window_size,
            raw_mode=config.raw_mode,
        )

        self._user_tracker = UserTracker(
            rssi_processor=self._rssi_processor,
            zone_manager=self._zone_manager,
            controller=self,
            hysteresis=config.hysteresis,
            rssi_timeout_threshold=config.rssi_timeout_threshold,
            rssi_timeout_duration=config.rssi_timeout_duration,
            no_ratchet=config.no_ratchet,
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

        # _receivers contains info on receiver ports, heartbeats, and status
        self._receivers: Dict[str, dict] = {}
        self._receivers_lock = threading.RLock()

        # The reverse of _receivers, with ports as keys
        self._port_to_receiver: Dict[str, str] = {}

        # Update the main thread's _running flag to indicate that the controller is not yet ready
        self._running = False

        # Initialize the _heartbeat_thread to be nothing. This gets updated later
        self._heartbeat_thread: Optional[threading.Thread] = None

        self._live_plot_enabled = config.live_plot

    @staticmethod
    def _normalise_uuid(uuid: str) -> str:
        """Normalise UUIDs for consistent map lookup."""
        return str(uuid).strip().lower()

    # ──────────────────────────────────────────────────────────────────────────
    # Controller Lifecycle
    # ──────────────────────────────────────────────────────────────────────────

    def start(self):
        """
        Start all subsystems and block in the message processing loop.
        """
        # The controller will now come online!
        self._running = True

        # Properly start Serial communications and the WebSocket server
        self._serial_manager.start()
        self._ws_server.start()

        # Properly create and start the heartbeat thread to check liveliness of receivers
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
        """
        while self._running:
            # Get a message from the queue and send it to the appropriate handler
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

        Args:
            message: Parsed, timestamped message dict from a receiver.
        """
        # Retrieve metadata from the received message
        msg_type = message.get("type")
        receiver_id = message.get("id")

        # Forget about messages that are improperly formatted
        if not msg_type or not receiver_id:
            logger.debug("Controller: discarding message — missing type or id: %s", message)
            return

        # Send the message to the appropriate handler, if possible
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
        Process a heartbeat message from a BLE receiver, updating receiver registrations and metadata as needed

        Args:
            receiver_id: Receiver's ID string.
            message: Full annotated message dict.
        """
        # Gather message metadata
        port = message.get("port")
        now = message.get("timestamp", time.time())

        with self._receivers_lock:
            # Check if the receiver is a new addition to the system or was previously inactive
            is_new = receiver_id not in self._receivers
            was_inactive = (
                not is_new
                and not self._receivers[receiver_id].get("active", True)
            )

            # Record the latest info from the receiver
            self._receivers[receiver_id] = {
                "port": port,
                "last_heartbeat": now,
                "active": True,
            }

            # Assuming a valid port exists, assign the port to the appropriate receiver entry
            if port:
                self._port_to_receiver[port] = receiver_id

        # If the receiver is entirely new to the system, register it!
        if is_new:
            self._zone_manager.register_receiver(receiver_id)
            logger.info(
                "Controller: new receiver registered — id='%s' port=%s",
                receiver_id, port,
            )
            self._send_lighting(receiver_id, "")

        # Send the current UUID whitelist to new or previously inactive receivers
        # Sending this on every heartbeat would take up too much time/bandwidth
        if is_new or was_inactive:
            self._send_uuid_whitelist(receiver_id)

        # Update the UI accordingly
        self._broadcast_state()

    def _handle_data(self, receiver_id: str, message: dict):
        """
        Process a BLE advertisement data message.

        Args:
            receiver_id: Receiver that captured the advertisement.
            message: The message dict.
        """
        # Gather message data
        uuid = message.get("uuid")
        rssi = message.get("rssi")

        # Forget about messages that don't have the needed info (RSSI, UUID)
        if uuid is None or rssi is None:
            logger.debug(
                "Controller: data message from '%s' missing uuid/rssi — discarding",
                receiver_id,
            )
            return

        # Ensure the RSSI value is an actual float-based value. Otherwise it's useless
        try:
            rssi = float(rssi)
        except (ValueError, TypeError):
            logger.warning(
                "Controller: invalid rssi '%s' from '%s' — discarding", rssi, receiver_id
            )
            return

        # Priority can be sent under different field names depending on firmware.
        priority = (
            message.get("priority")
            if "priority" in message
            else message.get("user_priority", message.get("prio", message.get("rank")))
        )

        if priority is None:
            priority = self._uuid_priority.get(self._normalise_uuid(uuid), 0)

        if isinstance(priority, bool):
            priority = int(priority)
        try:
            priority = int(priority)
        except (ValueError, TypeError):
            priority = self._uuid_priority.get(self._normalise_uuid(uuid), 0)

        # Valid data packet received, have the RSSI processor handle it.
        sample = self._user_tracker.process_rssi(uuid, receiver_id, rssi, priority=priority)

        # Update the UI accordingly
        self._broadcast_state()

        if self._live_plot_enabled:
            sample_payload = asdict(sample)
            sample_payload.update({
                "type": "rssi_sample",
                "timestamp": message.get("timestamp", time.time()),
            })
            self._ws_server.broadcast(sample_payload)

    # ──────────────────────────────────────────────────────────────────────────
    # Heartbeat monitor
    # ──────────────────────────────────────────────────────────────────────────

    def _heartbeat_monitor(self):
        """
        Background thread to mark receivers inactive when heartbeats stop arriving for a long enough duration
        """
        while self._running:
            # Wait for the heartbeat interval to elapse, logging the time at which the interval is completed
            time.sleep(_HEARTBEAT_CADENCE)
            now = time.time()
            changed = False

            with self._receivers_lock:
                # Iterate over each receiver
                for receiver_id, state in self._receivers.items():
                    # If a receiver is already inactive, no need to handle it
                    if not state.get("active", False):
                        continue

                    # If the last heartbeat of a receiver exceeds the timeout, mark it as inactive
                    elapsed = now - state.get("last_heartbeat", 0)
                    if elapsed > self.config.heartbeat_timeout:
                        state["active"] = False
                        changed = True
                        logger.warning(
                            "Controller: receiver '%s' inactive — no heartbeat for %.1f s",
                            receiver_id, elapsed,
                        )

            # If any receiver is marked as inactive, update the UI accordingly
            if changed:
                self._broadcast_state()

            # Evict users who haven't been heard by any receiver recently
            self._user_tracker.sweep_stale_users(self.config.rssi_timeout_duration)

    # ──────────────────────────────────────────────────────────────────────────
    # Outbound Commands to Receivers
    # ──────────────────────────────────────────────────────────────────────────

    def send_blink(self, receiver_id: str):
        """
        Send an LED blink command to a specific receiver to help identify the receiver in the real world

        Args:
            receiver_id: Target receiver ID.
        """
        # Get the Serial port of the receiver if it exists
        port = self._get_port(receiver_id)

        # Send the blink command to the receiver if the port/connection exists
        if port:
            self._serial_manager.send_to_port(
                port, {"type": "command", "command": "blink"}
            )
            logger.info(
                "Controller: blink sent to receiver '%s' on port %s", receiver_id, port
            )

        # Otherwise, note the failure
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
        # Get the Serial port of the receiver if it exists
        port = self._get_port(receiver_id)

        # Send the UUID list to the receiver if the port/connection exists
        if port:
            self._serial_manager.send_to_port(port, {
                "type": "uuid",
                "uuids": self.config.uuid_whitelist,
            })
            logger.info(
                "Controller: whitelist (%d UUIDs) sent to receiver '%s'",
                len(self.config.uuid_whitelist), receiver_id,
            )

    def _send_lighting(self, receiver_id: str, target_user: str):
        """
        Push the configured UUID whitelist to a specific receiver.

        Args:
            receiver_id: Target receiver ID.
        """
        # Get the Serial port of the receiver if it exists
        port = self._get_port(receiver_id)

        # Send the UUID list to the receiver if the port/connection exists
        if port:
            payload = {
                "type": "command",
                "command": "lighting",
                "light_target": target_user,
            }
            self._serial_manager.send_to_port(port, payload)
            logger.info(
                "Controller: Light Request for User %s sent to receiver '%s'",
                target_user, receiver_id,
            )
            logger.info("Controller: sending payload %s", payload)

    def _get_port(self, receiver_id: str) -> Optional[str]:
        """
        Look up the serial port path for a receiver ID.
        
        Args:
            receiver_id: Target receiver ID.
        """
        with self._receivers_lock:
            return self._receivers.get(receiver_id, {}).get("port")

    # ──────────────────────────────────────────────────────────────────────────
    # Configuration UI interface
    # ──────────────────────────────────────────────────────────────────────────

    def reorder_zones(self, ordered_receiver_ids: List[str]):
        """
        Update logical receiver/zone ordering for the logical queue.

        Args:
            ordered_receiver_ids: New desired zone ordering.
        """
        self._zone_manager.set_order(ordered_receiver_ids)
        logger.info("Controller: zone order updated → %s", ordered_receiver_ids)
        self._broadcast_state()

    def get_state(self) -> dict:
        """
        Build a state snapshot of all receivers for the configuration UI.

        Returns:
            Dict with keys: type, receivers, zones, users_by_zone.
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
            "live_plot": self.config.live_plot,
        }

    def _broadcast_state(self):
        """Push the current state to all connected configuration UI clients."""
        self._ws_server.broadcast(self.get_state())
