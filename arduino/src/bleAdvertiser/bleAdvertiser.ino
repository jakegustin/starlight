/*
    Based on Neil Kolban example for IDF: https://github.com/nkolban/esp32-snippets/blob/master/cpp_utils/tests/BLE%20Tests/SampleServer.cpp
    Ported to Arduino ESP32 by Evandro Copercini
    updates by chegewara

    Original Source for this code: https://github.com/espressif/arduino-esp32/blob/master/libraries/BLE/examples/Server/Server.ino
*/

#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>

// See the following for generating UUIDs:
// https://www.uuidgenerator.net/

#define SERVICE_UUID "12345678-1234-1234-1234-12345678abcd"

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("Starting BLE Advertiser!");

  if (!BLEDevice::init("Starlight-Example-Advertiser")) {
    Serial.println("BLE initialization failed!");
    return;
  }

  BLEServer *pServer = BLEDevice::createServer();
  BLEService *pService = pServer->createService(SERVICE_UUID);

  pService->start();
  // BLEAdvertising *pAdvertising = pServer->getAdvertising();  // this still is working for backward compatibility
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);  // functions that help with iPhone connections issue
  pAdvertising->setMaxPreferred(0x12);
  BLEDevice::startAdvertising();
  Serial.println("Advertisement started!");
}

void loop() {
  // put your main code here, to run repeatedly:
  delay(2000);
}