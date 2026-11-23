"""Unified procedural/video light-effect sidebar."""

import bpy
from bpy.types import Panel, UIList

from ..video_projection import video_natural_end, video_source_fps
from .runtime import state_for, trust_store


def stack_button(layout, action, *, text="", icon="NONE"):
    layout.operator("drones_anim.light_effect_stack", text=text, icon=icon).action = action


def function_controls(layout, effect, slot):
    properties = getattr(effect, slot)
    box = layout.box()
    box.prop(properties, "path", text="Python 文件")
    box.prop(properties, "name", text="函数名称")
    trusted = bool(properties.path) and trust_store().is_trusted(bpy.path.abspath(properties.path))
    box.label(text="本次会话已批准" if trusted else "未批准，不会执行", icon="CHECKMARK" if trusted else "LOCKED")
    row = box.row(align=True)
    for action, label in (
        ("DISCOVER", "列出函数"), ("REVOKE" if trusted else "APPROVE", "撤销批准" if trusted else "批准执行"),
    ):
        operator = row.operator("drones_anim.light_effect_function", text=label)
        operator.action, operator.slot = action, slot
    box.label(text="批准的 Python 未被沙箱隔离。", icon="ERROR")


def output_controls(layout, effect, *, y=False):
    output = effect.output_y if y else effect.output
    layout.prop(effect, "output_y" if y else "output", text="输出 Y" if y else "输出 X")
    if output.startswith("GRADIENT_") or output == "DISTANCE":
        layout.prop(effect, "output_mapping_mode_y" if y else "output_mapping_mode", text="映射")
    if output in {"CUSTOM", "CUSTOM_V2"}:
        function_controls(layout, effect, "output_function_y" if y else "output_function")
    elif output == "LIGHT_PRESET":
        layout.prop(effect, "preset_id", text="内置效果")


class DRONESANIM_UL_light_effects(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False, icon="FILE_MOVIE" if item.type == "VIDEO" else "TEXTURE")
        row.label(text=f"{item.frame_start}–{item.frame_end}")


class DroneAnimLightEffectsPanel(Panel):
    bl_idname = "OBJECT_PT_drones_anim_light_effects"
    bl_label = "灯效"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "灯光控制"
    bl_order = 10

    def draw(self, context):
        stack = context.scene.drones_anim_light_effects
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        row = layout.row(align=True)
        row.use_property_split = False
        row.prop(stack, "enabled", text="启用灯光效果")
        row.operator("drones_anim.light_effects_help", text="", icon="QUESTION", emboss=False)
        row = layout.row(align=True)
        stack_button(row, "ADD", text="添加灯效", icon="ADD")
        stack_button(row, "ADD_VIDEO", text="视频灯效", icon="FILE_MOVIE")
        row = layout.row()
        row.template_list("DRONESANIM_UL_light_effects", "", stack, "entries", stack, "active_entry_index", rows=4, sort_lock=True)
        buttons = row.column(align=True)
        editing = buttons.column(align=True)
        editing.enabled = stack.active_entry is not None
        for action, icon in (("REMOVE", "REMOVE"), ("DUPLICATE", "DUPLICATE"), ("UP", "TRIA_UP"), ("DOWN", "TRIA_DOWN")):
            stack_button(editing, action, icon=icon)
        row = layout.row(align=True)
        for action, label in (("IMPORT", "导入 JSON"), ("EXPORT", "导出 JSON")):
            row.operator("drones_anim.light_effect_json", text=label).action = action
        effect = stack.active_entry
        if effect is None:
            return
        box = layout.box()
        box.prop(effect, "name", text="名称")
        box.prop(effect, "enabled", text="启用此效果")
        box.prop(effect, "frame_start", text="起始帧")
        if effect.type == "VIDEO":
            end = box.row(align=True)
            field = end.row(align=True)
            field.enabled = not effect.video_end_locked
            field.prop(effect, "frame_end", text="结束帧")
            end.prop(effect, "video_end_locked", text="", icon="LOCKED" if effect.video_end_locked else "UNLOCKED")
        else:
            box.prop(effect, "duration", text="持续帧数")
            box.prop(effect, "frame_end", text="结束帧")
        row = box.row(align=True)
        stack_button(row, "START", text="起始设为当前帧")
        end = row.row(align=True)
        end.enabled = effect.type != "VIDEO" or not effect.video_end_locked
        stack_button(end, "END", text="结束设为当前帧")
        box.prop(effect, "fade_in_duration", text="淡入帧数")
        box.prop(effect, "fade_out_duration", text="淡出帧数")
        box = layout.box()
        box.prop(effect, "type", text="颜色来源")
        if effect.type == "VIDEO":
            video_controls(box, effect)
        elif effect.type == "FUNCTION":
            function_controls(box, effect, "color_function")
        else:
            if effect.texture is not None:
                if effect.type == "COLOR_RAMP":
                    box.template_color_ramp(effect.texture, "color_ramp", expand=True)
                else:
                    box.template_ID(effect.texture, "image", open="image.open")
                    box.operator("drones_anim.refresh_light_images", icon="FILE_REFRESH")
            output_controls(box, effect)
            if effect.type == "IMAGE":
                output_controls(box, effect, y=True)
        box = layout.box()
        box.prop(effect, "target", text="空间目标")
        if effect.target != "ALL" or effect.output == "DISTANCE" or effect.output_y == "DISTANCE":
            box.prop(effect, "mesh", text="目标网格")
        box.prop(effect, "drone_group", text="无人机集合")
        box.prop(effect, "invert_target", text="反向选择")
        for attr, label in (("blend_mode", "混合模式"), ("influence", "影响程度")):
            layout.prop(effect, attr, text=label)
        if effect.type != "VIDEO":
            layout.prop(effect, "randomness", text="随机偏移")
            layout.prop(stack, "random_seed", text="随机种子")
        for error in state_for(context.scene).errors:
            layout.label(text=error, icon="ERROR")


def video_controls(layout, effect):
    texture = effect.video_texture
    image = texture.image if isinstance(texture, bpy.types.ImageTexture) else None
    layout.operator("drones_anim.open_led_video",
                    text=bpy.path.basename(image.filepath) if image else "选择视频", icon="FILE_FOLDER")
    if effect.video_duration:
        layout.label(text=f"素材帧数：{effect.video_duration}")
        fps = video_source_fps(texture)
        layout.label(text=f"素材帧率：{fps:g} FPS" if fps > 0 else "请重新选择视频以读取帧率")
    if effect.video_duration and effect.frame_end > video_natural_end(effect):
        layout.label(text=f"第 {video_natural_end(effect) + 1} 帧起循环")
    layout.operator("drones_anim.capture_led_video_view", icon="VIEW_CAMERA")
    layout.label(text="投影视角已捕获" if effect.video_view_captured else "请先捕获投影视角",
                 icon="CHECKMARK" if effect.video_view_captured else "INFO")
    layout.operator("drones_anim.select_led_video_targets", icon="RESTRICT_SELECT_OFF")
    count = len(effect.video_targets)
    layout.label(text=f"影响范围：{count} 架无人机" if count else "影响范围：全部无人机")
