// Host-side check that the firmware's data lines match the dashboard's formatter
// byte for byte (expected strings come from web/src/protocol/protocol.ts).
//   g++ -std=c++11 -Wall -Wextra -I CO2Response test/test_line_format.cpp -o /tmp/t && /tmp/t
#include <stdio.h>
#include <string.h>

#include "line_format.h"

struct Case {
  uint16_t seq;
  uint32_t t;
  float v[3];
  const char* expected;
};

int main() {
  const Case cases[] = {
      {0, 0, {0.0f, 0.0f, 0.0f}, "D,0,0,0.0000,0.0000,0.0000*32"},
      {1042, 10420, {2.6316f, 2.5f, 2.5f}, "D,1042,10420,2.6316,2.5000,2.5000*02"},
      {65535, 4294967295UL, {4.9999f, 0.0001f, -0.5f}, "D,65535,4294967295,4.9999,0.0001,-0.5000*22"},
      {7, 123456, {1.23456f, 3.14159f, 0.00004f}, "D,7,123456,1.2346,3.1416,0.0000*01"},
  };
  int failures = 0;
  for (const Case& c : cases) {
    char line[96];
    formatDataLine(line, c.seq, c.t, c.v, 3);
    if (strcmp(line, c.expected) != 0) {
      printf("FAIL\n  got      %s\n  expected %s\n", line, c.expected);
      failures++;
    }
  }
  printf("%d/%d line-format cases passed\n", (int)(sizeof cases / sizeof cases[0]) - failures, (int)(sizeof cases / sizeof cases[0]));
  return failures ? 1 : 0;
}
