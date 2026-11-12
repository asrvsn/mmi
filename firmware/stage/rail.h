// The rail (coarse X): step generation on GPT2, the TMC2209 over its single-wire UART, and the rail's state
// machine (configure before every enable, park on a detent, driver off at rest). hardware.md, "Rail".
#pragma once
#include <stdint.h>

#include "params.h"

void railBegin(const Params& p);   // Serial1 and the step timer; the pins are already safe (EN high, STEP low)
void railPoll();                   // main loop: UART sequences, enable / power-down, health checks, events

// From the control tick (1 kHz, interrupt context):
void railTick(float target_ums, uint8_t blk);   // target in um/s (api frame); blk: half-spaces it may not enter
void railStopNow();                // abrupt stop: no further steps, speed 0
void railInhibit(uint8_t blk);     // what the step generator itself refuses, at once (limits, faults)
void railMotors(bool on);          // MOTORS changed

int8_t railDir();                  // -1, 0, +1: the direction it is stepping now
float railSpeed();                 // um/s now, api frame
float railPos();                   // um counted since power-up
int32_t railSteps();               // the same, in microsteps
int32_t railPhase();               // the rotor's electrical phase, MSCNT units
bool railBusy();                   // enabling, tuning, moving, or standing energised before powering down
bool railSettled();                // configured or failed since MOTORS came on
bool railFault();
const char* railMode();            // off, wait, idle, jog, stop, fault
const char* railDriver();          // ok, off, noreply, reset, error
