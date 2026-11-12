# A stage-top micromanipulator/incubator 

This repository contains the specifications for a combined micromanipulator/incubator stage insert usable for cell/microorganism handling and immobilization for imaging, particularly during multi-day developmental dynamics.

It is fully open source, containing:
* 3D parts suitable for SLA printing: [insert](hardware/insert/parts), [frame](hardware/frame/parts), [capillary mounts](hardware/capillary_mounts/parts), [lid](hardware/lid/parts)
* PCB schematics and board layouts: [main](hardware/frame/main_pcb), [drive](hardware/frame/drive_pcb), [heating](hardware/lid/heating_pcb), [top plate](hardware/lid/plate_pcb)
* A complete [BOM](bom.xlsx)
* Software and firmware for operation: [app](app), [client](client), [firmware](firmware)
* [Assembly](#3d-printing--assembly) and [operating](#usage-and-operation) instructions

The 3D model and an assembled prototype, side by side:

<p>
  <img src="hardware/complete.png" alt="The stage-top unit in the Fusion model" width="49%">
  <img src="hardware/complete_photo.png" alt="The v1 unit on the bench" width="49%">
</p>

Features of the insert are:
* A temperature (via Peltier) and flicker-free light-controlled enclosure fitting standard well-plates
* Motorized fine-motion XYZ axes (consisting of optical translation stages) for handling, cutting, and more general actuation of specimens via glass capillaries, jogged at commanded speeds down to ~10 um/s (open loop; the slow end is not yet characterised on the unit). A larger-stroke additional X axis consisting of a more traditional stepper leadscrew enables traversal of the entire well-plate width, allowing for:
* Parallelized capture and imaging of N specimens in a statically held row of capillaries, allowing for better control of batch variations.
* A combined syringe/gravity-driven flow control on all capillaries, allowing for transition between traditional displacement (syringe)-driven actuation suitable for capture and gentler pressure-driven immobilization suitable for imaging during development. The latter method is far more robust for multi-day holds and less perturbative during growth and development, where plastic deformations occur.
* Insert geometry fits into existing standard Zeiss "K" frame-style (110x160mm) stage openings as well as larger (130x160mm).
* Insert geometry and mass also enables stacking atop Z-actuated piezo inserts for rapid Z-stack acquisition. This is made possible by the ~1kg total mass of the insert, within typical 1.5-2kg Z-piezo load limits, and a second 130x160mm catch for the piezo stage's larger opening.
* As an insert, micromanipulation occurs as in motions composed with that of the stages, unlike traditional table-top micromanipulators (e.g. those used in electrophysiology). This enables both more natural user operation during manual placement with visual feedback, and also enables multiple captured specimens to be easily imaged using automated sequences (e.g. in ZEN software) without the need for external sync / triggers.
* Precise temperature control is available via a top-mounted (lid) Peltier assembly. This configuration both keeps the full under surface transparent, allowing RL illumination and imaging of the entire well-plate, and prevents Rayleigh-Benard type instability (convection cells) by heating the top rather than the bottom liquid surface. A fast control loop sets the lid surface temperature via on-board NTC's, while a slower loop changes the lid setpoint using readouts of dual medium-immersed temperature probes. The top-mounted heating lid has an imaging slot which retains TL illumination and widefield imaging capability (though not generally at optimal Kohler configuration due to the thickness of the unit) as well as t-PMT. 
* Blue- and red-shifted illumination using tunable-white CCT LED strips.
* Construction from commercially available hardware and 3D printed parts, with no specialized in-house machining or fabrication facilities required.
* Finally: **being an insert, it is designed for rapid setup and teardown in shared-use microscope facilities.** This is a primary benefit over larger built / bespoke setups e.g. with table-mounted micromanipulators or shrouding incubators.

![Pick and place: the end effector carries an organism from each dispensing line to a capture capillary](hardware/pick_place.gif)

The primary use-case is in time-lapse confocal laser scanning microscopy (CLSM) during growth and development with accurate simulation of diurnal cycles. Many other uses are possible. The use of a z-piezo insert in combination with SNR boosted acquisition methods (e.g. Zeiss Airyscan) enables rapid acquisition of z-stacks, hence this unit is designed to be compatible with this workflow.

Design notes of the current version (v1; v2 coming soon):
* The micromanipulation capability is fully open-loop (with only limit endstops) relying on visual feedback through eyepieces or cameras. Thus automated return sequences are not currently possible. A goal of v2 is to add position feedback. This will be accomplished by using serial-bus servos in v2, also eliminating the separate motor-drive board entirely (left flap seen above moving with the drive unit) and further reducing mass.
* Flow control (syringe- and gravity-driven) are fully manual, supported beside the microscope using unspecified, laboratory-dependent equipment (e.g. via standard hardware attaching to optical breadboards). V2 will motorize both methods of flow control, enabling integration with the other computer-controlled parts (actuation, lighting, and temperature), enabling some degree of experiment automation. Simultaneously, mounting hardware for the off-scope syringe assemblies will be included and further facilitate rapid setup / teardown.
* SLA printing is currently required due to fine details in the design. V2 will transition to an FDM-friendly design, in which parts can be more dimensionally stable depending on the material.
* In v2, the z-height of the unit will be reduced, enabling more optimal microscope condenser placement for widefield acquisition. 
* **Further firmware updates coming soon.**
<!-- * Firmware status: the drive (jogging, limit switches, halts), the lights and the lid run by hand: light levels and lid duty are set from the app's calibration pages or the protocol's `light` and `lid` commands. The lid's temperature control (the cascade above) and incubator programs (diurnal cycles on the Teensy's clock) are specified in [`firmware/api.md`](firmware/api.md) (9.3) and [`firmware/hardware.md`](firmware/hardware.md), but not yet implemented. -->

