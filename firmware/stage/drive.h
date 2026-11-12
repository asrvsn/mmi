// The drive: the 1 kHz control tick, the fine axes (DRV8251A), the limit switches and the MOTORS switch.
// The rail's own mechanics (steps, TMC2209) are in rail.h; the tick drives it from here.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "motion.h"
#include "params.h"

enum class Motors : uint8_t { OFF, WAIT, ON };

void driveBegin(const Params& p);   // pins, PWM, ADC, the limit interrupts, the tick
void drivePoll();                   // main loop: limit confirmation and release, faults, events, MOTORS on

enum class Jog : uint8_t { OK, MOTORS_OFF, MOTORS_WAIT, HALTED };
// A jog vector in um/s (api.md 6.4) from client `src` (a session's id, non-zero). A non-zero vector is refused while
// MOTORS is off or not ready, or while a halt holds; an all-zero one is always taken, and releases a halt. A non-zero
// vector not renewed within jog.lease halts the drive, as the halt command does.
Jog driveJog(const float t[AX_N], uint8_t src);
// Client `src` has gone: closed, replaced, stopped reading, or its link went down. If the jog in force is its own,
// that's an operator lost mid-move: halt, exactly as the halt command (api.md 2). True if it halted.
bool driveLost(uint8_t src);
void driveZeroJog();                // ramped stop: a malformed jog
// halt: everything stops now, without ramps, and stays stopped until an all-zero jog (the operator has let go).
void driveHalt();
bool driveHalted();
bool driveBusy();                   // an axis moving, braking, enabling or powering down, or a target set (the LED)
Motors driveMotors();
uint32_t driveTicks();              // the tick's count, for the watchdog
bool driveFault();                  // a latched fault (status LED)
uint8_t driveFaultCount();          // latched faults, replayed after !hello
const char* driveFaultLine(uint8_t i);
size_t driveRecord(char* buf, size_t cap);   // the drive record, api.md 6.6 (no leading "!drive ")
// The state clients mirror (api.md 6.5), as its events read, without the "!": an axis's limits ("limit x -",
// "limit r + pos=1234"), or a fine axis's stall ("stall z +"; 0 when it has none). drivePoll sends each whenever it
// changes; net.cpp sends them all after every !hello, as last sent, so every session sees the same sequence.
size_t driveLimitEvent(uint8_t axis, char* buf, size_t cap);
size_t driveStallEvent(uint8_t axis, char* buf, size_t cap);
