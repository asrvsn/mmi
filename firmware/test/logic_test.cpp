// Host tests for the firmware's pure logic: text, yaml, params, motion, the lights' levels, the lid's NTCs and duty.
// Build and run with ./run.sh.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lid.h"
#include "light.h"
#include "motion.h"
#include "params.h"
#include "text.h"
#include "yaml.h"

static int fails = 0, checks = 0;
#define CHECK(c)                                                         \
  do {                                                                   \
    checks++;                                                            \
    if (!(c)) { fails++; printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } \
  } while (0)
#define NEAR(a, b, tol) CHECK(fabs((double)(a) - (double)(b)) <= (tol))

struct Ctx { Params* p; Report* r; };
static void sink(void* c, const char* k, const char* v, int line) {
  Ctx* x = (Ctx*)c;
  paramsApply(*x->p, k, v, line, *x->r);
}

static bool loadText(const char* text, Params& p, Report& r, char* err = nullptr) {
  static char buf[20000];
  strncpy(buf, text, sizeof buf - 1);
  buf[sizeof buf - 1] = '\0';
  paramsDefaults(p);
  r.n = r.lost = 0;
  Ctx c{&p, &r};
  char e[96] = "";
  bool ok = yamlFlatten(buf, sink, &c, e, sizeof e);
  if (err) strcpy(err, e);
  if (ok) paramsFinish(p, r);
  return ok;
}

static char* readFile(const char* path) {
  FILE* f = fopen(path, "rb");
  if (!f) return nullptr;
  static char buf[20000];
  size_t n = fread(buf, 1, sizeof buf - 1, f);
  fclose(f);
  buf[n] = '\0';
  return buf;
}

static void testText() {
  float v;
  CHECK(parseNumber("250", v) && v == 250);
  CHECK(parseNumber("-12.5", v) && v == -12.5f);
  CHECK(parseNumber("+0.8", v) && fabsf(v - 0.8f) < 1e-6f);
  CHECK(parseNumber(".5", v) && v == 0.5f);
  CHECK(!parseNumber("", v));
  CHECK(!parseNumber("-", v));
  CHECK(!parseNumber("1e3", v));
  CHECK(!parseNumber("12a", v));
  CHECK(!parseNumber("1.2.3", v));
  float l[8];
  uint8_t n;
  CHECK(parseList("[0.038, 0.92]", l, 8, n) && n == 2 && l[1] == 0.92f);
  CHECK(parseList("1,2,3", l, 8, n) && n == 3 && l[2] == 3);
  CHECK(!parseList("[]", l, 8, n));
  CHECK(!parseList("[1,,2]", l, 8, n));
  CHECK(!parseList("[1, x]", l, 8, n));
  CHECK(!parseList("[1,2,3,4,5,6,7,8,9]", l, 8, n));
  char line[] = "  12  jog   x=40 y=-12.5 ";
  char* tok[8];
  size_t k = tokenize(line, tok, 8);
  CHECK(k == 4 && !strcmp(tok[0], "12") && !strcmp(tok[1], "jog") && !strcmp(tok[3], "y=-12.5"));
}

static void testShippedYaml() {
  char* text = readFile("../sd/parameters.yaml");
  CHECK(text != nullptr);
  if (!text) return;
  Params a, d;
  Report r;
  char err[96];
  CHECK(loadText(text, a, r, err));
  for (uint8_t i = 0; i < r.n; i++) printf("  shipped yaml says: %s\n", r.line[i]);
  CHECK(r.n == 0);
  paramsDefaults(d);
  Report rd{};
  paramsFinish(d, rd);
  CHECK(memcmp(&a, &d, sizeof a) == 0);   // the file is exactly the built-in defaults
  NEAR(a.ax[1].cal[1].speed[1], 289, 1e-4);
  CHECK(a.r.effector == -1 && a.r.detent_phase == 128 && a.r.microsteps == 16 && a.jog.lease == 250);
  NEAR(railStepsPerUm(a), 3.2, 1e-6);
}

