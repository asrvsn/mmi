#include "drive.h"

#include <Arduino.h>
#include <IntervalTimer.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "board.h"
#include "net.h"
#include "rail.h"
#include "text.h"

// Interrupt priorities (lower = more urgent): the step generator (GPT2, rail.cpp) 16; the control tick (PIT) and the
// limit switches (GPIO) both 48, so neither ever interrupts the other half way through; USB and Ethernet below.
namespace {
constexpr uint8_t PRIO_TICK = 48;
constexpr int8_t LIM_UNKNOWN = 2;   // a fine switch found open with nothing moving: which end is unknown
enum Out : uint8_t { COAST, WAKE, RUN, BRAKE };

const Params* P = nullptr;

// A limit input (hardware.md, "Limit switches": normally closed, high = open). Its interrupt runs on every change. A
// change to high stops whatever moves towards that switch at once; any change restarts the settling. The level
// counts once it has held for LIMIT_CONFIRM_US, so the contacts' bounce, opening or closing, neither confirms nor
// releases anything, and the end a trip blocks is decided once, at its first edge.
struct Input {
  volatile uint32_t changedAt = 0;   // micros() of the last change (the interrupt)
  volatile bool settled = true;      // unchanged for LIMIT_CONFIRM_US since: the level is the switch's
};

bool settle(Input& in) {   // with interrupts off
  if (!in.settled && (int32_t)(micros() - in.changedAt) >= (int32_t)hw::LIMIT_CONFIRM_US) in.settled = true;
  return in.settled;
}

void unsettle(Input& in) {
  in.changedAt = micros();
  in.settled = false;
}

struct Fine {
  volatile float v = 0;        // um/s, api frame, after ramps
  volatile float duty = 0;     // signed, motor frame, as output (0 unless running)
  volatile float iF = 0;       // A, IPROPI filtered over ~10 ms
  volatile uint8_t out = COAST;
  volatile uint16_t tState = 0, stallMs = 0, senseMs = 0, awayMs = 0;
  volatile uint32_t runMs = 0;
  volatile uint8_t blkLim = 0, blkStall = 0, blkFault = 0;
  Input in;                    // its hi and lo switches, in series
  volatile int8_t lim = 0;     // the switch, as confirmed: 0 closed, +1 / -1 open at that end, or LIM_UNKNOWN
  volatile bool tripping = false;   // opened, not yet confirmed: a glitch is taken back
  volatile int8_t tripEnd = 0;      // the end that trip was heading for; 0: nothing moved, so the end is unknown
  volatile uint8_t tripBlk = 0;     // the half-space it blocked
  volatile bool evSense = false;
};
Fine F[3];

struct RailSwitch {
  uint8_t pin;
  int8_t end;                  // -1 MIN (J7), +1 MAX (J8)
  bool effector;               // rides on the effector: also blocks fine x towards its end
  Input in;
  volatile bool open = false;  // confirmed open, or found open at power-up
  volatile bool tripping = false;   // opened, not yet confirmed
  volatile bool mismatch = false, lost = false;   // that trip: against the frame / the rail stopped without a ramp
  volatile uint8_t tripBlkR = 0, tripBlkX = 0;    // what it blocked, on the rail and on fine x
  volatile uint8_t holdR = 0, holdX = 0;          // a mismatch freezes the axes involved: latched once the trip is
                                                  // confirmed, lifted if it was a glitch
  bool evLost = false, evMismatch = false;        // main loop: to report after the state
  int32_t stepsAt = 0;         // where it opened, for the release check
};
RailSwitch SW[2];

volatile uint8_t railBlkLim = 0, railBlkFault = 0, effBlkX = 0;
const char AXIS[] = "xyzr";
char sentLim[AX_N][4], sentStall[3][4];   // the state as last sent (publish): what a client connecting now is told
volatile float jogT[AX_N];
volatile uint32_t jogStamp = 0;
uint8_t jogSrc = 0;            // the client whose non-zero jog set the targets (0: none yet)
volatile Motors motors = Motors::OFF;
volatile uint32_t motUpMs = 0, motWaitMs = 0, ticks = 0;
volatile bool fineReady = false, evLease = false, evMotorsOff = false, evRailLost = false;
volatile bool halted = false;
bool faultLatched = false;
char faultLine[4][160];
uint8_t nFaults = 0;
IntervalTimer tickTimer;

// A fault that holds until a restart: sent now, and replayed to every client that connects later.
void latch(const char* fmt, ...) {
  char line[160];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(line, sizeof line, fmt, ap);
  va_end(ap);
  faultLatched = true;
  if (nFaults < 4) strcpy(faultLine[nFaults++], line);
  netEvent("%s", line);
}

inline int8_t sgn(float v) { return v > 0 ? 1 : (v < 0 ? -1 : 0); }
inline uint8_t half(int8_t dir) { return dir > 0 ? BLK_POS : BLK_NEG; }

// Every block in force: limits, a stall, faults, and a mismatch being confirmed.
uint8_t railBlk() { return railBlkLim | railBlkFault | SW[0].holdR | SW[1].holdR; }
uint8_t fineBlk(int a) {
  const Fine& f = F[a];
  uint8_t b = f.blkLim | f.blkStall | f.blkFault;
  if (a == AX_X) b |= effBlkX | SW[0].holdX | SW[1].holdX;
  return b;
}

// ---- DRV8251A outputs (hardware.md "Fine axes": drive/brake PWM, through brake on a reversal, coast at rest) ----
void pinHigh(uint8_t p) { digitalWriteFast(p, HIGH); pinMode(p, OUTPUT); }   // data first, then the mux: no glitch
void pinLow(uint8_t p) { digitalWriteFast(p, LOW); pinMode(p, OUTPUT); }
void pinPwm(uint8_t p, float high) { analogWrite(p, (int)lroundf(high * 4096.0f)); }
void outCoast(int a) { pinLow(pin::IN1[a]); pinLow(pin::IN2[a]); }
void outBrake(int a) { pinHigh(pin::IN1[a]); pinHigh(pin::IN2[a]); }
// Forward: IN1 high, IN2 PWM whose high time brakes, so the drive is 1 - duty(IN2). Reverse mirrors it.
void outRun(int a, float d) {
  if (d > 0) { pinHigh(pin::IN1[a]); pinPwm(pin::IN2[a], 1.0f - d); }
  else { pinHigh(pin::IN2[a]); pinPwm(pin::IN1[a], 1.0f + d); }
}
void outDuty(int a, float d) { pinPwm(d > 0 ? pin::IN2[a] : pin::IN1[a], 1.0f - fabsf(d)); }

void brakeNow(int a) {
  Fine& f = F[a];
  f.v = 0;
  f.duty = 0;
  if (f.out == RUN || f.out == WAKE) { outBrake(a); f.out = BRAKE; f.tState = 0; }
}

void fineOut(int a, float d) {
  Fine& f = F[a];
  int8_t s = sgn(d), cur = sgn(f.duty);
  switch (f.out) {
    case COAST:   // asleep: brake for >= t_WAKE before the first PWM
      if (s) { outBrake(a); f.out = WAKE; f.tState = 0; }
      break;
    case WAKE:
      if (!s) { f.out = BRAKE; f.tState = 0; }
      else if (f.tState >= 1) { outRun(a, d); f.out = RUN; f.runMs = 0; f.duty = d; }
      break;
    case RUN:
      if (!s) { outBrake(a); f.out = BRAKE; f.tState = 0; f.duty = 0; }
      else if (s != cur) { outBrake(a); f.out = WAKE; f.tState = 0; f.duty = 0; }   // never through coast
      else if (d != f.duty) { outDuty(a, d); f.duty = d; }
      break;
    case BRAKE:   // awake: drive straight away, or coast (and sleep) after REST_BRAKE_MS
      if (s) { outRun(a, d); f.out = RUN; f.runMs = 0; f.duty = d; }
      else if (f.tState >= hw::REST_BRAKE_MS) { outCoast(a); f.out = COAST; }
      break;
  }
  if (f.tState < 0xFFFF) f.tState++;
}

// MUST (hardware.md rule 5): a stall stops the axis within 0.1 s. Past the inrush window, the filtered current
// above the absolute threshold, or above k x duty x VM / R_m (part-duty stalls), for stall_ms. Below the floor a
// stall is thermally harmless and isn't chased. An IPROPI that reads ~0 while driving hard is an open sense line.
void stallCheck(int a) {
  Fine& f = F[a];
  const FineAxis& p = P->ax[a];
  f.runMs++;
  float ad = fabsf(f.duty);
  if (f.runMs > P->fine.inrush_ms) {
    float thr = fminf(P->fine.stall_i, fmaxf(P->fine.stall_k * ad * hw::VM / p.r_m, P->fine.stall_floor));
    if (f.iF >= thr) {
      if (++f.stallMs >= P->fine.stall_ms) {
        f.blkStall |= half(sgn(f.v));
        f.stallMs = 0;
        brakeNow(a);
        return;
      }
    } else {
      f.stallMs = 0;
    }
  }
  if (ad >= 0.3f && f.runMs > 200 && f.iF < 0.02f) {
    if (++f.senseMs >= 200) {
      f.blkFault = BLK_POS | BLK_NEG;
      f.evSense = true;
      brakeNow(a);
    }
  } else {
    f.senseMs = 0;
  }
}

void fineTick(int a) {
  Fine& f = F[a];
  const FineAxis& p = P->ax[a];
  float amps = analogRead(pin::IPROPI[a]) * (hw::ADC_FULL_V / 4095.0f) / hw::IPROPI_V_PER_A;
  f.iF += (amps - f.iF) * 0.1f;
  if (!fineReady) {
    if (f.out != COAST) { outCoast(a); f.out = COAST; }
    f.v = 0;
    f.duty = 0;
    return;
  }
  uint8_t blk = fineBlk(a);
  bool unknown = f.lim == LIM_UNKNOWN || (f.tripping && !f.tripEnd);   // open at an end it can't tell
  float cap = unknown ? fminf(p.vmax, P->fine.vunk) : p.vmax;
  float t = fmaxf(-cap, fminf(cap, unblocked(jogT[a], blk)));
  float v = rampStep(unblocked(f.v, blk), t, p.acc, p.dec, 0.001f);
  f.v = v;
  float d = 0;
  if (v != 0) {
    d = fminf(tableDuty(p.cal[v > 0 ? 0 : 1], fabsf(v)), p.duty_max);
    if (d < p.duty_min) d = 0;   // pulse mode isn't built yet: below duty_min the axis doesn't move
    if ((v > 0) != (p.sign > 0)) d = -d;
  }
  fineOut(a, d);
  if (f.out == RUN) stallCheck(a);
  else f.stallMs = f.senseMs = 0;
  if (f.blkStall) {   // a stall's block lifts once the axis has run the other way for 50 ms
    bool away = (f.v > 0 && (f.blkStall & BLK_NEG)) || (f.v < 0 && (f.blkStall & BLK_POS));
    f.awayMs = away ? f.awayMs + 1 : 0;
    if (f.awayMs >= 50) { f.blkStall = 0; f.awayMs = 0; }
  }
}

void zeroJog() {
  for (int a = 0; a < AX_N; a++) jogT[a] = 0;
}

// The halt (api.md 2): every axis stops now, no ramp, no parking, and non-zero jogs are refused until an all-zero
// one. Called with interrupts off, or from the tick.
void haltNow() {
  zeroJog();
  halted = true;
  for (int a = 0; a < 3; a++) brakeNow(a);   // brake at once; the rest sequence coasts them 100 ms later
  railStopNow();                             // no further steps, no ramp, no parking
}

void motorsLost() {   // MOT_ON low: the motors have no supply; coast, stop the rail, refuse motion
  evRailLost = railDir() != 0;
  motors = Motors::OFF;
  fineReady = false;
  for (int a = 0; a < 3; a++) {
    outCoast(a);
    F[a].out = COAST;
    F[a].v = 0;
    F[a].duty = 0;
  }
  railMotors(false);
  zeroJog();
  evMotorsOff = true;
}

void tick() {
  ticks++;
  bool mot = digitalReadFast(pin::MOT_ON);
  if (!mot) {
    if (motors != Motors::OFF) motorsLost();
    motUpMs = 0;
  } else if (motors == Motors::OFF) {
    if (++motUpMs >= hw::MOT_DEBOUNCE_MS) { motors = Motors::WAIT; motWaitMs = 0; railMotors(true); }
  } else if (motors == Motors::WAIT && !fineReady) {
    if (++motWaitMs >= hw::HEAD_9V_MS) fineReady = true;
  }
  if ((jogT[0] != 0 || jogT[1] != 0 || jogT[2] != 0 || jogT[3] != 0) && millis() - jogStamp > P->jog.lease) {
    haltNow();   // its client went quiet mid-move (hung, or its link stalled): a halt, as if it were lost
    evLease = true;
  }
  for (int a = 0; a < 3; a++) fineTick(a);
  railTick(motors == Motors::ON ? jogT[AX_R] : 0.0f, railBlk());
}

// ---- Limit switches (hardware.md rule 4: any input going high stops that axis at once) ----
// A fine axis's trip: it blocks the end the axis was heading for, and the axis brakes. With nothing moving the end is
// unknown, and nothing is blocked (both ways at no more than vunk). From the interrupt, or from the poll if an edge
// went unseen.
void tripFine(int a) {
  Fine& f = F[a];
  int8_t dir = sgn(f.v);
  f.tripping = true;
  f.tripEnd = dir;
  f.tripBlk = 0;
  if (dir) {
    f.tripBlk = half(dir) & ~f.blkLim;
    f.blkLim |= half(dir);
    brakeNow(a);
  }
}

void limFine(int a) {   // the interrupt, on every change
  Fine& f = F[a];
  unsettle(f.in);
  if (!digitalReadFast(pin::LIM_FINE[a])) return;   // closing: released once settled (pollFine)
  if (f.lim || f.tripping) {   // open already: a bounce. Its end was decided at the trip's first edge, so the way out
    int8_t end = f.lim ? f.lim : f.tripEnd;   // stays open; only motion towards that end stops (it's blocked anyway)
    if ((end == 1 || end == -1) && sgn(f.v) == end) brakeNow(a);
    return;
  }
  tripFine(a);
}

// A rail switch blocks its own end on the rail. The effector's switch also blocks fine x towards that end, because
// fine x moves the effector too (the effector carries it, left of the carriage). Tripped while everything involved
// moves away from its end, the signs or the wiring contradict the frame: those axes freeze, until a restart once
// the trip is confirmed. From the interrupt, or from the poll if an edge went unseen.
void tripRail(int s) {
  RailSwitch& w = SW[s];
  int8_t dr = railDir();
  int8_t dx = w.effector ? sgn(F[AX_X].v) : 0;
  uint8_t b = half(w.end);
  w.tripping = true;
  w.tripBlkR = b & ~railBlkLim;
  railBlkLim |= b;
  w.tripBlkX = 0;
  if (w.effector) {
    w.tripBlkX = b & ~effBlkX;
    effBlkX |= b;
  }
  w.lost = dr == w.end;
  w.mismatch = false;
  bool toward = dr == w.end || dx == w.end;
  if (!toward && (dr == -w.end || dx == -w.end)) {
    w.mismatch = true;
    if (dr == -w.end) w.holdR = BLK_POS | BLK_NEG;
    if (dx == -w.end) w.holdX = BLK_POS | BLK_NEG;
  }
  if (dr == w.end || w.holdR) railStopNow();
  if (dx == w.end || w.holdX) brakeNow(AX_X);
  railInhibit(railBlk());
}

void limRail(int s) {   // the interrupt, on every change
  RailSwitch& w = SW[s];
  unsettle(w.in);
  if (!digitalReadFast(w.pin)) return;
  if (w.open || w.tripping) {   // a bounce: only motion towards this end stops (it's blocked anyway)
    if (railDir() == w.end) railStopNow();
    if (w.effector && sgn(F[AX_X].v) == w.end) brakeNow(AX_X);
    return;
  }
  tripRail(s);
}

void isrX() { limFine(0); }
void isrY() { limFine(1); }
void isrZ() { limFine(2); }
void isrMin() { limRail(0); }
void isrMax() { limRail(1); }

const char* blkText(uint8_t b) {
  return b == 0 ? "0" : b == BLK_POS ? "+" : b == BLK_NEG ? "-" : "-+";
}

// The limit state clients mirror (api.md 6.5): the half-spaces of the axis its switches block, as confirmed (the
// effector's on fine x too), then `?` for a fine switch open at an end it can't tell; `0` for none.
void limitText(int a, char out[4]) {
  uint8_t b = 0;
  bool unknown = false;
  if (a == AX_R) {
    for (const RailSwitch& w : SW)
      if (w.open) b |= half(w.end);
  } else {
    if (F[a].lim == LIM_UNKNOWN) unknown = true;
    else if (F[a].lim) b = half(F[a].lim);
    if (a == AX_X)
      for (const RailSwitch& w : SW)
        if (w.effector && w.open) b |= half(w.end);
  }
  size_t n = 0;
  if (b & BLK_NEG) out[n++] = '-';
  if (b & BLK_POS) out[n++] = '+';
  if (unknown) out[n++] = '?';
  if (!n) out[n++] = '0';
  out[n] = '\0';
}

// Main loop: once a fine input has settled, a trip is confirmed (or taken back as a glitch: the stop stands, the
// block goes, and the jog ramps up again), and a switch closed again releases.
void pollFine(int a) {
  Fine& f = F[a];
  noInterrupts();
  if (settle(f.in)) {
    bool high = digitalReadFast(pin::LIM_FINE[a]);
    if (f.tripping) {
      f.tripping = false;
      if (high) f.lim = f.tripEnd ? f.tripEnd : LIM_UNKNOWN;
      else f.blkLim &= ~f.tripBlk;
    } else if (f.lim && !high) {
      f.lim = 0;
      f.blkLim = 0;
    } else if (!f.lim && high) {   // open, with no edge seen: trip now
      tripFine(a);
      unsettle(f.in);
    }
  }
  interrupts();
  if (f.evSense) {
    f.evSense = false;
    char c = AXIS[a];
    latch("fault %c sense IPROPI reads ~0 A while driving (open sense line?): %c is off until a restart", c, c);
  }
}

void railFreeze() {
  noInterrupts();
  railStopNow();
  railBlkFault = BLK_POS | BLK_NEG;
  railInhibit(railBlk());
  interrupts();
}

void pollRail(int s) {
  RailSwitch& w = SW[s];
  const char* name = w.end < 0 ? "MIN" : "MAX";
  noInterrupts();
  if (settle(w.in)) {
    bool high = digitalReadFast(w.pin);
    if (w.tripping) {
      w.tripping = false;
      w.evLost = w.lost;   // stopped without a ramp, glitch or not
      if (high) {
        w.open = true;
        w.stepsAt = railSteps();
        w.evMismatch = w.mismatch;
        railBlkFault |= w.holdR;   // a contradiction, confirmed: frozen until a restart
        F[AX_X].blkFault |= w.holdX;
      } else {   // a glitch: undo what the trip added (the stop itself stands; the jog ramps up again)
        railBlkLim &= ~w.tripBlkR;
        effBlkX &= ~w.tripBlkX;
      }
      w.holdR = w.holdX = 0;
      w.mismatch = w.lost = false;
      railInhibit(railBlk());
    } else if (w.open && !high) {
      w.open = false;
      railBlkLim &= ~half(w.end);
      if (w.effector) effBlkX &= ~half(w.end);
      railInhibit(railBlk());
    } else if (!w.open && high) {   // open, with no edge seen: trip now
      tripRail(s);
      unsettle(w.in);
    }
  }
  interrupts();
  // The release check: moving away from an open switch must close it within r.release.
  if (w.open && !railBlkFault) {
    float away = (railSteps() - w.stepsAt) * -w.end / railStepsPerUm(*P);
    if (away > P->r.release) {
      railFreeze();
      latch("fault r switches %s still open %.1f mm after the rail moved away from it: check r.sign and the MIN/MAX "
            "wiring; frozen until a restart", name, (double)(away / 1000));
    }
  }
}

// The state clients mirror, sent whenever it changes (api.md 6.5): each axis's limits, each fine axis's stall.
void publish() {
  for (int a = 0; a < AX_N; a++) {
    char t[4];
    limitText(a, t);
    if (!strcmp(t, sentLim[a])) continue;
    strcpy(sentLim[a], t);
    if (a == AX_R) netEvent("limit r %s pos=%.0f", t, (double)railPos());
    else netEvent("limit %c %s", AXIS[a], t);
  }
  for (int a = 0; a < 3; a++) {
    const char* t = blkText(F[a].blkStall);
    if (!strcmp(t, sentStall[a])) continue;
    strcpy(sentStall[a], t);
    netEvent("stall %c %s", AXIS[a], t);
  }
}
}  // namespace

