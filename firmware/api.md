# Host API

The protocol between the stage-top unit and whatever drives it, over the Ethernet link. The Teensy 4.1 is the
**server**; the Raspberry Pi controller, or any PC, is the **client**.

**Status: draft 0.11 (2026-10-30).** Sections 1-5, the framing, apply to every subsystem. Section 6 is the drive (fine
XYZ and the rail); section 7, its calibration, comes later. Section 9 is the incubator: the lights (9.1) and the lid
by hand (9.2) are online; the lid's temperature control and the programs only have their names reserved so far (9.3).

**Online in the firmware (`stage/`, fw 0.9.1):** `jog` and `halt`, with the events and protections behind them (the
limits and stalls among them, mirrored by the client: 6.5); `light`, the LED strips by hand (9.1); `lid`, the
Peltiers by hand, and the lid's temperatures, read out once a second (9.2); plus `get` (the parameters in force),
`ping`, `help`, `status drive|light|lid`, `watch drive|light` and `bye`. The rest of section 6 is the spec the
commands still to come will follow. **The drive does nothing it isn't told to:** no homing, nothing energised at
MOTORS-on, motion only on a velocity command. Parameters: `parameters.md`.

- **The hardware rules the firmware keeps** (pins, start-up, the drivers, every MUST):
  `hardware.md`. Nothing here overrides them.
- **Day-to-day use** (the MOTORS and LID HEAT switches): `../operation/README.md`. **The gamepad:** `../app/README.md`.
- **The reference client:** `../client/`, a Python library that keeps section 8's client rules for any front end,
  with a test server that stands in for the unit; and `../app/`, the operator's window built on it.
- **Not this:** a pin-level USB console for the assembly checks; the boards' `assembly.md` guides run their checks
  with this protocol's own commands.

## 1. The model

- **The server owns every control loop and every safety rule.** The client is the user interface: it turns the
  gamepad, mouse and keyboard into requests, and shows what the unit reports.
- **Requests are physical:** µm/s, µm, °C, light levels. The client never sees a duty, a step, a pin or a register.
  The firmware maps each request onto the hardware through its own calibration, and clamps or refuses what it can't
  do safely.
- **Asynchronous at both ends.** The client may send at any time without waiting for answers. The server answers
  commands at once and in order, and pushes events whenever something happens: a limit, a stall, the MOTORS switch,
  the end of a move. Nothing has to be asked for to be heard about.
- **State is mirrored, not polled.** What the client needs to know at all times (the limits and stalls, MOTORS, a
  halt) the server sends in full when the session opens, then each change as it happens (section 3, rule 6). The
  client keeps a copy from those events alone, so nothing rides on the jogs, and the jogs stay unanswered.
- **No setting can override a MUST in `hardware.md`:** the duty and current caps, the limit and stall
  stops, the watchdog, the lid's limits. A parameter only moves a value within those bounds.
- **The drive and the incubator are independent.** Motion commands, their stops and their timeouts act on the motors
  only. The lid, the lights and a running incubator program carry on whatever the drive or the link does. The one
  exception is a test: the lid's duty set by hand ends with the session that set it (9.2).

## 2. Transport

| | |
|---|---|
| Link | Ethernet, a direct cable (auto-MDIX). Static: unit 192.168.50.2/24, host 192.168.50.1/24 |
| Socket | TCP, port 5000 |
| Bench | the Teensy's USB serial, same protocol |

- **One TCP session at a time; a new connection replaces the old one.** The old client gets `!bye replaced` and is
  closed; if its jog was in force, the drive halts (below). Why: a client that crashed can leave a half-open
  connection behind, and refusing newcomers would lock the operator out until it timed out.
- **Nagle off at both ends** (`TCP_NODELAY`). A jog is ~30 bytes and must leave at once, not wait for the ACK of the
  one before. Python: `s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)`. The firmware sets it on its socket
  (QNEthernet) and writes each line whole.
- **Losing the client that's driving halts the drive,** exactly as `halt` (6.4): everything stops at once, without
  ramps, and holds until an all-zero jog. That's any end of a session while its own non-zero jog is in force:
  - **the client closes the connection, or its program dies** (the FIN or RST arrives): on the next pass of the main
    loop, well under a millisecond;
  - **the link goes down** (the cable pulled at either end, the client switched off): QNEthernet reads the PHY's link
    bit every 125 ms, so within ~0.13 s. No FIN can come then, so the server aborts the session itself;
  - **it's replaced** by a new connection, says `bye`, or stops reading (below).

  `!halt on cause=<closed/link/replaced/bye/stuck>` goes to any other session, the next client finds `halt=1` in its
  `!hello`, and its first all-zero jog releases it. A session that ends while nothing of its own is in force changes
  nothing. **The same ending drops a lid duty that session set by hand** (9.2): `!lid ... duty=0 cut=<cause>`.
- **A client that hangs with its link up** (no FIN, no link loss) shows only in its jogs: they stop renewing. When
  the lease runs out (250 ms, 6.4), the drive halts in the same way, with `!halt on cause=lease`.
- **The server never blocks on a reader, and never drops an event silently.** A line goes into the session's 8 KB send
  buffer whole, or not at all; a TCP session that loses one (its client has stopped reading) is closed at once,
  `cause=stuck` (a halt if its jog was in force). So a client either gets every event, or reconnects and starts again
  from the snapshot (section 3, rule 6). `watch` records are skipped while the buffer is more than half full, so a
  slow reader loses a record, not the session. The USB port only gets output while it has room for it; with nobody
  reading for 1 s, its buffer is cleared.
- **USB serial is a second session, for the bench:** the same commands, events and rules; closing the port ends it
  like a closed connection (a halt if its jog was in force). Don't drive from both at once.
- **Why TCP, not UDP:** on a direct cable nothing is lost, so TCP's retransmission never holds a jog up, and the
  connection itself tells the server whether a client is there.

## 3. Lines

Every message is one line of printable ASCII, at most 512 characters, ending in `\n` (a `\r` before it is ignored).
Words are separated by one or more spaces. Empty lines are ignored.

### Commands (client to server)

    [tag] verb [arg ...]

- **tag:** optional, an unsigned integer of up to 9 digits. The server copies it into the reply, and into the `!end`
  of anything the command started.
- **verb:** a lowercase word: `jog`, `stop`, `status`.
- **args:** words, numbers or `key=value` pairs, as each command defines.
- **Numbers** are plain decimals: an optional sign, digits, an optional fraction (`250`, `-12.5`, `+0.8`). No
  exponents and no units: every key has a fixed unit (section 5). **Lists** are numbers joined by commas, without
  spaces: `d=0.15,0.3,0.92`.
- **Strict:** an unknown verb or key, or a malformed value, is an error, never ignored. `jog X=10` fails instead of
  doing nothing.

### Replies, info and events (server to client)

The first character says what a line is:

| First | Kind | Form | When |
|---|---|---|---|
| `@` | reply | `@<tag> ok [key=value ...]`, or `@<tag> err <code> [text]` | one per command, except `jog` |
| `#` | info | `#<tag> <data>` | the body of a multi-line answer, just before its `@` |
| `!` | event | `!<name> [args]` | whenever something happens |

Without a tag, a reply reads `@ ok` or `@ err ...`, and an info line `# ...`.

1. **Every command except `jog` gets exactly one reply, in the order the commands arrived.** A client can match them
   by counting, with or without tags. `jog` is a stream of set-points and is never answered (6.4).
   - **Why reply at all:** TCP already delivers every line, in order, so a reply isn't about delivery. It carries
     the server's decision (accepted, or refused and why) and any data. Without one, a refused `goto` would look like
     a rail that never moved.
