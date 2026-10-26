"""Helpers for Claude's execute_blender_code scripts.

    from claude_chat import helpers as h

They replace boilerplate Claude would otherwise rewrite (and pay output tokens for)
on every run. Every function takes object names, objects, or a collection name.
"""

import math
import os

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector
from mathutils.bvhtree import BVHTree


def _objs(items=None):
    """Names/objects/collection name -> list of objects (default: selection)."""
    if items is None:
        return list(bpy.context.selected_objects)
    if isinstance(items, (str, bpy.types.Object)):
        items = [items]
    out = []
    for it in items:
        if isinstance(it, bpy.types.Object):
            out.append(it)
        elif it in bpy.data.objects:
            out.append(bpy.data.objects[it])
        elif it in bpy.data.collections:
            out += list(bpy.data.collections[it].all_objects)
        else:
            raise KeyError(f"no object or collection named {it!r}")
    return out


def _obj(item):
    return _objs(item)[0]


def _view3d():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                return win, area, next(r for r in area.regions if r.type == "WINDOW")
    raise RuntimeError("no 3D viewport open")


def _select(objs, active=None):
    for o in bpy.context.view_layer.objects:
        if o:  # a just-deleted object can linger as None until the view layer updates
            o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


# ---------------------------------------------------------------- viewing

VIEWS = {"ISO": (65, 0, 35), "ISO_BACK": (65, 0, 215)}


def frame(objects=None, view="FRONT", shading="AUTO"):
    """Aim the viewport at objects before get_viewport_screenshot.
    view: FRONT BACK LEFT RIGHT TOP BOTTOM ISO ISO_BACK. FRONT looks along +Y
    (sees the front of a model facing -Y). shading: SOLID MATERIAL RENDERED, None (as is) or
    AUTO (solid with cavity in the 3D Printing mode, else MATERIAL)."""
    win, area, region = _view3d()
    objs = _objs(objects) if objects is not None else [
        o for o in bpy.context.visible_objects if o.type in {"MESH", "ARMATURE"}]
    prev_sel, prev_act = list(bpy.context.selected_objects), bpy.context.view_layer.objects.active
    _select(objs)
    r3d = area.spaces.active.region_3d
    with bpy.context.temp_override(window=win, area=area, region=region):
        if view in VIEWS:
            r3d.view_perspective = "PERSP"
            r3d.view_rotation = Euler([math.radians(a) for a in VIEWS[view]]).to_quaternion()
        else:
            bpy.ops.view3d.view_axis(type=view)
        bpy.ops.view3d.view_selected()
    _select(prev_sel, prev_act)
    if shading == "AUTO":
        from . import bridge
        if bridge.MODE == "print":
            from .printing import solid_view
            solid_view(area.spaces.active)
            shading = None
        else:
            shading = "MATERIAL"
    if shading:
        area.spaces.active.shading.type = shading


# ---------------------------------------------------------------- inspection

def _unweighted(o):
    return sum(1 for v in o.data.vertices if not any(g.weight > 0 for g in v.groups))


def report(objects=None):
    """One line per object: tris (after modifiers), size, floor/top height,
    materials, modifiers, and mesh problems. Returns the text; print it."""
    dg = bpy.context.evaluated_depsgraph_get()
    lines = []
    for o in _objs(objects):
        if o.type == "MESH":
            ev = o.evaluated_get(dg)
            me = ev.to_mesh()
            tris = sum(len(p.vertices) - 2 for p in me.polygons)
            zs = [(o.matrix_world @ v.co).z for v in me.vertices] or [0]
            ev.to_mesh_clear()
            bm = bmesh.new()
            bm.from_mesh(o.data)
            loose = sum(1 for v in bm.verts if not v.link_faces)
            open_edges = sum(1 for e in bm.edges if not e.is_manifold)
            ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
            bm.free()
            d = o.dimensions
            info = (f"{o.name}: {tris} tris, {d.x:.2f}x{d.y:.2f}x{d.z:.2f} m, "
                    f"z {min(zs):.2f}..{max(zs):.2f}, "
                    f"mats {[s.material.name for s in o.material_slots if s.material]}, "
                    f"mods {[m.type for m in o.modifiers]}, loose {loose}, "
                    f"open edges {open_edges}, ngons {ngons}")
            if any(m.type == "ARMATURE" for m in o.modifiers):
                info += f", unweighted verts {_unweighted(o)}"
            lines.append(info)
        elif o.type == "ARMATURE":
            bones = o.data.bones
            act = o.animation_data.action if o.animation_data else None
            lines.append(f"{o.name}: armature, {len(bones)} bones "
                         f"({sum(b.use_deform for b in bones)} deform), roots "
                         f"{[b.name for b in bones if not b.parent]}, action {act.name if act else None}, "
                         f"{len(bpy.data.actions)} actions in file, children "
                         f"{[c.name for c in o.children]}")
        else:
            lines.append(f"{o.name}: {o.type.lower()}")
    return "\n".join(lines)


