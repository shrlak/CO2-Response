// GSS SprintIR-R CO2 sensor on a hardware UART: streaming mode, " z #####" lines 50 times a second.
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
    // one field: the CO2 value the app uses (the datasheet allows two at most at 50 Hz)
    port_.print(CO2_USE_FILTERED ? F("M 4\r\n") : F("M 2\r\n"));
    port_.print(F(".\r\n"));    // ask for the ppm multiplier
  }

  // Zero the sensor: in fresh air (it takes the air as 400 ppm) or in nitrogen (0 ppm).
  // Have the sample line in that gas, with the pump running, for a minute first.
  void zeroInAir() { port_.print(F("G\r\n")); }
  void zeroInNitrogen() { port_.print(F("U\r\n")); }

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

  long multiplier() const { return multiplier_; }

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
