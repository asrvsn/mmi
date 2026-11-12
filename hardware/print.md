# 3D printing

The printed parts are STEP files in each assembly's `parts/` folder:
[insert](insert/parts), [frame](frame/parts), [capillary mounts](capillary_mounts/parts), [lid](lid/parts).
Print one of each, except the gears (`gear30_bore_ID13.15`, `gear16_D-0.05`) and the main enclosure's switch cap
(`main_enclosure_cap`): three of each. No part carries text; keep each print with its file name.

## Printer and resin

- SLA, in a stiff engineering resin. Everything was designed and fit-tested on a Formlabs Form 3L in Rigid 4000, at
  0.05 mm layers; the fits below are from those prints.
- The parts' fine features (clips, pins, gear teeth, M2 nut pockets) need SLA resolution; FDM won't hold them.

## Orientation and the job

- Print flat faces vertical, at a 45° tilt.
- **The frame:** on edge, standing on its back face, tilted ~15° in its own plane with the bearing end lowest. The
  supports then land on the back face, and its top, which carries the rail, stays straight: a frame that curls as it
  prints binds the leadscrew.
- Give long, thin sections that must stay dimensionally accurate priority when orienting (the braces and faces of
  the XYZ, the frame's rail seat, the capillary mount's ramp).
- Don't crowd the motion parts (the X face, the braces, the gears) onto one platform, and print them on a resin tank
  in good condition: a crowded 0.05 mm job of those parts on a worn, clouding tank came out soft, torn and flaking.

## Post-processing

- Wash in clean IPA, not IPA shared with other resins, then post-cure as the resin's instructions say.
- Before gluing anything in (the coupling hardware, the gears), scuff the faces and wipe them with IPA.

## Fits designed into the files

Print the files true to size; the clearances are already in them. On the printer above, holes print about 0.2 mm
small on the diameter, so:
- **Push fits** are drawn +0.2: the wrist's Ø6 balls and 6 x 2 mm magnets sit in Ø6.2 pockets, a firm push.
- **The large parts' 8 x 3 mm magnet pockets** (insert, frame, capillary mount, lid frame) are Ø8.4: the magnets drop
  in and are epoxied (Ø8.2 needed a vice on those stiff parts).
- **The 2 x 10 mm dowel pockets** are 10.15 deep with end lips: the dowels press in.
- **Flush faces** (countersunk heads) are drawn 0.2 mm below flush; 90° countersinks are 0.4 mm wider for it.
- **Clips** (the tube and cord channels) are drawn to grip whether a hole prints true or 0.2 tight.
- **The gears:** `gear30_bore_ID13.15` is the 30T for the stage's 13 mm knob (13.15 bore: +0.075 a side, glued on);
  `gear16_D-0.05` is the 16T pinion for the servo spline, its tooth form offset -0.05 for a smooth mesh. The prototype
  compared it with a -0.1 offset and kept -0.05; if your 16T meshes tight or loose, try an offset of ±0.05.

On another printer or resin, print the insert first and check its magnet pockets and a gear pair before the rest.

## Known issues

- The limit switches' printed arms and their supports are thin: handle them with care.
- The XYZ stack's weight sits off-centre on the rail carriage and can tip it back slightly.
