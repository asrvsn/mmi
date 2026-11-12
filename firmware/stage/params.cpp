#include "params.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "text.h"

namespace {
// The precomputed model (firmware/parameters.md): a lumped INJS11 model, 289 um/s at the 92 % cap, unloaded,
// friction taking ~3.8 % duty. The same for every fine axis and both directions until calibration.
void setModel(Table& t) {
  t.n = t.n_speed = 2;
  t.duty[0] = 0.038f; t.speed[0] = 0.0f;
  t.duty[1] = 0.92f;  t.speed[1] = 289.0f;
}

int num(const char* v, float lo, float hi, float& out, const char*& why) {
  float x;
  if (!parseNumber(v, x)) { why = "not a number"; return -1; }
  if (x < lo || x > hi) { why = "out of range"; return -1; }
  out = x;
  return 1;
}

template <typename T>
int whole(const char* v, long lo, long hi, T& out, const char*& why) {
  float x;
  if (!parseNumber(v, x) || x != (float)(long)x) { why = "not a whole number"; return -1; }
  if ((long)x < lo || (long)x > hi) { why = "out of range"; return -1; }
  out = (T)(long)x;
  return 1;
}

int sign(const char* v, int8_t& out, const char*& why) {
  if (!strcmp(v, "1") || !strcmp(v, "+1")) { out = 1; return 1; }
  if (!strcmp(v, "-1")) { out = -1; return 1; }
  why = "must be 1 or -1";
  return -1;
}

int text(const char* v, char* out, size_t cap, const char*& why) {
  if (strlen(v) >= cap) { why = "too long"; return -1; }
  strcpy(out, v);
  return 1;
}

int list(const char* v, float* out, uint8_t& n, const char*& why) {
  float tmp[TABLE_MAX];
  uint8_t k;
  if (!parseList(v, tmp, TABLE_MAX, k)) { why = "not a list of up to 8 numbers"; return -1; }
  memcpy(out, tmp, sizeof(float) * k);
  n = k;
  return 1;
}

int axisKey(FineAxis& a, const char* k, const char* v, const char*& why) {
  if (!strcmp(k, "sign")) return sign(v, a.sign, why);
  if (!strcmp(k, "vmax")) return num(v, 1, hw::FINE_V_CEIL, a.vmax, why);
  if (!strcmp(k, "acc")) return num(v, 10, 50000, a.acc, why);
  if (!strcmp(k, "dec")) return num(v, 10, 50000, a.dec, why);
  if (!strcmp(k, "duty_max")) return num(v, 0.05f, hw::DUTY_CEIL, a.duty_max, why);
  if (!strcmp(k, "duty_min")) return num(v, 0, 0.5f, a.duty_min, why);
  if (!strcmp(k, "r_m")) return num(v, 1, 10, a.r_m, why);
  if (!strcmp(k, "model.duty")) return list(v, a.model.duty, a.model.n, why);
  if (!strcmp(k, "model.speed")) return list(v, a.model.speed, a.model.n_speed, why);
  if (!strcmp(k, "cal.source")) return text(v, a.cal_source, sizeof a.cal_source, why);
  if (!strcmp(k, "cal.fwd.duty")) return list(v, a.cal[0].duty, a.cal[0].n, why);
  if (!strcmp(k, "cal.fwd.speed")) return list(v, a.cal[0].speed, a.cal[0].n_speed, why);
  if (!strcmp(k, "cal.rev.duty")) return list(v, a.cal[1].duty, a.cal[1].n, why);
  if (!strcmp(k, "cal.rev.speed")) return list(v, a.cal[1].speed, a.cal[1].n_speed, why);
  return 0;
}

int key(Params& p, const char* k, const char* v, const char*& why) {
  if ((k[0] == 'x' || k[0] == 'y' || k[0] == 'z') && k[1] == '.') return axisKey(p.ax[k[0] - 'x'], k + 2, v, why);
  if (!strcmp(k, "format")) {
    uint8_t f;
    return whole(v, 1, 1, f, why);
  }
  if (!strcmp(k, "unit")) return text(v, p.unit, sizeof p.unit, why);
  if (!strcmp(k, "fine.pwm_hz")) return num(v, 20000, 80000, p.fine.pwm_hz, why);
  if (!strcmp(k, "fine.stall_i")) return num(v, 0.2f, 0.70f, p.fine.stall_i, why);   // under a held stall's 0.76 A
  if (!strcmp(k, "fine.stall_k")) return num(v, 0.3f, 0.75f, p.fine.stall_k, why);   // under the loop's real locked current
  if (!strcmp(k, "fine.stall_floor")) return num(v, 0.1f, 0.5f, p.fine.stall_floor, why);
  if (!strcmp(k, "fine.stall_ms")) return whole(v, 10, hw::STALL_MS_CEIL, p.fine.stall_ms, why);
  if (!strcmp(k, "fine.inrush_ms")) return whole(v, 0, 200, p.fine.inrush_ms, why);
  if (!strcmp(k, "fine.vunk")) return num(v, 1, 100, p.fine.vunk, why);
  if (!strcmp(k, "fine.cal_ratio")) return num(v, 1.1f, 5, p.fine.cal_ratio, why);
  if (!strcmp(k, "r.sign")) return sign(v, p.r.sign, why);
  if (!strcmp(k, "r.vmax")) return num(v, 10, hw::RAIL_V_CEIL, p.r.vmax, why);
  if (!strcmp(k, "r.acc")) return num(v, 100, 200000, p.r.acc, why);
  if (!strcmp(k, "r.dec")) return num(v, 100, 200000, p.r.dec, why);
  if (!strcmp(k, "r.lead")) return num(v, 100, 10000, p.r.lead, why);
  if (!strcmp(k, "r.steps")) return whole(v, 200, 400, p.r.steps, why);
  if (!strcmp(k, "r.microsteps")) {
    uint16_t m;
    int ok = whole(v, 1, 256, m, why);
    if (ok > 0 && (m & (m - 1))) { why = "must be a power of 2"; return -1; }
    if (ok > 0) p.r.microsteps = m;
    return ok;
  }
  if (!strcmp(k, "r.irun")) return whole(v, 0, hw::RAIL_IRUN_CEIL, p.r.irun, why);
  if (!strcmp(k, "r.detent_phase")) return whole(v, 0, 255, p.r.detent_phase, why);
  if (!strcmp(k, "r.effector")) {
    if (!strcmp(v, "max")) { p.r.effector = 1; return 1; }
    if (!strcmp(v, "min")) { p.r.effector = -1; return 1; }
    why = "must be max or min";
    return -1;
  }
  if (!strcmp(k, "r.release")) return num(v, 100, 10000, p.r.release, why);
  if (!strcmp(k, "jog.lease")) return whole(v, 100, 500, p.jog.lease, why);
  if (!strcmp(k, "lid.duty_max")) return num(v, 0, hw::LID_DUTY_CEIL, p.lid.duty_max, why);
  if (!strcmp(k, "lid.medium_max")) return num(v, 10, hw::LID_MEDIUM_CEIL, p.lid.medium_max, why);
  if (!strcmp(k, "lid.sign")) return sign(v, p.lid.sign, why);
  return 0;
}

bool tableOk(const Table& t, const char*& why) {
  if (t.n != t.n_speed) { why = "duty and speed differ in length"; return false; }
  if (t.n < 2) { why = "needs at least 2 points"; return false; }
  for (uint8_t i = 0; i < t.n; i++) {
    if (t.duty[i] < 0 || t.duty[i] > hw::DUTY_CEIL) { why = "a duty outside 0-0.92"; return false; }
    if (t.speed[i] < 0 || t.speed[i] > hw::FINE_V_CEIL) { why = "a speed outside 0-400"; return false; }
    if (i && t.duty[i] <= t.duty[i - 1]) { why = "duties not increasing"; return false; }
    if (i && t.speed[i] < t.speed[i - 1]) { why = "speeds decreasing"; return false; }
  }
  if (t.speed[t.n - 1] <= 0) { why = "no speed above 0"; return false; }
  return true;
}

// Each calibrated point must lie within x/÷ ratio of the model at the same duty, wherever the model moves at all.
bool nearModel(const Table& cal, const Table& model, float ratio, const char*& why) {
  for (uint8_t i = 0; i < cal.n; i++) {
    float m = tableSpeed(model, cal.duty[i]);
    if (m < 10) continue;
    if (cal.speed[i] > m * ratio || cal.speed[i] < m / ratio) { why = "too far from the model"; return false; }
  }
  return true;
}
}  // namespace

