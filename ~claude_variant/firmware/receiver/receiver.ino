/**
 * Starlight System — BLE Receiver Firmware
 * ==========================================
 *
 * Runs on an ESP32. Performs the following roles:
 *
 *   1. ADVERTISES a heartbeat to the central controller over Serial (every 2 s).
 *   2. SCANS for BLE advertisements and filters them against a UUID whitelist
 *      received from the controller.
 *   3. REPORTS matching advertisements over Serial as JSON data messages.
 *   4. ACCEPTS commands from the controller (blink, uuid whitelist update).
 *   5. BLINKS the built-in LED in a specific sequence to confirm identity.
 *
 * Serial protocol
 * ---------------
 * All messages are newline-terminated JSON on a single line.
 *
 * Outbound (ESP32 → Controller):
 *   Heartbeat:  { "type": "heartbeat", "id": "<RECEIVER_ID>" }
 *   Data:       { "type": "data",      "id": "<RECEIVER_ID>", "uuid": "<UUID>", "rssi": <int> }
 *
 * Inbound (Controller → ESP32):
 *   UUID list:  { "type": "uuid",    "uuids": ["<UUID>", ...] }
 *   Blink:      { "type": "command", "command": "blink" }
 *
 * Configuration
 * -------------
 * Edit the constants in the CONFIGURATION section below before flashing.
 *
 * Hardware requirements
 * ---------------------
 * - ESP32 development board (any variant with BLE)
 * - Built-in LED on pin LED_BUILTIN (usually GPIO 2)
 * - USB connection to the central controller host
 */

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <ArduinoJson.h>
#include <vector>
#include <string>

// ═══════════════════════════════════════════════════════════════════════════
// CONFIGURATION — edit before flashing
// ═══════════════════════════════════════════════════════════════════════════

/** Unique identifier for this receiver. Must be distinct across all receivers. */
static const char* RECEIVER_ID = "receiver-3";

/** Serial baud rate — must match the central controller setting. */
static const uint32_t BAUD_RATE = 115200;

/** How often (ms) a heartbeat is sent to the controller. */
static const uint32_t HEARTBEAT_INTERVAL_MS = 2000;

/** BLE scan window (seconds). A shorter window reduces latency. */
static const uint8_t BLE_SCAN_WINDOW_SEC = 1;

/** Maximum number of UUIDs the whitelist can hold. */
static const uint8_t MAX_WHITELIST_SIZE = 32;

/** LED pin — built-in LED on most ESP32 devkits. */
static const uint8_t LED_PIN = 25;

// Blink sequence timings (ms) for the "identify" blink command.
// Pattern: 3 quick flashes then a long flash.
static const uint16_t BLINK_SHORT_ON  = 100;
static const uint16_t BLINK_SHORT_OFF = 100;
static const uint16_t BLINK_LONG_ON   = 500;
static const uint16_t BLINK_LONG_OFF  = 300;
static const uint8_t  BLINK_REPEATS   = 3;

// ═══════════════════════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════════════════════

/** Whitelist of UUIDs to track. Updated at runtime via Serial commands. */
static std::vector<std::string> g_whitelist;

/** Timestamp of the last heartbeat transmission. */
static uint32_t g_last_heartbeat_ms = 0;

/** BLE scan object (created once in setup). */
static BLEScan* g_ble_scan = nullptr;

// ═══════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Normalise a UUID string to uppercase with no surrounding whitespace.
 * Enables case-insensitive whitelist matching.
 */
static std::string normalise_uuid(const std::string& raw) {
    std::string result = raw;
    for (char& c : result) c = toupper(c);
    // Trim leading whitespace
    size_t start = result.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    // Trim trailing whitespace
    size_t end = result.find_last_not_of(" \t\r\n");
    return result.substr(start, end - start + 1);
}

/**
 * Return true if *uuid* appears in the current whitelist.
 * Comparison is case-insensitive (both sides are normalised to uppercase).
 */
static bool is_whitelisted(const std::string& uuid) {
    std::string norm = normalise_uuid(uuid);
    for (const auto& entry : g_whitelist) {
        if (norm == entry) return true;
    }
    return false;
}

// ═══════════════════════════════════════════════════════════════════════════
// BLE scan callback
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Called by the BLE stack for every advertisement received during a scan.
 *
 * Checks each advertised service UUID against the whitelist and, on a match,
 * emits a JSON data message over Serial.
 */
class AdvertisementCallback : public BLEAdvertisedDeviceCallbacks {
public:
    void onResult(BLEAdvertisedDevice device) override {
        // Iterate all service UUIDs advertised by this device.
        for (int i = 0; i < (int)device.getServiceUUIDCount(); i++) {
            std::string uuid = device.getServiceUUID(i).toString().c_str();
            if (is_whitelisted(uuid)) {
                // Emit a JSON data message.
                StaticJsonDocument<256> doc;
                doc["type"] = "data";
                doc["id"]   = RECEIVER_ID;
                doc["uuid"] = uuid.c_str();
                doc["rssi"] = device.getRSSI();

                serializeJson(doc, Serial);
                Serial.println();  // Newline terminator required by the protocol
                break;             // One report per advertisement packet
            }
        }
    }
};

