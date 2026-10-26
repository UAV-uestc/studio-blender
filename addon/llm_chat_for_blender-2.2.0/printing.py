"""3D printing helpers (the 3D Printing mode): checks, repairs, CAD-style booleans,
labels, splitting, bed layout and 3MF/STL export, all in millimetres.

Imported into helpers (h.print_check, h.export_print...). The printer comes from the
add-on preferences (printer(), default Creality K1C).
"""

import math
import os
import shutil
import subprocess
import zipfile

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

# name: (bed x, bed y, max z mm, nozzle mm, OrcaSlicer vendor, machine profile)
PRINTERS = {
    "Creality K1C": (220, 220, 250, 0.4, "Creality", "Creality K1C 0.4 nozzle"),
    "Creality K1": (220, 220, 250, 0.4, "Creality", "Creality K1 (0.4 nozzle)"),
    "Creality K1 Max": (300, 300, 300, 0.4, "Creality", "Creality K1 Max (0.4 nozzle)"),
    "Bambu Lab A1": (256, 256, 256, 0.4, "BBL", "Bambu Lab A1 0.4 nozzle"),
    "Bambu Lab P1S": (256, 256, 256, 0.4, "BBL", "Bambu Lab P1S 0.4 nozzle"),
    "Prusa MK4": (250, 210, 220, 0.4, "Prusa", "Prusa MK4 0.4 nozzle"),
}
PLA_DENSITY = 1.24  # g/cm3


def printer():
    """(name, bed_x, bed_y, bed_z, nozzle) from the add-on preferences."""
    name, bed = "Creality K1C", PRINTERS["Creality K1C"]
    try:
        p = next(a.preferences for k, a in bpy.context.preferences.addons.items()
                 if k.endswith("claude_chat"))
        if p.printer == "custom":
            return ("Custom", p.bed_x, p.bed_y, p.bed_z, p.nozzle)
        name, bed = p.printer, PRINTERS.get(p.printer, bed)
    except (StopIteration, AttributeError):
        pass
    return (name, *bed[:4])


def _objs(items):
    from .helpers import _objs as objs
    return [o for o in objs(items) if o.type in {"MESH", "CURVE", "FONT", "SURFACE", "META"}]


def _mm():
    """Millimetres per Blender unit in this scene."""
    u = bpy.context.scene.unit_settings
    return (u.scale_length if u.system != "NONE" else 1.0) * 1000.0


def setup_mm():
    """Work in millimetres: 1 Blender unit = 1 mm, viewport clipping and grid to match.
    Run once at the start of a print project (existing objects keep their unit sizes)."""
    u = bpy.context.scene.unit_settings
    u.system, u.scale_length, u.length_unit = "METRIC", 0.001, "MILLIMETERS"
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                s = area.spaces.active
                s.clip_start, s.clip_end = 0.1, 100000
                s.overlay.grid_scale = 0.001
                solid_view(s)
    return "1 unit = 1 mm"


def solid_view(space):
    """Solid shading with cavity: engravings, chamfers and small steps show in screenshots
    (material preview flattens them)."""
    sh = space.shading
    sh.type, sh.light, sh.color_type = "SOLID", "STUDIO", "OBJECT"
    sh.show_cavity, sh.cavity_type = True, "BOTH"
    sh.cavity_ridge_factor, sh.cavity_valley_factor = 1.0, 1.5


def _world_bm(o, dg=None):
    """The evaluated mesh in world space, in mm."""
    if dg is None:
        bpy.context.view_layer.update()  # objects moved in this script: fresh matrices
        dg = bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    ev.to_mesh_clear()
    bm.transform(Matrix.Scale(_mm(), 4) @ o.matrix_world)
    if o.matrix_world.determinant() < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    return bm


def _shells(bm):
    """Connected pieces: list of face-index lists."""
    parent = list(range(len(bm.verts)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for e in bm.edges:
        a, b = root(e.verts[0].index), root(e.verts[1].index)
        if a != b:
            parent[a] = b
    groups = {}
    for f in bm.faces:
        groups.setdefault(root(f.verts[0].index), []).append(f.index)
    return list(groups.values())


def _label_boxes(o):
    """Engraved label regions (world mm, padded) recorded by label()."""
    flat = list(o.get("print_labels", []))
    m = Matrix.Scale(_mm(), 4) @ o.matrix_world
    boxes = []
    for i in range(0, len(flat) - 5, 6):
        pts = [m @ Vector((x, y, z)) for x in (flat[i], flat[i + 3]) for y in (flat[i + 1], flat[i + 4])
               for z in (flat[i + 2], flat[i + 5])]
        boxes.append((Vector([min(p[k] for p in pts) - 0.3 for k in range(3)]),
                      Vector([max(p[k] for p in pts) + 0.3 for k in range(3)])))
    return boxes


def _thin(bm, tree, min_wall, limit=6000, skip=()):
    """Area (mm2) and a sample location of faces whose wall is thinner than min_wall,
    measured by a ray from each face straight into the material. Only walls count (the
    surface hit faces back within ~18 deg); a wedge (thread crest or run-out, chamfer) is
    not a wall and prints as far as the nozzle can. Faces inside `skip` boxes (engraved
    text) are ignored."""
    faces = [f for f in bm.faces if f.calc_area() > 1e-6 and not any(
        all(lo[k] <= c[k] <= hi[k] for k in range(3)) for lo, hi in skip
        for c in [f.calc_center_median()])]
    if len(faces) > limit:
        faces = sorted(faces, key=lambda f: -f.calc_area())[:limit]
    area, where = 0.0, None
    for f in faces:
        n = f.normal
        if n.length < 0.5:
            continue
        start = f.calc_center_median() - n * 1e-3
        hit, _, idx, dist = tree.ray_cast(start, -n, min_wall)
        if (hit is not None and idx != f.index and dist < min_wall - 1e-3
                and bm.faces[idx].normal.dot(n) < -0.95):
            area += f.calc_area()
            if where is None:
                where = start
    return area, where


def _overhang(bm, tree, cos_o, floor, ledge):
    """Area (mm2) of faces steeper than the overhang angle that reach more than `ledge` mm
    past the material below them: a ray runs just under the face, down its slope (or
    sideways under a flat ceiling), to where the supporting surface is. Thread flanks,
    small chamfers and narrow bridges are shorter and print without supports."""
    area = 0.0
    for f in bm.faces:
        n = f.normal
        if n.z >= -cos_o or min(v.co.z for v in f.verts) <= floor:
            continue
        start = f.calc_center_median() + n * 0.02
        flat = math.hypot(n.x, n.y)
        if flat > 0.05:
            t = (Vector((0, 0, -1)) + n * n.z).normalized()
            dirs = [(t, ledge / max(math.hypot(t.x, t.y), 1e-3))]
        else:
            dirs = [(Vector(d), ledge) for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))]
        if not any(tree.ray_cast(start, d, reach)[0] is not None for d, reach in dirs):
            area += f.calc_area()
    return area


