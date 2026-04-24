# Starlight

Starlight is a zone-based queue tracking prototype utilizing BLE RSSI:

- ESP32 receivers scan BLE advertisements.
- A Python controller ingests receiver heartbeats/data over serial.
- RSSI is processed with Kalman filtering + rolling average.
- Users are assigned to logical queue zones with hysteresis and timeout eviction.
- A browser UI shows receiver health, zone order, queue state, logs, and optional live RSSI plots.

## Repository status (current)

- Controller code is active and covered by unit tests.
- UI is a single static page served by the Python process.
- Firmware sketches are in [arduino/](arduino/), with both serial and ESP-NOW variants, though ESP-NOW is no longer officially supported.
- Hardware-in-the-loop firmware testing is not automated in this repo.

## Project layout

```text
starlight/
├── main.py
├── plot_rssi.py
├── requirements.txt
├── uuids.txt
├── INSTRUCTIONS.md
├── controller/
│   ├── config.py
│   ├── controller.py
│   ├── kalman_filter.py
│   ├── rssi_processor.py
│   ├── user_tracker.py
│   ├── zone_manager.py
│   ├── serial_connection.py
│   ├── serial_manager.py
│   ├── websocket_server.py
│   └── tests/
├── arduino/
│   ├── esp_advertiser/                  # simple BLE advertiser sketch
│   ├── receiver/                        # serial receiver
│   ├── receiver_espnow/                 # esp-now receiver
│   └── receiver_espnow_gateway/         # esp-now <-> serial gateway
├── ui/
│   └── index.html
├── data/                                # captured RSSI datasets
└── media/                               # demo videos/assets
```

## Quick start (controller)

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Put UUIDs to track (one per line) in [uuids.txt](uuids.txt).

3. Start controller:

```bash
python main.py --whitelist uuids.txt
```

Defaults:

- UI: http://localhost:8080
- WebSocket: ws://localhost:8765

## CLI options

```bash
python main.py --help
```

Common flags:

| Flag | Default | Purpose |
|---|---:|---|
| `--whitelist` | required | UUID whitelist text file |
| `--ws-host` | localhost | Bind host for WS/UI |
| `--ws-port` | 8765 | WebSocket port |
| `--ui-port` | 8080 | HTTP UI port |
| `--baud-rate` | 115200 | Serial baud |
| `--heartbeat-timeout` | 5.0 | Receiver inactivity timeout (s) |
| `--kalman-q` | 0.01 | Kalman process noise |
| `--kalman-r` | 2.0 | Kalman measurement noise |
| `--rolling-window` | 5 | Rolling average window size |
| `--hysteresis` | 3.0 | Zone-advance margin (dBm) |
| `--rssi-threshold` | -85.0 | Eviction floor (dBm) |
| `--rssi-timeout` | 10.0 | Eviction duration (s) |
| `--no-filter` | false | Use raw RSSI (skip filter + rolling average) |
| `--no-ratchet` | false | Allow bidirectional zone movement |
| `--live-plot` | false | Broadcast RSSI samples to UI charts |
| `--rssi-log` | unset | CSV output path for raw/filtered/avg RSSI |
| `--log-level` | INFO | Logging verbosity |

## Firmware sketches

### Serial mode (recommended)

- Receiver sketch: [arduino/receiver/receiver.ino](arduino/receiver/receiver.ino)
- Set a unique `RECEIVER_ID` per board.
- The controller auto-discovers macOS USB serial ports matching ESP32-style device names.

### ESP-NOW mode (experimental/prototype)

- Receiver: [arduino/receiver_espnow/receiver_espnow.ino](arduino/receiver_espnow/receiver_espnow.ino)
- Gateway: [arduino/receiver_espnow_gateway/receiver_espnow_gateway.ino](arduino/receiver_espnow_gateway/receiver_espnow_gateway.ino)
- Set gateway MAC in receiver sketch (`GATEWAY_MAC`) before flashing.

## Protocol (controller side)

Messages are one-line JSON over serial.

Receiver to controller:

```json
{ "type": "heartbeat", "id": "receiver-1" }
{ "type": "data", "id": "receiver-1", "uuid": "...", "rssi": -67 }
```

Controller to receiver:

```json
{ "type": "uuid", "uuids": ["...", "..."] }
{ "type": "command", "command": "blink" }
{ "type": "command", "command": "lighting", "light_target": "<uuid or empty>" }
```

## Tests

Run unit tests:

```bash
pytest controller/tests -v
```

The suite currently covers:

- `KalmanFilter`
- `RSSIProcessor`
- `ZoneManager`
- `UserTracker`
- Controller dispatch behavior with mocked serial ingestion