namespace {
void fmtNum(char* out, size_t cap, float v) {   // a plain decimal: no exponent, no float noise
  float a = fabsf(v);
  if (v == (float)(long)v && a < 1e9f) {
    snprintf(out, cap, "%ld", (long)v);
  } else if (a >= 1e-4f && a < 1e7f) {
    snprintf(out, cap, "%.7g", (double)v);   // 7 significant digits: plain in this range, and a float reads back
  } else {
    snprintf(out, cap, "%.6f", (double)v);
    size_t n = strlen(out);
    while (n > 0 && out[n - 1] == '0') out[--n] = '\0';
    if (n > 0 && out[n - 1] == '.') out[--n] = '\0';
  }
}

void fmtList(char* out, size_t cap, const float* a, uint8_t n) {
  size_t len = 0;
  out[0] = '\0';
  for (uint8_t i = 0; i < n; i++) {
    char one[24];
    fmtNum(one, sizeof one, a[i]);
    len += snprintf(out + len, len < cap ? cap - len : 0, "%s%s", i ? "," : "", one);
  }
}
}  // namespace

void paramsList(const Params& p, ParamSink sink, void* ctx) {
  char k[32], v[160];
  auto num = [&](const char* key, float x) { fmtNum(v, sizeof v, x); sink(ctx, key, v); };
  auto axisNum = [&](char c, const char* sub, float x) { snprintf(k, sizeof k, "%c.%s", c, sub); num(k, x); };
  auto axisList = [&](char c, const char* sub, const Table& t, bool speed) {
    snprintf(k, sizeof k, "%c.%s", c, sub);
    fmtList(v, sizeof v, speed ? t.speed : t.duty, t.n);
    sink(ctx, k, v);
  };
  sink(ctx, "format", "1");
  sink(ctx, "unit", p.unit);
  num("fine.pwm_hz", p.fine.pwm_hz);
  num("fine.stall_i", p.fine.stall_i);
  num("fine.stall_k", p.fine.stall_k);
  num("fine.stall_floor", p.fine.stall_floor);
  num("fine.stall_ms", p.fine.stall_ms);
  num("fine.inrush_ms", p.fine.inrush_ms);
  num("fine.vunk", p.fine.vunk);
  num("fine.cal_ratio", p.fine.cal_ratio);
  for (int i = 0; i < 3; i++) {
    const FineAxis& a = p.ax[i];
    char c = (char)('x' + i);
    axisNum(c, "sign", a.sign);
    axisNum(c, "vmax", a.vmax);
    axisNum(c, "acc", a.acc);
    axisNum(c, "dec", a.dec);
    axisNum(c, "duty_max", a.duty_max);
    axisNum(c, "duty_min", a.duty_min);
    axisNum(c, "r_m", a.r_m);
    axisList(c, "model.duty", a.model, false);
    axisList(c, "model.speed", a.model, true);
    snprintf(k, sizeof k, "%c.cal.source", c);
    sink(ctx, k, a.cal_source);
    axisList(c, "cal.fwd.duty", a.cal[0], false);
    axisList(c, "cal.fwd.speed", a.cal[0], true);
    axisList(c, "cal.rev.duty", a.cal[1], false);
    axisList(c, "cal.rev.speed", a.cal[1], true);
  }
  num("r.sign", p.r.sign);
  num("r.vmax", p.r.vmax);
  num("r.acc", p.r.acc);
  num("r.dec", p.r.dec);
  num("r.lead", p.r.lead);
  num("r.steps", p.r.steps);
  num("r.microsteps", p.r.microsteps);
  num("r.irun", p.r.irun);
  num("r.detent_phase", p.r.detent_phase);
  sink(ctx, "r.effector", p.r.effector < 0 ? "min" : "max");
  num("r.release", p.r.release);
  num("jog.lease", p.jog.lease);
  num("lid.duty_max", p.lid.duty_max);
  num("lid.medium_max", p.lid.medium_max);
  num("lid.sign", p.lid.sign);
}

