"""The Blender side of Claude's tools.

Claude Code starts mcp_server.py, which forwards each tool call here over a local
socket. The socket only listens on 127.0.0.1, on a port the OS picks, and every
request must carry a token that is random per Blender session, so no other
program on the machine can use it to run code in Blender.

Tool calls run on Blender's main thread (bpy is not thread safe), with the first
3D viewport as context so viewport operators work as they do for the user.
"""

import base64
import hmac
import io
import json
import os
import queue
import secrets
import socket
import tempfile
import threading
import traceback
from contextlib import redirect_stdout

import bpy

TOKEN = secrets.token_hex(24)
MAX_REQUEST = 8 * 1024 * 1024  # a script is never this big; stops runaway reads

_requests = queue.Queue()      # (tool, args, reply queue), handed to the main thread
_server = None
_port = 0
TURN_START = set()             # object names when the user's message was sent (scene_audit)
TURN_COLLECTIONS = set()       # collection names then: a collection made since is one asset
MODE = "game"                  # the panel's work mode when the turn started: game, print, bim


# ---------------------------------------------------------------- tools

def _describe_prop(prop):
    entry = {"identifier": prop.identifier, "type": prop.type}
    if prop.description:
        entry["description"] = prop.description
    if getattr(prop, "is_readonly", False):
        entry["readonly"] = True
    if prop.type == "ENUM":
        entry["items"] = [i.identifier for i in prop.enum_items]
    elif prop.type in {"FLOAT", "INT"} and getattr(prop, "array_length", 0):
        entry["array_length"] = prop.array_length
    elif prop.type in {"POINTER", "COLLECTION"} and getattr(prop, "fixed_type", None):
        entry["of"] = prop.fixed_type.identifier
    try:
        default = prop.default_array if getattr(prop, "array_length", 0) else prop.default
        entry["default"] = list(default) if hasattr(default, "__len__") and \
            not isinstance(default, str) else default
    except (AttributeError, TypeError):
        pass
    return entry


def execute_blender_code(code):
    namespace = {"bpy": bpy, "__name__": "__claude__"}
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            exec(compile(code, "<claude script>", "exec"), namespace)
    except Exception:  # noqa: BLE001  (reported back to Claude)
        printed = out.getvalue()
        raise RuntimeError((printed + "\n" if printed else "") + traceback.format_exc(limit=-6))
    return out.getvalue() or "(done, nothing printed)"


def get_scene_info():
    scene = bpy.context.scene
    objs = list(scene.objects)
    return json.dumps({
        "scene": scene.name,
        "unit_scale": scene.unit_settings.scale_length,
        "frame": [scene.frame_start, scene.frame_current, scene.frame_end],
        "render_engine": scene.render.engine,
        "objects": [{"name": o.name, "type": o.type,
                     "location": [round(v, 3) for v in o.location],
                     "dimensions": [round(v, 3) for v in o.dimensions],
                     "parent": o.parent.name if o.parent else None,
                     "collection": o.users_collection[0].name if o.users_collection else None}
                    for o in objs[:100]],
        "more_objects": max(0, len(objs) - 100),
        "materials": len(bpy.data.materials),
        "collections": [c.name for c in bpy.data.collections][:50],
    })


def get_object_info(object_name):
    o = bpy.data.objects.get(object_name)
    if not o:
        raise ValueError(f"No object named {object_name!r}")
    info = {
        "name": o.name, "type": o.type,
        "location": list(o.location), "rotation_euler": list(o.rotation_euler),
        "scale": list(o.scale), "dimensions": list(o.dimensions),
        "parent": o.parent.name if o.parent else None,
        "children": [c.name for c in o.children],
        "visible": o.visible_get(),
        "materials": [s.material.name for s in o.material_slots if s.material],
        "modifiers": [f"{m.name} ({m.type})" for m in o.modifiers],
    }
    if o.type == "MESH":
        m = o.data
        info["mesh"] = {"vertices": len(m.vertices), "edges": len(m.edges),
                        "faces": len(m.polygons),
                        "triangles": sum(len(p.vertices) - 2 for p in m.polygons),
                        "uv_layers": [u.name for u in m.uv_layers]}
    elif o.type == "ARMATURE":
        info["bones"] = [b.name for b in o.data.bones][:200]
    return json.dumps(info)


