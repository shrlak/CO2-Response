// ADS1115 (16-bit, I2C) driver using the Adafruit ADS1X15 library.
#pragma once
#include <Adafruit_ADS1X15.h>
#include <Wire.h>

#include "adc_driver.h"
#include "config.h"

class Ads1115Driver : public AdcDriver {
 public:
  bool begin() override {
    Wire.begin();
    Wire.setClock(I2C_CLOCK_HZ);
    if (!ads_.begin(ADS_I2C_ADDRESS)) return false;
    ads_.setGain(ADS_GAIN);
    ads_.setDataRate(RATE_ADS1115_860SPS);  // ~1.2 ms per conversion
    return true;
  }

  float readVolts(uint8_t ch) override {
    int16_t counts;
#if PNEUMO_DIFFERENTIAL
    if (ch == 2) {
      counts = ads_.readADC_Differential_2_3();
      return ads_.computeVolts(counts);
    }
#endif
    counts = ads_.readADC_SingleEnded(ch);
    return ads_.computeVolts(counts);
  }

  const char* name() const override { return "ADS1115"; }

 private:
  Adafruit_ADS1115 ads_;
};