2. **A reply means accepted or refused, never finished.** Anything that takes time (a move) replies at once
   and reports its end later, in an `!end` event.
3. **Replies are prompt** (aim: < 2 ms). The network task never waits on motion, the lid or the SD card.
4. **A multi-line answer goes out as one block.** Events come before or after it, never inside it.
5. **Clients ignore keys they don't know** in server lines, so the server can add fields without breaking them.
   Commands stay strict, so a typo is caught; replies and events can grow.
6. **State events: the whole state at the start, then every change.** An event that carries a state (`!limit`,
   `!stall`, `!motors`, `!halt`, `!light`, `!lid`) goes out whenever that state changes, and only then. The lid's
   temperatures are part of its state, so `!lid` also goes out with every readout of them, once a second (9.2). The
   whole of it is in the session's opening lines: `motors=` and `halt=` in `!hello`, then a `!limit` for every axis, a
   `!stall` for any axis with one, `!light` and `!lid` (section 4). Each event states the whole of its part (an axis's
   limits, not a difference), and they arrive in the order things happened, so a client that applies them in turn always
   holds the server's state. It never needs to poll; `status drive` says the same, for a check.

## 4. The session

| Command | Reply | |
|---|---|---|
| (connecting) | `!hello api=0.11 fw=<version> unit=<id> reset=<cause> cfg=<card/default/nocard> clock=<set/unset> motors=<off/wait/on> halt=<0/1>` | before anything else, then any `!cfg` and latched `!fault` lines, then the state: `!limit` for each axis, `!stall` for any axis with one (6.5), `!light` (9.1) and `!lid` (9.2) |
| `ping` | `@ ok t=<ms>` | t: ms since the Teensy started |
| `help` | `#` one line per command, then `@ ok` | |
| `bye` | `@ ok`, then the server closes | |

- **reset:** why the Teensy last started: `power`, `wdog` (WDOG1 fired, so something hung; `hardware.md`
  wants it reported) or `soft` (a software reset, or a firmware upload).
- **cfg:** whether `/parameters.yaml` was read from the SD card (`card`), or the built-in defaults run (`default`: no
  file, or it didn't parse; `nocard`). Each `!cfg` line after it names a key that was refused or ignored
  (`parameters.md`).
- **clock:** whether the RTC has been set since power-up (9.3).
- **halt:** `1` if a halt holds, e.g. because the previous client was lost mid-move (section 2): the first jog must be
  all zero.
- **Versions:** a client checks `api`'s major number. Commands, keys and events can be added within one; anything
  that changes the meaning of an existing one raises it.
- **No heartbeat, even while moving.** A jog carries its own lease (6.4), a `move`, `goto` or `home` ends by itself,
  and losing the driving client halts (section 2).
- **Keep-alive:** a pulled cable gives the client no error for minutes (TCP keeps retrying), so a client that wants
  to know the unit is there pings it. `mmi_client` pings after 1 s with nothing from the unit, and drops the link if
  no reply comes within 1 s.

## 5. Units and names

| Quantity | Unit |
|---|---|
| position, distance | µm |
| velocity | µm/s |
| acceleration | µm/s² |
| jerk | µm/s³ |
| current | A |
| temperature | °C |
| durations | ms (µs where marked) |
| rates | Hz |
| booleans | 0 / 1 |

- **Axes:** `x`, `y`, `z` (the fine stages) and `r` (the rail: coarse X).
- **Keys** are lowercase. Per-axis keys are dotted (`x.vmax`, `r.pos`), so a client can split them into nested
  dictionaries.

## 6. The drive

### 6.1 The axes

| | `x`, `y`, `z` (fine) | `r` (the rail, coarse X) |
|---|---|---|
| Drive | INJS11 gearmotor (its servo board bypassed), 16T to 30T onto the knob of a MAX-B34C-13S stage | NEMA11 (1.8°) on a T6x1 leadscrew, TMC2209 at 1/16 step |
| Lead | M6x0.25: 250 µm per knob turn, ~133 µm per servo output turn | 1 mm per turn: 5 µm per full step, 0.3125 µm per microstep |
| Travel | 13 mm; the switches trip at ±6.6 mm about mid-travel | 150 mm stroke, between its MIN and MAX switches |
| Fastest | ~300 µm/s at the 92 % duty cap, unloaded (INJS11: 0.074 s/60° at 8.4 V); to measure under load | a few mm/s to start (`hardware.md`); the stepper tester went to 5 mm/s |
| Slowest | ~2 µm/s, once the motor is pulsed below a crossover of ~20-40 µm/s (6.10; not built yet) | creeps smoothly; the tester's slowest jog was 20 µm/s |
| Position | **none.** There's no sensor: speed comes from a calibration (section 7), open loop | counted in steps since power-up (no homing); at rest always on a detent, 5 µm apart |
| At rest | brake ~100 ms, then coast; the gearbox and screw hold it | parked on a detent, driver off |

- **The chain:** the rail carries fine X, which carries Z, which carries Y, which carries the pipette arm.
- **Fine X and the rail overlap** between ~20 and ~250 µm/s, so either can make a slow X move.

### 6.2 Frame and signs

**The frame is the operator's view, looking at the unit from the capillary row's side:**

| Axis | + is | In the CAD (`mmi.f3d`) |
|---|---|---|
| `x`, `r` | right: the carriage towards the rail's bearing end. -x is left, towards the motor | -x of the CAD |
| `y` | away from the operator, towards the rail. -y is towards the operator | -y of the CAD |
| `z` | up, away from the coverslip | +z of the CAD |

- **It's right-handed** (x × y = z): the CAD's frame turned 180° about z.
- **No homing.** `r.pos` counts from power-up; the rail's MIN (-x) and MAX (+x) switches are limits only (6.5).
- **Coarse and fine X add:** +x on the fine stage is +x on the rail.
- **The signs.** The fine axes' are set at commissioning (`x.sign` ..., `hardware.md`: the servo leads'
  polarity is arbitrary). The rail's follows from the design: J3's wiring by colour, the TMC2209 (DIR low counts its
  phase up), the motor and a right-hand T6x1 screw. So `r.sign` is the same on every unit, to confirm once (6.5 catches
  it if wrong) and then leave.
- **It's the unit's frame, not the image's.** The microscope may show the image turned or mirrored; the client maps
  screen directions onto x and y (section 8).

### 6.3 What a joystick command is: a velocity

**The stick's deflection asks for a velocity. Acceleration is a limit the server applies, not something the client
sends.**

- **It's how stages and manipulators work:** deflection is speed, centred is stopped, and the controller ramps
  inside its own acceleration limit (Prior's `VS`, ASI's `VECTOR`, Zaber's `move vel`; micromanipulators' dynamic
  mode).
- **People steer well with rate control while they watch.** Acceleration control (a second-order task) makes the
  operator anticipate and overshoot; position control suits only a short range, here the D-pad's nudges (`move`).
- **It fails safe and is idempotent:** zero is rest (a centred stick, a dropped link, an expired lease), and each jog
  states the whole wanted state, so a repeat is harmless and nothing drifts.
- **It fits the hardware:** the fine axes have no position sensor, and the rail's step generator ramps to a target
  speed.

Two consequences for the message:
- **One vector per message, not one axis per message.** A `jog` carries every axis at once, so an XY diagonal stays
  straight (both components change together), and an axis the client forgets is stopped, not left running.