void driveBegin(const Params& p) {
  P = &p;
  zeroJog();
  analogReadResolution(12);
  analogReadAveraging(1);
  for (int a = 0; a < 3; a++) {
    analogWriteFrequency(pin::IN1[a], p.fine.pwm_hz);   // x and y: one submodule per pair; z: two (24, 25)
    analogWriteFrequency(pin::IN2[a], p.fine.pwm_hz);
    outCoast(a);
    pinMode(pin::LIM_FINE[a], INPUT);                   // 2k pull-ups on the cable side; high = open (or a hit)
  }
  pinMode(pin::LIM_MIN, INPUT);
  pinMode(pin::LIM_MAX, INPUT);
  pinMode(pin::MOT_ON, INPUT);
  SW[0].pin = pin::LIM_MIN; SW[0].end = -1; SW[0].effector = (p.r.effector < 0);
  SW[1].pin = pin::LIM_MAX; SW[1].end = 1;  SW[1].effector = (p.r.effector > 0);
  // Inputs already open at power-up: a fine one's end is unknown; a rail switch's end is its own.
  for (int a = 0; a < 3; a++)
    if (digitalReadFast(pin::LIM_FINE[a])) F[a].lim = LIM_UNKNOWN;
  for (RailSwitch& w : SW) {
    if (!digitalReadFast(w.pin)) continue;
    w.open = true;
    railBlkLim |= half(w.end);
    if (w.effector) effBlkX |= half(w.end);
  }
  if (SW[0].open && SW[1].open) { railBlkFault = BLK_POS | BLK_NEG; faultLatched = true; }
  railInhibit(railBlk());
  for (int a = 0; a < AX_N; a++) limitText(a, sentLim[a]);   // as things stand: no session has heard otherwise
  for (int a = 0; a < 3; a++) strcpy(sentStall[a], "0");
  attachInterrupt(digitalPinToInterrupt(pin::LIM_X), isrX, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pin::LIM_Y), isrY, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pin::LIM_Z), isrZ, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pin::LIM_MIN), isrMin, CHANGE);
  attachInterrupt(digitalPinToInterrupt(pin::LIM_MAX), isrMax, CHANGE);
  NVIC_SET_PRIORITY(IRQ_GPIO6789, PRIO_TICK);
  tickTimer.priority(PRIO_TICK);
  tickTimer.begin(tick, 1000);
}

