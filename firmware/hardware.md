# Hardware rules

What the Teensy 4.1 firmware has to do so that the hardware behaves as designed: the rules for each driver, pins and
timers, the order of things at start-up, and commissioning. Written against the boards as ordered on 2026-10-09: main
v20 (`../hardware/frame/main_pcb`), head v12 (the drive board, `../hardware/frame/drive_pcb`), lid v16 (the heating
board, `../hardware/lid/heating_pcb`). The protocol is `api.md`, the settings `parameters.md`; the status LED, the limit switches'
handling, building and the bench steps are in `README.md`. Nothing here is repeated there.

**MUST** marks a rule that protects the hardware, the sample or the stages: the firmware keeps every one (`sim_test`
checks them), and no parameter can override one. The rest are design choices and starting points. "Later" marks what
the firmware doesn't do yet.

## The rules that matter most

1. **Lid bridge: PWM with coast, never drive/brake** (Lid). Forward is IN1 = PWM with IN2 = 0. Reverse is IN1 = 0 with
   IN2 = PWM. Both inputs are never high together.
2. **Lid bridge: a wake pulse on every restart from duty 0, and after any pause of ≥ ~0.2 ms**, then the PWM (Lid).
   A duty under 2 % is 0.
3. **Motors are the opposite: PWM between drive and brake** (Fine axes), at 40 kHz with the duty capped at 92 %.
   Change direction through brake, never through coast.
4. **Any limit input going high stops that axis at once** (Limit switches).
5. **A stall on IPROPI stops that axis within 0.1 s** (Fine axes): ~0.70 A, plus a test against the duty.
6. **The TMC2209:** check GSTAT and rewrite its configuration before every enable, and poll it while enabled. Never
   set GCONF.internal_Rsense or program its OTP. EN stays high whenever the rail is parked (Rail).
7. **Lid ADCs:** write the whole ADS1115 configuration with every conversion. Coast the lid whenever they don't answer
   (Lid).
8. **SW10 is the lid heater's enable** (pin 41, low = off). While it reads low, both lid inputs are held low (Lid).
9. **The watchdog (WDOG1, ~0.5 s) is fed only from the main loop,** and only while the control tick is running
   (Watchdogs).
10. **Never `analogWrite` pins 4 or 7.** They share PWM submodules with pins 33 and 25.
11. **SW11 (MOTORS) powers every motor** (pin 40, MOT_ON, high = powered). While it reads low, motion is refused, STEP
    and the head's IN lines are held low, and no UART is expected from the TMC2209. When it comes back, the TMC2209's
    configuration is rewritten before the first move; a drop mid-move loses the rail's count (MOTORS switch).

## What the hardware guarantees anyway

The firmware can't break these. They are also what happens after a hang, once WDOG1 has reset the Teensy and its pins
have gone high-impedance.

| Thing | Hardware limit | After a reset, before `setup()` |
|---|---|---|
| Lid power | the lid's own 9.16 V buck: full duty is 0.67 A, 6.1 W (0.75 A, 7.0 W at the tolerance corners); TCO1 (72 ± 5 °C) in series with the string | IN1/IN2 pulled low on the lid (100k): coast, then sleep |
| Each fine-axis motor | DRV8251A current regulation at 0.93 A (VREF on the head) | inputs pulled low (100k in the driver): coast |
| Rail motor | 0.66 A RMS full scale (the sense resistors), which is also the reset default | EN pulled high (R2): driver off |
| LED strip | 39.7 mA per channel full scale (R13/R14) | set-points pulled to 0 (R26/R49): dark, on every reset and firmware upload |
| The head's 9 V servo rail | the head's buck (U3) current limit, then F4 on the ribbon's 24 V | V9_OFF pulled low (R36, on the head): rail on, drivers coasting |
| Every motor: the rail and the three fine axes | SW11 off: no 24 V on the TMC2209's VS or the head (Q8 off), so nothing the firmware does can move, stall or heat them | as the switch is set: the firmware can't turn motor power on |
| The whole unit | F1 + F3 (~2 A hold); the lid's F2 | - |

**What only the firmware enforces** (with SW11 on; with it off the motors have no power at all): stopping at the
limit switches; stopping stalled motors; the lid's temperature limits; ramps and parking; halting when the driving
client is lost or its jogs stop (`api.md` 2).

## Pins and timers