def floating(objects=None, tol=0.005):
    """Parts that don't touch the rest. Splits the meshes (default: all visible)
    into loose parts, links parts that intersect or come within tol metres, and
    reports every group not linked to the largest one, with its gap. A group of
    parts touching only each other (horns on a plate off the wall) is floating
    too. Returns the text; print it."""
    objs = _objs(objects) if objects is not None else [
        o for o in bpy.context.visible_objects if o.type == "MESH"]
    dg = bpy.context.evaluated_depsgraph_get()
    parts = []  # (label, verts, bvh, bbox min, bbox max)
    for o in objs:
        if o.type != "MESH":
            continue
        ev = o.evaluated_get(dg)
        bm = bmesh.new()
        bm.from_mesh(ev.to_mesh())
        ev.to_mesh_clear()
        bm.transform(o.matrix_world)
        bm.verts.ensure_lookup_table()
        seen = set()
        for v in bm.verts:
            if v.index in seen:
                continue
            stack, comp = [v], []
            seen.add(v.index)
            while stack:
                a = stack.pop()
                comp.append(a)
                for e in a.link_edges:
                    b = e.other_vert(a)
                    if b.index not in seen:
                        seen.add(b.index)
                        stack.append(b)
            idx = {a.index: i for i, a in enumerate(comp)}
            faces = {f for a in comp for f in a.link_faces}
            cos = [a.co.copy() for a in comp]
            polys = [[idx[x.index] for x in f.verts] for f in faces]
            if not polys:
                continue
            mn = Vector([min(c[k] for c in cos) for k in range(3)])
            mx = Vector([max(c[k] for c in cos) for k in range(3)])
            parts.append((f"{o.name}[{len(parts)}]", cos, BVHTree.FromPolygons(cos, polys), mn, mx))
        bm.free()
    if not parts:
        return "no mesh parts"

    def gap(a, b):
        if a[3].x > b[4].x + tol or b[3].x > a[4].x + tol or a[3].y > b[4].y + tol \
                or b[3].y > a[4].y + tol or a[3].z > b[4].z + tol or b[3].z > a[4].z + tol:
            return None
        if a[2].overlap(b[2]):
            return 0.0
        d = min(min((b[2].find_nearest(c)[3] for c in a[1]), default=1e9),
                min((a[2].find_nearest(c)[3] for c in b[1]), default=1e9))
        return d if d <= tol else None

    n = len(parts)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if root(i) != root(j) and gap(parts[i], parts[j]) is not None:
                parent[root(i)] = root(j)
    groups = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)
    # The main group is the bulkiest (bounding-box volume), not the most detailed: a
    # finely rounded wheel shouldn't count as "the model" with the body floating off it.
    vol = [max(1e-9, (p[4] - p[3]).x) * max(1e-9, (p[4] - p[3]).y) * max(1e-9, (p[4] - p[3]).z)
           for p in parts]
    main = max(groups.values(), key=lambda g: sum(vol[i] for i in g))
    lines = []
    for g in groups.values():
        if g is main:
            continue
        # distance from this group to the main group, and the closest point on it
        best = (1e9, None)
        for i in g:
            for c in parts[i][1]:
                for k in main:
                    hit = parts[k][2].find_nearest(c)
                    if hit[0] is not None and hit[3] < best[0]:
                        best = (hit[3], hit[0])
        mn = Vector([min(parts[i][3][k] for i in g) for k in range(3)])
        mx = Vector([max(parts[i][4][k] for i in g) for k in range(3)])
        lines.append(f"FLOATING {', '.join(parts[i][0] for i in g)}: gap {best[0]:.3f} m, "
                     f"bbox {tuple(round(x, 2) for x in mn)}..{tuple(round(x, 2) for x in mx)}, "
                     f"nearest main surface at {tuple(round(x, 2) for x in best[1]) if best[1] else None}")
    return "\n".join(lines) or f"nothing floating ({n} parts checked)"


