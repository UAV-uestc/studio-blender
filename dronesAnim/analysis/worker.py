"""Evaluate a scene snapshot and compute motion curves in a separate Blender."""

import importlib.util
import json
import os
import pickle
import sys
import traceback
from pathlib import Path
from time import perf_counter, sleep


def main(request_path):
    import bpy

    directory = Path(request_path).parent
    progress = 0.0
    last_update = 0.0

    def publish(state="running", error=None, force=False):
        nonlocal last_update
        now = perf_counter()
        if not force and now - last_update < 0.1:
            return
        temporary = directory / "progress.tmp"
        temporary.write_text(
            json.dumps({"state": state, "progress": progress, "error": error}), encoding="utf-8",
        )
        for attempt in range(10):
            try:
                os.replace(temporary, directory / "progress.json")
                break
            except PermissionError:
                # Windows may briefly hold the old file open during a UI poll.
                if not force:
                    return
                if attempt == 9:
                    raise
                sleep(0.01)
        last_update = now

    try:
        publish(force=True)
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        # Load only the numerical module, without registering the parent add-on.
        spec = importlib.util.spec_from_file_location(
            "drone_motion_worker_math", Path(__file__).with_name("numerics.py"),
        )
        math_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = math_module
        spec.loader.exec_module(math_module)
        bpy.ops.wm.open_mainfile(filepath=request["snapshot"], load_ui=False, use_scripts=False)
        scene = bpy.data.scenes[request["scene"]]
        view_layer = scene.view_layers[request["view_layer"]]
        drones = [scene.objects[name] for name in request["names"]]
        drivers = [(block.name, curve)
                   for blocks in (bpy.data.objects, bpy.data.shape_keys, bpy.data.scenes)
                   for block in blocks if block.animation_data is not None
                   for curve in block.animation_data.drivers]
        builder = math_module.AnalysisBuilder(
            request["start"], request["end"], request["fps"], request["names"],
        )
        with bpy.context.temp_override(scene=scene, view_layer=view_layer):
            count = builder.end - builder.start + 1
            for index, frame in enumerate(range(builder.start, builder.end + 1)):
                scene.frame_set(frame)
                depsgraph = view_layer.depsgraph
                points = [tuple(obj.evaluated_get(depsgraph).matrix_world.translation) for obj in drones]
                if bpy.app.autoexec_fail:
                    raise ValueError("快照需要自定义脚本驱动，后台无法求值：" + bpy.app.autoexec_fail_message)
                invalid = next((name for name, curve in drivers
                                if not curve.is_valid or not curve.driver.is_valid), None)
                if invalid is not None:
                    raise ValueError(f"快照中的驱动无法求值：{invalid}，请检查自定义驱动函数或插件依赖")
                builder.append(points)
                progress = 0.8 * (index + 1) / count
                publish()
            for step in builder.finish_steps():
                progress = 0.8 + 0.2 * step
                publish()
        result = builder.result
        plots = tuple((p.name, p.unit, p.curves, p.lower, p.upper, p.minimum, p.maximum)
                      for p in result.plots)
        data = (result.start, result.end, result.fps, result.names, plots,
                result.closest_frame, result.closest_pair)
        temporary = directory / "result.tmp"
        with temporary.open("wb") as stream:
            pickle.dump(data, stream, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, directory / "result.bin")
        progress = 1.0
        publish("done", force=True)
    except Exception as exc:
        traceback.print_exc()
        publish("error", str(exc), force=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1])