`analogWriteResolution(12)` is global; every PWM below uses it. F_BUS is 150 MHz. `stage/board.h` holds the same.

| Pin | Signal | Mode | Timer / ADC | Notes |
|---|---|---|---|---|
| 0 / 1 | TMC2209 UART (TX through 1k, RX on PDN_UART) | `Serial1`, half duplex, 115200 | - | address 0; the firmware hears its own TX echoed |
| 2 / 3 | STEP / DIR | output | - | STEP has R55 100k down |
| 4 | EN, TMC2209 (low = on) | output, **high at start-up** | FlexPWM2_0_A | R2 10k up. Never `analogWrite` (shares with 33) |
| 5 | DIAG (StallGuard) | input | - | unused until StallGuard is commissioned |
| 6 / 7 | LIM_MIN / LIM_MAX (rail) | input | 7: FlexPWM1_3_B | 2k up on the cable side, then 1k + 100 nF. Never `analogWrite` 7 (shares with 25) |
| 9 | V9_OFF: high = the head's 9 V buck off | output, low | - | through R77 1k and ribbon 15 to the head's Q5; R36 100k down there |
| 10 / 11 / 12 | D9 red / green / blue cathode (low = on) | PWM or GPIO | QuadTimer1 ch 0 / 2 / 1 | |
| 14 / 15 | lid IN1 / IN2 | PWM 20 kHz, or GPIO | QuadTimer3 ch 2 / 3 | **set each pin's frequency** (separate channels); 100 R here, 100k down on the lid |
| 16 / 17 | Wire1 SCL1 / SDA1 (the lid) | `Wire1`, 100 kHz: keep it there (rise time 0.25-0.63 µs) | - | 2k2 pull-ups on the main board |
| 20 / 21 / 22 | LIM_X / LIM_Y / LIM_Z | input | - | as pins 6 / 7; each the axis's hi and lo switches in series |
| 23 / 33 | LED set-point warm / cool | PWM 36 621 Hz, 12 bit | FlexPWM4_1_A / FlexPWM2_0_B | two calls (two modules); 150 MHz / 4096, so one count is one step |
| 28 / 29 | X motor IN1 / IN2 | PWM 40 kHz, or GPIO | FlexPWM3_1 B / A | one call sets both |
| 36 / 37 | Y motor IN1 / IN2 | as X | FlexPWM2_3 A / B | one call sets both |
| 24 / 25 | Z motor IN1 / IN2 | as X | FlexPWM1_2_X / FlexPWM1_3_X | **two submodules: set both** |
| 27 / 38 / 26 | X / Y / Z motor current (IPROPI) | analog | A13 / A14 / A12, all on ADC2 | read in turn; 3.15 V/A, the trip (0.93 A) at 2.93 V |
| 40 | MOT_ON: SW11 MOTORS, the motors' 24 V | input, **no pull** (R87 13k + C83 100 nF to GND on the board) | A16 | high = MOT_24V up: 100k / 13k from it, 2.76 V at 24 V (VIH 2.31 V). As A16: MOT_24V = 8.69 x the pin voltage. D16 (amber) shows it in hardware |
| 41 | LID_SW: SW10, the lid heater's enable | `INPUT_PULLUP` | - | low = off (SW10 grounds it). D11 shows it in hardware |
| 8, 13, 18, 19, 30-32, 34, 35, 39 | free | `INPUT_PULLDOWN` | - | 13 is the Teensy's own LED |

**QuadTimer pins (10-12, 14, 15):** `analogWrite(pin, 0)` still leaves a 2-count pulse, and the maximum value a
2-count gap. For a true 0 or 1 the pin goes to GPIO (`pinMode(pin, OUTPUT); digitalWrite(pin, LOW / HIGH)`); the next
`analogWrite` hands it back to the timer.

## Start-up

1. **Pins first:** EN (4) high; V9_OFF (9) low; every motor and lid IN pin GPIO low (coast); D9 red (10 low, 11-12
   high) until `setup()` ends; LID_SW (41) `INPUT_PULLUP`; MOT_ON (40) `INPUT` (its divider pulls it down); the free
   pins `INPUT_PULLDOWN`.
2. **The reset cause** (`!hello reset=`), then `/parameters.yaml` from the SD card (`parameters.md`).
3. **PWM:** `analogWriteResolution(12)` and each frequency in the table, the set-points at 0.
4. **The watchdog:** WDOG1 at ~0.5 s.
5. **The rail driver:** `Serial1`; EN stays high. The TMC2209 is configured once MOTORS is on (it's unpowered until
   then: MOTORS switch).
