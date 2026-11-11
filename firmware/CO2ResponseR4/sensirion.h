// Sensirion SFM3304-D flow sensor over I2C: start continuous measurement, read flow.
#pragma once
#include <Arduino.h>
#include <Wire.h>

#include "config.h"

// CRC-8 used by Sensirion sensors: polynomial 0x31, init 0xFF
static inline uint8_t sensirionCrc(const uint8_t* d, uint8_t n) {
  uint8_t crc = 0xFF;
  for (uint8_t i = 0; i < n; i++) {
    crc ^= d[i];
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
  }
  return crc;
}

class Sfm3304 {
 public:
  float scale = SFM_DEFAULT_SCALE;
  float offset = SFM_DEFAULT_OFFSET;
  bool ok = false;
  uint16_t errors = 0;

  bool begin() {
    Wire.begin();
    Wire.setClock(I2C_CLOCK_HZ);
    command(SFM_CMD_STOP);  // in case it was already measuring
    delay(2);
    readScale();
    ok = command(SFM_CMD_START_AIR);
    delay(12);  // the first measurement is ready after about 12 ms
    return ok;
  }

  // Flow in standard L/min, or NAN when the sensor did not answer correctly.
  float readFlow() {
    uint8_t buf[3];
    if (Wire.requestFrom((uint8_t)SFM_I2C_ADDRESS, (uint8_t)3) != 3) return fail();
    for (uint8_t i = 0; i < 3; i++) buf[i] = Wire.read();
    if (sensirionCrc(buf, 2) != buf[2]) return fail();
    int16_t raw = (int16_t)((buf[0] << 8) | buf[1]);
    ok = true;
    return SFM_FLOW_SIGN * ((float)raw - offset) / scale;
  }

 private:
  float fail() {
    errors++;
    ok = false;
    return NAN;
  }

  bool command(uint16_t cmd) {
    Wire.beginTransmission(SFM_I2C_ADDRESS);
    Wire.write((uint8_t)(cmd >> 8));
    Wire.write((uint8_t)(cmd & 0xFF));
    return Wire.endTransmission() == 0;
  }

  // Ask the sensor for its own scale factor and offset for air measurements.
  void readScale() {
    uint8_t arg[2] = {(uint8_t)(SFM_CMD_START_AIR >> 8), (uint8_t)(SFM_CMD_START_AIR & 0xFF)};
    Wire.beginTransmission(SFM_I2C_ADDRESS);
    Wire.write((uint8_t)(SFM_CMD_READ_SCALE >> 8));
    Wire.write((uint8_t)(SFM_CMD_READ_SCALE & 0xFF));
    Wire.write(arg[0]);
    Wire.write(arg[1]);
    Wire.write(sensirionCrc(arg, 2));
    if (Wire.endTransmission() != 0) return;
    delay(1);
    uint8_t b[9];
    if (Wire.requestFrom((uint8_t)SFM_I2C_ADDRESS, (uint8_t)9) != 9) return;
    for (uint8_t i = 0; i < 9; i++) b[i] = Wire.read();
    if (sensirionCrc(b, 2) != b[2] || sensirionCrc(b + 3, 2) != b[5]) return;
    int16_t s = (int16_t)((b[0] << 8) | b[1]);
    int16_t o = (int16_t)((b[3] << 8) | b[4]);
    if (s > 0) {
      scale = s;
      offset = o;
    }
  }
};