def _world_tree(o, dg):
    """(world-space vertex positions, BVH) of an object as evaluated (modifiers on)."""
    ev = o.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        cos = [o.matrix_world @ v.co for v in me.vertices]
        tree = BVHTree.FromPolygons(cos, [tuple(p.vertices) for p in me.polygons])
    finally:
        ev.to_mesh_clear()
    return cos, tree


def snap(objects, to, max_gap=0.3):
    """Move `objects` (together, as one rigid group) the shortest way until they touch
    `to`, overlapping by 2 mm. Use it on parts meant to touch that h.floating reports;
    leave intentional gaps alone. Gaps over max_gap metres are not moved, only reported
    with the move that would close them. Returns the text; print it."""
    objs = [o for o in _objs(objects) if o.type == "MESH"]
    targets = [o for o in _objs(to) if o.type == "MESH" and o not in objs]
    if not objs or not targets:
        return "snap: need mesh objects to move and mesh objects to touch"
    # Moving a parent already moves its children: move only the top ones.
    def ancestors(o):
        while o.parent:
            o = o.parent
            yield o
    tops = [o for o in objs if not any(p in objs for p in ancestors(o))]
    total = Vector()
    for _ in range(4):  # nearest points are sampled, so close in on contact in steps
        dg = bpy.context.evaluated_depsgraph_get()
        src = [_world_tree(o, dg) for o in objs]
        dst = [_world_tree(o, dg) for o in targets]
        if any(a[1].overlap(b[1]) for a in src for b in dst):
            break
        best = (float("inf"), None, None)  # (gap, point on objects, point on target)
        for (sc, st) in src:
            for (dc, dt) in dst:
                for c in sc[:: max(1, len(sc) // 3000)]:
                    hit = dt.find_nearest(c)
                    if hit[0] is not None and hit[3] < best[0]:
                        best = (hit[3], c, hit[0])
                for c in dc[:: max(1, len(dc) // 3000)]:
                    hit = st.find_nearest(c)
                    if hit[0] is not None and hit[3] < best[0]:
                        best = (hit[3], hit[0], c)
        gap, pa, pb = best
        if pa is None:
            return "snap: no surfaces to measure"
        if gap < 1e-4:
            break
        move = pb - pa
        move += move.normalized() * min(0.002, gap * 0.5)
        if total.length + move.length > max_gap:
            return (f"not moved: gap {gap:.3f} m is over max_gap {max_gap} m; moving by "
                    f"{move.x:.3f} {move.y:.3f} {move.z:.3f} would close it")
        for o in tops:
            o.matrix_world = Matrix.Translation(move) @ o.matrix_world
        total += move
    if total.length == 0:
        return "already touching, nothing moved"
    names = ", ".join(o.name for o in tops)
    return f"moved {names} by {total.x:.3f} {total.y:.3f} {total.z:.3f} to touch {targets[0].name}"


def peek(path):
    """List what a .blend contains without opening or appending it."""
    with bpy.data.libraries.load(os.path.expanduser(bpy.path.abspath(path))) as (src, _):
        return "\n".join(f"{k}: {getattr(src, k)}" for k in
                         ("collections", "objects", "materials", "images", "actions"))


def append(path, objects=None, collection="_Reference-noimp", offset=(0, 0, 0)):
    """Append objects (default all) from a .blend into a collection and move
    their top-level objects by offset. Returns the new objects."""
    with bpy.data.libraries.load(os.path.expanduser(bpy.path.abspath(path))) as (src, dst):
        dst.objects = [n for n in src.objects if objects is None or n in objects]
    col = bpy.data.collections.get(collection) or bpy.data.collections.new(collection)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    new = [o for o in dst.objects if o]
    for o in new:
        col.objects.link(o)
        if not o.parent:
            o.location += Vector(offset)
    return new


# ---------------------------------------------------------------- cleanup

def clean(objects=None, merge=0.0001):
    """Merge doubles, delete loose geometry, fix normals, triangulate n-gons."""
    out = []
    for o in _objs(objects):
        if o.type != "MESH":
            continue
        bm = bmesh.new()
        bm.from_mesh(o.data)
        n = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge)
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4],
                              quad_method="BEAUTY", ngon_method="BEAUTY")
        out.append(f"{o.name}: -{n - len(bm.verts)} verts")
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()
    return ", ".join(out)


def remove_hidden(objects=None):
    """Delete faces enclosed by other parts of the same mesh (kitbashed parts
    poking into each other). A face goes only if the space just outside it is
    inside the mesh's volume at its centre and near every corner, so partly
    buried faces are kept. Parts should be closed shells."""
    out = []
    for o in _objs(objects):
        if o.type != "MESH":
            continue
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        bvh = BVHTree.FromBMesh(bm)

        def inside(p, d):
            # Winding number along a ray: exits minus entries > 0 means inside.
            w, o = 0, p
            for _ in range(64):
                loc, hn, _, _ = bvh.ray_cast(o, d)
                if loc is None:
                    break
                w += 1 if hn.dot(d) > 0 else -1
                o = loc + d * 1e-5
            return w > 0

        # Majority of 3 skewed rays per point: one grazing an edge can't decide it.
        dirs = [Vector(d).normalized() for d in
                ((0.31, 0.17, 0.93), (-0.73, 0.61, -0.29), (0.23, -0.89, 0.41))]
        hidden = []
        for f in bm.faces:
            c, n = f.calc_center_median(), f.normal
            if n.length < 0.5:
                continue
            pts = [c] + [v.co.lerp(c, 0.1) for v in f.verts]
            if all(sum(inside(p + n * 1e-3, d) for d in dirs) >= 2 for p in pts):
                hidden.append(f)
        bmesh.ops.delete(bm, geom=hidden, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
        bm.to_mesh(o.data)
        bm.free()
        o.data.update()
        out.append(f"{o.name}: -{len(hidden)} hidden faces")
    return ", ".join(out)


def join(objects, name):
    """Join meshes into one object called name (keeps the first one's origin)."""
    objs = [o for o in _objs(objects) if o.type == "MESH"]
    _select(objs)
    with bpy.context.temp_override(active_object=objs[0], selected_editable_objects=objs):
        bpy.ops.object.join()
    objs[0].name = objs[0].data.name = name
    return objs[0]


# ---------------------------------------------------------------- materials

def _to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _inp(bsdf, *ids):
    return next((i for i in bsdf.inputs if i.identifier in ids), None)


def palette(objects, name):
    """Turn every material slot on the mesh(es) into one cell of a shared palette
    texture and replace them with a single material M_<name> (base colour,
    roughness/metallic, emission). Model with plain per-part materials, then call
    this once at the end. Overwrites the meshes' UVs."""
    objs = [o for o in _objs(objects) if o.type == "MESH"]
    mats = []
    for o in objs:
        for s in o.material_slots:
            if s.material and s.material not in mats:
                mats.append(s.material)
    cells = []
    for m in mats:
        b = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) if m.use_nodes else None
        if b:
            base = list(_inp(b, "Base Color").default_value)[:3]
            rough = _inp(b, "Roughness").default_value
            metal = _inp(b, "Metallic").default_value
            emi = _inp(b, "Emission Color", "Emission")
            strength = _inp(b, "Emission Strength")
            emit = list(emi.default_value)[:3] if emi else [0, 0, 0]
            s = strength.default_value if strength else 1.0
        else:
            base, rough, metal, emit, s = list(m.diffuse_color)[:3], m.roughness, m.metallic, [0, 0, 0], 0
        cells.append((base, rough, metal, [c * s for c in emit]))
    grid = 1
    while grid * grid < len(cells):
        grid *= 2
    px = 4
    size = grid * px
    max_emit = max([max(c[3]) for c in cells] + [0])

    def image(kind, fn, non_color):
        iname = f"T_{name}_{kind}"
        if iname in bpy.data.images:
            bpy.data.images.remove(bpy.data.images[iname])
        img = bpy.data.images.new(iname, size, size)
        if non_color:
            img.colorspace_settings.name = "Non-Color"
        pix = [0.0] * (size * size * 4)
        for i, cell in enumerate(cells):
            gx, gy = i % grid, i // grid
            rgb = fn(cell)
            for y in range(gy * px, gy * px + px):
                for x in range(gx * px, gx * px + px):
                    k = (y * size + x) * 4
                    pix[k:k + 4] = [*rgb, 1.0]
        img.pixels = pix
        img.pack()
        return img

    col = image("BaseColor", lambda c: [_to_srgb(v) for v in c[0]], False)
    orm = image("ORM", lambda c: [1.0, c[1], c[2]], True)
    emi = image("Emission", lambda c: [_to_srgb(v / max_emit) for v in c[3]], False) if max_emit > 0 else None

    mat = bpy.data.materials.get(f"M_{name}") or bpy.data.materials.new(f"M_{name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(b.outputs[0], out.inputs[0])

    def tex(img, y):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image, t.interpolation, t.location = img, "Closest", (-600, y)
        return t

    nt.links.new(tex(col, 300).outputs["Color"], _inp(b, "Base Color"))
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-300, 0)
    nt.links.new(tex(orm, 0).outputs["Color"], sep.inputs[0])
    nt.links.new(sep.outputs[1], _inp(b, "Roughness"))
    nt.links.new(sep.outputs[2], _inp(b, "Metallic"))
    if emi:
        nt.links.new(tex(emi, -300).outputs["Color"], _inp(b, "Emission Color", "Emission"))
        _inp(b, "Emission Strength").default_value = max_emit

    for o in objs:
        me = o.data
        uv = me.uv_layers.active or me.uv_layers.new(name="UVMap")
        slot_cell = [mats.index(s.material) if s.material in mats else 0 for s in o.material_slots]
        for p in me.polygons:
            i = slot_cell[p.material_index] if slot_cell else 0
            u, v = ((i % grid) + 0.5) / grid, ((i // grid) + 0.5) / grid
            for li in p.loop_indices:
                uv.data[li].uv = (u, v)
        me.materials.clear()
        me.materials.append(mat)
    return f"M_{name}: {len(cells)} colours, {size}px palette"


# ---------------------------------------------------------------- rigging

def unwrap(objects=None, angle=66, margin=0.02):
    """Smart UV project: non-overlapping UVs, needed before bake() or any texture."""
    objs = [o for o in _objs(objects) if o.type == "MESH"]
    _select(objs)
    for o in objs:
        if not o.data.uv_layers:
            o.data.uv_layers.new(name="UVMap")
    with bpy.context.temp_override(active_object=objs[0], selected_editable_objects=objs,
                                   object=objs[0]):
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=margin)
        bpy.ops.object.mode_set(mode="OBJECT")
    return f"unwrapped {len(objs)} meshes"


_BAKE = [("Base Color", "BaseColor", False), ("Roughness", "Roughness", True),
         ("Metallic", "Metallic", True), ("Emission Color", "Emission", False)]


def bake(objects, name, size=1024):
    """Bake the objects' materials (procedural noise, gradients, mixed shaders…) into
    image textures and replace them with one exportable material M_<name>. This is how
    a detailed look survives glTF: shade with any nodes you like, then bake once at the
    end. Needs UVs (run unwrap first). Returns a summary."""
    objs = [o for o in _objs(objects) if o.type == "MESH"]
    for o in objs:
        if not o.data.uv_layers:
            unwrap(objs)
            break
    mats = [s.material for o in objs for s in o.material_slots if s.material]
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    try:
        scene.render.engine = "CYCLES"
    except TypeError as e:
        raise RuntimeError(f"bake needs Cycles: {e}")
    scene.cycles.samples = 1
    scene.render.bake.use_pass_direct = scene.render.bake.use_pass_indirect = False
    scene.render.bake.margin = 8
    images = {}
    for socket, kind, non_color in _BAKE:
        # Bake into a new image: the object may still be showing the previous bake's
        # textures (a re-bake), and deleting those first would bake missing-texture pink.
        img = bpy.data.images.new(f"T_{name}_{kind}.new", size, size)
        if non_color:
            img.colorspace_settings.name = "Non-Color"
        undo = []
        used = False
        for m in mats:
            nt = m.node_tree
            b = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
            out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
            if not b or not out:
                continue
            inp = _inp(b, socket, socket.replace(" Color", ""))
            dark = False
            if socket == "Emission Color":
                strength = _inp(b, "Emission Strength")
                # Nothing emits here: bake black, but still give the material the target
                # image (Blender refuses a bake when one material of the object lacks it).
                dark = bool(strength and not strength.links and strength.default_value == 0)
            emit = nt.nodes.new("ShaderNodeEmission")
            if dark:
                emit.inputs["Color"].default_value = (0, 0, 0, 1)
            elif inp and inp.links:
                nt.links.new(inp.links[0].from_socket, emit.inputs["Color"])
                used = True
            elif inp:
                v = inp.default_value
                emit.inputs["Color"].default_value = (
                    (*list(v)[:3], 1) if hasattr(v, "__len__") else (v, v, v, 1))
                used = used or (max(list(v)[:3]) if hasattr(v, "__len__") else v) > 0
            link = out.inputs["Surface"].links[0]
            src = link.from_socket
            nt.links.remove(link)
            nt.links.new(emit.outputs[0], out.inputs["Surface"])
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            nt.nodes.active = tex
            undo.append((nt, emit, tex, src, out))
        if used:
            _select(objs)
            with bpy.context.temp_override(active_object=objs[0], selected_editable_objects=objs,
                                           object=objs[0]):
                bpy.ops.object.bake(type="EMIT")
            img.pack()
            images[kind] = img
        for nt, emit, tex, src, out in undo:
            nt.links.new(src, out.inputs["Surface"])
            nt.nodes.remove(emit)
            nt.nodes.remove(tex)
        if not used:
            bpy.data.images.remove(img)
    scene.render.engine = prev_engine

    mat = bpy.data.materials.get(f"M_{name}") or bpy.data.materials.new(f"M_{name}")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(b.outputs[0], out.inputs[0])
    y = 400
    for kind, socket in (("BaseColor", "Base Color"), ("Roughness", "Roughness"),
                         ("Metallic", "Metallic"), ("Emission", "Emission Color")):
        if kind not in images:
            continue
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image, t.location = images[kind], (-500, y)
        y -= 300
        target = _inp(b, socket, socket.replace(" Color", ""))
        if kind in ("Roughness", "Metallic"):
            # Single-channel inputs: feed the red channel, not the colour.
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            sep.location = (-250, y + 300)
            nt.links.new(t.outputs["Color"], sep.inputs[0])
            nt.links.new(sep.outputs[0], target)
        else:
            nt.links.new(t.outputs["Color"], target)
        if kind == "Emission":
            _inp(b, "Emission Strength").default_value = 1.0
    for o in objs:
        o.data.materials.clear()
        o.data.materials.append(mat)
    # Only now retire the previous bake's images and give the new ones their names.
    for kind, img in images.items():
        final = f"T_{name}_{kind}"
        old = bpy.data.images.get(final)
        if old and old != img:
            bpy.data.images.remove(old)
        img.name = final
    return f"M_{name}: baked {', '.join(images)} at {size}px"


def copy_rig(armature, name, location=(0, 0, 0)):
    """Duplicate an armature (own bone data, same actions) so a new character can
    share another's animations. Returns the copy."""
    src = _obj(armature)
    new = src.copy()
    new.data = src.data.copy()
    new.name = new.data.name = name
    new.location = location
    (bpy.context.collection or bpy.context.scene.collection).objects.link(new)
    return new


def _rig_of(o):
    return o.parent if o.parent and o.parent.type == "ARMATURE" else next(
        (m.object for m in o.modifiers if m.type == "ARMATURE" and m.object), None)


def bind(mesh, armature, weights_from=None):
    """Skin mesh to armature. weights_from: an already-skinned mesh (e.g. the
    reference character's body) whose weights are copied by nearest surface;
    far more reliable than automatic weights for armour and clothing. It may
    stand anywhere: its rig is moved onto `armature` while copying. Without it,
    automatic (heat) weights are used. Returns the unweighted vertex count."""
    me, arm = _obj(mesh), _obj(armature)
    for m in [m for m in me.modifiers if m.type == "ARMATURE"]:
        me.modifiers.remove(m)
    me.vertex_groups.clear()
    if weights_from:
        src = _obj(weights_from)
        src_arm = _rig_of(src)
        saved = []
        for a in {arm, src_arm} - {None}:
            saved.append((a, a.data.pose_position, a.matrix_world.copy()))
            a.data.pose_position = "REST"
        if src_arm:
            src_arm.matrix_world = arm.matrix_world.copy()
        bpy.context.view_layer.update()
        for g in src.vertex_groups:
            me.vertex_groups.new(name=g.name)
        dt = me.modifiers.new("WeightTransfer", "DATA_TRANSFER")
        dt.object, dt.use_object_transform = src, True
        dt.use_vert_data, dt.data_types_verts = True, {"VGROUP_WEIGHTS"}
        dt.vert_mapping = "POLYINTERP_NEAREST"
        dt.layers_vgroup_select_src, dt.layers_vgroup_select_dst = "ALL", "NAME"
        _select([me])
        with bpy.context.temp_override(object=me, active_object=me):
            bpy.ops.object.modifier_apply(modifier=dt.name)
        for a, pose, mw in saved:
            a.data.pose_position, a.matrix_world = pose, mw
        mw = me.matrix_world.copy()
        me.parent = arm
        me.matrix_world = mw
        me.modifiers.new("Armature", "ARMATURE").object = arm
    else:
        _select([me, arm], active=arm)
        with bpy.context.temp_override(active_object=arm, selected_editable_objects=[me, arm]):
            bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    # Keep the armature modifier first so later modifiers act on the deformed mesh.
    while me.modifiers[0].type != "ARMATURE":
        with bpy.context.temp_override(object=me):
            bpy.ops.object.modifier_move_up(modifier=next(m.name for m in me.modifiers if m.type == "ARMATURE"))
    return _unweighted(me)


def _fcurves(action):
    """An action's F-curves, for layered (4.4+) and older actions alike."""
    layers = getattr(action, "layers", None)
    if layers:
        return [fc for l in layers for s in l.strips for cb in s.channelbags for fc in cb.fcurves]
    return list(getattr(action, "fcurves", []))


def show_keys(armature):
    """After animating: make the keys editable in the Dope Sheet / Action Editor.
    Shows the bone collections of every keyed bone, selects those bones (the
    editors' "Only Show Selected" filter hides the rest), and gives each action a
    fake user so none is lost when another is assigned. Returns a summary."""
    rig = _objs(armature)[0]
    keyed, actions = set(), []
    for a in bpy.data.actions:
        names = {fc.data_path.split('"')[1] for fc in _fcurves(a)
                 if fc.data_path.startswith('pose.bones["')}
        names &= set(rig.pose.bones.keys())
        if names:
            keyed |= names
            actions.append(a)
            a.use_fake_user = True
    for name in keyed:
        bone = rig.data.bones[name]
        bone.hide = False
        for c in getattr(bone, "collections", []):
            c.is_visible = True
        pb = rig.pose.bones[name]
        if hasattr(pb, "select"):   # Blender 5+: selection lives on the pose bone
            pb.select = True
        else:
            bone.select = True
    current = rig.animation_data.action.name if rig.animation_data and rig.animation_data.action else None
    return (f"{len(keyed)} keyed bones shown and selected; actions: "
            f"{', '.join(a.name for a in actions) or 'none'} (assigned: {current})")


# ---------------------------------------------------------------- finishing

def finalize(objects=None, smooth_angle=30):
    """Make game-ready: apply modifiers (except Armature), rotation and scale;
    Shade Smooth with sharp edges above smooth_angle; origin at the base centre
    for unrigged meshes. Run clean() first if parts were joined."""
    out = []
    for o in _objs(objects):
        if o.type != "MESH":
            continue
        if o.data.users > 1:
            o.data = o.data.copy()
        _select([o])
        with bpy.context.temp_override(object=o, active_object=o, selected_editable_objects=[o]):
            for m in [m for m in o.modifiers if m.type != "ARMATURE"]:
                bpy.ops.object.modifier_apply(modifier=m.name)
            if not _rig_of(o):
                bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        me = o.data
        for p in me.polygons:
            p.use_smooth = True
        if hasattr(me, "set_sharp_from_angle"):
            me.set_sharp_from_angle(angle=math.radians(smooth_angle))
        if not _rig_of(o) and me.vertices:
            xs, ys, zs = zip(*(v.co for v in me.vertices))
            base = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)))
            me.transform(Matrix.Translation(-base))
            o.matrix_world = o.matrix_world @ Matrix.Translation(base)
        me.update()
        out.append(o.name)
    return "finalized " + ", ".join(out)


