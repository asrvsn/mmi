#include "teensy_sim.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <algorithm>
#include <deque>
#include <random>

#include "Arduino.h"
#include "IntervalTimer.h"
#include "Wire.h"
#include "board.h"
#include "drive.h"
#include "lid.h"
#include "light.h"
#include "net.h"
#include "params.h"
#include "rail.h"

namespace sim {
double fine[3] = {0, 0, 0};
bool jam[3] = {false, false, false};
int physSign[4] = {1, 1, 1, 1};
double railUm = 50000;
double railMinUm = 0;
double railMaxUm = 150000;
int rotorPhase = 128, snaps = 0, relaxes = 0, enLows = 0;
bool headPlugged = true;
bool motOn = false;
double diffUm = 20;
bool bounce = true;
Tmc chip;
int violReversal = 0, violMotorsOff = 0, railStepsMoved = 0;
bool lidPlugged = true, lidSw = true;
double lidBoard = 24.0, lidMedium = 23.0, lidRoom = 24.0;
double lidOffset[5] = {0.15, 0.10, -0.25, 0.05, -0.05};   // a, b, edge (the cold end), j2, j3
char lidBroken[5] = {0, 0, 0, 0, 0};
double lidV33 = 3.3;
int lidHeatSign = 1, lidConversions = 0;
double lidNoiseV = 0, lidMainsV = 0, lidMainsHz = 50;
int violLidBoth = 0, violLidSw = 0, violLidAbsent = 0, violLidWake = 0, violLidCfg = 0;
std::vector<std::string> events;
}  // namespace sim

using namespace sim;