def check(objects=None, min_wall=None, overhang=45.0, ledge=1.5):
    """Printability report per object, sizes in mm. Overhangs reaching less than `ledge` mm
    past the layer below (threads, small chamfers) don't count. Returns (text, problems)."""
    bpy.context.view_layer.update()
    name, bx, by, bz, nozzle = printer()
    min_wall = min_wall or 2 * nozzle
    dg = bpy.context.evaluated_depsgraph_get()
    lines, problems = [f"printer {name}: bed {bx}x{by}x{bz} mm, nozzle {nozzle} mm, "
                       f"min wall {min_wall} mm"], []
    cos_o = math.cos(math.radians(overhang))
    for o in _objs(objects if objects is not None else
                   [o for o in bpy.context.visible_objects if o.type == "MESH"]):
        bm = _world_bm(o, dg)
        if not bm.faces:
            bm.free()
            continue
        bmesh.ops.triangulate(bm, faces=bm.faces)
        bm.verts.index_update()
        bm.faces.index_update()
        bm.normal_update()
        co = np.array([v.co[:] for v in bm.verts])
        lo, hi = co.min(0), co.max(0)
        size = hi - lo
        bad_edges = sum(1 for e in bm.edges if not e.is_manifold)
        shells = _shells(bm)
        vol = bm.calc_volume(signed=True) / 1000.0  # cm3
        tree = BVHTree.FromBMesh(bm, epsilon=1e-5)
        bm.faces.ensure_lookup_table()
        inter = set()
        for i, k in tree.overlap(tree):
            fi, fk = bm.faces[i], bm.faces[k]
            # Tiny coplanar slivers folded over each other (boolean leftovers in a groove
            # floor) don't change the solid a slicer sees.
            if fi.normal.angle(fk.normal, 0) < 0.02 and fi.calc_area() + fk.calc_area() < 0.5:
                continue
            inter |= {i, k}
        area = sum(f.calc_area() for f in bm.faces)
        thin, where = _thin(bm, tree, min_wall, skip=_label_boxes(o))
        floor = lo[2] + 0.2
        over = _overhang(bm, tree, cos_o, floor, ledge)
        base = sum(f.calc_area() for f in bm.faces
                   if f.normal.z < -0.99 and max(v.co.z for v in f.verts) < lo[2] + 0.05)
        bm.free()
        # Rough filament use: 2 perimeters/top/bottom solid, 15% infill inside.
        shell_cm3 = min(abs(vol), area * min_wall * 1.5 / 1000.0)
        grams = (shell_cm3 + (abs(vol) - shell_cm3) * 0.15) * PLA_DENSITY
        lines.append(
            f"{o.name}: {size[0]:.1f}x{size[1]:.1f}x{size[2]:.1f} mm, {len(shells)} shell(s), "
            f"volume {abs(vol):.2f} cm3 (~{grams:.0f} g PLA at 15% infill), base on bed "
            f"{base:.0f} mm2, overhangs >{overhang:.0f} deg {over:.0f} mm2, "
            f"open/non-manifold edges {bad_edges}, self-intersecting faces {len(inter)}, "
            f"walls < {min_wall} mm: {thin:.0f} mm2")
        why = []
        if bad_edges:
            why.append(f"{bad_edges} open/non-manifold edges (not watertight: h.print_fix)")
        if inter:
            why.append(f"{len(inter)} self-intersecting faces (overlapping parts: "
                       "h.print_fix(union=True) or h.boolean UNION)")
        if vol < 0:
            why.append("inside-out normals (h.print_fix)")
        if thin > 1.0:
            why.append(f"{thin:.0f} mm2 of wall thinner than {min_wall} mm, e.g. near "
                       f"({where.x:.1f}, {where.y:.1f}, {where.z:.1f}) mm: thicken it or it "
                       "won't print")
        if size[0] > bx or size[1] > by or size[2] > bz:
            fits_flat = sorted(size[:2]) <= sorted([bx, by]) and size[2] <= bz
            why.append(f"{size[0]:.0f}x{size[1]:.0f}x{size[2]:.0f} mm is bigger than the "
                       f"{bx}x{by}x{bz} bed" + ("" if fits_flat else ": h.print_split it"))
        if base < 20 and size[2] > 5:
            why.append(f"only {base:.0f} mm2 flat on the bed: lay a flat face down or add a "
                       "brim/support base")
        if area and over / area > 0.05:
            why.append(f"{over:.0f} mm2 overhangs steeper than {overhang:.0f} deg: reorient, "
                       "chamfer them to 45 deg, or plan supports")
        if len(shells) > 1:
            why.append(f"{len(shells)} separate pieces in one object (fine if meant to be "
                       "printed apart, else join them with h.boolean UNION)")
        if min(size) < nozzle * 2:
            why.append(f"smallest size {min(size):.2f} mm is under two nozzle widths")
        problems += [f"{o.name}: {w}" for w in why]
    lines.append("problems: " + ("none" if not problems else "\n- " + "\n- ".join(problems)))
    return "\n".join(lines), problems