6. **The lid:** `Wire1` at 100 kHz. It reads `absent` until its first readout (Presence).
7. **The network:** Ethernet (QNEthernet), static 192.168.50.2/24, listening on TCP 5000.
8. **The limit inputs:** a fine-axis input already high means its end is unknown (`!limit <axis> ?`).
9. **No homing:** `r.pos` counts from power-up.

## Watchdogs and faults

- **WDOG1** (~0.5 s) is the backstop for a hang. **MUST:** it's fed only from the main loop, and only while the
  control tick (the 1 kHz interrupt) is running, so a stuck loop or a dead tick both reset the Teensy. A hung CPU
  leaves every PWM timer at its last duty; only the reset stops it (What the hardware guarantees).
- **After a watchdog reset:** `!hello reset=wdog`; nothing resumes (the lights dark, the lid off: `api.md` 9.3); the
  rail's count restarts; the TMC2209 is configured again before its next enable, as always.
- **The host:** there's no heartbeat. Losing the driving client, or its jogs lapsing (the 250 ms lease), halts the
  drive (`api.md` 2, 6.4). A lid duty set by hand ends with its session. The temperature control, when it comes, runs
  on locally through a dropped link, so an incubation isn't interrupted.

## MOTORS switch (SW11)

SW11 switches MOT_24V, the 24 V of the rail stepper (the TMC2209's VS) and of the head (F4 -> the ribbon), through a
soft start (Q8, ~20 ms ramp). It's on only while someone positions the sample; off, no fault of the firmware can
move, stall or heat a motor. MOT_ON (pin 40) reads it.

- **MUST, while MOT_ON is low:** motion refused (`!jog refused motors-off`, `err motors-off`); STEP low and EN high;
  the head's IN lines GPIO low (coast); no UART reply from the TMC2209 and no IPROPI current expected, neither a
  fault. V9_OFF can stay as it is (the head is unpowered either way).
- **When it goes high:** 50 ms of steady high (MOT_24V ramps ~20 ms after a ~5-25 ms delay), then `motors=wait`: the
  TMC2209 is configured and verified over its UART (it came up from reset, GSTAT.reset set: rule 6) and its phase
  restored (`api.md` 6.7), and the head's 9 V is given 100 ms (its soft start and buck). Then `!motors on`. Nothing
  is energised.
- **If it drops mid-move:** every axis stops at once (STEP and IN lines low), `!lost r motors`. A switch-off while
  parked is harmless: the rail rests on a detent with EN high.
- **Reading it:** a digital read is enough (2.76 V at 24 V). As A16 it also gives the motor supply, MOT_24V = pin
  voltage x 113 / 13 (`status sys`, later).
- **The limit and stall rules still apply while it's on:** that's the attended window.

## Fine axes (the head board's DRV8251A)

**Bridge control** (TI DRV8251A, Table 8-2): PWM between drive and brake.

| Command | IN1 | IN2 |
|---|---|---|
| forward | 1 (GPIO high) | PWM: on = brake, so the speed is 1 - duty(IN2) |
| reverse | PWM | 1 |
| brake | 1 | 1 |
| coast | 0 | 0 (the driver sleeps after 0.8-1.5 ms) |

- **Why drive/brake here, but coast on the lid:** a motor winding's inductance keeps its current flowing through the
  brake periods, so the speed follows the duty smoothly, and IPROPI reads the current all the time (in coast it reads
  0). The Peltier string has almost no inductance, which is why the lid works the other way.
- **PWM:** 40 kHz by default (`fine.pwm_hz`, 20-80 kHz). Coreless motors have very little inductance, so much lower
  means a large current ripple; at 80 kHz the driver's 2 x 200 ns dead time takes ~3 % off (the 92 % cap becomes
  ~89 %, 8.1 V). One `analogWriteFrequency()` per submodule: 28/29 one call, 36/37 one call, 24 and 25 one each.
- **Speed cap (MUST):** the rail is 9.16 V; the motors' average stays ≤ ~8.4 V, the INJS11's rating: at most 92 %
  drive (`<axis>.duty_max`).
- **Waking from coast:** the driver needs 250 µs (t_WAKE) after an input goes high, so it's held in brake for 0.3 ms
  before the first PWM.
