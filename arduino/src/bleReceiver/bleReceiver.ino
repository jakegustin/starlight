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

int scanTime = 5;  //In seconds
BLEScan *pBLEScan;

class MyAdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {
    if (advertisedDevice.getServiceUUIDCount() <= 0) {
      return;
    }

    const char* name = advertisedDevice.haveName() ? advertisedDevice.getName().c_str() : "unknown";
    Serial.print("Advertiser: ");
    Serial.print(name);
    Serial.print(" | RSSI=");
    Serial.print(advertisedDevice.getRSSI());
    Serial.print(" | UUID=");
    Serial.print(advertisedDevice.getServiceUUID().toString().c_str());
    Serial.print(" | Address=");
    Serial.println(advertisedDevice.getAddress().toString().c_str());
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

  pBLEScan->start(0, false);
}

void loop() {
  delay(2000);
}