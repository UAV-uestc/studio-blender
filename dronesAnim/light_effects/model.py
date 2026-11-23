"""Persistent, scene-local settings for the shared LED effect stack.

Adapted from Skybrush Studio for Blender (GPL-3.0-or-later).
"""

from uuid import uuid4

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    IntVectorProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Collection, Object, PropertyGroup, Scene, Texture

from .math import time_fraction
from .presets import get_preset_enum_items

OUTPUT_ITEMS = (
    ("FIRST_COLOR", "首端颜色", "", 1),
    ("LAST_COLOR", "末端颜色", "", 2),
    ("INDEXED_BY_DRONES", "无人机序号", "", 3),
    ("GRADIENT_XYZ", "空间渐变 XYZ", "", 4),
    ("GRADIENT_XZY", "空间渐变 XZY", "", 5),
    ("GRADIENT_YXZ", "空间渐变 YXZ", "", 6),
    ("GRADIENT_YZX", "空间渐变 YZX", "", 7),
    ("GRADIENT_ZXY", "空间渐变 ZXY", "", 8),
    ("GRADIENT_ZYX", "空间渐变 ZYX", "", 9),
    ("TEMPORAL", "时间进度", "", 10),
    ("DISTANCE", "距网格原点", "", 11),
    ("CUSTOM", "自定义函数 V1", "", 12),
    ("INDEXED_BY_FORMATION", "队形顶点序号", "", 13),
    ("LIGHT_PRESET", "内置效果", "", 14),
    ("CUSTOM_V2", "向量化函数 V2", "", 15),
)

MAPPING_ITEMS = (
    ("ORDERED", "等序排列", ""),
    ("PROPORTIONAL", "比例映射", ""),
)

BLEND_ITEMS = tuple(
    (identifier, label, "", index)
    for index, (identifier, label) in enumerate(
        (
            ("NORMAL", "正常"),
            ("MULTIPLY", "正片叠底"),
            ("SCREEN", "滤色"),
            ("DARKEN", "变暗"),
            ("LIGHTEN", "变亮"),
            ("OVERLAY", "叠加"),
            ("SOFT_LIGHT", "柔光"),
            ("HARD_LIGHT", "强光"),
        ),
        1,
    )
)


def _video_lock_changed(effect, context):
    if not effect.video_end_locked:
        from ..video_projection import video_natural_end

        effect.duration = max(1, video_natural_end(effect) - effect.frame_start + 1)
    _effect_changed(effect, context)


def _get_frame_end(effect):
    if effect.type == "VIDEO" and effect.video_end_locked:
        from ..video_projection import video_natural_end

        return video_natural_end(effect)
    return effect.frame_start + effect.duration - 1


def _set_frame_end(effect, value):
    if value <= effect.frame_start:
        effect.frame_start = value
        effect.duration = 1
    else:
        effect.duration = value - effect.frame_start + 1


def _mesh_only(_self, obj):
    return obj.type == "MESH"


def _unique_name(name, entries, *, exclude_pointer=None):
    name = name.strip() or "Untitled"
    used = {
        entry.name
        for entry in entries
        if entry.as_pointer() != exclude_pointer
    }
    if name not in used:
        return name
    number = 1
    while f"{name} ({number})" in used:
        number += 1
    return f"{name} ({number})"


def _effect_changed(value, _context):
    scene = value.id_data
    if isinstance(scene, Scene):
        from .runtime import invalidate

        invalidate(scene)
        scene.update_tag()


def _effect_type_changed(effect, context):
    if effect.type != "VIDEO" and effect.texture is None:
        texture = bpy.data.textures.new("DroneAnim Light Effect", type="IMAGE")
        texture["drones_anim_light_effect_owner"] = effect.effect_id
        texture.use_color_ramp = True
        for element in texture.color_ramp.elements:
            element.alpha = 1.0
        effect.texture = texture
    _effect_changed(effect, context)


def _name_updated(effect, _context):
    scene = effect.id_data
    stack = getattr(scene, "drones_anim_light_effects", None)
    if stack is None:
        return
    name = _unique_name(effect.name, stack.entries, exclude_pointer=effect.as_pointer())
    if effect.name != name:
        effect.name = name
    _effect_changed(effect, _context)


