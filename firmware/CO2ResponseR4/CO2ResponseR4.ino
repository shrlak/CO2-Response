// CO2-Response firmware for the Arduino Uno R4 Minima board with digital sensors:
//
//   Sensirion SFM3300-250-D flow sensor  I2C 0x40, 5 V, wired straight to SDA/SCL
//   GSS SprintIR-R-20 CO2 sensor         Serial1 (D0/D1) at 9600 baud, 3.3 V logic through
//                                        the BSS138 level shifter, powered from 5V_EXT
//   ZR370-02PM sample pump               IRLZ44N Q1 on D10 (PWM), powered from 5V_EXT
//   valve reed switch D3, alarm LED D13 (built in), optional button D2, optional buzzer D8,
//   optional valve servo D6. USB to the computer through the ADuM4160 isolator.
//
// It streams one checksummed line per sample over USB (protocol: docs/protocol.md):
//
//   D,<seq>,<t_ms>,<CO2 %>,<flow L/min>*<checksum>
//
// The values are already in engineering units (the info line says UNITS,1), so the app
// does no volts-to-units conversion. A sensor that is not answering is sent as 9999.
// The CO2 value changes 50 times a second (the SprintIR-R's rate) and is repeated between;
// the flow is the mean of the SFM3300 reads (every SFM_READ_US) since the previous line.
//
// The board also reports its button ("A,BUTTON") and the valve handle's reed switch
// ("A,REED,1" at the bag, "A,REED,0" at room air), lights its LED and sounds its
// buzzer when the app asks ("ALARM 0|1|2"), and sounds them by itself if the app's
// heartbeat stops.
//
// RESEARCH USE ONLY. Not a medical device.

#include <Arduino.h>
#include <Wire.h>

#include "config.h"
#include "line_format.h"
#include "sensirion.h"
#include "sprintir.h"

#if SERVO_PIN >= 0
#include <Servo.h>
static Servo valveServo;
#endif

static Sfm3300 flowSensor;
static SprintIR co2Sensor(Serial1);

static bool streaming = AUTOSTART;
static uint16_t rateHz = DEFAULT_RATE_HZ;
static uint32_t periodUs = 1000000UL / DEFAULT_RATE_HZ;
static uint32_t nextUs = 0;
static uint16_t seq = 0;
static uint32_t lastOverrunMs = 0;
static uint32_t lastSfmRetryMs = 0;
static uint32_t nextSfmReadUs = 0;
static uint8_t pumpDuty = PUMP_DUTY;
static bool pumpOn = false;

static char cmd[32];
static uint8_t cmdLen = 0;

// alarm output asked for by the app: 0 none, 1 medium (LED blinks), 2 high (LED + buzzer)
static uint8_t alarmLevel = 0;
// host watchdog
static bool wdArmed = false;
static bool hostLost = false;
static uint32_t lastHbMs = 0;
static uint32_t lastLostMs = 0;
// button and reed switch, debounced
static bool buttonDown = false;
static uint32_t buttonChangedMs = 0;
static int8_t reedAtBag = -1;
static uint32_t reedChangedMs = 0;
static bool valveToBag = false;

static void setPump(bool on) {
  pumpOn = on;
  analogWrite(PUMP_PIN, on ? pumpDuty : 0);
}

static void setValve(bool toBag) {
  valveToBag = toBag;
#if SERVO_PIN >= 0
  valveServo.write(toBag ? SERVO_BAG_DEG : SERVO_AIR_DEG);
#endif
}

static void sendInfo() {
  Serial.print(F("I,FW," FW_VERSION ",BOARD,UNO_R4,UNITS,1,CO2,SPRINTIR-R,FLOW,SFM3300,CH,2,RATE,"));
  Serial.print(rateHz);
  Serial.print(F(",BAUD,"));
  Serial.print(BAUD_RATE);
  Serial.print(F(",ADDR,0x40"));
  if (flowSensor.serial) {
    Serial.print(F(",SFM_SN,"));
    Serial.print(flowSensor.serial);
  }
  Serial.print(F(",CO2_MULT,"));
  Serial.print(co2Sensor.multiplier());
  Serial.print(F(",PUMP_DUTY,"));
  Serial.print(pumpDuty);
#if WATCHDOG_TIMEOUT_MS > 0
  Serial.print(F(",WD,"));
  Serial.print(WATCHDOG_TIMEOUT_MS);
#endif
#if SERVO_PIN >= 0
  Serial.print(F(",VALVE,1"));
#endif
#if REED_PIN >= 0
  Serial.print(F(",REED,1"));
#endif
#if HEATER_PIN >= 0
  Serial.println(F(",ALARM,1,PUMP,1,HEAT,1"));
#else
  Serial.println(F(",ALARM,1,PUMP,1"));
#endif
}

