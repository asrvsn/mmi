# Heating board: JLCPCB order

The heating board (its silkscreen says "LID BOARD v16"): 129.5 x 70 mm, 1.6 mm aluminium, one copper layer, 68 parts
on the copper side, ordered assembled. The top plate is a separate bare order
([`../../plate_pcb/jlc/ORDER.md`](../../plate_pcb/jlc/ORDER.md)) and can go in the same cart. JLCPCB's form changes:
if it disagrees with this file, follow the form and use the reasons here to choose.

## Files to upload

| Field on the form | File |
|---|---|
| Add gerber file | `heating_gerbers.zip`: copper, mask, paste, silkscreen, outline with the slot, and the unplated drill file (6 x 3.2 mm). No plated holes, so no PTH file |
| BOM | `heating_bom_jlc.csv`: 37 lines, 68 parts |
| CPL | `heating_cpl_jlc.csv`: 68 rows, all Top. Rotations are in JLCPCB's convention, checked against their own footprints |

If you change the board, regenerate all three from `../heating.kicad_pcb` / `.kicad_sch` before uploading.

## PCB options

- **Base material: Aluminum.** Not copper core: JLCPCB's copper core conducts only through drawn pedestals. With the
  Peltier pads as drawn it would be worse (1.7-2.0 K/W per module against 0.3-0.4 K/W) and ~65 g heavier.
- **Layers: 1.** Dimensions, read from the Gerbers: **129.5 x 70.0 mm**.
- **PCB qty: 5** (the minimum). Delivery format: **Single PCB**.
- **Thickness: 1.6 mm.** The frame, the M3x10 screws and the top plate are sized for it.
- **Thermal conductivity: 1 W, 3000 V** (the only choice). The dielectric is JLCPCB's pick (Hangyu HY-7000 or
  Huazheng HA60).
