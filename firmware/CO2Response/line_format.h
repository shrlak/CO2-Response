// Data-line formatting, kept free of Arduino APIs so it can be unit-tested on a PC
// (firmware/test/test_line_format.cpp) against the dashboard's parser.
#pragma once
#include <math.h>
#include <stdint.h>
#include <stdio.h>

// Append v with exactly 4 decimals, without printf float support (AVR has none).
static inline int appendFixed4(char* out, float v) {
  int n = 0;
  long scaled = lroundf(v * 10000.0f);
  if (scaled < 0) {
    out[n++] = '-';
    scaled = -scaled;
  }
  n += sprintf(out + n, "%ld.%04ld", scaled / 10000L, scaled % 10000L);
  return n;
}

// Writes "D,<seq>,<t_ms>,<v0>,...*<XX>" (no newline) into out; returns its length.
// The checksum is the XOR of every byte after the leading 'D' and before '*'.
static inline int formatDataLine(char* out, uint16_t seq, uint32_t tMs, const float* v, uint8_t n) {
  int len = sprintf(out, "D,%u,%lu", (unsigned)seq, (unsigned long)tMs);
  for (uint8_t i = 0; i < n; i++) {
    out[len++] = ',';
    len += appendFixed4(out + len, v[i]);
  }
  uint8_t cs = 0;
  for (int i = 1; i < len; i++) cs ^= (uint8_t)out[i];
  len += sprintf(out + len, "*%02X", cs);
  return len;
}