void drivePoll() {
  for (int a = 0; a < 3; a++) pollFine(a);
  for (int s = 0; s < 2; s++) pollRail(s);
  publish();   // the state first, then what it led to
  for (RailSwitch& w : SW) {
    if (w.evLost) netEvent("lost r limit");
    if (w.evMismatch)
      latch("fault r switches %s opened while moving away from it: check r.sign, x.sign, r.effector and the "
            "MIN/MAX wiring; frozen until a restart", w.end < 0 ? "MIN" : "MAX");
    w.evLost = w.evMismatch = false;
  }
  static bool bothReported = false;
  if (SW[0].open && SW[1].open && !bothReported) {
    bothReported = true;
    railFreeze();
    latch("fault r switches MIN and MAX both open (head unplugged, or a broken chain?); frozen until a restart");
  }
  if (evMotorsOff) {
    evMotorsOff = false;
    netEvent("motors off");
    if (evRailLost) { evRailLost = false; netEvent("lost r motors"); }
  }
  if (motors == Motors::WAIT && fineReady && railSettled()) {
    motors = Motors::ON;
    netEvent("motors on");
  }
  if (evLease) { evLease = false; netEvent("halt on cause=lease"); }
}

Jog driveJog(const float t[AX_N], uint8_t src) {
  bool any = t[0] != 0 || t[1] != 0 || t[2] != 0 || t[3] != 0;
  if (halted) {
    if (any) return Jog::HALTED;   // in flight before the halt, or the stick still held: ignored
    halted = false;                // all zero: the operator has let go
    netEvent("halt off");
  }
  if (any && motors != Motors::ON) {
    driveZeroJog();
    return motors == Motors::WAIT ? Jog::MOTORS_WAIT : Jog::MOTORS_OFF;
  }
  noInterrupts();
  for (int a = 0; a < AX_N; a++) jogT[a] = t[a];
  jogStamp = millis();
  interrupts();
  if (any) jogSrc = src;
  return Jog::OK;
}