def fit(parts, others=None, clearance=None):
    """Clearance between mating parts (mm): for each pair (every part against every one of
    `others`, or all pairs of `parts`): OVERLAP and how deep, TOUCHING (resting on each
    other, e.g. a part on a floor), TOO TIGHT (closer than `clearance`, default 0.15) or the
    min gap, with where. Returns the text."""
    from .helpers import _objs as objs
    clearance = 0.15 if clearance is None else clearance
    a_list = [o for o in objs(parts) if o.type == "MESH"]
    b_list = [o for o in objs(others) if o.type == "MESH"] if others is not None else None
    pairs = ([(a, b) for a in a_list for b in b_list if a != b] if b_list is not None else
             [(a, b) for i, a in enumerate(a_list) for b in a_list[i + 1:]])
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    cache = {}

    def tree(o):
        if o not in cache:
            bm = _world_bm(o, dg)
            cache[o] = (BVHTree.FromBMesh(bm), [v.co.copy() for v in bm.verts])
            bm.free()
        return cache[o]
    lines = []
    for a, b in pairs:
        ta, va = tree(a)
        tb, vb = tree(b)
        gap, where, depth, deep_at = 1e18, None, 0.0, None
        for t, verts in ((tb, va), (ta, vb)):
            for v in verts[::max(1, len(verts) // 20000)]:
                loc, n, _, d = t.find_nearest(v)
                if loc is None:
                    continue
                # Inside the other part: the nearest face's normal says so (unreliable at
                # edges, e.g. a nut around a thread), and a ray crosses it an odd number
                # of times.
                if (v - loc).dot(n) < -1e-3 and _inside_mesh(t, v):
                    if d > depth:
                        depth, deep_at = d, v
                elif d < gap:
                    gap, where = d, v
        if depth > 0.01:
            state, p = f"OVERLAP {depth:.2f} mm deep", deep_at
        elif gap < 0.01 or ta.overlap(tb):
            state, p = "TOUCHING (no gap)", where
        elif gap < clearance:
            state, p = f"TOO TIGHT, gap {gap:.2f} mm", where
        else:
            state, p = f"ok, min gap {gap:.2f} mm", where
        at = f" at ({p.x:.1f}, {p.y:.1f}, {p.z:.1f})" if p is not None else ""
        lines.append(f"{a.name} / {b.name}: {state}{at}")
    return "\n".join(lines) or "no pairs"


def _inside_mesh(tree, p):
    d, hits = Vector((0.2672, 0.5345, 0.8018)), 0
    while hits < 1000:
        loc = tree.ray_cast(p, d)[0]
        if loc is None:
            return hits % 2 == 1
        hits, p = hits + 1, loc + d * 1e-4
    return False


def fix(objects=None, union=False, merge=0.001):
    """Repair meshes for printing: merge doubles, drop loose bits and degenerate faces,
    fill holes, make normals point out. union=True also fuses overlapping shells into one
    solid (Manifold boolean). Applies modifiers. Returns a short report."""
    out = []
    for o in _objs(objects):
        _apply_all(o)
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge / _mm())
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=merge / _mm())
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bmesh.ops.holes_fill(bm, edges=[e for e in bm.edges if e.is_boundary], sides=0)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()
        if union:
            _self_union(o)
        _, probs = check(o)
        out.append(f"{o.name}: " + ("ok" if not probs else "; ".join(p.split(': ', 1)[1] for p in probs)))
    return "\n".join(out)


def _apply_all(o):
    if not o.modifiers and o.type == "MESH":
        return
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    o.modifiers.clear()
    if o.type == "MESH":
        old = o.data
        o.data = me
        if old.users == 0:
            bpy.data.meshes.remove(old)


def _self_union(o):
    """Fuse the overlapping loose parts of one mesh."""
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bm.verts.ensure_lookup_table()
    groups = _shells(bm)
    bm.free()
    if len(groups) < 2:
        return
    tmp = []
    for i, faces in enumerate(groups[1:]):
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        keep = set(faces)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep], context="FACES")
        me = bpy.data.meshes.new(f"{o.name}_part{i}")
        bm.to_mesh(me)
        bm.free()
        t = bpy.data.objects.new(me.name, me)
        t.matrix_world = o.matrix_world
        bpy.context.scene.collection.objects.link(t)
        tmp.append(t)
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bm.faces.ensure_lookup_table()
    keep = set(groups[0])
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep], context="FACES")
    bm.to_mesh(o.data)
    bm.free()
    boolean(o, tmp, "UNION")


