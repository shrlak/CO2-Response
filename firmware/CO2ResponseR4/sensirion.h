// Sensirion SFM3300-250-D flow sensor over I2C: start continuous measurement, read flow.
//
// The SFM3000 family (SFM3000, SFM3200, SFM3300, SFM3400) shares this interface:
// address 0x40, command 0x1000 starts continuous measurement, each read returns two
// data bytes and a CRC-8 (polynomial 0x31, initial value 0x00), and
// flow = (raw - offset) / scale in standard litres per minute.
#pragma once
#include <Arduino.h>
#include <Wire.h>

#include "config.h"

// CRC-8 of the SFM3000 family: polynomial 0x31 (x^8 + x^5 + x^4 + 1), initial value 0x00
static inline uint8_t sfmCrc(const uint8_t* d, uint8_t n) {
  uint8_t crc = 0x00;
  for (uint8_t i = 0; i < n; i++) {
    crc ^= d[i];
    for (uint8_t b = 0; b < 8; b++) crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
  }
  return crc;
}

class Sfm3300 {
 public:
  bool ok = false;
  uint16_t errors = 0;
  uint32_t serial = 0;

  bool begin() {
    Wire.begin();
    Wire.setClock(I2C_CLOCK_HZ);
    command(SFM_CMD_RESET);
    delay(100);  // the sensor needs up to 100 ms after a reset
    readSerial();
    ok = command(SFM_CMD_START);
    delay(1);
    float dummy;
    readOnce(dummy);  // the first value after the start command is not valid
    sum_ = 0;
    n_ = 0;
    return ok;
  }

  // Read the sensor now and add the value to the running mean (call every SFM_READ_US).
  void sample() {
    float f;
    if (readOnce(f)) {
      sum_ += f;
      n_++;
    }
  }

  // The flow for this data line, standard L/min: the mean of the reads since the last
  // call, or a fresh read. NAN when the sensor did not answer correctly.
  float take() {
    float f;
    if (n_ == 0) {
      if (!readOnce(f)) return NAN;
    } else {
      f = sum_ / n_;
    }
    sum_ = 0;
    n_ = 0;
    return f;
  }

 private:
  float sum_ = 0;
  uint16_t n_ = 0;

  bool readOnce(float& out) {
    uint8_t buf[3];
    if (Wire.requestFrom((uint8_t)SFM_I2C_ADDRESS, (uint8_t)3) != 3) return fail();
    for (uint8_t i = 0; i < 3; i++) buf[i] = Wire.read();
    if (sfmCrc(buf, 2) != buf[2]) return fail();
    uint16_t raw = (uint16_t)((buf[0] << 8) | buf[1]);
    ok = true;
    out = SFM_FLOW_SIGN * ((float)raw - SFM_OFFSET) / SFM_SCALE;
    return true;
  }

  bool fail() {
    errors++;
    ok = false;
    return false;
  }

  bool command(uint16_t cmd) {
    Wire.beginTransmission(SFM_I2C_ADDRESS);
    Wire.write((uint8_t)(cmd >> 8));
    Wire.write((uint8_t)(cmd & 0xFF));
    return Wire.endTransmission() == 0;
  }

  // The serial number goes in the info line, so a recording says which sensor made it.
  void readSerial() {
    if (!command(SFM_CMD_SERIAL)) return;
    delay(1);
    uint8_t b[6];
    if (Wire.requestFrom((uint8_t)SFM_I2C_ADDRESS, (uint8_t)6) != 6) return;
    for (uint8_t i = 0; i < 6; i++) b[i] = Wire.read();
    if (sfmCrc(b, 2) != b[2] || sfmCrc(b + 3, 2) != b[5]) return;
    serial = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[3] << 8) | b[4];
  }
};
