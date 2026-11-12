# Parameters file

`/parameters.yaml` at the root of the Teensy's SD card holds the unit's parameters: the drive's, and the lid's duty
cap, sign and medium limit. The firmware reads it once at start-up; a calibration will write it (not yet built). The
file to start from is `sd/parameters.yaml`: the built-in defaults, which are the precomputed model.

**Format 1** (2026-10-19; the lid's keys since 2026-10-30). Axes and signs are in the operator's frame (`api.md` 6.2):
+x right (towards the rail's bearing end), +y away from the operator, +z up. The lid's calibration and the incubator
programs will add their own sections.

## Rules

- **Every key is optional.** A key that's missing keeps its built-in default; so does one that's malformed or out of
  bounds. Whatever was refused or ignored is listed after every `!hello` as `!cfg` lines.
- **The bounds are hardware, compiled in** (`stage/board.h`): the 92 % duty cap, the 0.6 A rail current, the 80 ms
  stall limit, the lid's 80 % duty ceiling and 50 °C medium ceiling. No value in the file can cross them.
- **A file that doesn't parse is ignored whole:** every key runs on its default, and `!cfg` gives the line.
- **`!hello`'s `cfg=`** says which: `card` (the file was read), `default` (no file, or it didn't parse) or `nocard`.
- **The names are the API's.** A key's dotted path (`x.vmax`, `fine.stall_i`, `r.irun`) is its name in `get`
  (online) and `set` (later).

## The model and the calibration

The precomputed model sets each fine axis's speed against duty. Calibration replaces it with measured values, within
reason of the model.

- **`<axis>.model`:** the precomputed table, the same both ways. The firmware only uses it as the reference, and as
  the fallback.
- **`<axis>.cal`:** the tables in use, one per direction (`fwd` towards +, `rev` towards -). They start as copies of
  the model, and a calibration overwrites them.
- **Within reason** means each calibrated point lies within x/÷ `fine.cal_ratio` (default 2) of the model's speed at
  the same duty. A table that fails this, or isn't a valid table, is refused: that direction runs on the model, and
  `!cfg` says so.
- **A table** is 2-8 points: duties rising within 0-0.92, speeds never falling, in um/s. Between points the
  firmware interpolates linearly; past the last it holds the last duty.
- **`cal.source`** is `model` until a calibration writes it; the drive record's `x.cal` is 1 only once it isn't.
- **The model as shipped** is a lumped motor model from the INJS11's datasheet figure (0.074 s/60° at 8.4 V, so
  289 um/s at the 92 % cap through 16:30 and the M6x0.25 screw), with friction taking 3.8 % duty. It's a starting
  point for jog, not a measurement.

## The keys

### Shared by the fine axes: `fine`

