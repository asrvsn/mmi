#include "lid.h"

#include <Arduino.h>
#include <Wire.h>
#include <stdio.h>

#include "board.h"
#include "light.h"
#include "net.h"

namespace {
const Params* P = nullptr;

// ---- the ADCs (../hardware.md, "The ADCs"; lid v16's netlist): U4 0x48 reads T_EDGE, the 3.3 V, T_A and J3 on
// AIN0-3; U3 0x49 reads PELT+, PELT-, T_B and J2. A round converts one channel on each at once; the 3.3 V goes first,
// so every NTC of a cycle is divided by that cycle's reference. PELT+/- (the Seebeck reading) aren't read yet.
constexpr uint8_t V33 = LID_NS;            // the reference's slot in raw[]
constexpr int8_t NONE = -1;
struct Round {
  int8_t ain[2];                           // U4's channel, U3's (NONE: that one sits the round out)
  uint8_t slot[2];
};
const Round ROUNDS[] = {
    {{1, 2}, {V33, LID_B}},
    {{2, 3}, {LID_A, LID_J2}},
    {{0, NONE}, {LID_EDGE, 0}},
    {{3, NONE}, {LID_J3, 0}},
};
constexpr uint8_t N_ROUNDS = sizeof ROUNDS / sizeof ROUNDS[0];
const uint8_t ADDR[2] = {hw::ADS_U4, hw::ADS_U3};
const char* const NAME[LID_NS] = {"board.a", "board.b", "board.edge", "med.j2", "med.j3"};

// MUST (rule 7): single-shot, and the whole configuration with every conversion: OS, the channel against GND,
// +-4.096 V, single-shot, 128 SPS, the comparator off (AIN0 0xC383 ... AIN3 0xF383).
uint16_t config(int8_t ain) { return 0x8000 | (uint16_t)((4 + ain) << 12) | 0x0200 | 0x0100 | 0x0080 | 0x0003; }

bool writeConfig(uint8_t addr, uint16_t cfg) {
  Wire1.beginTransmission(addr);
  Wire1.write(0x01);
  Wire1.write((uint8_t)(cfg >> 8));
  Wire1.write((uint8_t)cfg);
  return Wire1.endTransmission() == 0;
}

bool readReg(uint8_t addr, uint8_t reg, uint16_t& v) {
  Wire1.beginTransmission(addr);
  Wire1.write(reg);
  if (Wire1.endTransmission() != 0) return false;
  if (Wire1.requestFrom(addr, (uint8_t)2) != 2) return false;
  v = (uint16_t)(Wire1.read() << 8);
  v |= (uint16_t)(Wire1.read() & 0xFF);
  return true;
}

// One bus transaction per lidPoll() at most (~0.5 ms at 100 kHz), so the loop never waits long on the lid. A cycle
// (the four rounds, ~45 ms) starts on every 62.5 ms slot, timed by micros() throughout, so each channel is sampled
// exactly 62.5 ms apart (what cancels the mains).
enum class Bus : uint8_t { IDLE, START, WAIT, CHECK, READ };
struct {
  Bus st = Bus::IDLE;
  uint8_t round = 0, adc = 0;
  uint32_t t0 = 0, next = 0;               // us: the round's conversions started; the next slot
  int16_t raw[LID_NS + 1];
} bus;

// ---- the window: the samples since the last readout
struct {
  uint8_t slot;                            // the slot under way, 0 to LID_WINDOW - 1
  LidStat t[LID_NS];
  Ntc worst[LID_NS];                       // OPEN or SHORT once any sample has read so
  float v33;                               // the 3.3 V's samples, summed
  uint8_t n33;
  bool v33bad;                             // a sample of it outside its 5 %: v33off
  float v33off;
} win;

void winReset() {
  win.slot = 0;
  for (uint8_t s = 0; s < LID_NS; s++) {
    win.t[s] = LidStat();
    win.worst[s] = Ntc::OK;
  }
  win.v33 = 0;
  win.n33 = 0;
  win.v33bad = false;
}

// ---- the last readout
bool present = false;                      // a readout has been made, and the lid has answered ever since
uint32_t readAt = 0;                       // ms: when
Ntc state[LID_NS];
float temp[LID_NS], sd[LID_NS];
float v33 = 0;
bool fault = false, hot = false, medHot = false;   // a sensor can't be trusted; the board at 60 C; the medium at its max
char faultText[40] = "", hotText[48] = "", medText[64] = "";
bool fitted[LID_NS];                       // a probe that has read since the lid came (back): open now, it's come loose
bool fresh = false;                        // a readout not sent yet

// ---- the duty, and who set it
uint16_t dutyCode = 0;
int8_t dutySign = 1;                       // + heats (lid.sign maps it onto IN1 or IN2)
uint8_t owner = 0;                         // the session whose duty is in force (0: none)
const char* cut = "0";                     // why the unit itself last set the duty to 0, since the last command

// ---- SW10, and the bridge
bool swOn = false;
uint32_t swHighSince = 0;
enum class Out : uint8_t { OFF, WAKE, RUN };
struct {
  Out st = Out::OFF;
  int8_t dir = 0;                          // the input driven: +1 IN1, -1 IN2
  uint16_t code = 0;
  uint32_t wakeAt = 0;                     // us
} out;

struct Shown { bool sw; uint8_t mode; uint16_t code; int8_t sign; const char* cut; };
Shown shown = {false, 255, 0, 1, nullptr};   // as last sent: nothing yet

enum Mode : uint8_t { M_ABSENT, M_FAULT, M_IDLE, M_MANUAL };
const char* const MODE[] = {"absent", "fault", "idle", "manual"};
uint8_t mode() { return !present ? M_ABSENT : fault ? M_FAULT : dutyCode ? M_MANUAL : M_IDLE; }

void pinHigh(uint8_t p) { digitalWriteFast(p, HIGH); pinMode(p, OUTPUT); }   // data first, then the mux: no glitch
void pinLow(uint8_t p) { digitalWriteFast(p, LOW); pinMode(p, OUTPUT); }

// Both inputs low: the bridge coasts, and sleeps after 1-1.5 ms (a QuadTimer pin's analogWrite(0) would still pulse).
void bridgeOff() {
  pinLow(pin::LID_IN1);
  pinLow(pin::LID_IN2);
  out.st = Out::OFF;
  out.dir = 0;
  out.code = 0;
}

void cutNow(const char* why) {
  dutyCode = 0;
  owner = 0;
  cut = why;
  bridgeOff();
}

// The bridge as the state asks: the duty while SW10 is on and the lid present and sound. Coast PWM on one input with
// the other low (rule 1); from rest, and on a reversal, the wake pulse first (rule 2).
void bridgeUpdate() {
  int8_t want = (swOn && present && !fault && dutyCode) ? (int8_t)(dutySign * P->lid.sign) : 0;
  if (!want) {
    if (out.st != Out::OFF) bridgeOff();
    return;
  }
  if (out.st != Out::OFF && want != out.dir) bridgeOff();   // reversing: the active input low first
  uint8_t p = want > 0 ? pin::LID_IN1 : pin::LID_IN2;
  switch (out.st) {
    case Out::OFF:
      pinHigh(p);
      out.dir = want;
      out.wakeAt = micros();
      out.st = Out::WAKE;
      break;
    case Out::WAKE:
      if (micros() - out.wakeAt < hw::LID_WAKE_US) break;
      analogWrite(p, dutyCode);
      out.code = dutyCode;
      out.st = Out::RUN;
      break;
    case Out::RUN:
      if (out.code != dutyCode) {
        analogWrite(p, dutyCode);
        out.code = dutyCode;
      }
      break;
  }
}

// MUST (rule 8): SW10 low holds both inputs low at once; back high, it counts after 20 ms steady.
void pollSwitch() {
  if (!digitalReadFast(pin::LID_SW)) {
    swOn = false;
    swHighSince = 0;
    return;
  }
  if (!swHighSince) swHighSince = millis() | 1;
  if (!swOn && millis() - swHighSince >= hw::LID_SW_MS) swOn = true;
}

// A transaction went unanswered: the lid is gone (MUST: both inputs low while either ADC doesn't answer), and the
// window with it. The bus tries again on the next slot; the first readout after that brings the lid back, and finds
// which probes are fitted anew.
void lost() {
  present = false;
  for (bool& f : fitted) f = false;
  if (dutyCode) cutNow("absent");
  else bridgeOff();
  bus.st = Bus::IDLE;
  bus.round = 0;
  winReset();
}

// A cycle has answered: each NTC's temperature, against that cycle's 3.3 V, into the window.
void sample() {
  int32_t c33 = bus.raw[V33];
  float v = c33 * (hw::ADS_FS_V / 32768.0f);
  win.v33 += v;
  win.n33++;
  if (!win.v33bad && fabsf(v - hw::LID_V33) > hw::LID_V33 * hw::LID_V33_TOL) {
    win.v33bad = true;
    win.v33off = v;
  }
  for (uint8_t s = 0; s < LID_NS; s++) {
    float c;
    Ntc n = ntcCelsius(bus.raw[s], c33, s < LID_BOARD_N ? hw::NTC_B_BOARD : hw::NTC_B_PROBE, c);
    if (n == Ntc::OK) win.t[s].add(win.slot, c);
    else if (win.worst[s] == Ntc::OK) win.worst[s] = n;
  }
}

// The window's last slot: the readout (each NTC's mean and SD, the 3.3 V's mean), then whether every sensor can be
// trusted and the board and the medium are under their limits, on those means. A sensor that read open or shorted
// even once in the window reads so. A probe open from the lid's first readout isn't fitted, and guards nothing; one
// that has read and goes open is a fault (../hardware.md, "The ADCs"). The thermal loop will run here too.
void readout() {
  if (!win.n33) { winReset(); return; }   // nothing sampled (a stalled loop; the watchdog would act first)
  v33 = win.v33 / win.n33;
  fault = hot = medHot = false;
  faultText[0] = hotText[0] = medText[0] = '\0';
  if (win.v33bad) {
    fault = true;
    snprintf(faultText, sizeof faultText, "v33 %.3f V", (double)win.v33off);
  }
  float hottest = -1000, warmest = -1000;
  int8_t hotSensor = -1, warmSensor = -1;
  for (uint8_t s = 0; s < LID_NS; s++) {
    bool board = s < LID_BOARD_N;
    state[s] = win.worst[s];
    if (state[s] == Ntc::OK) {
      temp[s] = win.t[s].mean();
      sd[s] = win.t[s].sd();
    }
    // a board NTC open, shorted or out of 0-100 C; a probe shorted, or open once fitted
    bool bad = board ? !(state[s] == Ntc::OK && temp[s] >= 0 && temp[s] <= 100)
                     : state[s] == Ntc::SHORT || (state[s] == Ntc::OPEN && fitted[s]);
    if (!board && state[s] == Ntc::OK) fitted[s] = true;
    if (bad && !fault) {
      fault = true;
      if (state[s] == Ntc::OK) snprintf(faultText, sizeof faultText, "%s %.2f C", NAME[s], (double)temp[s]);
      else snprintf(faultText, sizeof faultText, "%s %s", NAME[s], state[s] == Ntc::OPEN ? "open" : "short");
    }
    if (state[s] != Ntc::OK) continue;
    if (board && temp[s] > hottest) { hottest = temp[s]; hotSensor = (int8_t)s; }
    if (!board && temp[s] > warmest) { warmest = temp[s]; warmSensor = (int8_t)s; }
  }
  if (!fault && hottest >= hw::LID_BOARD_MAX_C) {
    hot = true;
    snprintf(hotText, sizeof hotText, "%s %.2f C, the limit is %.0f", NAME[hotSensor], (double)hottest,
             (double)hw::LID_BOARD_MAX_C);
  }
  if (!fault && warmSensor >= 0 && warmest >= P->lid.medium_max) {
    medHot = true;
    char lim[12];
    fmtLevel(P->lid.medium_max, lim, sizeof lim);
    snprintf(medText, sizeof medText, "%s %.2f C, the limit is %s (lid.medium_max)", NAME[warmSensor],
             (double)warmest, lim);
  }
  present = true;
  readAt = millis();
  fresh = true;
  if (dutyCode && fault) cutNow("fault");
  else if (dutyCode && hot) cutNow("hot");
  else if (dutyCode && medHot) cutNow("medium");
  winReset();
}

// A slot has gone by, sampled or skipped: the window's last one is read out.
void nextSlot() {
  if (++win.slot >= hw::LID_WINDOW) readout();
}

void pollBus() {
  uint32_t now = micros();
  const Round& r = ROUNDS[bus.round];   // bus.round < N_ROUNDS always (back to 0 at each cycle's end)
  switch (bus.st) {
    case Bus::IDLE:
      if ((int32_t)(now - bus.next) < 0) return;
      while ((int32_t)(now - bus.next) >= (int32_t)hw::LID_POLL_US) {   // slots the loop held up past: skipped,
        bus.next += hw::LID_POLL_US;                                     // the grid kept
        nextSlot();
      }
      bus.next += hw::LID_POLL_US;
      bus.round = bus.adc = 0;
      bus.st = Bus::START;
      return;
    case Bus::START:   // a conversion on each ADC in turn, then wait for both
      if (r.ain[bus.adc] != NONE && !writeConfig(ADDR[bus.adc], config(r.ain[bus.adc]))) return lost();
      if (++bus.adc < 2) return;
      bus.t0 = now;
      bus.adc = 0;
      bus.st = Bus::WAIT;
      return;
    case Bus::WAIT:
      if (now - bus.t0 >= hw::ADS_CONV_US) bus.st = Bus::CHECK;
      return;
    case Bus::CHECK: {   // OS reads 1 once its conversion is done (rule 7: poll it, then read)
      if (r.ain[bus.adc] == NONE) { bus.st = Bus::READ; return; }
      uint16_t cfg;
      if (!readReg(ADDR[bus.adc], 0x01, cfg)) return lost();
      if (cfg & 0x8000) bus.st = Bus::READ;
      else if (now - bus.t0 > hw::ADS_LATE_US) lost();
      return;
    }
    case Bus::READ: {
      if (r.ain[bus.adc] != NONE) {
        uint16_t v;
        if (!readReg(ADDR[bus.adc], 0x00, v)) return lost();
        bus.raw[r.slot[bus.adc]] = (int16_t)v;
      }
      if (++bus.adc < 2) { bus.st = Bus::CHECK; return; }
      bus.adc = 0;
      if (++bus.round < N_ROUNDS) { bus.st = Bus::START; return; }
      bus.round = 0;
      bus.st = Bus::IDLE;
      sample();
      nextSlot();
      return;
    }
  }
}
}  // namespace

