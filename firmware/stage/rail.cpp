#include "rail.h"

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "board.h"
#include "motion.h"
#include "net.h"

namespace {
// TMC2209 registers (datasheet rev 1.08, section 5).
constexpr uint8_t R_GCONF = 0x00, R_GSTAT = 0x01, R_IFCNT = 0x02, R_IOIN = 0x06, R_IHOLD_IRUN = 0x10,
                  R_TPOWERDOWN = 0x11, R_TPWMTHRS = 0x13, R_TCOOLTHRS = 0x14, R_VACTUAL = 0x22, R_SGTHRS = 0x40,
                  R_COOLCONF = 0x42, R_MSCNT = 0x6A, R_CHOPCONF = 0x6C, R_PWMCONF = 0x70;
constexpr uint8_t PRIO_STEP = 16;          // above the tick and the limit switches
constexpr float MAX_PERIOD_US = 10000;     // the step interrupt runs at least every 10 ms, even when crawling
constexpr float RESTORE_PERIOD_US = 500;   // phase-restore pulses (driver off), 2 kHz
constexpr uint32_t TUNE_MS = 200;          // AT#1: standing still at IRUN, > 130 ms

// ---- shared with the step interrupt and the tick ----
volatile int32_t pos = 0;             // microsteps, api frame, counted since power-up
volatile int32_t phase = 0;           // the rotor's electrical phase, in MSCNT units (0-1023)
volatile float vS = 0;                // microsteps/s, api frame, as commanded by the tick
volatile uint8_t inhibit = 0;         // half-spaces the step interrupt refuses outright
volatile bool stepping = false;       // EN is low and steps are allowed
volatile bool parking = false;
volatile int32_t parkAt = 0;
volatile int32_t restoreLeft = 0;     // pulses still to send with EN high to bring MSCNT back to `phase`
bool dirHigh = false;
float crawl = 0;
float ticksPerUs = 0;                 // GPT2's rate, measured at start-up

// ---- from the parameters ----
int8_t sign = 1;
float spu = 3.2f, vmaxS = 0, accS = 0, decS = 0;   // microsteps per um; microsteps/s, /s^2
int32_t micro = 16, inc = 16;         // microsteps per full step; MSCNT counts per microstep (256 / micro)
int32_t detentPhase = 128;            // MSCNT mod 256 where the unpowered rotor rests
uint32_t powerdownMs = 1000, chopconf = 0, iholdIrunRun = 0, iholdIrunRest = 0;

// ---- the state machine (main loop owns st; the tick reads it) ----
enum St : uint8_t { S_OFF, S_CONFIG, S_IDLE, S_ENABLE, S_TUNE, S_ON, S_FAULT, S_BROKEN };
volatile St st = S_OFF;
volatile bool motorsOn = false, motorsChanged = false, wantEnable = false, powerdownDue = false;
volatile uint32_t restMs = 0;
bool settled = false, needTune = true;
const char* drv = "off";
const char* lastFail = "";
uint32_t retryAt = 0, nextHealth = 0, tuneAt = 0;
uint8_t cfgStep = 0, tuneStep = 0;
uint32_t mscntRead = 0;

// ---- step generation (GPT2, its own interrupt; IntervalTimers all share one) ----
void setDir(bool high) {
  if (high == dirHigh) return;
  digitalWriteFast(pin::DIR, high ? HIGH : LOW);
  dirHigh = high;
  delayNanoseconds(100);   // DIR settles before the edge (t_DSU 20 ns)
}

void pulse() {
  digitalWriteFast(pin::STEP, HIGH);
  delayNanoseconds(200);
  digitalWriteFast(pin::STEP, LOW);
}

void doStep(int8_t d) {   // a real step: EN is low, the rotor follows
  bool high = (d * sign) > 0;
  setDir(high);
  pulse();
  pos += d;
  phase = (phase + (high ? -inc : inc)) & 1023;   // "a low state on DIR increases the microstep counter" (1.3.1)
}

void stepIsr() {
  GPT2_SR = GPT_SR_OF1;
  float next = 1000 * ticksPerUs;   // idle: look again in 1 ms
  if (restoreLeft > 0 && !stepping) {
    // With EN high the bridges are off, but the sequencer still counts (datasheet 3.6.1, "Restart the stepper
    // motor without position loss"): bring MSCNT to the rotor's phase before the driver energises it.
    setDir(false);
    pulse();
    restoreLeft = restoreLeft - 1;
    next = RESTORE_PERIOD_US * ticksPerUs;
  } else {
    float v = vS;
    if (stepping && v != 0) {
      int8_t d = v > 0 ? 1 : -1;
      if (!(inhibit & (d > 0 ? BLK_POS : BLK_NEG)) && !(parking && pos == parkAt)) {
        float period = 1e6f * ticksPerUs / fabsf(v), cap = MAX_PERIOD_US * ticksPerUs;
        if (period <= cap) {
          doStep(d);
          crawl = 0;
          next = period;
        } else {   // slower than one step per 10 ms: count the time across visits
          crawl += cap;
          if (crawl >= period) { doStep(d); crawl -= period; }
          next = cap;
        }
      }
    }
  }
  uint32_t at = GPT2_OCR1 + (uint32_t)next, cnt = GPT2_CNT, guard = (uint32_t)(2 * ticksPerUs);
  if ((int32_t)(at - cnt) < (int32_t)guard) at = cnt + guard;   // never schedule into the past
  GPT2_OCR1 = at;
#if defined(__arm__)
  asm volatile("dsb" ::: "memory");   // the flag clear lands before the return, or the interrupt fires twice
#endif
}

void stepTimerBegin() {
  CCM_CCGR0 |= CCM_CCGR0_GPT2_BUS(CCM_CCGR_ON) | CCM_CCGR0_GPT2_SERIAL(CCM_CCGR_ON);
  GPT2_CR = 0;
  GPT2_PR = 0;
  GPT2_SR = 0x3F;
  GPT2_IR = 0;
  GPT2_CR = GPT_CR_CLKSRC(1) | GPT_CR_FRR | GPT_CR_ENMOD;   // the peripheral clock, free running
  GPT2_CR |= GPT_CR_EN;
  uint32_t c0 = GPT2_CNT, t0 = micros();
  while (micros() - t0 < 2000) {}
  ticksPerUs = (float)(GPT2_CNT - c0) / (float)(micros() - t0);   // 24 with Teensy's PERCLK; measured anyway
  if (ticksPerUs < 1) { st = S_BROKEN; drv = "timer"; return; }
  GPT2_OCR1 = GPT2_CNT + (uint32_t)(1000 * ticksPerUs);
  attachInterruptVector(IRQ_GPT2, stepIsr);
  NVIC_SET_PRIORITY(IRQ_GPT2, PRIO_STEP);
  GPT2_IR = GPT_IR_OF1IE;
  NVIC_ENABLE_IRQ(IRQ_GPT2);
}

// A microstep position (api frame) where the rotor rests unpowered: MSCNT = detentPhase (mod 256). The datasheet
// (3.6.1): "most motors lock at a fullstep position ... both coils at identical absolute current", MSCNT 128, 384,
// 640, 896. Called from the tick.
int32_t detentGrid() {
  noInterrupts();
  int32_t p = pos, ph = phase;
  interrupts();
  // A +api step drives DIR high when sign > 0, which counts MSCNT down by inc.
  int32_t diff = sign > 0 ? ph - detentPhase : detentPhase - ph;
  return p + (((diff % 256) + 256) % 256) / inc;
}

// ---- the TMC2209's single-wire UART: one transaction at a time, never waiting ----
struct Tx {
  uint8_t b[8], n, rx[16], got, want;
  bool busy, read;
  uint32_t t0, gapUntil;
} tx;

uint8_t crc8(const uint8_t* d, uint8_t n) {   // CRC8-ATM, LSB first (datasheet 4.2)
  uint8_t c = 0;
  for (uint8_t i = 0; i < n; i++) {
    uint8_t b = d[i];
    for (uint8_t j = 0; j < 8; j++) {
      c = ((c >> 7) ^ (b & 1)) ? (uint8_t)((c << 1) ^ 0x07) : (uint8_t)(c << 1);
      b >>= 1;
    }
  }
  return c;
}

bool txStart(uint8_t reg, bool write, uint32_t val) {
  if (tx.busy || (int32_t)(micros() - tx.gapUntil) < 0) return false;
  while (Serial1.available()) Serial1.read();
  tx.b[0] = 0x05;
  tx.b[1] = tmc::ADDR;
  if (write) {
    tx.b[2] = reg | 0x80;
    tx.b[3] = val >> 24; tx.b[4] = val >> 16; tx.b[5] = val >> 8; tx.b[6] = val;
    tx.b[7] = crc8(tx.b, 7);
    tx.n = 8;
    tx.want = 8;   // our own echo
  } else {
    tx.b[2] = reg;
    tx.b[3] = crc8(tx.b, 3);
    tx.n = 4;
    tx.want = 12;  // echo + the 8-byte reply
  }
  tx.read = !write;
  tx.got = 0;
  tx.busy = true;
  tx.t0 = micros();
  Serial1.write(tx.b, tx.n);
  return true;
}

// 0 = not finished, 1 = done, -1 = failed (lastFail says why)
int txPoll(uint32_t& val) {
  if (!tx.busy) return 0;
  while (Serial1.available() && tx.got < sizeof tx.rx) tx.rx[tx.got++] = Serial1.read();
  if (tx.got >= tx.want) {
    tx.busy = false;
    tx.gapUntil = micros() + 200;
    if (memcmp(tx.rx, tx.b, tx.n) != 0) { lastFail = "echo"; return -1; }
    if (tx.read) {
      const uint8_t* r = tx.rx + tx.n;
      if (r[0] != 0x05 || r[1] != 0xFF || r[2] != tx.b[2] || r[7] != crc8(r, 7)) { lastFail = "crc"; return -1; }
      val = ((uint32_t)r[3] << 24) | ((uint32_t)r[4] << 16) | ((uint32_t)r[5] << 8) | r[6];
    }
    return 1;
  }
  if (micros() - tx.t0 > (tx.read ? 5000u : 3000u)) {
    lastFail = (tx.got >= tx.n && memcmp(tx.rx, tx.b, tx.n) == 0) ? "noreply" : "echo";
    tx.busy = false;
    tx.gapUntil = micros() + 1000;   // >= 12 bit times of idle resets its receiver
    return -1;
  }
  return 0;
}

// ---- register sequences, each checked as it goes ----
enum Chk : uint8_t { C_NONE, C_VERSION, C_IFCNT0, C_EQ, C_IFCNT, C_GSTAT_CLEAR, C_HEALTH, C_MSCNT };
struct Op {
  uint8_t reg;
  bool write;
  uint32_t val;   // to write, or the value a C_EQ read must return
  Chk chk;
};
Op seq[24];
uint8_t seqN = 0, seqI = 0;
bool seqActive = false;
uint8_t ifcnt = 0;   // what IFCNT should read after our writes

void seqClear() { seqN = seqI = 0; seqActive = true; }
void op(uint8_t reg, bool write, uint32_t val, Chk c) { if (seqN < 24) seq[seqN++] = {reg, write, val, c}; }

// Before every enable (hardware.md rule 6): check it's there, write the whole configuration, read it back, count
// the writes on IFCNT, clear GSTAT, and read MSCNT. MUST: never internal_Rsense, never OTP_PROG (no write to 0x04).
void buildConfig() {
  seqClear();
  op(R_IOIN, false, 0, C_VERSION);
  op(R_IFCNT, false, 0, C_IFCNT0);
  op(R_GCONF, true, tmc::GCONF, C_NONE);
  op(R_CHOPCONF, true, chopconf, C_NONE);
  op(R_IHOLD_IRUN, true, needTune ? iholdIrunRun : iholdIrunRest, C_NONE);
  op(R_TPOWERDOWN, true, tmc::TPOWERDOWN, C_NONE);
  op(R_TPWMTHRS, true, 0, C_NONE);   // StealthChop at all speeds
  op(R_TCOOLTHRS, true, 0, C_NONE);
  op(R_SGTHRS, true, 0, C_NONE);     // StallGuard off until commissioning
  op(R_COOLCONF, true, 0, C_NONE);
  op(R_PWMCONF, true, tmc::PWMCONF, C_NONE);
  op(R_VACTUAL, true, 0, C_NONE);    // steps come from STEP
  op(R_GSTAT, true, 0x07, C_NONE);   // clear reset, drv_err, uv_cp
  op(R_GCONF, false, tmc::GCONF, C_EQ);
  op(R_CHOPCONF, false, chopconf, C_EQ);
  op(R_PWMCONF, false, tmc::PWMCONF, C_EQ);
  op(R_IFCNT, false, 0, C_IFCNT);
  op(R_GSTAT, false, 0, C_GSTAT_CLEAR);
  op(R_MSCNT, false, 0, C_MSCNT);
}

// While enabled, every 100 ms (rule 6): a brown-out on VS restores the defaults silently with EN still low.
void buildHealth() {
  seqClear();
  op(R_GSTAT, false, 0, C_HEALTH);
  op(R_IFCNT, false, 0, C_IFCNT);
}

int seqPoll() {   // 0 running (or nothing to do), 1 finished, -1 failed
  if (!seqActive) return 0;
  if (!tx.busy) {
    if (seqI >= seqN) { seqActive = false; return 1; }
    txStart(seq[seqI].reg, seq[seqI].write, seq[seqI].val);
    return 0;
  }
  uint32_t val = 0;
  int r = txPoll(val);
  if (r == 0) return 0;
  if (r < 0) { seqActive = false; return -1; }
  const Op& o = seq[seqI++];
  const char* bad = nullptr;
  if (o.write) ifcnt++;
  switch (o.chk) {
    case C_VERSION: if ((val >> 24) != tmc::VERSION) bad = "version"; break;
    case C_IFCNT0: ifcnt = val & 0xFF; break;
    case C_EQ: if (val != o.val) bad = "readback"; break;
    case C_IFCNT: if ((val & 0xFF) != ifcnt) bad = "ifcnt"; break;
    case C_GSTAT_CLEAR: if (val & 0x03) bad = "gstat"; break;
    case C_HEALTH:
      if (val & 0x01) bad = "reset";
      else if (val & 0x06) bad = "drv_err";
      break;
    case C_MSCNT: mscntRead = val & 1023; break;
    default: break;
  }
  if (bad) { lastFail = bad; seqActive = false; return -1; }
  return 0;
}

// ---- state transitions (main loop) ----
void enableDriver(bool steps) {   // EN low only while MOTORS is on (MUST, rule 11)
  noInterrupts();
  if (motorsOn && !motorsChanged) {
    digitalWriteFast(pin::EN, LOW);
    stepping = steps;
  }
  interrupts();
}

void disableDriver() {
  noInterrupts();
  stepping = false;
  vS = 0;
  parking = false;
  digitalWriteFast(pin::EN, HIGH);
  interrupts();
}

const char* reported = nullptr;   // the failure last announced: retries stay quiet until it changes or clears

void failed(bool wasRunning) {
  disableDriver();
  restoreLeft = 0;
  bool news = !reported || strcmp(reported, lastFail) != 0;
  reported = lastFail;
  st = S_FAULT;
  settled = true;
  needTune = true;
  drv = lastFail;
  retryAt = millis() + 2000;
  if (wasRunning) netEvent("lost r driver");
  if (news) netEvent("fault r driver %s: the rail is off, retrying every 2 s", lastFail);
}

// Configure, then put MSCNT back at the rotor's phase while the driver is still off. Returns 1 when done.
void beginConfig(St s) {
  buildConfig();
  cfgStep = 0;
  st = s;
}

int configPoll() {
  switch (cfgStep) {
    case 0: {
      int r = seqPoll();
      if (r < 0) return -1;
      if (r > 0) {
        restoreLeft = ((phase - (int32_t)mscntRead) & 1023) / inc;   // pulses with DIR low count MSCNT up
        cfgStep = 1;
      }
      return 0;
    }
    case 1:
      if (restoreLeft == 0) { seqClear(); op(R_MSCNT, false, 0, C_MSCNT); cfgStep = 2; }
      return 0;
    default: {
      int r = seqPoll();
      if (r < 0) return -1;
      if (r == 0) return 0;
      if ((int32_t)mscntRead != phase) {   // the sequencer didn't follow: take its phase, and say the first move may jump
        netEvent("warn r phase: MSCNT reads %lu, not %ld: the next enable may move the rail up to 2 full steps",
                 (unsigned long)mscntRead, (long)phase);
        phase = (int32_t)mscntRead;
      }
      reported = nullptr;
      return 1;
    }
  }
}

void tunePoll() {   // AT#1 before the first move after a driver reset; the phase is restored, so nothing moves
  uint32_t now = millis();
  switch (tuneStep) {
    case 0:
      enableDriver(false);
      tuneAt = now;
      tuneStep = 1;
      break;
    case 1:
      if (now - tuneAt >= TUNE_MS) { seqClear(); op(R_IHOLD_IRUN, true, iholdIrunRest, C_NONE); tuneStep = 2; }
      break;
    default: {
      int r = seqPoll();
      if (r < 0) { failed(false); return; }
      if (r > 0) {
        needTune = false;
        restMs = 0;
        powerdownDue = false;
        enableDriver(true);
        st = S_ON;
        nextHealth = now + 100;
      }
      break;
    }
  }
}
}  // namespace

