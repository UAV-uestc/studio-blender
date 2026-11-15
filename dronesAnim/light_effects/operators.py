"""Local effect stack and explicit custom-file approval operators."""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from .functions import discover_functions
from .io import export_file, import_file
from .runtime import invalidate, trust_store, update_scene

FUNCTION_SLOTS = (
    ("color_function", "颜色函数", ""),
    ("output_function", "X 输出函数", ""),
    ("output_function_y", "Y 输出函数", ""),
)


def refresh(scene, *, images=False):
    invalidate(scene, images=images)
    scene.update_tag()
    update_scene(scene, bpy.context.evaluated_depsgraph_get())
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


class DroneAnimLightStackOperator(Operator):
    bl_idname = "drones_anim.light_effect_stack"
    bl_label = "编辑灯光效果"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=(
        ("ADD", "新建", ""), ("REMOVE", "删除", ""),
        ("DUPLICATE", "复制", ""), ("UP", "上移", ""), ("DOWN", "下移", ""),
        ("START", "起始设为当前帧", ""), ("END", "结束设为当前帧", ""),
    ))

    @classmethod
    def poll(cls, context):
        return context.scene is not None and hasattr(context.scene, "drones_anim_light_effects")

    def execute(self, context):
        stack = context.scene.drones_anim_light_effects
        if self.action == "ADD":
            effect = stack.append_effect("灯光效果")
            effect.frame_start = context.scene.frame_current
        elif stack.active_entry is None:
            return {"CANCELLED"}
        elif self.action == "REMOVE":
            stack.remove_active()
        elif self.action == "DUPLICATE":
            stack.duplicate_active()
        elif self.action in {"UP", "DOWN"}:
            stack.move_active(-1 if self.action == "UP" else 1)
        elif self.action == "START":
            stack.active_entry.frame_start = context.scene.frame_current
        elif self.action == "END":
            stack.active_entry.frame_end = context.scene.frame_current
        refresh(context.scene)
        return {"FINISHED"}


class DroneAnimRefreshLightImagesOperator(Operator):
    bl_idname = "drones_anim.refresh_light_images"
    bl_label = "刷新图像缓存"
    bl_description = "清除效果图像缓存并重新计算当前帧"

    def execute(self, context):
        refresh(context.scene, images=True)
        return {"FINISHED"}


class DroneAnimLightFunctionOperator(Operator):
    bl_idname = "drones_anim.light_effect_function"
    bl_label = "自定义函数文件"
    bl_description = "列出函数不执行文件；批准后 Python 拥有完整 Blender 权限"

    action: EnumProperty(items=(
        ("DISCOVER", "列出函数", ""), ("APPROVE", "批准执行", ""),
        ("REVOKE", "撤销批准", ""),
    ))
    slot: EnumProperty(items=FUNCTION_SLOTS, default="color_function")

    @classmethod
    def poll(cls, context):
        stack = getattr(context.scene, "drones_anim_light_effects", None)
        return stack is not None and stack.active_entry is not None

    def invoke(self, context, event):
        if self.action == "APPROVE":
            return context.window_manager.invoke_props_dialog(self, width=440)
        return self.execute(context)

    def draw(self, context):
        self.layout.label(text="批准的 Python 未被沙箱隔离。", icon="ERROR")
        self.layout.label(text="它可以访问 Blender、文件和网络。")
        self.layout.label(text="仅批准您信任的文件内容；修改后需重新批准。")
        effect = context.scene.drones_anim_light_effects.active_entry
        self.layout.label(text=getattr(effect, self.slot).path)

    def execute(self, context):
        effect = context.scene.drones_anim_light_effects.active_entry
        properties = getattr(effect, self.slot)
        if not properties.path:
            self.report({"ERROR"}, f"{effect.name}: 请先选择函数文件")
            return {"CANCELLED"}
        path = bpy.path.abspath(properties.path)
        try:
            if self.action == "DISCOVER":
                names = discover_functions(path)
                if not properties.name and names:
                    properties.name = names[0]
                self.report({"INFO"}, f"{effect.name}: " + (", ".join(names) or "没有顶层函数"))
                return {"FINISHED"}
            elif self.action == "APPROVE":
                trust_store().approve(path)
            else:
                trust_store().revoke(path)
        except (OSError, SyntaxError, ValueError) as exc:
            self.report({"ERROR"}, f"{effect.name}: {exc}")
            return {"CANCELLED"}
        refresh(context.scene)
        return {"FINISHED"}


class DroneAnimLightJSONOperator(Operator):
    bl_idname = "drones_anim.light_effect_json"
    bl_label = "灯光效果 JSON"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=(("IMPORT", "导入", ""), ("EXPORT", "导出", "")))
    filepath: StringProperty(name="文件", subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})
    export_filter: EnumProperty(name="导出范围", items=(
        ("ALL", "全部效果", ""), ("ENABLED", "已启用效果", ""), ("SELECTED", "当前选中效果", ""),
    ))
    import_filter: EnumProperty(name="导入范围", items=(
        ("ALL", "全部效果", ""), ("ENABLED", "已启用效果", ""),
    ))

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        self.layout.prop(self, "export_filter" if self.action == "EXPORT" else "import_filter")
        self.layout.label(text="图像、网格和集合仅保存名称引用。")
        self.layout.label(text="导入不会批准 Python 文件。", icon="LOCKED")

    def execute(self, context):
        if not self.filepath:
            self.report({"ERROR"}, "请选择 JSON 文件")
            return {"CANCELLED"}
        try:
            if self.action == "EXPORT":
                path = self.filepath
                if not path.lower().endswith(".json"):
                    path += ".json"
                count = export_file(context.scene.drones_anim_light_effects, path, filter=self.export_filter)
                self.report({"INFO"}, f"已导出 {count} 个灯光效果")
            else:
                count, warnings = import_file(context.scene, self.filepath, filter=self.import_filter)
                for warning in warnings:
                    self.report({"WARNING"}, warning)
                self.report({"INFO"}, f"已导入 {count} 个灯光效果")
                refresh(context.scene, images=True)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}
