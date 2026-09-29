import bpy
from bpy.props import IntProperty, PointerProperty
from bpy.types import Context, Operator, Panel, PropertyGroup, Scene

__all__ = ()


class DroneAnimPanelProperties(PropertyGroup):
    start_frame: IntProperty(
        name="起始帧",
        description="动画的起始帧",
        default=1,
        min=0,
    )

    end_frame: IntProperty(
        name="终止帧",
        description="动画的终止帧",
        default=250,
        min=0,
    )


class AttachDronesToVerticesOperator(Operator):
    bl_idname = "drones_anim.attach_drones_to_vertices"
    bl_label = "无人机附着顶点"
    bl_description = "将无人机附着到所选对象的顶点上"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.active_object is not None

    def execute(self, context: Context) -> set[str]:
        self.report({"INFO"}, "无人机附着顶点: 功能待实现")
        return {"FINISHED"}


class ExportMATOperator(Operator):
    bl_idname = "drones_anim.export_mat"
    bl_label = "导出MAT"
    bl_description = "导出MAT格式文件"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return True

    def execute(self, context: Context) -> set[str]:
        props = context.scene.drones_anim_panel
        start_frame = props.start_frame
        end_frame = props.end_frame
        self.report(
            {"INFO"},
            f"导出MAT: 起始帧={start_frame}, 终止帧={end_frame}",
        )
        return {"FINISHED"}


class DroneAnimPanel(Panel):
    bl_idname = "OBJECT_PT_drones_anim_panel"
    bl_label = "Drone Animations"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Drone Anim"

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.drones_anim_panel

        layout.operator(
            AttachDronesToVerticesOperator.bl_idname,
            text="无人机附着顶点",
        )

        layout.separator()

        layout.prop(props, "start_frame", text="起始帧")
        layout.prop(props, "end_frame", text="终止帧")

        layout.operator(
            ExportMATOperator.bl_idname,
            text="导出MAT",
        )


def register() -> None:
    Scene.drones_anim_panel = PointerProperty(type=DroneAnimPanelProperties)


def unregister() -> None:
    if hasattr(Scene, "drones_anim_panel"):
        del Scene.drones_anim_panel
