# Drive board: JLCPCB order

What to upload and what to tick on JLCPCB's order form for the drive board: 72.4 x 34.4 mm, 4 layers, 52 SMT parts,
all on the top side. What you fit by hand afterwards, and the checks on arrival: [`../assembly.md`](../assembly.md).

- **Service: PCBA Standard.** Every part sits at least 2.5 mm in from the edges, so JLCPCB's edge rails are no
  problem, and Standard adds solder-paste inspection and photo confirmation.
- **The main and heating boards can go in the same order** and ship together. Order no function test on any of them.
- JLCPCB's form changes. If it disagrees with this file, follow the form and use the reasons here to choose.

## Files to upload

| Field on the form | File |
|---|---|
| Add gerber file | `drive_gerbers.zip`: 4 copper layers, 11 Gerbers, plated and unplated drill files |
| BOM | `drive_bom_jlc.csv`: 25 lines, 52 parts. C68-C70 are not fitted and not listed. C19 and C56 (both C14663) share one line: JLCPCB's BOM page leaves a part listed on two lines unselected |
| CPL (pick and place) | `drive_cpl_jlc.csv`: 52 rows, all **top**. Rotations are in JLCPCB's convention, checked against their own footprints (Q3 and Q5 differ from KiCad's by 180°: their SOT-23 is drawn turned) |

## PCB options

- Base material **FR-4**; layers **4**; dimensions **72.4 x 34.4 mm** (read from the Gerbers).
- PCB qty **10**, Shortage preference **Accept Shortages**, max **4**. A bare board that fails JLCPCB's tests isn't
  assembled; with 10 made for 5 assembled, a shortfall ships as is instead of waiting 2-4 days for a remake. (5 made
  and 5 assembled works too; the form then offers no shortage choice.)
- Product type **Industrial/Consumer electronics** (Aerospace and Medical are stricter, dearer builds); different
  design **1**; delivery format **Single PCB**.
- Thickness **1.6 mm**.
- Material **FR4 TG155**: JLCPCB's recommendation for 4 layers and lead-free assembly. If you pick a brand, avoid
  KB6164 (it carries the laminate maker's watermark).
- Specify layer sequence **No**: the file names give it (checked at checkpoint 1).
- Stackup **JLC04161H-7628**, impedance control **no**: the stackup the board was designed for; nothing on it needs
  controlled impedance.
- Solder mask **green**, silkscreen **white** (green is the fastest colour).
- Surface finish **ENIG**, 1 u": flat pads for the HTSOP / PowerPAD thermal pads (U3, U4, U8, U9). HASL is domed.
- Copper: outer **1 oz**, inner **0.5 oz**.
- Via covering **Epoxy Filled & Capped**: there are vias in the thermal pads of U3, U4, U8, U9 and one at R33's pad 1.
  Plugged vias can't sit in a pad; open ones wick solder away and leave voids under the thermal pads.
- Via plating method **Horizontal Electroless Copper Plating** (the form sets it with filled vias).
- Min via hole / diameter: default (the vias are 0.30 / 0.60 mm).
- Board outline tolerance **±0.2 mm**.
- Confirm production file **Yes**, and tick **"Do not confirm automatically"** (otherwise it goes to production by
  itself after 48 h).
- Mark on PCB **Remove Mark**.
- Electrical test **Flying Probe Fully Test**: every bare board tested for opens and shorts.
- 4-wire Kelvin test **No** (Advanced Options): no via carries current on its own; power runs on via pairs and planes.
- Silkscreen technology **High-precision Printing**: some references are 0.8 mm, under the standard 1 mm minimum.
- Gold fingers, castellated holes, edge plating, UL marking **No**; press-fit holes, blind slots, backdrill **No**.
- Humidity indicator card **No**; paper between PCBs **No**; appearance quality **IPC Class 2 Standard**; package box
  either; inspection report **No**.
- PCB remark **Yes**, pasted as is (195 of the box's 200 characters):

> Epoxy filled & capped vias: please also fill and cap the thermal via holes in the thermal pads of U3 (8 x 0.30 mm)
> and U4, U8, U9 (6 x 0.33 mm each). The drill file lists them as component holes.

Why: KiCad's drill file tags the vias a footprint carries (the thermal pads') as component holes, in tools of their
own (0.30 mm x 8, 0.33 mm x 18), apart from the board's 97 vias. JLCPCB fill vias up to 0.5 mm, but a hole they take
for a component hole stays open and wicks solder from the thermal pad.

## Assembly options

- PCB Assembly **on**; PCBA type **Standard**; assembly side **Top side**; PCBA qty **5**.
- Edge rails / fiducials **Added by JLCPCB**: two rails along the long edges (about 72.4 x 70 mm with them), V-scored
  by default. The copper is 0.5 mm in from every edge (JLCPCB want at least 0.4 mm from a V-score).
- Confirm parts placement **Yes**, and tick **"Do not confirm automatically"** (otherwise it goes ahead after 72 h).
- Parts selection **By Customer**.
- Bake components **No**: no LEDs or QFNs; JLCPCB's dry storage covers the ICs.
- Board cleaning **No**; conformal coating **No**: the board lives dry, between its covers.
- Special stencil **No**: nothing finer than SOT-23's 0.95 mm pitch or smaller than 0402.
- Depanel boards and edge rails **No**: snap them off yourself (`../assembly.md`, step 1); nothing fragile lies across
  the edges.
- Flying probe test (assembled) **No**; function test **No**: the arrival checks (H0, H2) find the same in an hour.
- Photo confirmation **Yes**: the only check after which JLCPCB fix a wrong part before shipping.
- Packaging **ESD + Cardboard** (the tall inductor L1).
- Solder paste **High temp.** (SAC305, the default; the bismuth pastes make weaker joints).
- Nitrogen reflow **No** (1-2 days more; ENIG wets well in air).
- Stencil / fixture storage **No**; assembly remark **none**.
- **Review Before Payment:** use it if the form offers it.

## Checkpoints

Answer each of JLCPCB's confirmations the same day: the order waits for you. Two rules throughout:

- **Never rotate, move, swap or re-match a part in JLCPCB's tools.** The files were checked against JLCPCB's own
  footprints. If something looks wrong, compare it with `../drive.kicad_pcb` first; if the files are at fault, fix the
  KiCad board, regenerate them and upload again, so the order and the design can't drift apart.
- **After any change JLCPCB make** (a corrected placement, an answer to their question), check what they send back
  again before you confirm.

1. **After uploading the Gerber zip.**
   - The form reads 4 layers and 72.4 x 34.4 mm.
   - In their Gerber viewer, compare each layer with KiCad's (`../drive.kicad_pcb`, or the zip in KiCad's Gerber
     viewer). Inner-layer order is the classic misread: **In1 is a solid GND plane, In2 a solid +9V plane.** Tell them
     apart by which vias their thermal spokes join.
   - Drill view: 147 plated holes (0.30-1.0 mm), 2 unplated (2.2 mm, MH1 and MH2).