void railBegin(const Params& p) {
  sign = p.r.sign;
  spu = railStepsPerUm(p);
  vmaxS = p.r.vmax * spu;
  accS = p.r.acc * spu;
  decS = p.r.dec * spu;
  micro = p.r.microsteps;
  inc = 256 / micro;
  detentPhase = p.r.detent_phase;
  phase = detentPhase;   // at power-up the rotor rests on a detent: aim the driver's first phase at one
  uint32_t mres = 0;
  for (uint16_t m = 256; m > p.r.microsteps; m >>= 1) mres++;
  chopconf = tmc::TOFF | ((uint32_t)tmc::HSTRT << 4) | ((uint32_t)tmc::HEND << 7) | ((uint32_t)tmc::TBL << 15) |
             (mres << 24) | (1u << 28);   // intpol: interpolate to 256 microsteps
  iholdIrunRun = (uint32_t)p.r.irun | ((uint32_t)p.r.irun << 8) | ((uint32_t)tmc::IHOLDDELAY << 16);
  iholdIrunRest = (uint32_t)tmc::IHOLD | ((uint32_t)p.r.irun << 8) | ((uint32_t)tmc::IHOLDDELAY << 16);
  float unit = 262144.0f / tmc::CLK_HZ * 1000.0f;   // ms per 2^18 clocks
  powerdownMs = (uint32_t)(tmc::TPOWERDOWN * unit + p.r.irun * tmc::IHOLDDELAY * unit) + 100;
  Serial1.begin(tmc::BAUD);
  stepTimerBegin();
}