bool paramsGet(const Params& p, const char* key, char* value, size_t cap) {
  struct Find { const char* key; char* value; size_t cap; bool found; } f{key, value, cap, false};
  paramsList(p, [](void* ctx, const char* k, const char* v) {
    Find* f = (Find*)ctx;
    if (!f->found && !strcmp(k, f->key)) {
      snprintf(f->value, f->cap, "%s", v);
      f->found = true;
    }
  }, &f);
  return f.found;
}

void Report::add(const char* fmt, ...) {
  if (n >= MAX) { lost++; return; }
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(line[n], sizeof line[n], fmt, ap);
  va_end(ap);
  n++;
}

void paramsDefaults(Params& p) {
  memset(&p, 0, sizeof p);
  strcpy(p.unit, "unset");
  p.fine.pwm_hz = 40000;
  p.fine.stall_i = 0.70f;
  p.fine.stall_k = 0.6f;
  p.fine.stall_floor = 0.30f;
  p.fine.stall_ms = 50;
  p.fine.inrush_ms = 30;
  p.fine.vunk = 20;
  p.fine.cal_ratio = 2.0f;
  for (FineAxis& a : p.ax) {
    a.sign = 1;
    a.vmax = 250;
    a.acc = 2000;
    a.dec = 4000;
    a.duty_max = 0.92f;
    a.duty_min = 0;
    a.r_m = 3.5f;
    setModel(a.model);
    a.cal[0] = a.cal[1] = a.model;
    strcpy(a.cal_source, "model");
    a.cal_valid[0] = a.cal_valid[1] = true;
  }
  p.r.sign = 1;
  p.r.vmax = 5000;
  p.r.acc = 20000;
  p.r.dec = 20000;
  p.r.lead = 1000;
  p.r.steps = 200;
  p.r.microsteps = 16;
  p.r.irun = 18;
  p.r.detent_phase = 128;
  p.r.effector = -1;
  p.r.release = 2000;
  p.jog.lease = 250;
  p.lid.duty_max = 50;   // the board's steady state <= ~61 C, uncovered, in the lid model's worst case (parameters.md)
  p.lid.medium_max = 40;
  p.lid.sign = 1;
}

