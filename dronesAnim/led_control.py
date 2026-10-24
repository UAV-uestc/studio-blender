"""Self-contained LED sidebar, operators and animated viewport markers.

Adapted from Skybrush Studio's GPL-3.0-or-later LED controls.
"""

from random import shuffle

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Image, Operator, Panel, PropertyGroup, Scene

from .led_animation import get_drones, insert_color, update_emission
from .uv_sampling import sample_attached_colors

_draw_handle = None


def _views():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                yield area


def _redraw():
    for area in _views():
        area.tag_redraw()


def _visualization_changed(self, context):
    for area in _views():
        shading = area.spaces.active.shading
        if self.visualization == "MATERIALS":
            shading.color_type = "OBJECT"
            shading.wireframe_color_type = "OBJECT"
        elif self.visualization == "MARKERS":
            shading.color_type = "SINGLE"
            shading.wireframe_color_type = "THEME"
        area.tag_redraw()


def _emission_changed(self, context):
    update_emission(self.id_data, self.emission_strength)
    _redraw()


def _color_property(name, default=(1.0, 1.0, 1.0)):
    return FloatVectorProperty(
        name=name,
        description=f"设置{name}",
        subtype="COLOR",
        size=3,
        min=0,
        max=1,
        default=default,
    )


class DroneAnimLEDProperties(PropertyGroup):
    visualization: EnumProperty(
        name="显示方式",
        description="选择无人机灯光在三维视口中的显示方式",
        items=(
            ("NONE", "不显示标记", "关闭视口中的灯光标记"),
            ("MARKERS", "彩色标记", "使用高效的彩色点标记显示无人机灯光"),
            (
                "MATERIALS",
                "材质显示",
                "显示动画物体颜色和无人机发光材质",
            ),
        ),
        default="MATERIALS",
        update=_visualization_changed,
    )
    marker_size: IntProperty(
        name="标记大小",
        description="设置视口灯光标记的像素大小",
        min=1,
        soft_max=50,
        default=25,
    )
    primary_color: _color_property("主颜色")
    secondary_color: _color_property("副颜色", (0.0, 0.0, 0.0))
    emission_strength: FloatProperty(
        name="发光强度",
        description="设置无人机灯光材质的发光强度",
        min=0,
        soft_max=5,
        default=1,
        update=_emission_changed,
    )
    uv_image: PointerProperty(
        name="纹理图像",
        type=Image,
        description="直接采样静态 UV 纹理图像，不包含材质着色或节点坐标映射",
    )
    uv_map: StringProperty(
        name="UV 贴图",
        description="附着网格的 UV 层名称；留空时使用该网格的活动渲染 UV 贴图",
    )
    uv_scope: EnumProperty(
        name="采样范围",
        description="选择要采样纹理颜色的无人机范围",
        items=(
            (
                "ALL",
                "全部附着无人机",
                "采样当前场景中本插件管理的全部附着无人机",
            ),
            ("SELECTED", "选中的无人机", "仅采样本插件管理的选中无人机"),
        ),
        default="ALL",
    )
    uv_extension: EnumProperty(
        name="图像边界",
        description="设置超出图像范围的 UV 坐标如何取色",
        items=(
            ("REPEAT", "重复", "UV 坐标超出零到一区间时重复图像"),
            ("CLAMP", "延伸", "UV 坐标超出图像范围时使用边缘像素"),
        ),
        default="REPEAT",
    )


class DroneAnimSwapLEDColorsOperator(Operator):
    bl_idname = "drones_anim.swap_led_colors"
    bl_label = "交换主副颜色"
    bl_description = "交换灯光的主颜色和副颜色"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.drones_anim_led
        primary, secondary = tuple(props.primary_color), tuple(props.secondary_color)
        props.primary_color, props.secondary_color = secondary, primary
        return {"FINISHED"}