void railPoll() {
  if (st == S_BROKEN) return;
  uint32_t now = millis();
  if (motorsChanged) {
    noInterrupts();
    bool on = motorsOn;
    motorsChanged = false;
    interrupts();
    seqActive = false;
    tx.busy = false;
    restoreLeft = 0;
    settled = false;
    if (on) { needTune = true; beginConfig(S_CONFIG); drv = "wait"; }   // UART only: nothing energises
    else { disableDriver(); st = S_OFF; drv = "off"; }
  }
  switch (st) {
    case S_CONFIG: {
      int r = configPoll();
      if (r > 0) { st = S_IDLE; settled = true; drv = "ok"; }
      else if (r < 0) failed(false);
      break;
    }
    case S_IDLE:
      if (wantEnable) { wantEnable = false; beginConfig(S_ENABLE); }
      break;
    case S_ENABLE: {
      int r = configPoll();
      if (r < 0) {
        failed(false);
      } else if (r > 0) {
        drv = "ok";
        if (needTune) { tuneStep = 0; st = S_TUNE; }   // the configuration just wrote IHOLD = IRUN for it
        else { restMs = 0; powerdownDue = false; enableDriver(true); st = S_ON; nextHealth = now + 100; }
      }
      break;
    }
    case S_TUNE:
      tunePoll();
      break;
    case S_ON: {
      if (powerdownDue && !seqActive) { disableDriver(); st = S_IDLE; break; }   // parked: driver off
      if (!seqActive && (int32_t)(now - nextHealth) >= 0) { buildHealth(); nextHealth = now + 100; }
      if (seqPoll() < 0) failed(true);
      break;
    }
    case S_FAULT:
      if (motorsOn && (int32_t)(now - retryAt) >= 0) beginConfig(S_CONFIG);
      break;
    default:
      break;
  }
}