- **In µm/s at the sample, not in stick units or duty.** The client scales the stick to a physical speed (its speed
  ranges, perhaps the objective); the firmware turns µm/s into duty, pulses or steps through its calibration. So X
  and Y move alike for the same deflection even if their motors differ, coarse and fine X take the same numbers, and
  a script can ask for exactly 10 µm/s.

### 6.4 Commands

| Command | Does | |
|---|---|---|
| `jog [x=v] [y=v] [z=v] [r=v]` | sets the four target velocities (µm/s); an axis left out gets 0; a non-zero one not renewed within `jog.lease` ms halts; no reply | **online** |
| `status drive` | the drive record (6.6) | **online** |
| `watch drive <Hz>` | the drive record as an event, at that rate (0 = off, at most 50) | **online** |
| `halt` | stops every axis now, with no ramp, and holds until the operator lets go | **online** |
| `stop [axis ...]` | ramps to a stop (every axis if none is listed) | later |
| `move axis=d ... [v=speed]` | a relative move by d µm | later |
| `goto r=p [v=speed]` | an absolute rail move (needs a position reference) | not planned now |
| `home r` | homes the rail | not planned now: the drive moves only when told |
| `acq on` / `acq off` | quiet for an acquisition / back to normal | later |
| `get [key ...]` | the parameters in force (6.9) | **online** |
| `set key=value ...` / `save` | changes them / writes them to the card (6.9) | later |
| `cal <axis> ...` | the fine axes' calibration (section 7) | later |

#### jog

    jog x=40 y=-12.5

- **Sets all four target velocities;** an axis left out gets 0. `jog` alone sets all four to 0: a ramped stop.
- **Unanswered.** A jog is one sample of a stream, so it gets no reply. What the client needs to know arrives as
  events and in the record instead:
  - a non-zero jog that can't run sends `!jog refused <why>` once per reason: `motors-off`, `motors-wait` (MOTORS
    just switched on), `halted` (6.4, halt);
  - a malformed jog sends `!error jog <code> <text>`, at most once a second;
  - a blocked direction shows as `<axis>.blk` in the record, after the `!limit` or `!stall` that caused it.
- **Clamped** to each axis's `vmax`.
- **Ramped:** each axis goes from its current velocity towards its target at `acc` while speeding up, and at `dec`
  while slowing down (a reversal slows to zero at `dec`, then speeds up at `acc`). Every axis's speed is continuous,
  whatever the targets do. (The ramps are trapezoidal; S-curves on the rail, `r.jerk`, can come later.)
- **Leased:** the targets hold for `jog.lease` ms (default 250) after the command. A non-zero vector not renewed by
  then halts the drive, as `halt` does (held until an all-zero jog), with `!halt on cause=lease`: its client has hung,
  or its link has stalled. So while any component is non-zero, the client re-sends the vector at 20-50 Hz, changed or
  not, and it ends a move with an all-zero jog, never by going quiet.
- **Blocked half-spaces** (a limit or a stall, 6.5): a component into one is ignored; away from it is allowed.
- **A refused or malformed jog** sets every jog target to 0. An all-zero jog is always accepted: it asks for nothing,
  and it releases a halt.
- **Later, with `move`:** a 0 won't disturb a move running on that axis; a non-zero component takes the axis over.

#### halt

    halt
    @ ok

- **Now:** both bridges of every fine axis brake at once (then coast after 100 ms, the usual rest), and the rail
  stops stepping without a ramp or parking. The jog targets go to 0. It's done in one go, before the reply is even
  formatted.
- **A lost client halts too** (section 2), and so does a lease running out (6.4, jog): the same stop and the same
  hold, with `!halt on cause=<why>`.
- **Then it holds** (`!halt on` to every session, `halt=1` in the record): non-zero jogs are ignored, with
  `!jog refused halted`, until an all-zero jog arrives, i.e. until the operator has let go of the sticks and
  triggers. Then `!halt off`, and the next deflection moves again. Jogs already on their way when halt went, or a
  stick still held, can't start anything.
- **It never waits behind other messages.** The server has no queue of work: every command only sets state (a
  target, a flag) and is handled in microseconds, while the motion itself runs in the tick and the step interrupt. So
  lines that arrived before `halt` are dealt with almost at once, each just overwriting the jog target, and then the
  halt takes effect. The halt's latch covers whatever comes after.
- **What the client must do** (section 8): send `halt` the moment the button goes down, straight to the socket, not
  through any queue behind jogs. Keep only the latest jog vector (a mailbox, not a queue), so jogs can never pile up.
  With `TCP_NODELAY` the line leaves at once; on the direct cable it arrives in well under a millisecond.
- **The hardware stop is MOTORS** (SW11): it removes the motors' supply whatever the firmware or the link do. `halt`
  is the operational stop; neither touches the lid, the lights or an incubator program.
- With MOTORS off it still holds, harmlessly.

#### move and goto

- **`move x=5`** moves x by +5 µm. Several axes may be listed. `v=` sets the peak speed (default: each axis's
  `vmove`), clamped to `vmax`.
- **On the fine axes it's open loop.** The distance comes from the calibration, good to ~±10-20 %: a nudge, not a
  measurement.
- **On the rail** the target is rounded to the detent grid, 5 µm apart (6.7); a move under 2.5 µm does nothing.
- **Another `move` on an axis already moving by `move` adds to its target,** so fast D-pad presses accumulate. On a
  jogging axis it's `err busy`.
- **`goto`** needs a position reference, i.e. homing, which isn't planned now.
- `move` replies at once; `!end <axis> move <result> [tag=]` follows.

#### acq

- **`acq on`:** everything ramps to a stop and parks, then the head's 9 V goes off (V9_OFF). Once the unit is quiet
  (`hardware.md`, "Acquisition state": the rail parked with EN high for ≥ 200 ms, the drivers asleep,
  V9_OFF high), `!acq ready`. Motion commands meanwhile get `err acq`.
- **`acq off`:** the 9 V comes back. The firmware holds the first move for the ≥ 20 ms the buck and the drivers need,
  so the client needn't wait.
- With MOTORS off the head is unpowered anyway, so `acq on` is ready at once.

### 6.5 Limits, stalls and the MOTORS switch

**The one asymmetry: the rail's two switches.** The effector sits on the -x (left, motor) side of the rail's carriage,
and carries the rail's -x switch. So:

| Switch | Where | Stops, and blocks the half-space of |
|---|---|---|
| fine `x`, `y`, `z` (hi + lo in series) | the stage's own ends | that axis, towards the end it hit |
| rail MIN (J7), -x, the motor end | **on the effector** | **all -x motion: the rail and fine x**, since fine x moves the effector too |
| rail MAX (J8), +x, the bearing end | on the carriage | the rail's +x motion only |

`r.effector` (`parameters.md`, default `min`) says which switch is the effector's.

**A limit and a stall behave alike:** each makes a half-space of commands disallowed, ignored until it disengages. A
switch disengages when it closes again; a stall when the axis has run the other way for 50 ms.

