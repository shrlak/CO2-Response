// CO2-Response firmware: a dumb, reliable sampler.
//
// Reads CH0 (CO2), CH1 (flow sensor) and CH2 (pneumotach) from the ADC on a fixed
// micros() schedule and streams volts as checksummed text lines. All unit
// conversion, breath detection and alarms happen in the dashboard, so
// recalibrating never requires reflashing. Protocol: docs/protocol.md
//
//   D,<seq>,<t_ms>,<v0>,<v1>,<v2>*<checksum>
//
// RESEARCH USE ONLY. Not a medical device.

#include <Arduino.h>

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

static char cmd[32];
static uint8_t cmdLen = 0;

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
  Serial.println(BAUD_RATE);
}

static void setRate(uint16_t hz) {
  rateHz = hz;
  periodUs = 1000000UL / hz;
  nextUs = micros() + periodUs;
}

static void handleCommand(char* c) {
  for (char* p = c; *p; p++) *p = toupper(*p);
  if (strcmp(c, "INFO") == 0) {
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
  Serial.begin(BAUD_RATE);
  adcOk = adc->begin();
  if (adcOk) sendInfo();
  else Serial.println(F("E,ADC_NOT_FOUND"));
  setRate(rateHz);
}

void loop() {
  pollSerial();

  if (!adcOk) {
    // Never stream zeros from a missing ADC: report and keep retrying.
    if (millis() - lastErrMs >= 1000) {
      lastErrMs = millis();
      adcOk = adc->begin();
      if (adcOk) sendInfo();
      else Serial.println(F("E,ADC_NOT_FOUND"));
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