# ---------------------------------------------------------------- modelling

def boolean(target, cutters, op="DIFFERENCE", solver="MANIFOLD", keep=False):
    """Apply one boolean of all cutters at once (op DIFFERENCE, UNION, INTERSECT) with the
    fast Manifold solver (falls back to Exact if Manifold can't), then delete the cutters
    unless keep. Cutters must be closed solids. Returns the target."""
    from .helpers import _objs as objs
    t = objs(target)[0]
    cs = [c for c in objs(cutters) if c != t]
    if not cs:
        return t
    coll = bpy.data.collections.new("_bool_tmp")
    bpy.context.scene.collection.children.link(coll)
    for c in cs:
        coll.objects.link(c)
    _apply_all(t)
    before = len(t.data.polygons)

    def open_mesh(o):
        if o.type != "MESH":
            return True
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bad = any(not e.is_manifold for e in bm.edges)
        bm.free()
        return bad
    if solver == "MANIFOLD" and any(open_mesh(o) for o in [t] + cs):
        # Manifold leaves the mesh unchanged (silently) when an input isn't closed.
        print(f"boolean: {', '.join(o.name for o in [t] + cs if open_mesh(o))} not closed, "
              "using the Exact solver")
        solver = "EXACT"
    for s in ([solver, "EXACT"] if solver != "EXACT" else ["EXACT"]):
        m = t.modifiers.new("bool", "BOOLEAN")
        m.operation, m.operand_type, m.collection = op, "COLLECTION", coll
        try:
            m.solver = s
        except TypeError:
            m.solver = "EXACT"
        dg = bpy.context.evaluated_depsgraph_get()
        ev = t.evaluated_get(dg)
        n = len(ev.data.polygons)
        if n or op == "INTERSECT" or not before:
            break
        t.modifiers.remove(m)  # Manifold returns nothing on a non-manifold input
    _apply_all(t)
    bpy.context.scene.collection.children.unlink(coll)
    bpy.data.collections.remove(coll)
    if not keep:
        for c in cs:
            data = c.data
            bpy.data.objects.remove(c)
            if data and data.users == 0 and isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
    return t


_FACES = {"+X": (0, 1), "-X": (0, -1), "+Y": (1, 1), "-Y": (1, -1), "+Z": (2, 1), "-Z": (2, -1)}


def label(target, text, face="+Z", size=None, depth=0.6, emboss=False, at=None, font=None,
          rotate=0.0, bold=0.06):
    """Engrave (or emboss) text into a face of the object's bounding box: face +X -X +Y -Y
    +Z -Z, centred or at a world point `at` on that face. size = letter height in scene
    units (default fits the face; keep >= 4 mm on an FDM print), depth in scene units.
    rotate turns the text on the face (degrees). bold thickens the strokes by that fraction
    of the letter height on each side (the default font is thin for a 0.4 mm nozzle; 0 = as
    drawn). font: path to a .ttf/.otf."""
    from .helpers import _objs as objs
    t = objs(target)[0]
    axis, sign = _FACES[face.upper()]
    pts = [t.matrix_world @ Vector(c) for c in t.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    cu = bpy.data.curves.new("label", "FONT")
    cu.body = text
    cu.align_x, cu.align_y = "CENTER", "CENTER"
    if font:
        cu.font = bpy.data.fonts.load(font, check_existing=True)
    cu.extrude = depth / 2 + 0.05 * depth  # extrudes both ways from the text's plane
    tob = bpy.data.objects.new("label", cu)
    bpy.context.scene.collection.objects.link(tob)
    other = [i for i in range(3) if i != axis]
    span = [hi[i] - lo[i] for i in other]
    bpy.context.view_layer.update()
    w = max(tob.dimensions.x, 1e-6) / max(cu.size, 1e-6)
    h = 0.7  # cap height of the default font, per unit size
    cu.size = size or min(span[1] * 0.5 / h, span[0] * 0.8 / w)
    grow = bold * cu.size * h  # stroke growth per side
    # Orient: text's +Z out of the face; text's X along the face's first in-plane axis.
    n = Vector((0, 0, 0))
    n[axis] = sign
    up = Vector((0, 0, 1)) if axis != 2 else Vector((0, 1, 0))
    x = up.cross(n).normalized() if axis != 2 else Vector((sign, 0, 0))
    y = n.cross(x)
    rot = Matrix((x, y, n)).transposed().to_4x4() @ Matrix.Rotation(math.radians(rotate), 4, "Z")
    c = (lo + hi) / 2 if at is None else Vector(at)
    c[axis] = hi[axis] if sign > 0 else lo[axis]
    # Engraved: from `depth` inside the face to just past it; embossed: from just inside to
    # `depth` out, so the boolean never meets a coplanar face.
    off = (depth / 2 - 0.05 * depth) * (1 if emboss else -1)
    tob.matrix_world = Matrix.Translation(c + n * off) @ rot
    bpy.context.view_layer.update()
    me = bpy.data.meshes.new_from_object(tob.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    mob = bpy.data.objects.new("label_mesh", me)
    mob.matrix_world = tob.matrix_world
    bpy.context.scene.collection.objects.link(mob)
    bpy.data.objects.remove(tob)
    bpy.data.curves.remove(cu)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me)
    bm.free()
    letters = mob.dimensions.copy()
    before = len(t.data.polygons)
    cutters = [mob]
    if grow > 0:
        # Thicker strokes that stay watertight: the text shifted around a small circle in the
        # face plane (a curve offset folds over inside letters and breaks the boolean).
        for i in range(8):
            a_ = i * math.pi / 4
            cp = mob.copy()
            bpy.context.scene.collection.objects.link(cp)
            cp.matrix_world = Matrix.Translation(
                (x * math.cos(a_) + y * math.sin(a_)) * grow) @ mob.matrix_world
            cutters.append(cp)
    if not emboss:
        # Where the text is cut, in the part's own space: the ridges between engraved letters
        # are shallow and print fine, so the thin-wall check skips them.
        inv = t.matrix_world.inverted()
        pts = [inv @ (c_.matrix_world @ Vector(c)) for c_ in cutters for c in c_.bound_box]
        box = [min(p[i] for p in pts) for i in range(3)] + [max(p[i] for p in pts) for i in range(3)]
        t["print_labels"] = list(t.get("print_labels", [])) + box
    boolean(t, cutters, "UNION" if emboss else "DIFFERENCE")
    k = 1.0 / _mm()
    print(f"label {text!r} {'embossed on' if emboss else 'engraved in'} {t.name} {face}: "
          f"{letters.x / k:.1f} x {letters.y / k:.1f} mm, {depth / k:.2f} mm "
          f"{'high' if emboss else 'deep'}, faces {before} -> {len(t.data.polygons)}")
    return t


def _section(o, axis, at):
    """2D edges of the object's cross-section at world coordinate `at` on axis."""
    bm = _world_bm(o)
    s = 1.0 / _mm()
    bm.transform(Matrix.Scale(s, 4))
    co = Vector((0, 0, 0))
    co[axis] = at
    no = Vector((0, 0, 0))
    no[axis] = 1
    res = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
                                 plane_co=co, plane_no=no, dist=1e-6)
    other = [i for i in range(3) if i != axis]
    edges = [((e.verts[0].co[other[0]], e.verts[0].co[other[1]]),
              (e.verts[1].co[other[0]], e.verts[1].co[other[1]]))
             for e in res["geom_cut"] if isinstance(e, bmesh.types.BMEdge)]
    bm.free()
    return edges, other