- **A limit switch opening stops that axis at once** (`hardware.md`: from the pin interrupt). While it's
  open that direction is blocked; moves away are allowed. Once it has closed again, the block lifts.
  - **The end:** the rail's MIN and MAX are separate inputs, so it always knows. A fine axis's two switches share one
    input, so the firmware infers the end from the way the axis was moving when it opened.
  - **`?`:** a fine-axis input found open while still (at power-up, or a broken wire). The end is unknown, so both
    directions are allowed, at no more than `<axis>.vunk`, until it closes.
  - **Bounce:** a switch's contacts bounce for a few ms each time they open or close. The input counts only once it has
    held for 3 ms, so a bounce never confirms or releases anything. The end a trip blocks is decided once, at its first
    edge: backing off a switch (whose contacts bounce as they close) is never taken for a trip at the other end, nor
    for a switch opening against the frame (below).
  - **A glitch** (an input high for less than 3 ms, e.g. noise) stops the axis, then nothing more: no event, the block
    taken back, and the jog carries on.
  - **The rail after a limit hit** has stopped without a ramp, so its count may be off (`!lost r limit`). It's only a
    count for now: nothing uses it.
  - **A switch that contradicts the frame freezes the axes involved** until a restart (`!fault r switches`): one that
    opens while everything involved moves away from its end, one still open `r.release` µm after the rail moved away
    from it, or MIN and MAX open together (the head unplugged). These catch a wrong `r.sign` or `x.sign`, or swapped
    switch leads, at commissioning, before anything runs into an end.
- **The limit state, per axis: `!limit <axis> <state>`,** sent whenever it changes, and for every axis after each
  `!hello`. The server works out what each switch means for each axis, so the client needn't know the mechanics:
  - **`<state>`** is the half-spaces of that axis its limit switches block now: `0` (none), `-`, `+` or `-+`. A `?`
    follows when the axis's own switch is open at an end the firmware can't tell: `?` alone (both ways, slowly), or
    `-?` when the effector's switch blocks -x as well.
  - **The effector's switch counts on fine x too.** MIN opening sends `!limit r -` and `!limit x -` together, and
    closing, `!limit x 0` and `!limit r 0`. With fine x on its own + end as well, fine x is `-+`: it can go neither
    way, and only the rail can back it off.
  - **The rail's adds `pos=`,** where it is (µm, counted since power-up).
  - Only confirmed trips count: a glitch never shows.
- **A stall** (a fine axis's IPROPI; the rail's StallGuard is off until commissioning) stops that axis within 0.1 s,
  and `!stall <axis> <dir>` follows. That direction stays blocked until the axis has run the other way, and then
  `!stall <axis> 0`. Like `!limit`, the event states the axis's whole stall state (`0`, `-`, `+`, `-+`), and any
  axis with one is told after each `!hello`.
  - **While a fine axis pulses** (slow motion, 6.10) each pulse runs at the current limit, so the firmware can't use
    the current to tell a stall from a move. There's little to protect at that duty (~2 % on time), and the operator
    is watching: it's a gap to close on the bench, not a hazard.
- **MOTORS (SW11) off:** `!motors off`. Every axis is `off`, and a non-zero jog gets `!jog refused motors-off`, other
  motion commands `err motors-off`. A drop mid-move stops everything and loses the rail's position
  (`!lost r motors`).
- **MOTORS on:** `!motors on` once the firmware has debounced it and the head's 9 V is up, ~0.15 s
  (`hardware.md`, "MOTORS switch"). Until then the record shows `motors=wait`. Meanwhile the TMC2209 is
  configured and verified over its UART: nothing is energised (6.7).

### 6.6 The drive record

`status drive` replies with it, and `watch drive <Hz>` sends it as `!drive <record>`, the first one at once. It's one
line, every axis sampled at the same instant:

    !drive t=81234 motors=on halt=0 acq=off x.mode=jog x.v=40.0 x.d=0.160 x.lim=0 x.blk=0 x.i=0.140 x.cal=0 y.mode=idle ... r.mode=idle r.v=0 r.pos=37215 r.homed=0 r.lim=0 r.blk=0 r.drv=ok

| Key | Values | |
|---|---|---|
| `t` | ms | since the Teensy started |
| `motors` | `off`, `wait`, `on` | SW11, as the firmware has acted on it |
| `halt` | `0`, `1` | a halt holding (6.4) |
| `acq` | `off`, `wait`, `ready` | |
| `<axis>.mode` | `off`, `idle`, `jog`, `stop`, `wait` (the rail, configuring or tuning), `fault` (the rail) | `idle`: at rest (fine axes coasting; the rail parked, its driver off). `stop`: braking, parking, or standing energised before the driver powers down |
| `<axis>.v` | µm/s | the velocity commanded now, after ramps and clamps. On the fine axes that's what the firmware asks for, not a measurement |
| `<axis>.lim` | `0`, `-`, `+`, `-+`, then `?` for an unknown end | what the limit switches block, as `!limit` says it (6.5): the effector's switch on `x` too |
| `<axis>.blk` | `0`, `-`, `+`, `-+` | every blocked half-space: a limit, a stall, a fault |
| `x.i`, `y.i`, `z.i` | A | the motor current (IPROPI, filtered). ±25-45 % from unit to unit: for trends and stall margins |
| `x.d`, `y.d`, `z.d` | - | the signed duty being driven (motor frame), for bring-up |
| `x.cal`, `y.cal`, `z.cal` | `0`, `1` | `1` once a calibration has replaced the model (`parameters.md`) |
| `r.pos` | µm | counted since power-up |
| `r.homed` | `0` | always, for now |
| `r.drv` | `ok`, `off`, `wait`, `noreply`, `echo`, `crc`, `version`, `readback`, `ifcnt`, `gstat`, `reset`, `drv_err`, `timer` | the TMC2209: what its last check found |

### 6.7 How the rail comes to rest, and wakes

Not commands, but they show in the record:
- **Coming to rest:** when the rail's target speed reaches 0, its deceleration is stretched to end on the next detent
  ahead (never backwards), the current ramps to zero, and the driver switches off (`hardware.md`,
  "Parking rule"). `r.mode` is `stop` until the driver is off, then `idle`. A jog arriving meanwhile carries straight
  on.
- **The detents** are where the unpowered rotor rests. The TMC2209 datasheet (3.6.1) puts them where both coils carry
  the same current, MSCNT 128, 384, 640 and 896 (`r.detent_phase: 128`). The firmware tracks the driver's phase
  through every step it sends.
- **MOTORS off and on loses the driver's phase:** it restarts at MSCNT 0, and would pull the rotor up to two full
  steps (10 µm) the first time it energises. So after configuring it, while it's still off, the firmware sends it the
  steps that bring MSCNT back to the rotor's phase (with EN high its sequencer counts but its bridges stay off;
  datasheet 3.6.1), then reads MSCNT to check. The rail picks up where it was.
- **The first move after MOTORS-on waits ~0.2 s** for StealthChop's tuning (AT#1: energised at run current, standing
  still). With the phase restored nothing moves; at MOTORS-on itself nothing is energised.
- **Only the very first energising after a power-up** can move the rotor, by up to two full steps: until then the
  firmware can't know which detent it rests on.

### 6.8 Events and errors

| Event | Means |
|---|---|
| `!motors on` / `!motors off` | SW11 changed (6.5) |
| `!limit <axis> <state>` | the half-spaces its limit switches block changed (6.5): `-`, `+`, `-+`, `0`, with `?` for an unknown end. A switch opening stopped the axis. The rail's adds `pos=`. Also after every `!hello`, for each axis |
| `!stall <axis> <state>` | stalled (stopped, that direction blocked: `-`, `+`), or the block lifted (`0`). Also after `!hello`, for an axis with one |
| `!lost r <why>` | the rail's count may be off (`limit`, `motors`: MOTORS dropped mid-move, `driver`: the TMC2209 reset) |
| `!halt on [cause=<why>]` / `!halt off` | a halt took effect, to every session: the command (no cause), the driving client lost (`cause=closed`, `link`, `replaced`, `bye`, `stuck`; section 2), or its jog not renewed (`cause=lease`, 6.4) / released by an all-zero jog |
| `!end <axis> <op> <result> [tag=]` | (later) a `move` or `cal` ended: `ok`, `stopped`, `limit`, `stall`, `motors`, `acq`, `failed` |
| `!jog refused <why>` | a non-zero jog couldn't run: `motors-off`, `motors-wait`, `halted`; once per reason |
| `!warn r phase <text>` | the driver's phase didn't read back as restored: the next enable may move the rail up to 2 full steps |
| `!error <verb> <code> [text]` | an unanswered command (`jog`) was malformed; at most once a second |
| `!acq ready` | quiet for the acquisition |
| `!fault <axis> <code> [text]` | anything else that stops an axis: `r driver <why>` (the TMC2209 failed a check; the rail retries every 2 s), `x sense` (IPROPI reads ~0 A while driving hard: an open sense line), `r switches` (6.5). The ones that hold until a restart are replayed after every `!hello` |
| `!cfg <text>` | after `!hello`: a parameter refused or ignored (`parameters.md`) |
| `!drive <record>` | the record, at the `watch` rate |
| `!bye <why>` | the server is closing this session (`replaced`) |