- **Direction change: through brake (1, 1), never through coast.** A coast of ≥ 0.8 ms puts the driver to sleep, and
  the next 250 µs of drive are lost.
- **Current sense:** IPROPI = I x 1575 µA/A into 2k0 on the head = 3.15 V/A, down the ribbon and through 10k + 10 nF on
  the main board (that RC is part of the current limit: it doesn't slow the driver's regulation). The hardware limit
  is 0.93 A (VREF 2.93 V on the head), where IPROPI reads 2.93 V. At running currents (0.1-0.2 A) IPROPI varies
  ±25-45 % device to device (TI Fig. 7-9): for stalls and trends only.
- **MUST, stalls:**
  - **Why not just a high threshold:** the DRV8251A limits the *peak* current, with a 25 µs off-time, while the Teensy
    reads the *average*. A stall at 92 % duty reads 2.4-3.1 V (0.76-0.98 A), and at any duty under ~0.6 the
    locked-rotor current is only D x 9.16 V / R_m, well under the limit.
  - **Detect,** for `fine.stall_ms` (50 ms) while the axis is commanded to move, after the start-up inrush
    (`fine.inrush_ms`): the filtered current at `fine.stall_i` (0.70 A, just under a held stall's 0.76 A), or at
    `fine.stall_k` x D x 9.16 V / R_m (0.6, with `<axis>.r_m` measured at commissioning), never below
    `fine.stall_floor` (0.30 A: a stall under it is thermally harmless).
  - **Stop:** brake, then coast; that direction blocked (`!stall`). The whole stop takes < 0.1 s: three servos
    stalled together could trip the input fuses.
  - An IPROPI that reads ~0 A while driving hard is an open sense line: `!fault x sense`.
- **At rest:** ramp to zero, brake for 100 ms, then coast (IN1 = IN2 = 0): the drivers sleep, with no current and
  nothing switching. The gearbox and the stage's fine screw hold the position.
- **Direction:** an axis that runs backwards gets its sign flipped (`<axis>.sign`), not its wiring.
- **V9_OFF (pin 9), for `acq` (later):** high switches the head's 9 V buck off, through ribbon conductor 15. It goes
  low again ≥ 20 ms before the next move (the buck's 8 ms soft start, plus the drivers' wake).

## Limit switches

Eight normally-closed microswitches: the rail's MIN and MAX on pins 6 and 7, and each fine axis's hi and lo switches
in series on pins 20 / 21 / 22. A closed switch reads low; a hit, or a broken wire, reads high. The pull-up is the
external 2k on the cable side, so the pins are plain `INPUT`.

- **MUST: pin-change interrupts.** Any input going high stops that axis at once (the rail: stop stepping, a fine
  axis: speed 0 and brake); a glitch then costs only a stop. How the firmware then confirms, decides the end and
  ignores bounce: `README.md` ("The limit switches"), `api.md` 6.5.
- **No homing.** If it's ever added: approach MIN at moderate speed, back off ~0.5 mm, re-approach slowly (~0.1 mm/s)
  for a repeatable trigger point, set zero there, and move to the first detent clear of the switch.

## Rail (X coarse): TMC2209

| Quantity | Value |
|---|---|
| Lead screw | T6x1: 1 mm of travel per motor revolution |
| Motor | NEMA11, 1.8°/step (200 full steps/rev), 0.6 A, 5.5 Ω (4.4 measured), 3.2 mH |
| One full step | **5 µm** of rail travel |
| Microstepping on STEP | 1/16 (3200 microsteps/mm), interpolated to 1/256 in the driver. For smooth motion only, never for positioning: the rail always rests on the 5 µm detent grid |
| Run current | ~0.4 A RMS (`r.irun` 18); raise only if steps are lost |

The rail is open loop: its position is a count of steps since power-up. Steps come from the firmware's own step
generator, on GPT2 (the highest-priority interrupt).

### Configuration (`Serial1`, address 0, R_SENSE = 0.33 Ω; `stage/board.h`, namespace `tmc`)

- **GCONF:** `pdn_disable` (the PDN pin is the UART), `mstep_reg_select` (microsteps from the register),
  `multistep_filt`; `I_scale_analog = 0` (current from IRUN/IHOLD: VREF sits at ≥ 4.49 V through R81 22k from 5VOUT,
  which is full scale, so the reset default is the 0.66 A full scale too); `en_spreadCycle = 0` (StealthChop).
  - **MUST: never set `internal_Rsense` (bit 1), and never program the OTP.** With it set the chip pulls VREF to GND
    through 0.3-0.6 kΩ and uses that current as the reference: R81 limits the coils to ~0.45 A RMS then, but the
    motor is rated 0.6 A. GCONF is read back after every write.
- **CHOPCONF:** TOFF 4 (non-zero enables the driver), HSTRT 5, HEND 0, TBL 1, MRES for 1/16, `intpol = 1`,
  `vsense = 0`.
- **Current:** 3 x 1 Ω per coil (0.33 Ω) gives 0.66 A RMS full scale; IRUN 18 gives ~0.4 A (StealthChop wants
  IRUN ≥ 8); IHOLD 0, IHOLDDELAY 2, TPOWERDOWN 5 (Parking rule).
- **MUST, before pulling EN low:** the configuration is rewritten and verified (VERSION, IFCNT, each register read
  back, GSTAT cleared). A driver that has reset (GSTAT.reset, e.g. after a 24 V dip) would otherwise run the motor at
  full scale.
- **MUST, while enabled:** GSTAT and IFCNT polled every 100 ms. A brown-out on VS silently restores the defaults (1/8
  step, full current) with EN still low: on a reset, stop, `!lost r driver`, and configure again.
- **StealthChop at all speeds:** `TPWMTHRS = 0`; PWMCONF with `pwm_autoscale` and `pwm_autograd`. Its self-tuning,
  AT#1, runs once per MOTORS-on, on the first move: 200 ms standing still at run current (> 130 ms), then the move.
- **Starting speeds:** a few mm/s at 10-20 mm/s² (`r.vmax`, `r.acc`); raise them only while no steps are lost.

### Parking rule

The rail is only ever at rest in one state: **on a detent, with the driver off.** Detents are where the unpowered
rotor rests, every full step (a 5 µm grid). When the target speed reaches zero, the deceleration is stretched to end
on the next detent ahead, never the one behind (so the screw's backlash stays taken up the same way); the current then
ramps to zero (TPOWERDOWN ~0.1 s, IHOLD 0, IHOLDDELAY 2: over about half a second, no jerk), and EN goes high. The
detent and the self-locking T6x1 hold the position. How it shows in the record, and the phase kept across MOTORS
cycles: `api.md` 6.7.

**Why:** a powered stepper holding position is still chopped (fine jitter, hum, a magnetic field), and an unpowered
one left *between* detents jumps up to half a step (2.5 µm) when released.

### Guarding against lost steps

Conservative speeds and accelerations, and enough run current. StallGuard4 (`SGTHRS`, DIAG on pin 5) is off until
commissioning; set, a stall during a move would mean "position unknown". The microscope is the final check: a
reference feature in the image after coarse moves.

### Coarse/fine handoff in X

The rail does coarse X, in 5 µm detent steps; anything finer comes from the fine X stage, which covers far more than
one grid step plus backlash. For now the two are separate controls that add (`api.md` 8); a commanded X split into
the nearest rail grid point plus a fine offset, or small velocities going to fine X and large ones to the rail, is
for later.

## Lights (LED dimmer)

The two set-points are 12-bit PWM on pins 23 (warm) and 33 (cool) at 36 621 Hz, filtered to DC on the board. The
scale is V_set = 3.3 V x 0.6 x code / 4096 (0-1.98 V), and I_LED = (V_set - ~5 mV) / 49.9 Ω (0-39.7 mA), following the
Teensy's 3.3 V. The levels, the command and their behaviour: `api.md` 9.1. Settling is 50-75 ms: fine for dimming,
too slow to strobe with the camera. The light is DC, so it can stay on through an acquisition. Every reset and upload
goes dark, and nothing resumes (`api.md` 9.3).

## Lid

A DRV8871 H-bridge straight onto six Peltiers in series (13.7 Ω), two ADS1115 and five NTCs: three on the board
(RT1-RT3) and two probes in the medium (J2, J3: TEWA TT4-G10KC3, 10k 1 %, B 3977). The lid's own TPS5430 makes the
bridge's 9.16 V from the 24 V on the cable, which is live whenever the unit is on (SW9); the bridge's 20 kHz pulses
stay in a few-cm loop on the lid. There's no sensor on the top plate since J3 became a medium probe: the duty cap
bounds it (50 % cooling puts it at 43-51 °C uncovered). By hand, online: `api.md` 9.2.

### The heater enable (SW10)

- **SW10 is a plain input:** pin 41, `INPUT_PULLUP`; off grounds it.
- **MUST, while it reads low:** both IN pins GPIO low at once; the integrators frozen (with the temperature control);
  `sw=off`. The lid stays powered, and its temperatures are still read.
- **Going high:** 20 ms of steady high, then the drive resumes with the wake pulse.
- **D11 shows SW10 in hardware,** whatever the firmware does. SW10 off is the state to plug or unplug the lid in: the
  plug then breaks only the lid buck's idle few mA.

### Bridge control (TI DRV8871, Table 1)

| IN1 | IN2 | OUT1 | OUT2 | Mode |
|---|---|---|---|---|
| 0 | 0 | Z | Z | coast; sleeps after 1-1.5 ms |
| 1 | 0 | H | L | forward: current OUT1 -> OUT2 (PELT- -> PELT+) |
| 0 | 1 | L | H | reverse |
| 1 | 1 | L | L | brake: both low sides on |

**MUST: PWM with coast.** Forward: IN1 = PWM, IN2 = 0 (GPIO low); reverse: IN1 = 0, IN2 = PWM; off: both GPIO low.
Never PWM one input while the other is held high (that's drive/brake, with the duty inverted), and never both high.
- **Why** (the board audit, A.1.1): TI's "drive/brake works best" is advice for motors, whose winding current flows
  on through the brake periods. The string has no such current to keep: brake shorts it between pulses, and its
  Seebeck voltage then drives current backwards. That turns the string into a heat leak between board and plate (its
  K rises from 0.41 to ~0.72 W/K): heating costs 8-14 % more power, and cooling loses more (at D = 0.3 coast still
  pulls 1.5 W out of the board, brake none).
- **Which direction heats the chamber side** depends on how the modules were fitted: found once at commissioning from
  the board NTCs, and stored as `lid.sign`. The cable's IN1 line (pin 14) drives the DRV8871's IN1 pin and IN2 (pin
  15) its IN2, straight through; the Seebeck reading's sign is PELT+ minus PELT-.

### Pulse timing (DRV8871 §6.3 note 1, §6.5, §7.3.2)

- **PWM at 20 kHz:** inaudible; with no filter, the frequency doesn't change the heat; pins 14 and 15 are set
  separately (two QuadTimer channels).
- **Minimum pulse:** an input pulse needs 800 ns to be sure to register (typically 400 ns), 1.6 % at 20 kHz, so **a
  duty under 2 % is 0.**
- **Sleep and wake:** with both inputs low for 1-1.5 ms the DRV8871 sleeps (TI: 1 ms typical, 1.5 ms maximum, no
  minimum); it wakes once an input has been high for ≥ 5 µs, and drives 40-50 µs (t_ON) later. A running PWM never
  sleeps: its off-times (≤ 50 µs) are far shorter.
- **MUST, the wake pulse:** on every restart from duty 0, and after any pause of ≥ ~0.2 ms (a direction change, SW10,
  the lid coming back, a Seebeck reading, an acquisition), the active input is driven high as GPIO for 60 µs (the wake
  plus t_ON), then the PWM starts. Without it, PWM pulses shorter than 5 µs might never wake the driver.
- **Direction change:** the active input goes low first, then the other one starts with its wake pulse.

### Temperature control (later)

The plan, one step a readout (1 Hz, `api.md` 9.2):
- **Cascade.** An inner PI holds the plate (the lid board, the chamber side) at its set point with a signed duty. The
  board NTCs are its sensors, not only its guard, so the board is always at a known temperature and their calibration
  matters as much as the probes'. An outer PI moves that set point, slowly, from the medium probes against the
  medium's target (the medium follows the plate in tens of minutes, across ~20 mm of air), and the target follows the
  incubator program.
  - The plate is a weighted mean of RT1-RT3, the weights fitted once against a thermocouple on the plate. In the
    lid's 2D thermal model (30 min at 30 % heating) RT1 and RT2 sit within
    ±0.05 K of their mean, RT3 ~0.3 K below it, and the board's area mean +0.07 K above.
  - RT1 - RT3 tracks how much heat leaves the plate's edges: feed it forward rather than wait for the medium.
  - The NTCs answer a duty step within ~0.5 s, then the lid's ~7.5 min time constant, so the 1 s step costs the loop
    nothing.
- **Duty:** capped at `lid.duty_max` (50 %; the compiled ceiling is 80 %, the hardware's full drive 6.1 W); +10 K
  needs 20-50 %; under 2 %, 0.
- **Anti-windup:** the integrators frozen while the duty is at its cap, the lid is absent or SW10 off, or a limit
  clamps the target.
- **MUST, limits:**
  - **plate ≥ medium + 0.5 K:** otherwise water condenses on it and drips near the tips. "Cooling" means pulling the
    plate down to just above the medium, never below;
  - **board ≤ 60 °C** on the hottest of RT1-RT3 (below TCO1, the lid's own 72 ± 5 °C cutoff): online, `cut=hot`;
  - **medium ≤ `lid.medium_max`** (40 °C) on the hotter fitted probe: online, `cut=medium`; the outer loop will cap
    the plate's set point under it;
  - with no probe fitted there's no outer loop: the plate's set point is the target itself, and the condensation
    limit becomes plate ≥ set point - 0.5 K.
- **No response:** a plate that doesn't move with the duty (no change after ~1 min at |duty| ≥ 30 %) means coast and
  report: an open string, a lid off the stage, or TCO1 open (its 72 ± 5 °C cutoff between the bridge and the string;
  while driven, its PTC holds it open at ~1 W; it recloses only unpowered and ≤ 57 °C, so coasting resets it). Board
  NTCs at ≥ ~64 °C mean TCO1 has acted: "lid over-temperature".

### The ADCs (two ADS1115 on Wire1)

| ADC | Address | AIN0 | AIN1 | AIN2 | AIN3 |
|---|---|---|---|---|---|
| U4 (strip A) | 0x48 (ADDR to GND) | `board.edge` (RT3) | `v33`, the 3.3 V, direct | `board.a` (RT1) | `med.j3` (J3) |
| U3 (strip B) | 0x49 (ADDR to VDD) | PELT+ / 11 | PELT- / 11 | `board.b` (RT2) | `med.j2` (J2) |

- **MUST: single-shot, and the whole config register (0x01) written for every conversion.** Both ADCs return to their
  defaults (±2.048 V, single-shot) whenever the lid is plugged in or the unit powers up; at ±2.048 V an NTC channel
  saturates below 12.3 °C and then reads like an open sensor.
- **Config words** (OS = 1, single-shot, 128 SPS, comparator off):

  | Reading | PGA | Words, per MUX |
  |---|---|---|
  | NTC channels and 3.3 V | ±4.096 V | AIN0 `0xC383`, AIN1 `0xD383`, AIN2 `0xE383`, AIN3 `0xF383` |
  | PELT+ / PELT- single-ended (U3) | ±1.024 V | AIN0 `0xC783` (PELT+), AIN1 `0xD783` (PELT-) |
  | Seebeck, AIN0 - AIN1 (U3, MUX 000) | ±0.256 V | `0x8B83` (`0x8783` at ±1.024 V): PELT+ minus PELT- |

- **Conversions:** one takes 7.8 ms at 128 SPS; OS (bit 15) is polled until it reads 1, then register 0x00 (signed)
  is read, one transaction a pass of the main loop. The six channels go in four rounds, both ADCs converting at once,
  ~45 ms of every 62.5 ms slot; the ~17 ms to spare will hold the Seebeck reading. The rates and the readout (16 Hz,
  1 Hz, the mean and SD): `api.md` 9.2.
- **NTCs, ratiometric:** R = 10k x code / (code_3V3 - code), code_3V3 being U4's AIN1 at the same PGA, then T with
  each channel's own model: RT1-RT3 are Murata NCP18XH103F03RB (B25/50 = 3380 K; the "3435" in its part data is
  B25/85), the probes B 3977. Self-heating is 0.24-0.27 mW per NTC, nearly constant, so calibration takes it out
  (`api.md` 9.2).
- **The probes are optional.** With none on its pads a channel sits at code_3V3 (its 10k pulls it up), as an open
  sensor: open from the lid's first readout is "not fitted" (`med.j2=open`, guarding nothing); one that read and then
  goes open, or reads shorted, is a fault, until the lid is unplugged and plugged in again.
- **Faults:** a board NTC near code_3V3 is open, near 0 shorted; either, or `v33` outside 3.13-3.47 V, coasts the lid
  (`cut=fault`).
- **Reading U3's NTCs with the bridge coasting is optional** (since lid v15): its ground tree joins the board's at one
  point only (JP3), and no heater current flows between any ADC and its sensors, so the PWM runs through every
  conversion.

### The monitors and the Seebeck reading (later)

- **While the PWM runs, PELT+ / PELT- don't read D x VM:** between pulses the string floats, and the filters average
  that in. They're read only with the bridge stopped.
- **The Seebeck reading:** both inputs low; wait ≥ 10 ms (the filters' τ is 0.9 ms); one differential conversion, U3
  AIN0 - AIN1 at ±0.256 V (|ΔT| up to ~23 K). 11 x the result is S·ΔT (0.118 V/K across the string, plate to board).
  Then the wake pulse and the PWM again. Once a minute is plenty; each pauses the drive for ~20 ms.
- **Use:** an open-string test (~0 V while the NTCs say there's a difference of several K; an open TCO1 reads the
  same, as R12's tap is on the bridge's side of it), and a cross-check of the NTCs.
- **To check on the bench:** the open string centres on 0 V, so one ADC input sits at up to ~-40 mV, just outside the
  ADS1115's specified input range (inside its absolute maximum). If the reading is off, 390k from each of PELT+ and
  PELT- to +9 V would lift the open string to ~2 V (a hardware change, not fitted).

### Presence, faults and hangs

- **Lid present?** Both ADS1115 acknowledge whenever the lid is plugged in and the unit is on, whatever SW10's
  position. **MUST, while either doesn't answer:** both IN pins GPIO low, the integrators frozen: `mode=absent`,
  `cut=absent`. When it comes back: the configs written again (every conversion writes it anyway), `idle` from its
  first readout, nothing resumed; a drive starts again with the wake pulse.
- **The DRV8871 has no fault output.** It shuts itself down on overcurrent (3.7 A minimum) or overtemperature, and
  retries after 3 ms; faults are inferred from the temperatures (No response).
- **A hang:** the PWM timer keeps running at its last duty, at most the 6.1 W ceiling, until WDOG1 resets the Teensy
  and the lid's pull-downs coast the bridge.

## Acquisition state (z-stacks, later: `acq`, `api.md` 6.4)

"Ready to acquire" only when **all** of these hold:
1. **Rail:** parked on a detent, current ramped to zero, EN high, at least 200 ms since EN went high.
2. **Servos:** all three coasting (drivers asleep) and V9_OFF high (the head's 9 V buck off).
3. **Lid:** holding its duty, or coasting for the acquisition, its ADCs not polled meanwhile. Holding keeps 0.5-0.6 A
   pulses at 20 kHz in the string (round the slot, over the sample) and the lid's buck, all on the lid; a minute's
   coast costs a few tenths of a K (then the wake pulse, and the integrators as they were frozen).
4. **No motion command pending:** one that arrives during the acquisition is refused (`err acq`).

Still switching during one: the Teensy and Ethernet, the main board's 5 V buck, the LED set-points' PWM (filtered to
DC), the TMC2209's charge pump (its bridge off), and the lid's buck (it has no enable) and bridge, unless the lid
coasts.

## Commissioning (once per unit, into `/parameters.yaml`)

1. **The rail's detent phase** (`r.detent_phase`): MSCNT (0-1023 over 4 full steps) has its full steps at
   0 / 256 / 512 / 768 and at 128 / 384 / 640 / 896, and the detents coincide with one set. Park on a full step of the
   first set, ramp down, disable, and watch a high-contrast target at high magnification for a jump; repeat with the
   second. The set that doesn't move is the detent grid (128 per the datasheet).
2. **The fine axes' signs** (`x.sign`, `y.sign`, `z.sign`): `README.md`, bench step 4.
3. **The fine axes' winding resistance** (`<axis>.r_m`, for the duty-aware stall test): brake each motor with its
   shaft held and read IPROPI at a known duty, or measure the winding on the meter.
4. **The lid's heating sign** (`lid.sign`): `README.md`, bench step 10.
5. **The NTCs' offsets:** `api.md` 9.2, "uncalibrated" (later: the offsets become parameters).
6. **The plate weights** (for the temperature control, later): against a thermocouple on the plate.
7. **StallGuard** (`SGTHRS`, later): from a few moves at working speed, with and without a hand-held brake on the
   carriage.
8. **The LED channels:** current or light against level, including the dimmest levels if they matter (the op-amp's
   offset puts the dimmest usable level at 0.02-0.5 %, unit to unit).