// The tick, every 1 ms (interrupt context).
void railTick(float target, uint8_t blk) {
  uint8_t b = blk | inhibit;
  float t = fmaxf(-vmaxS, fminf(vmaxS, unblocked(target, b) * spu));
  if (st == S_IDLE) {
    if (t != 0) wantEnable = true;
    vS = 0;
    parking = false;
    return;
  }
  if (st != S_ON || !stepping) { vS = 0; parking = false; return; }
  float v = unblocked(vS, b);
  if (v == 0) parking = false;
  if (t != 0) {
    parking = false;
    v = rampStep(v, t, accS, decS, 0.001f);
    restMs = 0;
    powerdownDue = false;
  } else if (v != 0) {   // coming to rest: stretch the deceleration to end on the next detent ahead
    if (!parking) { parkAt = parkTarget(pos, v, decS, micro, detentGrid()); parking = true; }
    int32_t rem = (parkAt - pos) * (v > 0 ? 1 : -1);
    if (rem <= 0) {
      v = 0;
      parking = false;
    } else {
      float mag = fminf(fmaxf(fabsf(v), 100.0f), approachSpeed((float)rem, decS));
      v = v > 0 ? mag : -mag;
    }
    restMs = 0;
  } else if (++restMs >= powerdownMs) {
    powerdownDue = true;
  }
  vS = v;
}