def _view3d():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region:
                    return win, area, region
    return None, None, None


def _offscreen(area, region, max_size):
    """The viewport as it is now, drawn off-screen: (width, height, RGBA float array)."""
    if bpy.app.background:  # no GPU without a window (refreshing the view would crash)
        raise RuntimeError("no viewport drawing in background mode")
    import gpu
    import numpy as np
    space = area.spaces.active
    space.region_3d.update()
    scale = min(1.0, max_size / max(region.width, region.height))
    w, h = max(1, int(region.width * scale)), max(1, int(region.height * scale))
    off = gpu.types.GPUOffScreen(w, h)
    try:
        off.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region,
                        space.region_3d.view_matrix, space.region_3d.window_matrix,
                        do_color_management=True)
        buf = off.texture_color.read()
    finally:
        off.free()
    buf.dimensions = w * h * 4
    return w, h, np.asarray(buf, dtype=np.float32).reshape(h, w, 4) / 255.0


SHEET = ("FRONT", "RIGHT", "TOP", "ISO")
# Which faces each view shows (+X right, -Y front, +Z up).
SHOWS = {"FRONT": "the -Y face", "BACK": "the +Y face", "RIGHT": "the +X face",
         "LEFT": "the -X face", "TOP": "the +Z face", "BOTTOM": "the -Z face",
         "ISO": "-Y, +X and +Z faces (from front-right, above)",
         "ISO_BACK": "+Y, -X and +Z faces (from back-left, above)"}