// LED and buzzer: the host lost (worst), then the app's alarm level
static void serviceOutputs() {
  uint32_t now = millis();
#if WATCHDOG_TIMEOUT_MS > 0
  if (wdArmed && !hostLost && now - lastHbMs > WATCHDOG_TIMEOUT_MS) {
    hostLost = true;
    if (valveToBag) {  // nothing is watching the CO2 any more: back to room air
      setValve(false);
      Serial.println(F("A,VALVE,0"));
    }
  }
  if (hostLost && now - lastLostMs >= 1000) {
    lastLostMs = now;
    Serial.println(F("E,HOST_LOST"));
  }
#endif
  bool led, buzz;
  if (hostLost) {
    led = buzz = ((now / 250) & 1) == 0;
  } else if (alarmLevel == 2) {
    led = true;
    buzz = ((now / 500) & 1) == 0;
  } else if (alarmLevel == 1) {
    led = ((now / 500) & 1) == 0;
    buzz = false;
  } else {
    led = buzz = false;
  }
  digitalWrite(ALARM_LED_PIN, led ? HIGH : LOW);
#if BUZZER_PIN >= 0
  digitalWrite(BUZZER_PIN, buzz ? HIGH : LOW);
#else
  (void)buzz;
#endif
}

static void serviceInputs() {
  uint32_t now = millis();
  bool down = digitalRead(BUTTON_PIN) == LOW;
  if (down != buttonDown && now - buttonChangedMs > 30) {
    buttonDown = down;
    buttonChangedMs = now;
    if (down) Serial.println(F("A,BUTTON"));
  }
#if REED_PIN >= 0
  bool closed = digitalRead(REED_PIN) == LOW;
  int8_t atBag = (closed == (REED_CLOSED_IS_BAG != 0)) ? 1 : 0;
  if (atBag != reedAtBag && now - reedChangedMs > 50) {
    reedChangedMs = now;
    bool first = reedAtBag < 0;
    reedAtBag = atBag;
    if (!first || atBag) Serial.println(atBag ? F("A,REED,1") : F("A,REED,0"));
  }
#endif
}

static void setRate(uint16_t hz) {
  rateHz = hz;
  periodUs = 1000000UL / hz;
  nextUs = micros() + periodUs;
}