void paramsApply(Params& p, const char* k, const char* v, int line, Report& rep) {
  const char* why = "";
  int r = key(p, k, v, why);
  if (r == 0) rep.add("%s unknown (line %d), ignored", k, line);
  else if (r < 0) rep.add("%s rejected (line %d): '%s' %s", k, line, v, why);
}

void paramsFinish(Params& p, Report& rep) {
  for (int i = 0; i < 3; i++) {
    FineAxis& a = p.ax[i];
    const char* why = "";
    char c = (char)('x' + i);
    if (!tableOk(a.model, why)) {
      rep.add("%c.model rejected: %s; using the built-in model", c, why);
      setModel(a.model);
    }
    for (int d = 0; d < 2; d++) {
      a.cal_valid[d] = tableOk(a.cal[d], why) && nearModel(a.cal[d], a.model, p.fine.cal_ratio, why);
      if (!a.cal_valid[d]) {
        rep.add("%c.cal.%s rejected: %s; running on the model", c, d ? "rev" : "fwd", why);
        a.cal[d] = a.model;
      }
    }
    if (a.duty_min >= a.duty_max) {
      rep.add("%c.duty_min must be below duty_max; set to 0", c);
      a.duty_min = 0;
    }
  }
  if (p.r.detent_phase % (256 / p.r.microsteps)) {
    rep.add("r.detent_phase must be a whole number of microsteps (a multiple of %u); set to 128",
            (unsigned)(256 / p.r.microsteps));
    p.r.detent_phase = 128;
  }
}

float tableSpeed(const Table& t, float duty) {
  if (duty <= t.duty[0]) return t.speed[0];
  for (uint8_t i = 1; i < t.n; i++) {
    if (duty <= t.duty[i]) {
      float f = (duty - t.duty[i - 1]) / (t.duty[i] - t.duty[i - 1]);
      return t.speed[i - 1] + f * (t.speed[i] - t.speed[i - 1]);
    }
  }
  return t.speed[t.n - 1];
}

float tableDuty(const Table& t, float speed) {
  if (speed <= t.speed[0]) return t.duty[0];
  for (uint8_t i = 1; i < t.n; i++) {
    if (speed <= t.speed[i] && t.speed[i] > t.speed[i - 1]) {
      float f = (speed - t.speed[i - 1]) / (t.speed[i] - t.speed[i - 1]);
      return t.duty[i - 1] + f * (t.duty[i] - t.duty[i - 1]);
    }
  }
  return t.duty[t.n - 1];
}
