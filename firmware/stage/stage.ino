// Stage-top unit firmware, Teensy 4.1 (firmware/api.md 0.11): `jog`, the fine axes and the rail under velocity
// control, with the limit, stall, MOTORS and driver rules of ../hardware.md; `light`, the LED strips by hand; and
// `lid`, the Peltiers by hand, with the lid's temperatures read out once a second. Parameters from /parameters.yaml on
// the SD card (../parameters.md), else the built-in precomputed model.
//
// Never blocks once running: the 1 kHz control tick and the step generator are interrupts; the main loop only polls
// (network, USB, the TMC2209's UART, limit confirmation, the lid's ADCs one transaction at a time), so a command is
// handled within ~1 ms of arriving.

#include <SD.h>

#include "board.h"
#include "drive.h"
#include "lid.h"
#include "light.h"
#include "net.h"
#include "params.h"
#include "rail.h"
#include "yaml.h"

namespace {
Params P;
Report R;
const char* cfgState = "default";
const char* resetCause = "power";
uint32_t lastTicks = 0;

// hardware.md "Start-up", step 1: every output in its safe state before anything else runs.
void safePins() {
  digitalWriteFast(pin::EN, HIGH);   // the TMC2209 off (R2 already holds it high)
  pinMode(pin::EN, OUTPUT);
  digitalWriteFast(pin::STEP, LOW);
  pinMode(pin::STEP, OUTPUT);
  pinMode(pin::DIR, OUTPUT);
  pinMode(pin::DIAG, INPUT);
  digitalWriteFast(pin::V9_OFF, LOW);   // the head's 9 V on (its drivers coast until driven)
  pinMode(pin::V9_OFF, OUTPUT);
  for (int a = 0; a < 3; a++) {         // fine axes: both inputs low = coast
    digitalWriteFast(pin::IN1[a], LOW);
    pinMode(pin::IN1[a], OUTPUT);
    digitalWriteFast(pin::IN2[a], LOW);
    pinMode(pin::IN2[a], OUTPUT);
  }
  digitalWriteFast(pin::LID_IN1, LOW);  // the lid bridge coasts until the lid comes online
  pinMode(pin::LID_IN1, OUTPUT);
  digitalWriteFast(pin::LID_IN2, LOW);
  pinMode(pin::LID_IN2, OUTPUT);
  digitalWriteFast(pin::DIM_W, LOW);    // the LED strips dark
  pinMode(pin::DIM_W, OUTPUT);
  digitalWriteFast(pin::DIM_C, LOW);
  pinMode(pin::DIM_C, OUTPUT);
  for (uint8_t p : {pin::LED_R, pin::LED_G, pin::LED_B}) {   // D9 (active low): red from here until setup() ends
    digitalWriteFast(p, p == pin::LED_R ? LOW : HIGH);
    pinMode(p, OUTPUT);
  }
  pinMode(pin::LID_SW, INPUT_PULLUP);
  pinMode(pin::MOT_ON, INPUT);          // R87 13k pulls it down
  for (uint8_t p : pin::FREE) pinMode(p, INPUT_PULLDOWN);
}

const char* readResetCause() {
  uint32_t srsr = SRC_SRSR;
  SRC_SRSR = srsr;   // write-1-to-clear, so the next start reports its own cause
  if (srsr & SRC_SRSR_WDOG_RST_B) return "wdog";
  if (srsr & SRC_SRSR_LOCKUP_SYSRESETREQ) return "soft";
  if (srsr & SRC_SRSR_IPP_RESET_B) return "power";
  return "other";
}

void sink(void*, const char* key, const char* value, int line) { paramsApply(P, key, value, line, R); }

void loadParams() {
  paramsDefaults(P);
  if (!SD.begin(BUILTIN_SDCARD)) {
    cfgState = "nocard";
    R.add("no SD card: running on the built-in defaults (the precomputed model)");
  } else {
    File f = SD.open("parameters.yaml", FILE_READ);
    if (!f) {
      R.add("no /parameters.yaml on the card: running on the built-in defaults");
    } else {
      static char buf[16384];
      size_t n = f.read(buf, sizeof buf - 1);
      bool more = f.available() > 0;
      f.close();
      buf[n] = '\0';
      char err[96];
      if (more) {
        R.add("parameters.yaml is over 16 kB: running on the built-in defaults");
      } else if (!yamlFlatten(buf, sink, nullptr, err, sizeof err)) {
        R.add("parameters.yaml: %s; running on the built-in defaults", err);
        paramsDefaults(P);
      } else {
        cfgState = "card";
      }
    }
  }
  paramsFinish(P, R);
}

// WDOG1, ~0.5 s (hardware.md rule 9): fed only from the main loop, and only while the control tick is running.
// Started last in setup(): SD.begin can take longer than 0.5 s, and nothing can move before the tick has seen MOT_ON
// high for 50 ms anyway.
void wdogBegin() {
  CCM_CCGR3 |= CCM_CCGR3_WDOG1(CCM_CCGR_ON);   // its clock gate (imxrt.h: "WDOG1 requires CCM_CCGR3_WDOG1")
  WDOG1_WMCR = 0;   // no power-down counter
  WDOG1_WCR = WDOG_WCR_SRS | WDOG_WCR_WDA | WDOG_WCR_WT(0) | WDOG_WCR_WDE | WDOG_WCR_WDT | WDOG_WCR_SRE;
}

void wdogFeed() {
  WDOG1_WSR = 0x5555;
  WDOG1_WSR = 0xAAAA;
}

// D9, the status LED (common anode on 3.3 V; pins 10 / 11 / 12 low = red / green / blue on). A small state machine:
//   red          starting up: from the first line of setup() until it can take commands
//   green        ready, nothing to do
//   blue         working: a command just arrived (shown for >= 50 ms), or an axis is moving, braking, enabling or
//                standing energised before its driver powers down. A held stick keeps it blue
//   red, 2 Hz    a fault that holds until a restart (the !fault lines say which); blue still shows work
enum class Led : uint8_t { BOOT, READY, BUSY, FAULT };
Led led = Led::BOOT;

void ledShow(bool r, bool g, bool b) {
  digitalWriteFast(pin::LED_R, r ? LOW : HIGH);
  digitalWriteFast(pin::LED_G, g ? LOW : HIGH);
  digitalWriteFast(pin::LED_B, b ? LOW : HIGH);
}

void ledPoll() {
  uint32_t now = millis();
  led = (driveBusy() || netSinceCommand() < 50) ? Led::BUSY : driveFault() ? Led::FAULT : Led::READY;
  switch (led) {
    case Led::BUSY: ledShow(false, false, true); break;
    case Led::FAULT: ledShow((now / 250) & 1, false, false); break;
    case Led::READY: ledShow(false, true, false); break;
    default: ledShow(true, false, false); break;
  }
}
}  // namespace

void setup() {
  safePins();
  resetCause = readResetCause();
  analogWriteResolution(12);
  loadParams();
  railBegin(P);
  driveBegin(P);
  lightBegin();   // dark, as safePins() left the strips; set by the light command only (nothing resumes)
  lidBegin(P);    // coasting, as safePins() left the bridge; driven by the lid command only
  netBegin({resetCause, cfgState, &P, &R});
  wdogBegin();
}

void loop() {
  netPoll();
  drivePoll();
  railPoll();
  lidPoll();
  ledPoll();
  uint32_t t = driveTicks();
  if (t != lastTicks) {   // the tick has run since the last feed, and so has this loop
    lastTicks = t;
    wdogFeed();
  }
}
