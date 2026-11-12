# Main board: JLCPCB order

What to upload and what to choose on JLCPCB's order form for the main board (`../main.kicad_pcb`): 4 layers,
42 x 95 mm, every SMT part on the **underside**, assembled by JLCPCB. The parts you solder by hand afterwards are in
[`../assembly.md`](../assembly.md).

**The brief behind the choices:** the boards must work first time. A re-order costs 2-3 weeks, so any option that
costs hours or a day is worth taking when it removes a way for a whole batch to come out wrong. JLCPCB's form,
wording and defaults change: if the form disagrees with this file, follow the form and use the reasons here to choose.

## Files to upload

| Field on the form | File | Size |
|---|---|---|
| Add gerber file | `main_gerbers.zip`: 11 Gerbers (4 copper, 2 mask, 2 paste, 2 silkscreen, outline) + plated and unplated drill files | 382,221 bytes |
| BOM | `main_bom_jlc.csv`: 49 lines, 132 parts, every line with its LCSC number | 2,243 bytes |
| CPL (pick and place) | `main_cpl_jlc.csv`: 132 rows, all `Bottom` | 4,757 bytes |

- Upload exactly these three files, nothing else.
- **The CPL's rotations are JLCPCB's convention for bottom-side parts** (seen from below: 180° minus KiCad's
  rotation), with per-part corrections where JLCPCB's own footprint for an LCSC number is drawn turned against
  KiCad's. They were checked pad by pad against JLCPCB's footprints. Don't regenerate the CPL with a KiCad plugin:
  the common ones turn 1-3 parts on this board wrongly (the SOT-23s, or the TMC2209).

### Before you upload

1. **Stock:** every LCSC number in the BOM in stock for your quantity plus JLCPCB's attrition. A part that has gone
   out of stock needs a substitute of the same footprint, value and rating, changed in the BOM CSV (and the
   schematic), and its polarity checked at checkpoint 4.