bool driveLost(uint8_t src) {
  if (src == 0 || src != jogSrc) return false;   // someone else's jog is in force, or nobody's
  jogSrc = 0;
  if (jogT[0] == 0 && jogT[1] == 0 && jogT[2] == 0 && jogT[3] == 0) return false;   // its jog had ended already
  driveHalt();
  return true;
}

void driveHalt() {
  noInterrupts();   // one block: the tick and the limit interrupts can't run half way through it
  haltNow();
  interrupts();
}

bool driveHalted() { return halted; }

bool driveBusy() {
  for (int a = 0; a < 3; a++)
    if (F[a].out != COAST) return true;
  if (jogT[0] != 0 || jogT[1] != 0 || jogT[2] != 0 || jogT[3] != 0) return true;
  return railBusy();
}

void driveZeroJog() {
  noInterrupts();
  zeroJog();
  interrupts();
}

Motors driveMotors() { return motors; }
uint32_t driveTicks() { return ticks; }
bool driveFault() { return faultLatched || railFault(); }
uint8_t driveFaultCount() { return nFaults; }
const char* driveFaultLine(uint8_t i) { return i < nFaults ? faultLine[i] : ""; }

size_t driveLimitEvent(uint8_t a, char* b, size_t cap) {
  if (a >= AX_N) return 0;
  int n = a == AX_R ? snprintf(b, cap, "limit r %s pos=%.0f", sentLim[a], (double)railPos())
                    : snprintf(b, cap, "limit %c %s", AXIS[a], sentLim[a]);
  return n > 0 && (size_t)n < cap ? (size_t)n : 0;
}

