"""
Entry point for running the Starlight controller
"""

from __future__ import annotations

import argparse

from controller import CentralController
from controller.src.config_ui import ReceiverConfigServer
from controller.src.serial_mux import MultiSerialIngester


def main() -> None:
    """
    Collects CLI arguments and instantiates the CentralController instance
    """
    parser = argparse.ArgumentParser(
        prog="starlight-controller",
        description="Run the Starlight central controller by ingesting BLE receiver serial output.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help=(
            "Enable automatic scanning for serial ports / receivers"
        ),
    )
    parser.add_argument(
        "--ports",
        nargs="+",
        help="For when --scan is disabled: Serial ports to read from (e.g., /dev/ttyUSB0).",
    )
    parser.add_argument(
        "--scan-patterns",
        nargs="*",
        default=None,
        help="For when --scan is enabled: Custom glob patterns to scan for (e.g. '/dev/cu.*')",
    )
    parser.add_argument(
        "--scan-interval",
        type=float,
        default=2.0,
        help="For when --scan is enabled: How often to rescan for new serial ports. Default is 2s",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout (seconds) for removing a user with a weak signal. Default is 10.0",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=10,
        help="Number of RSSI values to average in the rolling buffer. Default is 10",
    )
    parser.add_argument(
        "--uuid-file",
        default="auto",
        help="Path to a file listing UUIDs to pre-register. Use \"auto\" for automatic additions.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging about detected ports and received lines.",
    )
    parser.add_argument(
        "--config-ui",
        action="store_true",
        help="Start a local web UI to view online receivers and reorder zones.",
    )
    parser.add_argument(
        "--config-ui-host",
        default="127.0.0.1",
        help="Host/IP for the configuration UI server. Default is 127.0.0.1",
    )
    parser.add_argument(
        "--config-ui-port",
        type=int,
        default=5253,
        help="Port for the configuration UI server. Default is 5253",
    )

    args = parser.parse_args()

    # If explicit ports are provided, treat that list as the desired zone order.
    if args.ports and args.scan:
        raise SystemExit("--scan and --ports cannot be used together.")

    scan_ports = args.scan or not bool(args.ports)

    if not args.ports and not scan_ports:
        raise SystemExit("Must provide --ports or enable --scan to discover serial devices.")

    # When ports are given, their order defines the zone order.
    zones: list[str] = list(args.ports or [])

    controller = CentralController(
        zone_order=zones,
        timeout_seconds=args.timeout,
        window_size=args.window_size,
        allow_dynamic_zones=scan_ports,
    )

    if args.uuid_file == "auto":
        print("Automatic UUID registration enabled")
        controller.automatic_registration = True
    else:
        try:
            with open(args.uuid_file, "r", encoding="utf-8") as f:
                for line in f:
                    uuid = line.strip()
                    if not uuid or uuid.startswith("#"):
                        continue
                    controller.register_user(uuid)
        except FileNotFoundError:
            print("ERROR: Could not find UUID file ", args.uuid_file)
            return

    print(f"Starting Starlight controller with zones: {zones}")
    if scan_ports:
        print("Serial scanning is enabled (new receivers will be detected automatically).")

    mux = MultiSerialIngester(
        controller,
        ports=args.ports,
        scan_ports=scan_ports,
        scan_patterns=args.scan_patterns,
        scan_interval=args.scan_interval,
        verbose=args.verbose,
    )

    ui_server: ReceiverConfigServer | None = None
    if args.config_ui:
        ui_server = ReceiverConfigServer(
            controller=controller,
            ingester=mux,
            host=args.config_ui_host,
            port=args.config_ui_port,
        )
        ui_server.start()
        host, port = ui_server.address()
        print(f"Config UI running at http://{host}:{port}")

    try:
        mux.run_forever()
    finally:
        if ui_server is not None:
            ui_server.stop()

if __name__ == "__main__":
    main()