def export_glb(objects, path):
    """Export objects (plus their armature) as .glb with modifiers applied. Only
    deform bones go out, so a Rigify control rig exports as a clean skeleton; its
    animation is sampled onto them."""
    objs = _objs(objects)
    objs += [a for a in {_rig_of(o) for o in objs if o.type == "MESH"} if a and a not in objs]
    _select(objs)
    path = os.path.expanduser(bpy.path.abspath(path))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                              export_apply=True, export_def_bones=True)
    return f"exported {len(objs)} objects to {path}"


# ---------------------------------------------------------------- 3D printing and BIM

from . import printing as _print  # noqa: E402
from .bim import (bim_door, bim_dimension, bim_drawing, bim_element, bim_material,  # noqa: E402,F401
                  bim_member, bim_opening, bim_project, bim_qto, bim_report, bim_save, bim_slab, bim_space,
                  bim_type, bim_walls, bim_window)

print_setup = _print.setup_mm
print_fix = _print.fix
print_fit = _print.fit
print_split = _print.split
print_arrange = _print.arrange
print_export = _print.export
print_slice = _print.slice
boolean = _print.boolean
label = _print.label


def print_check(objects=None, min_wall=None, overhang=45.0, ledge=1.5):
    """Printability report (mm): size, shells, volume and filament, bed contact, overhangs
    (those reaching under `ledge` mm past the layer below, like thread flanks, print fine and
    don't count), watertightness, self-intersections, thin walls (sharp edges aren't walls),
    fits the bed. Returns the text."""
    return _print.check(objects, min_wall, overhang, ledge)[0]


GUIDES_DIR = os.path.join(os.path.dirname(__file__), "guides")


def _body(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    return lines, "\n".join(l for l in lines if not l.startswith("# ")).strip()


def guide_path(name):
    """The user's edited copy (LLM Chat menu > Edit Guide), else the shipped guide; an
    unedited copy follows add-on updates."""
    import hashlib
    own = os.path.join(bpy.utils.user_resource("CONFIG", path="claude_chat"), "guides", name + ".md")
    if os.path.isfile(own):
        lines, body = _body(own)
        if f"# default: {hashlib.sha1(body.encode('utf-8')).hexdigest()[:12]}" not in lines:
            return own
    return os.path.join(GUIDES_DIR, name + ".md")


def guide(name=None):
    """Know-how for a mode, read when needed: guide() lists them."""
    names = sorted(f[:-3] for f in os.listdir(GUIDES_DIR) if f.endswith(".md"))
    if name not in names:
        return "guides: " + ", ".join(names)
    return _body(guide_path(name))[1]