2. **If you changed the board**, don't upload the files here. Regenerate them, and check them before you order:
   - ERC clean; DRC with schematic parity and the pours refilled, 0 errors and 0 unconnected (this is the copper
     JLCPCB gets):
     ```sh
     kicad-cli pcb drc --refill-zones --schematic-parity --severity-error -o drc.rpt main.kicad_pcb
     ```
   - the Gerbers and drill files, as these were made (in `main_pcb/`):
     ```sh
     G=jlc/gerbers; mkdir -p $G
     kicad-cli pcb export gerbers --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Mask,B.Mask,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,Edge.Cuts \
        --subtract-soldermask --no-x2 --no-netlist --check-zones -o $G/ main.kicad_pcb
     kicad-cli pcb export drill --format excellon --excellon-units mm --excellon-zeros-format decimal \
        --excellon-oval-format alternate --excellon-separate-th -o $G/ main.kicad_pcb
     (cd $G && zip -j ../main_gerbers.zip *.gtl *.g1 *.g2 *.gbl *.gts *.gbs *.gtp *.gbp *.gto *.gbo *.gm1 *.drl)
     ```
   - every part still >= 2.5 mm from the board edges (JLCPCB's assembly rule), and the BOM and CPL listing the same
     parts, without the hand-fitted ones (J1, J3, J15, J16, J19, J20, J23, SW9-SW11, C3, C12, the Teensy's sockets,
     the test points);
   - then hold every polarised part's rotation against JLCPCB's placement preview (checkpoint 4) before you pay.

## PCB options

| Option | Choose | Why |
|---|---|---|
| Base material | **FR-4** | |
| Layers | **4** | |
| Dimensions | read from the Gerbers: **42 x 95 mm** | |
| PCB qty | **5** (or more for spares) | with PCB qty = PCBA qty the only shortage setting is **Require Full Quantity**: a bare board that fails JLCPCB's test is remade first (+2-4 days) |
| Product type | **Industrial/Consumer electronics** | Aerospace and Medical are stricter, dearer builds |
| Different design | **1** | one board in the zip |
| Delivery format | **Single PCB** | |
| Thickness | **1.6 mm** | |
| Material | **FR4 TG155** | JLCPCB's recommendation for 4 layers and lead-free assembly. If you pick a brand, not KB6164: it carries the laminate maker's watermark |
| Specify layer sequence | **No** | the file names give it (.gtl, .g1, .g2, .gbl); checkpoint 1 checks In1 and In2 by content |
| Stackup | **JLC04161H-7628** | the stackup the board was designed for |
| Impedance control | **No** | Ethernet runs ~30 mm of plain track |
| Solder mask / silkscreen | **Green / white** | green is the fastest colour |
| Surface finish | **ENIG, 1 u"** | flat pads for U2's 0.5 mm QFN, U5's PowerPAD and D13's 0.65 mm SC-88; HASL is domed. Hand-solders well |
| Outer / inner copper | **1 oz / 0.5 oz** | |
| Via covering | **Epoxy Filled & Capped** | vias sit in U2's and U5's thermal pads and at the edges of four GND pads (C33, C52, R22, R87). The default (plugged) can't plug a hole in a pad: it wicks solder away and leaves voids under the thermal pads |
| Via plating method | **Horizontal Electroless Copper Plating** | the form requires it with epoxy-filled vias |
| Min via hole / diameter | default (**0.3 / 0.4-0.45 mm**) | |
| Board outline tolerance | **±0.2 mm** (default) | nothing needs ±0.1 |
| Confirm production file | **Yes**, tick **"Do not confirm automatically"** | otherwise a small order goes into production after 48 h unconfirmed |
| Mark on PCB | **Remove Mark** | |
| Electrical test | **Flying Probe Fully Test** | tests every bare board for opens and shorts against the Gerbers' netlist |
| 4-wire Kelvin test | **No** (under Advanced Options) | no via carries current on its own here: power runs on via pairs and planes |
| Silkscreen technology | **High-precision Printing** | the underside references are 0.8-1.0 mm tall, and the top side's Teensy pin names and test-point labels are your probe map |
| Gold fingers, castellated holes, edge plating, UL marking | **No** | |
| Press-fit hole, blind slots, backdrill | **No** | none on this board |
| Humidity indicator card | **No** | JLCPCB assemble the boards; spares only get hand-soldered |
| Paper between PCBs | **No** | |
| Appearance quality | **IPC Class 2 Standard** | |
| Package box | either | |
| Inspection report | **No** | paid paperwork; the flying-probe test runs anyway |
| PCB remark | **Yes**, paste the text below (the box takes 200 characters) | |

**PCB remark (paste as is):**

> Epoxy filled & capped vias: please also fill and cap the 8 thermal via holes (0.30 mm) in U5's thermal pad. The
> drill file lists them as component holes, but they are vias in a pad.

Why: U5's footprint carries its own 8 thermal vias, which KiCad's drill file tags as component holes (a 0.30 mm
tool of their own), apart from the board's vias. JLCPCB fill vias up to 0.5 mm, but one they take for a component
hole stays open. U2's thermal vias are ordinary board vias already.

## Assembly options

| Option | Choose | Why |
|---|---|---|
| PCB Assembly | **on** | |
| PCBA type | **Standard** | all 132 parts are on the underside, where a rotation convention error turns a whole batch; U2 is a QFN no iron can rework. Standard adds solder-paste inspection, photo confirmation and baking; Economic offers none of them |
| Assembly side | **Bottom side** | |
| PCBA qty | **= PCB qty** | |
| Edge rails / fiducials | **Added by JLCPCB** | the board is under 70 mm wide: two rails along the long edges, about 70 x 95 mm with them. Every part is >= 2.5 mm from the edges |
| Confirm parts placement | **Yes**, tick **"Do not confirm automatically"** | the main defence against systematic errors; otherwise it goes ahead after 72 h |
| Parts selection | **By Customer** | |
| Bake components | **Yes** | the side-view LEDs and U2 are moisture sensitive; JLCPCB refuse complaints about LEDs that failed unbaked. Named in the remark |
| Board cleaning | **No** | the board lives dry, in its enclosure |
| Conformal coating | **No** | |
| Special stencil | **No** | nothing finer than 0.5 mm pitch or smaller than 0402 |
| Depanel boards and edge rails | **No** | keep the rails on in transit; snap them off yourself |
| Flying probe test (assembled) | **No** | it mainly tests passives, needs a file the form has no field for, and its failure handling is undocumented; the unpowered checks in `../assembly.md` cover the same faults |
| Function test | **No** | JLCPCB don't say what they do with a board that fails; the board's own tests do the same on day one |
| Photo confirmation | **Yes** | the only check after assembly where JLCPCB correct a wrong part before shipping |
| Packaging | **ESD + Cardboard** | protects the edge LEDs |
| Solder paste | **High temp.** (SAC305, default) | the low-temperature pastes make weaker joints |
| Nitrogen reflow | **No** | +1-2 days; ENIG wets well in air |
| Stencil / fixture storage | **No** | |
| Assembly remark | **Yes**, paste the text below (the box takes 500 characters) | |
| Review Before Payment | **Yes**, if offered | JLCPCB review the options and remarks before you pay, instead of pausing production to ask |

**Assembly remark (paste as is):**

> All parts are on the bottom side. D8, D9, D11 and D16 are side-view LEDs 2.6 mm in from the top board edge with
> the lens facing that edge. Their anodes go on the pads marked "+" on the silkscreen; D9's red cathode (R-, its
> pin 1) goes on the pad marked "1". Please bake D8, D9, D11, D16 (C965877, C965884, C965874, C965876) and U2
> (C2150710) before assembly.

