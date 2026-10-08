import bpy
from bpy.props import FloatProperty, IntProperty, PointerProperty
from bpy.types import Context, Operator, Panel, PropertyGroup, Scene

from .drones import create_drone, create_drone_mesh, drone_names, get_drone_collection

__all__ = ()


class DroneAnimPanelProperties(PropertyGroup):
    drone_radius: FloatProperty(
        name="无人机半径",
        description="新建无人机的半径",
        default=0.5,
        min=0.001,
        unit="LENGTH",
        subtype="DISTANCE",
    )

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
    bl_description = "为场景中每个网格顶点创建无人机，并使其始终跟随顶点动画"
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
            # Only drones created by this operator count as existing bindings.
            bindings = {
                (constraint.target, drone.get("drones_anim_vertex_index"))
                for drone in existing_drones
                for constraint in drone.constraints
                if constraint.type == "COPY_LOCATION"
                and constraint.name == "Drone Anim Vertex"
                and constraint.target is not None
                and constraint.subtarget
            }
            pending_by_object = {
                obj: [
                    vertex.index
                    for vertex in obj.data.vertices
                    if (obj, vertex.index) not in bindings
                ]
                for obj in sources
            }
            count = sum(len(indices) for indices in pending_by_object.values())
            if not count:
                self.report({"INFO"}, "所有网格顶点均已附着无人机")
                return {"FINISHED"}

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
            template_mesh = create_drone_mesh(radius=scene.drones_anim_panel.drone_radius)
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

        layout.prop(props, "drone_radius", text="无人机半径")
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
