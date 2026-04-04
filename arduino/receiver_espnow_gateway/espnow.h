#pragma once
#include <stdint.h>

enum MsgType : uint8_t {
    MSG_HEARTBEAT  = 0x01,
    MSG_DATA       = 0x02,
    MSG_UUID_CHUNK = 0x03,
    MSG_COMMAND    = 0x04,
};

// 250-byte maximum, so 1 (type) + 20 (sender_id) + 3 (chunk meta) + 5×37 = 209 bytes < 250-byte limit.
static constexpr uint8_t UUIDS_PER_CHUNK = 5;

// packed attribute removes padding so sizeof() matches the wire size exactly.

struct MsgHeader {
    MsgType type;
    char    sender_id[20];
} __attribute__((packed));

struct MsgHeartbeat {
    MsgHeader hdr;
} __attribute__((packed));

struct MsgData {
    MsgHeader hdr;
    char      uuid[37];
    int8_t    rssi;
} __attribute__((packed));

struct MsgUuidChunk {
    MsgHeader hdr;
    uint8_t   chunk_index;
    uint8_t   total_chunks;
    uint8_t   count;
    char      uuids[UUIDS_PER_CHUNK][37];
} __attribute__((packed));

struct MsgCommand {
    MsgHeader hdr;
    char      command[20];
} __attribute__((packed));