| Error | Means |
|---|---|
| `syntax` | not a valid line (too long, a bad character) |
| `unknown` | no such command |
| `arg` | an argument missing, unknown or malformed |
| `range` | a value out of bounds |
| `motors-off` | SW11 is off |
| `acq` | in the acquisition state |
| `busy` | the axis is doing something that this command can't join or replace |
| `blocked` | a move towards an open limit or a stall |
| `nocard` | `save` with no SD card |
| `fault` | a fault stops this command: an axis's (see the `!fault` it came with), or a lid sensor's (9.2) |
| `absent` | the lid isn't answering (9.2) |
| `hot` | the lid's board is at its 60 °C limit (9.2) |
| `medium` | the medium is at its limit, `lid.medium_max` (9.2) |

### 6.9 Parameters

**The keys, their units, defaults and bounds: `parameters.md`.** They're read from `/parameters.yaml` on the SD card
at start-up; the names are the file's dotted paths.

- **`get x.vmax r.acc`** (online) replies `@ ok x.vmax=250 r.acc=20000`. `get` alone lists every key, one `#` line
  each (`#5 x.vmax=250`), then `@5 ok`. These are the values in force: the card's, after their bounds and the
  calibrations' checks against the model (`parameters.md`), or the defaults.
  - **Values:** numbers as plain decimals (`250`, `0.038`, never an exponent); lists comma-joined, without spaces
    (`x.model.duty=0.038,0.92`); words as they are (`r.effector=min`), double-quoted if they hold a space
    (`unit="bench 2"`).
  - **Errors:** `err arg unknown key <key>`, and `err arg too long for one line: ask for fewer keys`.
  - **A client reads them all on connecting,** e.g. to cap its full-scale speeds at `x.vmax`, `r.vmax` and so on.
- **`set x.vmax=150`** (later) replies with the values now in force (`@ ok x.vmax=150`), or `@ err range x.vmax`.
- **`save`** (later) writes them back to `/parameters.yaml`. It's refused while anything moves (`err busy`): an SD
  write can stall for tens of ms, so the firmware writes the card only from its background task, never from a
  control loop.

### 6.10 Timing

- **Stick to motion:** aim for < 20 ms. The gamepad reports at 1 kHz, the client sends at once, and the server applies
  a jog within 1 ms.
- **From rest:** a fine axis wakes in 0.3 ms. The rail rewrites and verifies its driver's configuration before each
  enable (`hardware.md`, rule 6): ~15 ms more, and ~0.2 s more on the first move after MOTORS-on (6.7).
- **Release to rest:** v / dec. A fine axis at 250 µm/s stops in ~60 ms, over ~8 µm. The rail at 5 mm/s stops in
  0.25 s, over ~0.6 mm, plus up to a detent.
- **halt:** within ~1 ms of the line arriving: the time to read it.
- **The driving client lost:** halted on the next loop pass after its FIN or RST (< 1 ms), or within ~0.13 s of a
  pulled cable (the PHY's link bit, read every 125 ms). A client that hangs with its link up: halted 250 ms after its
  last jog (the lease).
- **Slow fine motion will be pulsed** (not built yet: `<axis>.duty_min`, `parameters.md`). Steady PWM can't hold a
  slow speed open loop: the drive sits just above the gearbox's friction, whose drift then sets the speed (the duty
  for 2 µm/s gives 0-6 µm/s for ±30 % friction, in a lumped INJS11 model). So below a crossover found at calibration
  (likely 20-40 µm/s) the firmware will drive short full-torque pulses (~1 ms at the duty cap, brake between) and set
  the speed by their rate: ~0.1 µm each (0.08-0.17 µm), 2 µm/s at ~10-25 pulses/s. A pulse's torque is ~10x the
  friction, so friction moves each step by only 10-20 %, and the steps are below what the optics resolve.

### 6.11 An example

Typed into `nc 192.168.50.2 5000` (or the USB serial monitor). Jogs get no reply. A jog typed by hand moves for one
lease (250 ms) and then halts, so a plain `jog` has to release it before the next: a bench nudge, nothing more.
Driving is for a client that renews its jogs (`mmi_client`). The lid's `!lid` each second is left out.

    !hello api=0.11 fw=0.9.1 unit=unset reset=power cfg=card clock=unset motors=off halt=0
    !limit x 0
    !limit y 0
    !limit z 0
    !limit r 0 pos=0
    !light t=412 warm=0 cool=0
    !lid t=412 sw=off mode=absent duty=0 cut=0
    !motors on
    watch drive 2
    @ ok
    !drive t=5120 motors=on halt=0 acq=off x.mode=idle ... r.mode=idle r.v=0 r.pos=0 r.homed=0 r.lim=0 r.blk=0 r.drv=ok
    jog x=50
    !halt on cause=lease
    jog z=-80
    !jog refused halted
    jog
    !halt off
    jog z=-80
    !stall z -
    !halt on cause=lease
    jog
    !halt off
    jog z=80
    !stall z 0
    !halt on cause=lease
    jog
    !halt off
    7 halt
    @7 ok
    !halt on
    jog
    !halt off

## 7. Calibration (the fine axes): later

Not online yet: the fine axes run on the precomputed model (`parameters.md`), and the record shows `<axis>.cal=0`.
What follows is a sketch to come back to.

`jog` and `move` mean µm/s and µm only once each fine axis is calibrated: how far each drive setting moves its
stage in a given time, in each direction (gravity on z, the stages' springs and the gearboxes all make + and -
differ). The rail needs none: its steps are its ruler.
- **The microscope measures displacement, not speed:** a timed run at a fixed duty, then the displacement. ZEN's
  stage is motorised and calibrated, so x and y come from centring the tip in view before and after. z needs more
  thought.
- **A calibration overwrites `cal` within x/÷ `fine.cal_ratio` of the model** (`parameters.md`).

- **What it will store,** per axis and direction: the steady curve (duty against speed, the `cal` tables), the pulse
  step for the slow end (µm per pulse), the crossover between them, and the backlash at a reversal.
- **The split:** the firmware will offer timed open-loop actions (`cal x run d=<duty> t=<ms>`, `cal x pulse n=<count>`)
  under every protection, and keep the result; the client runs the procedure against ZEN and does the fitting.

## 8. Driving it from the gamepad (client notes)

The firmware never sees the gamepad: an 8BitDo Ultimate 2C (2dc8:310a) or a PS5 DualSense (054c:0ce6), on the
client's USB. This is how the client uses the API. `../client/` keeps the rules below for any front end, and `../app/`
maps the pads (known by their USB ids, the same buttons by position), or the keyboard when neither is attached.

