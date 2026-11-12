// The drive, rail, light and lid code itself (drive.cpp, rail.cpp, light.cpp, lid.cpp, unmodified) against the
// simulated Teensy, TMC2209, stages and lid in sim/. Each scenario runs in its own process, so the firmware starts
// from a fresh boot every time.
// SIM_VERBOSE=1 ./build/sim_test prints every event as it happens.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <string>

#include "board.h"
#include "drive.h"
#include "lid.h"
#include "light.h"
#include "params.h"
#include "rail.h"
#include "teensy_sim.h"

using namespace sim;

static int fails = 0;
#define CHECK(c)                                                       \
  do {                                                                 \
    if (!(c)) { fails++; printf("    FAIL line %d: %s\n", __LINE__, #c); } \
  } while (0)

// A client (session id src): re-sends the same vector every 50 ms (api.md 8), for ms.
static Jog jog(float x, float y, float z, float r, uint32_t ms, uint8_t src = 1) {
  float t[AX_N] = {x, y, z, r};
  Jog last = Jog::OK;
  for (uint32_t done = 0; done < ms; done += 50) {
    last = driveJog(t, src);
    run(std::min<uint32_t>(50, ms - done));
  }
  return last;
}

// The client lets go: all-zero jogs, a ramped stop (the rail parks on a detent). A move not ended this way would end
// in a halt when its lease ran out.
static void letGo(uint32_t ms) { jog(0, 0, 0, 0, ms); }

static void motorsOn() {
  motOn = true;
  run(500);   // debounce 50 ms, the head's 9 V, the TMC2209 configured and its phase restored (UART only)
}

// The limit, stall, lost and fault events since clearEvents(), in order, joined by " | ", without the rail's pos=.
static std::string limitEvents() {
  std::string out;
  for (const std::string& e : events) {
    if (e.compare(0, 6, "limit ") && e.compare(0, 6, "stall ") && e.compare(0, 5, "lost ") && e.compare(0, 6, "fault "))
      continue;
    std::string t = e.substr(0, e.find(" pos="));
    out += (out.empty() ? "" : " | ") + t;
  }
  return out;
}

static void clean() {   // every scenario ends with no rule broken
  CHECK(violReversal == 0);
  CHECK(violMotorsOff == 0);
  CHECK(chip.forbidden == 0);
  CHECK(violLidBoth == 0);
  CHECK(violLidSw == 0);
  CHECK(violLidAbsent == 0);
  CHECK(violLidWake == 0);
  CHECK(violLidCfg == 0);
}

// The lid's record now, and one value from it (NAN if it isn't there, or isn't a number).
static std::string lidRec() {
  char b[400];
  lidRecord(b, sizeof b);
  return b;
}
static double lidVal(const char* key) {
  std::string r = " " + lidRec(), k = std::string(" ") + key + "=";
  size_t i = r.find(k);
  if (i == std::string::npos) return NAN;
  char* end;
  double v = strtod(r.c_str() + i + k.size(), &end);
  return end == r.c_str() + i + k.size() ? NAN : v;
}
static bool lidHas(const char* text) { return lidRec().find(text) != std::string::npos; }

// The !lid events since clearEvents(), as their state (sw mode duty cut), joined by " | ": each state once, as the
// readouts in between repeat it.
static std::string lidStates() {
  std::string out, last;
  for (const std::string& e : events) {
    if (e.compare(0, 4, "lid ")) continue;
    std::string t;
    for (const char* k : {"sw=", "mode=", "duty=", "cut="}) {
      size_t i = e.find(std::string(" ") + k);
      t += (t.empty() ? "" : " ") + e.substr(i + 1, e.find(' ', i + 1) - i - 1);
    }
    if (t != last) out += (out.empty() ? "" : " | ") + t;
    last = t;
  }
  return out;
}

static int lidEvents() {
  int n = 0;
  for (const std::string& e : events) n += !e.compare(0, 4, "lid ");
  return n;
}

static void lidReady() {   // booted, the first readout made (a second's 16 samples) and SW10 seen on
  boot();
  run(1100);
  clearEvents();
}

// Until a readout whose window began after now (the one under way may hold samples from before): within 2 s.
static void readout() { run(2000); }

static char why[64];
static LidSet lid(float pct, uint8_t src = 1) {
  why[0] = 0;
  return lidSet(pct, src, why, sizeof why);
}

static void bootMotorsOff() {
  boot();
  run(200);
  float t[AX_N] = {50, 0, 0, 0};
  CHECK(driveMotors() == Motors::OFF);
  CHECK(driveJog(t, 1) == Jog::MOTORS_OFF);
  run(100);
  CHECK(fine[0] == 0);
  CHECK(level(pin::EN) == 1);
  CHECK(level(pin::IN1[0]) == 0 && level(pin::IN2[0]) == 0);
  CHECK(!driveBusy());
  clean();
}

static void motorsOnDoesNothing() {
  boot();
  motorsOn();
  run(2000);
  CHECK(saw("motors on"));
  CHECK(chip.reg[0x00] == 0x1C0);                       // GCONF: pdn_disable, mstep_reg_select, multistep_filt
  CHECK(chip.reg[0x6C] == 0x14008054u);                 // CHOPCONF: toff 4, hstrt 5, tbl 1, 1/16, intpol
  CHECK(chip.reg[0x10] == (18u | (18u << 8) | (2u << 16)));   // IHOLD = IRUN, ready for AT#1 at the first move
  CHECK(chip.reg[0x11] == 5 && chip.reg[0x70] == 0xC10D0E24u);
  CHECK((chip.reg[0x01] & 3) == 0);                     // GSTAT cleared
  CHECK(chip.mscnt == 128);                             // the sequencer put on the rotor's phase, driver still off
  CHECK(enLows == 0 && snaps == 0);                     // nothing energised: the drive waits to be told
  CHECK(level(pin::EN) == 1 && !strcmp(railMode(), "idle") && !strcmp(railDriver(), "ok"));
  CHECK(!driveBusy());
  clean();
}

static void jogFineRampsAndRests() {
  boot();
  motorsOn();
  clearEvents();
  CHECK(jog(100, 0, 0, 0, 500) == Jog::OK);
  CHECK(fine[0] > 40 && fine[0] < 52);                 // 100 um/s after a 50 ms ramp: ~47 um
  CHECK(level(pin::IN1[0]) == 1);                      // forward: IN1 high, IN2 PWM braking 1 - duty of the time
  float duty = 0.038f + 100.0f / 289.0f * (0.92f - 0.038f);
  CHECK(fabsf(pwmHigh(pin::IN2[0]) - (1 - duty)) < 0.002f);
  CHECK(driveBusy());
  letGo(300);                                          // ramps down, then brakes
  run(200);
  CHECK(level(pin::IN1[0]) == 0 && level(pin::IN2[0]) == 0);   // braked 100 ms, then coasting (driver asleep)
  CHECK(!driveHalted());
  CHECK(!driveBusy());
  double at = fine[0];
  run(500);
  CHECK(fine[0] == at);
  clean();
}

static void reversalThroughBrake() {
  boot();
  motorsOn();
  jog(150, 0, 0, 0, 300);
  double a = fine[0];
  jog(-150, 0, 0, 0, 300);
  CHECK(fine[0] < a);
  jog(0, 200, -200, 0, 300);
  jog(0, -200, 200, 0, 300);
  clean();   // no forward <-> reverse without a brake
}

static void fineLimitBlocksHalfSpace() {
  boot();
  fine[0] = 6500;
  motorsOn();
  clearEvents();
  jog(150, 0, 0, 0, 1500);                             // runs into the + switch at 6600, whose contacts bounce
  CHECK(fine[0] >= 6600 && fine[0] < 6602);            // stopped at once, at the first edge
  CHECK(limitEvents() == "limit x +");                 // the end it was heading for, once the input settled
  double p = fine[0];
  jog(150, 0, 0, 0, 400);
  CHECK(fine[0] == p);                                 // +x ignored
  jog(-100, 0, 0, 0, 800);                             // away: the switch closes 20 um back, bouncing as it does
  CHECK(fine[0] < 6580 && limitEvents() == "limit x + | limit x 0");   // released once settled; never a "-"
  double q = fine[0];
  jog(50, 0, 0, 0, 200);
  CHECK(fine[0] > q);                                  // + allowed again
  char ev[64];
  CHECK(driveLimitEvent(AX_X, ev, sizeof ev) && !strcmp(ev, "limit x 0"));   // what a client connecting now gets
  clean();
}

static void stallStopsAndBlocks() {
  boot();
  motorsOn();
  clearEvents();
  jam[2] = true;
  jog(0, 0, 150, 0, 120);
  CHECK(saw("stall z +"));                             // within 0.1 s of starting against the jam
  char ev[64];
  CHECK(driveStallEvent(AX_Z, ev, sizeof ev) && !strcmp(ev, "stall z +"));
  jam[2] = false;
  double p = fine[2];
  jog(0, 0, 150, 0, 300);
  CHECK(fine[2] == p);                                 // + stays blocked
  jog(0, 0, -100, 0, 300);
  CHECK(fine[2] < p);
  CHECK(limitEvents() == "stall z + | stall z 0");     // backed off for 50 ms: lifted, and said so
  CHECK(driveStallEvent(AX_Z, ev, sizeof ev) == 0);    // nothing for a client connecting now
  double q = fine[2];
  jog(0, 0, 100, 0, 300);
  CHECK(fine[2] > q);                                  // having backed off, + is allowed again
  clean();
}

static void railJogParksAndPowersDown() {
  boot();
  motorsOn();
  double start = railUm;
  jog(0, 0, 0, 2000, 1200);                            // the first move waits ~0.2 s for AT#1, then runs
  CHECK(level(pin::EN) == 0);                          // enabled while moving
  CHECK(railUm - start > 1600 && railUm - start < 2000);
  letGo(400);                                          // decelerate, park on a detent
  CHECK(((railPhase() - 128) % 256 + 256) % 256 == 0); // stopped where the rotor rests unpowered
  CHECK(fabs((railUm - start) - railPos()) < 1e-6);    // every counted step reached the motor, nothing jumped
  run(1500);
  CHECK(level(pin::EN) == 1 && !strcmp(railMode(), "idle"));   // current ramped to zero, then the driver off
  CHECK(snaps == 0 && relaxes == 0);
  jog(0, 0, 0, -1000, 300);                            // and again, the other way (no AT#1 this time)
  letGo(1500);
  CHECK(level(pin::EN) == 1 && relaxes == 0 && snaps == 0);
  CHECK(fabs((railUm - start) - railPos()) < 1e-6);
  CHECK(!driveBusy());
  clean();
}

static void motorsCycleNoJump() {
  boot();
  motorsOn();
  double start = railUm;
  jog(0, 0, 0, 1500, 800);
  letGo(2000);                                         // parked, driver off
  motOn = false;                                       // MOTORS off and on: the TMC2209 forgets its phase
  run(300);
  motorsOn();
  jog(0, 0, 0, -1500, 800);
  letGo(2000);
  CHECK(snaps == 0 && relaxes == 0);                   // its phase was put back before it energised
  CHECK(fabs((railUm - start) - railPos()) < 1e-6);
  clean();
}

static void firstBootSnapsOnce() {
  rotorPhase = 384;                                    // resting a full step away from where the driver starts
  boot();
  motorsOn();
  jog(0, 0, 0, 1000, 600);
  CHECK(snaps == 1);                                   // the first energising after power-up may pull it once
  letGo(2000);
  motOn = false;
  run(300);
  motorsOn();
  jog(0, 0, 0, 1000, 600);
  letGo(2000);
  CHECK(snaps == 1);                                   // never again
  clean();
}

static void effectorSwitchBlocksBoth() {
  boot();
  railUm = 300;                                        // the effector 0.3 mm from the -x (motor) end
  motorsOn();
  clearEvents();
  jog(0, 0, 0, -1000, 800);
  CHECK(limitEvents() == "limit x - | limit r - | lost r limit");   // fine x's -x is blocked too, and said so
  double r = railUm, x = fine[0];
  jog(0, 0, 0, -1000, 300);
  CHECK(railUm == r);                                  // -x on the rail ignored
  jog(-50, 0, 0, 0, 300);
  CHECK(fine[0] == x);                                 // -x on the fine stage ignored too: it moves the effector
  jog(50, 0, 0, 0, 800);                               // +x: the effector backs off its switch (20 um), bouncing
  CHECK(fine[0] > x + 20);
  CHECK(limitEvents() == "limit x - | limit r - | lost r limit | limit x 0 | limit r 0");
  double x2 = fine[0];
  jog(50, 0, 0, 0, 200);
  CHECK(fine[0] > x2);                                 // backing off isn't a trip: fine x isn't frozen
  jog(0, 0, 0, 1000, 300);
  CHECK(railUm > r);
  jog(0, 0, 0, -1000, 100);
  CHECK(!saw("fault"));
  clean();
}

static void carriageSwitchRailOnly() {
  boot();
  railUm = railMaxUm - 300;                            // the carriage 0.3 mm from the +x (bearing) end
  motorsOn();
  clearEvents();
  jog(0, 0, 0, 1000, 800);
  CHECK(limitEvents() == "limit r + | lost r limit");  // the rail only
  double r = railUm, x = fine[0];
  jog(0, 0, 0, 1000, 300);
  CHECK(railUm == r);                                  // +x on the rail ignored
  jog(50, 0, 0, 0, 300);
  CHECK(fine[0] > x);                                  // fine x is free: this switch is on the carriage
  jog(0, 0, 0, -1000, 300);                            // away: the switch closes 20 um back, bouncing
  CHECK(railUm < r - 20 && limitEvents() == "limit r + | lost r limit | limit r 0");
  double r2 = railUm;
  jog(0, 0, 0, -1000, 200);
  CHECK(railUm < r2);                                  // backing off isn't a trip: the rail isn't frozen
  jog(0, 0, 0, 1000, 100);
  CHECK(railUm > r2 - 400 && !saw("fault"));           // and +x runs again
  clean();
}

// x on its own + end, and then the effector on MIN: x can go neither way, the rail only +x.
static void fineXBlockedBothWays() {
  boot();
  fine[0] = 6500;
  railUm = -6300;                                      // the effector (rail + fine x) 0.2 mm from MIN
  motorsOn();
  clearEvents();
  jog(150, 0, 0, 0, 1000);
  CHECK(limitEvents() == "limit x +");                 // its own switch, at +
  jog(0, 0, 0, -1000, 600);
  CHECK(limitEvents() == "limit x + | limit x -+ | limit r - | lost r limit");
  double x = fine[0], r = railUm;
  jog(100, 0, 0, 0, 200);
  jog(-100, 0, 0, 0, 200);
  CHECK(fine[0] == x);                                 // neither way
  jog(0, 0, 0, 1000, 400);                             // the rail +x backs the effector off
  CHECK(railUm > r && limitEvents() == "limit x + | limit x -+ | limit r - | lost r limit | limit x + | limit r 0");
  clean();
}

// Noise on an input: a stop, then the jog carries on, and nothing is reported. Not even on MIN while the rail runs
// away from it (a real trip then would be a contradiction, and freeze the rail).
static void glitchesStopThenCarryOn() {
  boot();
  motorsOn();
  clearEvents();
  jog(0, 100, 0, 2000, 600);                           // y and the rail moving, nowhere near an end
  double y = fine[1], r = railUm;
  glitch(pin::LIM_Y, 1000);
  glitch(pin::LIM_MIN, 1000);
  jog(0, 100, 0, 2000, 600);
  CHECK(fine[1] > y + 30 && railUm > r + 600);         // each stopped, then carried on
  CHECK(limitEvents() == "");
  clean();
}

static void wrongRailSignFreezes() {
  physSign[3] = -1;                                    // the motor turns the other way to what r.sign says
  boot();
  railUm = 200;
  motorsOn();
  clearEvents();
  jog(0, 0, 0, 1000, 800);                             // "+x" actually runs the effector into MIN
  CHECK(saw("fault r switches MIN"));
  double r = railUm;
  jog(0, 0, 0, -1000, 300);
  jog(0, 0, 0, 1000, 300);
  CHECK(railUm == r);                                  // frozen both ways until a restart
  clean();
}

static void haltStopsNowAndLatches() {
  boot();
  motorsOn();
  clearEvents();
  jog(150, 0, 0, 2000, 600);
  int steps = railStepsMoved;
  CHECK(steps > 0);
  driveHalt();
  CHECK(level(pin::IN1[0]) == 1 && level(pin::IN2[0]) == 1);   // braking at once, inside the same call
  run(5);
  double x = fine[0], r = railUm;
  steps = railStepsMoved;
  run(100);
  CHECK(railStepsMoved == steps && fine[0] == x);      // nothing more: no ramp, no parking
  CHECK(driveHalted());
  CHECK(jog(150, 0, 0, 2000, 300) == Jog::HALTED);     // the stick still held, or jogs already in flight: ignored
  CHECK(fine[0] == x && railUm == r);
  CHECK(jog(0, 0, 0, 0, 100) == Jog::OK && !driveHalted() && saw("halt off"));   // let go: released
  CHECK(jog(-100, 0, 0, 0, 300) == Jog::OK);
  CHECK(fine[0] < x);
  clean();
}

static void lostClientHalts() {
  boot();
  motorsOn();
  clearEvents();
  jog(150, 0, 0, 2000, 600, 1);                        // client 1 (TCP) driving
  CHECK(!driveLost(2) && !driveHalted());              // another session closing: not its jog, nothing happens
  CHECK(driveLost(1));                                 // the driving client gone: a halt
  CHECK(level(pin::IN1[0]) == 1 && level(pin::IN2[0]) == 1);   // braking at once, inside the same call
  run(5);
  double x = fine[0], r = railUm;
  int steps = railStepsMoved;
  run(100);
  CHECK(railStepsMoved == steps && fine[0] == x && driveHalted());
  CHECK(jog(150, 0, 0, 2000, 300, 1) == Jog::HALTED);  // the client back with the stick still held: ignored
  CHECK(fine[0] == x && railUm == r);
  CHECK(jog(0, 0, 0, 0, 100, 1) == Jog::OK && !driveHalted());   // let go: released
  jog(0, 80, 0, 0, 200, 2);                            // client 2 (USB) driving now, then letting go
  jog(0, 0, 0, 0, 100, 2);
  CHECK(!driveLost(2) && !driveHalted());              // nothing in force: nothing to halt
  CHECK(!driveLost(1));
  clean();
}

static void leaseRunsOutHalts() {
  boot();
  motorsOn();
  clearEvents();
  jog(150, 0, 0, 2000, 600);                           // then nothing more: the client hung, its link still up
  int steps = railStepsMoved;
  run(190);                                            // 240 ms since the last jog: inside the lease, still moving
  CHECK(!driveHalted() && railStepsMoved > steps);
  run(20);                                             // past 250 ms: a halt
  CHECK(driveHalted() && saw("halt on cause=lease"));
  CHECK(level(pin::IN1[0]) == 1 && level(pin::IN2[0]) == 1);   // braking
  run(5);
  double x = fine[0], r = railUm;
  steps = railStepsMoved;
  run(100);
  CHECK(railStepsMoved == steps && fine[0] == x);      // stopped as a halt stops: no ramp, no parking
  CHECK(jog(150, 0, 0, 2000, 300) == Jog::HALTED);     // the client back with the stick still held: ignored
  CHECK(fine[0] == x && railUm == r);
  CHECK(jog(0, 0, 0, 0, 100) == Jog::OK && !driveHalted() && saw("halt off"));   // let go: released
  CHECK(jog(-100, 0, 0, 0, 300) == Jog::OK);
  CHECK(fine[0] < x);
  letGo(300);
  CHECK(!driveHalted());                               // a move ended by letting go: no halt
  clean();
}

static void motorsOffMidMove() {
  boot();
  motorsOn();
  jog(50, 0, 0, 2000, 600);
  float t[AX_N] = {50, 0, 0, 2000};
  driveJog(t, 1);
  motOn = false;
  run(20);
  CHECK(saw("motors off") && saw("lost r motors"));
  run(50);
  CHECK(driveJog(t, 1) == Jog::MOTORS_OFF);
  CHECK(level(pin::EN) == 1);
  clean();
}

static void driverBrownOut() {
  boot();
  motorsOn();
  jog(0, 0, 0, 2000, 500);
  tmcBrownOut();                                       // a VS dip: the chip resets with EN still low
  jog(0, 0, 0, 2000, 200);
  CHECK(saw("fault r driver reset") && saw("lost r driver"));
  CHECK(level(pin::EN) == 1);
  letGo(4000);                                         // retries after 2 s: configures, restores its phase
  CHECK(!strcmp(railDriver(), "ok"));
  double r = railUm;
  jog(0, 0, 0, 1000, 600);
  CHECK(railUm > r);
  clean();
}

static void headUnplugged() {
  headPlugged = false;                                 // every switch reads open
  boot();
  run(50);
  CHECK(saw("fault r switches MIN and MAX both open"));
  char ev[64];
  CHECK(driveLimitEvent(AX_X, ev, sizeof ev) && !strcmp(ev, "limit x -?"));   // the effector's -, its own unknown
  CHECK(driveLimitEvent(AX_Y, ev, sizeof ev) && !strcmp(ev, "limit y ?"));
  CHECK(driveLimitEvent(AX_R, ev, sizeof ev) && !strcmp(ev, "limit r -+ pos=0"));
  motorsOn();
  int before = railStepsMoved;
  jog(-100, 100, 0, 1000, 500);
  CHECK(railStepsMoved == before);                     // the rail doesn't move
  CHECK(fine[0] == 0);                                 // the effector's switch reads open too: -x blocked
  CHECK(fine[1] > 0 && fine[1] <= 20 * 0.5 + 0.1);     // fine y only at vunk: which end it's at is unknown
  clean();
}

static void driverSilent() {
  chip.mute = true;
  boot();
  motorsOn();
  CHECK(saw("fault r driver noreply"));
  CHECK(saw("motors on"));                             // the fine axes run regardless
  jog(0, 80, 0, 500, 300);
  CHECK(fine[1] > 0);
  CHECK(railStepsMoved == 0);
  clean();
}

// The lights: dark at boot; a level as the 12-bit PWM's duty on its own pin; held through whatever the drive does.
static void lightsByHand() {
  boot();
  CHECK(pwmHigh(pin::DIM_W) == 0 && pwmHigh(pin::DIM_C) == 0);   // dark from the first instant (safePins, then 0)
  CHECK(lightSet(LIGHT_WARM, 40) && !lightSet(LIGHT_WARM, 40));   // a change once; the same level again isn't one
  CHECK(lightSet(LIGHT_COOL, 100));
  CHECK(fabsf(pwmHigh(pin::DIM_W) - 1638 / 4096.0f) < 1e-6f && pwmHigh(pin::DIM_C) == 1.0f);
  char rec[96];
  lightRecord(rec, sizeof rec);
  CHECK(strstr(rec, "warm=39.99 cool=100"));
  motorsOn();
  jog(100, 0, 0, 1000, 300);
  driveHalt();                                                     // a halt, MOTORS off: the lights don't care
  motOn = false;
  run(300);
  CHECK(fabsf(pwmHigh(pin::DIM_W) - 1638 / 4096.0f) < 1e-6f && pwmHigh(pin::DIM_C) == 1.0f);
  CHECK(lightSet(LIGHT_COOL, 0) && pwmHigh(pin::DIM_C) == 0);
  clean();
}

// The lid: each NTC sampled at 16 Hz (the whole configuration every time) and read out once a second, its mean and
// SD; !lid with every readout.
static void lidReadsItsSensors() {
  boot();
  run(5);
  CHECK(lidHas("mode=absent") && !lidHas("board.a="));         // no readout yet
  run(900);
  CHECK(lidHas("mode=absent"));                                  // the first takes 16 samples
  run(200);
  CHECK(lidHas("sw=on mode=idle duty=0 cut=0"));
  CHECK(fabs(lidVal("board.a") - 24.15) < 0.005 && fabs(lidVal("board.b") - 24.10) < 0.005);
  CHECK(fabs(lidVal("board.edge") - 23.75) < 0.005);
  CHECK(fabs(lidVal("med.j2") - 23.05) < 0.005 && fabs(lidVal("med.j3") - 22.95) < 0.005);
  CHECK(lidVal("board.a.sd") >= 0 && lidVal("board.a.sd") < 0.002 && lidVal("med.j3.sd") < 0.002);   // still: no noise
  CHECK(fabs(lidVal("v33") - 3.3) < 0.002);
  CHECK(lidRec().size() > 200 && lidRec().size() < 300);
  int n = lidConversions;
  clearEvents();
  run(5000);
  CHECK(lidConversions - n >= 6 * 80 - 6 && lidConversions - n <= 6 * 80 + 6);   // 6 a cycle at 16 Hz
  CHECK(lidEvents() == 5 && lidStates() == "sw=on mode=idle duty=0 cut=0");        // a readout a second
  clean();
}

// The probes: one open from the lid's first readout isn't fitted, and guards nothing; one that has read and goes
// open has come loose, and one shorted can't guard either: both faults. Plugged in again, decided anew.
static void lidProbes() {
  lidBroken[LID_J3] = 'o';                                       // no probe on J3
  lidReady();
  CHECK(lidHas("med.j3=open v33=") && !lidHas("med.j3.sd") && lidHas("mode=idle"));
  CHECK(lid(20) == LidSet::OK);
  lidBroken[LID_J2] = 'o';                                       // J2's lead off
  readout();
  CHECK(lidHas("med.j2=open") && lidHas("mode=fault duty=0 cut=fault"));
  CHECK(lid(20) == LidSet::FAULT && !strcmp(why, "med.j2 open"));
  lidBroken[LID_J2] = 's';
  readout();
  CHECK(lid(20) == LidSet::FAULT && !strcmp(why, "med.j2 short"));
  lidBroken[LID_J2] = 0;
  readout();
  CHECK(lidHas("mode=idle") && lid(20) == LidSet::OK);
  lidBroken[LID_J2] = 'o';                                       // taken off for good: re-plugging the lid accepts it
  lidPlugged = false;
  run(100);
  lidPlugged = true;
  run(1100);
  CHECK(lidHas("mode=idle"));
  CHECK(lidHas("med.j2=open med.j3=open") && lid(20) == LidSet::OK);
  clean();
}

// The readout's mean through noise and 50 / 60 Hz pickup (16 samples a second fall evenly over either); its SD is
// the noise, and not a warming board's rise.
static void lidReadoutStats() {
  lidNoiseV = 0.5e-3;                                            // 0.5 mV rms on every NTC: ~16 mK at 24 C
  lidReady();
  CHECK(lidVal("board.a.sd") > 0.010 && lidVal("board.a.sd") < 0.025);
  CHECK(fabs(lidVal("board.a") - 24.15) < 0.015);                // the mean of 16: ~4 mK
  lidNoiseV = 0;
  lidMainsV = 5e-3;                                              // 5 mV of 50 Hz: +-0.16 K, sample to sample
  readout();
  CHECK(lidVal("board.a.sd") > 0.08 && fabs(lidVal("board.a") - 24.15) < 0.004);
  CHECK(fabs(lidVal("med.j2") - 23.05) < 0.004);
  lidMainsHz = 60;
  readout();
  CHECK(lidVal("board.a.sd") > 0.08 && fabs(lidVal("board.a") - 24.15) < 0.004);
  lidMainsV = 0;
  CHECK(lid(50) == LidSet::OK);                                  // the board warming
  readout();
  double a0 = lidVal("board.a");
  run(1000);
  CHECK(lidVal("board.a") > a0 + 0.04 && lidVal("board.a.sd") < 0.003);   // its rise isn't counted as noise
  clean();
}

// By hand: the wake pulse, then coast PWM on one input; a reversal through both low; heat warms the board.
static void lidByHand() {
  lidReady();
  CHECK(lid(30) == LidSet::OK);
  CHECK(level(pin::LID_IN1) == 1 && level(pin::LID_IN2) == 0);   // the wake pulse
  run(1);
  CHECK(fabsf(pwmHigh(pin::LID_IN1) - 1229 / 4096.0f) < 1e-6f && level(pin::LID_IN2) == 0);
  CHECK(fabsf(lidDuty() - 1229 * 100.0f / 4096) < 1e-4f && lidHas("mode=manual duty=30 cut=0"));
  double a0 = lidVal("board.a");
  run(10000);
  CHECK(lidVal("board.a") > a0 + 0.1);                           // + heats (lid.sign 1, the modules as assumed)
  CHECK(lid(-20) == LidSet::OK);
  CHECK(level(pin::LID_IN1) == 0 && level(pin::LID_IN2) == 1);   // IN1 low first, then IN2's wake
  run(1);
  CHECK(fabsf(pwmHigh(pin::LID_IN2) - 819 / 4096.0f) < 1e-6f && level(pin::LID_IN1) == 0);
  CHECK(lid(-25) == LidSet::OK);                                 // a change on the way: straight to the new duty
  CHECK(fabsf(pwmHigh(pin::LID_IN2) - 1024 / 4096.0f) < 1e-6f);
  run(5);
  CHECK(lid(0) == LidSet::OK && level(pin::LID_IN1) == 0 && level(pin::LID_IN2) == 0);
  run(5);
  CHECK(lidStates() == "sw=on mode=manual duty=30 cut=0 | sw=on mode=manual duty=-20 cut=0 | "
                       "sw=on mode=manual duty=-25 cut=0 | sw=on mode=idle duty=0 cut=0");
  CHECK(lid(1.9f) == LidSet::OK && lidDuty() == 0 && level(pin::LID_IN1) == 0);   // under 2 %: 0
  clean();
}

// The cap: never past lid.duty_max, even rounding to the PWM's steps; lid.sign -1 drives IN2 for +.
static void lidCapAndSign() {
  config().lid.duty_max = 33;
  config().lid.sign = -1;
  lidHeatSign = -1;                                              // the modules fitted the other way up
  lidReady();
  CHECK(lid(33) == LidSet::OK);
  run(1);
  CHECK(level(pin::LID_IN1) == 0 && pwmHigh(pin::LID_IN2) > 0.32f && pwmHigh(pin::LID_IN2) <= 0.33f);
  double a0 = lidVal("board.a");
  run(5000);
  CHECK(lidVal("board.a") > a0 + 0.05);                          // still heats: the sign follows the modules
  clean();
}

// SW10 off: both inputs low at once, the duty kept; back on, it drives again after 20 ms, with the wake pulse.
static void lidSwitchHoldsIt() {
  lidReady();
  CHECK(lid(40) == LidSet::OK);
  run(5);
  lidSw = false;
  run(1);
  CHECK(level(pin::LID_IN1) == 0 && level(pin::LID_IN2) == 0);
  CHECK(lidHas("sw=off mode=manual duty=39.99 cut=0"));        // 40 % is step 1638.4: the nearest is said
  CHECK(lid(45) == LidSet::OK);                                  // taken, and held
  run(5);
  CHECK(level(pin::LID_IN1) == 0);
  lidSw = true;
  run(10);
  CHECK(level(pin::LID_IN1) == 0);                               // not yet: 20 ms steady
  run(20);
  CHECK(fabsf(pwmHigh(pin::LID_IN1) - 1843 / 4096.0f) < 1e-6f);
  CHECK(lidStates() == "sw=on mode=manual duty=39.99 cut=0 | sw=off mode=manual duty=39.99 cut=0 | "
                       "sw=off mode=manual duty=45 cut=0 | sw=on mode=manual duty=45 cut=0");
  driveHalt();                                                   // a halt, MOTORS: the lid doesn't care
  motOn = true;
  run(300);
  motOn = false;
  run(100);
  CHECK(fabsf(pwmHigh(pin::LID_IN1) - 1843 / 4096.0f) < 1e-6f);
  clean();
}

// A board NTC at 60 C cuts the duty, either way, and refuses another until it's under; 0 always goes.
static void lidHotCuts() {
  lidReady();
  CHECK(lid(-50) == LidSet::OK);                                 // cooling: cut too (the sign may yet be wrong)
  run(10);
  lidBoard = 60.1;                                               // board.a reads 60.25, less what the cooling takes
  readout();
  CHECK(level(pin::LID_IN1) == 0 && level(pin::LID_IN2) == 0 && lidDuty() == 0);
  CHECK(lidHas("mode=idle duty=0 cut=hot"));
  CHECK(lid(20) == LidSet::HOT && std::string(why).find("board.a 60.") == 0 && strstr(why, "C, the limit is 60"));
  CHECK(lid(0) == LidSet::OK && lidHas("cut=0"));                // any lid command takes the cut back to 0
  lidBoard = 59.6;
  readout();
  CHECK(lid(20) == LidSet::OK);
  run(5);
  CHECK(lidStates() == "sw=on mode=manual duty=-50 cut=0 | sw=on mode=idle duty=0 cut=hot | "
                       "sw=on mode=idle duty=0 cut=0 | sw=on mode=manual duty=20 cut=0");
  clean();
}

// A medium probe at lid.medium_max (40 C) cuts the duty, either way, and refuses another until it's under. The
// file's limit, not a fixed one; with neither probe fitted, nothing guards the medium but the board's 60 C.
static void lidMediumCuts() {
  lidReady();
  CHECK(lid(30) == LidSet::OK);
  run(10);
  lidMedium = 40.0;                                              // med.j2 reads 40.05, med.j3 39.95
  readout();
  CHECK(level(pin::LID_IN1) == 0 && lidDuty() == 0 && lidHas("mode=idle duty=0 cut=medium"));
  CHECK(lid(-12.5) == LidSet::MEDIUM && !strcmp(why, "med.j2 40.05 C, the limit is 40 (lid.medium_max)"));
  CHECK(lid(0) == LidSet::OK);
  lidMedium = 39.5;
  readout();
  CHECK(lid(-12.5) == LidSet::OK);
  config().lid.medium_max = 37.5;
  readout();
  CHECK(lidHas("mode=idle duty=0 cut=medium") && lid(12.5) == LidSet::MEDIUM);
  CHECK(std::string(why).find("the limit is 37.5 (lid.medium_max)") != std::string::npos);
  lidBroken[LID_J2] = lidBroken[LID_J3] = 'o';                  // the probes off, the lid plugged in again
  lidPlugged = false;
  run(100);
  lidPlugged = true;
  run(1100);
  CHECK(lid(12.5) == LidSet::OK && lidHas("med.j2=open med.j3=open"));
  run(5);
  CHECK(lidStates() == "sw=on mode=manual duty=30 cut=0 | sw=on mode=idle duty=0 cut=medium | "
                       "sw=on mode=idle duty=0 cut=0 | sw=on mode=manual duty=-12.5 cut=0 | "
                       "sw=on mode=idle duty=0 cut=medium | sw=on mode=absent duty=0 cut=medium | "
                       "sw=on mode=idle duty=0 cut=medium | sw=on mode=manual duty=12.5 cut=0");
  clean();
}

// A sensor that can't be trusted (a board NTC open, shorted or out of 0-100 C, or the 3.3 V off): cut, and refused.
static void lidSensorFault() {
  lidReady();
  CHECK(lid(30) == LidSet::OK);
  run(10);
  lidBroken[LID_EDGE] = 'o';
  readout();
  CHECK(level(pin::LID_IN1) == 0 && lidHas("mode=fault duty=0 cut=fault") && lidHas("board.edge=open"));
  CHECK(lid(10) == LidSet::FAULT && !strcmp(why, "board.edge open"));
  CHECK(lid(0) == LidSet::OK);
  lidBroken[LID_EDGE] = 0;
  readout();
  CHECK(lidHas("mode=idle") && lid(10) == LidSet::OK);
  lidOffset[LID_B] = -30;                                        // reads -6 C: a lifted or failing NTC
  readout();
  CHECK(lidHas("mode=fault duty=0 cut=fault") && lid(10) == LidSet::FAULT);
  CHECK(std::string(why).find("board.b -") == 0);
  lidOffset[LID_B] = 0.1;
  lidV33 = 3.0;
  readout();
  CHECK(lid(10) == LidSet::FAULT && !strcmp(why, "v33 3.000 V"));
  lidV33 = 3.3;
  readout();
  CHECK(lid(10) == LidSet::OK);
  clean();
}

// Unplugged: cut at the first transaction unanswered, refused while it's gone; plugged back in, idle at its first
// readout, nothing resumes.
static void lidUnplugged() {
  lidReady();
  CHECK(lid(30) == LidSet::OK);
  run(10);
  lidPlugged = false;
  run(70);
  CHECK(level(pin::LID_IN1) == 0 && lidHas("mode=absent duty=0 cut=absent") && !lidHas("board.a="));
  CHECK(lid(10) == LidSet::ABSENT && lid(0) == LidSet::OK);
  lidPlugged = true;                                             // its ADCs back at their power-up defaults
  run(500);
  CHECK(lidHas("mode=absent"));                                  // not until a readout
  run(700);
  CHECK(lidHas("mode=idle duty=0 cut=0") && fabs(lidVal("board.a") - lidBoard - 0.15) < 0.005);
  CHECK(level(pin::LID_IN1) == 0);
  CHECK(lidStates() == "sw=on mode=manual duty=30 cut=0 | sw=on mode=absent duty=0 cut=absent | "
                       "sw=on mode=absent duty=0 cut=0 | sw=on mode=idle duty=0 cut=0");
  clean();
}

// A duty is its session's: that session ending drops it, anyone else's ending doesn't.
static void lidSessionLost() {
  lidReady();
  CHECK(lid(30, 1) == LidSet::OK);
  CHECK(!lidLost(2, "closed"));
  CHECK(lid(25, 2) == LidSet::OK);                               // the bench's USB session takes it over
  CHECK(!lidLost(1, "link") && lidDuty() > 24);
  CHECK(lidLost(2, "closed") && lidDuty() == 0 && level(pin::LID_IN1) == 0 && lidHas("cut=closed"));
  CHECK(!lidLost(2, "closed"));                                  // nothing left to drop
  CHECK(lid(0, 1) == LidSet::OK && lidHas("cut=0") && !lidLost(1, "bye"));
  clean();
}

static void recordFormat() {
  boot();
  motorsOn();
  jog(0, -60, 0, 0, 200);
  char rec[600];
  size_t n = driveRecord(rec, sizeof rec);
  CHECK(n > 100 && n < 512);
  CHECK(strstr(rec, "motors=on") && strstr(rec, "halt=0") && strstr(rec, "y.mode=jog") && strstr(rec, "y.v=-60.0"));
  CHECK(strstr(rec, "x.mode=idle") && strstr(rec, "r.mode=idle") && strstr(rec, "x.cal=0") && strstr(rec, "r.drv=ok"));
  CHECK(strstr(rec, "x.lim=0 x.blk=0") && strstr(rec, "r.lim=0 r.blk=0"));
  clean();
}

int main() {
  struct Case { const char* name; void (*fn)(); } cases[] = {
      {"boot with MOTORS off: nothing moves, jogs refused", bootMotorsOff},
      {"MOTORS on: driver configured and phased over UART, nothing energised", motorsOnDoesNothing},
      {"jog x: ramp, drive/brake PWM at the model's duty, let go, brake then coast", jogFineRampsAndRests},
      {"reversals pass through brake", reversalThroughBrake},
      {"fine limit, bouncing: stops at once, blocks that half-space, releases; the end decided once", fineLimitBlocksHalfSpace},
      {"stall: stops within 0.1 s, blocks until backed off, then lifts", stallStopsAndBlocks},
      {"rail jog: AT#1 on the first move, steps, parks on a detent, driver off", railJogParksAndPowersDown},
      {"MOTORS off and on while parked: no jump", motorsCycleNoJump},
      {"first boot: at most one jump, never again", firstBootSnapsOnce},
      {"effector switch (MIN): blocks -x on the rail and fine x, and says so for both", effectorSwitchBlocksBoth},
      {"carriage switch (MAX): blocks +x on the rail only", carriageSwitchRailOnly},
      {"fine x on its + end and the effector on MIN: x blocked both ways", fineXBlockedBothWays},
      {"glitches: a stop, then the jog carries on; nothing reported, nothing frozen", glitchesStopThenCarryOn},
      {"rail sign wrong: MIN trips moving +x, the rail freezes", wrongRailSignFreezes},
      {"halt: everything stops now, held until the operator lets go", haltStopsNowAndLatches},
      {"the driving client lost: a halt, held the same way; anyone else's loss: nothing", lostClientHalts},
      {"the lease running out: a halt, held the same way", leaseRunsOutHalts},
      {"MOTORS off mid-move: everything stops, position lost", motorsOffMidMove},
      {"driver brown-out mid-move: caught, position lost, retried", driverBrownOut},
      {"head unplugged: rail frozen, fine axes at the unknown-end cap", headUnplugged},
      {"TMC2209 silent: rail fault, fine axes still run", driverSilent},
      {"the lights: dark at boot, a level as its PWM, held through a halt and MOTORS off", lightsByHand},
      {"the lid's sensors: 16 Hz, read out once a second (a mean and SD each), ratiometric; !lid every readout", lidReadsItsSensors},
      {"the readout: its mean through noise and 50 / 60 Hz pickup; its SD the noise, not a warming board's rise", lidReadoutStats},
      {"the probes: open from the start unfitted; gone open or shorted a fault; re-plugged, decided anew", lidProbes},
      {"the lid by hand: wake pulse, coast PWM on one input, reversals through both low; heat warms", lidByHand},
      {"the lid's cap, rounding included; lid.sign -1 drives IN2", lidCapAndSign},
      {"SW10 off: both inputs low at once, the duty held; on again after 20 ms; halt and MOTORS ignored", lidSwitchHoldsIt},
      {"a board NTC at 60 C: cut either way, refused until under; 0 always taken", lidHotCuts},
      {"a medium probe at lid.medium_max: cut either way, refused until under; no probe, no guard", lidMediumCuts},
      {"a board sensor open, out of range, or the 3.3 V off: cut and refused", lidSensorFault},
      {"the lid unplugged: cut at once, refused; back, idle at its first readout, nothing resumes", lidUnplugged},
      {"a lid duty ends with its session, and only its own", lidSessionLost},
      {"the drive record", recordFormat},
  };
  int failed = 0, n = 0;
  for (const Case& c : cases) {
    n++;
    fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
      c.fn();
      fflush(stdout);
      _exit(fails ? 1 : 0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    bool ok = WIFEXITED(status) && WEXITSTATUS(status) == 0;
    printf("  %s  %s\n", ok ? "ok  " : "FAIL", c.name);
    failed += !ok;
  }
  printf("sim: %d scenarios, %d failed\n", n, failed);
  return failed ? 1 : 0;
}
