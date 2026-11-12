# Top plate: JLCPCB order

The lid's top plate is the heating board's twin in 1.6 mm aluminium:
- the same outline (129.5 x 70 mm, R2 corners), imaging slot (93 x 10 mm, R1 corners) and six M3 holes (3.2 mm,
  unplated);
- no parts, pads or tracks: a copper fill under white mask on the room side, 0.5 mm clear of every edge and hole, and
  two silkscreen labels.

It lies aluminium face down on the six Peltiers' tops, on thermal paste, and is the lid's room-side radiator. It is
ordered bare, using JLCPCB's aluminium PCB service as a milling service, and can go in the same cart as the heating
board ([`../../heating_pcb/jlc/ORDER.md`](../../heating_pcb/jlc/ORDER.md)). If the form disagrees with this file,
follow the form and use the reasons here to choose.

## File to upload

| Field on the form | File |
|---|---|
| Add gerber file | `plate_gerbers.zip`: copper, mask (open only at the six holes), silkscreen, outline with the slot, and the unplated drill file (6 x 3.2 mm) |

## PCB options

- **Base material: Aluminum. Layers: 1.** Dimensions, read from the Gerbers: **129.5 x 70.0 mm**.
- **PCB qty: 5** (the minimum). Shortage preference: default. Delivery format: **Single PCB**.
- **Thickness: 1.6 mm.** The M3x10 screws through board, frame and plate are sized for it.
- **Thermal conductivity: 1 W, 3000 V. Copper: 1 oz.** Both are the only choice.
- **Solder mask white, silkscreen black.** The mask covers everything but the holes. Solder mask radiates at ε ≈ 0.9
  in the thermal infrared whatever its colour (the resin sets it, not the pigment), so white sheds heat as well as
  black, and is free.
- **Surface finish: HASL (with lead).** No copper is exposed, so the finish doesn't matter.
- **Electrical test: Not Test.** There is no circuit.
- **Confirm production file: No.** There's no circuit and no assembly, and checkpoint 1 already shows whether JLCPCB
  read the outline, slot and holes right. It would only add a wait.
- **Mark on PCB: Remove Mark.** This face shows on top of the lid.
- **Anything else the form shows:** as on the heating board (Industrial/Consumer, design 1, IPC Class 2, the extras
  No). Silkscreen technology: the cheaper one (its two labels are 1.2 mm).
- **PCB remark: none.** If JLCPCB ask about a copper fill connected to nothing: it's a heat spreader with no circuit,
  as designed.
- **PCB Assembly: off. SMT stencil: none.**

## Checkpoints

1. **After uploading the zip.** The form reads 1 layer and 129.5 x 70.0 mm. In JLCPCB's Gerber viewer: the same
   outline, slot and six holes as the heating board, mask over all the copper, and the two labels.
2. **Options set, and before paying:** every option, and the order summary, match this file.
3. **On arrival:**
   - check it is flat with a straightedge;
   - lay it over the heating board: the outlines, slots and six holes line up.

Fitting it to the lid: [`../../heating_pcb/assembly.md`](../../heating_pcb/assembly.md), step 14 (MX-4 on the
Peltiers, aluminium face down, 6 x M3x10 button heads up from under the heating board into nuts on the plate).
