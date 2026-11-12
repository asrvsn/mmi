# Firmware

The Teensy 4.1's firmware for the stage-top unit, built up a subsystem at a time: fw 0.9.1, `api.md` 0.11. Online:
- **the drive by hand:** `jog`, `halt`, `get`. It does nothing it isn't told to: no homing, nothing energised when
  MOTORS comes on. Losing the client that's driving halts it: its connection closing, the link going down, or its
  jogs no longer renewed (the 250 ms lease);
- **the limits and stalls, pushed, not polled** (`api.md` 6.5): each session opens with the whole state, a `!limit`
  per axis, then each change as it happens (`!limit x -` with `!limit r -` when the effector's switch, MIN, trips).
  A client mirrors that, and the jogs stay unanswered;
- **the lights by hand** (`api.md` 9.1): `light warm=<%> cool=<%>`, % of full scale (39.7 mA), held whatever the link
  or the drive do, dark after any restart; each change goes to every session as `!light`;
- **the lid by hand** (`api.md` 9.2): `lid duty=<%>`, signed (+ heats, per `lid.sign`), capped at `lid.duty_max` (50 %),
  cut at a 60 °C board NTC, a medium probe at `lid.medium_max` (40 °C), a bad sensor or a lid gone, and dropped with
  the session that set it: an attended test. The five NTCs are sampled at 16 Hz and read out once a second (a mean
  and an SD each), every readout going to every session as `!lid`.

| File | What |
|---|---|
| `hardware.md` | the hardware rules: the MUSTs, what the hardware guarantees, pins and timers, start-up, each driver's rules, commissioning |
| `api.md` | the host protocol (TCP 192.168.50.2:5000, or USB serial): what a client sends and gets back |
| `parameters.md` | `/parameters.yaml` on the SD card: every key, its default and bounds |
| `sd/parameters.yaml` | the file to put on the card: the built-in defaults, i.e. the precomputed model |
| `stage/` | the sketch (Arduino IDE): `stage.ino` and its modules, below |
| `test/` | host checks: `./test/run.sh` |

**Clients:** `../client/` (Python: the API's client rules, and a test server that stands in for the unit) and
`../app/` (the operator's window, driven with the 8BitDo, a PS5 DualSense or the keyboard). **The hardware rules
every line follows:** `hardware.md`, checked against the main and head netlists on 2026-10-18.

## The sketch

| Module | Does |
|---|---|
| `stage.ino` | safe pins first, the reset cause, `/parameters.yaml`, then the modules; the watchdog (WDOG1, 0.5 s) and the status LED (below) |
| `drive.cpp` | the 1 kHz control tick (an interrupt): MOTORS, the jog lease, the fine axes (ramps, drive/brake PWM, IPROPI stall checks), the limit switches (below) and the rail's switch asymmetry, and the limit and stall state it sends |
| `rail.cpp` | the rail: step pulses on GPT2 (the highest-priority interrupt), the TMC2209 over its UART without ever waiting, configure-and-verify before every enable, the driver's phase tracked and restored across MOTORS cycles, StealthChop's AT#1 on the first move, parking on a detent, health checks while enabled |
| `net.cpp` | the TCP and USB sessions, line framing and the online commands |
| `light.cpp` | the LED strips: the two set-points as 12-bit PWM at 36.6 kHz on pins 23 / 33 (the board filters them to DC); `light.h` has the level arithmetic and the command's parsing, tested on a PC |
| `lid.cpp` | the lid: the two ADS1115 on Wire1, one transaction a pass (each conversion with its whole configuration, its OS bit polled), the NTCs ratiometric to the lid's 3.3 V, sampled at 16 Hz and read out once a second (a mean and SD each); SW10; the DRV8871 in coast PWM at 20 kHz on pins 14 / 15, with the wake pulse; the cuts (the board's 60 °C, `lid.medium_max`, the sensors, the lid gone) and the session's ownership; `lid.h` has the NTC, readout and duty arithmetic and the command's parsing, tested on a PC |
| `params.cpp`, `yaml.cpp`, `motion.cpp`, `text.cpp` | the parameter set, the YAML subset reader, ramps and the park target, number parsing (no hardware: tested on a PC) |
| `board.h` | pins and the hardware constants no file can change |

**Nothing blocks once it runs.** The tick and the step generator are interrupts, and the main loop only polls
(Ethernet, USB, the TMC2209's UART, limit confirmation), so a command is handled within about 1 ms of arriving. The
watchdog is fed only while both the loop and the tick are alive.

## The status LED (D9)

| D9 | Means |
|---|---|
| red | starting up: from the first line of `setup()` until it can take commands (the SD card, the modules, Ethernet) |
| green | ready, nothing to do |
| blue | working: a command just arrived (shown for at least 50 ms), or an axis is moving, braking, enabling, tuning, or standing energised before its driver powers down. A held stick keeps it blue; after a jog it stays blue until the rail's driver is off (~1 s) |
| red, blinking 2 Hz | a fault that holds until a restart; the `!fault` lines after `!hello` say which. Also the rail's TMC2209 failing its check while MOTORS is on (`!fault r driver ...`): that one is retried every 2 s and stops blinking once it passes, or with MOTORS off. Blue still shows work |

D8 (green, the board's 5 V) and D16 (amber, MOTORS) are hardware.

## The limit switches

- **Each input's interrupt fires on every change.** Going high (a switch opening) stops what moves towards it at once.
- **Each change restarts a 3 ms settle.** The level counts only once it has held that long, so the contacts' bounce
  (a few ms, opening and closing) neither confirms nor releases anything.
- **A trip's end is decided at its first edge** (on a fine axis, the way it was moving). Later edges are the switch
  bouncing, so backing off is never taken for a trip at the other end, nor for a switch opening against the frame.
- **A glitch** (high for under 3 ms) stops the axis, and that's all: the block is taken back and the jog carries on.
- **`publish()` sends the state** each time it changes (`!limit <axis> <state>`, `!stall <axis> <state>`). `net.cpp`
  replays the last sent after every `!hello`.

`test/` runs all this against switches that bounce (`sim/`: 1.5 ms of chatter on every change, 20 µm of differential
travel), with glitches.

## Building and flashing

- **Arduino IDE 2** with the Teensy core (Boards Manager, PJRC's URL
  `https://www.pjrc.com/teensy/package_teensy_index.json`; 1.62.0 here) and **QNEthernet** (Library Manager; 0.37.0
  here). Open `stage/stage.ino`, board Teensy 4.1, USB type Serial, optimise "Faster" (the "nano" options drop `%f`
  from printf). Upload.
- **Or from a terminal,** in `mmi/` (`arduino-cli` ships inside the IDE:
  `/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli`):

  ```sh
  arduino-cli compile --fqbn teensy:avr:teensy41 firmware/stage
  arduino-cli board list                                          # the Teensy's port: protocol "teensy"
  arduino-cli upload  --fqbn teensy:avr:teensy41 -p <that port> firmware/stage
  ```

- **Or all of it, on a Mac or Linux:** `../write_firmware` builds, waits for the Teensy on USB (retrying until its
  button is pressed, if its program ignores USB's request), uploads, and checks that its `!hello` names the firmware
  just built and what the SD card held. With the card in the computer, it offers to copy `sd/parameters.yaml` onto
  it first. On Linux the Teensy needs PJRC's udev rules (`00-teensy.rules`), and the check its serial port's group
  (`dialout`, usually).
- **The SD card:** copy `sd/parameters.yaml` to the root of the Teensy's microSD (FAT32). Without it the same values
  run anyway, and `!hello` says `cfg=default` or `cfg=nocard`.

## Testing on a PC

`./test/run.sh` builds and runs two checks with the system's C++ compiler:
- **`logic_test`** (154 checks): the YAML reader, parameter bounds and calibration checks, ramps, the park target,
  `jog` parsing; the lights' levels and `light`'s parsing; the lid's NTCs (25, 37 and 60 °C by each one's beta,
  open, short), its readout (the mean; the SD about the window's line, so a ramp isn't noise; 16 samples 62.5 ms apart
  cancel 50 and 60 Hz, 10 a second don't), its duty steps under the cap and the floor, and `lid`'s parsing; `get`'s
  values read back into the same parameters; and `sd/parameters.yaml` being exactly the built-in defaults.
- **`sim_test`** (34 scenarios): `drive.cpp`, `rail.cpp`, `light.cpp` and `lid.cpp` unmodified, against a mocked Teensy,
  a simulated TMC2209 on the UART (MSCNT included), simulated stages, bouncing switches, a rotor that jumps if
  energised off its phase, and the lid (its two ADS1115 on a mocked Wire1, five NTCs with noise and mains pickup on
  demand, SW10, a board the Peltiers heat). They cover:
  - MOTORS on (nothing energised) and off; no jump across a MOTORS cycle, at most one at first boot;
  - jog ramps and reversals through brake; halt and its hold, and the same halt when the driving client is lost or
    its lease runs out;
  - every switch bouncing as it trips and as it's backed off, with the exact events each sends; fine x on its own +
    end with the effector on MIN (blocked both ways); glitches on y and MIN;
  - stalls and their lifting; parking on a detent; a wrong sign, a brown-out, the head unplugged, a silent driver;
  - the lights: dark at boot, a level as its pin's PWM, held through a halt and MOTORS off;
  - the lid: the readouts and `!lid` each second, the mean through noise and pickup, the SD not a warming board's
    rise; probes unfitted, come loose or shorted; the wake pulse, coast PWM, reversals through both low; the cap,
    `lid.sign`, SW10; each cut (board, medium, sensor, 3.3 V, unplugged) refusing until it clears; a duty ending with
    its session only.

  All the while it checks that no MUST is broken (the lid's too: never both inputs high, both low within 2 ms of
  SW10 off and within 250 ms of the lid going, a wake pulse before every PWM start, the whole ADC configuration with
  every conversion). `SIM_VERBOSE=1` prints the events.

## First power-up, on the bench

Over USB first: the Arduino serial monitor (newline endings) or `screen /dev/cu.usbmodem* 115200`. A `jog` typed by
hand moves for one lease (250 ms) and then halts (`!halt on cause=lease`) until a plain `jog`: a nudge, no more. So
motion is checked with the gamepad, from step 3, with the USB monitor left open: every event shows there too.

1. **MOTORS off, flash, connect.** `!hello`, then any `!cfg` lines, then `!limit x 0` and so on: every axis's
   limits, each `0` with the head plugged in. A switch reading open at rest is wired to NO.
2. **MOTORS on.** `!motors on` follows within ~0.2 s, and `r.drv=ok` once the TMC2209 has been configured and verified
   over its UART. Nothing moves or energises. The first rail move afterwards waits ~0.2 s while StealthChop tunes
   (AT#1).
3. **The gamepad:** the Pi or a PC at 192.168.50.1, then `python -m mmi_app` with no `[view]` rotation or mirror
   (`../app/README.md`; `../start_application` sets up the link first). With the 8BitDo or the
   DualSense plugged in, the window's corner shows a gamepad and the hint says HOME (PS); the drive line shows what
   each stick sends (up = +). Gently: half a stick is ~20 µm/s.
4. **The signs, one axis at a time, slowly,** in the operator's view (`api.md` 6.2): right stick right = +x, right,
   towards the bearing; right stick up = +y, away from you; left stick up = +z, up. Flip `x.sign` and so on in
   `parameters.yaml` where it's backwards. The rail: the right trigger should move it right; `r.sign` is fixed by the
   design, so this only confirms it.
5. **The rail's switches, before any long rail move.**
   - **MIN, the effector's:** drive the rail slowly left (left trigger) and press it by hand. `!limit x -` and
     `!limit r -`, the rail stops, and the right stick's -x doesn't move fine x either: the app shows LIMIT on both.
   - **MAX, the carriage's:** drive right and press it. `!limit r +`; only the rail refuses +x.
   - **A `!fault r switches` here** means a sign or the MIN/MAX leads are swapped. It freezes the rail until a
     restart, by design.
6. **The fine switches:** press each while driving towards it: `!limit x +` and so on, then let it go: `!limit x 0`.
   Pressed while nothing moves, it's `!limit x ?`.
7. **halt:** press B (Circle) while moving, outside the menu. Everything stops at once, `!halt on`, and nothing moves
   until every stick and trigger has been let go. Typed on USB, `halt` does the same, and a plain `jog` releases it.
8. **The current:** `watch drive 5` on USB while driving: `x.i` reads ~0.05-0.3 A moving (IPROPI is ±25-45 % unit to
   unit).
9. **The lights:** `light warm=50`.
   - **Before the strip goes on J16:** the test point SP-W (TP7, warm's filtered set-point) reads 0.99 V, settled
     within ~75 ms; SP-C (TP8) for `cool=`. With no strip nothing flows, so IS-W and IS-C read 0.
   - **With it:** IS-W (TP9, warm's sense) reads ~0.985 V, i.e. 19.7 mA (49.9 mV per mA); IS-C (TP10) for cool. Below
     ~0.25 % both stay dark (the sink's offset).
   - From the app: its menu, Light calib.
10. **The lid** (lid board assembled, `../hardware/lid/heating_pcb/assembly.md`; SW10 OFF to plug it into J20):
    - **`status lid`:** `mode=idle`, and the five temperatures within ~1-2 K of a room thermometer (the board ~1 K
      over the room, from the lid's own buck); `v33` 3.20-3.40 V. Each `.sd` a few mK: tens of mK on a still lid
      means pickup, or a joint. An unplugged probe reads `open`; the lid unplugged, `mode=absent`. The USB monitor
      shows a `!lid` a second.
    - **The heating sign:** SW10 ON, then `lid duty=30` for ~30 s, watching `board.a` and `board.b` in the `!lid`
      lines. If they fall, put `lid.sign: -1` in `parameters.yaml`, restart, and check again: + must heat. `lid off`.
    - **The current:** `lid duty=50` adds ~0.14 A on the 24 V supply (3.4 W).
    - **SW10 OFF while it drives:** `sw=off`, the current drops at once; ON again, it comes back. A USB session
      closing (the monitor shut) drops the duty: `cut=closed`.
    - **The medium's limit:** a probe dipped in water over 40 °C while a duty runs: `cut=medium` at the first readout
      that says so, and `lid duty=10` refused, `err medium ...`. Pulling a probe's lead off: `mode=fault`,
      `med.j2 open`.
    - From the app: its menu, Temp calib.
11. **Losing the client mid-move:**
    - **Pull the Ethernet cable:** everything stops within ~0.13 s and USB shows `!halt on cause=link`. Plug it back
      in: the client reconnects within a few seconds, and nothing moves until the sticks have been let go.
    - **Quit the client** (Ctrl-C): `cause=closed`, at once.
    - **Suspend it** (Ctrl-Z): `cause=lease`, 0.25 s later. Then `fg`, and let go to resume.