def _inside(p, edges):
    x, y = p
    c = False
    for (x1, y1), (x2, y2) in edges:
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _edge_dist(p, edges):
    best = 1e18
    px, py = p
    for (x1, y1), (x2, y2) in edges:
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / L))
        best = min(best, math.hypot(px - x1 - t * dx, py - y1 - t * dy))
    return best


def split(target, axis="Z", at=None, pins=2, pin_d=None, pin_depth=None, clearance=None):
    """Cut an object in two at a plane (axis X/Y/Z, world coordinate `at`, default the
    middle) and drill matching holes for alignment pins in both cut faces (pins=0 for none).
    Pin size defaults suit 1.75 mm filament offcuts (holes 1.75 + clearance). Returns
    (lower, upper) objects and prints the pins to cut."""
    from .helpers import _objs as objs
    o = objs(target)[0]
    _apply_all(o)
    a = "XYZ".index(axis.upper())
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    lo = min(p[a] for p in pts)
    hi = max(p[a] for p in pts)
    at = (lo + hi) / 2 if at is None else at
    k = 1.0 / _mm()  # scene units per mm
    clearance = (clearance if clearance is not None else 0.15) * k
    pin_d = (pin_d if pin_d is not None else 1.75) * k
    pin_depth = (pin_depth if pin_depth is not None else 6.0) * k
    edges, other = _section(o, a, at)
    holes = []
    if pins and edges:
        xs = [p[0] for e in edges for p in e]
        ys = [p[1] for e in edges for p in e]
        step = max((max(xs) - min(xs)), (max(ys) - min(ys))) / 30
        cand = []
        gx = min(xs)
        while gx <= max(xs):
            gy = min(ys)
            while gy <= max(ys):
                if _inside((gx, gy), edges):
                    d = _edge_dist((gx, gy), edges)
                    if d > pin_d * 1.5:
                        cand.append(((gx, gy), d))
                gy += step
            gx += step
        for _ in range(pins):
            if not cand:
                break
            if not holes:
                pick = max(cand, key=lambda c: c[1])
            else:
                pick = max(cand, key=lambda c: min(math.dist(c[0], h) for h in holes))
            holes.append(pick[0])
    size = max(o.dimensions) * 4 + 10
    halves = []
    for side in (-1, 1):
        cp = o.copy()
        cp.data = o.data.copy()
        bpy.context.scene.collection.objects.link(cp)
        box = _box(size, a, at + side * size / 2)
        boolean(cp, [box], "INTERSECT")
        cp.name = f"{o.name}_{'A' if side < 0 else 'B'}"
        cutters = []
        for h in holes:
            loc = Vector((0, 0, 0))
            loc[a] = at
            loc[other[0]], loc[other[1]] = h
            cutters.append(_cyl((pin_d + clearance) / 2, pin_depth * 2 + clearance * 2, a, loc))
        if cutters:
            boolean(cp, cutters, "DIFFERENCE")
        halves.append(cp)
    me = o.data
    bpy.data.objects.remove(o)
    if me.users == 0:
        bpy.data.meshes.remove(me)
    if holes:
        print(f"{len(holes)} pin(s): {pin_d / k:.2f} mm x {pin_depth * 2 / k - 0.5:.1f} mm "
              f"(holes {(pin_d + clearance) / k:.2f} mm, {pin_depth / k:.1f} deep each side)")
    return tuple(halves)


