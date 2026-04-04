#include <Arduino.h>
#include <esp_now.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <map>
#include <vector>
#include <string>
#include <array>
#include <cstring>
#include "espnow.h"

// ═══════════════════════════════════════════════════════════════════════════
// CONFIGURATION — EDIT BEFORE FLASHING
// ═══════════════════════════════════════════════════════════════════════════

/** Serial baud rate. This has to match the central controller setting! */
static const uint32_t BAUD_RATE = 115200;

/** Status LED pin. */
static const uint8_t STATUS_LED_PIN = 25;

// ═══════════════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════════════

static const char*   GATEWAY_ID    = "gateway";
static const uint8_t BROADCAST_MAC[6] = { 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF };

// ═══════════════════════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════════════════════

// Maps receiver IDs to MAC addresses. Needed for ESP-NOW
static std::map<std::string, std::array<uint8_t, 6>> g_receiver_macs;

struct IncomingMsg {
    volatile bool pending;
    uint8_t       src_mac[6];
    uint8_t       data[250];
    uint8_t       len;
};
static IncomingMsg g_incoming = {};

// ═══════════════════════════════════════════════════════════════════════════
// Peer management
// ═══════════════════════════════════════════════════════════════════════════

/** Register a peer if not already known. */
static bool add_peer_if_new(const uint8_t* mac) {
    if (esp_now_is_peer_exist(mac)) return true;
    esp_now_peer_info_t peer{};
    memcpy(peer.peer_addr, mac, 6);
    peer.channel = 0;
    peer.encrypt = false;
    return esp_now_add_peer(&peer) == ESP_OK;
}

// ═══════════════════════════════════════════════════════════════════════════
// Outbound: ESP-NOW → Receivers
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Broadcast a complete UUID whitelist to all receivers, using chunking if needed
 */
static void broadcast_uuid_list(const std::vector<std::string>& uuids) {
    // Compute number of chunks needed
    const uint8_t total       = static_cast<uint8_t>(uuids.size());
    const uint8_t num_chunks  = (total == 0)
                                    ? 1
                                    : static_cast<uint8_t>((total + UUIDS_PER_CHUNK - 1) / UUIDS_PER_CHUNK);

    // For each chunk: set up a message with the chunked portion and send it
    for (uint8_t ci = 0; ci < num_chunks; ci++) {
        MsgUuidChunk msg{};
        msg.hdr.type = MSG_UUID_CHUNK;
        strncpy(msg.hdr.sender_id, GATEWAY_ID, sizeof(msg.hdr.sender_id) - 1);
        msg.chunk_index  = ci;
        msg.total_chunks = num_chunks;

        const uint8_t base  = ci * UUIDS_PER_CHUNK;
        uint8_t       count = 0;
        for (uint8_t i = 0; i < UUIDS_PER_CHUNK && (base + i) < total; i++) {
            strncpy(msg.uuids[i], uuids[base + i].c_str(), 36);
            msg.uuids[i][36] = '\0';
            count++;
        }
        msg.count = count;

        esp_now_send(BROADCAST_MAC, reinterpret_cast<const uint8_t*>(&msg), sizeof(msg));
        delay(5);
    }
}

/**
 * Send a command to one specific receiver by its string ID.
 */
static bool unicast_command(const char* receiver_id, const char* command) {
    auto it = g_receiver_macs.find(receiver_id);
    if (it == g_receiver_macs.end()) return false;

    MsgCommand msg{};
    msg.hdr.type = MSG_COMMAND;
    strncpy(msg.hdr.sender_id, GATEWAY_ID, sizeof(msg.hdr.sender_id) - 1);
    strncpy(msg.command, command, sizeof(msg.command) - 1);

    esp_now_send(it->second.data(), reinterpret_cast<const uint8_t*>(&msg), sizeof(msg));
    return true;
}

// ═══════════════════════════════════════════════════════════════════════════
// Outbound: Receiver messages to controller
// ═══════════════════════════════════════════════════════════════════════════

static void forward_heartbeat(const char* receiver_id) {
    StaticJsonDocument<128> doc;
    doc["type"] = "heartbeat";
    doc["id"]   = receiver_id;
    serializeJson(doc, Serial);
    Serial.println();
}

static void forward_data(const char* receiver_id, const char* uuid, int8_t rssi) {
    StaticJsonDocument<256> doc;
    doc["type"] = "data";
    doc["id"]   = receiver_id;
    doc["uuid"] = uuid;
    doc["rssi"] = rssi;
    serializeJson(doc, Serial);
    Serial.println();
}

