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
)
from bpy.types import Operator, Panel, PropertyGroup, Scene

from .led_animation import get_drones, insert_color, update_emission

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
        name=name, subtype="COLOR", size=3, min=0, max=1, default=default
    )


class DroneAnimLEDProperties(PropertyGroup):
    visualization: EnumProperty(
        name="Visualization",
        items=(
            ("NONE", "None", "Disable LED viewport markers"),
            ("MARKERS", "Markers", "Fast colored viewport markers"),
            (
                "MATERIALS",
                "Materials",
                "Display animated object colors and LED shaders",
            ),
        ),
        default="MATERIALS",
        update=_visualization_changed,
    )
    marker_size: IntProperty(name="Marker size", min=1, soft_max=50, default=25)
    primary_color: _color_property("Primary color")
    secondary_color: _color_property("Secondary color", (0.0, 0.0, 0.0))
    emission_strength: FloatProperty(
        name="Emission",
        min=0,
        soft_max=5,
        default=1,
        update=_emission_changed,
    )


class DroneAnimSwapLEDColorsOperator(Operator):
    bl_idname = "drones_anim.swap_led_colors"
    bl_label = "Swap LED Colors"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.drones_anim_led
        primary, secondary = tuple(props.primary_color), tuple(props.secondary_color)
        props.primary_color, props.secondary_color = secondary, primary
        return {"FINISHED"}


class DroneAnimApplyLEDColorsOperator(Operator):
    bl_idname = "drones_anim.apply_led_colors"
    bl_label = "Apply Colors to Selected Drones"
    bl_description = "Apply or fade to primary, secondary or gradient LED colors"
    bl_options = {"REGISTER", "UNDO"}

    primary_color: _color_property("Primary color")
    secondary_color: _color_property("Secondary color", (0.0, 0.0, 0.0))
    color: EnumProperty(
        name="Color to apply",
        items=(
            ("PRIMARY", "Primary", ""),
            ("SECONDARY", "Secondary", ""),
            ("GRADIENT", "Gradient (1 -> 2)", ""),
        ),
        default="PRIMARY",
    )
    fade: BoolProperty(
        name="Fade to color",
        default=False,
        description="Fade from the previous keyframe rather than stepping at this frame",
    )
    gradient_mode: EnumProperty(
        name="Order in gradient",
        items=(
            ("DEFAULT", "Default", ""),
            ("RANDOM", "Random", ""),
            ("X", "X coordinate", ""),
            ("Y", "Y coordinate", ""),
            ("Z", "Z coordinate", ""),
            ("DISTANCE", "Distance from 3D cursor", ""),
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
            self.report({"ERROR"}, "Select some dronesAnim drones first")
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


class DroneAnimLEDControlPanel(Panel):
    bl_idname = "OBJECT_PT_drones_anim_led_control"
    bl_label = "LED Control"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "LED Control"

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
            layout.label(text=f"Set Wireframe Color to {wire}", icon="ERROR")
        elif shading.type == "SOLID" and solid and shading.color_type != solid:
            layout.label(text=f"Set Object Color to {solid}", icon="ERROR")
        layout.prop(props, "visualization")
        if props.visualization == "MARKERS":
            layout.prop(props, "marker_size")
        row = layout.row()
        row.use_property_split = False
        row.prop(props, "primary_color", text="Primary", icon="COLOR")
        row.operator(
            DroneAnimSwapLEDColorsOperator.bl_idname, text="", icon="ARROW_LEFTRIGHT"
        )
        row.prop(props, "secondary_color", text="Secondary", icon="COLOR")
        for fade, label in ((False, "Apply"), (True, "Fade to")):
            row = layout.row()
            for color in ("PRIMARY", "SECONDARY"):
                operator = row.operator(
                    DroneAnimApplyLEDColorsOperator.bl_idname, text=label
                )
                operator.color, operator.fade = color, fade
                operator.primary_color = props.primary_color
                operator.secondary_color = props.secondary_color


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
