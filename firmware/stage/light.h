// The LED strips (the main board's LED dimmer, ../../hardware/frame/main_pcb; api.md 9.1): warm and cool, each a linear
// current sink whose set-point is 12-bit PWM on pin 23 / 33 at 36.6 kHz, filtered to DC on the board. A level is % of
// the channel's full scale: code 4096, the sink's 1.98 V, 39.7 mA. Dark at power-up, and until set.
//
// The inline helpers are pure (host-tested in test/logic_test.cpp); light.cpp drives the pins.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "text.h"

enum LightChannel : uint8_t { LIGHT_WARM = 0, LIGHT_COOL = 1, LIGHT_N = 2 };
constexpr uint16_t LIGHT_STEPS = 4096;   // the code for 100 %: 12 bits, so 0.024 % a step

// A level (%) as the nearest code, and back.
inline uint16_t lightCode(float pct) {
  if (!(pct > 0)) return 0;   // and NaN
  if (pct >= 100) return LIGHT_STEPS;
  return (uint16_t)lroundf(pct * (LIGHT_STEPS / 100.0f));
}
inline float lightLevel(uint16_t code) { return code * (100.0f / LIGHT_STEPS); }

// A level as the protocol writes it: to 0.01 %, without trailing zeros ("39.99", "12.5", "0", "100").
inline void fmtLevel(float pct, char* out, size_t cap) {
  snprintf(out, cap, "%.2f", (double)pct);
  char* dot = strchr(out, '.');
  if (!dot) return;
  char* end = out + strlen(out) - 1;
  while (end > dot && *end == '0') *end-- = '\0';
  if (end == dot) *end = '\0';
}

// The light command's arguments: warm= and / or cool=, plain decimals, 0-100 (api.md 9.1). On false, err is the
// error's code ("arg" or "range") and why its text; out is then not to be used.
inline bool parseLight(char* const* tok, size_t ntok, float out[LIGHT_N], bool given[LIGHT_N], const char*& err,
                       const char*& why) {
  static const char* const names[LIGHT_N] = {"warm", "cool"};
  given[LIGHT_WARM] = given[LIGHT_COOL] = false;
  for (size_t i = 0; i < ntok; i++) {
    const char* eq = strchr(tok[i], '=');
    int c = -1;
    for (int k = 0; k < LIGHT_N; k++)
      if (eq && (size_t)(eq - tok[i]) == strlen(names[k]) && !strncmp(tok[i], names[k], strlen(names[k]))) c = k;
    err = "arg";
    if (c < 0) { why = "unknown key (warm= cool=)"; return false; }
    if (given[c]) { why = "a channel given twice"; return false; }
    if (!parseNumber(eq + 1, out[c])) { why = "not a number"; return false; }
    if (out[c] < 0 || out[c] > 100) { err = "range"; why = c == LIGHT_WARM ? "warm 0-100" : "cool 0-100"; return false; }
    given[c] = true;
  }
  if (!given[LIGHT_WARM] && !given[LIGHT_COOL]) { err = "arg"; why = "light [warm=<%>] [cool=<%>]: one or both"; return false; }
  return true;
}

// The hardware (light.cpp). analogWriteResolution(12) must already be set (stage.ino's setup()).
void lightBegin();                         // the PWM at 36.6 kHz, both channels dark
bool lightSet(uint8_t ch, float pct);      // a level (0-100, checked by the caller); true if the code changed
float lightNow(uint8_t ch);                // the level in force, %
size_t lightRecord(char* buf, size_t cap); // the record: "t=<ms> warm=<%> cool=<%>"