void railStopNow() {
  vS = 0;
  parking = false;
}

void railInhibit(uint8_t blk) { inhibit = blk; }

void railMotors(bool on) {
  if (!on) {   // MUST (rule 11): STEP low and EN high at once
    stepping = false;
    vS = 0;
    parking = false;
    restoreLeft = 0;
    digitalWriteFast(pin::EN, HIGH);
    digitalWriteFast(pin::STEP, LOW);
  }
  motorsOn = on;
  motorsChanged = true;
}

int8_t railDir() { return vS > 0 ? 1 : (vS < 0 ? -1 : 0); }
float railSpeed() { return vS / spu; }
float railPos() { return pos / spu; }
int32_t railSteps() { return pos; }
int32_t railPhase() { return phase; }
bool railSettled() { return settled; }
bool railFault() { return st == S_FAULT || st == S_BROKEN; }
bool railBusy() { return st == S_ENABLE || st == S_TUNE || st == S_ON; }

const char* railMode() {
  switch (st) {
    case S_OFF: return "off";
    case S_CONFIG: case S_ENABLE: case S_TUNE: return "wait";
    case S_IDLE: return "idle";
    case S_ON: return (vS != 0 && !parking) ? "jog" : "stop";
    default: return "fault";
  }
}

const char* railDriver() { return drv; }
