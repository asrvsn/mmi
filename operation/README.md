# Operation: rules

The essentials for running the unit day to day. The images in this folder are the README's usage and operation steps.

## Switches and lights

Three slide switches on the main enclosure's top, front to back (from the operator toward the microscope), each
with its LED beside it. Each switches **on toward the back** (`18_power_on.png`).

| Switch | What it does | LED |
|---|---|---|
| PWR (SW9) | the whole unit (soft start: the 24 V may be plugged in with it on or off) | green: 5 V up. The RGB status LED (D9): red starting up, green ready, blue working (a command, or an axis moving), red blinking at 2 Hz a fault that holds until a restart (`../firmware/README.md`, "The status LED") |
| MOTORS (SW11) | 24 V to the rail stepper and the three fine axes. On only while positioning | amber: motors powered. Dark = nothing can move |
| LID HEAT (SW10) | lets the lid heat. Off holds the heater off; the lid stays powered and its temperatures still read | red: the heater may run |

Power up PWR, MOTORS, LID HEAT in that order; switch off in reverse. The LED strips go dark while the Teensy resets or takes a
firmware upload, and come back when the firmware restarts.

## Motors

- Switch MOTORS on to position, off when done; check its LED is dark. Leave it off for incubations, diurnal runs, and
  whenever nobody is at the unit: unpowered, nothing can move, stall or heat.
- Switching off while parked is harmless (the rail rests on a detent; the gearboxes and fine screws hold the fine
  axes). Mid-move, the rail stops and its position is lost.
- The lid and the lights keep running with the motors off.

## Moving the stage

There is no homing: positions count from power-up, and the only stops are each axis's limit switches at its ends,
with no soft limits in the firmware. The collisions below happen inside that travel, so avoid them by hand:
- **Long rail moves:** raise Z first, and lower it again over the target.
- **The row:** dock with Y short of the row's tips, at the capillary's height, then move Y toward the row last, and
  slowly. With Z low and Y forward, the effector's tips meet the row's tips.
- **Low Z:** a little below the docked height the tips are under the plate floor's top. Keep them over a coverslip
  window there, and raise Z ~1 mm before crossing the rib between the two windows.
- **Lid on:** keep Z within ~4 mm above the docked height; higher, the wrist meets the lid board's back edge and the
  lid frame.
- **Lid off, under the condenser:** ~10 mm above the docked height the wrist reaches the condenser's cone.
- **The rail's right (+x) end:** don't drive the effector past the row's last capillary. Beyond it the wrist's
  bracket meets the plate's +x wall before the rail's MAX switch trips. The left end is guarded by the MIN switch.

The distances are from the model's fit check (2026-10-21), made before the capillary row's last changes: treat them
as approximate, and go slowly near them the first time.

## Lid

- Plugging and unplugging the lid with the unit running is normal; ground connects first and breaks last.
- Best practice: LID HEAT off, (un)plug, LID HEAT on, so the contacts never break heater current.
- The lid port carries 24 V whenever the unit is on.

## USB

- USB may be plugged in at any time, board on or off. With the board off, USB powers only the Teensy (enough to
  program it).
- Before imaging, unplug USB from a mains-earthed PC: its earth joins the unit's 0 V and can add hum. A laptop on
  battery is fine. Ethernet, the normal link, is isolated.

## Fuses

All six are self-resetting; nothing needs replacing.
- The lights out alone: the LED strip's fuse tripped (a short in the strip, a joint or its lead).
- The Teensy dark alone (the network drops, the 24 V ports stay live): the 5 V logic supply's fuse tripped.
- The unit dark under load: PWR off, wait ~30 s, on again, then find what overloaded it. In normal use the unit draws
  ~0.9 A; the lid can't draw more than ~0.3 A at 24 V.

## Z-piezo

Calibrate the z-piezo with the unit mounted on it (with plate and medium, ~1.1-1.3 kg) before imaging, and again
whenever that load changes noticeably (the capillary row on or off, another plate or medium depth).

## Never

- Leave MOTORS on with nobody at the unit.
- Plug or unplug the rail motor (main board J3) with MOTORS on: it can destroy the stepper driver. With MOTORS off its
  supply is off.
- Run the 24 V supply above 24 V: the boards' TVS diodes conduct from 26.7 V, and the supply's trimmer reaches
  28.8 V, where they would likely fail short. Set it to 24.0 V once and lock the trimmer.