| Input (8BitDo / DualSense) | Sends |
|---|---|
| right stick | `jog x= y=`: fine X and Y |
| left stick, vertical | `jog z=`: up = +z. Its horizontal is ignored |
| triggers (L2 / R2) | `jog r=`: the rail, left -, right +, proportional (decided 2026-10-19) |
| bumpers | the sticks' speed range, down / up (later) |
| D-pad | the app's menu, with the sticks; `move` nudges (later) |
| B / Circle | `halt`, the moment it goes down. In the app's menu it goes back a level instead, and doesn't halt |
| HOME (or STAR) / PS | the app's menu: opening it halts any drive, then the controls move the menu instead; in it, out of the whole menu. Nothing to the unit |
| A / Cross | in the menu, into the chosen item, or a switch |

- **The menu's Light calib. sets the lights by hand** (9.1): going in sets them to its sliders, each move sends the
  new level (`light` through a mailbox in `mmi_client`, so the window never waits), and coming out switches them off.
- **Its Temp calib. drives the lid by hand** (9.2) and lists the lid's five temperatures, each readout's mean with
  its SD under it, and the unit's two limits. A switch above the duty slider, off each time the page is entered, has
  to be turned on (A) before any duty goes out; coming out, or a cut by the unit, turns it off and sends
  `lid duty=0`.
- **Show what's blocked as it's asked for.** The client mirrors the limits and stalls (6.5), so a front end can say,
  as a stick asks for a blocked direction, why it isn't moving: `mmi_app` writes LIMIT or STALL, in red, in place of
  that axis's speed. Nothing extra goes to the unit for this, and nothing comes back per jog.
- **Coarse and fine X are separate controls, like a microscope's coarse and fine focus.** The triggers drive the
  rail and the right stick fine X; used together they simply add. No button switches the stick from one to the other:
  a switch in mid-motion hands the speed from one actuator to the other, and their different ramps make the tip's
  speed dip or jump for a few hundred ms.
- **Not the left stick's horizontal for the rail:** no thumb moves a stick purely up and down. Focusing Z would also
  drive the rail sideways at up to mm/s, and a rail move would drive Z towards the coverslip. The triggers are
  separate, spring back to zero and take a deliberate squeeze.
- **Each axis's speed is continuous anyway** (6.4: acceleration limits on all four). A flicked stick or a changed
  range becomes a smooth change of speed, not a jolt.
- **Notice a dead link, and come back carefully.** Ping after ~1 s with nothing from the unit; no reply within ~1 s
  means the link is gone (the unit has halted by then), so close and reconnect. After reconnecting, as after any
  halt, send the all-zero jog first, once the operator has let go.
- **Jogs go through a mailbox, halt goes at once.** The jog sender holds only the latest vector and sends it every
  50 ms (or on a change), so nothing ever queues. The halt button writes `halt` to the socket the moment it's pressed,
  from wherever the button is read. The jogs can carry on as the sticks stand: the server ignores non-zero vectors
  until it sees an all-zero one, i.e. until the operator has let go. `mmi_client` also holds them back itself until
  its caller asks for zero, and sends that zero before anything else. It does the same after a reconnect and after
  MOTORS comes on, where the server holds nothing, so a stick held through either doesn't start a move.
- **Shape the sticks:** a deadband of ~5-10 %, then an exponential map from ~2 µm/s at its edge to the range's top
  speed at full deflection (2 to 300 µm/s puts half deflection at ~25 µm/s). A good top is about half a field of view
  per second: ~300 µm/s at 10-20x, ~150 at 40x, ~100 at 63x. The bumpers step through those; ramp the scale over
  ~0.3 s, or change it only while the sticks are centred.
- **Shape the triggers** the same way, from ~20 µm/s to the rail's top (5 mm/s to start).
- **Send** on every change of the shaped vector (at most 50 Hz), and every 50 ms while any component is non-zero;
  then one all-zero `jog` when everything is released. On a gamepad disconnect, send the all-zero `jog` at once:
  a lease left to run out would halt.
- **Don't renew for a front end that has hung.** A sender thread that renews the last vector by itself would keep a
  move going while the loop reading the pad is stuck (a blocked terminal, a stalled screen update), with the halt
  button stuck with it. So the renewing stops, and a halt goes out, if the pad loop hasn't renewed the vector within
  ~0.25 s: `MmiClient(deadman=0.25)`, as `mmi_app` sets it.
- **Screen to stage:** keep a 2x2 map from stick directions to x and y (a rotation in 90° steps, and a mirror). Set it
  once by jogging +x and +y and watching which way the tip goes on screen, so the tip follows the stick in the image.
  The triggers follow the operator's view: left trigger -x (left), right trigger +x (right).
- **Along the pipette:** because x and z are both in µm/s, the client can offer a virtual axis along the pipette
  (x and z mixed at its angle) with no firmware change.

A minimal client:

```python
import socket, threading, time

s = socket.create_connection(("192.168.50.2", 5000))
s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

def reader():                                   # replies and events, as they arrive
    for line in s.makefile("r", encoding="ascii", newline="\n"):
        if not line.startswith("@ ok"):         # show events and errors
            print(line.rstrip())

threading.Thread(target=reader, daemon=True).start()

def jog(**v):                                   # jog(x=40, y=-12.5): µm/s; the axes left out get 0
    s.sendall((" ".join(["jog"] + [f"{a}={v[a]:.1f}" for a in v]) + "\n").encode())

for _ in range(40):                             # 2 s of slow +x at 20 Hz, then stop
    jog(x=20)
    time.sleep(0.05)
jog()
```

## 9. The incubator

**The drive and the incubator are independent** (section 1). Nothing here has a lease or a halt, and a halt or
MOTORS changes none of it. The link going changes nothing either, except a lid duty set by hand (9.2), a test that
ends with its session.

### 9.1 The lights (online)

