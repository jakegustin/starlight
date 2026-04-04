#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <ArduinoJson.h>

#include <esp_now.h>
#include "espnow.h"
#include <WiFi.h>

#include <vector>
#include <string>
#include <cstring>

// ═══════════════════════════════════════════════════════════════════════════
// CONFIGURATION — EDIT BEFORE FLASHING
// ═══════════════════════════════════════════════════════════════════════════

/** Unique identifier for this receiver. This must be distinct across all receivers! */
static const char* RECEIVER_ID = "receiver-1";

/** How often (ms) a heartbeat is sent to the controller. */
static const uint32_t HEARTBEAT_INTERVAL_MS = 2000;

/** Maximum number of UUIDs the whitelist can hold. */
static const uint8_t MAX_WHITELIST_SIZE = 32;

/** Status LED pin. */
static const uint8_t STATUS_LED_PIN = 25;

/** MAC Address of the Gateway */
static const uint8_t GATEWAY_MAC[6] = { 0xA4, 0xF0, 0x0F, 0x76, 0x40, 0x40 };

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

/** Basic data structure to capture an incoming message */
struct IncomingMsg {
    volatile bool pending;
    uint8_t       data[250];
    uint8_t       len;
};
static IncomingMsg g_incoming = {};

/** Accumulator structure to handle chunked UUIDs */
static struct {
    bool                     active;
    uint8_t                  total_chunks;
    uint8_t                  received_mask;
    std::vector<std::string> pending;
} g_uuid_accum = {};

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
// Outbound messages
// ═══════════════════════════════════════════════════════════════════════════

/** Transmit a heartbeat message to the gateway, which will handle forwarding. */
static void send_heartbeat() {
    MsgHeartbeat msg{};
    msg.hdr.type = MSG_HEARTBEAT;
    strncpy(msg.hdr.sender_id, RECEIVER_ID, sizeof(msg.hdr.sender_id) - 1);
    esp_now_send(GATEWAY_MAC, reinterpret_cast<const uint8_t*>(&msg), sizeof(msg));
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
// Inbound ESP-NOW message processing
// ═══════════════════════════════════════════════════════════════════════════

static void process_incoming(const uint8_t* data, uint8_t len) {
    // Forget about messages that are too short to meet our specification
    if (len < sizeof(MsgHeader)) return;

    const MsgHeader* hdr = reinterpret_cast<const MsgHeader*>(data);

    switch (hdr->type) {

        // UUID chunk command: accumulate chunks, then commit when all chunks have arrived
        case MSG_UUID_CHUNK: {
            if (len < sizeof(MsgUuidChunk)) return;
            const MsgUuidChunk* msg = reinterpret_cast<const MsgUuidChunk*>(data);

            // Reset accumulator if this is the first chunk of a new update
            if (msg->chunk_index == 0 || !g_uuid_accum.active) {
                g_uuid_accum.active        = true;
                g_uuid_accum.total_chunks  = msg->total_chunks;
                g_uuid_accum.received_mask = 0;
                g_uuid_accum.pending.clear();
            }

            // Append UUIDs carried by this chunk
            for (uint8_t i = 0; i < msg->count && i < UUIDS_PER_CHUNK; i++) {
                std::string norm = normalise_uuid(msg->uuids[i]);
                if (!norm.empty() && (int)g_uuid_accum.pending.size() < MAX_WHITELIST_SIZE) {
                    g_uuid_accum.pending.push_back(norm);
                }
            }
            g_uuid_accum.received_mask |= (1u << msg->chunk_index);

            // All chunks received — atomically swap in the new whitelist
            const uint8_t full_mask = (1u << msg->total_chunks) - 1;
            if ((g_uuid_accum.received_mask & full_mask) == full_mask) {
                g_whitelist = std::move(g_uuid_accum.pending);
                g_uuid_accum.active = false;
                g_uuid_accum.pending.clear();
            }
            break;
        }

        // Command command: Execute the specified command (e.g. blink)
        case MSG_COMMAND: {
            if (len < sizeof(MsgCommand)) return;
            const MsgCommand* msg = reinterpret_cast<const MsgCommand*>(data);
            if (strcmp(msg->command, "blink") == 0) {
                do_blink();
            }
            break;
        }

        default:
          break;
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// ESP-NOW receive callback
// ═══════════════════════════════════════════════════════════════════════════

static void on_data_recv(const esp_now_recv_info_t* /*info*/, const uint8_t* data, int len) {
    if (g_incoming.pending) return;
    if (len > (int)sizeof(g_incoming.data)) return;
    memcpy(g_incoming.data, data, len);
    g_incoming.len     = static_cast<uint8_t>(len);
    g_incoming.pending = true;
}

// ═══════════════════════════════════════════════════════════════════════════
// Arduino entry points
// ═══════════════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(115200);

    // Small delay to ensure Serial connection is properly started
    delay(500);

    pinMode(STATUS_LED_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, HIGH);

    // Initialize WiFi since it's required for some reason...
    WiFi.mode(WIFI_STA);
    WiFi.disconnect();

    // Verify ESP-NOW intializes appropriately
    if (esp_now_init() != ESP_OK) {
        // If it fails, note the issue and just wait.
        while (true) {
          do_blink();
          delay(1000);
        }
    }

    // Register the ESP-NOW callback for receiving data
    esp_now_register_recv_cb(on_data_recv);

    // Register the gateway as the sole send target
    esp_now_peer_info_t peer{};
    memcpy(peer.peer_addr, GATEWAY_MAC, 6);
    peer.channel = 0;
    peer.encrypt = false;
    esp_now_add_peer(&peer);

    BLEDevice::init("");
    g_ble_scan = BLEDevice::getScan();
    g_ble_scan->setAdvertisedDeviceCallbacks(&g_advertisement_callback, true);
    g_ble_scan->setActiveScan(true);
    g_ble_scan->setInterval(100);
    g_ble_scan->setWindow(99);

    // Start continuous scanning
    g_ble_scan->start(0, nullptr, false);

    // Announce ourselves immediately
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

    // Check out the messages left in the ESP-NOW inbox
    if (g_incoming.pending) {
        process_incoming(g_incoming.data, g_incoming.len);
        g_incoming.pending = false;
    }

    // Yield back to the BLE stack / scheduler
    delay(10);
}