def _box(size, axis, center):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)
    me = bpy.data.meshes.new("cut_box")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("cut_box", me)
    loc = Vector((0, 0, 0))
    loc[axis] = center
    ob.location = loc
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _cyl(r, length, axis, loc, segs=32):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r, radius2=r, depth=length)
    me = bpy.data.meshes.new("pin_hole")
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new("pin_hole", me)
    ob.location = loc
    ob.rotation_euler = [(0, math.pi / 2, 0), (math.pi / 2, 0, 0), (0, 0, 0)][axis]
    bpy.context.scene.collection.objects.link(ob)
    return ob


# ---------------------------------------------------------------- layout and export

def _pack(sizes, gap):
    """Shelf-pack (w, d) footprints in mm onto beds: [(plate, x, y)] in the same order."""
    _, bx, by, _, _ = printer()
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    out = [None] * len(sizes)
    plate, x, y, row_h = 0, 0.0, 0.0, 0.0
    for i in order:
        w, d = sizes[i]
        if x + w > bx and x > 0:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        if y + d > by and y > 0:
            plate, x, y, row_h = plate + 1, 0.0, 0.0, 0.0
        out[i] = (plate, x, y)
        x += w + gap
        row_h = max(row_h, d)
    return out


def arrange(objects=None, gap=6.0, restore=False):
    """Preview the print layout in the scene: parts move onto the bed (as oriented now), packed
    with gap mm between; extra plates sit beside the first. restore=True puts every part back
    where it was. h.print_export packs on its own, so this is only for looking. Returns the
    plate count."""
    _, bx, _, _, _ = printer()
    k = 1.0 / _mm()
    objs = _objs(objects)
    if restore:
        for o in objs:
            if "print_home" in o:
                o.matrix_world = Matrix([o["print_home"][r * 4:r * 4 + 4] for r in range(4)])
                del o["print_home"]
        return 0
    bpy.context.view_layer.update()
    boxes = []
    for o in objs:
        pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
        boxes.append((Vector([min(p[i] for p in pts) for i in range(3)]),
                      Vector([max(p[i] for p in pts) for i in range(3)])))
    spots = _pack([((hi.x - lo.x) / k, (hi.y - lo.y) / k) for lo, hi in boxes], gap)
    for o, (lo, hi), (plate, x, y) in zip(objs, boxes, spots):
        if "print_home" not in o:
            o["print_home"] = [v for row in o.matrix_world for v in row]
        o.location += Vector(((plate * (bx + 40) + x) * k - lo.x, y * k - lo.y, -lo.z))
    bpy.context.view_layer.update()
    return max(s[0] for s in spots) + 1 if spots else 0


def _tris(o, dg):
    """World-space triangles in mm: (verts Nx3, tris Mx3)."""
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    me.calc_loop_triangles()
    v = np.empty(len(me.vertices) * 3, np.float64)
    me.vertices.foreach_get("co", v)
    t = np.empty(len(me.loop_triangles) * 3, np.int64)
    me.loop_triangles.foreach_get("vertices", t)
    ev.to_mesh_clear()
    v = v.reshape(-1, 3)
    m = np.array(Matrix.Scale(_mm(), 4) @ o.matrix_world)
    v = v @ m[:3, :3].T + m[:3, 3]
    t = t.reshape(-1, 3)
    if np.linalg.det(m[:3, :3]) < 0:
        t = t[:, ::-1]
    return v, t


def _write_3mf(path, items):
    """items: [(name, verts, tris)]. A plain 3MF core model in millimetres."""
    objs, build = [], []
    for i, (name, v, t) in enumerate(items, 1):
        vs = "".join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{c:.4f}"/>' for a, b, c in v)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in t)
        nm = name.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
        objs.append(f'<object id="{i}" name="{nm}" type="model"><mesh><vertices>{vs}</vertices>'
                    f'<triangles>{ts}</triangles></mesh></object>')
        build.append(f'<item objectid="{i}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Application">LLM Chat for Blender</metadata>'
             f'<resources>{"".join(objs)}</resources><build>{"".join(build)}</build></model>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
                   'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.'
                   '3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/'
                   '3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/'
                   '2013/01/3dmodel"/></Relationships>')
        z.writestr("3D/3dmodel.model", model)


def _write_stl(path, v, t):
    tri = v[t].astype(np.float32)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"LLM Chat for Blender".ljust(80, b" "))
        f.write(np.uint32(len(t)).tobytes())
        f.write(rec.tobytes())


def _out_dir():
    p = _prefs()
    if p and getattr(p, "print_dir", ""):
        return bpy.path.abspath(p.print_dir)
    return os.path.join(os.path.expanduser("~"), "Desktop", "LLM Chat prints")


