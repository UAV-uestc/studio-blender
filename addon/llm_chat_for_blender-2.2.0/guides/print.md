# Guide: 3D printing (FDM). Read by the AI with h.guide("print") when it needs it.
# Edit freely (LLM Chat menu > Edit Guide); Reset restores the shipped one.
# Lines starting with "# " are notes and aren't sent.

UNITS AND PRECISION
- h.print_setup() once: 1 unit = 1 mm. Every size comes from the brief, a datasheet or a
  measured reference rather than the eye. Put the dimensions as named variables at the top of
  the build script (W = 113.0, WALL = 2.0, ROD_D = 3.0...), so a change is one number.
- Scaled models (1:2, 1:2.5...): divide every real size by the scale. Features that end up
  under 0.8 mm (antennas, cables, thin plates) get thickened to 0.8-1.2 mm or left out; say which.

MODELLING LIKE CAD
- Solids from bmesh (create_cube/cone/circle + extrude, scale verts to exact sizes) and
  h.boolean(target, cutters, "DIFFERENCE"|"UNION"|"INTERSECT"): one call takes all cutters
  (Manifold solver, milliseconds). Build many holes/slots as cutters and subtract once.
- Rounded edges: Bevel modifier (width in mm, segments 2-4, limit by angle) then apply; fillets
  on the outside, chamfers (45 deg) on edges that face down while printing.
- Holes: 32-64 segments for a round hole; horizontal holes > 6 mm as a teardrop (45 deg top)
  or print the part so the hole is vertical.
- Engraved/embossed text: h.label(part, "L1 NADIR", face="+Z", size=5, depth=0.6). Legible
  minimum on a 0.4 nozzle: letters 4 mm tall, 0.5-0.6 mm deep/high, simple words.
- Threads: real threads below M6 don't print well: use a hole for a self-tapping screw
  (M3: 2.5 mm hole), a heat-set insert hole (M3 insert: 4.0-4.2 mm, 5.5 mm deep) or a
  captive hex-nut pocket (M3 nut: 5.7 mm across flats + 0.2).
- Each printed part is one watertight object named for what it is ("Tray L1", "Part 07
  CubeStar"); parts printed separately stay separate objects.

FIT AND TOLERANCES (per side, 0.4 nozzle, PLA/PETG)
- Press fit 0.05-0.1 mm, snug/sliding 0.15-0.2, loose 0.3, parts printed in place
  (hinges) 0.4-0.5. A 3 mm rod goes in a 3.3-3.4 mm hole to slide, 3.1-3.15 to grip.
- Holes print ~0.1-0.2 mm small, outer sizes ~0.1 big: put the clearance in the model.
- Snap fits: cantilever length >= 5x its thickness, 0.5-1 mm hook, PETG better than PLA.
- Stacking parts: locating lips/pins 2-3 mm tall with 0.2 mm clearance, chamfered entries.

DESIGN RULES
- Walls >= 0.8 mm (two lines), 1.2-2 mm for anything handled. Ribs/fins >= 0.8 mm.
- Overhangs up to 45 deg print clean; bridges up to ~20 mm. Beyond that: reorient, split,
  add a 45 deg chamfer, or plan supports (and say so).
- A flat face on the bed, as large as possible. Chamfer bottom edges 0.4-0.5 mm (first layer
  squish, "elephant foot").
- Orientation: layers are the weak direction. Pins, clips and hooks lie flat, so the load runs
  along the layers.
- Bigger than the bed: h.print_split(part, "Z"|"X"|"Y", at=...) cuts it with pin holes for
  1.75 mm filament pieces (or set pin_d). Cut where the seam is hidden or flat.
- Text, labels and small details on top faces or vertical faces, not on the bottom.

CHECK
- h.print_check(parts): fix every problem it lists. h.print_fix(part) repairs holes, normals
  and doubles; h.print_fix(part, union=True) also fuses overlapping pieces into one solid.
- Look: get_viewport_screenshot(view="ALL", target=part), and frame joints and fits close up.
  For internal parts, a section: a copy of the part cut by h.boolean INTERSECT with a box.

DELIVER
- Keep the parts assembled in the scene; give each one its print orientation at export:
  h.print_export(parts, "name", rotate={"Part 07": (180, 0, 0)}) -> .3mf per plate (mm,
  named objects, packed with gaps; opens in Creality Print, OrcaSlicer, Bambu Studio,
  PrusaSlicer). fmt="stl" gives one STL per part. h.print_arrange previews the layout in the
  scene; h.print_arrange(parts, restore=True) puts the parts back.
- h.print_slice(paths, quality="0.20mm Standard", filament="Generic PLA", settings=None) ->
  G-code with print time and grams, using the slicer's own profile for the printer.
  settings= changes OrcaSlicer keys on top, e.g. {"filament_max_volumetric_speed": 8,
  "nozzle_temperature": 230, "nozzle_temperature_initial_layer": 230}. Qualities: 0.08mm
  SuperDetail, 0.12mm Fine, 0.16mm Optimal, 0.20mm Standard, 0.24mm Draft. Fine detail and
  small text: 0.12 or 0.16. No slicer installed: the .3mf is the deliverable.
- Creality K1C: 220 x 220 x 250 mm, 0.4 mm nozzle, enclosed (PLA with the lid open, PETG, ABS,
  CF filaments). The user sends the .gcode (or the .3mf) from Creality Print to the printer.

REPLY
- Files (full paths), parts and plates, each part's size, print time and filament, and what
  the user does after printing (pins to cut, glue, inserts, supports to remove).
