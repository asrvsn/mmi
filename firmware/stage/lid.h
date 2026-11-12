// The lid (../../hardware/lid/heating_pcb, ../hardware.md "Lid"; api.md 9.2): six Peltiers in series on a DRV8871,
// from pins 14 / 15 (IN1 / IN2, coast PWM at 20 kHz), and five NTCs on two ADS1115 on Wire1: three on the board, two
// probes hanging in the medium. Each NTC is sampled at 16 Hz, and read out once a second: the mean of its 16 samples
// and their SD. By hand for now: `lid duty=<%>`, capped at lid.duty_max; cut when a board NTC reaches 60 C, a medium
// probe lid.medium_max, a sensor reads wrong or the lid stops answering; dropped when the session that set it ends.
//
// The inline helpers are pure (host-tested in test/logic_test.cpp); lid.cpp drives the pins and the bus.
#pragma once
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "board.h"
#include "params.h"
#include "text.h"

// RT1 (T_A), RT2 (T_B), RT3 (T_EDGE) on the board; the probes on J2 (T_MED) and J3 (T_TOP in the schematic, a second
// medium probe since 2026-10-28). The board's three guard the board (and will hold it, the thermal loop's inner
// sensors); the probes guard the medium. A probe open from the lid's first readout is taken as not fitted; one that
// has read, and goes open, as come loose (a fault).
enum LidSensor : uint8_t { LID_A, LID_B, LID_EDGE, LID_J2, LID_J3, LID_NS };
constexpr uint8_t LID_BOARD_N = 3;
constexpr uint16_t LID_STEPS = 4096;   // 12-bit PWM: 0.024 % a step

// An NTC in its divider (10k 1 % from the lid's 3.3 V, the NTC to ground), read ratiometrically: its code, and the
// 3.3 V's at the same PGA. OPEN near the 3.3 V's code (no probe fitted, a broken lead), SHORT near 0; otherwise the
// temperature by the beta equation, 10k at 25 C.
enum class Ntc : uint8_t { OK, OPEN, SHORT };
inline Ntc ntcCelsius(int32_t code, int32_t code33, float beta, float& celsius) {
  if (code33 <= 0) return Ntc::SHORT;   // no reference: nothing reads
  float x = (float)code / (float)code33;
  if (x >= 0.97f) return Ntc::OPEN;     // over 323k: below -36 C
  if (x <= 0.03f) return Ntc::SHORT;    // under 309 R: above 150 C
  celsius = 1.0f / (1.0f / 298.15f + logf(x / (1.0f - x)) / beta) - 273.15f;
  return Ntc::OK;
}

// A duty's size (%) as the PWM's code: the nearest step; under the 2 % floor, 0; never past the cap.
inline uint16_t lidCode(float pct, float cap) {
  float a = fabsf(pct);
  if (!(a >= hw::LID_DUTY_FLOOR)) return 0;   // and NaN
  uint16_t top = (uint16_t)floorf(fminf(cap, hw::LID_DUTY_CEIL) * (LID_STEPS / 100.0f) + 1e-3f);
  uint16_t k = (uint16_t)lroundf(a * (LID_STEPS / 100.0f));
  return k < top ? k : top;
}
inline float lidLevel(uint16_t code) { return code * (100.0f / LID_STEPS); }

// One sensor's readout: the mean of a window's samples, and their SD about the window's straight line, so a board
// warming at 0.3 K/s reads as its noise, not as its rise (u: the sample's slot in the window). Doubles: Teensy 4.1's
// FPU has them, and a float's 7 digits would lose a few mK of SD under 25 C squared.
struct LidStat {
  uint8_t n = 0;
  double su = 0, suu = 0, sx = 0, sux = 0, sxx = 0;
  void add(float u, float x) {
    n++;
    su += u;
    suu += (double)u * u;
    sx += x;
    sux += (double)u * x;
    sxx += (double)x * x;
  }
  float mean() const { return n ? (float)(sx / n) : NAN; }
  float sd() const {   // 0 under 3 samples: a line through 2 fits them exactly
    if (n < 3) return 0;
    double um = su / n, xm = sx / n;
    double Suu = suu - n * um * um, Sux = sux - n * um * xm, Sxx = sxx - n * xm * xm;
    double ss = Suu > 0 ? Sxx - Sux * Sux / Suu : Sxx;
    return ss > 0 ? (float)sqrt(ss / (n - 2)) : 0.0f;
  }
};

// The lid command's arguments (api.md 9.2): `duty=<%>`, or `off`. On false, err is the error's code ("arg" or
// "range") and why its text; duty is then not to be used.
inline bool parseLid(char* const* tok, size_t ntok, float cap, float& duty, const char*& err, const char*& why) {
  err = "arg";
  why = "lid duty=<%> or lid off";
  if (ntok != 1) return false;
  if (!strcmp(tok[0], "off")) { duty = 0; return true; }
  if (strncmp(tok[0], "duty=", 5)) { why = "unknown key (duty=)"; return false; }
  if (!parseNumber(tok[0] + 5, duty)) { why = "not a number"; return false; }
  if (fabsf(duty) > cap) { err = "range"; why = "past lid.duty_max"; return false; }
  return true;
}

// The hardware (lid.cpp). analogWriteResolution(12) must already be set (stage.ino's setup()).
void lidBegin(const Params& p);   // Wire1 at 100 kHz, the PWM at 20 kHz, the bridge coasting
void lidPoll();                   // main loop: SW10, one bus transaction at most, the readout and its cuts, the bridge,
                                  // !lid
enum class LidSet : uint8_t { OK, ABSENT, FAULT, HOT, MEDIUM };
// A duty (%, signed, checked against the cap by the caller) from session `src` (non-zero). Any duty but 0 is refused
// while the lid is absent, a sensor reads wrong, or the board or the medium is at its limit; then `why` says which
// reading.
LidSet lidSet(float pct, uint8_t src, char* why, size_t cap);
// Session `src` has ended (closed, replaced, stuck, bye, the link down: `cause`). If the duty in force is its own,
// it's dropped, cut=<cause>. True if it was.
bool lidLost(uint8_t src, const char* cause);
float lidDuty();                       // in force, %, signed
size_t lidRecord(char* buf, size_t cap);   // the record (api.md 9.2), without "!lid ": the last readout, the state now
void lidPublish();                     // !lid to every session: each readout, and any change of sw, mode, duty or cut