namespace {
enum Mode : uint8_t { M_IN, M_OUT, M_PWM };
struct Pin {
  Mode mode = M_IN;
  uint8_t level = 0, input = 0;
  float pwm = 0;
  void (*isr)() = nullptr;
  int isrMode = 0;
};
struct Switch {      // a switch input's contacts: the state the stage puts them in, then the bounce after a change
  bool open = false;
  int slot = -1;     // 100 us slots since the last change, while it bounces; -1 once still
  uint64_t glitchUntil = 0;
};
Switch sws[64];
const char BOUNCE[] = "101101011101111";   // per 100 us slot after a change: 1 = the new state, 0 = back to the old
Pin pins[64];
uint64_t nowUs = 0;
bool autoAdvance = false, lastMot = false;
void (*tickFn)() = nullptr;
float tickUs = 0;
uint64_t nextTick = 0, motOffSince = 0;
void (*gptFn)() = nullptr;
std::deque<uint8_t> rx;
std::vector<uint8_t> txAcc;
double current[3] = {0, 0, 0};
int lastDir[3] = {0, 0, 0};
bool brakedSince[3] = {true, true, true};
Params P;
Report R;

uint8_t crc8(const uint8_t* d, size_t n) {
  uint8_t c = 0;
  for (size_t i = 0; i < n; i++) {
    uint8_t b = d[i];
    for (int j = 0; j < 8; j++) {
      c = ((c >> 7) ^ (b & 1)) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
      b >>= 1;
    }
  }
  return c;
}

void tmcFeed() {   // the TMC2209's side of the wire: datagrams in, replies out (after our own echo)
  while (txAcc.size() >= 4) {
    if (txAcc[0] != 0x05) { txAcc.erase(txAcc.begin()); continue; }
    bool w = txAcc[2] & 0x80;
    size_t need = w ? 8 : 4;
    if (txAcc.size() < need) return;
    uint8_t reg = txAcc[2] & 0x7F;
    bool ok = crc8(txAcc.data(), need - 1) == txAcc[need - 1] && txAcc[1] == tmc::ADDR;
    if (ok && chip.powered && !chip.mute) {
      if (w) {
        uint32_t v = ((uint32_t)txAcc[3] << 24) | ((uint32_t)txAcc[4] << 16) | ((uint32_t)txAcc[5] << 8) | txAcc[6];
        chip.writes++;
        if (reg == 0x04 || (reg == 0x00 && (v & 2))) chip.forbidden++;
        if (reg == 0x01) chip.reg[1] &= ~v;   // GSTAT: write 1 to clear
        else chip.reg[reg] = v;
        chip.ifcnt++;
      } else {
        uint32_t v = reg == 0x02 ? chip.ifcnt : reg == 0x6A ? (uint32_t)chip.mscnt : chip.reg[reg];
        uint8_t r[8] = {0x05, 0xFF, reg, (uint8_t)(v >> 24), (uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v, 0};
        r[7] = crc8(r, 7);
        for (uint8_t c : r) rx.push_back(c);
      }
    }
    txAcc.erase(txAcc.begin(), txAcc.begin() + need);
  }
}

int wrap1024(int x) { return ((x % 1024) + 1024) % 1024; }

int mscntInc() {   // MSCNT counts per STEP: MRES from CHOPCONF once mstep_reg_select is set, else the pins' 1/8
  uint32_t g = chip.reg[0x00], c = chip.reg[0x6C];
  int micro = (g & (1u << 7)) ? (256 >> ((c >> 24) & 0xF)) : 8;
  return 256 / (micro < 1 ? 1 : micro);
}

void moveRotor(int d) {   // d in MSCNT units; 1024 = 4 full steps = 20 um. MSCNT up (DIR low) moves -x by default
  rotorPhase = wrap1024(rotorPhase + d);
  railUm += physSign[3] * (-d) * (20.0 / 1024);
}

bool energised() { return pins[pin::EN].level == 0 && pins[pin::EN].mode == M_OUT && chip.powered; }

void energise() {   // the field pulls the rotor to MSCNT, the short way round (up to +-2 full steps)
  int d = wrap1024(chip.mscnt - rotorPhase);
  if (d >= 512) d -= 1024;
  if (d) { moveRotor(d); snaps++; }
}

void relax() {   // unpowered, it settles on the nearest detent, MSCNT 128 (mod 256)
  int k = ((rotorPhase - 128) % 256 + 256) % 256;
  int d = k <= 128 ? -k : 256 - k;
  if (d) { moveRotor(d); relaxes++; }
}

void stepEdge() {
  if (!chip.powered) return;
  int d = pins[pin::DIR].level ? -mscntInc() : mscntInc();   // "a low state on DIR increases the counter"
  chip.mscnt = wrap1024(chip.mscnt + d);
  if (energised()) {
    moveRotor(d);
    railStepsMoved++;
  }
}

double modelSpeed(double d) { return d <= 0.038 ? 0 : 289.0 * (d - 0.038) / (0.92 - 0.038); }

double bridge(int a, bool& braking) {   // the drive fraction a DRV8251A sees: + forward, - reverse
  auto frac = [](int p) { const Pin& q = pins[p]; return q.mode == M_PWM ? (double)q.pwm : (q.level ? 1.0 : 0.0); };
  double h1 = frac(pin::IN1[a]), h2 = frac(pin::IN2[a]);
  braking = h1 >= 1 && h2 >= 1;
  if (h1 >= 1 && h2 < 1) return 1 - h2;
  if (h2 >= 1 && h1 < 1) return -(1 - h1);
  return 0;
}

void setInput(int p, bool v) {
  Pin& q = pins[p];
  bool was = q.input;
  q.input = v;
  if (was == v || !q.isr) return;
  if (q.isrMode == CHANGE || (q.isrMode == RISING && v) || (q.isrMode == FALLING && !v)) q.isr();
}

// Snap action: open from `at` on (towards dir), closed again only once diffUm back.
bool snap(bool open, double pos, double at, int dir) {
  double past = (pos - at) * dir;
  return open ? past > -diffUm : past >= 0;
}

// A switch input as the pin sees it: high = open, with the bounce after each change, or a glitch.
bool switchInput(int p, bool open) {
  Switch& w = sws[p];
  if (open != w.open) { w.open = open; w.slot = 0; }
  bool level = open;
  if (w.slot >= 0) {
    if (!bounce || w.slot >= (int)sizeof BOUNCE - 1) w.slot = -1;
    else if (BOUNCE[w.slot++] == '0') level = !open;
  }
  if (nowUs < w.glitchUntil) level = true;
  return level;
}
bool fineOpen[3][2];   // each fine axis's lo and hi switch (in series on one input)

// ---- the lid's ADS1115s: U4 at 0x48 (AIN0 T_EDGE, AIN1 the 3.3 V, AIN2 T_A, AIN3 J3), U3 at 0x49 (AIN0 / AIN1
// PELT+ / PELT- over 11, not modelled; AIN2 T_B, AIN3 J2). A conversion samples its input when it starts (noise and
// mains pickup included), and its result shows (OS back to 1) 7.8 ms later. Plugging the lid in resets both, as the
// real ones power up.
struct Ads {
  uint8_t addr;
  uint16_t config;
  uint8_t ptr;
  int16_t conv, next;
  uint64_t doneAt;   // us; 0: idle
};
Ads ads[2] = {{hw::ADS_U4, 0x8583, 0, 0, 0, 0}, {hw::ADS_U3, 0x8583, 0, 0, 0, 0}};
int wireAddr = -1;
std::vector<uint8_t> wireOut;
std::deque<uint8_t> wireIn;
uint64_t lidHighSince[64], lidSwOffSince = 0, lidGoneSince = 0;
bool lidWasPlugged = true;

double ntcVolts(int s) {
  if (lidBroken[s] == 'o') return lidV33;   // the 10k pulls it to the 3.3 V
  if (lidBroken[s] == 's') return 0;
  bool board = s < LID_BOARD_N;
  double t = (board ? lidBoard : lidMedium) + lidOffset[s] + 273.15;
  double r = 10e3 * exp((board ? 3380.0 : 3977.0) * (1 / t - 1 / 298.15));
  double v = lidV33 * r / (r + 10e3);
  static std::mt19937 gen(7);
  static std::normal_distribution<double> gauss(0.0, 1.0);
  if (lidNoiseV > 0) v += lidNoiseV * gauss(gen);
  if (lidMainsV > 0) v += lidMainsV * sin(2 * M_PI * lidMainsHz * (double)nowUs * 1e-6);
  return v;
}

double adsInput(int dev, int ain) {
  static const int U4[4] = {LID_EDGE, -1, LID_A, LID_J3};
  if (dev == 0) return U4[ain] < 0 ? lidV33 : ntcVolts(U4[ain]);
  return ain == 2 ? ntcVolts(LID_B) : ain == 3 ? ntcVolts(LID_J2) : 0.0;
}

void adsStart(Ads& a, int dev) {
  uint16_t c = a.config;
  if (!(c & 0x8000) || !(c & 0x0100)) return;   // OS = 1 in single-shot mode starts one
  int mux = (c >> 12) & 7, pga = (c >> 9) & 7;
  if (mux < 4 || pga != 1 || (c & 0xFF) != 0x83) violLidCfg++;   // not the NTCs' whole configuration
  else lidConversions++;
  static const double FS[8] = {6.144, 4.096, 2.048, 1.024, 0.512, 0.256, 0.256, 0.256};
  double v = mux >= 4 ? adsInput(dev, mux - 4) : 0;
  a.next = (int16_t)std::max(-32768L, std::min(32767L, lround(v / FS[pga] * 32768)));
  a.doneAt = nowUs + 7813;
}

Ads* adsAt(int addr) {
  if (!lidPlugged) return nullptr;
  for (Ads& a : ads)
    if (a.addr == addr) return &a;
  return nullptr;
}

bool lidHigh(int p) { const Pin& q = pins[p]; return q.mode == M_PWM ? q.pwm > 0 : q.level != 0; }

void lidPhysics(double dt) {
  if (lidPlugged && !lidWasPlugged)
    for (Ads& a : ads) { a.config = 0x8583; a.ptr = 0; a.doneAt = 0; }   // powered up again: the defaults
  lidWasPlugged = lidPlugged;
  setInput(pin::LID_SW, lidSw);
  bool in1 = lidHigh(pin::LID_IN1), in2 = lidHigh(pin::LID_IN2);
  if (in1 && in2) violLidBoth++;
  if (lidSw) lidSwOffSince = 0;
  else if (!lidSwOffSince) lidSwOffSince = nowUs | 1;
  else if (nowUs - lidSwOffSince > 2000 && (in1 || in2)) violLidSw++;
  if (lidPlugged) lidGoneSince = 0;
  else if (!lidGoneSince) lidGoneSince = nowUs | 1;
  else if (nowUs - lidGoneSince > 250000 && (in1 || in2)) violLidAbsent++;
  // The board, crudely: full heat puts ~6 W into its 41 J/K, full cool takes ~3 W out, 0.1 W/K to the room (50 %
  // settles ~30 K up, as client/mmi_client/sim_lid.py does from a 22 C room).
  double heat = lidPlugged ? lidDrive() * lidHeatSign : 0;
  double q = heat > 0 ? 6.0 * heat : 3.0 * heat;
  lidBoard += (q - 0.1 * (lidBoard - lidRoom)) / 41.0 * dt;
}

void physics(double dt) {
  if (motOn != lastMot) {
    lastMot = motOn;
    if (motOn) {
      tmcPowerUp();
      if (energised()) energise();
    } else {
      if (energised()) relax();
      chip.powered = false;
    }
  }
  for (int a = 0; a < 3; a++) {
    bool braking = false;
    double d = motOn ? bridge(a, braking) : 0;
    int dir = d > 0 ? 1 : (d < 0 ? -1 : 0);
    if (braking) brakedSince[a] = true;
    if (dir) {
      if (lastDir[a] && dir != lastDir[a] && !brakedSince[a]) violReversal++;
      lastDir[a] = dir;
      brakedSince[a] = false;
    }
    double I = 0;
    if (dir) {
      double nx = fine[a] + dir * physSign[a] * modelSpeed(fabs(d)) * dt;
      if (jam[a] || fabs(nx) > 7000) I = fmin(0.93, fabs(d) * 9.16 / 3.5);
      else { fine[a] = nx; I = 0.08 + 0.12 * fabs(d); }
    }
    current[a] = I;
  }
  setInput(pin::MOT_ON, motOn);
  lidPhysics(dt);
  static bool railOpen[2];
  for (int a = 0; a < 3; a++) {
    fineOpen[a][0] = snap(fineOpen[a][0], fine[a], -6600, -1);
    fineOpen[a][1] = snap(fineOpen[a][1], fine[a], 6600, 1);
  }
  railOpen[0] = snap(railOpen[0], railUm + fine[0], railMinUm, -1);   // the effector's, at the motor end
  railOpen[1] = snap(railOpen[1], railUm, railMaxUm, 1);              // the carriage's, at the bearing end
  for (int a = 0; a < 3; a++)
    setInput(pin::LIM_FINE[a], !headPlugged || switchInput(pin::LIM_FINE[a], fineOpen[a][0] || fineOpen[a][1]));
  setInput(pin::LIM_MIN, !headPlugged || switchInput(pin::LIM_MIN, railOpen[0]));
  setInput(pin::LIM_MAX, !headPlugged || switchInput(pin::LIM_MAX, railOpen[1]));
  if (!motOn) {   // MUST, rule 11: while MOT_ON is low, STEP low, EN high, the head's inputs low
    if (!motOffSince) motOffSince = nowUs | 1;
    else if (nowUs - motOffSince > 2000) {
      bool bad = pins[pin::EN].level == 0 || pins[pin::STEP].level;
      for (int a = 0; a < 3; a++)
        bad |= pins[pin::IN1[a]].mode == M_PWM || pins[pin::IN2[a]].mode == M_PWM || pins[pin::IN1[a]].level ||
               pins[pin::IN2[a]].level;
      if (bad) violMotorsOff++;
    }
  } else {
    motOffSince = 0;
  }
}
}  // namespace

// ---- the mocked Teensy API ----
uint32_t sim_GPT2_CR, sim_GPT2_PR, sim_GPT2_SR, sim_GPT2_IR, sim_GPT2_OCR1, sim_CCM_CCGR0;
uint32_t sim_gpt_cnt() { return (uint32_t)(nowUs * 24); }
SimSerial Serial1;
void SimSerial::begin(uint32_t) {}
int SimSerial::available() { return (int)rx.size(); }
int SimSerial::read() {
  if (rx.empty()) return -1;
  int c = rx.front();
  rx.pop_front();
  return c;
}
size_t SimSerial::write(const uint8_t* b, size_t n) {
  for (size_t i = 0; i < n; i++) { rx.push_back(b[i]); txAcc.push_back(b[i]); }   // our own echo, then the chip
  tmcFeed();
  return n;
}

void pinMode(uint8_t p, uint8_t m) { pins[p].mode = m == OUTPUT ? M_OUT : M_IN; }
void digitalWriteFast(uint8_t p, uint8_t v) {
  if ((p == pin::LID_IN1 || p == pin::LID_IN2) && v && !pins[p].level) lidHighSince[p] = nowUs;
  bool rising = p == pin::STEP && !pins[p].level && v;
  bool wasOn = p == pin::EN && energised();
  pins[p].level = v ? 1 : 0;
  if (rising && pins[p].mode == M_OUT) stepEdge();
  if (p == pin::EN) {
    bool on = energised();
    if (on && !wasOn) { enLows++; energise(); }
    if (!on && wasOn) relax();
  }
}
uint8_t digitalReadFast(uint8_t p) { return pins[p].mode == M_IN ? pins[p].input : pins[p].level; }
void analogWrite(uint8_t p, int v) {
  bool lid = p == pin::LID_IN1 || p == pin::LID_IN2;   // a start from GPIO: the wake pulse first (>= 5 us high)
  if (lid && pins[p].mode != M_PWM && !(pins[p].mode == M_OUT && pins[p].level && nowUs - lidHighSince[p] >= 5))
    violLidWake++;
  pins[p].mode = M_PWM;
  pins[p].pwm = v / 4096.0f;
}
int analogRead(uint8_t p) {
  for (int a = 0; a < 3; a++)
    if (p == pin::IPROPI[a]) return (int)lround(fmin(current[a] * 3.15 / 3.3, 1.0) * 4095);
  return 0;
}
void analogWriteFrequency(uint8_t, float) {}
void analogWriteResolution(int) {}
void analogReadResolution(int) {}
void analogReadAveraging(int) {}
uint32_t millis() { return (uint32_t)(nowUs / 1000); }
uint32_t micros() {
  if (autoAdvance) nowUs++;
  return (uint32_t)nowUs;
}
void delayNanoseconds(uint32_t) {}
void noInterrupts() {}
void interrupts() {}
int digitalPinToInterrupt(uint8_t p) { return p; }
void attachInterrupt(int irq, void (*fn)(), int mode) {
  pins[irq].isr = fn;
  pins[irq].isrMode = mode;
}
void attachInterruptVector(int irq, void (*fn)()) {
  if (irq == IRQ_GPT2) gptFn = fn;
}
bool simTimerBegin(void (*fn)(), float us) {
  tickFn = fn;
  tickUs = us;
  nextTick = nowUs + (uint64_t)us;
  return true;
}

TwoWire Wire1;
void TwoWire::beginTransmission(uint8_t addr) {
  wireAddr = addr;
  wireOut.clear();
}
size_t TwoWire::write(uint8_t b) {
  wireOut.push_back(b);
  return 1;
}
uint8_t TwoWire::endTransmission(bool) {
  Ads* a = adsAt(wireAddr);
  if (!a) return 2;   // nothing acknowledged the address
  if (!wireOut.empty()) {
    a->ptr = wireOut[0] & 3;
    if (wireOut.size() >= 3 && a->ptr == 1) {
      a->config = (uint16_t)((wireOut[1] << 8) | wireOut[2]);
      adsStart(*a, a == &ads[0] ? 0 : 1);
    }
  }
  return 0;
}
uint8_t TwoWire::requestFrom(uint8_t addr, uint8_t n) {
  wireIn.clear();
  Ads* a = adsAt(addr);
  if (!a) return 0;
  if (a->doneAt && nowUs >= a->doneAt) { a->conv = a->next; a->doneAt = 0; }
  bool busy = a->doneAt != 0;
  uint16_t v = a->ptr == 0 ? (uint16_t)a->conv : a->ptr == 1 ? (uint16_t)((a->config & 0x7FFF) | (busy ? 0 : 0x8000)) : 0;
  for (uint8_t i = 0; i < n; i++) wireIn.push_back(i == 0 ? (uint8_t)(v >> 8) : i == 1 ? (uint8_t)v : 0xFF);
  return n;
}
int TwoWire::available() { return (int)wireIn.size(); }
int TwoWire::read() {
  if (wireIn.empty()) return -1;
  int c = wireIn.front();
  wireIn.pop_front();
  return c;
}

// The firmware's events land here instead of on a socket.
void netEvent(const char* fmt, ...) {
  char b[640];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  events.push_back(b);
  if (getenv("SIM_VERBOSE")) printf("    %8.3f s  !%s\n", nowUs / 1e6, b);
}

namespace sim {
float lidDrive() {
  auto frac = [](int p) { const Pin& q = pins[p]; return q.mode == M_PWM ? q.pwm : (q.level ? 1.0f : 0.0f); };
  return frac(pin::LID_IN1) - frac(pin::LID_IN2);
}

Params& config() {
  static bool init = false;
  if (!init) { paramsDefaults(P); init = true; }
  return P;
}

void tmcPowerUp() {
  memset(chip.reg, 0, sizeof chip.reg);
  chip.reg[0x00] = 0x101;        // I_scale_analog, multistep_filt
  chip.reg[0x01] = 0x1;          // GSTAT.reset
  chip.reg[0x06] = 0x21000000u;  // IOIN.VERSION
  chip.reg[0x6C] = 0x10000053u;  // CHOPCONF
  chip.reg[0x70] = 0xC10D0024u;  // PWMCONF
  chip.ifcnt = 0;
  chip.mscnt = 0;
  chip.powered = true;
}
void tmcBrownOut() {   // a VS dip with EN low: the chip resets, and its field jumps to MSCNT 0
  tmcPowerUp();
  if (energised()) energise();
}

bool saw(const char* prefix) {
  for (const std::string& e : events)
    if (e.compare(0, strlen(prefix), prefix) == 0) return true;
  return false;
}
void clearEvents() { events.clear(); }
void glitch(int p, uint32_t us) { sws[p].glitchUntil = nowUs + us; }

void boot() {
  // stage.ino's safePins(), as far as these modules care
  pins[pin::EN].mode = M_OUT; pins[pin::EN].level = 1;
  pins[pin::STEP].mode = M_OUT; pins[pin::STEP].level = 0;
  pins[pin::DIR].mode = M_OUT;
  for (int a = 0; a < 3; a++) { pinMode(pin::IN1[a], OUTPUT); pinMode(pin::IN2[a], OUTPUT); }
  physics(0);   // the inputs as they stand (no interrupts attached yet)
  Params& p = config();
  paramsFinish(p, R);
  autoAdvance = true;   // railBegin times GPT2 against micros()
  railBegin(p);
  autoAdvance = false;
  driveBegin(p);
  lightBegin();
  lidBegin(p);
}

void run(uint32_t ms) {
  uint64_t end = nowUs + (uint64_t)ms * 1000;
  while (nowUs < end) {
    nowUs++;
    if (gptFn && (sim_GPT2_IR & GPT_IR_OF1IE) && (sim_GPT2_CR & GPT_CR_EN) &&
        (int32_t)(sim_gpt_cnt() - sim_GPT2_OCR1) >= 0)
      gptFn();
    if (tickFn && nowUs >= nextTick) { nextTick += (uint64_t)tickUs; tickFn(); }
    if (nowUs % 100 == 0) physics(100e-6);
    if (nowUs % 20 == 0) { drivePoll(); railPoll(); lidPoll(); }
  }
}

float pwmHigh(int p) { return pins[p].mode == M_PWM ? pins[p].pwm : -1; }
int level(int p) { return pins[p].mode == M_PWM ? -1 : pins[p].level; }
}  // namespace sim