void lidBegin(const Params& p) {
  P = &p;
  bridgeOff();   // as safePins() left it
  analogWriteFrequency(pin::LID_IN1, hw::LID_PWM_HZ);   // two QuadTimer3 channels: each its own
  analogWriteFrequency(pin::LID_IN2, hw::LID_PWM_HZ);
  Wire1.begin();
  Wire1.setClock(100000);   // keep it there: the lid's cable and pull-ups (hardware.md, pins 16 / 17)
  bus.st = Bus::IDLE;
  bus.next = micros();
  winReset();
}

void lidPoll() {
  pollSwitch();
  pollBus();
  bridgeUpdate();
  lidPublish();
}

LidSet lidSet(float pct, uint8_t src, char* why, size_t cap) {
  uint16_t code = lidCode(pct, P->lid.duty_max);
  if (code) {
    if (!present) return LidSet::ABSENT;
    if (fault) { snprintf(why, cap, "%s", faultText); return LidSet::FAULT; }
    if (hot) { snprintf(why, cap, "%s", hotText); return LidSet::HOT; }
    if (medHot) { snprintf(why, cap, "%s", medText); return LidSet::MEDIUM; }
  }
  dutyCode = code;
  dutySign = code && pct < 0 ? -1 : 1;
  owner = code ? src : 0;
  cut = "0";
  bridgeUpdate();
  return LidSet::OK;
}