static void testYamlErrors() {
  Params p;
  Report r;
  char err[96];
  CHECK(!loadText("x:\n\tvmax: 1\n", p, r, err) && strstr(err, "tab"));
  CHECK(!loadText("x:\n  vmax: 1\n   acc: 2\n", p, r, err) && strstr(err, "indent"));
  CHECK(!loadText("x:\n  - 1\n", p, r, err) && strstr(err, "lists"));
  CHECK(!loadText("x:\ny: \n", p, r, err));
  CHECK(!loadText("  x: 1\n", p, r, err) && strstr(err, "column 1"));
  CHECK(loadText("x:\n  vmax: 999\n  acc: 500   # comment\nbogus: 3\nr:\n  effector: max\n", p, r, err));
  CHECK(p.ax[0].vmax == 250);           // out of range: default kept
  CHECK(p.ax[0].acc == 500);
  CHECK(p.r.effector == 1);
  CHECK(r.n == 2 && strstr(r.line[0], "x.vmax rejected") && strstr(r.line[1], "bogus unknown"));
  CHECK(loadText("x:\n  duty_max: 0.95\n  sign: 2\nr:\n  microsteps: 12\n  irun: 31\n", p, r, err));
  CHECK(p.ax[0].duty_max == 0.92f && p.ax[0].sign == 1 && p.r.microsteps == 16 && p.r.irun == 18 && r.n == 4);
  CHECK(loadText("fine:\n  stall_ms: 100\n", p, r, err) && p.fine.stall_ms == 50 && r.n == 1);   // MUST < 0.1 s
  CHECK(loadText("r:\n  detent_phase: 100\n", p, r, err) && p.r.detent_phase == 128 && r.n == 1);   // not a microstep
  CHECK(loadText("r:\n  detent_phase: 0\n", p, r, err) && p.r.detent_phase == 0 && r.n == 0);
}

static void testCalibration() {
  Params p;
  Report r;
  char err[96];
  // a plausible calibration: accepted
  CHECK(loadText("y:\n  cal:\n    source: measured 2026-10-19\n    fwd:\n      duty: [0.05, 0.3, 0.92]\n"
                 "      speed: [0, 70, 260]\n", p, r, err));
  CHECK(r.n == 0 && p.ax[1].cal_valid[0] && p.ax[1].cal[0].n == 3);
  NEAR(tableDuty(p.ax[1].cal[0], 70), 0.3, 1e-5);
  // 4x the model at 0.3: rejected, the model runs instead
  CHECK(loadText("z:\n  cal:\n    rev:\n      duty: [0.05, 0.3]\n      speed: [0, 400]\n", p, r, err));
  CHECK(!p.ax[2].cal_valid[1] && r.n == 1 && strstr(r.line[0], "z.cal.rev rejected"));
  CHECK(p.ax[2].cal[1].n == 2 && p.ax[2].cal[1].speed[1] == 289);
  // mismatched lengths and decreasing duties
  CHECK(loadText("x:\n  cal:\n    fwd:\n      duty: [0.1, 0.5, 0.9]\n      speed: [0, 200]\n", p, r, err));
  CHECK(!p.ax[0].cal_valid[0] && strstr(r.line[0], "differ in length"));
  CHECK(loadText("x:\n  model:\n    duty: [0.5, 0.1]\n    speed: [0, 200]\n", p, r, err));
  CHECK(strstr(r.line[0], "x.model rejected") && p.ax[0].model.duty[1] == 0.92f);
}

static void testTables() {
  Params p;
  paramsDefaults(p);
  const Table& m = p.ax[0].model;
  NEAR(tableDuty(m, 0), 0.038, 1e-6);
  NEAR(tableDuty(m, 289), 0.92, 1e-6);
  NEAR(tableDuty(m, 144.5), (0.038 + 0.92) / 2, 1e-5);
  NEAR(tableDuty(m, 1000), 0.92, 1e-6);   // clamped to the table's end
  NEAR(tableSpeed(m, 0.02), 0, 1e-6);
  NEAR(tableSpeed(m, 0.479), 144.5, 1e-3);
}