size_t driveStallEvent(uint8_t a, char* b, size_t cap) {
  if (a >= 3 || !strcmp(sentStall[a], "0")) return 0;
  int n = snprintf(b, cap, "stall %c %s", AXIS[a], sentStall[a]);
  return n > 0 && (size_t)n < cap ? (size_t)n : 0;
}

size_t driveRecord(char* b, size_t cap) {
  size_t n = 0;
  const char* m = motors == Motors::ON ? "on" : motors == Motors::WAIT ? "wait" : "off";
  appendf(b, cap, &n, "t=%lu motors=%s halt=%d acq=off", (unsigned long)millis(), m, halted ? 1 : 0);
  for (int a = 0; a < 3; a++) {
    const Fine& f = F[a];
    char c = (char)('x' + a);
    const char* mode = !fineReady ? "off" : f.out == COAST ? "idle" : (f.out == BRAKE && jogT[a] == 0) ? "stop" : "jog";
    int cal = strcmp(P->ax[a].cal_source, "model") != 0 && P->ax[a].cal_valid[0] && P->ax[a].cal_valid[1];
    char lim[4];
    limitText(a, lim);
    appendf(b, cap, &n, " %c.mode=%s %c.v=%.1f %c.d=%.3f %c.lim=%s %c.blk=%s %c.i=%.3f %c.cal=%d", c, mode, c,
            (double)f.v, c, (double)f.duty, c, lim, c, blkText(fineBlk(a)), c, (double)f.iF, c, cal);
  }
  char lim[4];
  limitText(AX_R, lim);
  appendf(b, cap, &n, " r.mode=%s r.v=%.0f r.pos=%.0f r.homed=0 r.lim=%s r.blk=%s r.drv=%s", railMode(),
          (double)railSpeed(), (double)railPos(), lim, blkText(railBlk()), railDriver());
  return n;
}
