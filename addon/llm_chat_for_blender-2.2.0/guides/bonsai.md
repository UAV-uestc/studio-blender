# Guide: buildings and BIM with Bonsai (IFC). Read by the AI with h.guide("bonsai").
# Edit freely (LLM Chat menu > Edit Guide); Reset restores the shipped one.
# Lines starting with "# " are notes and aren't sent.

HELPERS (metres, points (x, y) on a storey; all return Blender objects unless noted)
- h.bim_project(name, storeys=(("Ground Floor", 0.0), ("First Floor", 3.2)...)): project,
  site, building, storeys (reuses the open project). First call loads Bonsai (~20 s).
- h.bim_type(name, "IfcWallType"|"IfcSlabType"|"IfcCoveringType", layers=(("Brick", 0.1),
  ("Mineral wool", 0.12), ("Concrete block", 0.15), ("Plaster", 0.015))): real build-ups,
  outside to inside / top to bottom. Profiles: h.bim_type("C 300x300", "IfcColumnType",
  (("Concrete C30/37", 0),), profile=(0.3, 0.3)) or profile=("CIRCLE", 0.15).
- h.bim_material(name, category, color=(r, g, b[, transparency])): materials with a colour.
- h.bim_walls(points, storey, height, wall_type, closed=True): joined walls along a polyline,
  returned in segment order (wall i runs from point i to point i+1); layers sit on the left of
  travel: counter-clockwise outer outlines put them inside.
- h.bim_slab(outline, storey, slab_type, top=0.0, predefined_type="FLOOR"|"ROOF"|"LANDING",
  holes=[outline, ...]): top at storey level + top, thickness below it; holes are real
  openings (stairwells, shafts). h.bim_opening(element, outline, bottom_z, top_z) cuts a void
  through any element.
- h.bim_door(wall, at, width, height, operation) / h.bim_window(wall, at, width, height, sill):
  `at` = metres from the wall's start to the opening's near edge; cuts a real opening.
  Operations: SINGLE_SWING_LEFT/RIGHT, DOUBLE_DOOR_SINGLE_SWING, SLIDING_TO_LEFT;
  partitions: SINGLE_PANEL, DOUBLE_PANEL_VERTICAL, TRIPLE_PANEL_VERTICAL.
- h.bim_member(start_xyz, end_xyz, type, storey): columns, beams, braces.
- h.bim_space(outline, storey, "1.02", height, long_name="Kitchen"): rooms; give floor area.
- h.bim_element(obj, "IfcStair"|"IfcRailing"|"IfcFurniture"|"IfcRoof"|..., storey): a mesh you
  modelled becomes an element (stairs, roofs with pitches, furniture, custom parts).
- h.bim_report(): storeys, counts, quantities (lengths, areas, volumes), problems.
- h.bim_drawing("PLAN", storey) / ("ELEVATION_N"|"_S"|"_E"|"_W") / ("SECTION_X"|"SECTION_Y",
  at=metres): SVG drawings with dimension chains, room names and areas, levels.
  h.bim_dimension([(x, y, z), ...]) adds a chain to the active drawing.
- h.bim_save(path=None) -> .ifc path. Everything else in Bonsai (schedules, sheets, costs) is
  available through bpy.ops.bim.* and ifcopenshell.api on bonsai.tool.Ifc.get().

WORKFLOW FOR A BUILDING
1. Brief to numbers: storeys and levels, gross footprint, room list with areas, structure
   (load-bearing walls or frame), wall/slab build-ups. Say the assumptions in one line.
2. Grid and outline first (exterior walls per storey), then slabs, then interior walls
   (thinner partition type, e.g. 0.1 m), then doors/windows, then spaces, stairs, roof.
3. One script per storey or per system; loops over room/opening tables keep it short:
   rooms = [("Kitchen", [(0,0),(4.2,0),(4.2,3.6),(0,3.6)]), ...].
4. h.bim_report() after each storey; the quantities must match the brief (areas, counts).
5. Screenshots of the 3D model (view ALL, and ISO of each storey with the upper ones hidden),
   then drawings: a plan per storey, two elevations, one section through the stair.
6. h.bim_save, reply with the .ifc and drawing paths and the key numbers.

DIMENSIONS THAT ARE USUALLY RIGHT (adapt to the brief and local code)
- Floor-to-floor 3.0-3.3 m (housing), 3.6-4.2 m (offices); clear ceiling >= 2.5 m.
- Exterior wall 0.30-0.45 m with insulation; partitions 0.10-0.15 m; slab 0.20-0.25 m.
- Doors 0.9 x 2.1 m (entrance 1.0-1.2 m), windows sill 0.9 m, head 2.1-2.2 m.
- Stairs: riser <= 0.175 m, tread >= 0.28 m, 2R + T = 0.60-0.64 m, width >= 1.0 m,
  headroom >= 2.1 m. Corridors >= 1.2 m. Accessible WC 1.7 x 2.2 m.
- Column grid 6-8 m for frames; beam depth ~ span / 12-15.

PITFALLS
- Plain Blender meshes are not exported to IFC: make them elements (h.bim_element) or put
  references in a _Reference collection.
- Don't move IFC objects by editing mesh vertices; move objects (their placement), and rebuild
  walls with h.bim_walls if a run changes.
- Drawings need a saved .ifc (h.bim_drawing saves it first); SVGs go to drawings/ next to it.