2. **PCB options set:** every option as listed above; the stackup dialog shows JLC04161H-7628 with 1 oz outer and
   0.5 oz inner layers; via covering Epoxy Filled & Capped.
3. **The BOM page:** every row matched to exactly the LCSC part in `drive_bom_jlc.csv`, in stock, with no row
   unmatched, short or "not selected", and no extra rows.
4. **The placement preview (top side).** Zoom in on D4, D6, D15 (cathode bands), Q3, Q5, and pin 1 of U3, U4, U8, U9.
   - Each part's pin-1 or polarity mark sits on our silkscreen's mark (the preview draws our silkscreen from the
     Gerbers), and each part sits on its pads.
   - Any rotations the preview lists match `drive_cpl_jlc.csv` (not KiCad's numbers).
   - C68-C70 stay empty; nothing on the ribbon pads J2, the wire holes, J17 or the test pads.
5. **Before paying:** the summary matches this file's options exactly, the PCB remark word for word.
6. **"Confirm production file"** (their CAM data, a zip, after paying):
   - 4 layers, 72.4 x 34.4 mm, the two notches at the ribbon end; their panel's rails, and how they join (V-cut or
     mouse bites);
   - copper 0.5 mm in from every edge;
   - every via in a pad on the `sk` (filled-via) layer: U3's 8 x 0.30 mm and U4's, U8's and U9's 6 x 0.33 mm thermal
     vias (the remark), and the +9V via at R33's pad 1. If any are missing, reply asking for them.
7. **"Confirm parts placement"** (their DFM review): compare their corrected and original placements and the DFM
   table. Every line Assemble = Yes; the same polarity list as checkpoint 4; C68-C70 empty; nothing on the ribbon pads,
   wire holes, J17 or test points. The CPL already matches their footprints, so a part they still turn deserves a
   look before you answer.
8. **Photo confirmation** (download the full-resolution photos): every BOM part present and the right way round, the
   thermal-pad parts (U3, U4, U8, U9) seated flat, no visible bridge, nothing on a hand-fit footprint.
9. **On arrival:** inspect within 7 days (the claim window to rely on), with the same questions as the photos; then
   `../assembly.md` from step 1.
