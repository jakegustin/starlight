# Starlight — BLE Queue Positioning System

A prototype BLE-based queue zone tracker. Three ESP32 receivers report advertisement RSSI to a Python central controller, which uses a Kalman filter + rolling average + hysteresis logic to assign users to zones and push live state to a browser-based configuration UI.

## Quick start

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Flash the firmware

#### Serial Variant
- `firmware/receiver/receiver.ino` 
  - Edit `RECEIVER_ID` to be a unique string value, then flash the board.
  - Repeat this for all desired BLE receiver nodes.

#### ESP-NOW variant
- `firmware/receiver_espnow_gateway/receiver_espnow_gateway.ino`
  - Flash this to one ESP32 connected to the controller/laptop via USB.
  - It forwards ESP-NOW messages to Serial JSON for the Python controller.
- `firmware/receiver_ble_to_espnow/receiver_ble_to_espnow.ino`
  - Flash this to each BLE receiver node.
  - It scans BLE advertisements and sends heartbeat/data via ESP-NOW.

### 3. Edit your UUID whitelist
Add your BLE advertiser UUIDs (one per line) to `uuids.txt`.

### 4. Run the controller
```bash
python main.py --whitelist uuids.txt
```

The controller auto-discovers connected ESP32s via USB serial heartbeats.  
Config UI: **http://localhost:8080**  
WebSocket: **ws://localhost:8765**

### 5. Run tests
```bash
pytest controller/tests/ -v
```

## CLI reference
```
python main.py --help
```

Key flags:
| Flag | Default | Description |
|------|---------|-------------|
| `--whitelist` | *(required)* | Path to UUID whitelist file |
| `--baud-rate` | 115200 | Serial baud rate |
| `--heartbeat-timeout` | 5.0 s | Seconds before receiver marked inactive |
| `--kalman-q` | 0.01 | Kalman process noise (Q) |
| `--kalman-r` | 2.0 | Kalman measurement noise (R) |
| `--rolling-window` | 5 | Rolling average window size |
| `--hysteresis` | 3.0 dBm | Advancement threshold |
| `--rssi-threshold` | -85 dBm | Eviction RSSI floor |
| `--rssi-timeout` | 10.0 s | Eviction timer duration |
| `--ws-port` | 8765 | WebSocket port |
| `--ui-port` | 8080 | Config UI HTTP port |
| `--live-plot` | false | Stream raw and filtered RSSI samples to the browser UI |
| `--log-level` | INFO | DEBUG / INFO / WARNING / ERROR |

## Architecture

```
ESP32 (×3)                  Central Controller (Python)
──────────                  ───────────────────────────
BLE Scanner                 main.py
  │ JSON/Serial               └─ Controller
  ▼                               ├─ SerialManager
SerialManager ──────────────────▶     ├─ SerialConnection (per port)
  │ queue.Queue                   ├─ ZoneManager
  ▼                               ├─ RSSIProcessor
Controller._process_loop            │   └─ KalmanFilter (per uuid+receiver)
  ├─ heartbeat → ZoneManager      └─ UserTracker
  └─ data → UserTracker               └─ WebSocketServer
                                           ├─ WS broadcasts → Browser UI
                                           └─ HTTP serves ui/index.html
```

## File layout
```
starlight/
├── main.py                  Entry point
├── requirements.txt
├── uuids.txt                BLE UUID whitelist
├── controller/
│   ├── config.py            ControllerConfig dataclass
│   ├── controller.py        Central orchestrator
│   ├── kalman_filter.py     1D Kalman filter
│   ├── rssi_processor.py    Filter + rolling average manager
│   ├── zone_manager.py      Ordered zone list
│   ├── user_tracker.py      Zone assignment + eviction logic
│   ├── serial_connection.py Single serial port reader thread
│   ├── serial_manager.py    Port discovery + connection pool
│   ├── websocket_server.py  WS + HTTP servers
│   └── tests/
│       ├── test_kalman_filter.py
│       ├── test_rssi_processor.py
│       ├── test_zone_manager.py
│       ├── test_user_tracker.py
│       └── test_serial_mock.py
├── arduino/
│   └── receiver/
│       └── receiver.ino                 ESP32 BLE receiver firmware with Serial communications
│   └── receiver_espnow/
│       └── receiver_espnow.ino          ESP32 BLE receiver firmware with ESP-NOW communications
│   └── receiver_espnow_gateway/
│       └── receiver_espnow_gateway.ino  ESP32 gateway firmware, forwarding ESP-NOW messages to controller via Serial
└── ui/
    └── index.html                       Configuration UI
```

## Message Flow (Serial)

```
ESP32 firmware
    ↓ Serial JSON (heartbeat / data)
SerialConnection (per port, threaded)
    ↓ parsed + timestamped dict
shared queue.Queue
    ↓ consumed by _process_loop (main thread)
Controller._dispatch_message
    ├─ heartbeat → _handle_heartbeat → ZoneManager, UUID whitelist push
    └─ data      → _handle_data      → UserTracker → RSSIProcessor
```

## Message Flow (ESP-NOW)

```
ESP32 firmware
    ↓ ESP-NOW JSON (heartbeat / data)
ESP32 gateway firmware
    ↓ parse ESP-NOW JSON, forward to controller via Serial
SerialConnection (per port, threaded)
    ↓ parsed + timestamped dict
shared queue.Queue
    ↓ consumed by _process_loop (main thread)
Controller._dispatch_message
    ├─ heartbeat → _handle_heartbeat → ZoneManager, UUID whitelist push
    └─ data      → _handle_data      → UserTracker → RSSIProcessor
```

## Message Protocol

All messages are newline-terminated JSON on a single line.

Outbound (ESP32 → Controller):

```
  Heartbeat:  { "type": "heartbeat", "id": "<RECEIVER_ID>" }
  Data:       { "type": "data",      "id": "<RECEIVER_ID>", "uuid": "<UUID>", "rssi": <int> }
```

Inbound (Controller → ESP32):
```
  UUID list:  { "type": "uuid",    "uuids": ["<UUID>", ...] }
  Blink:      { "type": "command", "command": "blink" }
```