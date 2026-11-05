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
#if defined(WIRE_HAS_TIMEOUT) || defined(ARDUINO_ARCH_AVR)  // AVR core 1.8.3+ has setWireTimeout
    // A loose SDA/SCL wire must not hang the sketch: give up on a stuck bus after 3 ms.
    Wire.setWireTimeout(3000, true);
#endif
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

  void printInfo(Print& out) const override {
    out.print(F(",ADDR,0x"));
    out.print(ADS_I2C_ADDRESS, HEX);
    out.print(F(",FSR,"));
    out.print(fullScaleVolts(), 3);
  }

  // Full-scale input range set by the PGA gain (inputs still never exceed VDD).
  static float fullScaleVolts() {
    switch (ADS_GAIN) {
      case GAIN_TWOTHIRDS: return 6.144f;
      case GAIN_ONE: return 4.096f;
      case GAIN_TWO: return 2.048f;
      case GAIN_FOUR: return 1.024f;
      case GAIN_EIGHT: return 0.512f;
      default: return 0.256f;
    }
  }

 private:
  Adafruit_ADS1115 ads_;
};