# BOM

The complete bill of materials, including:
* non-3D printed hardware and adhesives
* stepper and servo motors
* experimental apparati
* through-hole / hand-soldered PCB components 

are in [`bom.xlsx`](bom.xlsx). These sheets organize by supplier and contain helper columns to track order status.

# Control system

Three PCBs make up the v1 control system: 
1. A [main board](hardware/frame/main_pcb) built around a Teensy 4.1 (left)
2. An aluminum-core, Peltier-heated [lid board](hardware/lid/heating_pcb) (center) and a [top-plate pcb](hardware/lid/plate_pcb) acting as a passive radiator.
3. A [motor drive board](hardware/frame/drive_pcb) (top) which moves along with the stepper rail and connects via flexible ribbon cable to the main board.

![The three PCBs in the unit: main (left), drive (back), heating (lid)](hardware/pcbs.png)

Send all four off to a PCB assembly service; here we include the assets for JLCPCB in each folder, with the order options in its `jlc/ORDER.md`. Order with those options, not the form's defaults: the heating board and the top plate are aluminium-base boards (ordered as default FR-4 they won't spread the heat), the main and drive boards are 4-layer ENIG with epoxy-filled vias, and the main board is assembled on its bottom side. Upon delivery, hand-soldering of several larger components is necessary, detailed in:
<!-- Follow these guides, which also hold each board's checks before first power (on a current-limited bench supply) and the order in which it meets the mechanical build below: -->

* [Main board](hardware/frame/main_pcb/assembly.md)
* [Lid (heating) board](hardware/lid/heating_pcb/assembly.md)
* [Drive board](hardware/frame/drive_pcb/assembly.md)

# Hardware (3D prints)

# 3D printing & assembly

In the following, we give 3D printing and assembly steps in brief. Detailed print settings are in [hardware/print.md](hardware/print.md).

## Stage insert

Print the [insert file](hardware/insert/parts/insert.step).

Epoxy the 8mm 440C balls to their pockets in `insert` to create a kinematic coupling.

![insert/1](hardware/insert/assembly/1_insert.png)

## Unit frame

Print the [unit frame's parts](hardware/frame/parts): one of each, except three each of the two gears and the main enclosure's switch cap ([print.md](hardware/print.md)).

Epoxy magnets and dowels to the underside of `frame` to complete the kinematic coupling: one 8 x 3 mm magnet per pocket, then its dowel pair.

![frame/1](hardware/frame/assembly/1_frame_underside.png)

Epoxy balls topside (six: the capillary mount's and lid's couplings). Take the NEMA11 150mm actuator apart: keep the motor with its leadscrew, the carriage on its MGN9 rail, and the leadscrew's end bearing (in the kit's aluminium back plate); the kit's own frame and both aluminium plates aren't used. Press the end bearing into the frame's Ø13.15 seat at the bearing end, flush with the end face. Lay the rail in the frame's groove and bolt it through its 3rd, 5th and 7th holes with 3 x M3x12 socket heads into M3 nuts inset in the frame, tightened in turns to ~0.5 N·m (the nuts sit in resin).