Two channels, warm (2700 K) and cool (6500 K), in the one CCT strip on the chamber's walls. Each is a linear current
sink whose set-point is Teensy PWM filtered to DC (the main board's schematic, `../hardware/frame/main_pcb/main.kicad_sch`), so
the strip sees pure DC: nothing flickers on camera, and the lights can stay on through an acquisition.

| Command | Reply | |
|---|---|---|
| `light [warm=<%>] [cool=<%>]` | `@ ok warm=<%> cool=<%>`: both levels, as now in force | sets either channel or both; one left out keeps its level |
| `status light` | `@ ok t=<ms> warm=<%> cool=<%>` | the record |
| `watch light <Hz>` | `@ ok`, then `!light <record>` at that rate | 0 = off, at most 50, as `watch drive` |

    light warm=40 cool=12.5
    @ ok warm=39.99 cool=12.5
    !light t=81234 warm=39.99 cool=12.5

- **A level is % of that channel's full scale:** 0-100, any plain decimal. The unit sets the nearest of its 4097 steps
  (12-bit PWM, 0.024 % apart) and replies with what it set, to 0.01 %: 40 is 1638.4 steps, so `warm=39.99`.
  - **Full scale** is the set-point's 1.98 V: 39.7 mA through the strip, nominally. The current is about
    (level x 19.8 mV - 5 mV) / 49.9 ohm, and follows the Teensy's 3.3 V.
  - **Not calibrated:** the level is drive, not light. A calibration will map it to irradiance, per channel.
  - **Dark at the bottom:** the sink's 5 mV offset keeps the strip dark below ~0.25 % (0.02-0.5 %, unit to unit).
  - **Nothing reads the current back:** the reply says what's set, not what flows. It can be measured at the test
    points IS-W (TP9) and IS-C (TP10), 49.9 mV per mA; the filtered set-points are SP-W and SP-C (TP7, TP8).
- **Settling:** a new level settles in ~50-75 ms (the set-point's filter); the reply comes at once.
- **Mirrored state** (section 3, rule 6): `!light` goes to every session whenever a `light` command changes a level,
  and after every `!hello`; with `watch light`, also at that rate. A client keeps the lights' state from these.
- **Held until changed:** by whatever client, through any halt, a lost link or MOTORS off. Several clients may set
  them; the last command counts, and everyone hears it.
- **Dark after any restart** (power, watchdog, an upload): nothing resumes (9.3).
- **Errors** change nothing: `err arg` (no channel given, an unknown key, a channel twice, not a number), `err range`
  (outside 0-100).
- **Later:** `err prog` while an incubator program runs (9.3).

### 9.2 The lid by hand (online)

Six Peltiers in series between the lid board (the chamber side) and its top plate, on one DRV8871 from the lid's own
9.16 V, and five NTCs on two ADS1115: three on the board, two probes lying in the medium
(`../hardware/lid/heating_pcb/assembly.md`, `hardware.md` "Lid"). By
hand only, so far: a duty, for the bench and the calibration.

| Command | Reply | |
|---|---|---|
| `lid duty=<%>` | `@ ok duty=<%>`: the duty now in force | drives the string: + heats the chamber side, - cools it |
| `lid off` | `@ ok duty=0` | the same as `lid duty=0` |
| `status lid` | `@ ok <record>` | the last readout, and the state now |

`!lid <record>` comes by itself, with every readout of the sensors, once a second; there's no `watch lid`
(`err arg`).

    lid duty=30
    @ ok duty=30
    !lid t=81000 sw=on mode=manual duty=30 cut=0 board.a=24.312 board.a.sd=0.003 board.b=24.271 board.b.sd=0.004
    board.edge=24.018 board.edge.sd=0.003 med.j2=22.950 med.j2.sd=0.002 med.j3=22.881 med.j3.sd=0.003 v33=3.301

| Key | Values | |
|---|---|---|
| `t` | ms | since the Teensy started: when the readout was made, at its second's end. A line between readouts (a change) repeats the last readout, `t` and all. While `mode=absent`, when the line was made |
| `sw` | `on`, `off` | SW10, LID HEAT |
| `mode` | `absent`, `fault`, `idle`, `manual` | `absent`: the lid isn't answering, or hasn't been read out since it came back; `fault`: a sensor reads wrong (below); `idle`: no duty; `manual`: a duty in force |
| `duty` | % | the duty in force, signed; driven while `sw=on` |
| `cut` | `0`, `hot`, `medium`, `fault`, `absent`, `closed`, `link`, `replaced`, `bye`, `stuck` | why the unit itself last set the duty to 0, since the last `lid` command (`0`: it hasn't) |
| `board.a`, `board.b`, `board.edge` | °C, `open`, `short` | the board's NTCs, each its readout's mean: RT1 (strip A, between PE1 and PE2), RT2 (strip B, between PE6 and PE5), RT3 (the -X end, past the slot's end: away from the cable) |
| `med.j2`, `med.j3` | °C, `open`, `short` | the medium probes: J2's hangs through the slot's -X end, J3's through its +X end. `open` from the lid's first readout: not fitted |
| `<sensor>.sd` | °C | after each sensor that reads: its readout's SD (below) |
| `v33` | V | the lid's 3.3 V, the NTCs' reference: its readout's mean |

The sensors and `v33` are left out while `mode=absent`.

- **The readout:** each NTC, and the 3.3 V, is sampled every 62.5 ms (16 Hz) and read out once a second (1 Hz): the
  mean of its 16 samples, and their SD about the second's straight line, so a board warming at 0.3 K/s reads as its
  few mK of noise rather than ~90 mK of rise. The means are what everything acts on: the cuts below, and the
  temperature control when it comes, one step a readout. Each readout goes out as `!lid`.
  - **Why those rates** (from a 2D thermal model of the 1.6 mm aluminium board and
    top plate, the modules and NTCs where lid v16 has them):
    - the board's NTCs see a duty change within ~0.5 s, the aluminium spreading it from the modules that fast; then
      the lid's own time constant, ~7.5 min; the medium, tens of minutes;
    - so a loop on a 1 s readout runs as a continuous one would: in the model a PI holds the board to ~1 mK rms,
      the same at 0.25 s as at 1 s. At 5 s it settles ~15 % slower; at 15-30 s it has to be slowed, and holds
      10-40x worse;
    - 16 samples a second fall evenly over a cycle of 50 Hz mains and of 60 Hz, so pickup from either cancels in the
      mean. Polled at 10 Hz, 1 mV of either would bias it by up to 32 mK; at 20 Hz, of 60 Hz; at 25 Hz, of 50 Hz;
    - the bus is busy ~45 ms of each 62.5 (six conversions in four rounds at 128 SPS), leaving room for the string's
      Seebeck reading later.
  - **A sensor that reads open or shorted even once in a second** reads so for that readout.
- **The duty** is % of full drive (9.16 V across the 13.7 Ω string: 0.67 A, 6.1 W), signed. + is the direction
  `lid.sign` names (`parameters.md`: found at commissioning, as the one that warms `board.a` and `board.b`).
  - **Capped at `lid.duty_max`**, 50 % by default (below). Past it: `err range`, and nothing changes.
  - **Set to the nearest of 4096 steps** (12-bit PWM at 20 kHz), and replied to 0.01 %, as a light level. Under 2 %
    is 0: the bridge's shortest pulse.
  - **Coast PWM:** the string is driven during each pulse and floats between them, never braked
    (`hardware.md`).
- **A duty belongs to the session that set it.** When that session ends (closed, the link down, replaced, `bye`, or
  it stopped reading) the duty goes to 0, `cut=<why>`. It's an attended test, and never outlives its client.
- **The unit cuts it to 0 by itself,** `cut=<why>`, and refuses any duty but 0 while the cause lasts, with the same
  word (`err hot ...`). On each readout:
  - **`hot`:** a board NTC at 60 °C, whichever way the duty runs;
  - **`medium`:** a medium probe at `lid.medium_max`, 40 °C by default (`parameters.md`), whichever way the duty
    runs. A probe that isn't fitted guards nothing: with neither, only the board's 60 °C stands;
  - **`fault`:** a sensor can't be trusted: a board NTC open, shorted or outside 0-100 °C; a probe shorted, or open
    after it had read (its lead come loose; unplugging and re-plugging the lid takes it as unfitted again); or `v33`
    outside 3.13-3.47 V;
  - **`absent`:** the lid stopped answering: at once, on the first transaction it misses. Plugged back in, it's
    `idle` from its first readout, a second later: nothing resumes.
- **SW10 off holds the drive off** (both bridge inputs low, `hardware.md` rule 8) and keeps the duty:
  `sw=off` with `mode=manual`. Back on, it drives again 20 ms later.
- **0 is always accepted,** even while absent, faulty, hot or at the medium's limit; and any `lid` command sets `cut`
  back to `0`.
- **Mirrored state** (section 3, rule 6): `!lid` goes to every session with every readout, at once whenever `sw`,
  `mode`, `duty` or `cut` changes, and after every `!hello`.
- **Nothing else touches it:** a halt, MOTORS, a jog's lease. **Off after any restart**, as the lights.
- **Errors** change nothing: `err arg` (no `duty=`, an unknown key, not a number), `err range` (past `lid.duty_max`),
  `err absent`, `err fault <sensor> <reading>` (`err fault board.a open`), `err hot <sensor> <°C>`,
  `err medium <probe> <°C>` (`err medium med.j2 40.05 C, the limit is 40 (lid.medium_max)`).

**Why 50 %:** the board's steady state, held at 50 % heating, in the lid's thermal model:

| Room, chamber | Board | Medium |
|---|---|---|
| 22 °C, chamber to room 0.15 W/K | 51 °C | 28 °C |
| 28 °C | 57 °C | 34 °C |
| 28 °C, an insulated chamber (0.05 W/K), the string at its tolerance corner | 61 °C | 43 °C |
| the same, with the top plate covered | 71 °C | 47 °C |

The cap draws ~3.4 W from 24 V. Cooling at 50 % warms the top plate to 43-51 °C (70 °C covered), with no sensor on it
since J3 became a medium probe. Only a covered plate gets past the 60 °C cut, and the string's cutoff TCO1 (72 ± 5 °C)
stands behind it. In an insulated chamber the medium heads for 36-43 °C: `lid.medium_max` cuts it at 40.

**The temperatures are uncalibrated:** each NTC (1 %), through its 1 % divider resistor and ratiometric to `v33`, is
good to ~±0.5 K; a readout's own noise is a few mK. **The board's three matter as much as the probes.** They'll be
the temperature control's inner sensors, holding the board at a set point, so the board is always at a known
temperature; the medium is then reached by moving that set point, more slowly (the cascade, `hardware.md`
"Temperature control"). Calibration will add an offset per sensor:
- **the board's three** against a reference bonded to the board beside each, the lid settled at idle and then held
  warm. Not against the room's air: idle, the lid's buck (~0.1 W) keeps the board 0.7-0.85 K over the room, unevenly
  (the lid's 2D thermal model);
- **the probes** in place, in the medium, against a reference thermometer at the working temperature, two points if
  the program spans several K: that also takes out the heat each lead brings down from the warmer board.

### 9.3 Reserved for what comes next

The framing above is shared. These names are held for what comes next (provisional):

| Topic | Commands | Record and events |
|---|---|---|
| `lid` | `lid temp=<°C>`: the lid holding the medium at a temperature, through anything (the incubation) | `!lid` gains `set=` and a mode |
| `prog` | `prog load ...`, `prog start`, `prog stop` (the incubator program) | `!prog`, `!end prog ...` |
| `clock` | `clock set <date and time>` | the RTC the program runs on |
| `log` | `log read ...` | `#` lines, from the SD card |
| `sys` | | `status sys`: the motor supply (A16), the reset cause, versions |

- **Manual or program, never both:** while a program runs, manual `light` and `lid`
  set-points are refused (`err prog`) until `prog stop`.
- **Nothing resumes after a power-off or a reset, for now.** The unit starts in its safe state and the client sets
  it up again; the resume logic comes later.
- **The clock needs no backup cell:** since nothing resumes, the client sets it at the start of each session
  (`clock set`). Until then `!hello` says `clock=unset`, and a program can't start.
- **The SD card** (the Teensy's own slot; a 16 GB card per unit in the BOM) holds the parameters and calibrations
  (6.9, section 7), the logs and any saved state, such as the rail's last parked position, as text files.

## 10. Open questions

1. **The rail's sign:** the default is `r.sign: 1`, DIR high = +x, as the stepper tester's "fwd" (DIR high) on the
   same coil wiring. Did the tester's stick-right run the carriage right, towards the bearing? If not, it's -1, once.
2. **A belt-and-braces halt over UDP?** A datagram to a second port would bypass the TCP stream entirely, should a
   client ever back it up. On the direct cable, with the client rules above, it shouldn't be needed.
3. **Sessions:** the newest connection takes over. Is a read-only observer wanted too (e.g. the imaging PC logging
   temperatures)? That would be a second port.
4. **Fine-axis position:** none, as drafted. A dead-reckoned estimate, re-zeroed at every switch trip, would show
   roughly where each stage is in its 13 mm. Worth having? Real feedback needs hardware: the servos' pots are still in
   place.

## Changes

- **0.11 (2026-10-30):** the lid's sensors read out (9.2): each NTC sampled at 16 Hz, and once a second the mean of
  its 16 samples and their SD (`<sensor>.sd`, about the second's straight line). `!lid` goes out with every readout,
  unasked, and `watch lid` is gone (`err arg`); `t` is the readout's. The medium's limit, `lid.medium_max` (40 °C by
  default): a probe at it cuts the duty, `cut=medium`, and refuses another, new error code `medium`. A probe shorted,
  or open after it had read, is a fault (it can't guard the medium); one open from the lid's first readout is still
  just unfitted. The lid reads `absent` until its first readout after coming back. The board NTCs' places corrected
  (RT1 between PE1 and PE2, RT2 between PE6 and PE5, RT3 at the -X end).
- **0.10 (2026-10-30):** the lid by hand online (9.2): `lid duty=<%>` (signed: + heats; capped at `lid.duty_max`,
  50 % by default), `lid off`, `status lid`, `watch lid`. `!lid` is a state event (`sw`, `mode`, `duty`, `cut`), after
  every `!hello` and on every change, and carries the five temperatures and the lid's 3.3 V; it replaces the
  provisional `medium=`, `lid=` and `set=` record. A duty ends with the session that set it; the unit cuts it at a
  60 °C board, a bad board sensor or a lid that stops answering. New error codes `absent` and `hot`. The rest of the
  incubator is reserved in 9.3.
- **0.9 (2026-10-29):** the lights online (9.1): `light [warm=<%>] [cool=<%>]`, `status light`, `watch light`; `!light`
  a state event, after every `!hello` and on every change. Section 9 is now the incubator: lights (9.1), the rest
  reserved (9.2).
- **0.8 (2026-10-21):** the limit and stall state mirrored: `!limit <axis> <state>` is each axis's whole state
  (`0`, `-`, `+`, `-+`, `?`; the effector's switch counting on fine x), sent on every change and, with `!stall` for
  any axis with one, after every `!hello`; `<axis>.lim` the same. A TCP session that can't take a line is closed at
  once (`watch` records skipped instead while half full). Contact bounce can't confirm, release or re-decide a trip.
- **0.7 (2026-10-20):** a non-zero jog not renewed within the lease halts, as `halt`, with `!halt on cause=lease`
  (was a ramped stop and `!timeout jog`). Clients drive through `mmi_client`, ending every move with an all-zero jog.
- **0.6 (2026-10-19):** losing the driving client (closed, link down, replaced, `bye`, stuck) halts the drive, held
  until an all-zero jog; `!halt on cause=`, `halt=` in `!hello`.
- **0.5 (2026-10-19):** `get` online. The reference client, now `../client/` and `../app/`.
- **0.4 (2026-10-19):** `halt` online; the operator's frame (so MIN is the effector's switch at -x, MAX the
  carriage's); limits and stalls as disallowed half-spaces; no homing, nothing energised at MOTORS-on (the driver's
  phase restored instead); detents at MSCNT 128 + 256k; `!halt`, `!jog refused`, `!warn r phase`; trapezoidal ramps.
- **0.3 (2026-10-19):** `jog` online, with `ping`, `help`, `status drive`, `watch drive`, `bye`; the rail's switch
  asymmetry, the frame-contradiction faults, `!cfg`, `cfg=`; `x.d` and `r.drv` in the record; `parameters.md`.
- **0.2 (2026-10-18):** `jog` unanswered; parameters and calibrations on the SD card; section 7; slow fine motion
  pulsed; the gamepad layout; nothing resumes after a restart.
- **0.1 (2026-10-18):** first draft: transport, lines, the session, the drive.
