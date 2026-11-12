// The simulated world behind the mocked Teensy: time, pins and interrupts, a TMC2209 on the UART (its MSCNT
// included), the three fine stages (INJS11 + MAX-B34C-13S, as the precomputed model), and the rail: its rotor snaps
// to the driver's phase when energised and settles on a detent when not, with the effector's switch (MIN, -x, the
// motor end) and the carriage's (MAX, +x, the bearing end). Frame: the operator's view, x right, y away, z up. And
// the lid: its two ADS1115 on Wire1 and five NTCs, SW10, and a board the Peltiers heat or cool.
#pragma once
#include <stdint.h>

#include <string>
#include <vector>

#include "params.h"

namespace sim {
// Physical world (um). Fine stages: 0 = mid travel, switches at +-6600, hard ends at +-7000.
extern double fine[3];
extern bool jam[3];          // a stage that won't turn: the motor stalls
extern int physSign[4];      // +1 = the motor turns the way the firmware's sign assumes; -1 = wired the other way
extern double railUm;        // the carriage, +x towards the bearing end
extern double railMinUm;     // where the effector (rail + fine x) trips MIN, at the motor end
extern double railMaxUm;     // where the carriage trips MAX, at the bearing end
extern int rotorPhase;       // the rotor's electrical position, MSCNT units; at rest on a detent (128 + 256k)
extern int snaps;            // times energising pulled the rotor to a different phase (a jump)
extern int relaxes;          // times de-energising let it settle onto a detent (a jump too)
extern int enLows;           // times EN went low (the driver energised)
extern bool headPlugged;     // false: every switch input reads open
extern bool motOn;           // SW11
// The switches (D2F, snap action): each opens at its trip point and closes again only diffUm back, and every change
// of its contacts bounces for 1.5 ms (bounce). glitch(): an input pulled high for a while, the switch itself shut.
extern double diffUm;
extern bool bounce;
void glitch(int pin, uint32_t us);

struct Tmc {
  bool powered = false, mute = false;
  uint32_t reg[128];
  uint8_t ifcnt = 0;
  int mscnt = 0;
  int forbidden = 0;         // writes to OTP_PROG, or GCONF.internal_Rsense set
  int writes = 0;
};
extern Tmc chip;
void tmcPowerUp();           // registers to their reset values, GSTAT.reset set
void tmcBrownOut();          // the same, mid-run (a VS dip)

// The lid: its two ADS1115 on Wire1, five NTCs (the board's three, then the probes on J2 and J3), SW10, and a board
// that the Peltiers heat or cool. A board sensor reads lidBoard + its offset, a probe lidMedium + its offset.
extern bool lidPlugged;      // false: neither ADC answers (the cable out)
extern bool lidSw;           // SW10 on (LID_SW high)
extern double lidBoard, lidMedium, lidRoom;   // C
extern double lidOffset[5];
extern char lidBroken[5];    // 'o' open, 's' shorted, 0 sound
extern double lidV33;        // the lid's 3.3 V
extern int lidHeatSign;      // +1: IN1 heats the board (the chamber side); -1: IN2 does (the modules the other way up)
extern int lidConversions;   // ADS1115 conversions started with the whole configuration written
extern double lidNoiseV;     // V rms of white noise on every NTC's voltage, a fresh draw each conversion (0: none)
extern double lidMainsV, lidMainsHz;   // V of mains pickup on every NTC (0: none), and its frequency

// Monitors of the MUST rules, counted as the simulation runs.
extern int violReversal;     // a bridge going forward <-> reverse without a brake between
extern int violMotorsOff;    // MOT_ON low > 2 ms with EN low, STEP high or an IN line high
extern int railStepsMoved;   // steps that reached the motor (EN low, powered)
extern int violLidBoth;      // the lid's two inputs high at once (brake, or drive/brake PWM)
extern int violLidSw;        // SW10 off > 2 ms with a lid input high or pulsing
extern int violLidAbsent;    // the lid unanswering > 250 ms with a lid input high or pulsing
extern int violLidWake;      // a lid input's PWM started without >= 5 us high just before (the wake pulse)
extern int violLidCfg;       // a conversion started without the whole configuration (single-shot, +-4.096 V)
float lidDrive();            // the drive the string sees now: + IN1's duty, - IN2's

extern std::vector<std::string> events;
bool saw(const char* prefix);   // an event starting with prefix has been sent
void clearEvents();

Params& config();                  // the parameters boot() will use (defaults until a test changes them)
void boot();                       // the firmware's setup(), in stage.ino's order
void run(uint32_t ms);             // advance time, running the step interrupt, the tick, the main loop and physics
float pwmHigh(int pin);            // -1: the pin isn't PWM
int level(int pin);                // 1 / 0 for a GPIO output, -1 if PWM
}  // namespace sim