def export(objects=None, name=None, fmt="3mf", folder=None, force=False, rotate=None,
           gap=6.0):
    """Export print files in mm without moving anything in the scene. Each part is laid on the
    bed as oriented in the scene, or turned first by rotate={part name: (x, y, z) degrees}
    (print orientation apart from the assembly). 3mf (default): parts packed onto plates, one
    file per plate, each part a named object; opens in Creality Print / OrcaSlicer / Bambu
    Studio / PrusaSlicer. stl: one file per part. Refuses parts that aren't watertight unless
    force. Returns the paths."""
    objs = _objs(objects)
    if not objs:
        raise ValueError("nothing to export")
    _, probs = check(objs)
    bad = [p for p in probs if "non-manifold" in p or "inside-out" in p]
    if bad and not force:
        raise ValueError("not printable yet (force=True exports anyway):\n" + "\n".join(bad))
    folder = folder or _out_dir()
    os.makedirs(folder, exist_ok=True)
    name = name or os.path.splitext(os.path.basename(bpy.data.filepath))[0] or objs[0].name
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    parts = []
    for o in objs:
        v, t = _tris(o, dg)
        r = (rotate or {}).get(o.name)
        if r:
            from mathutils import Euler
            m = np.array(Euler([math.radians(a) for a in r]).to_matrix())
            v = v @ m.T
        v = v - v.min(0)  # corner of its box at the origin, resting on z = 0
        parts.append((o.name, v, t))
    paths = []
    if fmt.lower() == "stl":
        for nm, v, t in parts:
            p = os.path.join(folder, f"{bpy.path.clean_name(nm)}.stl")
            _write_stl(p, v, t)
            paths.append(p)
        return paths
    _, bx, by, _, _ = printer()
    spots = _pack([(v[:, 0].max(), v[:, 1].max()) for _, v, _ in parts], gap)
    plates = {}
    for (nm, v, t), (plate, x, y) in zip(parts, spots):
        plates.setdefault(plate, []).append((nm, v + np.array([x, y, 0.0]), t))
    for n, items in sorted(plates.items()):
        allv = np.vstack([i[1] for i in items])
        lo, hi = allv.min(0), allv.max(0)
        shift = np.array([bx / 2 - (lo[0] + hi[0]) / 2, by / 2 - (lo[1] + hi[1]) / 2, 0.0])
        suffix = f"_plate{n + 1}" if len(plates) > 1 else ""
        p = os.path.join(folder, f"{bpy.path.clean_name(name)}{suffix}.3mf")
        _write_3mf(p, [(nm, v + shift, t) for nm, v, t in items])
        paths.append(p)
    return paths


# ---------------------------------------------------------------- slicing

SLICERS = ("orca-slicer", "OrcaSlicer", "orcaslicer", "CrealityPrint", "creality-print")


def _prefs():
    try:
        return next(a.preferences for k, a in bpy.context.preferences.addons.items()
                    if k.endswith("claude_chat"))
    except StopIteration:
        return None


def find_slicer():
    """Path of OrcaSlicer (or Creality Print, built on it): the add-on preference, else
    PATH and the usual install folders (AppImages in ~/Applications, Downloads, Desktop)."""
    p = _prefs()
    if p and p.slicer_path and os.path.isfile(bpy.path.abspath(p.slicer_path)):
        return bpy.path.abspath(p.slicer_path)
    for s in SLICERS:
        found = shutil.which(s)
        if found:
            return found
    home = os.path.expanduser("~")
    cands = [r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
             r"C:\Program Files\Creality\Creality Print 6\CrealityPrint.exe",
             "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
             "/Applications/CrealityPrint.app/Contents/MacOS/CrealityPrint"]
    for d in (os.path.join(home, "Applications"), os.path.join(home, "Downloads"),
              os.path.join(home, "Desktop"), "/opt"):
        try:
            cands += [os.path.join(d, f) for f in sorted(os.listdir(d), reverse=True)
                      if f.lower().startswith(("orcaslicer", "orca-slicer", "crealityprint",
                                               "creality_print")) and
                      f.lower().endswith(".appimage")]
        except OSError:
            pass
    return next((c for c in cands if os.path.isfile(c)), None)


def _profile_roots(slicer, vendor):
    """Folders holding the slicer's system profiles (vendor + the shared filament library)."""
    d = os.path.dirname(os.path.realpath(slicer))
    roots = [os.path.join(d, "resources", "profiles"), os.path.join(d, "..", "resources", "profiles"),
             os.path.join(d, "..", "Resources", "profiles")]
    if slicer.lower().endswith(".appimage"):
        # The profiles live inside the AppImage: extract just those folders, once per version.
        cache = os.path.join(bpy.utils.user_resource("CONFIG", path="claude_chat", create=True),
                             "slicer_profiles", f"{os.path.basename(slicer)}-{os.path.getsize(slicer)}")
        out = os.path.join(cache, "squashfs-root", "resources", "profiles")
        if not os.path.isdir(os.path.join(out, vendor)):
            os.makedirs(cache, exist_ok=True)
            for pat in (f"resources/profiles/{vendor}*", "resources/profiles/OrcaFilamentLibrary*"):
                subprocess.run([slicer, "--appimage-extract", pat], cwd=cache, capture_output=True,
                               timeout=300)
        roots.insert(0, out)
    home = os.path.expanduser("~")
    for app in ("OrcaSlicer", "CrealityPrint", "Creality Print"):
        roots += [os.path.join(home, ".config", app, "system"),
                  os.path.join(os.environ.get("APPDATA", home), app, "system"),
                  os.path.join(home, "Library", "Application Support", app, "system")]
    return [r for r in roots if os.path.isdir(r)]


