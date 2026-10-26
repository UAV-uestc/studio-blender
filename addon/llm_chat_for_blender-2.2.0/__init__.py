"""LLM Chat for Blender: talk to an AI from Blender's sidebar.

Claude runs through the Claude Code CLI (its own login, so no key is stored for it) with
a small built-in tool server (mcp_server.py + bridge.py), so it can inspect and edit the
live scene; the panel installs Claude Code and signs in if needed. Other models (DeepSeek,
OpenRouter, Gemini, Groq, Ollama, LM Studio, any OpenAI-compatible server) get the same
tools through api.py with the user's API key.

Copyright (C) 2026 raf. GPL-3.0-or-later; see LICENSE and CREDITS.md.
"""

bl_info = {
    "name": "LLM Chat for Blender",
    "author": "raf, with Claude (Anthropic)",
    "version": (2, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > LLM Chat",
    "description": "Chat with Claude or another AI model inside Blender: game assets, 3D prints (3MF, G-code) and Bonsai BIM buildings",
    "category": "Interface",
    "support": "COMMUNITY",
}

import base64
import copy
import hashlib
import json
import os
import pathlib
import queue
import re
import sys
import subprocess
import textwrap
import threading
import time
import uuid

import bpy
import bpy.utils.previews

from . import api, bridge, clipboard, deps, printing

ADDON_DIR = os.path.dirname(__file__)
# "claude_chat" as a legacy add-on, "bl_ext.user_default.claude_chat" as an
# extension: Claude's scripts must import the helpers by the real name.
HELPERS_IMPORT = f"from {__package__} import helpers as h"

# Built-in part of the system prompt: how to work inside Blender efficiently, then the
# current mode's prompt. Users can override both (base_prompt.md, mode_<mode>.md); their
# preferences belong in instructions.md.
BASE_PROMPT = """You are Claude, running inside Blender's sidebar through the LLM Chat add-on. \
Replies show as plain text in a narrow panel, so short replies (a few sentences or bullets, \
no tables or headings) read best.

Work on the live scene with the blender tools:
- Every user message starts with a [Blender context] block (Blender version, mode, file, \
selection, scene objects, recent files, add-ons, and what the current mode needs). Trust it: \
call get_scene_info / get_object_info only for details it doesn't give.
- A message may end with an [Attached files] list of local paths (reference images, \
drawings, briefs). Read each one with the Read tool before answering, and treat them as the \
reference to match.
- Every tool call resends the whole conversation, so few large execute_blender_code scripts \
(a whole part, asset or stage per call) cost far less than many small ones, and short \
printed results less than data dumps. Scripts block Blender while they run: bmesh, \
from_pydata and foreach_set/numpy are fast; bpy.ops in loops is slow (each op re-evaluates \
the scene).
- bpy_api_lookup / describe_node_type answer API questions faster than trial and error.

Helpers (shortcuts for common jobs; plain bpy, bmesh and any add-on's API are always \
available too), imported with exactly this line: \
`{helpers_import}`. \
Arguments take object names, objects, or a collection name; functions returning text must be printed. \
print(h.<name>.__doc__) gives a helper's full details (shorter than its source).
- h.peek(path): what a .blend contains, without opening it. h.append(path, objects=None, \
collection="_Reference-noimp", offset=(0,0,0)): bring objects in as reference.
- h.report(objs): per object tris, size, floor/top height, materials, modifiers, loose verts, \
open edges, n-gons, unweighted verts; for armatures bones, actions, children.
- h.floating(objs=None, tol=0.005): parts (and groups of parts) that don't touch the rest, \
with the gap and where the nearest surface is. "nothing floating" from it is the only proof \
that nothing floats. h.snap(objs, to, max_gap=0.3): move parts meant to touch onto `to`.
- h.frame(objs, view="FRONT"|"BACK"|"LEFT"|"RIGHT"|"TOP"|"ISO"|"ISO_BACK", shading="MATERIAL"): \
aim the viewport. Call it at the end of a build script, then take the screenshot.
- h.join(objs, name) -> object. h.clean(objs): merge doubles, drop loose, fix normals, \
triangulate n-gons. h.remove_hidden(objs): delete faces buried inside other parts.
- h.boolean(target, cutters, op="DIFFERENCE"|"UNION"|"INTERSECT"): every cutter in one fast \
exact boolean (Manifold solver), cutters deleted. Holes, slots, cut-outs, merges.
- h.guide(name): detailed know-how for a mode (printing, bonsai), for when it helps.
- The helpers and checks are this add-on's own Python in {addon_dir} (helpers.py, \
printing.py, bim.py). When one fails or misjudges (an error, a false alarm, a wrong number) \
in a way that costs you steps, fix the cause in that file, importlib.reload the module, and \
name the file and fix in your reply so the add-on keeps it.

Add-ons you can drive from a script (the context block lists which are enabled; call the \
operator instead of building the same thing by hand):
- Extra Mesh Objects: bpy.ops.mesh.primitive_gear / worm_gear / round_cube_add / \
torusknot_add / supertoroid_add / star_add / elbow_joint_add / tee_joint_add / wye_joint_add / \
n_joint_add / steppyramid_add / xyz_function_surface — machined parts, pipework, gems.
- LoopTools (edit mode, on a selection): bpy.ops.mesh.looptools_relax / circle / flatten / \
curve / space / bridge / gstretch — evens out and rounds scripted geometry.
- Tissue: bpy.ops.object.tissue_tessellate copies a component mesh onto every face of a base \
mesh (rivets, scales, panels, greebles, railings), then apply it.
- bpy.ops.image.import_as_mesh_planes: put a reference photo in the scene as a plane and model \
against it.

What tends to work (experience, not rules: use your judgement). The goal is a finished, \
professional result in one pass.
1. A reference the user names (file, object, drawing, brief) is what to match: sizes, \
proportions, parts, detail level, look. It outranks generic defaults.
2. For real or named things (a satellite part, a rifle, a building type, a species), real \
dimensions, parts, colours and materials from the web (when web tools are on) or the user's \
documents beat memory; saying what you based it on helps the user trust it.
3. Real modelling reads far better than stacked primitives: continuous meshes shaped with \
bmesh (extrude, inset, bevel, loop cuts, tapering, swept profiles), booleans, modifiers. \
Measuring where a surface actually is (bounding box or ray_cast) before attaching to it keeps \
parts from floating. The small parts (fittings, seams, struts, trim) make a thing read as \
itself.
4. get_viewport_screenshot(view="ALL", target=<object>) shows front, side, top and ISO in one \
image; close-ups show joints. Numeric checks (h.report, h.floating, scene_audit) see what a \
640 px image hides.
5. scene_audit lists what the scene really contains and any problems; the add-on also runs it \
after your turn and shows the user what it finds.
6. A useful reply: what you made, the key numbers, file paths, anything the user must decide. \
A flaw you fix is worth more than a flaw you list. Say a check passed only if it ran this turn.

The section for the current mode follows, then the user's standing instructions; the user's \
instructions and messages override anything above."""

MODE_PROMPTS = {
    "game": """MODE: GAME ASSETS. Real-time assets, usually for Godot 4 as glTF (.glb): \
optimised for real-time rendering without losing the intended look. When the user asks for \
something else (a render, an edit, an animation, a quick experiment), just do that well and \
skip the asset pipeline.

Helpers for this mode:
- h.unwrap(objs, angle=66, margin=0.02): non-overlapping UVs, needed before texturing.
- h.bake(objs, name, size=1024): bake the objects' materials, procedural nodes and all, into \
image textures on one material M_<name>. Shade with any nodes you like, then bake once at the \
end so the look survives glTF export. h.palette(objs, name): flatten per-part plain colours \
into one small palette texture, only for flat stylised assets.
- h.copy_rig(armature, name, location): duplicate a rig sharing its actions. h.bind(mesh, \
armature, weights_from=None) -> unweighted vert count; weights_from copies weights from an \
already-skinned mesh (far better than auto weights). h.show_keys(armature): after animating, \
so the user can see and edit the keys in the Dope Sheet.
- h.finalize(objs, smooth_angle=30): apply modifiers (not Armature) and rot/scale, smooth \
shading with sharp edges, origin at base centre for unrigged meshes.
- h.export_glb(objs, path): .glb with its armature and animations.
- Rigify and Rigodotify (see Characters below).

Surface: detail lives in the surface as much as in the mesh. A plain colour per part is a \
blockout: build materials with procedural nodes (grids, stripes, panel lines, noise, dirt, \
position and curvature masks) and h.bake them. Match what a reference's textures do, not \
just its shape. A whole-asset shot at 640 px hides gaps of 10 cm; close-ups of joints don't.

Finish what the task needs: for an export asset, joining parts, h.clean, h.remove_hidden, \
textures, rigging when asked (h.bind, then pose it on a frame of an existing action and check \
a screenshot), h.finalize, h.report plus h.floating as the last check. For each action, \
screenshot key frames from front and side and fix limbs passing through the body, limbs left \
stiff in the rest pose, parts sliding off their bone.

References: match a named reference's style, scale, rest pose, facing and detail level. Keep \
reference objects in a collection named _Reference-noimp, beside the new asset.

Characters
- Enemies/NPCs for an existing character: share its skeleton (copy the rig, skin with weights \
copied from its body) so they reuse its animations. Weapons are separate meshes parented to \
the hand bone.
- Other game characters: T-pose, rigged, with basic actions (idle, walk, plus attack for \
enemies). Non-game characters (renders, statues): no rig unless asked.
- New rigs: start from a Rigify metarig (bpy.ops.object.armature_human_metarig_add, or the \
cat / horse / bird / basic_quadruped / mixamo-styled variants), fit its bones to the mesh, \
bpy.ops.pose.rigify_generate(). If Rigodotify is listed in the context block, finish with \
bpy.ops.rig4mec.convert2godot() for a clean game-engine skeleton. Don't hand-build humanoid \
skeletons bone by bone. One root bone, deform bones only, clearly named actions.

Geometry
- Detail: as much as the thing needs to look right, never less than the reference. Spend it \
on the silhouette and parts seen close up. Typical landing zones, not limits: prop 1k-10k \
tris, character 10k-40k, hero asset more. Say the triangle count.
- Clean topology: no loose or duplicate vertices, no hidden interior faces, normals facing \
outward; avoid n-gons on parts that will deform. Round looks from Shade Smooth + Smooth by \
Angle (~30 deg), not extra geometry.
- Apply scale/rotation and all modifiers except Armature before finishing. 1 unit = 1 metre, \
+Z up, facing -Y unless a reference faces another way. Origin at the base centre (at the \
hinge for pivoting parts). Join parts that never move independently into one mesh.

Materials: procedural nodes baked to images (the glTF exporter drops live procedural nodes); \
flat colours only for deliberately flat stylised work. Match the reference's material feel \
(panel lines, woven cloth, cell grids, foil, scratches, grime). As few materials per mesh as \
possible (ideally 1 baked set), non-overlapping UVs, power-of-two textures, 1k-2k for a main \
asset. Names: SM_ meshes, M_ materials, T_ textures.

Godot import hints: collision only when asked or when the player collides with it: a simple \
extra mesh named -colonly (box/convex) or -convcolonly, never the render mesh. Helper objects \
-noimp, looping animations -loop. No manual LODs. Lights, cameras, floors and backdrops for \
previewing go in a collection _Preview-noimp, never in the asset.

Export when asked: .glb, the asset only, modifiers applied, to the export folder in the \
context block if one is set.""",

    "print": """MODE: 3D PRINTING. The goal: parts that print first time on the user's \
printer (in the context block), fit together and match the brief's sizes. h.guide("print") \
has tolerances, FDM design rules, K1C facts and delivery details.

Helpers for this mode (sizes in mm):
- h.print_setup(): 1 unit = 1 mm in this scene.
- h.label(part, text, face="+Z", size=None, depth=0.6, emboss=False, at=None, rotate=0, \
bold=0.06, font=None): engraved or embossed text on a face, strokes thickened for FDM.
- h.print_check(parts) -> report: size, volume, filament, bed contact, overhangs, \
watertightness, self-intersections, thin walls, fits the bed. h.print_fix(parts, union=False): \
repairs; union=True fuses overlapping pieces into one solid.
- h.print_fit(parts, others=None, clearance=0.15) -> per pair: OVERLAP, TOO TIGHT or the \
min gap and where, for parts that mate or nest.
- h.print_split(part, axis="Z", at=None, pins=2) -> (A, B): cuts a part bigger than the bed, \
with matching pin holes.
- h.print_export(parts, name, fmt="3mf"|"stl", rotate={part: (x, y, z) degrees}) -> paths: \
packs the parts onto plates inside the files (one 3MF per plate) and leaves the scene as it \
is, so the assembly stays assembled; rotate sets a print orientation; refuses parts that \
aren't watertight unless force=True. h.print_arrange(parts) / (parts, restore=True): preview \
the plate layout in the scene and put it back; \
h.print_slice(paths, quality="0.20mm Standard", filament="Generic PLA", settings=None) -> \
G-code paths, print time and grams, when a slicer is found (context block); settings= changes \
slicer keys (temperatures, speeds, flow).

What matters here: exact sizes (from the brief, datasheets or measurements; named variables \
at the top of a script make a change one edit), real geometry that survives a 0.4 mm nozzle \
(materials and textures don't print), one watertight object per printed part, clearances on \
parts that mate, and an orientation that prints well. The add-on's check after each turn is \
h.print_check. Deliverables are the .3mf (and .gcode with a slicer) plus what the user does \
after printing.""",

    "bim": """MODE: BONSAI (BIM). The goal: buildings and their parts as an IFC model with \
the Bonsai add-on that a professional can open, measure, schedule and draw from: real \
elements on storeys, real build-ups, quantities, dimensioned drawings. h.guide("bonsai") has \
the helper signatures, a workflow that suits big projects and usual building dimensions.

Helpers for this mode (metres), shortcuts over Bonsai's own generators: h.bim_project, \
h.bim_type, h.bim_material, h.bim_walls, h.bim_slab (holes= for stairwells), h.bim_door, h.bim_window, h.bim_member, \
h.bim_opening, h.bim_space, h.bim_element (a modelled mesh becomes an IFC element), h.bim_report, \
h.bim_drawing, h.bim_dimension, h.bim_save. Bonsai's operators (bpy.ops.bim.*) and \
ifcopenshell.api on bonsai.tool.Ifc.get() are there for anything they don't cover.

What matters here: only IFC elements are part of the model (plain meshes aren't exported), \
dimensions follow the brief and building practice (state assumptions), and quantities in \
h.bim_report should match the brief. The add-on's check after each turn is h.bim_report. \
Deliverables are the .ifc and the drawings (plans, elevations, sections as SVG).""",
}

PROMPT_NOTE = """# Built-in prompt: how the AI works inside Blender (tools, helpers, workflow).
# Not meant to be changed: edits can break the add-on. Put your preferences in Edit
# Instructions instead; they override this. Reset restores the default, and an unedited
# copy follows add-on updates. {helpers_import} and {addon_dir} are filled in by the add-on.
# Lines starting with "# " are notes and aren't sent.
"""

MODE_NOTE = """# Mode prompt: {mode}. Sent with every message while this mode is on, after the
# built-in prompt. Edit it to change how the AI works in this mode; Reset restores the
# default, and an unedited copy follows add-on updates.
# Lines starting with "# " are notes and aren't sent.
"""

DEFAULT_INSTRUCTIONS = """# Standing instructions for the AI, sent with every message in every mode.
# Edit freely; shorter = fewer tokens. Lines starting with "# " are notes and aren't sent.
# Rules for one kind of work (game assets, 3D printing, BIM) are in that mode's prompt:
# LLM Chat menu > Edit Mode Prompt.

- Don't ask before building: if my request is open, choose sensible defaults, build it, and \
name them in one line.
- Only use a reference I name; don't pick or suggest one yourself (recent files are just for \
finding files I name)."""

# Stamps of earlier default instructions (game rules, now in the Game Assets mode prompt): an
# unedited copy is replaced by the new default.
OLD_INSTRUCTIONS = {"c0aadd5afa8e", "1f2db5f6bfe0"}

MODES = [
    ("game", "Game Assets", "Real-time assets for game engines (.glb)", "GHOST_ENABLED", 0),
    ("print", "3D Printing", "Exact, printable parts: 3MF/STL and G-code for your printer", "MOD_SOLIDIFY", 1),
    ("bim", "Bonsai (BIM)", "Buildings as IFC with Bonsai: elements, quantities, drawings", "HOME", 2),
]

# Claude Code's aliases always point at the newest model of each family, so a new
# release is picked up without updating the add-on. The version it resolved to is
# read from each run and remembered in models.json, just for the labels.
MODELS = [
    ("opus", "Opus", "Most capable (always the latest Opus)"),
    ("sonnet", "Sonnet", "Faster, lighter on usage limits (always the latest Sonnet)"),
    ("haiku", "Haiku", "Fastest, for small edits (always the latest Haiku)"),
    ("api", "Other model", "DeepSeek, OpenRouter, Gemini, Groq, Ollama, LM Studio… with your API key "
                           "(set in the setup box or the add-on preferences)"),
]
CLAUDE_MODELS = [m for m in MODELS if m[0] != "api"]
API_PROVIDERS = [(k, v[0], "") for k, v in api.PROVIDERS.items()]
KEY_ENV = {"deepseek": "DEEPSEEK_API_KEY", "gemini": "GEMINI_API_KEY",
           "openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY", "custom": "OPENAI_API_KEY"}
# What the other models are told when they take over a chat Claude started (or back).
SWITCH_NOTE = ("(Earlier in this chat, answered by another model; you take over from here:)\n"
               "{transcript}\n(End of the earlier part.)\n\n")
_NOT_CHAT = re.compile(r"embed|tts|transcri|whisper|image|imagen|veo|live|audio|aqa|guard|"
                       r"moderation|rerank|vision-preview", re.I)
EFFORTS = [("low", "Low", ""), ("medium", "Medium", ""), ("high", "High", "")]



# ---------------------------------------------------------------- state

class _State:
    messages = []        # dicts: {"role": user|assistant|tool|error|info, "text": str, "open": bool}
    session_id = None    # Claude Code session, reused so the chat keeps memory
    chat_id = None       # this chat in history.json
    api_history = []     # the other models' API messages for this chat (no system prompt)
    owner = ""           # who answered last: "claude" or "api"
    ends = {}            # "claude"/"api" -> message index where its last turn ended
    status = ""          # an API model's progress line (retrying, switching model)
    api_models = {}      # provider -> model ids from its /models list
    models_status = ""
    proc = None
    run = 0              # bumped per request / new chat, so stale events are ignored
    events = queue.Queue()
    busy = False
    started = 0.0
    streaming = False    # last assistant message is still receiving deltas
    icons = None
    attachments = []     # local file paths to send with the next message
    last_enter = 0.0     # when Enter was last pressed (so click-away doesn't send)
    watching = False     # the key watcher modal operator is running
    setup_status = ""    # last line from an install / sign-in started in the panel
    claude = None        # cached CLI path (looking it up globs the disk)
    claude_checked = 0.0
    auth = None          # (signed_in, account) once checked, None while unknown
    login = None         # deps.Login while a sign-in runs


S = _State


def _redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "UI":
                        region.tag_redraw()


def _add(role, text):
    S.messages.append({"role": role, "text": text})
    S.streaming = False


def _icon():
    return S.icons["claude"].icon_id if S.icons else 0


# ---------------------------------------------------------------- files

def _data_dir():
    return bpy.utils.user_resource("CONFIG", path="claude_chat", create=True)


def _workspace():
    # A fixed working directory: sessions can always be resumed, and no stray
    # CLAUDE.md from the .blend's folder gets loaded into the context.
    path = os.path.join(_data_dir(), "workspace")
    os.makedirs(path, exist_ok=True)
    return path


PROMPT_FILES = {"instructions": "instructions.md", "base": "base_prompt.md",
                **{f"mode_{m[0]}": f"mode_{m[0]}.md" for m in MODES},
                "guide_print": os.path.join("guides", "print.md"),
                "guide_bonsai": os.path.join("guides", "bonsai.md")}
MODE_GUIDE = {"print": "guide_print", "bim": "guide_bonsai"}


def _stamp(text):
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def _builtin(which):
    """The add-on's own text for a followed prompt (base or a mode), else None."""
    if which == "base":
        return BASE_PROMPT
    if which.startswith("mode_"):
        return MODE_PROMPTS[which[5:]]
    return None


def _prompt_default(which):
    if which.startswith("guide_"):
        with open(os.path.join(ADDON_DIR, "guides", which[6:] + ".md"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        body = "\n".join(l for l in lines if not l.startswith("# ")).strip()
        notes = [l for l in lines if l.startswith("# ")]
        return "\n".join(notes + [f"# default: {_stamp(body)}", "", body]) + "\n"
    text = _builtin(which)
    if text is None:
        return DEFAULT_INSTRUCTIONS + "\n"
    note = PROMPT_NOTE if which == "base" else MODE_NOTE.format(
        mode=next(m[1] for m in MODES if m[0] == which[5:]))
    return f"{note}# default: {_stamp(text)}\n\n{text}\n"


def _prompt_path(which):
    path = os.path.join(_data_dir(), PROMPT_FILES[which])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(_prompt_default(which))
    return path


def _prompt_text(which):
    """The Text datablock editing that prompt file, if it's open in Blender."""
    path = os.path.normpath(_prompt_path(which))
    for t in bpy.data.texts:
        if t.filepath and os.path.normpath(bpy.path.abspath(t.filepath)) == path:
            return t
    return None


def _save_text(t):
    """Save a file-backed Text through Blender, so it doesn't flag an outside change."""
    if not t.is_dirty:
        return
    try:
        with bpy.context.temp_override(edit_text=t):
            bpy.ops.text.save()
    except Exception:  # noqa: BLE001
        with open(bpy.path.abspath(t.filepath), "w", encoding="utf-8") as f:
            f.write(t.as_string())


def _write_prompt(which, text):
    with open(_prompt_path(which), "w", encoding="utf-8") as f:
        f.write(text)
    t = _prompt_text(which)
    if t:
        t.clear()
        t.write(text)
        _save_text(t)


def _load_prompt(which):
    t = _prompt_text(which)
    if t:
        _save_text(t)
    with open(_prompt_path(which), encoding="utf-8") as f:
        lines = f.read().splitlines()
    # Lines starting with "# " are notes to the user, not sent to Claude.
    body = "\n".join(l for l in lines if not l.startswith("# ")).strip()
    builtin = _builtin(which)
    if builtin is None:
        if _stamp(body) in OLD_INSTRUCTIONS:  # an unedited copy of the old game-only default
            _write_prompt(which, _prompt_default(which))
            return _load_prompt(which)
        return body
    if f"# default: {_stamp(body)}" in lines and _stamp(body) != _stamp(builtin):
        # An unedited copy of an older add-on's default: follow the update.
        _write_prompt(which, _prompt_default(which))
        body = builtin.strip()
    return body.replace("{helpers_import}", HELPERS_IMPORT).replace("{addon_dir}", ADDON_DIR)


def _system_prompt():
    p = _prefs()
    return "\n\n".join(x for x in (_load_prompt("base"), _load_prompt(f"mode_{p.mode}"),
                                    _load_prompt("instructions")) if x)


def _history_path():
    return os.path.join(_data_dir(), "history.json")


def _models_path():
    return os.path.join(_data_dir(), "models.json")


def _resolved_models():
    try:
        with open(_models_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _remember_model(alias, model_id):
    known = _resolved_models()
    if not model_id or known.get(alias) == model_id:
        return
    known[alias] = model_id
    try:
        with open(_models_path(), "w") as f:
            json.dump(known, f)
    except OSError:
        pass


def _model_name(alias):
    """'Opus 5.5' from the id the alias last resolved to, else just 'Opus'."""
    base = next((n for ident, n, _ in MODELS if ident == alias), alias)
    m = re.match(r"claude-[a-z]+-([\d-]+?)(?:-\d{8})?$", _resolved_models().get(alias, ""))
    return f"{base} {m.group(1).replace('-', '.')}" if m else base


def _read_history():
    try:
        with open(_history_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _title(messages=None):
    first = next((m["text"] for m in (messages or S.messages) if m["role"] == "user"), "")
    first = first.splitlines()[0] if first else ""
    return first[:60] + ("…" if len(first) > 60 else "")


def _api_without_images(msgs):
    """Saved chats keep the text, not the screenshots (they would make the file huge)."""
    out = []
    for m in msgs:
        if isinstance(m.get("content"), list):
            m = dict(m, content="\n".join(p.get("text", "") for p in m["content"]
                                          if p.get("type") == "text") + " [image]")
        out.append(m)
    return out


def _save_history():
    S.chat_id = S.chat_id or S.session_id
    if not S.chat_id or not S.messages:
        return
    hist = [h for h in _read_history() if h.get("id") != S.chat_id]
    hist.insert(0, {"id": S.chat_id, "title": _title(), "time": time.time(),
                    "messages": S.messages, "session": S.session_id,
                    "api": _api_without_images(S.api_history), "owner": S.owner,
                    "ends": S.ends})
    try:
        with open(_history_path(), "w") as f:
            json.dump(hist[:30], f)
    except OSError as e:
        print("LLM Chat: could not save history:", e)


# ---------------------------------------------------------------- helpers

def _addon_prefs():
    addon = bpy.context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


def _prefs():
    # Enabled without being stored in preferences (e.g. from a script): use defaults.
    return _addon_prefs() or type("Defaults", (), {
        k: v.keywords.get("default", "") for k, v in CLAUDECHAT_Prefs.__annotations__.items()})


def _find_claude(refresh=False):
    """The CLI path, looked up at most every few seconds (panels redraw often)."""
    if refresh or time.time() - S.claude_checked > 5:
        p = _prefs().claude_path.strip()
        S.claude = (p if os.path.isfile(p) else None) if p else deps.find_claude()
        S.claude_checked = time.time()
    return S.claude


def _check_auth():
    """Ask the CLI whether it's signed in, off the UI thread."""
    claude = _find_claude(refresh=True)
    if not claude:
        S.auth = None
        return

    def work():
        result = deps.auth_status(claude)

        def apply():
            S.auth = result
            _redraw()
        bpy.app.timers.register(apply, first_interval=0.0)
    threading.Thread(target=work, daemon=True).start()


def _builtin_tools():
    """Claude Code's own tools to switch on, from the add-on preferences.
    They are also passed as allowed, so headless runs don't deny them."""
    p = _prefs()
    tools = []
    if p.use_web:
        tools += ["WebSearch", "WebFetch"]
    if p.use_files:
        tools += ["Read", "Glob", "Grep"]
    if p.use_shell:
        tools += ["Bash", "Write", "Edit"]
    tools += [t.strip() for t in p.extra_tools.split(",") if t.strip()]
    return ",".join(dict.fromkeys(tools))


def _recent_files(limit=8):
    """Blender's File > Open Recent list, so Claude can locate .blend files the user names."""
    path = os.path.join(bpy.utils.user_resource("CONFIG"), "recent-files.txt")
    try:
        with open(path) as f:
            files = [l.strip() for l in f if l.strip() and os.path.isfile(l.strip())]
    except OSError:
        return []
    return [p for p in files if p != bpy.data.filepath][:limit]


# Add-ons whose operators are worth driving from a script, by module suffix.
USEFUL_ADDONS = {
    "extra_mesh_objects": "Extra Mesh Objects", "looptools": "LoopTools", "tissue": "Tissue",
    "rigify": "Rigify", "rigodotify": "Rigodotify", "f2": "F2",
    "material-combiner-addon-master": "Material Combiner", "paws_bakery": "PAWS Bakery",
    "vertex_group_assistant": "Vertex Group Assistant",
}


def _useful_addons():
    keys = bpy.context.preferences.addons.keys()
    return [name for mod, name in USEFUL_ADDONS.items()
            if any(k == mod or k.endswith("." + mod) for k in keys)]


def _blender_context():
    ctx = bpy.context
    scene = ctx.scene
    obj = ctx.active_object
    sel = [o.name for o in ctx.selected_objects]
    objs = list(scene.objects)
    listing = ", ".join(f"{o.name} ({o.type.lower()})" for o in objs[:30])
    if len(objs) > 30:
        listing += f", … +{len(objs) - 30} more"
    lines = [
        f"blender: {bpy.app.version_string}",
        f"file: {bpy.data.filepath or '(unsaved)'}",
        f"mode: {ctx.mode}, frame {scene.frame_current}",
        f"active: {obj.name + ' (' + obj.type.lower() + ')' if obj else 'none'}",
        f"selected: {', '.join(sel[:20]) + (' …' if len(sel) > 20 else '') if sel else 'none'}",
        f"scene objects ({len(objs)}): {listing or 'none'}",
        f"recent files: {', '.join(_recent_files()) or 'none'}",
        f"add-ons: {', '.join(_useful_addons()) or 'none'}",
    ]
    p = _prefs()
    lines.insert(0, f"mode: {next(m[1] for m in MODES if m[0] == p.mode)}")
    if p.mode == "game" and p.export_dir:
        lines.append(f"export folder: {bpy.path.abspath(p.export_dir)}")
    elif p.mode == "print":
        name, bx, by, bz, nozzle = printing.printer()
        u = scene.unit_settings
        lines.append(f"printer: {name}, bed {bx}x{by}x{bz} mm, nozzle {nozzle} mm")
        lines.append(f"units: {'1 unit = 1 mm' if abs(u.scale_length - 0.001) < 1e-9 and u.system == 'METRIC' else 'not mm yet (h.print_setup())'}")
        lines.append(f"slicer: {printing.find_slicer() or 'none found (deliver .3mf)'}")
        lines.append(f"print folder: {printing._out_dir()}")
    elif p.mode == "bim":
        lines.append(_bim_context())
    return "[Blender context]\n" + "\n".join(lines) + "\n[/Blender context]\n\n"


def _bonsai_name():
    import addon_utils
    return next((m.__name__ for m in addon_utils.modules() if m.__name__.endswith(".bonsai")
                 or m.__name__ == "bonsai"), None)


def _bim_context():
    if not any(k.endswith("bonsai") for k in bpy.context.preferences.addons.keys()):
        return ("bonsai: " + ("installed, not loaded yet (h.bim_project loads it)" if _bonsai_name()
                              else "NOT INSTALLED: tell the user to install Bonsai from "
                                   "Edit > Preferences > Get Extensions"))
    try:
        import bonsai.tool as tool
        f = tool.Ifc.get()
    except Exception:  # noqa: BLE001
        f = None
    if not f:
        return "bonsai: loaded, no IFC project open"
    storeys = ", ".join(f"{s.Name} @ {s.Elevation or 0:.2f}" for s in
                        sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0))
    return (f"ifc: {tool.Ifc.get_path() or '(not saved)'}, {f.schema}, "
            f"{len(f.by_type('IfcElement'))} elements, storeys: {storeys or 'none'}")


BONSAI_URL = "https://extensions.blender.org/add-ons/bonsai/"
ORCA_URL = "https://github.com/OrcaSlicer/OrcaSlicer/releases/latest"
_SLICER = [0.0, None]


def _slicer():
    """printing.find_slicer, looked up at most every few seconds (panels redraw often)."""
    if time.time() - _SLICER[0] > 5:
        _SLICER[:] = [time.time(), printing.find_slicer()]
    return _SLICER[1]


def _on_mode(p):
    """Bonsai mode loads Bonsai (it takes a while the first time), so the first message
    doesn't wait for it."""
    if p.mode != "bim" or any(k.endswith("bonsai") for k in bpy.context.preferences.addons.keys()):
        return _redraw()
    name = _bonsai_name()
    if not name:
        S.messages.append({"role": "error", "text": "Bonsai isn't installed: Edit > Preferences "
                           "> Get Extensions, search Bonsai, Install (or " + BONSAI_URL + ")."})
        return _redraw()
    S.status = "Loading Bonsai…"
    _redraw()

    def load():
        import addon_utils
        try:
            addon_utils.enable(name, default_set=True)
            S.status = ""
            S.messages.append({"role": "info", "text": "Bonsai loaded"})
        except Exception as e:  # noqa: BLE001
            S.status = ""
            S.messages.append({"role": "error", "text": f"Couldn't load Bonsai: {e}"})
        _redraw()
    bpy.app.timers.register(load, first_interval=0.2)


def _mcp_config_path():
    """Config for the built-in tool server, run with Blender's own Python. The file
    holds this session's bridge token, so only the user can read it."""
    cfg = {"mcpServers": {"blender": {
        "command": sys.executable,
        "args": ["-I", os.path.join(ADDON_DIR, "mcp_server.py")],
        "env": {"CLAUDE_CHAT_PORT": str(bridge.start()), "CLAUDE_CHAT_TOKEN": bridge.TOKEN},
    }}}
    path = os.path.join(_data_dir(), "mcp.json")
    if os.path.exists(path):
        os.remove(path)  # recreate, so the permissions below always apply
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(cfg, f)
    return path


def _link_path():
    return os.path.join(_data_dir(), "link.json")


def _write_link():
    """Lets the Godot plugin's Claude use this Blender (when the user ticks that option
    there): how to start the tool server and this session's bridge port and token.
    Only the user can read it; removed when the add-on is disabled."""
    path = _link_path()
    data = {"python": sys.executable, "server": os.path.join(ADDON_DIR, "mcp_server.py"),
            "port": bridge.start(), "token": bridge.TOKEN, "pid": os.getpid(),
            "blender": bpy.app.version_string}
    try:
        if os.path.exists(path):
            os.remove(path)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
            json.dump(data, f)
    except OSError:
        pass
    return None  # timer: run once


def _remove_link():
    try:
        with open(_link_path()) as f:
            mine = json.load(f).get("pid") == os.getpid()
        if mine:
            os.remove(_link_path())
    except (OSError, ValueError):
        pass


# ---------------------------------------------------------------- worker

def _reader(proc, run, prompt):
    """Background thread: send the prompt, then forward the CLI's stream-json lines
    to the main thread."""
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError:
        pass  # the CLI failed to start; its exit code and stderr say why
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            S.events.put((run, "json", json.loads(line)))
        except json.JSONDecodeError:
            pass
    err = proc.stderr.read()
    S.events.put((run, "exit", (proc.wait(), err)))


def _short_tool(name, inp):
    name = name.replace("mcp__blender__", "")
    detail = ""
    if isinstance(inp, dict):
        if "code" in inp:
            lines = [l.strip() for l in inp["code"].splitlines() if l.strip()]
            # Prefer Claude's own comment describing the script, else the first real line.
            detail = next((l.lstrip("# ") for l in lines if l.startswith("#")), None) or next(
                (l for l in lines if not l.startswith(("import ", "from "))), "")
        else:
            args = [f"{k}={v}" for k, v in inp.items() if k != "user_prompt" and v not in ("", None)]
            detail = ", ".join(args)
    detail = detail[:70]
    return f"{name}: {detail}" if detail else name


def _handle(ev):
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        S.session_id = ev.get("session_id")
        _remember_model(_prefs().model, ev.get("model"))
        blender = next((m for m in ev.get("mcp_servers", []) if m.get("name") == "blender"), None)
        if not blender or blender.get("status") != "connected":
            # Without the Blender tools Claude can't do anything useful: stop the run.
            _stop()
            status = blender.get("status") if blender else "missing"
            _add("error", f"Claude couldn't load its Blender tools ({status}). Try again; "
                          "if it keeps happening, restart Blender.")
    elif t == "stream_event":
        e = ev["event"]
        if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta":
            if not (S.streaming and S.messages and S.messages[-1]["role"] == "assistant"):
                _add("assistant", "")
                S.streaming = True
            S.messages[-1]["text"] += e["delta"]["text"]
        elif e.get("type") == "content_block_stop":
            S.streaming = False
    elif t == "assistant":
        # Full message (after streaming): only tool calls need rendering here.
        for block in ev["message"].get("content", []):
            if block.get("type") == "tool_use":
                _add("tool", _short_tool(block["name"], block.get("input")))
    elif t == "result":
        S.session_id = ev.get("session_id", S.session_id)
        if ev.get("is_error"):
            msg = str(ev.get("result") or ev.get("subtype"))
            if re.search(r"log ?in|api key|auth", msg, re.I):
                S.auth = (False, "")
                msg += "\nSign in again from the setup box below."
            _add("error", msg)
        else:
            _audit_note()
        _add("info", _duration(time.time() - S.started))
        _save_history()


def _audit_note():
    """After a turn, check what was added ourselves, so a tidy summary can't hide a
    floating part or an unweighted vertex."""
    if not any(o.name not in bridge.TURN_START for o in bpy.data.objects):
        return
    try:
        problems = bridge.scene_audit().get("problems", [])
    except Exception:  # noqa: BLE001  (a check failing must not break the chat)
        return
    if problems:
        _add("error", "The add-on's checks find problems (ask Claude to fix them, or "
                      "ignore any that are intended):\n" +
             "\n".join("- " + p for p in problems[:8]) +
             (f"\n… and {len(problems) - 8} more" if len(problems) > 8 else ""))


def _pump():
    """Main-thread timer: apply queued events to the chat."""
    while True:
        try:
            run, kind, payload = S.events.get_nowait()
        except queue.Empty:
            break
        if run != S.run:
            continue  # from a request that was stopped or a chat that was cleared
        if kind == "json":
            _handle(payload)
        elif kind == "api":
            _handle_api(*payload)
        elif kind == "exit":
            code, err = payload
            if code not in (0, None) and not (S.messages and S.messages[-1]["role"] == "info"):
                _add("error", (err.strip() or f"claude exited with code {code}")[-500:])
            S.busy, S.proc = False, None
    _redraw()  # also keeps the elapsed-time counter ticking
    return 0.1 if S.busy else None


def _is_api(p=None):
    return (p or _prefs()).model == "api"


def _missed(who):
    """What this chat said since `who` last answered, when the other side answered in
    between: Claude and the other models can take turns in one chat."""
    at = len(S.messages) - 1  # the user's message just added
    if S.owner and S.owner != who:
        S.ends[S.owner] = at
    start = S.ends.get(who)
    if start is None:
        has_own = S.session_id if who == "claude" else S.api_history
        start = at if has_own else 0
    S.owner = who
    lines = [f"{'User' if m['role'] == 'user' else 'AI'}: {m['text'].strip()}"
             for m in S.messages[start:at] if m["role"] in ("user", "assistant") and m["text"].strip()]
    return SWITCH_NOTE.format(transcript="\n\n".join(lines)[-20000:]) if lines else ""


def send(text):
    text = text.strip()
    if not text or S.busy:
        return
    if _is_api():
        return _send_api(text)
    claude = _find_claude(refresh=True)
    _add("user", text)
    if not claude:
        _add("error", "Claude Code isn't installed yet: use Install Claude Code below.")
        return _redraw()

    try:  # one undo step to jump back to before Claude's edits
        bpy.ops.ed.undo_push(message="Before the AI")
    except Exception:  # noqa: BLE001
        pass

    bridge.TURN_START = {o.name for o in bpy.data.objects}  # what scene_audit compares to
    bridge.TURN_COLLECTIONS = {c.name for c in bpy.data.collections}
    bridge.MODE = _prefs().mode
    p = _prefs()
    prompt = _missed("claude") + (_blender_context() if p.include_context else "") + text
    S.chat_id = S.chat_id or uuid.uuid4().hex
    if S.attachments:
        if not p.use_files:
            _add("error", "Attached files need the Files tool: turn on \"Files\" in the "
                          "add-on preferences.")
            return _redraw()
        prompt += "\n\n[Attached files]\n" + "\n".join(S.attachments)
        S.attachments = []
    # The prompt goes in on stdin and the system prompt in a file, never on the
    # command line: on Windows a claude.cmd shim runs through cmd.exe, which would
    # treat characters like & and " in the text (or in an object name from a
    # downloaded .blend) as commands, and caps the whole line at 8191 characters.
    system_prompt = os.path.join(_data_dir(), "system_prompt.md")
    with open(system_prompt, "w", encoding="utf-8") as f:
        f.write(_system_prompt())
    cmd = [
        claude, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--model", p.model, "--effort", p.effort,
        "--mcp-config", _mcp_config_path(), "--strict-mcp-config",
        "--tools", _builtin_tools(),
        "--allowedTools", ",".join(["mcp__blender"] + _builtin_tools().split(",")).strip(","),
        "--disable-slash-commands",
        "--append-system-prompt-file", system_prompt,
    ]
    if S.session_id:
        cmd += ["--resume", S.session_id]
    env = dict(os.environ, DISABLE_TELEMETRY="1")
    try:
        S.proc = subprocess.Popen(deps.command_for(*cmd), cwd=_workspace(), env=env,
                                  text=True, encoding="utf-8", bufsize=1,
                                  stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  **deps.popen_kwargs())
    except OSError as e:
        _add("error", f"Failed to start Claude: {e}")
        return _redraw()
    S.run += 1
    S.busy, S.started = True, time.time()
    threading.Thread(target=_reader, args=(S.proc, S.run, prompt), daemon=True).start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1)
    _redraw()


def _stop():
    S.run += 1  # an API model's thread sees this and stops after its current step
    if S.proc:
        S.proc.terminate()
    S.busy, S.proc, S.status = False, None, ""


# ---------------------------------------------------------------- other models (api.py)

def _api_key(p=None, prov=None):
    p = p or _prefs()
    prov = prov or p.api_provider
    return (getattr(p, f"api_key_{prov}", "") or "").strip() or \
        os.environ.get(KEY_ENV.get(prov, ""), "").strip()


def _api_base(p=None, prov=None):
    p = p or _prefs()
    prov = prov or p.api_provider
    if prov == "custom":
        return p.api_base_url.strip().rstrip("/")
    return api.PROVIDERS[prov][1]


def _set_up(p, prov):
    """A provider whose models the menu lists: with a key, or (local servers and custom, which
    need none) the chosen one or one listed before."""
    base = _api_base(p, prov)
    if not base or api.check_url(base):
        return False
    if api.PROVIDERS[prov][4]:
        return bool(_api_key(p, prov))
    return prov == p.api_provider or bool(S.api_models.get(prov))


def _api_model(p=None):
    p = p or _prefs()
    return p.api_model.strip() or api.PROVIDERS[p.api_provider][2]


def _api_config():
    """Connection settings, or a message saying what's missing."""
    p = _prefs()
    label, _, _, key_url, needs_key = api.PROVIDERS[p.api_provider]
    base = _api_base(p)
    if not base:
        return "Set the API address (base URL) in the setup box."
    bad = api.check_url(base)
    if bad:
        return bad
    key = _api_key(p)
    if needs_key and not key:
        return f"Add your {label} API key in the setup box (get one at {key_url})."
    if not _api_model(p):
        return "Pick a model: open the model menu next to the input and refresh the list."
    # Fallbacks only stand in for the provider's default, never for a model the user picked.
    fallbacks = [] if p.api_model.strip() else list(api.FALLBACKS.get(p.api_provider, []))
    return {"base": base, "key": key, "model": _api_model(p), "fallbacks": fallbacks,
            "max_steps": p.step_limit}


TEXT_EXTS = (".txt", ".md", ".json", ".csv", ".py", ".gd", ".tscn", ".cfg")


def _user_content(text, paths):
    """The user message: text plus attachments (images inline, text files pasted in), as
    the other models have no file tool."""
    parts = [{"type": "text", "text": text}]
    for path in paths:
        name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in clipboard.IMAGE_EXTS:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode()
                mime = {".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif"}.get(ext, "png")
                parts.append({"type": "text", "text": f"Attached image: {name}"})
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:image/{mime};base64,{data}"}})
            elif ext in TEXT_EXTS:
                with open(path, encoding="utf-8", errors="replace") as f:
                    body = f.read(30000)
                parts.append({"type": "text", "text": f"Attached file {name}:\n{body}"})
            else:
                parts.append({"type": "text", "text": f"Attached file path: {path}"})
        except OSError as e:
            parts.append({"type": "text", "text": f"(Couldn't read {name}: {e})"})
    return parts if len(parts) > 1 else text


def _send_api(text):
    cfg = _api_config()
    _add("user", text)
    if not isinstance(cfg, dict):
        _add("error", cfg)
        return _redraw()
    try:  # one undo step to jump back to before the AI's edits
        bpy.ops.ed.undo_push(message="Before AI")
    except Exception:  # noqa: BLE001
        pass
    bridge.start()  # the main-thread timer that runs the tools
    bridge.TURN_START = {o.name for o in bpy.data.objects}
    bridge.TURN_COLLECTIONS = {c.name for c in bpy.data.collections}
    bridge.MODE = _prefs().mode
    p = _prefs()
    # The same prompts Claude gets, addressed to this model (it has only the Blender tools).
    system = _system_prompt().replace(
        "You are Claude,", "You are an AI assistant,", 1)
    content = _user_content(_missed("api") + (_blender_context() if p.include_context else "")
                            + text, S.attachments)
    S.attachments = []
    S.api_history = S.api_history + [{"role": "user", "content": content}]
    S.chat_id = S.chat_id or uuid.uuid4().hex
    S.run += 1
    run = S.run
    S.busy, S.started, S.status = True, time.time(), ""

    def emit(kind, value):
        S.events.put((run, "api", (kind, value)))

    msgs = [{"role": "system", "content": system}] + list(S.api_history)
    threading.Thread(target=api.run, args=(cfg, msgs, emit, lambda: run != S.run),
                     daemon=True).start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1)
    _redraw()


def _handle_api(kind, value):
    if kind == "text":
        _add("assistant", value)
    elif kind == "tool":
        name, args = value
        _add("tool", _short_tool(name, args or {}))
        S.status = ""
    elif kind == "status":
        S.status = value
    elif kind == "error":
        _add("error", value)
    elif kind == "history":
        S.api_history = value[1:]  # without the system prompt, rebuilt every message
    elif kind == "done":
        S.busy, S.status = False, ""
        _add("info", _duration(time.time() - S.started))
        _save_history()


# ---------------------------------------------------------------- drawing

_MD = [(re.compile(r"\*\*(.+?)\*\*"), r"\1"), (re.compile(r"`([^`]+)`"), r"\1"),
       (re.compile(r"^#+\s*", re.M), ""), (re.compile(r"^```\w*$", re.M), "")]
_BULLET = re.compile(r"^(\s*)([-*]|\d+[.)])\s+")


def _plain(text):
    for rx, rep in _MD:
        text = rx.sub(rep, text)
    return text


def _wrap(layout, text, width):
    col = layout.column(align=True)
    col.scale_y = 0.8
    for para in _plain(text).rstrip().split("\n"):
        indent = ""
        m = _BULLET.match(para)
        if m:
            marker = "•" if m.group(2) in "-*" else m.group(2)
            para = f"{m.group(1)}{marker} {para[m.end():]}"
            indent = " " * (len(m.group(1)) + len(marker) + 1)  # hanging indent
        for line in textwrap.wrap(para, width, subsequent_indent=indent) or [""]:
            col.label(text=line)
    return col


def _copy_button(layout, index):
    """Labels can't be selected in Blender's UI, so every message gets a copy button."""
    row = layout.row()
    row.alignment = "RIGHT"
    row.scale_y = 0.7
    row.operator("claude_chat.copy", text="", icon="COPYDOWN", emboss=False).index = index


def _toggle(layout, index, is_open, text):
    row = layout.row()
    row.alignment = "LEFT"
    op = row.operator("claude_chat.toggle", text=text, emboss=False,
                         icon="DISCLOSURE_TRI_DOWN" if is_open else "DISCLOSURE_TRI_RIGHT")
    op.index = index


def _draw_tools(layout, items, width, live):
    """A folded group of consecutive tool calls, like 'Ran 4 tools'."""
    first, m0 = items[0]
    col = layout.column(align=True)
    col.active = False
    n = len(items)
    label = f"Running {items[-1][1]['text'].split(':')[0]}…" if live else \
        f"Ran {n} tool{'s' if n > 1 else ''}"
    _toggle(col, first, m0.get("open"), label)
    if m0.get("open"):
        for _, m in items:
            _wrap(col, "• " + m["text"], width)


def _draw_turn(layout, turn, width, latest):
    body = turn
    if turn[0][1]["role"] == "user":
        i, m = turn[0]
        body = turn[1:]
        is_open = latest or m.get("open")
        box = layout.box()
        if latest:
            _wrap(box, m["text"], width - 3)
            _copy_button(box, i)
        else:
            _toggle(box, i, is_open, (m["text"].splitlines() or [""])[0][: width - 4])
            if not is_open:
                return
            if len(m["text"]) > width - 4 or "\n" in m["text"]:
                _wrap(box, m["text"], width - 3)
            _copy_button(box, i)
    k = 0
    while k < len(body):
        i, m = body[k]
        role = m["role"]
        if role == "tool":
            group = [body[k]]
            while k + 1 < len(body) and body[k + 1][1]["role"] == "tool":
                k += 1
                group.append(body[k])
            _draw_tools(layout, group, width, live=latest and S.busy and k == len(body) - 1)
        elif role == "assistant":
            _wrap(layout.column(), m["text"] or "…", width)
            if not (S.busy and S.streaming and k == len(body) - 1):
                _copy_button(layout, i)
        elif role == "error":
            box = layout.box()
            box.alert = True
            box.label(text="Error", icon="ERROR")
            _wrap(box, m["text"], width - 3)
        elif role == "info":
            row = layout.row()
            row.alignment = "RIGHT"
            row.active = False
            n_tools = sum(1 for _, x in body if x["role"] == "tool")
            row.label(text=m["text"] + (f" · {n_tools} tools" if n_tools else ""))
        k += 1


def _model_label(p):
    if _is_api(p):
        return f"{api.PROVIDERS[p.api_provider][0].split(' (')[0]} · {_api_model(p) or '?'}"
    return f"{_model_name(p.model)} {p.effort.title()}"


def _draw_api_setup(layout, context):
    """Provider, key and model for the other models; in the chat until they're usable,
    and in the Setup panel and preferences."""
    p = _prefs()
    col = layout.column()
    col.label(text="Other models (API key)", icon="WORLD")
    col.prop(p, "api_provider", text="")
    if p.api_provider == "custom":
        col.prop(p, "api_base_url", text="URL")
    label, _, default, key_url, needs_key = api.PROVIDERS[p.api_provider]
    if needs_key or p.api_provider == "custom":
        col.prop(p, f"api_key_{p.api_provider}", text="Key")
    if key_url:
        col.operator("claude_chat.open_url", text="Get an API key" if needs_key else "Download",
                     icon="URL").url = key_url
    row = col.row(align=True)
    try:
        row.prop(p, "api_model", text="", placeholder=f"Model ({default or 'pick one'})")
    except TypeError:  # Blender without placeholder support
        row.prop(p, "api_model", text="Model")
    row.operator("claude_chat.fetch_models", text="", icon="FILE_REFRESH")
    if S.models_status:
        sub = col.row()
        sub.active = False
        sub.label(text=S.models_status)
    msg = _api_config()
    if not isinstance(msg, dict):
        for line in textwrap.wrap(msg, 40):
            col.label(text=line)
    if not api.is_local(_api_base(p) or "https://x"):
        note = col.column(align=True)
        note.active = False
        for line in textwrap.wrap(api.PRIVACY, 40):
            note.label(text=line)


def _draw_setup(layout, context, extras=False):
    """Install / sign-in steps. Shown in the chat until Claude is ready, and in
    the Setup panel. Returns True when Claude is ready to chat."""
    claude = _find_claude()
    ready = bool(claude and S.auth and S.auth[0])
    col = layout.column()
    if claude:
        row = col.row()
        row.label(text="Claude Code", icon="CHECKMARK")
        sub = row.row()
        sub.active = False
        sub.label(text="installed")
        row = col.row()
        if S.auth is None:
            row.label(text="Checking sign-in…", icon="TIME")
        elif S.auth[0]:
            row.label(text="Signed in", icon="CHECKMARK")
            sub = row.row()
            sub.active = False
            sub.label(text=S.auth[1][:24])
        else:
            row.label(text="Not signed in", icon="ERROR")
        row.operator("claude_chat.check_auth", text="", icon="FILE_REFRESH", emboss=False)
    else:
        col.label(text="Claude Code isn't installed", icon="ERROR")

    if S.setup_status:
        box = col.box()
        box.scale_y = 0.8
        for line in textwrap.wrap(S.setup_status, 34):
            box.label(text=line)

    if not deps.online_access() and not claude:
        box = col.box()
        box.label(text="Blender's online access is off", icon="ERROR")
        box.prop(context.preferences.system, "use_online_access", text="Allow Online Access")

    if not claude:
        col.operator("claude_chat.install_claude", icon="IMPORT")
        col.operator("claude_chat.open_url", text="Other ways to install",
                     icon="URL").url = deps.CLAUDE_URL
    elif S.login and S.login.running:
        if S.login.url:
            col.operator("claude_chat.open_url", text="Open sign-in page",
                         icon="URL").url = S.login.url
        if S.login.waiting_for_code:
            row = col.row(align=True)
            try:
                row.prop(context.window_manager, "claude_chat_code", text="",
                         placeholder="Paste the code here")
            except TypeError:  # Blender without placeholder support
                row.prop(context.window_manager, "claude_chat_code", text="")
            row.operator("claude_chat.submit_code", text="", icon="CHECKMARK")
        col.operator("claude_chat.cancel_login", icon="CANCEL")
    elif S.auth is not None and not S.auth[0]:
        col.operator("claude_chat.login", icon="USER")
        sub = col.column()
        sub.active = False
        sub.label(text="Uses your Claude subscription or API account")

    if extras:
        col.separator()
        have = [n for m, n in {**deps.EXTRAS, **deps.BUILTIN_EXTRAS}.items()
                if deps.is_enabled(m)]
        full = len(have) == len(deps.EXTRAS) + len(deps.BUILTIN_EXTRAS)
        col.label(text="Modelling extras", icon="CHECKMARK" if full else "INFO")
        if not full:
            col.operator("claude_chat.install_extras", icon="ADD")
        col.separator()
        col.operator("claude_chat.open_url", text="Credits & licences",
                     icon="HELP").url = pathlib.Path(ADDON_DIR, "CREDITS.md").as_uri()  # file:///C:/… on Windows
    return ready


class CLAUDECHAT_PT_setup(bpy.types.Panel):
    bl_label = "Setup"
    bl_idname = "CLAUDECHAT_PT_setup"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LLM Chat"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        _draw_setup(self.layout, context, extras=True)
        self.layout.separator()
        _draw_api_setup(self.layout.box(), context)


DONATE_URL = "https://revenantwings.itch.io/donation"
MODE_HINTS = {
    "game": ("Describe an asset, e.g.", "\"low-poly wooden crate, ~500 tris\"",
             "The AI sees your selection."),
    "print": ("Describe a part, attach a brief, e.g.", "\"a 1:2 tray for layer L1, 4 rod holes\"",
              "You get checked 3MF and G-code files."),
    "bim": ("Describe a building, e.g.", "\"two-storey house, 120 m2, 3 bedrooms\"",
            "You get an IFC model, quantities, drawings."),
}


def _duration(seconds):
    """42 s, 3 min 05 s, 1 h 12 min"""
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


class CLAUDECHAT_OT_open_url(bpy.types.Operator):
    bl_idname = "claude_chat.open_url"
    bl_label = "Open"
    bl_description = "Open this page in your browser"

    url: bpy.props.StringProperty()

    def execute(self, context):
        bpy.ops.wm.url_open(url=self.url)
        return {"FINISHED"}


def _setup_report(msg):
    S.setup_status = msg
    _redraw()


class CLAUDECHAT_OT_install_claude(bpy.types.Operator):
    bl_idname = "claude_chat.install_claude"
    bl_label = "Install Claude Code"
    bl_description = ("Download and run Anthropic's official Claude Code installer "
                      "(from claude.ai). Takes a few minutes")

    @classmethod
    def poll(cls, context):
        return not S.setup_status.endswith("…")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title="Install Claude Code?",
            message="Downloads Anthropic's official installer from claude.ai and runs it.",
            confirm_text="Install")

    def execute(self, context):
        def report(msg):
            _setup_report(msg)
            if not msg.endswith("…"):
                _check_auth()
        deps.in_thread(deps.install_claude, report)
        _setup_report("Starting…")
        return {"FINISHED"}


class CLAUDECHAT_OT_login(bpy.types.Operator):
    bl_idname = "claude_chat.login"
    bl_label = "Sign in to Claude"
    bl_description = "Sign in in your browser, the same way the VS Code extension does"

    def execute(self, context):
        claude = _find_claude(refresh=True)
        if not claude:
            return {"CANCELLED"}

        def report(msg):
            def apply():
                _setup_report(msg)
                if not (S.login and S.login.running):
                    _check_auth()
            bpy.app.timers.register(apply, first_interval=0.0)
        try:
            S.login = deps.Login(claude, report)
        except OSError as e:
            _setup_report(f"Couldn't start sign-in: {e}")
            return {"CANCELLED"}
        _setup_report("A browser window should open. Sign in there.")
        return {"FINISHED"}


class CLAUDECHAT_OT_submit_code(bpy.types.Operator):
    bl_idname = "claude_chat.submit_code"
    bl_label = "Submit code"
    bl_description = "Send the code from the sign-in page"

    def execute(self, context):
        wm = context.window_manager
        if S.login and wm.claude_chat_code.strip():
            S.login.send_code(wm.claude_chat_code)
            wm.claude_chat_code = ""
            _setup_report("Checking the code…")
        return {"FINISHED"}


class CLAUDECHAT_OT_cancel_login(bpy.types.Operator):
    bl_idname = "claude_chat.cancel_login"
    bl_label = "Cancel sign-in"

    def execute(self, context):
        if S.login:
            S.login.cancel()
        S.login = None
        _setup_report("")
        return {"FINISHED"}


class CLAUDECHAT_OT_check_auth(bpy.types.Operator):
    bl_idname = "claude_chat.check_auth"
    bl_label = "Check again"
    bl_description = "Look for Claude Code and its sign-in again"

    def execute(self, context):
        S.auth = None
        _check_auth()
        return {"FINISHED"}


class CLAUDECHAT_OT_install_extras(bpy.types.Operator):
    bl_idname = "claude_chat.install_extras"
    bl_label = "Install modelling extras"
    bl_description = ("Install Extra Mesh Objects, LoopTools and Tissue from "
                      "extensions.blender.org, and enable Rigify")

    def execute(self, context):
        deps.install_extras(_setup_report)  # Blender ops must run on the main thread
        return {"FINISHED"}


class CLAUDECHAT_PT_panel(bpy.types.Panel):
    bl_label = "LLM Chat"
    bl_idname = "CLAUDECHAT_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LLM Chat"

    def draw_header(self, context):
        self.layout.label(text="", icon_value=_icon())

    def draw(self, context):
        layout = self.layout
        p = _prefs()
        scale = context.preferences.system.ui_scale
        width = max(16, int((context.region.width - 36 * scale) / (6.4 * scale)))

        # Title bar: chat title, history, new chat, more.
        row = layout.row(align=True)
        title = row.row()
        title.active = bool(S.messages)
        title.label(text=_title() or "New chat")
        row.menu("CLAUDECHAT_MT_history", text="", icon="TIME")
        row.operator("claude_chat.new", text="", icon="ADD")
        row.menu("CLAUDECHAT_MT_more", text="", icon="COLLAPSEMENU")
        row.operator("claude_chat.open_url", text="", icon="FUND").url = DONATE_URL
        row = layout.row(align=True)
        row.prop(p, "mode", text="")
        if p.mode == "print":
            row.prop(p, "printer", text="")
            if not _slicer():
                row = layout.row(align=True)
                row.label(text="No slicer: 3MF only", icon="INFO")
                row.operator("claude_chat.open_url", text="Get OrcaSlicer",
                             icon="URL").url = ORCA_URL

        if not S.messages:
            col = layout.column(align=True)
            col.separator(factor=2)
            r = col.row()
            r.alignment = "CENTER"
            r.label(text="LLM Chat", icon_value=_icon())
            col.separator()
            hints = col.column(align=True)
            hints.active = False
            for h in MODE_HINTS[p.mode]:
                r = hints.row()
                r.alignment = "CENTER"
                r.label(text=h)
            col.separator(factor=2)

        # Conversation, split into turns; older turns fold to their first line.
        turns = []
        for i, m in enumerate(S.messages):
            if m["role"] == "user" or not turns:
                turns.append([])
            turns[-1].append((i, m))
        for ti, turn in enumerate(turns):
            _draw_turn(layout, turn, width, latest=ti >= len(turns) - p.expanded_turns)

        if S.busy:
            row = layout.row()
            row.active = False
            row.label(text=f"Working… {_duration(time.time() - S.started)}"
                      + (f"  {S.status}" if S.status else ""), icon_value=_icon())
        elif S.status:
            row = layout.row()
            row.active = False
            row.label(text=S.status, icon="TIME")

        # Input box.
        box = layout.box()
        for i, path in enumerate(S.attachments):
            row = box.row(align=True)
            row.label(text=os.path.basename(path)[:26], icon="IMAGE_DATA")
            row.operator("claude_chat.unattach", text="", icon="X", emboss=False).index = i
        field = box.column()
        field.scale_y = 1.4
        try:
            field.prop(context.scene, "claude_chat_input", text="",
                       placeholder="Ask the AI… (Enter to send)")
        except TypeError:  # Blender without placeholder support
            field.prop(context.scene, "claude_chat_input", text="")
        split = box.split(factor=0.75)
        left = split.row(align=True)
        left.menu("CLAUDECHAT_MT_model", text=_model_label(p))
        left.operator("claude_chat.paste_image", text="", icon="PASTEDOWN")
        left.operator("claude_chat.attach_file", text="", icon="FILE_IMAGE")
        if p.mode == "print":
            left.operator("claude_chat.export_print", text="", icon="EXPORT")
        elif p.mode == "bim":
            left.operator("claude_chat.export_ifc", text="", icon="EXPORT")
        elif p.export_dir:
            left.operator("claude_chat.export_glb", text="", icon="EXPORT")
        right = split.row()
        right.alignment = "RIGHT"
        if S.busy:
            right.operator("claude_chat.stop", text="", icon="CANCEL")
        else:
            right.operator("claude_chat.send", text="", icon="TRIA_UP")

        if _is_api(p):
            if not isinstance(_api_config(), dict):
                _draw_api_setup(layout.box(), context)
        elif not (S.auth and S.auth[0] and _find_claude()):
            _draw_setup(layout.box(), context)


# ---------------------------------------------------------------- menus

class CLAUDECHAT_MT_model(bpy.types.Menu):
    bl_label = "Model"

    def draw(self, context):
        p = _addon_prefs()
        if not p:
            self.layout.label(text="Enable the add-on in Preferences")
            return
        col = self.layout.column()
        for ident, _, _ in CLAUDE_MODELS:
            col.prop_enum(p, "model", ident, text=_model_name(ident))
        # Every provider that is set up, each with its models (picked, default, newest).
        for prov, info in api.PROVIDERS.items():
            if not _set_up(p, prov):
                continue
            col.separator()
            label = info[0].split(" (")[0]
            cur = _api_model(p) if prov == p.api_provider else ""
            ids = list(dict.fromkeys([cur, info[2]] + S.api_models.get(prov, [])[:40]))
            for m in ids:
                if m:
                    on = _is_api(p) and prov == p.api_provider and m == cur
                    op = col.operator("claude_chat.set_model", text=f"{label} · {m}",
                                      icon="CHECKBOX_HLT" if on else "BLANK1")
                    op.model = f"{prov}|{m}"
        col.separator()
        col.operator("claude_chat.set_model", text="Other models: providers and keys…",
                     icon="PREFERENCES").model = ""
        if not _is_api(p):
            col.separator()
            col.label(text="Effort")
            for ident, _, _ in EFFORTS:
                col.prop_enum(p, "effort", ident)


class CLAUDECHAT_MT_history(bpy.types.Menu):
    bl_label = "Recent Chats"

    def draw(self, context):
        hist = _read_history()
        if not hist:
            self.layout.label(text="No saved chats yet")
        for i, h in enumerate(hist[:15]):
            op = self.layout.operator("claude_chat.resume", text=h.get("title") or "Untitled",
                                      icon="CHECKMARK" if h.get("id") == S.chat_id else "NONE")
            op.index = i


class CLAUDECHAT_MT_more(bpy.types.Menu):
    bl_label = "LLM Chat"

    def draw(self, context):
        col = self.layout.column()
        col.operator("claude_chat.edit_instructions", icon="TEXT")
        col.operator("claude_chat.reset_instructions", icon="LOOP_BACK")
        p = _prefs()
        mode = next(m[1] for m in MODES if m[0] == p.mode)
        col.separator()
        col.operator("claude_chat.edit_instructions", text=f"Edit Mode Prompt ({mode})",
                     icon="TEXT").which = f"mode_{p.mode}"
        col.operator("claude_chat.reset_instructions", text="Reset Mode Prompt",
                     icon="LOOP_BACK").which = f"mode_{p.mode}"
        if p.mode in MODE_GUIDE:
            col.operator("claude_chat.edit_instructions", text=f"Edit Guide ({mode})",
                         icon="HELP").which = MODE_GUIDE[p.mode]
            col.operator("claude_chat.reset_instructions", text="Reset Guide",
                         icon="LOOP_BACK").which = MODE_GUIDE[p.mode]
        col.separator()
        col.operator("claude_chat.edit_instructions", text="Edit Built-in Prompt (Advanced)",
                     icon="ERROR").which = "base"
        col.separator()
        col.operator("claude_chat.copy", text="Copy Whole Chat", icon="COPYDOWN").index = -1
        col.operator("claude_chat.transcript", icon="TEXT")
        col.operator("preferences.addon_show", text="Add-on Preferences",
                     icon="PREFERENCES").module = __name__


# ---------------------------------------------------------------- operators

class CLAUDECHAT_OT_send(bpy.types.Operator):
    bl_idname = "claude_chat.send"
    bl_label = "Send"
    bl_description = "Send the message to Claude"

    def execute(self, context):
        text = context.scene.claude_chat_input
        context.scene.claude_chat_input = ""
        send(text)
        return {"FINISHED"}


class CLAUDECHAT_OT_stop(bpy.types.Operator):
    bl_idname = "claude_chat.stop"
    bl_label = "Stop"
    bl_description = "Stop Claude's current reply"

    def execute(self, context):
        _stop()
        _add("info", "stopped")
        _save_history()
        return {"FINISHED"}


class CLAUDECHAT_OT_new(bpy.types.Operator):
    bl_idname = "claude_chat.new"
    bl_label = "New Chat"
    bl_description = "Start a fresh conversation (the current one stays in history)"

    def execute(self, context):
        if S.busy:
            _save_history()
        _stop()
        S.messages = []
        S.session_id = S.chat_id = None
        S.api_history, S.owner, S.ends = [], "", {}
        return {"FINISHED"}


class CLAUDECHAT_OT_resume(bpy.types.Operator):
    bl_idname = "claude_chat.resume"
    bl_label = "Resume Chat"
    bl_description = "Continue this conversation"

    index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return not S.busy

    def execute(self, context):
        hist = _read_history()
        if not 0 <= self.index < len(hist):
            return {"CANCELLED"}
        h = hist[self.index]
        S.messages = copy.deepcopy(h["messages"])
        S.chat_id = h["id"]
        S.session_id = h.get("session", h["id"] if "api" not in h else None)  # older chats: id = session
        S.api_history = copy.deepcopy(h.get("api", []))
        S.owner = h.get("owner", "claude" if S.session_id else "")
        S.ends = h.get("ends", {})
        return {"FINISHED"}


class CLAUDECHAT_OT_set_model(bpy.types.Operator):
    bl_idname = "claude_chat.set_model"
    bl_label = "Use Model"
    bl_description = "Use this model (an empty name: show the provider setup)"

    model: bpy.props.StringProperty()  # "provider|model"

    def execute(self, context):
        p = _addon_prefs()
        p.model = "api"
        if self.model:
            prov, _, m = self.model.rpartition("|")
            if prov in api.PROVIDERS:
                p.api_provider = prov
            p.api_model = m if m != api.PROVIDERS[p.api_provider][2] else ""
        else:
            bpy.ops.preferences.addon_show(module=__name__)
        return {"FINISHED"}


class CLAUDECHAT_OT_fetch_models(bpy.types.Operator):
    bl_idname = "claude_chat.fetch_models"
    bl_label = "Refresh Model List"
    bl_description = "Ask every provider that is set up which models your key can use"

    def execute(self, context):
        p = _prefs()
        base = _api_base(p)
        bad = api.check_url(base) if base else "Set the API address first."
        if bad:
            self.report({"ERROR"}, bad)
            return {"CANCELLED"}
        jobs = [(prov, _api_base(p, prov), _api_key(p, prov)) for prov in api.PROVIDERS if _set_up(p, prov)]
        S.models_status = "Loading models…"

        def work():
            got, notes = {}, []
            for provider, base, key in jobs:
                label = api.PROVIDERS[provider][0].split(" (")[0]
                try:
                    got[provider] = [i for i in api.list_models(base, key) if not _NOT_CHAT.search(i)]
                    notes.append(f"{label} {len(got[provider])}")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{label}: couldn't list ({getattr(e, 'code', '')} {e})"[:80])
            status = "Models: " + ", ".join(notes) + " (in the model menu)"

            def apply():
                S.api_models.update(got)
                S.models_status = status
                _redraw()
            bpy.app.timers.register(apply, first_interval=0.0)
        threading.Thread(target=work, daemon=True).start()
        return {"FINISHED"}


class CLAUDECHAT_OT_toggle(bpy.types.Operator):
    bl_idname = "claude_chat.toggle"
    bl_label = "Expand"
    bl_description = "Show or hide details"

    index: bpy.props.IntProperty()

    def execute(self, context):
        if 0 <= self.index < len(S.messages):
            m = S.messages[self.index]
            m["open"] = not m.get("open")
        return {"FINISHED"}


PROMPT_ITEMS = [
    ("instructions", "Instructions", "Your standing instructions, sent with every message"),
    ("base", "Built-in Prompt", "How Claude works inside Blender. Not meant to be changed"),
    *[(f"mode_{m[0]}", f"{m[1]} Prompt", f"How the AI works in the {m[1]} mode") for m in MODES],
    ("guide_print", "3D Printing Guide", "Printing know-how the AI reads when it needs it"),
    ("guide_bonsai", "Bonsai Guide", "BIM know-how the AI reads when it needs it"),
]


class CLAUDECHAT_OT_edit_instructions(bpy.types.Operator):
    bl_idname = "claude_chat.edit_instructions"
    bl_label = "Edit Instructions"
    bl_description = ("Open Claude's standing instructions in a Text Editor window. "
                      "Changes apply from the next message")

    which: bpy.props.EnumProperty(items=PROMPT_ITEMS, options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, props):
        if props.which == "base":
            return ("Open the built-in prompt (how the AI works inside Blender) in a Text "
                    "Editor window. Not meant to be changed: edits can break the add-on")
        return cls.bl_description

    def execute(self, context):
        t = _prompt_text(self.which) or bpy.data.texts.load(_prompt_path(self.which))
        bpy.ops.wm.window_new()
        area = context.window_manager.windows[-1].screen.areas[0]
        area.ui_type = "TEXT_EDITOR"
        space = area.spaces.active
        space.text = t
        space.show_word_wrap = True
        if self.which == "base":
            self.report({"WARNING"}, "Built-in prompt: not meant to be changed. Your "
                                     "preferences belong in Edit Instructions")
        return {"FINISHED"}


class CLAUDECHAT_OT_reset_instructions(bpy.types.Operator):
    bl_idname = "claude_chat.reset_instructions"
    bl_label = "Reset Instructions to Default"
    bl_description = "Replace the standing instructions with the defaults"

    which: bpy.props.EnumProperty(items=PROMPT_ITEMS, options={"SKIP_SAVE"})

    @classmethod
    def description(cls, context, props):
        if props.which != "instructions":
            return "Undo all edits to this prompt or guide"
        return cls.bl_description

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        _write_prompt(self.which, _prompt_default(self.which))
        self.report({"INFO"}, "Reset to default")
        return {"FINISHED"}


class CLAUDECHAT_OT_copy(bpy.types.Operator):
    bl_idname = "claude_chat.copy"
    bl_label = "Copy"
    bl_description = "Copy this message to the clipboard (-1 copies the whole chat)"

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        if self.index < 0:
            text = "\n\n".join(f"[{m['role']}] {m['text']}" for m in S.messages)
            what = "chat"
        else:
            text = S.messages[self.index]["text"]
            what = "message"
        context.window_manager.clipboard = text
        self.report({"INFO"}, f"Copied {what} ({len(text)} chars)")
        return {"FINISHED"}


def _attachment_dir():
    path = os.path.join(_data_dir(), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def _image_editor_area():
    """image.clipboard_paste only runs in an Image Editor, so borrow an area."""
    for win in bpy.context.window_manager.windows:
        areas = sorted(win.screen.areas, key=lambda a: a.width * a.height)
        area = next((a for a in areas
                     if a.type in {"PROPERTIES", "OUTLINER", "DOPESHEET_EDITOR", "TIMELINE",
                                   "IMAGE_EDITOR"}), areas[0] if areas else None)
        if area:
            return win, area
    return None, None


def _native_paste(folder):
    """Blender's own clipboard paste, via a borrowed Image Editor. Returns a path."""
    win, area = _image_editor_area()
    if not area:
        return None
    before = set(bpy.data.images.keys())
    old_type = area.type
    try:
        area.type = "IMAGE_EDITOR"
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, area=area, region=region):
            if not bpy.ops.image.clipboard_paste.poll():
                return None
            bpy.ops.image.clipboard_paste()
    except RuntimeError:
        return None
    finally:
        area.type = old_type
    new = [bpy.data.images[n] for n in set(bpy.data.images.keys()) - before]
    if not new:
        return None
    img = new[0]
    path = os.path.join(folder, f"paste_{int(time.time())}.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)  # the saved file is what gets sent
    return path


class CLAUDECHAT_OT_paste_image(bpy.types.Operator):
    bl_idname = "claude_chat.paste_image"
    bl_label = "Paste Image"
    bl_description = ("Paste an image from the clipboard (a screenshot, a reference photo) "
                      "and send it with your next message")

    def execute(self, context):
        folder = _attachment_dir()
        # Text on the clipboard is a copied file path or no image at all, so check
        # it before the image readers: on X11 those block Blender, and freeze it if
        # Blender itself owns the copied text.
        text = context.window_manager.clipboard
        if text.strip():
            path = clipboard.path_from_text(text)
            if not path:
                self.report({"WARNING"}, "The clipboard holds text, not an image")
                return {"CANCELLED"}
        else:
            path = _native_paste(folder) if clipboard.native_works() else None
            if not path:
                # Blender reads no clipboard images on X11. Tools first (cheap),
                # then a child process that talks to X11 directly.
                data = clipboard.image_bytes()
                if data:
                    path = clipboard.save(data, folder)
            if not path:
                path = clipboard.x11_via_subprocess(folder)
        if not path:
            self.report({"ERROR"}, "No image on the clipboard. Copy an image or an image "
                                   "file, or use the file button.")
            return {"CANCELLED"}
        S.attachments.append(path)
        _redraw()
        self.report({"INFO"}, f"Attached {os.path.basename(path)}")
        return {"FINISHED"}


class CLAUDECHAT_OT_attach_file(bpy.types.Operator):
    bl_idname = "claude_chat.attach_file"
    bl_label = "Attach File"
    bl_description = "Pick an image or file to send with your next message"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp;*.txt;*.md;*.json;*.csv;*.blend",
        options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if self.filepath and os.path.isfile(self.filepath):
            S.attachments.append(bpy.path.abspath(self.filepath))
            _redraw()
        return {"FINISHED"}


class CLAUDECHAT_OT_unattach(bpy.types.Operator):
    bl_idname = "claude_chat.unattach"
    bl_label = "Remove attachment"
    bl_description = "Don't send this file"

    index: bpy.props.IntProperty()

    def execute(self, context):
        if 0 <= self.index < len(S.attachments):
            S.attachments.pop(self.index)
            _redraw()
        return {"FINISHED"}


class CLAUDECHAT_OT_transcript(bpy.types.Operator):
    bl_idname = "claude_chat.transcript"
    bl_label = "Chat to Text Editor"
    bl_description = "Write the chat to a Text datablock (for copying / reading long replies)"

    def execute(self, context):
        txt = bpy.data.texts.get("LLM Chat") or bpy.data.texts.new("LLM Chat")
        txt.clear()
        txt.write("\n\n".join(f"[{m['role']}]\n{m['text']}" for m in S.messages))
        self.report({"INFO"}, "Written to Text 'LLM Chat' (see Text Editor)")
        return {"FINISHED"}


class CLAUDECHAT_OT_export_glb(bpy.types.Operator):
    bl_idname = "claude_chat.export_glb"
    bl_label = "Export to Godot"
    bl_description = ("Export the selected objects (with their children) as .glb "
                      "to the export folder, named after the active object")

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects) and bool(_prefs().export_dir)

    def execute(self, context):
        folder = bpy.path.abspath(_prefs().export_dir)
        os.makedirs(folder, exist_ok=True)
        root = context.active_object or context.selected_objects[0]
        path = os.path.join(folder, bpy.path.clean_name(root.name) + ".glb")
        before = list(context.selected_objects)
        for o in before:
            for child in o.children_recursive:
                child.select_set(True)
        try:
            bpy.ops.export_scene.gltf(filepath=path, export_format="GLB",
                                      use_selection=True, export_apply=True)
        finally:
            for o in context.selected_objects:
                o.select_set(o in before)
        self.report({"INFO"}, f"Exported {path}")
        return {"FINISHED"}


class CLAUDECHAT_OT_export_print(bpy.types.Operator):
    bl_idname = "claude_chat.export_print"
    bl_label = "Export for Printing"
    bl_description = ("Check the selected parts (or every visible part) and save them as .3mf "
                      "in the print folder, then slice them to G-code if a slicer is found")

    def execute(self, context):
        parts = [o for o in context.selected_objects if o.type == "MESH"] or bridge._print_parts()
        try:
            paths = printing.export(parts)
        except ValueError as e:
            self.report({"ERROR"}, str(e)[:300])
            return {"CANCELLED"}
        msg = "Saved " + ", ".join(paths)
        if printing.find_slicer():
            msg = printing.slice(paths)
        _add("info", msg)
        self.report({"INFO"}, msg.splitlines()[-1][:200])
        return {"FINISHED"}


class CLAUDECHAT_OT_export_ifc(bpy.types.Operator):
    bl_idname = "claude_chat.export_ifc"
    bl_label = "Save IFC"
    bl_description = "Save the Bonsai project as .ifc (next to the .blend unless it has a path)"

    @classmethod
    def poll(cls, context):
        return hasattr(bpy.ops, "bim") and any(
            k.endswith("bonsai") for k in context.preferences.addons.keys())

    def execute(self, context):
        from . import bim
        try:
            path = bim.bim_save()
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Saved {path}")
        return {"FINISHED"}


# ---------------------------------------------------------------- setup

def _on_input(self, context):
    """Blender fires this when the field is confirmed, which includes clicking
    away from it. Only send if Enter was pressed just now; otherwise the text
    stays in the field as a draft."""
    text = self.claude_chat_input
    if not text.strip() or S.busy or not _prefs().send_on_enter:
        return
    if S.watching and time.time() - S.last_enter > 0.5:
        return  # clicked elsewhere: keep the draft, let the Send button send it
    self.claude_chat_input = ""  # re-triggers this callback with "", which is a no-op
    bpy.app.timers.register(lambda: send(text), first_interval=0.01)


class CLAUDECHAT_OT_key_watch(bpy.types.Operator):
    """Runs quietly in the background, passing every event through, only noting
    when Enter was last pressed. Without it we cannot tell Enter from click-away."""
    bl_idname = "claude_chat.key_watch"
    bl_label = "Watch for Enter"
    bl_options = {"INTERNAL"}

    def modal(self, context, event):
        if not S.watching:
            return {"CANCELLED"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            S.last_enter = time.time()
        return {"PASS_THROUGH"}

    def invoke(self, context, event):
        S.watching = True
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}


def _keep_watching():
    """Start the watcher, and restart it after a file load drops modal handlers."""
    if not S.watching:
        try:
            bpy.ops.claude_chat.key_watch("INVOKE_DEFAULT")
        except (RuntimeError, AttributeError):
            pass  # no window yet; try again on the next tick
    return 2.0


class CLAUDECHAT_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    claude_path: bpy.props.StringProperty(
        name="Claude CLI path", subtype="FILE_PATH",
        description="Leave empty to auto-detect (PATH, ~/.local/bin, VS Code extension)")
    model: bpy.props.EnumProperty(name="Model", default="opus", items=MODELS)
    mode: bpy.props.EnumProperty(
        name="Mode", default="game", items=MODES, update=lambda self, ctx: _on_mode(self),
        description="What the AI makes: its built-in prompt, checks and exports follow the mode")
    printer: bpy.props.EnumProperty(
        name="Printer", default="Creality K1C",
        items=[(k, k, f"{v[0]} x {v[1]} x {v[2]} mm, {v[3]} mm nozzle", i)
               for i, (k, v) in enumerate(printing.PRINTERS.items())] +
              [("custom", "Custom", "Your own bed size and nozzle", 99)])
    bed_x: bpy.props.FloatProperty(name="Bed X (mm)", default=220, min=10)
    bed_y: bpy.props.FloatProperty(name="Bed Y (mm)", default=220, min=10)
    bed_z: bpy.props.FloatProperty(name="Max height (mm)", default=250, min=10)
    nozzle: bpy.props.FloatProperty(name="Nozzle (mm)", default=0.4, min=0.1, max=2)
    slicer_path: bpy.props.StringProperty(
        name="Slicer", subtype="FILE_PATH",
        description="OrcaSlicer or Creality Print, to turn prints into G-code. Empty = "
                    "auto-detect (PATH, Program Files, AppImages in ~/Applications, Downloads)")
    print_dir: bpy.props.StringProperty(
        name="Print folder", subtype="DIR_PATH",
        description="Where 3MF/STL/G-code files go. Empty = Desktop/LLM Chat prints")
    api_provider: bpy.props.EnumProperty(
        name="Provider", default="deepseek", items=API_PROVIDERS,
        description="Where the other models run (when the model menu says Other model)")
    api_model: bpy.props.StringProperty(
        name="API model", description="Model id at the provider; empty = its default")
    api_base_url: bpy.props.StringProperty(
        name="API base URL", description="Custom provider only, e.g. https://example.com/v1")
    for _p in api.PROVIDERS:
        __annotations__[f"api_key_{_p}"] = bpy.props.StringProperty(
            name=f"{api.PROVIDERS[_p][0]} key", subtype="PASSWORD",
            description="Sent only to this provider. Or set the environment variable "
                        + KEY_ENV.get(_p, "(none)"))
    del _p
    effort: bpy.props.EnumProperty(name="Effort", default="medium", items=EFFORTS)
    export_dir: bpy.props.StringProperty(
        name="Export folder", subtype="DIR_PATH",
        description="Where .glb exports go, e.g. your Godot project's assets folder. "
                    "Also tells the AI where to export")
    use_web: bpy.props.BoolProperty(
        name="Web", default=True,
        description="Let the AI search the web and read pages, for reference photos, "
                    "real dimensions and how a thing is actually built (Claude models). A page "
                    "could contain text written to mislead the AI")
    use_files: bpy.props.BoolProperty(
        name="Files", default=True,
        description="Let the AI read files on this computer with its file tools "
                    "(reference images, your Godot project's code; Claude models). Needed for attachments")
    use_shell: bpy.props.BoolProperty(
        name="Shell + write files", default=False,
        description="Also give the AI a terminal and file-editing tools, e.g. to edit "
                    "your game's source (Claude models). It can change anything on your computer")
    extra_tools: bpy.props.StringProperty(
        name="Extra tools", default="",
        description="Other Claude Code tools to allow, comma separated (e.g. 'Task')")
    include_context: bpy.props.BoolProperty(
        name="Send scene context", default=True,
        description="Prefix each message with the selection, mode and scene object list")
    send_on_enter: bpy.props.BoolProperty(name="Send on Enter", default=True)
    step_limit: bpy.props.IntProperty(
        name="Step limit", default=0, min=0,
        description="Most tool rounds an API model may take per message, then it stops and "
                    "waits for you (it is told 3 rounds before). 0 = no limit")
    expanded_turns: bpy.props.IntProperty(
        name="Expanded turns", default=1, min=1, max=20,
        description="How many recent turns stay unfolded; older ones fold to one line")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "claude_path")
        found = _find_claude()
        col.label(text=f"Detected: {found or 'not found'}",
                  icon="CHECKMARK" if found else "ERROR")
        row = col.row()
        row.prop(self, "model")
        row.prop(self, "effort")
        col.prop(self, "export_dir")
        box = col.box()
        box.label(text="3D Printing", icon="MOD_SOLIDIFY")
        box.prop(self, "printer")
        if self.printer == "custom":
            row = box.row(align=True)
            for k in ("bed_x", "bed_y", "bed_z", "nozzle"):
                row.prop(self, k)
        box.prop(self, "slicer_path")
        sub = box.row()
        sub.active = False
        sub.label(text=f"Detected: {printing.find_slicer() or 'none (3MF only)'}")
        box.prop(self, "print_dir")
        _draw_api_setup(col.box(), context)
        row = col.row(align=True)
        row.label(text="Claude may use:")
        row.prop(self, "use_web", toggle=True)
        row.prop(self, "use_files", toggle=True)
        row.prop(self, "use_shell", toggle=True)
        note = col.column(align=True)
        note.active = False
        for line in ("Claude always works by running Python inside Blender, and Blender's "
                     "Python can reach",
                     "any file on this computer, whatever the switches above say. Only "
                     "open .blend files",
                     "you trust while chatting, and turn Web off for sensitive work."):
            note.label(text=line)
        col.prop(self, "extra_tools")
        row = col.row()
        row.prop(self, "include_context")
        row.prop(self, "send_on_enter")
        col.prop(self, "expanded_turns")
        col.prop(self, "step_limit")
        row = col.row()
        row.operator("claude_chat.edit_instructions", icon="TEXT")
        row.operator("claude_chat.reset_instructions", icon="LOOP_BACK")
        row = col.row()
        row.operator("claude_chat.edit_instructions", text="Edit Built-in Prompt (Advanced)",
                     icon="ERROR").which = "base"
        row.operator("claude_chat.reset_instructions", text="Reset Built-in Prompt",
                     icon="LOOP_BACK").which = "base"


