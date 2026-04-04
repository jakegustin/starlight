#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <ArduinoJson.h>

#include <vector>
#include <string>

// ═══════════════════════════════════════════════════════════════════════════
// CONFIGURATION — EDIT THIS BEFORE FLASHING
// ═══════════════════════════════════════════════════════════════════════════

/** Unique identifier for this receiver. This must be distinct across all receivers! */
static const char* RECEIVER_ID = "receiver-1";

/** Serial baud rate. This has to match the central controller setting! */
static const uint32_t BAUD_RATE = 115200;

/** How often (ms) a heartbeat is sent to the controller. */
static const uint32_t HEARTBEAT_INTERVAL_MS = 2000;

/** Maximum number of UUIDs the whitelist can hold. */
static const uint8_t MAX_WHITELIST_SIZE = 32;

/** Status LED pin */
static const uint8_t STATUS_LED_PIN = 27;
static const uint8_t PRIORITY_PIN = 26;
static const uint8_t STANDARD_PIN = 25;

// Blink sequence timings (ms) for the "blink" command.
static const uint16_t BLINK_SHORT_ON  = 100;
static const uint16_t BLINK_SHORT_OFF = 100;
static const uint16_t BLINK_LONG_ON   = 500;
static const uint16_t BLINK_LONG_OFF  = 300;
static const uint8_t  BLINK_REPEATS   = 3;

// ═══════════════════════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════════════════════

/** Whitelist of UUIDs to track. Updated at runtime via Controller commands. */
static std::vector<std::string> g_whitelist;

/** Timestamp of the last heartbeat transmission. */
static uint32_t g_last_heartbeat_ms = 0;

/** BLE scan object. */
static BLEScan* g_ble_scan = nullptr;

// ═══════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════

/** Normalise a UUID string to be uppercase and to not have whitespace. */
static std::string normalise_uuid(const std::string& raw) {
    std::string result = raw;
    // Convert to uppercase
    for (char& c : result) c = toupper(c);

    // Trim leading whitespace
    size_t start = result.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";

    // Trim trailing whitespace
    size_t end = result.find_last_not_of(" \t\r\n");
    return result.substr(start, end - start + 1);
}

/** Determine if a given UUID is in the current whitelist */
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
 * If UUID is on the whitelist, send a message to the controller to inform of advertisement
 */
class AdvertisementCallback : public BLEAdvertisedDeviceCallbacks {
public:
    void onResult(BLEAdvertisedDevice device) override {
        // Iterate over all received UUIDs
        for (int i = 0; i < (int)device.getServiceUUIDCount(); i++) {
            std::string uuid = device.getServiceUUID(i).toString().c_str();

            // If the UUID is whitelisted, send the data message!
            if (is_whitelisted(uuid)) {
                StaticJsonDocument<256> doc;
                doc["type"] = "data";
                doc["id"]   = RECEIVER_ID;
                doc["uuid"] = uuid.c_str();
                doc["rssi"] = device.getRSSI();

                serializeJson(doc, Serial);
                Serial.println();
                break;
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

/** Execute the identity blink sequence synchronously. */
static void do_blink() {
    for (uint8_t i = 0; i < BLINK_REPEATS; i++) {
        digitalWrite(STATUS_LED_PIN, LOW);
        delay(BLINK_SHORT_ON);
        digitalWrite(STATUS_LED_PIN, HIGH);
        delay(BLINK_SHORT_OFF);
    }
    digitalWrite(STATUS_LED_PIN, LOW);
    delay(BLINK_LONG_ON);
    digitalWrite(STATUS_LED_PIN, HIGH);
    delay(BLINK_LONG_OFF);
}

// ═══════════════════════════════════════════════════════════════════════════
// Demo LEDs
// ═══════════════════════════════════════════════════════════════════════════

/** Execute the identity blink sequence synchronously. */
static void do_lighting(const char * id) {
    if (strcmp(id, "12345678-1234-1234-1234-12345678abcd") == 0) {
        digitalWrite(PRIORITY_PIN, HIGH);
        digitalWrite(STANDARD_PIN, LOW);
    } else if (strcmp(id, "12345678-1234-1234-1234-12345678abce") == 0) {
        digitalWrite(PRIORITY_PIN, LOW);
        digitalWrite(STANDARD_PIN, HIGH);
    } else {
        digitalWrite(PRIORITY_PIN, LOW);
        digitalWrite(STANDARD_PIN, LOW);    
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Inbound command processing
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Process a JSON command received over Serial from the central controller.
 *
 * @param raw  Null-terminated C-string containing the raw line from Serial.
 */
static void process_command(const char* raw) {
    StaticJsonDocument<1024> doc;
    DeserializationError err = deserializeJson(doc, raw);

    // If the JSON is malformed for any reason, just forget about it
    if (err) {
        return;
    }

    const char* type = doc["type"];
    if (!type) return;

    // UUID command: replace current whitelist with received whitelist
    if (strcmp(type, "uuid") == 0) {
        JsonArray uuids = doc["uuids"].as<JsonArray>();
        g_whitelist.clear();
        for (JsonVariant v : uuids) {
            std::string norm = normalise_uuid(v.as<std::string>());
            if (!norm.empty() && (int)g_whitelist.size() < MAX_WHITELIST_SIZE) {
                g_whitelist.push_back(norm);
            }
        }

    // Command command: Execute the specified command (e.g. blink)
    } else if (strcmp(type, "command") == 0) {
        const char* cmd = doc["command"];
        if (cmd && strcmp(cmd, "blink") == 0) {
            do_blink();
        } else if (cmd && strcmp(cmd, "lighting") == 0) {
            const char * target = doc["light_target"];
            do_lighting(target ? target : "");
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Arduino entry points
// ═══════════════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(BAUD_RATE);

    // Small delay to ensure Serial connection is properly started
    delay(500);

    pinMode(STATUS_LED_PIN, OUTPUT);
    pinMode(PRIORITY_PIN, OUTPUT);
    pinMode(STANDARD_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, HIGH);
    digitalWrite(PRIORITY_PIN, LOW);
    digitalWrite(STANDARD_PIN, LOW);

    BLEDevice::init("");
    g_ble_scan = BLEDevice::getScan();
    g_ble_scan->setAdvertisedDeviceCallbacks(&g_advertisement_callback, true /*wantDuplicates*/);
    g_ble_scan->setActiveScan(true);  // Active scan helps to reduce latency
    g_ble_scan->setInterval(100);
    g_ble_scan->setWindow(99);

    // Start continuous scanning.
    g_ble_scan->start(0, nullptr, false);

    // Announce ourselves immediately.
    send_heartbeat();
    g_last_heartbeat_ms = millis();
}

void loop() {
    // Send heartbeat message if the interval/period has elapsed
    uint32_t now = millis();
    if (now - g_last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
        send_heartbeat();
        g_last_heartbeat_ms = now;
    }

    // If Serial connection is online, read bytes until buffer fills or newline is found
    if (Serial.available()) {
        static char line_buf[1024];
        int len = Serial.readBytesUntil('\n', line_buf, sizeof(line_buf) - 1);
        if (len > 0) {
            line_buf[len] = '\0';
            process_command(line_buf);
        }
    }

    // Yield to the BLE stack / scheduler.
    delay(10);
}
