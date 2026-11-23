"""Run with Blender --background --factory-startup --python-exit-code 1.

Synthetic decoded video frames isolate LED composition from GUI-only decoding.
The resulting MAT is independently readable by SciPy outside Blender.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dronesAnim
from dronesAnim import led_control, mat_export, safety, video_projection
from dronesAnim.light_effects import runtime
from dronesAnim.sampling import sampling_plan


def main():
    output = ROOT / "tmp" / "non-numba-merge"
    output.mkdir(parents=True, exist_ok=True)
    handlers_before = {name: tuple(getattr(bpy.app.handlers, name))
                       for name, _callback in runtime._HANDLERS}
    dronesAnim.register()
    try:
        scene = bpy.context.scene
        scene.render.fps = 24
        collection = bpy.data.collections.new("Drones")
        scene.collection.children.link(collection)
        parent = bpy.data.objects.new("MergeTestParent", None)
        scene.collection.objects.link(parent)
        parent.location = (10, 0, 0)
        for name, x in (("Drone10", 1), ("Drone2", -1)):
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata([(0, 0, 0)], [], [])
            obj = bpy.data.objects.new(name, mesh)
            collection.objects.link(obj)
            obj["drones_anim_vertex_index"] = int(name[5:])
            obj.parent = parent
            obj.location = (x, 0, 3)
            obj.color = (1, 0, 0, 1)
        led = scene.drones_anim_led
        led.visualization = "MARKERS"
        stack = scene.drones_anim_light_effects
        effect = stack.append_effect("Procedural green")
        effect.frame_start, effect.duration = 1, 3
        for element in effect.texture.color_ramp.elements:
            element.color = (0, 1, 0, 1)
        drones = mat_export._export_drones(scene)
        assert [drone.name for drone in drones] == ["Drone2", "Drone10"]
        video = stack.append_effect("Synthetic blue video", effect_type="VIDEO")
        video.name = "Synthetic blue video"
        video.frame_start, video.video_duration = 1, 2
        video.video_view_captured = True
        video.video_view_matrix = tuple(np.eye(4).ravel())
        video.video_viewport_size = (100, 100)
        texture = bpy.data.textures.new("SyntheticVideo", type="IMAGE")
        texture["drones_anim_video_fps"] = 24.0
        video.video_texture = texture
        video.video_targets.add().drone = drones[0]
        video.enabled = True
        decoded = video_projection.VideoFrame(
            np.array([[[0, 0, 1, 1]]], dtype=np.float32), 1.0, False)
        expected_rgb = np.array([
            [[0, 0, 1], [0, 1, 0]],
            [[0, 0, 1], [0, 1, 0]],
            [[0, 1, 0], [0, 1, 0]],
            [[1, 0, 0], [1, 0, 0]],
        ])
        with patch.object(video_projection, "read_video_frame", return_value=decoded):
            for frame in range(1, 5):
                scene.frame_set(frame)
                graph = bpy.context.evaluated_depsgraph_get()
                ordered, colors, errors = runtime.final_colors(scene, graph)
                assert not errors, errors
                assert tuple(ordered) == tuple(drones)
                np.testing.assert_allclose(colors[:, :3], expected_rgb[frame - 1])
                positions, marker_colors = led_control.marker_samples(scene, graph)
                np.testing.assert_allclose(positions, [(9, 0, 3), (11, 0, 3)])
                np.testing.assert_allclose(np.array(marker_colors)[:, :3], colors[:, :3])
                # Material publication uses the same composited colors and leaves originals intact.
                runtime.final_colors(scene, graph, publish=True)
                np.testing.assert_allclose([obj.evaluated_get(graph).color[:3] for obj in drones],
                                           expected_rgb[frame - 1])
                np.testing.assert_allclose([obj.color[:3] for obj in drones], [(1, 0, 0)] * 2)
                runtime.restore_scene(scene, graph)
            stack.enabled = False
            scene.frame_set(1)
            graph = bpy.context.evaluated_depsgraph_get()
            ordered, colors, errors = runtime.final_colors(scene, graph)
            assert not errors, errors
            np.testing.assert_allclose(colors[:, :3], [(1, 0, 0), (1, 0, 0)])
            stack.enabled = True
            scene.frame_set(7, subframe=0.25)
            plan = sampling_plan(1, 4)
            samples = mat_export._sample_drones(bpy.context, drones, plan)
            assert scene.frame_current == 7 and scene.frame_subframe == 0.25
            again = mat_export._sample_drones(bpy.context, drones, plan)
            assert samples == again
            rgb = np.stack([np.array(samples[ch]).reshape(4, 2) for ch in "rgb"], axis=-1)
            np.testing.assert_allclose(rgb, expected_rgb * 255)
            mat_export._write_mat_file(output / "combined.mat", 2, plan.count, samples)
            # A video validation error must stop export and restore the caller's frame.
            video.video_view_captured = False
            led_control._video_changed(video, bpy.context)
            try:
                mat_export._sample_drones(bpy.context, drones, plan)
            except ValueError as error:
                assert "Synthetic blue video" in str(error)
            else:
                raise AssertionError("Invalid video did not fail sampling")
            assert scene.frame_current == 7 and scene.frame_subframe == 0.25
            assert safety._scanning is False
            # Native video decoding/export retains its existing GUI requirement.
            try:
                video_projection.validate_video_export(bpy.context, plan)
            except ValueError:
                pass
            else:
                raise AssertionError("Background video export was not rejected")
            video.enabled = False
            scene.drones_anim_panel.start_frame = 1
            scene.drones_anim_panel.end_frame = 4
            scene.drones_anim_panel.sampling_mode = "SCENE"
            mat_export.export_mat(bpy.context, str(output / "procedural.mat"))
        # Video property lifecycle, operator registration, and clean unload/reload.
        assert bpy.ops.drones_anim.open_led_video.get_rna_type()
        assert bpy.ops.drones_anim.light_effect_stack.get_rna_type()
    finally:
        dronesAnim.unregister()
    for name in ("drones_anim_led", "drones_anim_light_effects", "drones_anim_panel"):
        assert not hasattr(bpy.types.Scene, name), name
    for name, callbacks in handlers_before.items():
        assert tuple(getattr(bpy.app.handlers, name)) == callbacks, name
    dronesAnim.register()
    dronesAnim.unregister()
    report = {"blender": bpy.app.version_string, "status": "passed",
              "coverage": ["register/unregister twice", "operator IDs", "property lifecycle",
                           "procedural and video composition", "video target scope",
                           "video end boundary", "world transforms", "marker/material parity",
                           "deterministic MAT sampling", "frame restoration",
                           "invalid video rejection", "background video export gate"]}
    (output / "smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("NON_NUMBA_MERGE_SMOKE_PASSED", json.dumps(report))


if __name__ == "__main__":
    main()