- **Copper: 1 oz** (the only choice; 2 oz and ENIG aren't offered on aluminium).
- **Solder mask white, silkscreen black.** The copper side faces up under the top plate and the bare aluminium faces
  the chamber (painted at assembly). All masks radiate alike in the infrared, so the colour costs nothing thermally;
  white is free and the easiest to inspect.
- **Surface finish: HASL (with lead).** The twelve Peltier leads and the cable are hand-soldered onto pads that leak
  heat through the dielectric into the core. Leaded solder (183 °C against ~217 °C) wets sooner at a lower iron
  temperature, which is kinder to the Peltiers' low-melt internal joints. Use leaded wire for the hand joints. If
  JLCPCB switch the finish to lead-free, nothing else changes.
- **Electrical test: Flying Probe Fully Test.** The form defaults aluminium to "Not Test": change it. An etching open
  would hide in the long single-layer runs (the string, the tracks round the slot).
- **Confirm production file: Yes**, and tick "Do not confirm automatically" (checkpoint 6).
- **Mark on PCB: Remove Mark.**
- **Anything else the form shows:** product type Industrial/Consumer electronics; different design 1; IPC Class 2.
  Press-fit holes, blind slots, backdrill, humidity indicator card, paper between boards, inspection report: No.
  Silkscreen technology: the cheaper one (only R18's label is under 1 mm, at 0.8 mm).
- **PCB remark:** none.

## Assembly options

- **PCB Assembly: on.** PCBA type **Standard** (the only choice: Economic takes only 2-, 4- and 6-layer boards).
  Assembly side **Top**.
- **PCBA qty: all of them** (= PCB qty). With equal quantities the form offers no shortage choice, and a bare board
  that fails is remade before assembly.
- **Edge rails / fiducials: Added by JLCPCB.** Expect two 5 mm rails along the long edges (129.5 x 80 mm with them).
- **Depanel boards and edge rails: Yes.** An aluminium V-score needs a vise and some force; JLCPCB break them by hand
  (+1 day). Either way, check the board is flat when it arrives.
- **Confirm parts placement: Yes**, and tick "Do not confirm automatically" (checkpoint 7).
- **Parts selection: By Customer.**
- **Bake components: No.** The board has no LEDs, and JLCPCB's dry storage covers its ICs.
- **Board cleaning: No.** TCO1 and RT1-RT3 forbid it:
  - Bourns' SC-series cautions (TCO1): "The breaker is not washable. Do not wash", and no ultrasonic vibration during
    or after mounting.
  - Murata's NCP18 notice (RT1-RT3): "Please do not clean the products in the case of using a non-washed type flux".
    JLCPCB's paste is no-clean.
- **Conformal coating: No.** Their nozzle's smallest area (17 x 17 mm) can't keep the coat off the Peltier pads, their
  lead pads and J1-J3. The copper side is coated by hand once those are soldered (`../assembly.md`, step 12).
- **Special stencil: No.** Nothing is finer than 0.5 mm pitch or smaller than 0402.
- **Flying probe test (assembled): No. Function test: No.** The bench test on arrival covers it: `../assembly.md`
  steps 1 and 6, at TP1 (9 V), TP2 (3V3) and TP4 (GND).
- **Photo confirmation: Yes** (checkpoint 8).
- **Packaging:** ESD + Cardboard. **Solder paste:** High temp. (SAC305, the only paste offered with leaded HASL).
  **Nitrogen reflow:** No. **Stencil / fixture storage:** No.
- **Review Before Payment: Yes**, if the form offers it.
- **Assembly remark: Yes.** Paste this as it is (it fits the 500-character box):

> The six large exposed copper pads (Peltier modules) and the pads of J1, J2 and J3 are not assembled; please keep
> them free of paste. Please do not clean or wash this board: TCO1 (Bourns SC72AAB thermal cutoff, C2859510) and
> RT1-RT3 (NTC thermistors, C13564) must not be washed or cleaned (no water, solvent, ultrasonic or dry-ice cleaning).

**Stock:** TCO1 (C2859510, the string's 72 °C thermal cutoff) is an extended part with little stock. Check there is
one per board before ordering.

## Checkpoints

**Two rules for the whole order:**
- Never rotate, move, swap or re-match a part in JLCPCB's tools. If something is wrong, fix the KiCad source or the
  CSV, regenerate, and upload again, so the order and the design can't drift apart.
- After any change JLCPCB make (an answer to their question, a corrected placement), check what they send again
  before you confirm.

**What to compare against:** `../heating.kicad_pcb` in KiCad (the PCB editor and its 3D viewer), or the zip opened in
KiCad's Gerber viewer. Screenshot each step.

1. **After uploading the zip.** The form reads 1 layer and 129.5 x 70.0 mm. In JLCPCB's Gerber viewer:
   - the outline with the slot (93 x 10 mm, R1 corners);
   - six 3.2 mm unplated holes and no plated ones;
   - mask openings only at the pads.
2. **The PCB options set:** every option as listed above.
3. **The BOM page:** 37 lines, each matched to exactly the CSV's LCSC part, with no line unmatched, short or "not
   selected", and no extra lines. JP1-JP4 are parts too (0 Ω: JP1 0603, JP2-JP4 1206).
4. **The placement preview** (top side, zoomed in). Each part sits on its pads, with its pin-1 or polarity mark on our
   silkscreen's mark:
   - D1 and D3 (cathode bands), D2 (pin 1), Q1, and pin 1 of U1-U5;
   - TCO1 lengthwise along X (its pads 3.42 mm apart in X, 2.3 mm in Y);
   - nothing on the six Peltier pads or their lead pads, J1-J3, or TP1, TP2 and TP4;
   - any rotations listed equal the CPL's.
5. **Before paying:** the order summary matches this file, the remark word for word.
6. **"Confirm production file"** (after paying). Download their production Gerbers and lay them over ours, layer by
   layer:
   - 1 layer, 129.5 x 70.0 mm, the slot milled, no plated holes;
   - the Peltier pads and the lead pads bare, and every trace under mask;
   - the mask dam (≥ 0.25 mm) between the hand-soldered pads (Peltier lead pads, J1-J3) and the traces beside them is
     kept, so no trace is bared next to those pads;
   - note their panel: the rails and how they join (V-cut).
7. **"Confirm parts placement"** (their DFM review). Did they turn, move or drop any part? Every line should say
   Assemble = Yes. JP2 and JP4 sit across a 24 V / ground crossing: no solder spreading from their pads towards the
   track under them (≥ 0.7 mm, under mask). Answer their questions only after this check.
8. **Photo confirmation.** Download the full-resolution photos, and check:
   - every BOM part is present, and checkpoint 4's polarity list is the right way round;
   - no bridges on U3's and U4's 0.5 mm pins or on D2 (SC-88);
   - nothing on a hand-fitted footprint.
9. **On arrival.** Inspect within 7 days:
   - photograph both sides;
   - check the board is flat with a straightedge;
   - bench-test it the same day: `../assembly.md` steps 1 and 6.
