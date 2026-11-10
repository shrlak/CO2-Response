// CO2-Response firmware: a dumb, reliable sampler.
//
// Reads CH0 (CO2), CH1 (flow sensor) and CH2 (pneumotach) from the ADC on a fixed
// micros() schedule and streams volts as checksummed text lines. All unit
// conversion, breath detection and alarms happen in the dashboard, so
// recalibrating never requires reflashing. Protocol: docs/protocol.md
//
//   D,<seq>,<t_ms>,<v0>,<v1>,<v2>*<checksum>
//
// It also watches for the host's heartbeat ("HB"): if the monitor app stops sending
// it, the board sounds its own buzzer, because the CO2 alarms are no longer checked.
//
// RESEARCH USE ONLY. Not a medical device.

#include <Arduino.h>

#include <Wire.h>

#include "ads1115_driver.h"
#include "config.h"
#include "line_format.h"

static Ads1115Driver adcBoard;      // ADC driver: change this line to swap boards
static AdcDriver* adc = &adcBoard;  // ADC driver

static bool adcOk = false;
static bool streaming = AUTOSTART;
static uint16_t rateHz = DEFAULT_RATE_HZ;
static uint32_t periodUs = 1000000UL / DEFAULT_RATE_HZ;
static uint32_t nextUs = 0;
static uint16_t seq = 0;
static uint32_t lastErrMs = 0;
static uint32_t lastOverrunMs = 0;
static uint8_t scanCountdown = 0;

static char cmd[32];
static uint8_t cmdLen = 0;

// host watchdog (see config.h)
static bool wdArmed = false;
static bool hostLost = false;
static bool buzzing = false;
static uint32_t lastHbMs = 0;
static uint32_t lastLostMs = 0;

// valve output (see config.h): true = to the bag (rebreathing), false = room air
static bool valveToBag = false;

static void setValve(bool toBag) {
  valveToBag = toBag;
#if VALVE_PIN >= 0
  digitalWrite(VALVE_PIN, toBag ? HIGH : LOW);
#endif
}

static void alarmOutput(bool on) {
  if (on == buzzing) return;
  buzzing = on;
  digitalWrite(ALARM_LED_PIN, on ? HIGH : LOW);
#if BUZZER_PIN >= 0
  if (on) tone(BUZZER_PIN, BUZZER_HZ);
  else noTone(BUZZER_PIN);
#endif
}

// Sound the buzzer in 250 ms pulses while the host's heartbeat is missing.
static void serviceWatchdog() {
#if WATCHDOG_TIMEOUT_MS > 0
  uint32_t now = millis();
  if (wdArmed && !hostLost && now - lastHbMs > WATCHDOG_TIMEOUT_MS) {
    hostLost = true;
    if (valveToBag) {  // nothing is watching the CO2 any more: back to room air
      setValve(false);
      Serial.println(F("A,VALVE,0"));
    }
  }
  if (!hostLost) return;
  alarmOutput(((now / 250) & 1) == 0);
  if (now - lastLostMs >= 1000) {
    lastLostMs = now;
    Serial.println(F("E,HOST_LOST"));
  }
#endif
}

static void heartbeat(bool on) {
  wdArmed = on;
  lastHbMs = millis();
  if (hostLost || !on) {
    hostLost = false;
    alarmOutput(false);
  }
}

static void sendSample(uint16_t s, uint32_t tMs, const float* v) {
  char line[96];
  int len = formatDataLine(line, s, tMs, v, NUM_CHANNELS);
  line[len++] = '\n';
  Serial.write((const uint8_t*)line, len);
}

static void sendInfo() {
  Serial.print(F("I,FW," FW_VERSION ",ADC,"));
  Serial.print(adc->name());
  Serial.print(F(",CH,"));
  Serial.print(NUM_CHANNELS);
  Serial.print(F(",RATE,"));
  Serial.print(rateHz);
  Serial.print(F(",BAUD,"));
  Serial.print(BAUD_RATE);
  adc->printInfo(Serial);
#if WATCHDOG_TIMEOUT_MS > 0
  Serial.print(F(",WD,"));  // tells the host to send heartbeats
  Serial.print(WATCHDOG_TIMEOUT_MS);
#endif
#if VALVE_PIN >= 0
  Serial.print(F(",VALVE,1"));  // this board has a valve output
#endif
  Serial.println();
}

