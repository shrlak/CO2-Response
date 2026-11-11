// GSS SprintIR-6S CO2 sensor on a hardware UART: streaming mode, "Z ##### z #####" lines.
#pragma once
#include <Arduino.h>

#include "config.h"

class SprintIR {
 public:
  explicit SprintIR(HardwareSerial& port) : port_(port) {}

  void begin() {
    port_.begin(CO2_BAUD);
    delay(50);
    port_.print(F("K 1\r\n"));  // streaming mode
    port_.print(F("M 6\r\n"));  // send Z (filtered) and z (raw)
    port_.print(F(".\r\n"));    // ask for the ppm multiplier
  }

  // Read whatever has arrived; returns true when a new CO2 value was parsed.
  bool poll() {
    bool fresh = false;
    while (port_.available()) {
      char c = (char)port_.read();
      if (c == '\n' || c == '\r') {
        if (len_) fresh |= parse();
        len_ = 0;
      } else if (len_ < sizeof(line_) - 1) {
        line_[len_++] = c;
      }
    }
    return fresh;
  }

  // CO2 in %, or NAN if the sensor has not answered for CO2_TIMEOUT_MS
  float percent() const {
    if (lastMs_ == 0 || millis() - lastMs_ > CO2_TIMEOUT_MS) return NAN;
    return ppm_ / 10000.0f;
  }

 private:
  HardwareSerial& port_;
  char line_[48];
  uint8_t len_ = 0;
  long multiplier_ = CO2_DEFAULT_MULTIPLIER;
  float ppm_ = 0;
  uint32_t lastMs_ = 0;

  bool parse() {
    line_[len_] = '\0';
    // the multiplier answer: ". 00010"
    if (line_[0] == '.' || (line_[0] == ' ' && line_[1] == '.')) {
      long m = atol(strchr(line_, '.') + 1);
      if (m > 0) multiplier_ = m;
      return false;
    }
    char want = CO2_USE_FILTERED ? 'Z' : 'z';
    for (char* p = line_; *p; p++) {
      if (*p == want && (p[1] == ' ')) {
        ppm_ = (float)atol(p + 2) * multiplier_;
        lastMs_ = millis();
        return true;
      }
    }
    return false;
  }
};
