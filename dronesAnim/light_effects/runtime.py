"""Scene-local effect snapshots and non-destructive presentation state."""

from contextlib import contextmanager
from dataclasses import dataclass, field

import bpy
import numpy as np
from bpy.app.handlers import persistent

from .evaluator import evaluate_stack
from .functions import TrustedFunctions
from .images import ImagePixelCache
from .indexing import ordered_drones


@dataclass
class SceneState:
    revision: int = 0
    key: object = None
    drones: tuple = ()
    colors: object = None
    errors: tuple = ()
    published: bool = False
    images: ImagePixelCache = field(default_factory=ImagePixelCache)


_states = {}
_trust = TrustedFunctions()
_busy = False
# Render and undo gates are covered on Blender 4.2 and 5.2.
_LIVE_RUNTIME_ENABLED = True


def state_for(scene):
    return _states.setdefault(scene.as_pointer(), SceneState())


def invalidate(scene, *, images=False):
    state = state_for(scene)
    state.revision += 1
    state.key = None
    if images:
        state.images.clear()


def _refresh_base(drones, depsgraph):
    """Discard evaluated overrides by asking Blender to reevaluate originals."""
    for drone in drones:
        try:
            drone.update_tag(refresh={"OBJECT"})
        except ReferenceError:
            pass
    depsgraph.update()


def _publish(state, drones, colors, depsgraph):
    try:
        for drone, color in zip(drones, colors):
            final = tuple(float(value) for value in color)
            drone.evaluated_get(depsgraph).color = final
    except Exception:
        state.published = False
        state.key = None
        _refresh_base(drones, depsgraph)
        raise
    state.published = True


def restore_scene(scene, depsgraph=None):
    global _busy
    if _busy:
        return
    state = _states.get(scene.as_pointer())
    if state is None:
        return
    _busy = True
    try:
        if state.published:
            if depsgraph is not None:
                _refresh_base(state.drones, depsgraph)
            else:
                with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                    _refresh_base(state.drones, bpy.context.evaluated_depsgraph_get())
        state.published = False
        invalidate(scene)
    finally:
        _busy = False


def final_colors(scene, depsgraph, *, publish=False):
    """Get ordered drones and final RGBA; optionally publish material colors."""
    global _busy
    state = state_for(scene)
    drones = tuple(ordered_drones(scene))
    identities = tuple(drone.as_pointer() for drone in drones)
    key = (
        scene.frame_current, scene.frame_subframe, state.revision, identities,
        depsgraph.as_pointer(),
        tuple(tuple(drone.color) for drone in drones),
    )
    if state.key == key and state.colors is not None:
        if publish:
            if _busy:
                raise RuntimeError("Light effects are already being evaluated")
            _busy = True
            try:
                _publish(state, drones, state.colors, depsgraph)
            finally:
                _busy = False
        return state.drones, state.colors.copy(), state.errors
    if _busy:
        raise RuntimeError("Light effects are already being evaluated")
    _busy = True
    try:
        _refresh_base(drones, depsgraph)
        state.published = False
        evaluated = [drone.evaluated_get(depsgraph) for drone in drones]
        positions = np.array(
            [tuple(drone.matrix_world.translation) for drone in evaluated],
            dtype=np.float32,
        ).reshape((-1, 3))
        base = np.array(
            [tuple(drone.color) for drone in evaluated], dtype=np.float32
        ).reshape((-1, 4))
        colors, errors = evaluate_stack(
            scene, drones, positions, base, depsgraph, state.images, _trust
        )
        if publish:
            _publish(state, drones, colors, depsgraph)
        state.key = key
        state.drones = drones
        state.colors = colors.copy()
        state.errors = tuple(errors)
        return drones, colors.copy(), state.errors
    except Exception:
        state.published = False
        state.key = None
        raise
    finally:
        _busy = False


def trust_store():
    return _trust


@contextmanager
def base_color_edit(scene):
    """Defer procedural publication until a batch of base keys is complete."""
    global _busy
    previous = _busy
    _busy = True
    try:
        yield
    finally:
        invalidate(scene)
        _busy = previous
        if not previous:
            with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                update_scene(scene, bpy.context.evaluated_depsgraph_get())


def update_scene(scene, depsgraph):
    if _busy or not hasattr(scene, "drones_anim_light_effects"):
        return
    stack = scene.drones_anim_light_effects
    frame = scene.frame_current + scene.frame_subframe
    if not stack.enabled or not any(
        effect.enabled and effect.frame_start <= frame <= effect.frame_end
        for effect in stack.entries
    ):
        state = _states.get(scene.as_pointer())
        if state is not None and state.published:
            restore_scene(scene, depsgraph)
        return
    mode = getattr(getattr(scene, "drones_anim_led", None), "visualization", "NONE")
    if mode != "MATERIALS":
        restore_scene(scene, depsgraph)
    final_colors(scene, depsgraph, publish=mode == "MATERIALS")


@persistent
def _on_frame_pre(scene, depsgraph=None):
    restore_scene(scene, depsgraph)


@persistent
def _on_frame_post(scene, depsgraph=None):
    if _busy:
        return
    invalidate(scene)
    if depsgraph is not None:
        update_scene(scene, depsgraph)


@persistent
def _on_graph(scene, depsgraph):
    if _busy or not hasattr(scene, "drones_anim_light_effects"):
        return
    invalidate(scene)
    update_scene(scene, depsgraph)


@persistent
def _on_save_pre(_unused):
    for scene in bpy.data.scenes:
        restore_scene(scene)


@persistent
def _on_save_post(_unused):
    scene = bpy.context.scene
    if scene is not None:
        update_scene(scene, bpy.context.evaluated_depsgraph_get())


@persistent
def _on_load_pre(_unused):
    for scene in bpy.data.scenes:
        restore_scene(scene)


@persistent
def _on_load_post(_unused):
    _states.clear()
    _trust.clear()


@persistent
def _on_history_pre(_unused):
    for scene in bpy.data.scenes:
        restore_scene(scene)
    # Undo can replace original RNA objects; never carry references across it.
    _states.clear()


@persistent
def _on_history_post(_unused):
    for scene in bpy.data.scenes:
        invalidate(scene, images=True)


_HANDLERS = (
    ("frame_change_pre", _on_frame_pre),
    ("frame_change_post", _on_frame_post),
    ("depsgraph_update_post", _on_graph),
    ("save_pre", _on_save_pre),
    ("save_post", _on_save_post),
    ("load_pre", _on_load_pre),
    ("load_post", _on_load_post),
    ("undo_pre", _on_history_pre),
    ("undo_post", _on_history_post),
    ("redo_pre", _on_history_pre),
    ("redo_post", _on_history_post),
)


def register():
    if not _LIVE_RUNTIME_ENABLED:
        return
    for name, callback in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    for scene in bpy.data.scenes:
        restore_scene(scene)
    for name, callback in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        while callback in handlers:
            handlers.remove(callback)
    _states.clear()
    _trust.clear()