![frame/2](hardware/frame/assembly/2_frame.png)

Bolt the motor (3 x M2.5x8 button heads + washers, from inside the frame), snug only, threading the leadscrew through the carriage's nut into the end bearing (any stepper tester spins it quickly; or turn the shaft by hand). Run the carriage to the motor end, so the nut pulls the motor onto the screw's line on its pilot, then tighten the three screws in turns. Test full travel: the effort should stay even. If it tightens toward the motor end, repeat that alignment; toward the bearing end, open the Ø13.15 seat slightly on the side the screw pushes toward and fix the bearing with a drop of epoxy. Finish with a thin film of grease on the screw. Strong print warping or dimensional inaccuracies can make this unavoidable, in which case one option is to not place the end bearing, allowing the screw to move loosely.

![frame/3](hardware/frame/assembly/3_frame_motor.png) 

Epoxy the 30T gear to the translator knob and bolt the 16T gear to the servo spline (M2.5x8 button head), x3. Translators are compact manual linear stages with a knob drive (not a micrometer head); servos are micro digital servos with a bottom wire exit, used as plain gearmotors. At this step, therefore, remove the servos' control boards (and positioning potentiometers), solder a twisted pair of 26 AWG silicone (~200 mm) to each motor's terminals and bring it out through the same exit ([drive board guide](hardware/frame/drive_pcb/assembly.md), step 8).

![frame/4](hardware/frame/assembly/4_translator_gears.png)

Bolt `y_brace` to the Y stage base: 2 x M2x6 button heads through the base into M2 nuts inset in the brace. The table covers the holes, so run the translator back and forth (turn the knob) to reach each of the two inset M2s in turn.

![frame/5](hardware/frame/assembly/5_yaxis_back.png)

From the top, 2 x M4x8 countersunk through the brace into the base. Epoxy the brace's half of the wrist coupling: a pair of 6 x 2 mm magnets per pocket, then its dowel pair.

![frame/6](hardware/frame/assembly/6_yaxis_top.png)