class DroneAnimApplyLEDColorsOperator(Operator):
    bl_idname = "drones_anim.apply_led_colors"
    bl_label = "设置选中无人机的灯光颜色"
    bl_description = "为选中无人机直接设置或渐变至主颜色、副颜色或渐变色"
    bl_options = {"REGISTER", "UNDO"}

    primary_color: _color_property("主颜色")
    secondary_color: _color_property("副颜色", (0.0, 0.0, 0.0))
    color: EnumProperty(
        name="目标颜色",
        description="选择要应用的主颜色、副颜色或渐变色",
        items=(
            ("PRIMARY", "主颜色", "使用主颜色"),
            ("SECONDARY", "副颜色", "使用副颜色"),
            ("GRADIENT", "渐变色（主到副）", "按排序从主颜色过渡到副颜色"),
        ),
        default="PRIMARY",
    )
    fade: BoolProperty(
        name="渐变至目标颜色",
        default=False,
        description="从上一个颜色关键帧平滑过渡至当前帧的目标颜色",
    )
    gradient_mode: EnumProperty(
        name="渐变排序",
        description="选择无人机分配渐变颜色时的排序方式",
        items=(
            ("DEFAULT", "默认顺序", "按无人机的默认顺序分配渐变色"),
            ("RANDOM", "随机顺序", "随机排列无人机后分配渐变色"),
            ("X", "X 轴坐标", "按无人机的世界坐标 X 值排序"),
            ("Y", "Y 轴坐标", "按无人机的世界坐标 Y 值排序"),
            ("Z", "Z 轴坐标", "按无人机的世界坐标 Z 值排序"),
            ("DISTANCE", "距三维游标的距离", "按无人机到三维游标的距离排序"),
        ),
        default="DISTANCE",
    )

    @classmethod
    def poll(cls, context):
        return (
            context.scene is not None
            and context.mode == "OBJECT"
            and any(drone.select_get() for drone in get_drones(context.scene))
        )

    def invoke(self, context, event):
        props = context.scene.drones_anim_led
        self.primary_color = props.primary_color
        self.secondary_color = props.secondary_color
        if event.type == "LEFTMOUSE":
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        drones = [drone for drone in get_drones(context.scene) if drone.select_get()]
        if not drones:
            self.report({"ERROR"}, "请先选择本插件管理的无人机")
            return {"CANCELLED"}
        if self.color == "GRADIENT":
            if self.gradient_mode == "RANDOM":
                shuffle(drones)
            elif self.gradient_mode != "DEFAULT":
                graph = context.evaluated_depsgraph_get()
                positions = {
                    drone: drone.evaluated_get(graph).matrix_world.translation.copy()
                    for drone in drones
                }
                if self.gradient_mode == "DISTANCE":
                    cursor = context.scene.cursor.location
                    drones.sort(
                        key=lambda drone: (positions[drone] - cursor).length_squared
                    )
                else:
                    axis = "XYZ".index(self.gradient_mode)
                    drones.sort(key=lambda drone: positions[drone][axis])
        for index, drone in enumerate(drones):
            if self.color == "PRIMARY":
                color = self.primary_color
            elif self.color == "SECONDARY":
                color = self.secondary_color
            else:
                ratio = index / (len(drones) - 1) if len(drones) > 1 else 0.5
                color = tuple(
                    a * (1 - ratio) + b * ratio
                    for a, b in zip(self.primary_color, self.secondary_color)
                )
            insert_color(
                drone,
                color,
                context.scene.frame_current,
                step=not self.fade,
                frame_start=context.scene.frame_start,
            )
        _redraw()
        return {"FINISHED"}


