// One interface for every ADC board, so swapping the board touches one file.
#pragma once
#include <Arduino.h>

class AdcDriver {
 public:
  virtual ~AdcDriver() {}
  // Returns false if the converter does not answer.
  virtual bool begin() = 0;
  // Volts at the converter input for logical channel ch (0-based).
  virtual float readVolts(uint8_t ch) = 0;
  virtual const char* name() const = 0;
  // Extra INFO fields, each starting with a comma (e.g. ",ADDR,0x48,FSR,6.144").
  virtual void printInfo(Print& out) const { (void)out; }
};