@bpy.app.handlers.persistent
def _save_instructions_handler(*_):
    for which in PROMPT_FILES:
        t = _prompt_text(which)
        if t:
            _save_text(t)


classes = (
    CLAUDECHAT_Prefs, CLAUDECHAT_PT_panel, CLAUDECHAT_PT_setup,
    CLAUDECHAT_OT_open_url, CLAUDECHAT_OT_install_claude, CLAUDECHAT_OT_login,
    CLAUDECHAT_OT_submit_code, CLAUDECHAT_OT_cancel_login, CLAUDECHAT_OT_check_auth,
    CLAUDECHAT_OT_install_extras,
    CLAUDECHAT_MT_model, CLAUDECHAT_MT_history, CLAUDECHAT_MT_more,
    CLAUDECHAT_OT_send, CLAUDECHAT_OT_stop, CLAUDECHAT_OT_new, CLAUDECHAT_OT_resume,
    CLAUDECHAT_OT_toggle, CLAUDECHAT_OT_edit_instructions, CLAUDECHAT_OT_reset_instructions,
    CLAUDECHAT_OT_transcript, CLAUDECHAT_OT_export_glb, CLAUDECHAT_OT_export_print,
    CLAUDECHAT_OT_export_ifc, CLAUDECHAT_OT_copy,
    CLAUDECHAT_OT_key_watch, CLAUDECHAT_OT_paste_image, CLAUDECHAT_OT_attach_file,
    CLAUDECHAT_OT_unattach, CLAUDECHAT_OT_set_model, CLAUDECHAT_OT_fetch_models,
)
_HANDLERS = [h for h in ("save_pre", "exit_pre") if hasattr(bpy.app.handlers, h)]