static AdvertisementCallback g_advertisement_callback;

// ═══════════════════════════════════════════════════════════════════════════
// Outbound messages
// ═══════════════════════════════════════════════════════════════════════════

/** Transmit a heartbeat JSON message to the controller. */
static void send_heartbeat() {
    StaticJsonDocument<128> doc;
    doc["type"] = "heartbeat";
    doc["id"]   = RECEIVER_ID;
    serializeJson(doc, Serial);
    Serial.println();
}

// ═══════════════════════════════════════════════════════════════════════════
// Blink sequence
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Execute the identity blink sequence synchronously.
 *
 * Pattern: BLINK_REPEATS short flashes followed by one long flash.
 * The LED state is restored to OFF after the sequence.
 *
 * Note: This blocks for ~(BLINK_REPEATS*(ON+OFF) + LONG_ON + LONG_OFF) ms.
 * At the default values this is ~1.1 s — acceptable for an identification
 * command that is only triggered manually from the UI.
 */
static void do_blink() {
    for (uint8_t i = 0; i < BLINK_REPEATS; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(BLINK_SHORT_ON);
        digitalWrite(LED_PIN, LOW);
        delay(BLINK_SHORT_OFF);
    }
    digitalWrite(LED_PIN, HIGH);
    delay(BLINK_LONG_ON);
    digitalWrite(LED_PIN, LOW);
    delay(BLINK_LONG_OFF);
}

// ═══════════════════════════════════════════════════════════════════════════
// Inbound command processing
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Process a JSON command received over Serial from the central controller.
 *
 * Supported commands:
 *   {"type":"uuid","uuids":["<UUID>",...]}  — replace the UUID whitelist
 *   {"type":"command","command":"blink"}    — trigger the blink sequence
 *
 * Malformed JSON or unknown types are silently discarded.
 *
 * @param raw  Null-terminated C-string containing the raw line from Serial.
 */
static void process_command(const char* raw) {
    StaticJsonDocument<1024> doc;
    DeserializationError err = deserializeJson(doc, raw);
    if (err) {
        // Malformed JSON — discard silently per spec.
        return;
    }

    const char* type = doc["type"];
    if (!type) return;

    if (strcmp(type, "uuid") == 0) {
        // Replace whitelist with the new array.
        JsonArray uuids = doc["uuids"].as<JsonArray>();
        g_whitelist.clear();
        for (JsonVariant v : uuids) {
            std::string norm = normalise_uuid(v.as<std::string>());
            if (!norm.empty() && (int)g_whitelist.size() < MAX_WHITELIST_SIZE) {
                g_whitelist.push_back(norm);
            }
        }

    } else if (strcmp(type, "command") == 0) {
        const char* cmd = doc["command"];
        if (cmd && strcmp(cmd, "blink") == 0) {
            do_blink();
        }
    }
    // Unknown types are silently ignored.
}

// ═══════════════════════════════════════════════════════════════════════════
// Arduino entry points
// ═══════════════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(BAUD_RATE);

    // Allow the host a moment to open the serial port before sending the
    // first heartbeat (prevents the initial message being missed).
    delay(500);

    // LED setup.
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);

    // Initialise BLE.
    BLEDevice::init("");
    g_ble_scan = BLEDevice::getScan();
    g_ble_scan->setAdvertisedDeviceCallbacks(&g_advertisement_callback, true /*wantDuplicates*/);
    g_ble_scan->setActiveScan(true);  // Active scan requests scan-response packets too
    g_ble_scan->setInterval(100);
    g_ble_scan->setWindow(99);

    // Start continuous scanning.  Results fire via the callback in real-time.
    g_ble_scan->start(0, nullptr, false);  // 0 = scan forever

    // Announce ourselves immediately.
    send_heartbeat();
    g_last_heartbeat_ms = millis();
}

void loop() {
    // ── Send periodic heartbeat ────────────────────────────────────────────
    uint32_t now = millis();
    if (now - g_last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
        send_heartbeat();
        g_last_heartbeat_ms = now;
    }

    // ── Process inbound Serial commands ───────────────────────────────────
    if (Serial.available()) {
        // Read until newline (up to 1023 chars) to handle large UUID lists.
        static char line_buf[1024];
        int len = Serial.readBytesUntil('\n', line_buf, sizeof(line_buf) - 1);
        if (len > 0) {
            line_buf[len] = '\0';
            process_command(line_buf);
        }
    }

    // Yield to the BLE stack / RTOS scheduler.
    delay(10);
}