static void testRamp() {
  // speeding up at acc, slowing at dec, a reversal holding zero for one tick
  float v = 0;
  for (int i = 0; i < 10; i++) v = rampStep(v, 100, 2000, 4000, 0.001f);
  NEAR(v, 20, 1e-3);
  for (int i = 0; i < 100; i++) v = rampStep(v, 100, 2000, 4000, 0.001f);
  NEAR(v, 100, 1e-4);
  v = rampStep(v, 0, 2000, 4000, 0.001f);
  NEAR(v, 96, 1e-4);
  v = 2;
  v = rampStep(v, -50, 2000, 4000, 0.001f);
  CHECK(v == 0);   // 2 - 4 would cross zero: it stops at zero this tick
  v = rampStep(v, -50, 2000, 4000, 0.001f);
  NEAR(v, -2, 1e-4);   // then speeds up the other way at acc
  CHECK(unblocked(5, BLK_POS) == 0 && unblocked(-5, BLK_POS) == -5 && unblocked(-5, BLK_NEG) == 0);
}

static void testPark() {
  // grid 16 microsteps, origin 0: stop distance at 3200 steps/s and 64000 steps/s^2 is 80 steps
  CHECK(parkTarget(1000, 3200, 64000, 16, 0) == 1088);   // 1080 -> next detent ahead 1088
  CHECK(parkTarget(1000, -3200, 64000, 16, 0) == 912);   // 920 -> 912 (ahead going -)
  CHECK(parkTarget(1008, 0.001f, 64000, 16, 0) == 1008);  // already on a detent, barely moving
  CHECK(parkTarget(1001, 0.001f, 64000, 16, 0) == 1008);  // never behind
  CHECK(parkTarget(1001, -0.001f, 64000, 16, 0) == 992);
  CHECK(parkTarget(-5, 0.001f, 64000, 16, 0) == 0);
  CHECK(parkTarget(-5, -0.001f, 64000, 16, 0) == -16);
  CHECK(parkTarget(1000, 3200, 64000, 16, 8) == 1080);    // detents offset by half a step
  CHECK(parkTarget(1000, 3200, 64000, 16, 3 + 16 * 50) == 1091);   // any origin: detents at 3 mod 16
  NEAR(approachSpeed(80, 64000), 3200, 1e-2);
  CHECK(approachSpeed(0, 64000) == 0);
}

static void testJog() {
  float t[AX_N];
  const char* why = "";
  char a1[] = "x=40", a2[] = "y=-12.5", a3[] = "r=1500";
  char* ok[] = {a1, a2, a3};
  CHECK(parseJog(ok, 3, t, why) && t[AX_X] == 40 && t[AX_Y] == -12.5f && t[AX_Z] == 0 && t[AX_R] == 1500);
  CHECK(parseJog(nullptr, 0, t, why) && t[0] == 0 && t[3] == 0);
  char b1[] = "X=10";
  char* bad1[] = {b1};
  CHECK(!parseJog(bad1, 1, t, why));
  char c1[] = "x=1", c2[] = "x=2";
  char* bad2[] = {c1, c2};
  CHECK(!parseJog(bad2, 2, t, why) && strstr(why, "twice"));
  char d1[] = "z=fast";
  char* bad3[] = {d1};
  CHECK(!parseJog(bad3, 1, t, why));
  char e1[] = "x";
  char* bad4[] = {e1};
  CHECK(!parseJog(bad4, 1, t, why));
}

// get's output must read back into the very same parameter set (it's what a client sees, and may send back with set).
static void collect(void* ctx, const char* k, const char* v) {
  Ctx* c = (Ctx*)ctx;
  paramsApply(*c->p, k, v, 0, *c->r);
}