The remark names the silkscreen marks, not pad numbers: JLCPCB's own footprint for D11 (C965874) numbers its anode 1,
the KiCad footprint 2.

## Checkpoints

Two rules for the whole order:

- **Never rotate, move, swap or re-match a part in JLCPCB's tools.** If something is wrong, fix the file (the CPL or
  BOM here, or the KiCad board and regenerate) and upload again, so the order and the files can't drift apart.
- **After any change JLCPCB make** (an answer to their question, a corrected placement, edited data), check what they
  send back again before you confirm.

Screenshot each step at full resolution and keep them with the order. The reference for the board is KiCad: the board
file, or the zip opened in KiCad's Gerber viewer.

1. **After uploading the Gerber zip.** The form reads **4 layers, 42 x 95 mm**. In JLCPCB's Gerber viewer, top,
   bottom, each inner layer and the drill view match KiCad's. **In1 and In2 not swapped:** In1 is a ground pour cut
   by hundreds of signal tracks; In2 is a solid ground plane with no tracks. The upload boxes show the names and
   sizes in the table above.
2. **PCB options set.** Every option as listed above; the stackup dialog shows JLC04161H-7628 with 1 oz outer and
   0.5 oz inner layers; via covering Epoxy Filled & Capped.
3. **After uploading the BOM and CPL** (the Bill of Materials page). Every row matched to exactly the LCSC part in
   the BOM CSV: none unmatched, short of stock or set not to be placed, and no extra rows.
4. **The placement preview** (the bottom side). Zoom in on each polarised part and check JLCPCB's pin-1 / polarity
   mark against our silkscreen's (pin-1 triangles, "+" marks, diode bars), which their preview draws from our
   Gerbers. If the preview shows the bottom side from the top, compare mirrored.
   - D3, D5, D7, D10, D13 (cathode bands / pin 1);
   - D8, D9, D11, D16: lens towards the top edge, anodes on the "+" marks, D9's R- on the "1";
   - Q1, Q2, Q4, Q6, Q8; U2 (the QFN's pin 1), U5, U7;
   - every part on its pads, and nothing on a hand-fitted footprint (J3, J16, J23, the Teensy's socket rows, the
     test pads).
5. **Before paying** (the order summary). Every option as above, quantities, and both remarks word for word.
6. **"Confirm production file"** (after paying; JLCPCB's message with their production Gerbers, often an `ok`
   folder, and their panel image). Check:
   - 4 layers, 42 x 95 mm, In1 / In2 in their places;
   - J15's four holes are plated slots, end to end; the three M2.5 holes and J15's / J19's pegs unplated;
   - the filled-via layer (`sk`) holds every via in a pad: U2's and U5's thermal pads (U5's 8 by the remark) and the
     GND vias at the edges of C33's, C52's, R22's, R87's and U5 pin 6's pads;
   - the rails: **V-cut is accepted.** JLCPCB want copper >= 0.4 mm from a V-scored edge. The bottom edge and the
     front end keep it (copper >= 1.97 mm in), but along the top edge J16's mounting pad and J19 pin 13 sit
     0.30-0.32 mm in, and the GND pours 0.30 mm. At worst the score nicks a pad's outer edge or bares the pour at the
     edge: cosmetic. If JLCPCB ask, answer that V-cut rails are fine and the copper near the top edge is as designed.
7. **"Confirm parts placement"** (JLCPCB's DFM review, within hours: their corrected placement beside yours, a DFM
   table, perhaps questions). Did they turn, move or drop any part? Every line Assemble = Yes, nothing on a
   hand-fitted footprint, and each polarised part from step 4 still on its silkscreen mark. Since the CPL already
   uses their convention, their corrected placement should match the original; a part they still turn deserves a
   look. Answer their questions only after this.
8. **Photo confirmation** (the assembled boards, before shipping). Download the full-resolution photos, both sides:
   every BOM part present and the right way round (the step 4 list), no part on a hand-fit footprint, no bridges on
   U2's QFN edge or D13 (SC-88), D8, D9, D11, D16 whole. JLCPCB X-ray U2 automatically; its report is in the order
   history: no bridges between its pins, no voids over a quarter of its thermal pad.
9. **On arrival.** Inspect within **7 days**: that's the claim window to rely on. Photograph both sides of one board
   and check them as at 8. Then snap the rails off yourself:
   - the **top-edge rail first, bent towards the top (connector) side**, so it swings away from D8, D9, D11 and D16
     on the underside;
   - then the bottom rail (no large MLCC sits within 5 mm of it).

   Then [`../assembly.md`](../assembly.md), from "On arrival".

Answer each confirmation the same day: the order waits for you.
