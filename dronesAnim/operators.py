from pathlib import Path

import bpy
from bpy.props import FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Context, Operator, Panel, PropertyGroup, Scene
from bpy_extras.io_utils import ExportHelper

from .drones import create_drone, create_drone_mesh, drone_names, get_drone_collection
from .mat_export import export_mat
from .sampling import pattern_studio_sampling_plan

__all__ = ()


class DroneAnimPanelProperties(PropertyGroup):
    curve_point_count: IntProperty(name="顶点总数", default=100, min=2, max=10000)
    curve_point_spacing: FloatProperty(
        name="间距", description="沿曲线的目标间距，保留端点后均分，实际间距通常略大",
        default=2.0, min=0.001, unit="LENGTH",
    )
    drone_radius: FloatProperty(
        name="无人机半径",
        description="新建无人机的半径",
        default=0.5,
        min=0.001,
        unit="LENGTH",
        subtype="DISTANCE",
    )

    drone_subdivisions: IntProperty(
        name="细分等级", description="新生成无人机球体的 Icosphere 细分等级",
        default=2, min=1, max=5, soft_max=3,
    )

    start_frame: IntProperty(
        name="起始帧",
        description="导出起始帧，使用场景时间轴帧号",
        default=1,
        min=0,
    )

    end_frame: IntProperty(
        name="终止帧",
        description="导出终止帧，使用场景时间轴帧号，不包含此帧",
        default=250,
        min=0,
    )


class AttachDronesToVerticesOperator(Operator):
    bl_idname = "drones_anim.attach_drones_to_vertices"
    bl_label = "无人机附着顶点"
    bl_description = "为网格顶点附着无人机；已有附着时删除所有无人机，再次点击重新附着"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context) -> bool:
        return context.scene is not None and any(
            obj.type == "MESH" for obj in context.scene.objects
        )

    def execute(self, context: Context) -> set[str]:
        scene = context.scene
        existing_collection = bpy.data.collections.get("Drones")
        existing_drones = (
            set(existing_collection.all_objects) if existing_collection else set()
        )
        attachments = [
            constraint
            for drone in existing_drones
            for constraint in drone.constraints
            if drone.type == "MESH"
            and constraint.type == "COPY_LOCATION"
            and constraint.name == "Drone Anim Vertex"
            and constraint.target is not None
            and constraint.subtarget
        ]
        if attachments:
            previous_mode = context.mode
            if previous_mode == "EDIT_MESH":
                bpy.ops.object.mode_set(mode="OBJECT")
            try:
                groups = {
                    (constraint.target, constraint.subtarget)
                    for constraint in attachments
                }
                drones = [obj for obj in existing_drones if obj.type == "MESH"]
                meshes = {drone.data for drone in drones}
                for drone in drones:
                    bpy.data.objects.remove(drone, do_unlink=True)
                for obj, group_name in groups:
                    group = obj.vertex_groups.get(group_name)
                    if group is not None:
                        obj.vertex_groups.remove(group)
                for mesh in meshes:
                    if mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
                self.report(
                    {"INFO"}, f"已删除 {len(drones)} 架无人机，再次点击可重新附着"
                )
            finally:
                if previous_mode == "EDIT_MESH" and context.active_object is not None:
                    bpy.ops.object.mode_set(mode="EDIT")
            return {"FINISHED"}

        sources = [
            obj
            for obj in scene.objects
            if obj.type == "MESH"
            and obj not in existing_drones
            and len(obj.data.vertices) > 0
        ]
        if not sources:
            self.report({"WARNING"}, "场景中没有可附着的网格顶点")
            return {"CANCELLED"}

        # Vertex-group assignments are unavailable in mesh edit mode.
        previous_mode = context.mode
        if previous_mode == "EDIT_MESH":
            bpy.ops.object.mode_set(mode="OBJECT")

        try:
            pending_by_object = {
                obj: [vertex.index for vertex in obj.data.vertices]
                for obj in sources
            }
            count = sum(len(indices) for indices in pending_by_object.values())
            if not count:
                self.report({"WARNING"}, "场景中没有可附着的网格顶点")
                return {"CANCELLED"}

            depsgraph = context.evaluated_depsgraph_get()
            positions_by_object = {}
            for obj, indices in pending_by_object.items():
                if not indices:
                    continue
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    if len(mesh.vertices) != len(obj.data.vertices):
                        self.report(
                            {"ERROR"},
                            f"{obj.name}: 修改器改变了顶点数量，无法可靠地按顶点索引附着",
                        )
                        return {"CANCELLED"}
                    positions_by_object[obj] = {
                        index: evaluated.matrix_world @ mesh.vertices[index].co
                        for index in indices
                    }
                finally:
                    evaluated.to_mesh_clear()

            drone_collection = get_drone_collection(scene)
            template_mesh = create_drone_mesh(
                radius=scene.drones_anim_panel.drone_radius,
                subdivisions=scene.drones_anim_panel.drone_subdivisions,
            )
            try:
                names = iter(tuple(drone_names(count)))
                for obj, positions in positions_by_object.items():
                    for index, position in positions.items():
                        # One vertex per group follows the evaluated mesh.
                        group = obj.vertex_groups.new(
                            name=f"Drone Anim Vertex {index}"
                        )
                        group.add([index], 1.0, "REPLACE")
                        drone = create_drone(
                            location=position,
                            name=next(names),
                            template_mesh=template_mesh,
                            collection=drone_collection,
                            frame_start=scene.frame_start,
                        )
                        drone["drones_anim_vertex_index"] = index
                        constraint = drone.constraints.new(type="COPY_LOCATION")
                        constraint.name = "Drone Anim Vertex"
                        constraint.target = obj
                        constraint.subtarget = group.name
                        constraint.target_space = "WORLD"
                        constraint.owner_space = "WORLD"
                        constraint.use_offset = False
                        constraint.influence = 1.0
            finally:
                bpy.data.meshes.remove(template_mesh)

            self.report({"INFO"}, f"已创建并附着 {count} 架无人机")
        finally:
            if previous_mode == "EDIT_MESH":
                bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class ExportMATOperator(Operator, ExportHelper):
    bl_idname = "drones_anim.export_mat"
    bl_label = "导出MAT"
    bl_description = "导出MAT格式文件"
    filename_ext = ".mat"
    filter_glob: StringProperty(default="*.mat", options={"HIDDEN"})

    @classmethod
    def poll(cls, context: Context) -> bool:
        if context.scene is None:
            return False
        props = context.scene.drones_anim_panel
        if props.end_frame <= props.start_frame:
            cls.poll_message_set("终止帧必须大于起始帧（不包含终止帧）")
            return False
        return True

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = (
                str(Path(bpy.data.filepath).with_suffix(".mat"))
                if bpy.data.filepath else "drone_show.mat"
            )
        return ExportHelper.invoke(self, context, event)

    def execute(self, context: Context) -> set[str]:
        try:
            result = export_mat(context, self.filepath)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            self.report({"ERROR"}, f"MAT导出失败: {exc}")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"已导出 {result.drone_count} 架无人机，{result.plan.count} 帧，{result.plan.fps} fps: {result.destination}",
        )
        return {"FINISHED"}