static void testGetRoundTrip() {
  Params a, b;
  Report ra{}, rb{};
  char err[96];
  CHECK(loadText("unit: lab one\nx:\n  vmax: 123.4\n  sign: -1\ny:\n  cal:\n    source: measured 2026-10-19\n"
                 "    fwd:\n      duty: [0.05, 0.3, 0.92]\n      speed: [0, 70, 260]\nr:\n  effector: max\n"
                 "  detent_phase: 0\n  acc: 12345\njog:\n  lease: 300\nlid:\n  duty_max: 37.5\n  medium_max: 35\n  sign: -1\n",
                 a, ra, err));
  CHECK(ra.n == 0);
  paramsDefaults(b);
  Ctx c{&b, &rb};
  paramsList(a, collect, &c);
  paramsFinish(b, rb);
  CHECK(rb.n == 0);
  CHECK(memcmp(&a, &b, sizeof a) == 0);
  char v[160];
  CHECK(paramsGet(a, "x.vmax", v, sizeof v) && !strcmp(v, "123.4"));
  CHECK(paramsGet(a, "y.cal.fwd.speed", v, sizeof v) && !strcmp(v, "0,70,260"));
  CHECK(paramsGet(a, "r.effector", v, sizeof v) && !strcmp(v, "max"));
  CHECK(paramsGet(a, "unit", v, sizeof v) && !strcmp(v, "lab one"));
  CHECK(paramsGet(a, "jog.lease", v, sizeof v) && !strcmp(v, "300"));
  CHECK(paramsGet(a, "lid.duty_max", v, sizeof v) && !strcmp(v, "37.5"));
  CHECK(paramsGet(a, "lid.medium_max", v, sizeof v) && !strcmp(v, "35"));
  CHECK(paramsGet(a, "lid.sign", v, sizeof v) && !strcmp(v, "-1"));
  CHECK(!paramsGet(a, "x.nope", v, sizeof v));
  int n = 0;
  paramsList(a, [](void* ctx, const char*, const char*) { (*(int*)ctx)++; }, &n);
  CHECK(n == 2 + 8 + 3 * 14 + 11 + 1 + 3);   // format, unit; fine; x, y, z (14 each); r; jog; lid
  Params d;
  Report rd{};
  CHECK(loadText("lid:\n  duty_max: 81\n  medium_max: 51\n  sign: 2\n", d, rd) && rd.n == 3);   // past 80 %, 50 C
  CHECK(d.lid.duty_max == 50 && d.lid.medium_max == 40 && d.lid.sign == 1);                     // the defaults kept
  Params e;
  Report re{};
  CHECK(loadText("lid:\n  medium_max: 9.5\n", e, re) && re.n == 1 && e.lid.medium_max == 40);
}

