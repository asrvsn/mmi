#include "motion.h"

#include <math.h>
#include <string.h>

#include "text.h"

float rampStep(float v, float t, float acc, float dec, float dt) {
  if (v == t) return v;
  bool growing = (v == 0) || (v > 0 && t > v) || (v < 0 && t < v);
  float step = (growing ? acc : dec) * dt;
  float n = (t > v) ? fminf(v + step, t) : fmaxf(v - step, t);
  if ((v > 0 && n < 0) || (v < 0 && n > 0)) n = 0;   // through zero: one tick at rest
  return n;
}

int32_t parkTarget(int32_t pos, float v, float dec, int32_t grid, int32_t offset) {
  double stop = (double)pos + (v > 0 ? 1.0 : -1.0) * ((double)v * v / (2.0 * dec));
  double k = (stop - offset) / grid;
  double kk = (v > 0) ? ceil(k - 1e-9) : floor(k + 1e-9);
  return (int32_t)(kk * grid) + offset;
}

float approachSpeed(float remaining, float dec) {
  return remaining > 0 ? sqrtf(2.0f * dec * remaining) : 0.0f;
}

bool parseJog(char* const* tok, size_t ntok, float out[AX_N], const char*& why) {
  static const char names[AX_N] = {'x', 'y', 'z', 'r'};
  bool seen[AX_N] = {false, false, false, false};
  for (int a = 0; a < AX_N; a++) out[a] = 0;
  for (size_t i = 0; i < ntok; i++) {
    const char* t = tok[i];
    int a = -1;
    for (int k = 0; k < AX_N; k++)
      if (t[0] == names[k] && t[1] == '=') a = k;
    if (a < 0) { why = "unknown key (x= y= z= r=)"; return false; }
    if (seen[a]) { why = "an axis given twice"; return false; }
    if (!parseNumber(t + 2, out[a])) { why = "not a number"; return false; }
    seen[a] = true;
  }
  return true;
}