def _profiles(roots, kind):
    """{name or file stem: path} of every profile of a kind (machine, process, filament)."""
    import glob
    import json
    idx = {}
    for r in roots:
        for p in glob.glob(os.path.join(r, "*", kind, "**", "*.json"), recursive=True):
            idx.setdefault(os.path.basename(p)[:-5], p)
            try:
                with open(p, encoding="utf-8") as f:
                    idx.setdefault(json.load(f).get("name"), p)
            except (OSError, ValueError):
                pass
    return idx


def _resolve(idx, name):
    """A profile with everything it inherits merged in (the CLI wants it flat)."""
    import json
    chain, n = [], name
    while n:
        with open(idx[n], encoding="utf-8") as f:
            d = json.load(f)
        chain.append(d)
        n = d.get("inherits")
    out = {}
    for d in reversed(chain):
        out.update(d)
    out.pop("inherits", None)
    out["name"] = name
    return out


def slice(paths, quality="0.20mm Standard", filament="Generic PLA", settings=None):
    """Slice 3MF/STL files into G-code for the chosen printer with OrcaSlicer's own
    profiles for it (quality: the start of a process name, e.g. 0.12mm Fine, 0.16mm Optimal,
    0.20mm Standard, 0.24mm Draft; filament: part of a filament name, e.g. Generic PLA,
    Generic PETG, Hyper PLA). settings: OrcaSlicer keys changed on top of the profiles, e.g.
    {"filament_max_volumetric_speed": 8, "nozzle_temperature": 230}. Writes <name>.gcode next
    to each file and returns a summary with print time and filament per plate."""
    import json
    import tempfile
    slicer = find_slicer()
    pname, *_ = printer()
    if not slicer:
        return ("No slicer found: install OrcaSlicer (free, has the Creality K1C profiles) or "
                "set its path in the add-on preferences. The .3mf files open in Creality "
                "Print / OrcaSlicer as they are.")
    if pname not in PRINTERS:
        return "Custom printer: open the .3mf in your slicer (no profile to slice with)."
    vendor, machine = PRINTERS[pname][4:]
    roots = _profile_roots(slicer, vendor)
    machines, processes, filaments = (_profiles(roots, k) for k in ("machine", "process", "filament"))
    if machine not in machines:
        return f"The slicer has no {machine!r} profile."
    short = machine.replace(" (", " ").replace(")", "")
    proc = next((n for n in sorted(processes) if n and n.startswith(quality) and
                 (machine in n or short in n) and "CFS" not in n), None)
    fil = None
    for n in sorted(filaments, key=lambda n: (len(n or ""), n or "")):
        if not n or filament.lower() not in n.lower():
            continue
        try:
            comp = _resolve(filaments, n).get("compatible_printers") or []
        except (KeyError, OSError, ValueError):
            continue
        if machine in comp:
            fil = n
            break
    if not proc or not fil:
        return (f"No {'process ' + quality if not proc else 'filament ' + filament} profile for "
                f"{machine}. Processes: " + ", ".join(sorted({n.split(' @')[0] for n in processes
                                                               if n and machine in n})))
    tmp = tempfile.mkdtemp(prefix="llmchat_slice_")
    files = []
    for k, (kind, idx, n) in enumerate((("machine", machines, machine),
                                       ("process", processes, proc),
                                       ("filament", filaments, fil))):
        f = os.path.join(tmp, f"{kind}.json")
        prof = _resolve(idx, n)
        for key, val in (settings or {}).items():
            # A key goes to the profile that has it (filament if none); per-extruder values
            # are lists of strings.
            if key in prof or (kind == "filament" and not any(
                    key in _resolve(i, m) for i, m in ((machines, machine), (processes, proc)))):
                old = prof.get(key)
                prof[key] = [str(val)] * max(1, len(old)) if isinstance(old, list) else str(val)
        with open(f, "w", encoding="utf-8") as fh:
            json.dump(prof, fh)
        files.append(f)
    out = []
    for path in ([paths] if isinstance(paths, str) else paths):
        od = os.path.join(tmp, os.path.basename(path))
        os.makedirs(od, exist_ok=True)
        r = subprocess.run([slicer, "--slice", "0", "--load-settings", f"{files[0]};{files[1]}",
                            "--load-filaments", files[2], "--outputdir", od, path],
                           capture_output=True, text=True, timeout=1800)
        gcodes = sorted(f for f in os.listdir(od) if f.endswith(".gcode"))
        if not gcodes:
            out.append(f"{os.path.basename(path)}: slicing failed ({r.returncode}): "
                       f"{(r.stderr or r.stdout)[-400:]}")
            continue
        base = os.path.splitext(path)[0]
        for g in gcodes:
            dst = base + (f"_{g[:-6]}" if len(gcodes) > 1 else "") + ".gcode"
            shutil.move(os.path.join(od, g), dst)
            info = {}
            with open(dst, "rb") as fh:
                fh.seek(max(0, os.path.getsize(dst) - 60000))
                for line in fh.read().decode("utf-8", "replace").splitlines():
                    for key in ("estimated printing time (normal mode)", "total filament used [g]",
                                "filament used [mm]"):
                        if line.startswith(f"; {key} ="):
                            info[key] = line.split("=", 1)[1].strip()
            out.append(f"{dst}: {info.get('estimated printing time (normal mode)', '?')}, "
                       f"{info.get('total filament used [g]', '?')} g "
                       f"({float(info.get('filament used [mm]', 0) or 0) / 1000:.1f} m)")
    shutil.rmtree(tmp, ignore_errors=True)
    return f"{machine}, {proc}, {fil}:\n" + "\n".join(out)
