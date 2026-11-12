// Motion arithmetic: velocity ramps, the rail's park target, and the jog command's arguments.
// No Arduino dependencies (host-testable).
#pragma once
#include <stddef.h>
#include <stdint.h>

enum Axis : uint8_t { AX_X = 0, AX_Y = 1, AX_Z = 2, AX_R = 3, AX_N = 4 };
constexpr uint8_t BLK_POS = 1, BLK_NEG = 2;   // blocked half-spaces (api.md 6.5)

// One control tick of a velocity ramp, from v towards target: at acc while |v| grows, at dec while it shrinks.
// A reversal slows to zero at dec, holds zero for that tick, then speeds up at acc.
float rampStep(float v, float target, float acc, float dec, float dt);

// Clears whatever a block forbids: a target into a blocked half-space becomes 0.
inline float unblocked(float t, uint8_t blk) {
  if ((t > 0 && (blk & BLK_POS)) || (t < 0 && (blk & BLK_NEG))) return 0;
  return t;
}

// The rail's park target (hardware.md, "Parking rule"): the first detent at or beyond the point where a stop at
// dec would end, ahead in the direction of travel, never behind. pos and the result in microsteps, v in
// microsteps/s, dec in microsteps/s^2; detents sit every `grid` microsteps, `offset` from 0.
int32_t parkTarget(int32_t pos, float v, float dec, int32_t grid, int32_t offset);

// The fastest speed (microsteps/s) that still stops within `remaining` microsteps at dec.
float approachSpeed(float remaining, float dec);

// The jog command's arguments ("x=40", "r=-1500", ...) into four targets in um/s, axes left out at 0.
// Strict: unknown keys, repeats and malformed numbers fail with a reason.
bool parseJog(char* const* tok, size_t ntok, float out[AX_N], const char*& why);