class DroneAnimLightFunctionProperties(PropertyGroup):
    """A user-selected Python file and function; execution needs separate trust."""

    path: StringProperty(name="Function file", subtype="FILE_PATH", update=_effect_changed)
    name: StringProperty(name="Function name", update=_effect_changed)


class DroneAnimLightVideoTarget(PropertyGroup):
    drone: PointerProperty(type=Object, update=_effect_changed)


class DroneAnimLightEffect(PropertyGroup):
    """Settings and texture references for one procedural or video effect."""

    effect_id: StringProperty(name="Identifier", options={"HIDDEN"})
    name: StringProperty(name="Name", default="Untitled", update=_name_updated)
    enabled: BoolProperty(name="Enabled", default=True, update=_effect_changed)
    type: EnumProperty(
        name="Effect type",
        items=(
            ("COLOR_RAMP", "颜色渐变", "", 1),
            ("IMAGE", "图像", "", 2),
            ("FUNCTION", "Python 函数", "", 3),
            ("VIDEO", "视频投影", "", 4),
        ),
        default="COLOR_RAMP",
        update=_effect_type_changed,
    )
    frame_start: IntProperty(name="Start frame", default=0, update=_effect_changed)
    duration: IntProperty(name="Duration", default=1, min=1, update=_effect_changed)
    frame_end: IntProperty(name="End frame", get=_get_frame_end, set=_set_frame_end)
    fade_in_duration: IntProperty(name="Fade in", default=0, min=0, update=_effect_changed)
    fade_out_duration: IntProperty(name="Fade out", default=0, min=0, update=_effect_changed)
    output: EnumProperty(name="Output X", items=OUTPUT_ITEMS, default="LAST_COLOR", update=_effect_changed)
    output_y: EnumProperty(name="Output Y", items=OUTPUT_ITEMS, default="LAST_COLOR", update=_effect_changed)
    output_mapping_mode: EnumProperty(name="Mapping X", items=MAPPING_ITEMS, update=_effect_changed)
    output_mapping_mode_y: EnumProperty(name="Mapping Y", items=MAPPING_ITEMS, update=_effect_changed)
    output_function: PointerProperty(type=DroneAnimLightFunctionProperties)
    output_function_y: PointerProperty(type=DroneAnimLightFunctionProperties)
    color_function: PointerProperty(type=DroneAnimLightFunctionProperties)
    preset_id: EnumProperty(name="Preset", items=get_preset_enum_items, default=0, update=_effect_changed)
    texture: PointerProperty(type=Texture, name="Texture", options={"HIDDEN"}, update=_effect_changed)
    mesh: PointerProperty(type=Object, name="Mesh", poll=_mesh_only, update=_effect_changed)
    drone_group: PointerProperty(type=Collection, name="Drone group", update=_effect_changed)
    missing_mesh_name: StringProperty(options={"HIDDEN"}, update=_effect_changed)
    missing_group_name: StringProperty(options={"HIDDEN"}, update=_effect_changed)
    missing_image_name: StringProperty(options={"HIDDEN"}, update=_effect_changed)
    target: EnumProperty(
        name="Target",
        items=(
            ("ALL", "全部无人机", "", 1),
            ("INSIDE_MESH", "网格内部", "", 2),
            ("FRONT_SIDE", "首个面前方", "", 3),
        ),
        default="ALL",
        update=_effect_changed,
    )
    invert_target: BoolProperty(name="Invert target", default=False, update=_effect_changed)
    blend_mode: EnumProperty(name="Blend mode", items=BLEND_ITEMS, default="NORMAL", update=_effect_changed)
    influence: FloatProperty(name="Influence", default=1.0, min=0.0, soft_max=1.0, update=_effect_changed)
    randomness: FloatProperty(name="Randomness", default=0.0, min=0.0, soft_max=1.0, update=_effect_changed)

    video_texture: PointerProperty(type=Texture, update=_effect_changed)
    video_duration: IntProperty(min=0, update=_effect_changed)
    video_view_captured: BoolProperty(update=_effect_changed)
    video_view_matrix: FloatVectorProperty(size=16, update=_effect_changed)
    video_viewport_size: IntVectorProperty(size=2, min=1, update=_effect_changed)
    video_targets: CollectionProperty(type=DroneAnimLightVideoTarget)
    video_end_locked: BoolProperty(default=True, update=_video_lock_changed)
    video_end: IntProperty(get=_get_frame_end, set=_set_frame_end, update=_effect_changed)

    # The decoder uses these aliases; enabled and frame range live in the shared model.
    @property
    def video_enabled(self):
        return self.enabled

    @property
    def video_start(self):
        return self.frame_start

    def get_time_fraction_for_frame(self, frame):
        return time_fraction(self.frame_start, self.duration, frame)


