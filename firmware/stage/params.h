// The drive's parameters: compiled-in defaults (the precomputed model), overridden key by key from
// /parameters.yaml on the SD card (../parameters.md). Every value is checked against hard bounds; a value out of
// bounds keeps its default and is reported. No Arduino dependencies (host-testable).
#pragma once
#include <stddef.h>
#include <stdint.h>

constexpr uint8_t TABLE_MAX = 8;

// Speed (um/s) at each duty, both increasing; between points the firmware interpolates.
struct Table {
  uint8_t n, n_speed;   // the two lists arrive as separate keys; paramsFinish checks they match
  float duty[TABLE_MAX];
  float speed[TABLE_MAX];
};

struct FineAxis {
  int8_t sign;          // +1 / -1: the motor direction that moves the stage towards + (api.md 6.2)
  float vmax;           // um/s
  float acc, dec;       // um/s^2
  float duty_max;       // cap, <= 0.92
  float duty_min;       // below this, pulse mode (not built yet: the axis doesn't move); 0 = never
  float r_m;            // ohm, the winding (the duty-aware stall test)
  Table model;          // precomputed, both directions
  Table cal[2];         // in use: [0] towards +, [1] towards -. Starts as a copy of the model
  char cal_source[24];  // "model" until a calibration writes it
  bool cal_valid[2];    // false: that direction runs on the model
};

struct Params {
  char unit[24];
  struct {
    float pwm_hz;
    float stall_i, stall_k, stall_floor;   // A, -, A
    uint16_t stall_ms, inrush_ms;
    float vunk;                            // um/s while a switch is open with its end unknown
    float cal_ratio;                       // a calibration must stay within x/÷ this of the model
  } fine;
  FineAxis ax[3];       // x, y, z
  struct {
    int8_t sign;
    float vmax, acc, dec;                  // um/s, um/s^2
    float lead;                            // um per motor turn
    uint16_t steps;                        // full steps per turn
    uint16_t microsteps;                   // 1, 2, 4 ... 256
    uint8_t irun;                          // 0-28: (irun+1)/32 x 0.657 A rms
    uint16_t detent_phase;                 // MSCNT (mod 256) where the unpowered rotor rests: 128 (datasheet) or 0
    int8_t effector;                       // the switch on the effector: -1 = MIN (J7, the motor end), +1 = MAX (J8)
    float release;                         // um a switch may stay open while the rail moves away from it
  } r;
  struct {
    uint16_t lease;                        // ms
  } jog;
  struct {
    float duty_max;                        // %, the cap on lid duty= either way, <= 80
    float medium_max;                      // C: a medium probe at this cuts the duty, 10-50
    int8_t sign;                           // +1: IN1 heats the chamber side; -1: IN2 does
  } lid;
};

// Messages for the client about the parameter file (replayed after !hello as "!cfg ..." lines).
struct Report {
  static constexpr uint8_t MAX = 16;
  char line[MAX][96];
  uint8_t n;
  uint8_t lost;          // messages that didn't fit
  void add(const char* fmt, ...);
};

// Every parameter as text, in the file's order: the values `get` sends and paramsApply reads back (numbers as
// plain decimals, lists joined by commas, `r.effector` as min / max).
typedef void (*ParamSink)(void* ctx, const char* key, const char* value);
void paramsList(const Params& p, ParamSink sink, void* ctx);
bool paramsGet(const Params& p, const char* key, char* value, size_t cap);   // false: no such key

void paramsDefaults(Params& p);
void paramsApply(Params& p, const char* key, const char* value, int line, Report& rep);
void paramsFinish(Params& p, Report& rep);   // tables: shape, monotonic, calibration vs the model

// Speed (um/s) to duty through a table, for speed >= 0; clamps to the table's ends.
float tableDuty(const Table& t, float speed);
float tableSpeed(const Table& t, float duty);
// Microsteps per um on the rail.
inline float railStepsPerUm(const Params& p) { return p.r.steps * (float)p.r.microsteps / p.r.lead; }