def get_viewport_screenshot(max_size=800, view=None, target=None):
    """An image of the viewport. With `view` (FRONT, BACK, LEFT, RIGHT, TOP, ISO, ISO_BACK)
    the camera is aimed at `target` (or everything) from that side first; view "ALL" gives
    one image with FRONT, RIGHT, TOP and ISO tiled 2x2. The user's own view is restored
    afterwards. Returns PNG bytes (and a caption for tiled views)."""
    import numpy as np
    from . import helpers as h
    win, area, region = _view3d()
    if not area:
        raise RuntimeError("No 3D viewport is open")
    max_size = max(64, min(int(max_size or 800), 2000))
    r3d = area.spaces.active.region_3d
    saved = (r3d.view_location.copy(), r3d.view_rotation.copy(), r3d.view_distance,
             r3d.view_perspective, area.spaces.active.shading.type)
    view = str(view).upper() if view else None
    views = list(SHEET) if view == "ALL" else ([view] if view else [None])
    objs = None
    if target:
        objs = [o for o in bpy.data.objects if o.name == target or o.name.startswith(target + "_")
                or (o.parent and o.parent.name == target)]
        if not objs:
            raise ValueError(f"No object named {target!r}")
    fd, path = tempfile.mkstemp(suffix=".png", prefix="claude_view_")
    os.close(fd)
    try:
        try:
            tiles = []
            for v in views:
                if v:
                    h.frame(objs, view=v)
                tiles.append(_offscreen(area, region, max_size // 2 if len(views) > 1 else max_size))
            if len(tiles) == 1:
                w, hgt, px = tiles[0]
            else:
                th, tw = min(t[1] for t in tiles), min(t[0] for t in tiles)
                t = [x[2][:th, :tw] for x in tiles]
                sep = np.ones((th, 2, 4), dtype=np.float32)
                top = np.hstack([t[0], sep, t[1]])          # FRONT | RIGHT
                bottom = np.hstack([t[2], sep, t[3]])       # TOP | ISO
                line = np.ones((2, top.shape[1], 4), dtype=np.float32)
                px = np.vstack([bottom, line, top])         # image rows run bottom-up
                hgt, w = px.shape[:2]
            img = bpy.data.images.new("_claude_view", w, hgt, alpha=True)
            img.pixels.foreach_set(px.ravel())
        except Exception:  # noqa: BLE001  (no GPU context: grab the window instead)
            if view == "ALL":
                raise
            with bpy.context.temp_override(window=win, area=area, region=region):
                bpy.ops.screen.screenshot_area(filepath=path)
            img = bpy.data.images.load(path)
            w, hgt = img.size
            if max(w, hgt) > max_size:
                k = max_size / max(w, hgt)
                img.scale(max(1, int(w * k)), max(1, int(hgt * k)))
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)
        with open(path, "rb") as f:
            out = {"image": base64.b64encode(f.read()).decode()}
        if len(views) > 1:
            out["caption"] = ("Four views: top-left FRONT (-Y face), top-right RIGHT (+X face), "
                              "bottom-left TOP (+Z face), bottom-right ISO (-Y, +X, +Z faces).")
        elif view in SHOWS:
            out["caption"] = f"{view} view: shows {SHOWS[view]}."
        return out
    finally:
        if view:
            r3d.view_location, r3d.view_rotation, r3d.view_distance = saved[0], saved[1], saved[2]
            r3d.view_perspective = saved[3]
            area.spaces.active.shading.type = saved[4]
        try:
            os.remove(path)
        except OSError:
            pass


def bpy_api_lookup(query):
    q = (query or "").strip().removeprefix("bpy.")
    if q.startswith("ops."):
        parts = [p.split("(")[0] for p in q[4:].split(".") if p]
        if len(parts) < 2:
            raise ValueError("Expected bpy.ops.<category>.<operator>")
        op = getattr(getattr(bpy.ops, parts[0], None), parts[1], None)
        if op is None:
            raise ValueError(f"Unknown operator bpy.ops.{parts[0]}.{parts[1]}")
        rna = op.get_rna_type()
        return json.dumps({"operator": f"bpy.ops.{parts[0]}.{parts[1]}",
                           "description": rna.description,
                           "parameters": [_describe_prop(p) for p in rna.properties
                                          if p.identifier != "rna_type"]})
    parts = [p for p in q.split(".") if p and p != "types"]
    if not parts:
        raise ValueError("Empty query")
    cls = getattr(bpy.types, parts[0], None)
    if cls is None:
        close = sorted(n for n in dir(bpy.types) if parts[0].lower() in n.lower())[:20]
        raise ValueError(f"Unknown type {parts[0]}. Close matches: {', '.join(close)}")
    rna = cls.bl_rna
    if len(parts) > 1:
        name = parts[1]
        if name in rna.properties:
            return json.dumps(_describe_prop(rna.properties[name]))
        if name in rna.functions:
            fn = rna.functions[name]
            return json.dumps({"function": f"{parts[0]}.{name}", "description": fn.description,
                               "parameters": [_describe_prop(p) for p in fn.parameters]})
        raise ValueError(f"{parts[0]} has no property or function {name!r}")
    return json.dumps({
        "type": parts[0], "description": rna.description,
        "base": rna.base.identifier if rna.base else None,
        "properties": [_describe_prop(p) for p in rna.properties if p.identifier != "rna_type"],
        "functions": list(rna.functions.keys()),
    })


def describe_node_type(bl_idname, property_overrides=None):
    """Instantiate the node in a scratch tree to report its real sockets."""
    cls = getattr(bpy.types, bl_idname, None)
    if not (isinstance(cls, type) and issubclass(cls, bpy.types.Node)):
        close = sorted(n for n in dir(bpy.types) if "Node" in n and bl_idname.lower() in n.lower())
        raise ValueError(f"Unknown node type {bl_idname}. Close matches: {', '.join(close[:15])}")
    for tree_type in ("ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree"):
        tree = bpy.data.node_groups.new("_claude_scratch", tree_type)
        try:
            node = tree.nodes.new(bl_idname)
        except RuntimeError:
            bpy.data.node_groups.remove(tree)
            continue
        try:
            for k, v in (property_overrides or {}).items():
                setattr(node, k, v)
            base = set(bpy.types.Node.bl_rna.properties.keys())

            def sockets(items):
                out = []
                for i, s in enumerate(items):
                    e = {"index": i, "name": s.name, "identifier": s.identifier, "type": s.type}
                    if hasattr(s, "default_value"):
                        d = s.default_value
                        e["default"] = list(d) if hasattr(d, "__len__") and \
                            not isinstance(d, str) else d
                    out.append(e)
                return out

            return json.dumps({
                "bl_idname": bl_idname, "tree": tree_type,
                "properties": [_describe_prop(p) for p in node.bl_rna.properties
                               if p.identifier not in base],
                "inputs": sockets(node.inputs), "outputs": sockets(node.outputs),
            }, default=str)
        finally:
            bpy.data.node_groups.remove(tree)
    raise ValueError(f"{bl_idname} can't be created in any node tree")


def _owns(action, rig):
    """Whether an action was made for this rig: its slot is named after the rig (4.4+
    layered actions) and it keys only bones the rig has."""
    slots = getattr(action, "slots", None)
    if slots is not None and len(slots) and not any(sl.identifier[2:] == rig.name for sl in slots):
        return False
    from .helpers import _fcurves
    bones = {fc.data_path.split('"')[1] for fc in _fcurves(action)
             if fc.data_path.startswith('pose.bones["')}
    return bool(bones) and bones <= set(rig.pose.bones.keys())


def _still_actions(meshes):
    """Play each of a rig's own actions and measure how far its skinned mesh moves:
    an action that is keyed but doesn't move anything is reported."""
    found = []
    scene = bpy.context.scene
    frame = scene.frame_current
    for m in meshes:
        rig = next((x.object for x in m.modifiers if x.type == "ARMATURE" and x.object), None)
        if not rig or not bpy.data.actions:
            continue
        ad = rig.animation_data or rig.animation_data_create()
        current = ad.action
        try:
            for act in [a for a in bpy.data.actions if _owns(a, rig)]:
                try:
                    ad.action = act
                except Exception:  # noqa: BLE001  (an action for another kind of data)
                    continue
                start, end = (int(x) for x in act.frame_range)
                samples = []
                for f in sorted({start, (3 * start + end) // 4, (start + end) // 2,
                                 (start + 3 * end) // 4}):
                    scene.frame_set(f)
                    dg = bpy.context.evaluated_depsgraph_get()
                    samples.append([v.co.copy() for v in m.evaluated_get(dg).data.vertices])
                move = max((max((a - b).length for a, b in zip(samples[0], s)) for s in samples[1:]),
                           default=0.0)
                if move < 0.01:
                    found.append(f"action {act.name} doesn't move {m.name} (max {move:.3f} m): "
                                 f"its keys must pose the rig's control bones differently over time")
        finally:
            ad.action = current
            scene.frame_set(frame)
    return found


FLOATING_LIMIT = 300000  # vertices per asset; beyond this the audit's contact check is too slow
NEAR = 0.25              # metres: assets this close are checked for contact together


def scene_audit(before=None):
    """What the scene really contains compared with the start of this turn (objects
    added, tris, materials, rigs, actions) and the problems the checks find."""
    from . import helpers as h
    if MODE == "print":
        return _print_audit()
    if MODE == "bim":
        return _bim_audit()
    before = set(TURN_START if before is None else before)
    new = [o for o in bpy.data.objects if o.name not in before]
    widgets = [o for o in new if o.name.startswith("WGT")]  # Rigify control shapes
    new = [o for o in new if o not in widgets]
    lines = [f"objects added this turn: {len(new)}"
             + (f" (plus {len(widgets)} rig control shapes)" if widgets else "")]
    problems = []
    dg = bpy.context.evaluated_depsgraph_get()
    meshes = []
    for o in new:
        if o.type == "MESH":
            meshes.append(o)
            tris = sum(len(p.vertices) - 2 for p in o.evaluated_get(dg).data.polygons)
            mats = [s.material.name for s in o.material_slots if s.material]
            lines.append(f"- {o.name}: {tris} tris, {round(max(o.dimensions), 2)} m long, "
                         f"materials {mats or 'none'}, parent {o.parent.name if o.parent else 'none'}")
            if not mats:
                problems.append(f"{o.name} has no material")
            linked = {i for e in o.data.edges for i in e.vertices}
            stray = len(o.data.vertices) - len(linked)
            if stray:
                problems.append(f"{o.name}: {stray} stray vertices not connected to anything")
            rig = next((m.object for m in o.modifiers if m.type == "ARMATURE" and m.object), None)
            if rig:
                deform = {b.name for b in rig.data.bones if b.use_deform}
                names = {g.index: g.name for g in o.vertex_groups}
                loose = sum(1 for v in o.data.vertices if not any(
                    g.weight > 0 and names.get(g.group) in deform for g in v.groups))
                if loose:
                    problems.append(f"{o.name}: {loose} vertices aren't weighted to any deform "
                                    f"bone, so they won't move with the rig")
        elif o.type == "ARMATURE":
            used = any(m.type == "ARMATURE" and m.object == o for x in bpy.data.objects
                       if x.type == "MESH" for m in x.modifiers)
            lines.append(f"- {o.name}: armature, {len(o.data.bones)} bones, "
                         f"{sum(b.use_deform for b in o.data.bones)} deform")
            if not used:
                problems.append(f"{o.name} is an unused armature (no mesh is skinned to it)")
        else:
            lines.append(f"- {o.name}: {o.type.lower()}")
    acts = [f"{a.name} ({int(a.frame_range[1] - a.frame_range[0])} frames)" for a in bpy.data.actions]
    lines.append(f"actions in the file: {acts or 'none'}")
    problems += _still_actions(meshes)
    # Floating parts, checked per asset, so separate props standing apart aren't reported
    # as floating away from each other. An asset is a top parent with its children, or
    # everything in a collection made this turn; assets whose bounding
    # boxes come within NEAR metres are checked together (a loose wheel by its car).
    from mathutils import Vector
    assets = {}
    for o in meshes:
        top = o
        while top.parent:
            top = top.parent
        col = top.users_collection[0] if top.users_collection else None
        key = col.name if col and col.name not in TURN_COLLECTIONS and \
            col != bpy.context.scene.collection else top.name
        assets.setdefault(key, []).append(o)

    def box(group):
        pts = [o.matrix_world @ Vector(c) for o in group for c in o.bound_box]
        return (Vector([min(q[i] for q in pts) for i in range(3)]),
                Vector([max(q[i] for q in pts) for i in range(3)]))
    groups = list(assets.values())
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for k in range(i + 1, len(groups)):
                (amin, amax), (bmin, bmax) = box(groups[i]), box(groups[k])
                if all(amin[d] - NEAR <= bmax[d] and bmin[d] - NEAR <= amax[d] for d in range(3)):
                    groups[i] += groups.pop(k)
                    merged = True
                    break
            if merged:
                break
    assets = {g[0].name: g for g in groups}
    for group in assets.values():
        if sum(len(o.data.vertices) for o in group) > FLOATING_LIMIT:
            lines.append(f"floating check skipped for {group[0].name} (too dense to check "
                         f"quickly): run h.floating on its parts")
            continue
        try:
            problems += [l for l in h.floating(group).splitlines() if l.startswith("FLOATING")]
        except Exception as e:  # noqa: BLE001
            lines.append(f"floating check failed: {e}")
    if problems:
        lines.append("PROBLEMS (fix each one, or tell the user why it is intended):")
        lines += [f"- {p}" for p in problems]
    else:
        lines.append("no problems found by the automatic checks")
    return {"text": "\n".join(lines), "problems": problems}


def _print_parts():
    """The parts meant for printing: visible meshes outside reference/preview collections."""
    return [o for o in bpy.context.visible_objects if o.type == "MESH" and not any(
        c.name.startswith(("_Reference", "_Preview")) or c.name.endswith("-noimp")
        for c in o.users_collection)]


def _print_audit():
    from . import printing
    parts = _print_parts()
    if not parts:
        return {"text": "no printable parts in the scene", "problems": []}
    text, problems = printing.check(parts)
    return {"text": text, "problems": problems}


def _bim_audit():
    import sys
    if "bonsai" not in sys.modules and not any(k.endswith(".bonsai") for k in sys.modules):
        return {"text": "Bonsai isn't loaded: no IFC project", "problems": []}
    from . import bim
    try:
        text = bim.bim_report(qto=False)
    except RuntimeError as e:
        return {"text": str(e), "problems": []}
    tail = text.rsplit("problems: ", 1)[-1]
    problems = [] if tail == "none" else tail.split("; ")
    return {"text": text, "problems": problems}


TOOLS = {f.__name__: f for f in (execute_blender_code, get_scene_info, get_object_info,
                                  get_viewport_screenshot, bpy_api_lookup, describe_node_type,
                                  scene_audit)}


# ---------------------------------------------------------------- main thread

def _run(tool, args):
    fn = TOOLS.get(tool)
    if not fn:
        return {"error": f"Unknown tool {tool}"}
    try:
        win, area, region = _view3d()
        if area:
            with bpy.context.temp_override(window=win, area=area, region=region):
                result = fn(**args)
        else:
            result = fn(**args)
    except Exception as e:  # noqa: BLE001  (reported back to Claude)
        return {"error": str(e) or type(e).__name__}
    return result if isinstance(result, dict) else {"text": result}


def call(tool, args):
    """From another thread (the API models' loop, api.py): run a tool on the main thread
    and wait for it. start() must have run (it registers the main-thread timer)."""
    reply = queue.Queue()
    _requests.put((tool, args if isinstance(args, dict) else {}, reply))
    return reply.get()


def _drain():
    while True:
        try:
            tool, args, reply = _requests.get_nowait()
        except queue.Empty:
            return 0.05
        reply.put(_run(tool, args))


# ---------------------------------------------------------------- socket

def _recv_line(conn):
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_REQUEST:
            raise ValueError("request too large")
    return data


def _serve(conn):
    with conn:
        try:
            conn.settimeout(30)
            req = json.loads(_recv_line(conn))
            if not hmac.compare_digest(str(req.get("token", "")), TOKEN):
                conn.sendall(b'{"error": "bad token"}\n')
                return
            conn.settimeout(None)  # a big build script can take minutes
            reply = queue.Queue()
            _requests.put((req.get("tool"), req.get("args") or {}, reply))
            conn.sendall(json.dumps(reply.get()).encode() + b"\n")
        except (OSError, ValueError):
            pass


def _accept(server):
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            return  # closed by stop()
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


def start():
    """Start listening (once per session). Returns the port."""
    global _server, _port
    if _server:
        return _port
    _server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _server.bind(("127.0.0.1", 0))
    _server.listen(4)
    _port = _server.getsockname()[1]
    threading.Thread(target=_accept, args=(_server,), daemon=True).start()
    if not bpy.app.timers.is_registered(_drain):
        bpy.app.timers.register(_drain, persistent=True)
    return _port


def stop():
    global _server
    if _server:
        _server.close()
        _server = None
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
