"""Press-and-hold list grips; commit after Blender releases its RNA button."""
import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator

from .operators import refresh
from .ordering import move

_pending = []


def commit_pending():
    pending, _pending[:] = tuple(_pending), []
    for scene_uid, effect_id, destination in pending:
        scene = bpy.context.scene
        if scene is not None and scene.session_uid == scene_uid:
            bpy.ops.drones_anim.reorder_light_effect(
                "EXEC_DEFAULT", effect_id=effect_id, destination=destination,
            )
    return None


class DroneAnimReorderLightEffectOperator(Operator):
    bl_idname = "drones_anim.reorder_light_effect"
    bl_label = "调整灯效顺序"
    bl_options = {"INTERNAL", "UNDO"}

    effect_id: StringProperty(options={"HIDDEN"})
    destination: IntProperty(min=0, options={"HIDDEN"})

    def execute(self, context):
        stack = context.scene.drones_anim_light_effects
        source = next((i for i, entry in enumerate(stack.entries) if entry.effect_id == self.effect_id), None)
        if source is None or not 0 <= self.destination < len(stack.entries):
            return {"CANCELLED"}
        if source == self.destination:
            return {"CANCELLED"}
        move(context.scene, source, self.destination)
        stack.active_entry_index = self.destination
        refresh(context.scene)
        return {"FINISHED"}


class DroneAnimDragLightEffectOperator(Operator):
    bl_idname = "drones_anim.drag_light_effect"
    bl_label = "拖动灯效"
    bl_description = "按住六点手柄上下拖动，松开后调整灯效顺序；Esc 取消"
    bl_options = {"INTERNAL", "BLOCKING", "MODAL_PRIORITY"}

    effect_id: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        stack = context.scene.drones_anim_light_effects
        source = next((i for i, entry in enumerate(stack.entries) if entry.effect_id == self.effect_id), None)
        if source is None or len(stack.entries) < 2 or context.area is None or context.region is None:
            return {"CANCELLED"}
        self._scene_uid = context.scene.session_uid
        self._source = self._destination = source
        self._count = len(stack.entries)
        self._start_y = event.mouse_y
        scale = max(1.0, context.preferences.system.ui_scale)
        view = context.region.view2d
        low, high = view.region_to_view(0, 0), view.region_to_view(0, 100)
        zoom = 100 / abs(high[1] - low[1]) if high[1] != low[1] else 1.0
        self._row_height = max(1.0, 20 * scale * zoom)
        self._area = context.area
        self._window = context.window
        stack.drag_target = source
        self._window.cursor_modal_set("SCROLL_Y")
        context.window_manager.modal_handler_add(self)
        self._area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _cleanup(self, context):
        scene = context.scene
        if scene is not None and hasattr(scene, "drones_anim_light_effects"):
            scene.drones_anim_light_effects.drag_target = -1
        try:
            self._window.cursor_modal_restore()
            self._area.header_text_set(None)
            self._area.tag_redraw()
        except (ReferenceError, AttributeError):
            pass

    def modal(self, context, event):
        scene = context.scene
        if (scene is None or scene.session_uid != self._scene_uid
                or not hasattr(scene, "drones_anim_light_effects")
                or len(scene.drones_anim_light_effects.entries) != self._count):
            self._cleanup(context)
            return {"CANCELLED", "PASS_THROUGH"}
        if event.type in {"ESC", "RIGHTMOUSE", "WINDOW_DEACTIVATE"}:
            self._cleanup(context)
            return {"CANCELLED", "PASS_THROUGH"}
        if event.type == "MOUSEMOVE":
            delta = round((self._start_y - event.mouse_y) / self._row_height)
            self._destination = max(0, min(self._count - 1, self._source + delta))
            scene.drones_anim_light_effects.drag_target = self._destination
            self._area.header_text_set(f"移至第 {self._destination + 1} 项 · 松开确认 · Esc 取消")
            self._area.tag_redraw()
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            destination = self._destination
            self._cleanup(context)
            if destination != self._source:
                _pending.append((self._scene_uid, self.effect_id, destination))
                if not bpy.app.timers.is_registered(commit_pending):
                    bpy.app.timers.register(commit_pending, first_interval=0.0)
            # The Boolean grip's native button must finish before collection data moves.
            return {"CANCELLED", "PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        self._cleanup(context)


def unregister():
    _pending.clear()
    if bpy.app.timers.is_registered(commit_pending):
        bpy.app.timers.unregister(commit_pending)
    for scene in bpy.data.scenes:
        if hasattr(scene, "drones_anim_light_effects"):
            scene.drones_anim_light_effects.drag_target = -1