// The lid (api.md 9.2): the NTCs, ratiometric to the 3.3 V, by each one's beta; the duty to the PWM's steps under its
// cap; the command's arguments.
static void testLid() {
  float t = -99;
  CHECK(ntcCelsius(13000, 26000, 3380, t) == Ntc::OK);
  NEAR(t, 25.0, 1e-3);                                                // the divider's midpoint: 10k, 25 C
  // 60 C on a board NTC: R = 10k exp(3380 (1/333.15 - 1/298.15)) = 3.0398k, so the divider sits at 0.23311.
  double r60 = exp(3380 * (1 / 333.15 - 1 / 298.15)), x60 = r60 / (r60 + 1);
  CHECK(ntcCelsius((int32_t)lround(x60 * 26400), 26400, 3380, t) == Ntc::OK);
  NEAR(t, 60.0, 0.01);
  double r37 = exp(3977 * (1 / 310.15 - 1 / 298.15)), x37 = r37 / (r37 + 1);   // a probe at 37 C
  CHECK(ntcCelsius((int32_t)lround(x37 * 26400), 26400, 3977, t) == Ntc::OK);
  NEAR(t, 37.0, 0.01);
  CHECK(ntcCelsius(26400, 26400, 3380, t) == Ntc::OPEN);              // no probe: the 10k pulls it to the 3.3 V
  CHECK(ntcCelsius(25700, 26400, 3380, t) == Ntc::OPEN);              // 97 %: below -36 C, a broken lead
  CHECK(ntcCelsius(0, 26400, 3380, t) == Ntc::SHORT && ntcCelsius(700, 26400, 3380, t) == Ntc::SHORT);
  CHECK(ntcCelsius(1000, 0, 3380, t) == Ntc::SHORT);                  // no reference reading
  CHECK(lidCode(30, 50) == 1229 && lidCode(-30, 50) == 1229 && lidCode(50, 50) == 2048 && lidCode(25, 50) == 1024);
  CHECK(lidCode(1.99f, 50) == 0 && lidCode(2, 50) == 82 && lidCode(NAN, 50) == 0);   // under the 2 % floor: 0
  CHECK(lidCode(33, 33) == 1351 && lidLevel(1351) <= 33);             // the nearest step, but never past the cap
  CHECK(lidCode(100, 100) == 3276 && lidCode(80, 80) == 3276);        // nor past the 80 % ceiling, whatever the cap
  float d = 0;
  const char *err = "", *why = "";
  char a1[] = "duty=30", a2[] = "duty=-12.5", a3[] = "off", a4[] = "duty=+50";
  char* ok1[] = {a1};
  char* ok2[] = {a2};
  char* ok3[] = {a3};
  char* ok4[] = {a4};
  CHECK(parseLid(ok1, 1, 50, d, err, why) && d == 30);
  CHECK(parseLid(ok2, 1, 50, d, err, why) && d == -12.5f);
  CHECK(parseLid(ok3, 1, 50, d, err, why) && d == 0);
  CHECK(parseLid(ok4, 1, 50, d, err, why) && d == 50);
  char b1[] = "duty=50.01", b2[] = "duty=-51";
  char* high[] = {b1};
  char* low[] = {b2};
  CHECK(!parseLid(high, 1, 50, d, err, why) && !strcmp(err, "range"));
  CHECK(!parseLid(low, 1, 50, d, err, why) && !strcmp(err, "range"));
  CHECK(!parseLid(nullptr, 0, 50, d, err, why) && !strcmp(err, "arg"));
  char c1[] = "Duty=1", c2[] = "duty", c3[] = "duty=", c4[] = "duty=warm", c5[] = "duty=1e1", c6[] = "temp=25";
  char* bad[] = {c1, c2, c3, c4, c5, c6};
  for (char* x : bad) CHECK(!parseLid(&x, 1, 50, d, err, why) && !strcmp(err, "arg"));
  char e1[] = "duty=1", e2[] = "duty=2";
  char* two[] = {e1, e2};
  CHECK(!parseLid(two, 2, 50, d, err, why) && !strcmp(err, "arg"));
  char b[16];
  fmtLevel(-lidLevel(1229), b, sizeof b);
  CHECK(!strcmp(b, "-30"));                                           // said as set, to 0.01 %
  // A readout (LidStat): the window's mean, and the SD about its straight line.
  LidStat still, ramp, alt;
  for (int k = 0; k < 16; k++) {
    still.add(k, 25.0f);
    ramp.add(k, 25 + 0.01875f * k);                                   // 0.3 K/s, 62.5 ms a slot
    alt.add(k, 25 + 0.01875f * k + (k % 2 ? 0.004f : -0.004f));      // and +-4 mK on top
  }
  CHECK(still.n == 16 && fabsf(still.mean() - 25) < 1e-6f && still.sd() == 0);
  NEAR(ramp.mean(), 25 + 0.01875 * 7.5, 1e-5);
  CHECK(ramp.sd() < 1e-4f);                                           // the rise isn't noise
  NEAR(alt.sd(), 0.004 * sqrt((16 - 64.0 / 340) / 14), 2e-5);         // the alternation, less the bit the line takes
  LidStat pair;
  pair.add(0, 25);
  pair.add(1, 26);
  CHECK(pair.sd() == 0 && LidStat().sd() == 0 && isnan(LidStat().mean()));
  // 16 samples 62.5 ms apart average 50 and 60 Hz out, whatever the phase; 10 a second (100 ms) alias 50 Hz to DC.
  const double mains[] = {50.0, 60.0}, phases[] = {0.0, 1.0, 2.5};
  for (double f : mains)
    for (double ph : phases) {
      LidStat m;
      for (int k = 0; k < 16; k++) m.add(k, 25 + 0.1f * (float)sin(2 * M_PI * f * k * 0.0625 + ph));
      CHECK(fabsf(m.mean() - 25) < 2e-5f && m.sd() > 0.06f);
    }
  LidStat ten;
  for (int k = 0; k < 10; k++) ten.add(k, 25 + 0.1f * (float)sin(2 * M_PI * 50 * k * 0.1 + M_PI / 2));
  CHECK(fabsf(ten.mean() - 25) > 0.09f);
}

