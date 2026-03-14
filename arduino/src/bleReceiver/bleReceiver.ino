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

char validUUIDs[MAX_UUIDS][37]; // 37 chars in UUID, including null terminator
BLEScan *pBLEScan;

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

    const char* deviceName = advertisedDevice.haveName() ? advertisedDevice.getName().c_str() : "unknown";
    char buf[256];
    int len = snprintf(
        buf, sizeof(buf),
        "{\"id\":\"%s\",\"type\":\"data\",\"devicename\":\"%s\",\"rssi\":%d,\"uuid\":\"%s\"}\n",
        RECEIVER_NAME,
        deviceName,
        advertisedDevice.getRSSI(),
        uuid.c_str()
    );

    if (len > 0 && len < (int)sizeof(buf)) {
      Serial.write((uint8_t*)buf, len);
    }
  }
};

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("Scanning...");

  BLEDevice::init("");
  pBLEScan = BLEDevice::getScan();  //create new scan
  pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks(), true);
  pBLEScan->setActiveScan(true);  //active scan uses more power, but get results faster
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(99);  // less or equal setInterval value

  pBLEScan->start(0, nullptr);
}

void loop() {
  delay(5000);
  Serial.printf("{\"id\": \"%s\", \"type\": \"heartbeat\"}\n",
        RECEIVER_NAME
  );
}