static void handleCommand(char* c) {
  for (char* p = c; *p; p++) *p = toupper(*p);
  if (strcmp(c, "HB") == 0) {
    wdArmed = true;
    lastHbMs = millis();
    hostLost = false;
  } else if (strcmp(c, "HB OFF") == 0) {
    wdArmed = false;
    hostLost = false;
    Serial.println(F("A,HB,OFF"));
  } else if (strncmp(c, "ALARM ", 6) == 0) {
    alarmLevel = (uint8_t)constrain(atoi(c + 6), 0, 2);
    Serial.print(F("A,ALARM,"));
    Serial.println(alarmLevel);
  } else if (strncmp(c, "PUMP ", 5) == 0) {
    // PUMP 0 = off, PUMP 1 = on, PUMP 2..255 = on at that PWM duty
    long v = atol(c + 5);
    if (v > 1) pumpDuty = (uint8_t)constrain(v, 0, 255);
    setPump(v != 0);
    Serial.print(F("A,PUMP,"));
    Serial.print(pumpOn ? 1 : 0);
    Serial.print(F(","));
    Serial.println(pumpDuty);
  } else if (strcmp(c, "CO2ZERO AIR") == 0) {
    co2Sensor.zeroInAir();  // the sample line must be in fresh air (taken as 400 ppm)
    Serial.println(F("A,CO2ZERO,AIR"));
  } else if (strcmp(c, "CO2ZERO N2") == 0) {
    co2Sensor.zeroInNitrogen();  // the sample line must be in pure nitrogen
    Serial.println(F("A,CO2ZERO,N2"));
#if HEATER_PIN >= 0
  } else if (strncmp(c, "HEAT ", 5) == 0) {
    analogWrite(HEATER_PIN, c[5] != '0' ? HEATER_DUTY : 0);
    Serial.println(c[5] != '0' ? F("A,HEAT,1") : F("A,HEAT,0"));
#endif
  } else if (strcmp(c, "VALVE 1") == 0 || strcmp(c, "VALVE 0") == 0) {
#if SERVO_PIN >= 0
    bool toBag = c[6] == '1' && !hostLost;  // to the bag only while the host is alive
    setValve(toBag);
    Serial.println(toBag ? F("A,VALVE,1") : F("A,VALVE,0"));
#else
    Serial.println(F("E,NO_VALVE"));
#endif
  } else if (strcmp(c, "INFO") == 0) {
    sendInfo();
#if REED_PIN >= 0
    if (reedAtBag == 1) Serial.println(F("A,REED,1"));
#endif
  } else if (strcmp(c, "START") == 0) {
    streaming = true;
    setPump(true);
    nextUs = micros() + periodUs;
    Serial.println(F("A,START"));
  } else if (strcmp(c, "STOP") == 0) {
    streaming = false;
    setPump(false);
    Serial.println(F("A,STOP"));
  } else if (strncmp(c, "RATE", 4) == 0) {
    long hz = atol(c + 4);
    if (hz < 1 || hz > MAX_RATE_HZ) {
      Serial.println(F("E,BAD_RATE"));
    } else {
      setRate((uint16_t)hz);
      Serial.print(F("A,RATE,"));
      Serial.println(hz);
    }
  } else if (c[0] != '\0') {
    Serial.println(F("E,UNKNOWN_CMD"));
  }
}

static void pollSerial() {
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      cmd[cmdLen] = '\0';
      if (cmdLen) handleCommand(cmd);
      cmdLen = 0;
    } else if (cmdLen < sizeof(cmd) - 1) {
      cmd[cmdLen++] = ch;
    }
  }
}

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
#if REED_PIN >= 0
  pinMode(REED_PIN, INPUT_PULLUP);
#endif
  pinMode(ALARM_LED_PIN, OUTPUT);
#if BUZZER_PIN >= 0
  pinMode(BUZZER_PIN, OUTPUT);
#endif
#if HEATER_PIN >= 0
  pinMode(HEATER_PIN, OUTPUT);
  analogWrite(HEATER_PIN, HEATER_DUTY);
#endif
  pinMode(PUMP_PIN, OUTPUT);
#if SERVO_PIN >= 0
  valveServo.attach(SERVO_PIN);
#endif
  setValve(false);  // room air until the host says otherwise
  setPump(streaming);
  Serial.begin(BAUD_RATE);
  co2Sensor.begin();
  if (!flowSensor.begin()) Serial.println(F("E,FLOW_NOT_FOUND"));
  sendInfo();
  setRate(rateHz);
}

void loop() {
  pollSerial();
  co2Sensor.poll();
  serviceInputs();
  serviceOutputs();

  uint32_t now = micros();
#if SFM_AVERAGE
  if ((int32_t)(now - nextSfmReadUs) >= 0) {
    nextSfmReadUs = now + SFM_READ_US;
    flowSensor.sample();
  }
#endif
  if ((int32_t)(now - nextUs) < 0) return;
  uint32_t missed = (now - nextUs) / periodUs;
  if (missed) {  // fell behind: skip the slots but count them, so the app sees the gap
    seq += (uint16_t)missed;
    nextUs += missed * periodUs;
    if (millis() - lastOverrunMs >= 1000) {
      lastOverrunMs = millis();
      Serial.println(F("E,OVERRUN"));
    }
  }
  nextUs += periodUs;

  float v[2];
  float co2 = co2Sensor.percent();
  float flow = flowSensor.take();
  if (isnan(flow) && millis() - lastSfmRetryMs > 1000) {
    lastSfmRetryMs = millis();
    flowSensor.begin();  // a loose cable or a reset sensor: start it again
  }
  v[0] = isnan(co2) ? NOT_ANSWERING : co2;
  v[1] = isnan(flow) ? NOT_ANSWERING : flow;
  if (streaming) {
    char line[64];
    int len = formatDataLine(line, seq, millis(), v, 2);
    line[len++] = '\n';
    Serial.write((const uint8_t*)line, len);
  }
  seq++;
}
