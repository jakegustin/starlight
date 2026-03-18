/*
   Based on Neil Kolban example for IDF: https://github.com/nkolban/esp32-snippets/blob/master/cpp_utils/tests/BLE%20Tests/SampleScan.cpp
   Ported to Arduino ESP32 by Evandro Copercini

   Original Source for this code: https://github.com/espressif/arduino-esp32/blob/master/libraries/BLE/examples/Scan/Scan.ino
*/

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>

#define MAX_UUIDS 128
#define RECEIVER_NAME "Receiver A"
#define IDENTITY_LED_PIN 25
#define HEARTBEAT_MS 5000

// Initializing some variables. How exciting.
BLEScan *pBLEScan;
String serialLineBuffer;
unsigned long lastHeartbeatMs = 0;

bool blinkEnabled = false;
bool blinkLedOn = false;
int blinkTransitionsRemaining = 0;
int blinkDuration = 180;
unsigned long nextBlinkToggleMs = 0;

/*
    ***************
    BLINK REQUESTS
    ****************
*/

// Initiates a blink sequence from a controller's request
void startBlinkSequence() {
  const int blinks = 3;
  const int onMs = 180;
  const int offMs = 180;

  blinkEnabled = true;
  blinkLedOn = true;
  blinkTransitionsRemaining = blinks * 2;
  digitalWrite(IDENTITY_LED_PIN, HIGH);
  nextBlinkToggleMs = millis() + blinkDuration;
}

// Updates the identity/status LED based upon the current blink progress
void updateBlinker() {
  // If we're not in the process of blinking, leave it on
  if (!blinkEnabled || blinkTransitionsRemaining <= 0) {
    digitalWrite(IDENTITY_LED_PIN, HIGH);
    return;
  }

  // If it's not time for a transition yet, don't change anything
  unsigned long now = millis();
  if (now < nextBlinkToggleMs) {
    return;
  }

  // Transition time: invert the LED signal!
  blinkLedOn = !blinkLedOn;
  digitalWrite(IDENTITY_LED_PIN, blinkLedOn ? HIGH : LOW);
  blinkTransitionsRemaining -= 1;

  // If that was the last transition, indicate that blinking is no longer needed
  if (blinkTransitionsRemaining <= 0) {
    blinkEnabled = false;
    blinkLedOn = false;
    digitalWrite(IDENTITY_LED_PIN, HIGH);
    return;
  }

  // Update the interval accordingly
  nextBlinkToggleMs = now + blinkDuration;
}

/*
    ***************
      SERIAL CONNS
    ****************
*/

// Processes a single line received via Serial connection
void handleIncomingSerialLine(const String& line) {
  // If the line is empty, just return early: nothing to process
  if (line.length() == 0) {
    return;
  }

  // Remove spaces for easier processing
  String compact = line;
  compact.replace(" ", "");

  // If the line doesn't have a valid request, do nothing
  if (compact.indexOf("\"type\":\"command\"") < 0) {
    return;
  }
  if (compact.indexOf("\"command\":\"blink\"") < 0) {
    return;
  }

  // Blink request identified: let's execute it!
  startBlinkSequence();
}

// Handles a data stream received from a Serial connection
void processIncomingSerial() {
  // Continue while the serial connection is online
  while (Serial.available() > 0) {

    // Read character by character, not stopping until we hit a newline
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (serialLineBuffer.length() > 0) {
        handleIncomingSerialLine(serialLineBuffer);
        serialLineBuffer = "";
      }
      continue;
    }

    // If the existing buffer is at capacity, don't cause a buffer overflow
    if (serialLineBuffer.length() < 255) {
      serialLineBuffer += ch;
    } else {
      serialLineBuffer = "";
    }
  }
}

/*
    ***************
     BLE RECEIVING
    ****************
*/

class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {
    if (advertisedDevice.getServiceUUIDCount() <= 0) {
      return;
    }

    // Cache the UUID string (avoid calling toString().c_str() multiple times on a temporary)
    String uuid = advertisedDevice.getServiceUUID().toString();
    if (strcmp(uuid.c_str(), "12345678-1234-1234-1234-12345678abcd") != 0) {
      return;
    }

    char buf[256];
    int len = snprintf(
        buf, sizeof(buf),
        "{\"id\":\"%s\",\"type\":\"data\",\"rssi\":%d,\"uuid\":\"%s\"}\n",
        RECEIVER_NAME,
        advertisedDevice.getRSSI(),
        uuid.c_str()
    );

    if (len > 0 && len < (int)sizeof(buf)) {
      Serial.write((uint8_t*)buf, len);
    }
  }
};

/*
    ***************
       CORE FUNCS
    ****************
*/

void setup() {
  // Initialize Serial connection
  Serial.begin(115200);
  delay(1000);
  Serial.println("Scanning...");

  // Set our identity/status pin to start high/on
  pinMode(IDENTITY_LED_PIN, OUTPUT);
  digitalWrite(IDENTITY_LED_PIN, HIGH);

  // Initialize the BLE Receiver
  BLEDevice::init("");
  pBLEScan = BLEDevice::getScan();
  pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks(), true);
  pBLEScan->setActiveScan(true); // More power consumption, but lower latency!
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(99);

  pBLEScan->start(0, nullptr);
}

void loop() {
  // Handle any messages from the controller
  processIncomingSerial();

  // Blink the identity/status LED if the controller dictates it
  updateBlinker();

  // If it has been HEARTBEAT_MS ms since the last heartbeat, send a new one out
  unsigned long now = millis();
  if (now - lastHeartbeatMs >= HEARTBEAT_MS) {
    lastHeartbeatMs = now;
    Serial.printf("{\"id\": \"%s\", \"type\": \"heartbeat\"}\n",
          RECEIVER_NAME
    );
  }

  delay(5);
}