bool lidLost(uint8_t src, const char* cause) {
  if (!dutyCode || owner != src) return false;
  cutNow(cause);
  return true;
}

float lidDuty() { return dutyCode ? dutySign * lidLevel(dutyCode) : 0.0f; }

size_t lidRecord(char* b, size_t cap) {
  char d[12];
  fmtLevel(lidDuty(), d, sizeof d);
  size_t n = 0;
  appendf(b, cap, &n, "t=%lu sw=%s mode=%s duty=%s cut=%s", (unsigned long)(present ? readAt : millis()),
          swOn ? "on" : "off", MODE[mode()], d, cut);
  if (!present) return n;
  for (uint8_t s = 0; s < LID_NS; s++) {
    if (state[s] == Ntc::OK) appendf(b, cap, &n, " %s=%.3f %s.sd=%.3f", NAME[s], (double)temp[s], NAME[s], (double)sd[s]);
    else appendf(b, cap, &n, " %s=%s", NAME[s], state[s] == Ntc::OPEN ? "open" : "short");
  }
  appendf(b, cap, &n, " v33=%.3f", (double)v33);
  return n;
}

void lidPublish() {
  Shown now = {swOn, mode(), dutyCode, dutySign, cut};
  bool same = now.sw == shown.sw && now.mode == shown.mode && now.code == shown.code && now.sign == shown.sign &&
              shown.cut && !strcmp(now.cut, shown.cut);
  if (same && !fresh) return;
  shown = now;
  fresh = false;
  char rec[320];
  lidRecord(rec, sizeof rec);
  netEvent("lid %s", rec);
}
