#!/usr/bin/env python3
"""
Starlight System - Entry Point
================================

Parses command-line arguments, loads the BLE UUID whitelist, constructs the
ControllerConfig, and starts the central controller (which in turn starts the
configuration UI and WebSocket servers).

The controller runs indefinitely until a SIGINT (Ctrl-C) or SIGTERM signal is
received, at which point all subsystems are stopped cleanly.

Usage
-----
    python main.py --whitelist uuids.txt [options]

Run ``python main.py --help`` for the full argument reference.
"""

import argparse
import logging
import signal
import sys
from pathlib import Path

from controller.config import ControllerConfig
from controller.controller import Controller

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Whitelist loading ──────────────────────────────────────────────────────────

def load_whitelist(path: str) -> list:
    """
    Load BLE UUIDs from a plain-text file (one UUID per line).

    Blank lines and lines beginning with ``#`` are ignored, making the file
    format comment-friendly.

    Args:
        path: Path to the whitelist file.

    Returns:
        List of UUID strings (stripped of leading/trailing whitespace).

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    whitelist_path = Path(path)
    if not whitelist_path.exists():
        raise FileNotFoundError(f"Whitelist file not found: {path}")

    uuids = []
    with open(whitelist_path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if line and not line.startswith("#"):
                uuids.append(line)

    logger.info("Loaded %d UUID(s) from whitelist '%s'", len(uuids), path)
    return uuids


# ── Argument parsing ───────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    """
    Construct and return the argument parser for the Starlight controller.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="starlight",
        description=(
            "Starlight — BLE-based queue zone tracking system.\n"
            "Starts the central controller, WebSocket server, and configuration UI."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Required ──────────────────────────────────────────────────────────────
    parser.add_argument(
        "--whitelist",
        required=True,
        metavar="PATH",
        help="Path to a text file listing whitelisted BLE UUIDs (one per line).",
    )

    # ── Networking ────────────────────────────────────────────────────────────
    net = parser.add_argument_group("networking")
    net.add_argument(
        "--ws-host", default="localhost",
        help="Hostname for the WebSocket server.",
    )
    net.add_argument(
        "--ws-port", type=int, default=8765,
        help="Port for the WebSocket server.",
    )
    net.add_argument(
        "--ui-port", type=int, default=8080,
        help="Port for the HTTP server serving the configuration UI.",
    )

    # ── Serial ────────────────────────────────────────────────────────────────
    ser = parser.add_argument_group("serial")
    ser.add_argument(
        "--baud-rate", type=int, default=115200,
        help="Serial baud rate for BLE receiver communication.",
    )
    ser.add_argument(
        "--heartbeat-timeout", type=float, default=5.0,
        help=(
            "Seconds without a heartbeat before a receiver is marked inactive. "
            "Must exceed 2x the receiver's heartbeat interval (default: 2 s)."
        ),
    )

    # ── Kalman filter ─────────────────────────────────────────────────────────
    kf = parser.add_argument_group("Kalman filter")
    kf.add_argument(
        "--kalman-q", type=float, default=0.01, dest="kalman_q",
        help="Kalman filter process noise (Q). Higher = more responsive.",
    )
    kf.add_argument(
        "--kalman-r", type=float, default=2.0, dest="kalman_r",
        help="Kalman filter measurement noise (R). Higher = smoother.",
    )

    # ── RSSI processing ───────────────────────────────────────────────────────
    rssi = parser.add_argument_group("RSSI processing")
    rssi.add_argument(
        "--rolling-window", type=int, default=5,
        help="Number of filtered samples in the rolling RSSI average window.",
    )
    rssi.add_argument(
        "--hysteresis", type=float, default=3.0,
        help="RSSI advantage (dBm) the next zone must have to trigger advancement.",
    )
    rssi.add_argument(
        "--rssi-threshold", type=float, default=-85.0,
        help="RSSI floor (dBm) below which the eviction timer starts.",
    )
    rssi.add_argument(
        "--rssi-timeout", type=float, default=10.0,
        help="Seconds below rssi-threshold before a user is evicted.",
    )

    # ── Demo / diagnostic modes ───────────────────────────────────────────────
    demo = parser.add_argument_group("demo modes")
    demo.add_argument(
        "--no-filter",
        action="store_true",
        default=False,
        help=(
            "Bypass Kalman filtering and rolling averaging — raw RSSI is used directly. "
            "Useful for demonstrating how noisy unfiltered signal is."
        ),
    )
    demo.add_argument(
        "--no-ratchet",
        action="store_true",
        default=False,
        help=(
            "Allow users to move in either direction between zones, not just forward. "
            "Hysteresis still applies to prevent thrashing."
        ),
    )

    # ── Plotting ────────────────────────────────────────────────────────────
    parser.add_argument(
        "--live-plot",
        action="store_true",
        default=False,
        help="Stream live raw and filtered RSSI samples to the browser UI.",
    )
    parser.add_argument(
        "--rssi-log",
        metavar="PATH",
        default=None,
        help="Write raw and filtered RSSI samples per user and receiver to a CSV file.",
    )

    # ── Diagnostics ───────────────────────────────────────────────────────────
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )

    return parser


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    """Parse arguments, load the whitelist, and start the Starlight controller."""
    parser = build_arg_parser()
    args = parser.parse_args()

    # Apply the requested log level globally.
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Load the UUID whitelist.
    try:
        whitelist = load_whitelist(args.whitelist)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    if not whitelist:
        logger.warning(
            "Whitelist is empty — no UUIDs will be tracked. "
            "Receivers will be told to ignore all advertisements."
        )

    # Build controller configuration.
    config = ControllerConfig(
        baud_rate=args.baud_rate,
        heartbeat_timeout=args.heartbeat_timeout,
        kalman_process_noise=args.kalman_q,
        kalman_measurement_noise=args.kalman_r,
        rolling_window_size=args.rolling_window,
        hysteresis=args.hysteresis,
        rssi_timeout_threshold=args.rssi_threshold,
        rssi_timeout_duration=args.rssi_timeout,
        ws_host=args.ws_host,
        ws_port=args.ws_port,
        ui_port=args.ui_port,
        uuid_whitelist=whitelist,
        raw_mode=args.no_filter,
        no_ratchet=args.no_ratchet,
        live_plot=args.live_plot,
        rssi_csv_log=args.rssi_log,
    )

    controller = Controller(config)

    # Graceful shutdown on SIGINT (Ctrl-C) and SIGTERM.
    def _shutdown(sig, _frame):
        logger.info("Signal %s received — shutting down Starlight...", sig)
        controller.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "Starlight starting — UI at http://%s:%d | WS at ws://%s:%d",
        args.ws_host, args.ui_port, args.ws_host, args.ws_port,
    )
    controller.start()


if __name__ == "__main__":
    main()