class DroneAnimLightEffectCollection(PropertyGroup):
    """Scene-specific effect stack and its current selection."""

    list_version: IntProperty(default=0, options={"HIDDEN"})
    enabled: BoolProperty(name="Light effects", default=True, update=_effect_changed)
    entries: CollectionProperty(type=DroneAnimLightEffect)
    active_entry_index: IntProperty(name="Active effect", default=0, min=0)
    random_seed: IntProperty(name="Random seed", default=0, update=_effect_changed)

    @property
    def active_entry(self):
        index = self.active_entry_index
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def append_effect(self, name="Untitled", *, select=True, effect_type="COLOR_RAMP", at_top=False):
        self.list_version = 1
        entry = self.entries.add()
        entry.effect_id = uuid4().hex
        entry.name = name
        entry.type = effect_type
        index = len(self.entries) - 1
        if at_top:
            self.entries.move(index, 0)
            index = 0
        if select:
            self.active_entry_index = index
        _effect_changed(self, None)
        return self.entries[index]

    def duplicate_active(self):
        index = self.active_entry_index
        if self.active_entry is None:
            return None
        copy = self.entries.add()
        source = self.entries[index]
        copy.effect_id = uuid4().hex
        copy.name = source.name
        for attr in (
            "enabled",
            "type",
            "frame_start",
            "duration",
            "fade_in_duration",
            "fade_out_duration",
            "output",
            "output_y",
            "output_mapping_mode",
            "output_mapping_mode_y",
            "preset_id",
            "mesh",
            "drone_group",
            "missing_mesh_name",
            "missing_group_name",
            "missing_image_name",
            "target",
            "invert_target",
            "blend_mode",
            "influence",
            "randomness",
            "video_texture", "video_duration", "video_view_captured",
            "video_view_matrix", "video_viewport_size", "video_end_locked",
        ):
            setattr(copy, attr, getattr(source, attr))
        for attr in ("output_function", "output_function_y", "color_function"):
            source_fn = getattr(source, attr)
            target_fn = getattr(copy, attr)
            target_fn.path = source_fn.path
            target_fn.name = source_fn.name
        if source.texture is not None:
            previous_texture = copy.texture
            texture = source.texture.copy()
            texture["drones_anim_light_effect_owner"] = copy.effect_id
            copy.texture = texture
            if previous_texture is not None and previous_texture.users == 0:
                bpy.data.textures.remove(previous_texture)
        copy.duration = source.duration
        for target in source.video_targets:
            copy.video_targets.add().drone = target.drone
        self.entries.move(len(self.entries) - 1, index + 1)
        self.active_entry_index = index + 1
        return self.active_entry

    def remove_active(self):
        entry = self.active_entry
        if entry is None:
            return
        index = self.active_entry_index
        effect_id = entry.effect_id
        video_texture = entry.video_texture
        entry.video_texture = None
        entry.texture = None
        self.entries.remove(index)
        for texture in tuple(bpy.data.textures):
            if (
                texture.get("drones_anim_light_effect_owner") == effect_id
                and texture.users == 0
            ):
                bpy.data.textures.remove(texture)
        from ..video_projection import discard_unused_video

        discard_unused_video(video_texture)
        self.active_entry_index = max(0, min(index, len(self.entries) - 1))
        _effect_changed(self, None)

    def move_active(self, direction):
        index = self.active_entry_index
        destination = index + direction
        if self.active_entry is not None and 0 <= destination < len(self.entries):
            self.entries.move(index, destination)
            self.active_entry_index = destination
            _effect_changed(self, None)


def register():
    Scene.drones_anim_light_effects = PointerProperty(type=DroneAnimLightEffectCollection)


def unregister():
    if hasattr(Scene, "drones_anim_light_effects"):
        del Scene.drones_anim_light_effects