// ═══════════════════════════════════════════════════════════════════════════
// Inbound: Receiver ESP-NOW message processing
// ═══════════════════════════════════════════════════════════════════════════

static void process_espnow_msg(const uint8_t* src_mac, const uint8_t* data, uint8_t len) {
    if (len < sizeof(MsgHeader)) return;
    const MsgHeader* hdr = reinterpret_cast<const MsgHeader*>(data);

    // If the receiver is not previously known, add MAC address mapping
    add_peer_if_new(src_mac);

    switch (hdr->type) {

        // Heartbeat: register/refresh sender MAC, forward heartbeat to controller
        case MSG_HEARTBEAT: {
            std::array<uint8_t, 6> mac_arr;
            memcpy(mac_arr.data(), src_mac, 6);
            g_receiver_macs[hdr->sender_id] = mac_arr;
            forward_heartbeat(hdr->sender_id);
            break;
        }

        // Data: forward BLE info to controller
        case MSG_DATA: {
            if (len < sizeof(MsgData)) return;
            const MsgData* msg = reinterpret_cast<const MsgData*>(data);
            forward_data(msg->hdr.sender_id, msg->uuid, msg->rssi);
            break;
        }

        default:
            break;
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Inbound: Serial command processing from controller
// ═══════════════════════════════════════════════════════════════════════════

static void process_serial_command(const char* raw) {
    StaticJsonDocument<1024> doc;
    if (deserializeJson(doc, raw)) return;

    const char* type = doc["type"];
    if (!type) return;

    // UUID update command: normalise and broadcast to all receivers
    if (strcmp(type, "uuid") == 0) {
        JsonArray arr = doc["uuids"].as<JsonArray>();
        std::vector<std::string> uuids;
        
        // For each UUID, normalize it
        for (JsonVariant v : arr) {
            std::string s = v.as<std::string>();
            for (char& c : s) c = toupper(c);
            size_t start = s.find_first_not_of(" \t\r\n");

            if (start == std::string::npos) continue;

            size_t end = s.find_last_not_of(" \t\r\n");
            s = s.substr(start, end - start + 1);

            // Assuming a valid UUID, add it to our local list
            if (!s.empty()) uuids.push_back(s);
        }

        // UUID list set: broadcast the new list to all receivers!
        broadcast_uuid_list(uuids);

    // Command command: unicast to the named receiver
    } else if (strcmp(type, "command") == 0) {
        const char* cmd = doc["command"];
        const char* id  = doc["id"];
        if (cmd && id) {
            unicast_command(id, cmd);
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// ESP-NOW receive callback
// ═══════════════════════════════════════════════════════════════════════════

static void on_data_recv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
    if (g_incoming.pending) return;
    if (len > (int)sizeof(g_incoming.data)) return;
    memcpy(g_incoming.src_mac, info->src_addr, 6);
    memcpy(g_incoming.data, data, len);
    g_incoming.len     = static_cast<uint8_t>(len);
    g_incoming.pending = true;
}

// ═══════════════════════════════════════════════════════════════════════════
// Arduino entry points
// ═══════════════════════════════════════════════════════════════════════════

void setup() {
    Serial.begin(BAUD_RATE);

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
        Serial.println("ESP-NOW init failed");
        while (true) {
          delay(1000);
        }
    }

    // Register the ESP-NOW callback for receiving data
    esp_now_register_recv_cb(on_data_recv);

    // Register broadcast peer for UUID whitelist sending
    esp_now_peer_info_t bcast{};
    memcpy(bcast.peer_addr, BROADCAST_MAC, 6);
    bcast.channel = 0;
    bcast.encrypt = false;
    esp_now_add_peer(&bcast);

    // Announce gateway readiness to controller for observability
    StaticJsonDocument<128> ready;
    ready["type"] = "gateway_ready";
    ready["mac"]  = WiFi.macAddress();
    serializeJson(ready, Serial);
    Serial.println();
}

void loop() {
    // Check out the messages left in the ESP-NOW inbox
    if (g_incoming.pending) {
        process_espnow_msg(
            g_incoming.src_mac,
            g_incoming.data,
            g_incoming.len
        );
        g_incoming.pending = false;
    }

    // If Serial connection is online, read bytes until buffer fills or newline is found
    if (Serial.available()) {
        static char line_buf[1024];
        int len = Serial.readBytesUntil('\n', line_buf, sizeof(line_buf) - 1);
        if (len > 0) {
            line_buf[len] = '\0';
            process_serial_command(line_buf);
        }
    }

    // Yield to the BLE stack / scheduler.
    delay(10);
}
