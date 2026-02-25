// PWM Demo
int mycolor = 0;               // The color to be made brighter
int lastcolor = 3;             // The color to be made dimmer
int pins[] = {25, 26, 27, 14}; // The pins corresponding to the test LEDs
int current_cycle_num = 0;     // How far along the color transition should be
int passed_first_run = 0;      // Variable that ensures there is no 2nd LED during the first transition

// "BLE" Demo
int ble1pins[] = {12, 13};                    // The pins corresponding to Receiver 1's LEDs used in this demo
int ble2pins[] = {9, 10};                     // The pins corresponding to Receiver 1's LEDs used in this demo
int prioritySliderPins[] = {34, 32};          // The pins corresponding to the priority user at the 2 receivers
int standardSliderPins[] = {35, 33};          // The pins corresponding to the standard user at the 2 receivers
int queue[2];                                 // The current user queue state (will be in central controller module)
float userTimeout[2][2];                      // The times at each receiver where we deem a user to have exited the queue
int rssiHistory[2][2][10];                    // History of RSSI for averaging: divided by receiver, then by user.
float currentTime = (float)millis() / 1000.0; // Track the current time (to determine timeout activation)

int priorityUserZoneHistory[] = {0, 0};
int standardUserZoneHistory[] = {0, 0};

void setup() {
  // Open serial connection to transmit data (for development/logging purposes)
  // NOTE: Keep baum at 115200 and not a low value like 9600: buffering can slow down the core loop
  Serial.begin(115200);

  // Set the PWM demo GPIO pins to output mode
  for (int i = 0; i < sizeof(pins) / sizeof(int); i++){
    pinMode(pins[i], OUTPUT);
  }

  // Set the BLE demo GPIO pins to output mode and initialize userTimeout
  for (int i = 0; i < sizeof(ble1pins) / sizeof(int); i++){
    pinMode(ble1pins[i], OUTPUT);
    pinMode(ble2pins[i], OUTPUT);

    for (int j = 0; j < 10; j++) {
      userTimeout[i][j] = -1.0;
      rssiHistory[i][0][j] = -1;
      rssiHistory[i][1][j] = -1;
    }
  }

  // Print confirmation message for logging purposes
  Serial.println("Barrow Online"); 
  // Using "Barrow" naming for now. This will change.
  // Barrow is kind of a fun word to say.
}

void loop() { 
  // ##################
  // # BLE COMMS DEMO #
  // ##################

  if (current_cycle_num % 50 == 0) {
    // Get current "BLE RSSI" (potentiometer readings for demo)
    int priority1 = map(analogRead(prioritySliderPins[0]), 0, 4095, 0, 180);
    int priority2 = map(analogRead(prioritySliderPins[1]), 0, 4095, 0, 180);
    int standard1 = map(analogRead(standardSliderPins[0]), 0, 4095, 0, 180);
    int standard2 = map(analogRead(standardSliderPins[1]), 0, 4095, 0, 180);

    // Add some randomness to simulate RSSI noise
    priority1 = max(priority1 + (int)random(-10, 11), 0);
    priority2 = max(priority2 + (int)random(-10, 11), 0);
    standard1 = max(standard1 + (int)random(-10, 11), 0);
    standard2 = max(standard2 + (int)random(-10, 11), 0);

    // Print the current values for debugging purposes
    Serial.println((String)"Priority @ Receiver 1: " + priority1);
    Serial.println((String)"Priority @ Receiver 2: " + priority2);
    Serial.println((String)"Standard @ Receiver 1: " + standard1);
    Serial.println((String)"Standard @ Receiver 2: " + standard2);

    // Get timing info just to check
    currentTime = (float)millis() / 1000.0;
    Serial.println((String)"CURRENT: " + currentTime);
    
    // ------- RECEIVER 1 -------

    // RECEIVER 1: If our priority user entered the queue, light the zone for them as they need
    if (priority1 > 30 && priorityUserZoneHistory[1] == 0) {
      analogWrite(ble1pins[0], 255);
      analogWrite(ble1pins[1], 0);
      priorityUserZoneHistory[0] = 1;
    } else {
      analogWrite(ble1pins[0], 0);
    }
    
    // RECEIVER 1: If our standard user entered the queue, note that.
    if (standard1 > 30 && standardUserZoneHistory[1] == 0) {
      standardUserZoneHistory[0] = 1;

      // RECEIVER 1: If the priority user is not in the zone or moved ahead already, light the zone for the standard user
      if (priority1 <= 30 || priorityUserZoneHistory[1] == 1) {
        analogWrite(ble1pins[1], 255);
      } else {
        analogWrite(ble1pins[1], 0);
      }
    } else {
      analogWrite(ble1pins[1], 0);
    }

    // ------- RECEIVER 2 -------

    // RECEIVER 2: If our priority user entered the next zone, light the zone for them as they need
    if (priority2 > 30 && priorityUserZoneHistory[0] == 1) {
      analogWrite(ble2pins[0], 255);
      analogWrite(ble2pins[1], 0);
      priorityUserZoneHistory[1] = 1;
    } else {
      analogWrite(ble2pins[0], 0);
    }
    
    // RECEIVER 2: If our standard user entered the next zone, note that.
    if (standard2 > 30 && standardUserZoneHistory[0] == 1) {
      standardUserZoneHistory[1] = 1;

      // RECEIVER 2: If the priority user is not in the zone or left the queue, light the zone for the standard user
      if (priority2 <= 30 || priorityUserZoneHistory[0] == 0) {
        analogWrite(ble2pins[1], 255);
      } else {
        analogWrite(ble2pins[1], 0);
      }
    } else {
      analogWrite(ble2pins[1], 0);
    }

    // RECCEIVER 2: Check to confirm whether the users have left the queue
    if (priority2 <= 30 && priorityUserZoneHistory[1] == 1) {
        priorityUserZoneHistory[0] = 0;
        priorityUserZoneHistory[1] = 0;
    }

   if (standard2 <= 30 && standardUserZoneHistory[1] == 1) {
        standardUserZoneHistory[0] = 0;
        standardUserZoneHistory[1] = 0;
    }
  }

  // ##################
  // # PWM LIGHT DEMO #
  // ##################

  // Make the target LED brighter
  analogWrite(pins[mycolor], current_cycle_num);

  // Assuming this isn't the very first time, also dim the previous light at the same pace
  if (passed_first_run != 0)
    analogWrite(pins[lastcolor], 255 - current_cycle_num);

  // If the LEDs are fully lit/dim, select the next LEDs and reset the count
  if (current_cycle_num == 255) {
    current_cycle_num = 0;
    mycolor = (mycolor + 1) % 4;
    lastcolor = (lastcolor + 1) % 4;

    // If this was the first time we lit up an LED, signal that we can now start dimming previous LEDs
    if (passed_first_run == 0)
      passed_first_run = 1;
  } else {
    current_cycle_num += 1;
  }
  delay(1);
}