Fit the subminiature hinge-lever microswitches (8 per unit, low operating force) to the three printed faces and wire each stage-end pair NC in series with 30 AWG wire (the arm's channel has no room for 26 AWG on all sixteen switch wires): a bridge between the NC pins, a lead from each outer pin. `x_face` takes X's pair and the rail MAX switch (J8, the bearing end), `x_brace` Z's pair, `z_brace` Y's pair and the rail MIN switch (J7, the motor end). Every switch is wired COM to NC, its NO pin cut short ([drive board guide](hardware/frame/drive_pcb/assembly.md), step 9).

![frame/7](hardware/frame/assembly/7_limit_switches.png)

Slide the Y servo into `z_brace` (tabs on the wall; 2 x M2x16 into nuts in the side slots), then bolt `z_brace` to the Y table from below: 3 x M4x10 and 2 x M2x10 countersunk.

![frame/8](hardware/frame/assembly/8_translator_z_brace.png)

Bolt the Z stage base to `z_brace` as for Y: 2 x M2x6 button heads into inset M2 nuts, the stage driven to its top end to reach the hole.

![frame/9](hardware/frame/assembly/9_z_translator.png)

Z servo into `x_brace` (2 x M2x16 + nuts), then `x_brace` onto the Z table: 3 x M4x10, 2 x M2x10 countersunk.

![frame/10](hardware/frame/assembly/10_z_translator_face.png)

X stage base onto `x_brace`: 2 x M2x6 button heads into inset M2 nuts.

![frame/11](hardware/frame/assembly/11_x_translator.png)

X servo into `x_face`, body first (2 x M2x16 + nuts). Bolt the drive board's arm to `x_face`: 2 x M2x20 into nuts recessed in the arm.

![frame/12](hardware/frame/assembly/12_x_translator_face.png)

`x_face` onto the X table: 3 x M4x10, 3 x M2x10 countersunk, heads on the face.

![frame/13](hardware/frame/assembly/13_xyz.png)

Drive board, wires first and covers last ([drive board guide](hardware/frame/drive_pcb/assembly.md), steps 3-15):
1. The ribbon: measure the 20-way IDC ribbon (2.54 mm pitch, sockets on both ends) in place (the guide's step 3 dry-fits the tray, the main board and `x_face` for it), as a U-loop from the main board's J1 that rolls with the carriage, and cut the far socket off where it meets J2's pads (~250-300 mm). Solder that end to the J2 pads, red stripe on pad 1, and buzz it (H1) before any switch is wired. Strain relief: a bead of neutral-cure RTV or epoxy across the ribbon (no room for a cable tie).
2. The board into the arm's frame. Per axis, route the servo's motor pair and the switch pair along the arm's channel, cut them to length and solder them: motors to J4-J6, the stop chains to J9/J11/J13, the rail's MIN and MAX switches to J7 and J8. Check them at the IDC socket (H3).
3. Then the housing: back plate + front cover, 4 x M2x10 + nuts.

![frame/14](hardware/frame/assembly/14_xyz_wiring.png)

Slide the rail carriage into `x_face` from the side as shown (its lip hugs the carriage) and bolt the plate to it: 4 x M3x10 countersunk from the top.

![frame/15](hardware/frame/assembly/15_frame_xyz.png)

Cut two 100 mm lengths of tunable-white 24 V LED strip (COB; cut only at its 50 mm marks, and the walls take up to 120 mm) and stick them on edge to the frame's long inner walls, LEDs facing the plate. Link the strips' 24V, W- and C- pads at the bearing end, and run three leads from the motor end through the frame's lead tunnel to a 3-pin PH plug for the main board's J16 (halved pre-crimped leads: pin 1 24V, pin 2 W- (warm), pin 3 C- (cool); [main board guide](hardware/frame/main_pcb/assembly.md), step 19).

![frame/16](hardware/frame/assembly/16_frame_lights.png)

Bolt the main enclosure's tray to the frame's three end pads: 3 x M3x8 countersunk from the frame side into M3 nuts in the tray's bosses.

![frame/17](hardware/frame/assembly/17_frame_board_mount.png)

Main board, hand-assembled and checked through M8 of [its guide](hardware/frame/main_pcb/assembly.md) (Teensy 4.1 in its sockets), into the tray: plug in the rail motor (J3) and the LED strip (J16), the unit off. The rail motor's lead ends in bare wires: make its 4-pin ZH plug as in the guide's step 18 (pin 1 black, 2 green, 3 red, 4 blue), and never plug or unplug it live. Cover on with 3 x M2x20 socket heads into M2 nuts in the tray's back face; switch caps in from the top. The ribbon's IDC socket mates on J1 through the cover's opening.

<!-- The unit's first power-up, everything plugged in, is the main board guide's S1-S3: on the current-limited bench supply first, then the 24 V supply. -->

![frame/18](hardware/frame/assembly/18_main_board.png)

## Capillary mounts

Print the [capillary mount files](hardware/capillary_mounts/parts).

Epoxy the mount's half of its coupling to its underside: a pair of 8 x 3 mm magnets per pocket, then its dowel pair; glue in the three M2 nuts for the face plate. On `end_effector_base`, epoxy its three Ø6 mm balls (440C or chrome steel: they sit on magnets) and press in the two M2 nuts for the clamp cover from below.

![capillary_mounts/1](hardware/capillary_mounts/assembly/1_capillary_mounts.png)

Press 2 mm solid silicone cord (~0.22 m per unit) into the clamping plates' grooves, cut flush: four lengths in `face_plate`, two in `end_effector_clamp`. If using the 3 mm foam cord instead, check the cord parameters in Fusion before printing the plates.

![capillary_mounts/2](hardware/capillary_mounts/assembly/2_face_plates.png)

## Peltier-heated lid

Print the [lid frame](hardware/lid/parts/lid_frame.step).

Epoxy the lid frame's half of its coupling: one 8 x 3 mm magnet per pocket, then its dowel pair.

![lid/1](hardware/lid/assembly/1_lid_frame.png)

The heating board follows [its guide](hardware/lid/heating_pcb/assembly.md). All its hot-plate work comes before the frame, whose resin softens from ~77 °C:
1. In one hot-plate session (~100 °C):
   * the six 20 x 20 mm Peltier modules on thermal paste, their leads soldered to PE1-PE6;
   * the two medium probes (10k NTC in a Ø3 x 12 mm stainless cap, AWG28 PTFE leads) on J2 and J3, either lead on either pad. Cut the leads to ~55 mm (J2) and ~115 mm (J3) from the cap (46-48 and 107-109 mm in the model, plus leeway), strip ~2 mm and tin;
   * the 7 x 26 AWG lid cable on J1 (twisted in pairs: +24 V/GND, SDA/SCL, IN1/IN2, plus a second GND).
2. Thread the cable through `lid_frame`'s +X tower channel (the frame hangs on it until glued), make its 7-pin PH plug for the main board's J20 (wire n to pin n), and run the board's checks on the main board.
3. Paint the aluminium (chamber) side matt black, put a 7 mm piece of polyimide tape over MH1, MH2, MH4 and MH6, then epoxy the board into `lid_frame`.
4. Conformal coat the copper side with TCO1 (the Peltier string's thermal cutoff) masked, as the coat can glue its contacts shut; mask the Peltier tops and the six screw holes too.
5. Dress the probe leads: each pair runs from its pads over the imaging slot's edge, down through the slot and along the board's underside, then down to its cap, which lies flat on the dish floor just past the coverslips' outer end, tip away from you: J2's through the slot's -X end, J3's between R16 and PE2 to the slot's +Y edge, then under the board to the +X end.

![lid/2](hardware/lid/assembly/2_lid_board.png) 

Top plate on thermal paste over the six Peltiers, 6 x M3x10 button heads from below into M3 nuts on top: tighten evenly, the Peltiers sandwiched tight between board and plate. Then line the slot with polyimide tape, bridging the gap between board and plate, and run a bead of neutral-cure silicone into the tower's channel round the cable (guide steps 15-16).

![lid/3](hardware/lid/assembly/3_lid_plate.png)

## Completed unit

![The completed unit](hardware/complete.png)

# Software

The Teensy 4.1's firmware, a Python client library with a test server that stands in for the unit, and the operator's app.

| | |
|---|---|
| `firmware/` | the firmware (`stage/`), its protocol (`api.md`), parameters, hardware rules and host tests: `firmware/README.md` |
| `client/` | `mmi_client`: the client library (standard library only) and the test server: `client/README.md` |
| `app/` | `mmi_app`: the operator's window, driven by a hand controller or the keyboard: `app/README.md` |
| `env.yml` | the `mmi` conda env: both packages and all they need |
| `start_application` | starts the app, on the unit or a test server (a Mac or Linux) |
| `write_firmware` | builds the firmware, writes it to the Teensy over USB, and checks it (a Mac or Linux) |

## Install

With conda (Miniforge), from this folder:

```sh
conda env create -f env.yml     # once: Python 3.12, PyQt6, pyqtgraph, pygame ..., and client/ + app/ installed editable
conda activate mmi              # for the test server and the tests (start_application finds the env itself)
conda env update -f env.yml     # after env.yml changes
```

Any other Python 3.11+: `pip install -e client -e app` (their `pyproject.toml` list what they need;
`pip install -e "client[test]" -e "app[test]"` adds pytest). The tests: `pytest` in `client/` and in `app/`
(offscreen); the firmware's, `firmware/test/run.sh`.

## Write the firmware

The first flash is part of the main board's bring-up (its guide, step T). For updates: with Teensy USB exposed without enclosure and all board switches off, connect micro-usb to controller PC and execute `./write_firmware`. It builds, uploads, and checks
the `!hello` (`firmware/README.md`, "Building and flashing"). Then, close the enclosure; do not connect to USB during normal operation with 24v plugged in.

## Start the app

```sh
./start_application                  # the unit, over wired Ethernet
./start_application 127.0.0.1        # the test server on this computer (below)
```

- **The unit:** the cable from this computer's Ethernet port or adapter into the unit's J19, the unit on (PWR, SW9). It
waits for the link, gives the interface 192.168.50.1/24 if it hasn't an address there (your password; temporary),
waits for the unit, says which gamepad the app will take, and starts the app.
- **Any other address** skips the Ethernet set-up and waits until something answers there.
- **The app's own arguments** pass through: `--fullscreen`, `-v` (log connects, drops and inputs), `--config my.toml`
(`app/config.example.toml`). It won't start a second app while one is open.
- **Driving:** MOTORS (SW11) on, then the sticks and triggers, and MOTORS off again when done; B (Circle, Space) halts; HOME (PS, Esc) opens the menu:
`app/README.md`, "Controls".

## Test with the test server

```sh
python -m mmi_client.test_server     # shell 1, in the mmi env: the unit on 127.0.0.1:5000, printing what it's sent
./start_application 127.0.0.1        # shell 2: the app, driving it
```

- **It simulates the unit at the protocol level:** the drive with its lease and halt, the stage's travel and its limit
switches, stalls, MOTORS, the lights and the lid (with its thermal model). Options: `--motors-off`, `--strict`,
`0.0.0.0:5000` to be reachable from another machine (the Pi): `client/README.md`.
- **With Fusion 360:** start Fusion with its MCP add-in (it listens on 127.0.0.1:27182) and open the `mmi` design,
with its "kin: rail / X / Z / Y" joints. The test server finds it by itself and drives those joints, so every jog
moves the model on screen, stopping at the stage's ends; Ctrl-C puts them back to the as-built pose. Without Fusion
it simply runs; `--no-fusion` keeps it out even with Fusion open.

![The app: status, drive and the light and temperature traces](app/1_main.png)

![The menu: light calibration](app/2_menu_light.png)

![The menu: temperature calibration](app/3_menu_temp.png)

# Experiment preparation

Fluids preparation for an experiment.

## Syringe assembly

Fill the syringe (2.5 mL gastight glass) fully and purge it tip up, to a droplet at the Luer tip. Assemble: the reservoir line's (1/16" ID) female fitting on the stopcock's (3-way female x female x male, locking Luer) male port, the outlet's male fitting in its in-line female port, the syringe into the side port, OFF toward the outlet. The Luer tip is a slip fit, so seat it firmly.

![operation/1](operation/1_purge_syringe.png)

OFF right (toward the reservoir line); push 1 cm to fill the outlet to a droplet.

![operation/2](operation/2_purge_outlet.png)

OFF left; push 1 cm to fill the reservoir line to a droplet at its end.

![operation/3](operation/3_purge_inlet_line.png)

Line into the reservoir flask (T25 culture flask used as reservoir), its end 1 cm off the floor; pull the plunger fully out to refill. Repeat per syringe assembly.

![operation/4](operation/4_fill_syringe.png)

## Capillaries

We generally use 2.00 mm OD x 1.00 mm ID borosilicate capillaries. Leave blunt for **dispensing** lines, pull to desired ID for **capture** and **actuation** lines. Follow standard procedures for pulling larger (50-100um) tips as needed, e.g. pulling smaller and then scoring and breaking against ceramic tile. A starting point on a Sutter P-97 with a box filament (from the Sutter Pipette Cookbook's large-tip programs, untested on this glass): run a ramp test, then HEAT = ramp + 25, PULL 0, VEL 140, TIME 100, pressure 200; 2 mm glass should pull 15-25 s after pressing PULL (adjust HEAT in steps of 5). The ID/OD ratio stays about 0.5, so for a 100 um ID tip, score where the OD is about 200 um.

OFF right. Push 10 cm of tubing onto the outlet barb and a blunt (unpulled) capillary 3 mm into its end; push 1 cm to fill to a droplet. Repeat per dispensing capillary.

![operation/5](operation/5_purge_dispensors.png)

Dip the dispensor's tip in the sample (e.g. a 60 mm dish) and pull 2 cm back to draw it up the capillary and into the tube.

![operation/6](operation/6_fill_dispensor.png)

As for the dispensors, with pulled capture capillaries, tips broken back to ~50 um ID. Repeat per capture capillary.

![operation/7](operation/7_purge_captures.png)

The same for the two shorter actuator capillaries.

![operation/8](operation/8_purge_actuators.png)

Lay the actuator capillaries in the end effector's grooves and caliper each tip to 20.3 mm beyond the base's end face (the nominal pose, open loop). Clamp cover on: 2 x M2x8 countersunk hex. Keep the capillary tips upright throughout, so no air gets in.

![operation/9](operation/9_mount_actuators.png)

Lay the capillaries on the mount's ramp, the dispensors innermost in each group (8th and 9th): captures to 42.4 mm beyond the mount's end face, dispensors to 39.6 mm. Face plate on: 3 x M2x8. Keep the capillary tips upright throughout, so no air gets in.

![operation/10](operation/10_mount_capillaries.png)

## Well plate

Cut two 44 x 16 mm windows in the single-well plate's floor (a rotary tool) for 22 x 50 mm (No. 1.5) coverslips: centred across the plate's width, either side of a 7 mm rib on its centre line, 16.4-60.4 and 67.4-111.4 mm along its length from either outer end. Each coverslip overlaps its window by 3 mm all round, and the row's capillary tips sit over them. File the edges, and clean all surfaces with IPA. Glue the coverslips on using a UV-cured optical adhesive rated USP Class VI, e.g. NOA 81. Rinse with clean solvent (e.g. DI water) and test for leaks. Ensure the coverslips mount from the bottom, as this ensures objectives don't snag on well plate surfaces.

![operation/11](operation/11_well_plate.png)

## On the microscope

Insert into microscope's stage insert (standard "K" frame or larger z-piezo openings both supported); well plate in, with ~5 mm of medium (~49 mL).

![operation/12](operation/12_insert_scope.png)

Lower the frame onto the insert's kinematic coupling.

![operation/13](operation/13_snap_frame.png)

Snap the end effector onto its wrist coupling and press its two tubes into `x_face`'s clips: the lip clip, then up the vertical clips. Their line runs to the actuators' syringe assembly beside the scope.

![operation/14](operation/14_snap_actuators.png)

Snap the capillary mount onto its coupling; its tubes run to the dispensors' and captures' syringe assemblies.

![operation/15](operation/15_snap_capillaries.png)

Lid onto its coupling; its cable into the main board's J20.

![operation/16](operation/16_snap_lid.png)

Lower the condenser; microscope is ready to image.

![operation/17](operation/17_complete.png)

# Run experiment

The unit takes 24 V DC, centre positive, into J15 (a 5.5 x 2.1 mm plug), from a supply set to 24.0 V with its trimmer locked; never above, as the boards' surge-protection diodes conduct from 26.7 V. The [main board guide](hardware/frame/main_pcb/assembly.md) ("The 24 V supply") covers the supply's mains wiring and its -V to earth link. Connect Ethernet (J19) to the PC running the application specified above.

The three switches run front to back from the operator and switch on toward the back: PWR (SW9) the unit, MOTORS (SW11) the rail and the fine axes, LID HEAT (SW10) the lid's heater. Switch on PWR, then MOTORS while positioning, then LID HEAT. Once the organisms are placed, switch MOTORS off again: leave it off for incubation and whenever nobody is at the unit (unpowered, nothing can move, stall or heat). Upon shutdown, flip the switches in reverse order and disconnect power and Ethernet. Consult [operation/README.md](operation/README.md) for the detailed rules of operation, including moves the limit switches don't guard against.

![operation/18](operation/18_power_on.png)

Many configurations are possible. Here we show one where the left actuator serves as a pick-and-place effector, while the right carries a sharper tip for cutting, injection, or microsurgery. On the static side, we show two different dispensing lines, perhaps with different strains or cell lines. Visual feedback confirms pick and place from respective dispensing lines. Other lines are imaging mounts, in which organisms or cells are held either by syringe action (classical) or by gravity-driven hydrostatic pressure difference (typically resulting in a much smaller force, still stable for multi-day imaging and suitable for plastic deformations occuring during growth and development). 

![operation/19](operation/19_operation.png)

Any configuration of the capillaries as mounts, actuators, or tools is in principle possible; the static capillary row serves as a "Turing tape" of sorts. V2 will support mid-experiment control using direct acquisition PC commands e.g. as Experiment Designer blocks in ZEN.

# Cleanup & preparation for next use

After use, all surfaces contacting fluid bodies (highlighted in blue), should be cleaned or disposed of. These include the inlet and outlet lines and well plate bath and flask reservoirs. The contacting surfaces of `mmi` to these bodies is minimized to a few non-disposable components.

![operation/20](operation/20_fluids.png)

The immersed temperature probes and glass syringes, highlighted in red, require cleaning and/or disinfection e.g. with 70% ethanol. The syringes can be autoclaved, but not the probes.

![operation/21](operation/21_disinfect.png)

All other materials can be disposed of and replaced sterile between uses: culture flasks, tubing, stopcocks, Luer barbs, capillaries, and well plate-cover slip assembly.

![operation/22](operation/22_dispose.png)
