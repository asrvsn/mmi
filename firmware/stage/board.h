// Pins and fixed hardware facts: ../hardware.md "Pins and timers", checked against the main and head netlists
// (2026-10-18). Values here are hardware, not settings: parameters.yaml can't move them.
#pragma once
#include <stdint.h>

namespace pin {
constexpr uint8_t TMC_RX = 0, TMC_TX = 1;   // Serial1; TX through R1 1k to PDN_UART, RX on it: we hear our own echo
constexpr uint8_t STEP = 2, DIR = 3;
constexpr uint8_t EN = 4;                   // TMC2209 enable, low = on (R2 pulls it high). Never analogWrite (pin 33)
constexpr uint8_t DIAG = 5;
constexpr uint8_t LIM_MIN = 6, LIM_MAX = 7; // rail switches; never analogWrite 7 (pin 25)
constexpr uint8_t V9_OFF = 9;               // high = the head's 9 V buck off
constexpr uint8_t LED_R = 10, LED_G = 11, LED_B = 12;   // D9, low = on
constexpr uint8_t LID_IN1 = 14, LID_IN2 = 15;           // the lid's DRV8871 (lid.cpp): coast PWM, or both low
constexpr uint8_t LIM_X = 20, LIM_Y = 21, LIM_Z = 22;   // each fine axis's hi + lo switches in series
constexpr uint8_t DIM_W = 23, DIM_C = 33;               // LED strip set-points (light.cpp): 12-bit PWM, low = dark
constexpr uint8_t MOT_ON = 40;              // SW11: high = the motors' 24 V is up
constexpr uint8_t LID_SW = 41;              // SW10: low = lid heater off
// Fine axes in order x, y, z: DRV8251A IN1 / IN2 (PWM between drive and brake) and IPROPI.
constexpr uint8_t IN1[3] = {28, 36, 24};
constexpr uint8_t IN2[3] = {29, 37, 25};
constexpr uint8_t IPROPI[3] = {27, 38, 26}; // A13, A14, A12
constexpr uint8_t LIM_FINE[3] = {LIM_X, LIM_Y, LIM_Z};
constexpr uint8_t FREE[] = {8, 13, 18, 19, 30, 31, 32, 34, 35, 39};
}  // namespace pin

namespace hw {
constexpr float VM = 9.16f;                 // the head's motor rail, V
constexpr float IPROPI_V_PER_A = 3.15f;     // 1575 uA/A into 2k0 on the head
constexpr float ADC_FULL_V = 3.3f;          // 12-bit ADC against the Teensy's 3.3 V
constexpr float DUTY_CEIL = 0.92f;          // MUST: fine motors average <= ~8.4 V
constexpr float FINE_V_CEIL = 400.0f;       // um/s: no setting may ask more of a fine axis
constexpr float RAIL_V_CEIL = 20000.0f;     // um/s
constexpr uint8_t RAIL_IRUN_CEIL = 28;      // (28+1)/32 x 0.657 A = 0.60 A rms, the NEMA11's rating
constexpr uint32_t WAKE_US = 300;           // DRV8251A t_WAKE 250 us: brake this long before the first PWM
constexpr uint32_t REST_BRAKE_MS = 100;     // at rest: brake, then coast (the drivers sleep)
constexpr uint32_t STALL_MS_CEIL = 80;      // MUST: a stall stops within 0.1 s
constexpr uint32_t MOT_DEBOUNCE_MS = 50;    // MOT_ON steady high before acting on it
constexpr uint32_t HEAD_9V_MS = 100;        // the head's soft start + buck after MOT_24V
constexpr uint32_t LIMIT_CONFIRM_US = 3000; // a limit input must stay high this long to count (it stops at once anyway)
constexpr uint32_t LIGHT_PWM_HZ = 36621;    // the LED set-points: 150 MHz / 4096, so 12 bits are one count each
// The lid (../hardware.md "Lid"; the ADCs' inputs checked against lid v16's netlist, ../../hardware/lid/heating_pcb,
// 2026-10-30).
constexpr uint32_t LID_PWM_HZ = 20000;      // the bridge's coast PWM: inaudible, and the DRV8871's 800 ns pulse is 1.6 %
constexpr float LID_DUTY_CEIL = 80.0f;      // %: lid.duty_max can't pass it (the 9.16 V supply's full drive is 6.1 W)
constexpr float LID_DUTY_FLOOR = 2.0f;      // %: under it, 0 (a pulse too short to be sure to register)
constexpr uint32_t LID_WAKE_US = 60;        // MUST: the active input high this long before the PWM (wake 5 us + t_ON 50)
constexpr uint32_t LID_SW_MS = 20;          // SW10 steady on this long before driving again
constexpr float LID_BOARD_MAX_C = 60.0f;    // MUST: a board NTC at this cuts the drive (../hardware.md "Lid")
constexpr float LID_MEDIUM_CEIL = 50.0f;    // C: lid.medium_max can't pass it
// The NTCs' two rates: each sampled every 62.5 ms (16 Hz), and a readout of the last
// 16, their mean and SD, once a second (1 Hz): the thermal loop's period and !lid's. 16 samples a second fall evenly
// over a cycle of 50 and of 60 Hz mains, so pickup from either averages out of the mean (10, 20 or 25 Hz don't).
constexpr uint32_t LID_POLL_US = 62500;
constexpr uint8_t LID_WINDOW = 16;
constexpr uint32_t ADS_CONV_US = 9000;      // an ADS1115 conversion at 128 SPS: 7.8 ms, +10 % for its clock
constexpr uint32_t ADS_LATE_US = 30000;     // ... and one that hasn't finished by this is a fault (the lid's gone)
constexpr uint8_t ADS_U4 = 0x48, ADS_U3 = 0x49;   // ADDR to GND / to VDD
constexpr float ADS_FS_V = 4.096f;          // the NTC channels' PGA: 12.3 C and below would saturate at 2.048 V
constexpr float NTC_B_BOARD = 3380.0f;      // RT1-RT3, Murata NCP18XH103F03RB: B25/50 (its "3435" is B25/85)
constexpr float NTC_B_PROBE = 3977.0f;      // J2, J3: TEWA TT4-G10KC3 medium probes, 10k 1 %
constexpr float LID_V33 = 3.3f, LID_V33_TOL = 0.05f;   // the lid's 3.3 V: within 5 %, or no NTC reading is trusted
}  // namespace hw

// The TMC2209 (rail). Its fixed configuration, hardware.md "Rail": everything but IRUN lives here.
namespace tmc {
constexpr uint32_t BAUD = 115200;
constexpr uint8_t ADDR = 0;                 // MS1 = MS2 = GND
constexpr uint8_t VERSION = 0x21;           // IOIN[31:24]
constexpr float RSENSE = 0.33f;             // 3 x 1R per coil
// GCONF: pdn_disable, mstep_reg_select, multistep_filt. MUST: never internal_Rsense (bit 1); never OTP_PROG.
constexpr uint32_t GCONF = (1u << 6) | (1u << 7) | (1u << 8);
constexpr uint8_t TOFF = 4, HSTRT = 5, HEND = 0, TBL = 1;
constexpr uint8_t IHOLD = 0, IHOLDDELAY = 2, TPOWERDOWN = 5;   // ramp to zero current at standstill, then EN high
constexpr uint32_t PWMCONF = 0xC10D0E24;    // reset value with PWM_GRAD 14: autoscale + autograd on
constexpr float CLK_HZ = 12e6f;
}  // namespace tmc