// The light command (api.md 9.1): levels to the 12-bit codes and back, as written, and its arguments.
static void testLight() {
  CHECK(lightCode(0) == 0 && lightCode(100) == 4096 && lightCode(50) == 2048 && lightCode(12.5f) == 512);
  CHECK(lightCode(40) == 1638 && lightCode(-1) == 0 && lightCode(101) == 4096 && lightCode(NAN) == 0);
  CHECK(lightCode(0.012f) == 0 && lightCode(0.013f) == 1);          // half a step, 0.0122 %
  char b[16];
  fmtLevel(lightLevel(1638), b, sizeof b);
  CHECK(!strcmp(b, "39.99"));                                        // 40 isn't a step: the nearest is said
  fmtLevel(lightLevel(512), b, sizeof b);
  CHECK(!strcmp(b, "12.5"));
  fmtLevel(0, b, sizeof b);
  CHECK(!strcmp(b, "0"));
  fmtLevel(100, b, sizeof b);
  CHECK(!strcmp(b, "100"));
  fmtLevel(lightLevel(1), b, sizeof b);
  CHECK(!strcmp(b, "0.02"));
  for (uint16_t k = 0; k <= LIGHT_STEPS; k++)                       // what the unit says reads back as what it set
    if (lightCode(lightLevel(k)) != k) { CHECK(false); break; }
  float v[LIGHT_N];
  bool given[LIGHT_N];
  const char *err = "", *why = "";
  char a1[] = "warm=40", a2[] = "cool=12.5";
  char* ok[] = {a1, a2};
  CHECK(parseLight(ok, 2, v, given, err, why) && given[0] && given[1] && v[0] == 40 && v[1] == 12.5f);
  char c1[] = "cool=0";
  char* one[] = {c1};
  CHECK(parseLight(one, 1, v, given, err, why) && !given[LIGHT_WARM] && given[LIGHT_COOL] && v[LIGHT_COOL] == 0);
  CHECK(!parseLight(nullptr, 0, v, given, err, why) && !strcmp(err, "arg"));   // neither channel
  char d1[] = "warm=101";
  char* high[] = {d1};
  CHECK(!parseLight(high, 1, v, given, err, why) && !strcmp(err, "range") && strstr(why, "warm"));
  char e1[] = "cool=-0.5";
  char* low[] = {e1};
  CHECK(!parseLight(low, 1, v, given, err, why) && !strcmp(err, "range") && strstr(why, "cool"));
  char f1[] = "warm=1", f2[] = "warm=2";
  char* twice[] = {f1, f2};
  CHECK(!parseLight(twice, 2, v, given, err, why) && !strcmp(err, "arg") && strstr(why, "twice"));
  char g1[] = "Warm=1", g2[] = "warmth=1", g3[] = "warm", g4[] = "warm=dim", g5[] = "warm=1e2";
  char* bad[] = {g1, g2, g3, g4, g5};
  for (char* t : bad) CHECK(!parseLight(&t, 1, v, given, err, why) && !strcmp(err, "arg"));
}

int main() {
  testText();
  testGetRoundTrip();
  testShippedYaml();
  testYamlErrors();
  testCalibration();
  testTables();
  testRamp();
  testPark();
  testJog();
  testLight();
  testLid();
  printf("logic: %d checks, %d failed\n", checks, fails);
  return fails ? 1 : 0;
}