class DroneAnimSampleUVColorsOperator(Operator):
    bl_idname = "drones_anim.sample_uv_colors"
    bl_label = "采样 UV 颜色并插入关键帧"
    bl_description = (
        "在动画播放头所在的当前帧采样 UV 颜色，并保持至下一个颜色关键帧；"
        "UV 接缝使用第一个相邻面的坐标"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.mode == "OBJECT"

    def execute(self, context):
        scene = context.scene
        props = scene.drones_anim_led
        drones = [
            drone
            for drone in get_drones(scene)
            if props.uv_scope == "ALL" or drone.select_get()
        ]
        if not drones:
            self.report({"ERROR"}, "所选范围内没有本插件管理的无人机")
            return {"CANCELLED"}
        frame = scene.frame_current + scene.frame_subframe
        try:
            colors = sample_attached_colors(
                drones,
                props.uv_image,
                context.evaluated_depsgraph_get(),
                uv_map=props.uv_map,
                repeat=props.uv_extension == "REPEAT",
            )
            for drone, color in colors:
                insert_color(
                    drone,
                    color,
                    frame,
                    step=False,
                    frame_start=scene.frame_start,
                    hold=True,
                )
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            _redraw()
        self.report(
            {"INFO"},
            f"已在第 {frame:g} 帧为 {len(colors)} 架无人机插入 UV 颜色关键帧",
        )
        return {"FINISHED"}


class DroneAnimLEDControlPanel(Panel):
    bl_idname = "OBJECT_PT_drones_anim_led_control"
    bl_label = "灯光控制"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "灯光控制"

    def draw(self, context):
        props = context.scene.drones_anim_led
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        shading = context.space_data.shading
        expected = {"MATERIALS": ("OBJECT", "OBJECT"), "MARKERS": ("THEME", "SINGLE")}
        wire, solid = expected.get(props.visualization, (None, None))
        if (
            shading.type == "WIREFRAME"
            and wire
            and shading.wireframe_color_type != wire
        ):
            color_name = {"OBJECT": "物体", "THEME": "主题"}[wire]
            layout.label(text=f"请将线框颜色设为「{color_name}」", icon="ERROR")
        elif shading.type == "SOLID" and solid and shading.color_type != solid:
            color_name = {"OBJECT": "物体", "SINGLE": "单一"}[solid]
            layout.label(text=f"请将实体视图颜色设为「{color_name}」", icon="ERROR")
        layout.prop(props, "visualization")
        if props.visualization == "MARKERS":
            layout.prop(props, "marker_size")
        row = layout.row()
        row.use_property_split = False
        row.prop(props, "primary_color", text="主颜色", icon="COLOR")
        row.operator(
            DroneAnimSwapLEDColorsOperator.bl_idname, text="", icon="ARROW_LEFTRIGHT"
        )
        row.prop(props, "secondary_color", text="副颜色", icon="COLOR")
        for fade, label in ((False, "应用"), (True, "渐变至")):
            row = layout.row()
            for color in ("PRIMARY", "SECONDARY"):
                operator = row.operator(
                    DroneAnimApplyLEDColorsOperator.bl_idname, text=label
                )
                operator.color, operator.fade = color, fade
                operator.primary_color = props.primary_color
                operator.secondary_color = props.secondary_color
        box = layout.box()
        box.label(text="UV 纹理采样", icon="TEXTURE")
        box.template_ID(props, "uv_image", open="image.open")
        box.prop(props, "uv_map")
        frame = context.scene.frame_current + context.scene.frame_subframe
        box.label(text=f"当前帧：{frame:g}", icon="TIME")
        box.prop(props, "uv_scope")
        box.prop(props, "uv_extension")
        box.label(text="UV 贴图留空：使用活动渲染 UV")
        box.label(text="UV 接缝：使用第一个相邻面")
        box.operator(DroneAnimSampleUVColorsOperator.bl_idname, icon="KEY_HLT")


def _draw_markers():
    import gpu
    from gpu_extras.batch import batch_for_shader

    context = bpy.context
    scene = context.scene
    if scene is None or context.region_data is None:
        return
    props = scene.drones_anim_led
    if props.visualization != "MARKERS" or not context.space_data.overlay.show_overlays:
        return
    graph = context.evaluated_depsgraph_get()
    drones = [
        drone.evaluated_get(graph) for drone in get_drones(scene) if drone.visible_get()
    ]
    if not drones:
        return
    shader = gpu.shader.from_builtin("POINT_FLAT_COLOR")
    batch = batch_for_shader(
        shader,
        "POINTS",
        {
            "pos": [tuple(drone.matrix_world.translation) for drone in drones],
            "color": [tuple(drone.color) for drone in drones],
        },
    )
    blend, depth = gpu.state.blend_get(), gpu.state.depth_test_get()
    try:
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.point_size_set(props.marker_size)
        shader.bind()
        batch.draw(shader)
    finally:
        gpu.state.point_size_set(1)
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


@persistent
def _on_frame(scene, depsgraph=None):
    _redraw()


@persistent
def _on_load(_unused):
    for scene in bpy.data.scenes:
        props = scene.drones_anim_led
        update_emission(scene, props.emission_strength)
    _visualization_changed(bpy.context.scene.drones_anim_led, bpy.context)


def register():
    global _draw_handle
    Scene.drones_anim_led = PointerProperty(type=DroneAnimLEDProperties)
    if _on_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_on_frame)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_markers, (), "WINDOW", "POST_VIEW"
        )


def unregister():
    global _draw_handle
    if _on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_on_frame)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        _draw_handle = None
    if hasattr(Scene, "drones_anim_led"):
        del Scene.drones_anim_led