| Key | Unit | Default | Bounds | |
|---|---|---|---|---|
| `fine.pwm_hz` | Hz | 40000 | 20000-80000 | drive/brake PWM (`hardware.md`: 40 kHz) |
| `fine.stall_i` | A | 0.70 | 0.2-0.70 | a stall: the filtered current above this... (a stall held at the driver's 0.93 A limit reads 0.76-0.98 A, so no higher) |
| `fine.stall_k` | - | 0.6 | 0.3-0.75 | ...or above stall_k x duty x 9.16 V / r_m, for part-duty stalls (the driver's 0.45 Ω, the wiring and a warm winding hold the real locked current at ~0.8 of that, so no higher) |
| `fine.stall_floor` | A | 0.30 | 0.1-0.5 | never chased below this: a stall under it stays thermally harmless |
| `fine.stall_ms` | ms | 50 | 10-80 | how long it must last |
| `fine.inrush_ms` | ms | 30 | 0-200 | not checked this long after a start or a reversal |
| `fine.vunk` | um/s | 20 | 1-100 | the speed cap while a switch is open and its end is unknown |
| `fine.cal_ratio` | - | 2.0 | 1.1-5 | how far a calibration may stray from the model |

### Each fine axis: `x`, `y`, `z`

| Key | Unit | Default | Bounds | |
|---|---|---|---|---|
| `sign` | - | 1 | 1, -1 | the motor direction that moves the stage towards + (commissioning) |
| `vmax` | um/s | 250 | 1-400 | the jog clamp |
| `acc` / `dec` | um/s² | 2000 / 4000 | 10-50000 | ramps: speeding up / slowing down (a reversal slows at dec first) |
| `duty_max` | - | 0.92 | 0.05-0.92 | the duty cap |
| `duty_min` | - | 0 | 0-0.5 | below this duty, pulse mode. Not built yet: below it the axis doesn't move. 0 = steady PWM always |
| `r_m` | ohm | 3.5 | 1-10 | the winding, for the duty-aware stall test (measure it) |
| `model.duty` / `model.speed` | - / um/s | [0.038, 0.92] / [0, 289] | a table | the precomputed model |
| `cal.source` | text | model | | who wrote `cal` |
| `cal.fwd.duty` / `cal.fwd.speed` | | the model | a table | in use towards + |
| `cal.rev.duty` / `cal.rev.speed` | | the model | a table | in use towards - |

### The rail: `r`

| Key | Unit | Default | Bounds | |
|---|---|---|---|---|
| `sign` | - | 1 | 1, -1 | 1: DIR high moves the carriage towards +x. Fixed by the design (J3 wired by colour, the TMC2209's DIR sense, the motor, a right-hand T6x1), so the same on every unit: confirm it once |
| `vmax` | um/s | 5000 | 10-20000 | the jog clamp |
| `acc` / `dec` | um/s² | 20000 / 20000 | 100-200000 | `hardware.md`: 10-20 mm/s² to start |
| `lead` | um | 1000 | 100-10000 | per motor turn (T6x1) |
| `steps` | - | 200 | 200-400 | full steps per turn |
| `microsteps` | - | 16 | 1-256, a power of 2 | per full step on STEP; the driver interpolates to 256 |
| `irun` | - | 18 | 0-28 | run current: (irun + 1) / 32 x 0.657 A rms, so 0.39 A |
| `detent_phase` | MSCNT | 128 | 0-255, whole microsteps | where the unpowered rotor rests, MSCNT mod 256: 128 per the datasheet (3.6.1, both coils at equal current), 0 if commissioning finds the other set |
| `effector` | - | min | min, max | the rail switch that rides on the effector (below) |
| `release` | um | 2000 | 100-10000 | a switch still open this far after the rail moved away from it is a sign or wiring fault |

**The effector switch.** The effector sits on the -x side of the rail's carriage, towards the motor, and carries the
rail's -x switch, MIN on J7. That switch stops -x motion of both the rail and fine x, since fine x moves the effector
too. The other switch, MAX on J8 at the bearing end (+x), rides on the carriage and stops the rail alone. `max` would
mean the effector's switch had been wired to J8 instead.

### Jog: `jog`

| Key | Unit | Default | Bounds | |
|---|---|---|---|---|
| `jog.lease` | ms | 250 | 100-500 | how long a jog holds before falling to 0 |

### The lid: `lid`

| Key | Unit | Default | Bounds | |
|---|---|---|---|---|
| `lid.duty_max` | % | 50 | 0-80 | the cap on `lid duty=` (`api.md` 9.2), either way. 50 % holds the board under ~61 °C, steady, in a 28 °C room with an insulated chamber and the string at its tolerance corner (the lid's thermal model): 51 °C in a 22 °C room |
| `lid.medium_max` | °C | 40 | 10-50 | a medium probe (`med.j2`, `med.j3`) reading this or more cuts the duty, either way, and refuses another but 0 until it's under (`api.md` 9.2: `cut=medium`). On the readout's mean, once a second. A probe that reads `open` (not fitted) guards nothing |
| `lid.sign` | - | 1 | 1, -1 | 1: driving IN1 (pin 14) heats the chamber side, so + is heat. Found at commissioning (`README.md`, bench step 10): the direction that warms `board.a` and `board.b` |

The duty is in %, as `lid duty=` and the `!lid` record have it (the fine axes' `duty_max` is a fraction).

### Others

| Key | | |
|---|---|---|
| `format` | 1 | this file's format |
| `unit` | text, < 24 characters | this unit's name, shown in `!hello` |

## What isn't here

These are fixed in `stage/board.h` from `hardware.md`, not settings:
- the TMC2209's chopper and StealthChop configuration (TOFF 4, TBL 1, IHOLD 0, IHOLDDELAY 2, TPOWERDOWN 5, PWMCONF);
- the pins and the current-sense scale;
- the 0.3 ms wake and the 100 ms brake before coast;
- the 3 ms limit confirmation;
- the lid's 60 °C board cut, its 2 % duty floor, 20 kHz PWM and wake pulse, and the NTCs' B values (the board's
  Murata 3380 K, the probes' TEWA 3977 K);
- the NTCs' two rates: each sampled at 16 Hz, and read out (the mean and SD of its 16 samples) once a second, the
  thermal loop's period and `!lid`'s (`api.md` 9.2). The loop's
  gains will be tuned for them.

## The YAML it reads

A subset, so a small parser can read it reliably:
- nested mappings by indentation (spaces, not tabs);
- `key: value`, and `key:` opening a nested block;
- `#` comments;
- double-quoted strings;
- numbers as plain decimals (no exponents);
- lists only in flow form, `[0.038, 0.92]`.

Block lists (`- item`), anchors and multi-line values are errors. 16 kB at most.