class DroneAnimAttachmentHelpOperator(Operator):
    bl_idname = "drones_anim.attachment_help"
    bl_label = "无人机附着帮助"
    bl_description = "查看生成参数、顶点附着和重新生成的说明"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=440)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="在源网格的每个顶点创建一架无人机。")
        layout.label(text="处理范围为当前场景的全部网格，隐藏网格也会参与。")
        layout.separator()
        layout.label(text="半径：新生成球体的大小，直径为半径的两倍。")
        layout.label(text="细分等级：数值越高，球体越圆，模型开销越大。")
        layout.label(text="这两个参数在生成无人机时应用。")
        layout.separator()
        layout.label(text="生成后编辑源网格顶点，无人机会跟随对应位置。")
        layout.label(text="已有顶点附着时，再点附着按钮会删除整组无人机。", icon="ERROR")
        layout.label(text="删除会一并移除这些无人机的位置与颜色关键帧。")
        layout.label(text="清除后再次点击，按当前网格顶点重新生成。")


class DroneAnimPanel(Panel):
    bl_idname = "OBJECT_PT_drones_anim_panel"
    bl_label = "无人机动画"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "无人机动画"

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.drones_anim_panel

        box = layout.box()
        header = box.row(align=True)
        header.label(text="无人机附着")
        header.operator("drones_anim.attachment_help", text="", icon="QUESTION", emboss=False)
        row = box.row(align=True)
        row.prop(props, "drone_radius", text="半径")
        row.prop(props, "drone_subdivisions", text="细分等级")
        box.operator(
            AttachDronesToVerticesOperator.bl_idname,
            text="无人机附着顶点",
        )

        box = layout.box()
        box.label(text="曲线生成 Mesh")
        row = box.row(align=True)
        row.prop(props, "curve_point_count", text="顶点数")
        by_count = row.operator("drones_anim.curves_to_mesh", text="生成 Mesh")
        by_count.mode = "COUNT"
        row = box.row(align=True)
        row.prop(props, "curve_point_spacing", text="间距")
        by_distance = row.operator("drones_anim.curves_to_mesh", text="生成 Mesh")
        by_distance.mode = "DISTANCE"


class DroneAnimMATExportPanel(Panel):
    bl_idname = "OBJECT_PT_drones_anim_mat_export"
    bl_label = "导出 MAT"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "无人机动画"
    bl_order = 10

    def draw(self, context: Context) -> None:
        layout = self.layout
        props = context.scene.drones_anim_panel

        layout.prop(props, "start_frame", text="起始帧")
        layout.prop(props, "end_frame", text="结束帧（不包含）")
        layout.label(text="Pattern Studio · 固定 30 fps")
        try:
            plan = pattern_studio_sampling_plan(
                props.start_frame, props.end_frame,
                context.scene.render.fps / context.scene.render.fps_base,
            )
            layout.label(text=f"共 {plan.count} 帧 / {plan.duration:.6g} 秒")
        except ValueError as exc:
            layout.label(text=str(exc), icon="ERROR")

        layout.operator(
            ExportMATOperator.bl_idname,
            text="导出MAT",
        )


def register() -> None:
    Scene.drones_anim_panel = PointerProperty(type=DroneAnimPanelProperties)


def unregister() -> None:
    if hasattr(Scene, "drones_anim_panel"):
        del Scene.drones_anim_panel
