"""Video asset and projection actions for the shared light-effect list."""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..mat_export import _export_drones
from ..video_projection import discard_unused_video, load_video
from .operators import refresh


def selected_video(scene):
    effect = scene.drones_anim_light_effects.active_entry
    return effect if effect is not None and effect.type == "VIDEO" else None


class DroneAnimOpenVideoOperator(Operator):
    bl_idname = "drones_anim.open_led_video"
    bl_label = "选择视频"
    bl_description = "为当前视频灯效选择本地视频"
    bl_options = {"UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.scene is not None and selected_video(context.scene) is not None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        effect = selected_video(context.scene)
        try:
            texture, duration = load_video(bpy.path.abspath(self.filepath))
        except (ValueError, RuntimeError, OSError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        previous = effect.video_texture
        effect.video_texture = texture
        effect.video_duration = duration
        if previous is None:
            effect.name = bpy.path.basename(texture.image.filepath)
        discard_unused_video(previous)
        refresh(context.scene)
        return {"FINISHED"}


class DroneAnimCaptureVideoViewOperator(Operator):
    bl_idname = "drones_anim.capture_led_video_view"
    bl_label = "捕获当前视角与无人机"
    bl_description = "记录当前 3D 视角和选中的无人机；未选中时保留原范围"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == "VIEW_3D"
                and selected_video(context.scene) is not None)

    def execute(self, context):
        region = next((region for region in context.area.regions if region.type == "WINDOW"), None)
        view = context.region_data or context.space_data.region_3d
        if view is None or region is None or min(region.width, region.height) < 1:
            self.report({"ERROR"}, "请在有效的 3D 视口中捕获视角")
            return {"CANCELLED"}
        matrix = view.perspective_matrix.copy()
        if matrix.determinant() == 0:
            self.report({"ERROR"}, "请等待 3D 视口更新后重新捕获")
            return {"CANCELLED"}
        effect = selected_video(context.scene)
        effect.video_view_matrix = tuple(value for row in matrix for value in row)
        effect.video_viewport_size = (region.width, region.height)
        effect.video_view_captured = True
        selected = set(context.selected_objects or ())
        drones = _export_drones(context.scene, ordered=False)
        targets = [drone for drone in drones if drone in selected]
        if targets:
            effect.video_targets.clear()
            if len(targets) < len(drones):
                for drone in targets:
                    effect.video_targets.add().drone = drone
        refresh(context.scene)
        return {"FINISHED"}


class DroneAnimSelectVideoTargetsOperator(Operator):
    bl_idname = "drones_anim.select_led_video_targets"
    bl_label = "选择影响无人机"
    bl_description = "选中当前视频灯效影响范围内可选择的无人机"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.scene is not None and context.mode == "OBJECT"
                and selected_video(context.scene) is not None)

    def execute(self, context):
        effect = selected_video(context.scene)
        drones = _export_drones(context.scene, ordered=False)
        selectable = set(context.selectable_objects or ())
        if effect.video_targets:
            selectable.intersection_update(target.drone for target in effect.video_targets)
        targets = [drone for drone in drones if drone in selectable]
        target_set = set(targets)
        selected = set(context.selected_objects or ())
        for obj in selected - target_set:
            obj.select_set(False, view_layer=context.view_layer)
        for drone in target_set - selected:
            drone.select_set(True, view_layer=context.view_layer)
        if context.view_layer.objects.active not in target_set:
            context.view_layer.objects.active = targets[0] if targets else None
        return {"FINISHED"}