@bpy.app.handlers.persistent
def _restart_watch(*_args):
    S.watching = False  # _keep_watching starts a fresh one on its next tick


def register():
    S.icons = bpy.utils.previews.new()
    S.icons.load("claude", os.path.join(ADDON_DIR, "claude_icon.png"), "IMAGE")
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.claude_chat_input = bpy.props.StringProperty(
        name="Message", description="Type a message; Enter sends it", update=_on_input,
        options=set())
    bpy.types.WindowManager.claude_chat_code = bpy.props.StringProperty(
        name="Sign-in code", options=set())
    for h in _HANDLERS:
        getattr(bpy.app.handlers, h).append(_save_instructions_handler)
    bpy.app.handlers.load_post.append(_restart_watch)
    bpy.app.timers.register(_keep_watching, first_interval=0.5, persistent=True)
    bpy.app.timers.register(lambda: _check_auth(), first_interval=1.0)
    if not bpy.app.background:
        bpy.app.timers.register(_write_link, first_interval=1.5, persistent=True)


def unregister():
    _stop()
    _remove_link()
    if bpy.app.timers.is_registered(_write_link):
        bpy.app.timers.unregister(_write_link)
    bridge.stop()
    if S.login:
        S.login.cancel()
        S.login = None
    S.watching = False
    if bpy.app.timers.is_registered(_keep_watching):
        bpy.app.timers.unregister(_keep_watching)
    if _restart_watch in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restart_watch)
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    for h in _HANDLERS:
        lst = getattr(bpy.app.handlers, h)
        if _save_instructions_handler in lst:
            lst.remove(_save_instructions_handler)
    del bpy.types.Scene.claude_chat_input
    del bpy.types.WindowManager.claude_chat_code
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    bpy.utils.previews.remove(S.icons)
    S.icons = None
