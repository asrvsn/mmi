#include "light.h"

#include <Arduino.h>

#include "board.h"

namespace {
const uint8_t PIN[LIGHT_N] = {pin::DIM_W, pin::DIM_C};
uint16_t code[LIGHT_N] = {0, 0};
}  // namespace

void lightBegin() {
  for (uint8_t c = 0; c < LIGHT_N; c++) {
    analogWriteFrequency(PIN[c], hw::LIGHT_PWM_HZ);   // 150 MHz / 4096: one count a step
    analogWrite(PIN[c], 0);                           // dark, as safePins() left it
    code[c] = 0;
  }
}

bool lightSet(uint8_t ch, float pct) {
  uint16_t k = lightCode(pct);
  if (k == code[ch]) return false;
  code[ch] = k;
  analogWrite(PIN[ch], k);   // the filter settles the set-point in ~50-75 ms
  return true;
}

float lightNow(uint8_t ch) { return lightLevel(code[ch]); }

size_t lightRecord(char* b, size_t cap) {
  char w[12], c[12];
  fmtLevel(lightNow(LIGHT_WARM), w, sizeof w);
  fmtLevel(lightNow(LIGHT_COOL), c, sizeof c);
  size_t n = 0;
  appendf(b, cap, &n, "t=%lu warm=%s cool=%s", (unsigned long)millis(), w, c);
  return n;
}