// List every I2C address that answers, so a wrong ADDR pin or a missing wire is
// obvious in the dashboard (Settings > Diagnostics): "I,I2C_SCAN,0x49" or "none".
static void i2cScan() {
  Serial.print(F("I,I2C_SCAN,"));
  bool any = false;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      if (any) Serial.print(' ');
      Serial.print(F("0x"));
      if (a < 16) Serial.print('0');
      Serial.print(a, HEX);
      any = true;
    }
  }
  if (!any) Serial.print(F("none"));
  Serial.println();
}

static void setRate(uint16_t hz) {
  rateHz = hz;
  periodUs = 1000000UL / hz;
  nextUs = micros() + periodUs;
}

static void handleCommand(char* c) {
  for (char* p = c; *p; p++) *p = toupper(*p);
  if (strcmp(c, "HB") == 0) {
    heartbeat(true);  // no reply: it arrives once a second
  } else if (strcmp(c, "HB OFF") == 0) {
    heartbeat(false);  // the host is disconnecting on purpose
    Serial.println(F("A,HB,OFF"));
  } else if (strcmp(c, "VALVE 1") == 0 || strcmp(c, "VALVE 0") == 0) {
#if VALVE_PIN >= 0
    // to the bag only while the host is known to be alive
    bool toBag = c[6] == '1' && !hostLost;
    setValve(toBag);
    Serial.println(toBag ? F("A,VALVE,1") : F("A,VALVE,0"));
#else
    Serial.println(F("E,NO_VALVE"));
#endif
  } else if (strcmp(c, "INFO") == 0) {
    sendInfo();
  } else if (strcmp(c, "START") == 0) {
    streaming = true;
    nextUs = micros() + periodUs;
    Serial.println(F("A,START"));
  } else if (strcmp(c, "STOP") == 0) {
    streaming = false;
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
#if VALVE_PIN >= 0
  pinMode(VALVE_PIN, OUTPUT);
  digitalWrite(VALVE_PIN, LOW);  // room air until the host says otherwise
#endif
  pinMode(ALARM_LED_PIN, OUTPUT);
#if BUZZER_PIN >= 0
  pinMode(BUZZER_PIN, OUTPUT);
#endif
  Serial.begin(BAUD_RATE);
  adcOk = adc->begin();
  if (adcOk) {
    sendInfo();
  } else {
    Serial.println(F("E,ADC_NOT_FOUND"));
    i2cScan();
  }
  setRate(rateHz);
}

void loop() {
  pollSerial();
  serviceWatchdog();

  if (!adcOk) {
    // Never stream zeros from a missing ADC: report and keep retrying.
    if (millis() - lastErrMs >= 1000) {
      lastErrMs = millis();
      adcOk = adc->begin();
      if (adcOk) {
        sendInfo();
      } else {
        Serial.println(F("E,ADC_NOT_FOUND"));
        if (++scanCountdown >= 5) {  // re-scan every 5 s
          scanCountdown = 0;
          i2cScan();
        }
      }
    }
    return;
  }

  uint32_t now = micros();
  if ((int32_t)(now - nextUs) < 0) return;

  // If we fell behind, skip the missed slots but still advance seq so the
  // dashboard counts them as dropped samples.
  uint32_t missed = (now - nextUs) / periodUs;
  if (missed) {
    seq += (uint16_t)missed;
    nextUs += missed * periodUs;
    if (millis() - lastOverrunMs >= 1000) {
      lastOverrunMs = millis();
      Serial.println(F("E,OVERRUN"));
    }
  }
  nextUs += periodUs;

  uint32_t tMs = millis();
  float v[NUM_CHANNELS];
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) v[i] = adc->readVolts(i);
  if (streaming) sendSample(seq, tMs, v);
  seq++;
}
