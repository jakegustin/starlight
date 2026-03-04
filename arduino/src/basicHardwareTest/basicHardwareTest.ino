int current = 1;
bool goingUp = true;

void setup() {
  // put your setup code here, to run once:
  Serial.begin(115200);
  pinMode(25, OUTPUT);
  pinMode(26, OUTPUT);
  digitalWrite(25, HIGH);
}

void loop() {
  if (current == 255) {
    goingUp = false;
    Serial.println("GOING DOWN");
  } else if (current == 0) {
    goingUp = true;
    Serial.println("GOING UP");
  }

  if (goingUp) {
    current += 1;
  } else {
    current -= 1;
  }

  analogWrite(26, current);


  delay(1);
}
