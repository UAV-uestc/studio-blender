"""Isolated regression checks for shared video/procedural lists and legacy migration."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dronesAnim
from dronesAnim import led_control, video_projection
from dronesAnim.light_effects import io, migration, runtime, ui


def rgb(scene):
    runtime.invalidate(scene)
    drones, colors, errors = runtime.final_colors(scene, bpy.context.evaluated_depsgraph_get())
    assert not errors, errors
    return colors[:, :3]


def setup_video(effect, texture):
    effect.video_texture = texture
    effect.video_duration = 3
    effect.video_view_captured = True
    effect.video_view_matrix = tuple(np.eye(4).ravel())
    effect.video_viewport_size = (100, 100)


class LayoutProbe:
    """Exercise conditional panel draw paths without a visible window."""
    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []
    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return LayoutProbe(self.calls)
        return call


def main():
    dronesAnim.register()
    try:
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.drones_anim_led.visualization = "MARKERS"
        collection = bpy.data.collections.new("Drones")
        scene.collection.children.link(collection)
        mesh = bpy.data.meshes.new("UnifiedDrone")
        mesh.from_pydata([(0, 0, 0)], [], [])
        drone = bpy.data.objects.new("UnifiedDrone", mesh)
        collection.objects.link(drone)
        drone["drones_anim_vertex_index"] = 0
        drone.location = (0, 0, 3)
        drone.color = (1, 0, 0, 1)
        stack = scene.drones_anim_light_effects
        green = stack.append_effect("Green")
        green.frame_start, green.duration = 1, 3
        for stop in green.texture.color_ramp.elements:
            stop.color = (0, 1, 0, 1)
        assert bpy.ops.drones_anim.light_effect_stack(action="ADD_VIDEO") == {"FINISHED"}
        video = stack.active_entry
        video.name = "Blue video"
        video.frame_start = 1
        texture = bpy.data.textures.new("UnifiedVideo", type="IMAGE")
        texture["drones_anim_video_fps"] = 24.0
        texture["drones_anim_video"] = True
        setup_video(video, texture)
        video.video_targets.add().drone = drone
        decoded = video_projection.VideoFrame(np.array([[[0, 0, 1, 1]]], dtype=np.float32), 1, False)
        with patch.object(video_projection, "read_video_frame", return_value=decoded):
            scene.frame_set(1)
            np.testing.assert_allclose(rgb(scene), [(0, 0, 1)])
            bpy.ops.drones_anim.light_effect_stack(action="DOWN")
            np.testing.assert_allclose(rgb(scene), [(0, 1, 0)])
            bpy.ops.drones_anim.light_effect_stack(action="UP")
            video = stack.active_entry
            video.influence = 0.5
            np.testing.assert_allclose(rgb(scene), [(0, 0.5, 0.5)])
            video.enabled = False
            np.testing.assert_allclose(rgb(scene), [(0, 1, 0)])
            video.enabled = True
            video.influence = 1
            video.video_end_locked = False
            video.frame_end = 7
            assert video.duration == 7
            assert video_projection.video_source_frame(video, 4) == 1
            bpy.ops.drones_anim.light_effect_stack(action="DUPLICATE")
            copy = stack.active_entry
            assert copy.type == "VIDEO" and copy.duration == 7
            assert copy.video_texture == texture and copy.video_targets[0].drone == drone
            bpy.ops.drones_anim.light_effect_stack(action="REMOVE")
            assert texture.name in bpy.data.textures
            video = next(entry for entry in stack.entries if entry.type == "VIDEO")
            video.video_end_locked = True
            # Unified JSON retains video configuration, assets, and mixed order.
            exported = io.export_data(stack)
            with runtime.base_color_edit(scene):
                for entry in list(stack.entries):
                    entry.enabled = False
                count, warnings = io.import_data(scene, exported)
            assert count == 2 and not warnings, warnings
            imported = list(stack.entries)[-2:]
            assert [entry.type for entry in imported] == ["VIDEO", "COLOR_RAMP"]
            assert imported[0].video_targets[0].drone == drone
            assert imported[0].video_view_captured
            stack.active_entry_index = len(stack.entries) - 2
            selected = io.export_data(stack, filter="SELECTED")
            assert list(selected.values())[0]["video"] == exported["Blue video"]["video"]
            np.testing.assert_allclose(rgb(scene), [(0, 0, 1)])
            bad = io.export_data(stack, filter="SELECTED")
            next(iter(bad.values()))["video"]["viewMatrix"] = [float("nan")] * 16
            before = len(stack.entries)
            try:
                io.import_data(scene, bad)
            except ValueError:
                pass
            else:
                raise AssertionError("Nonfinite video matrix was accepted")
            assert len(stack.entries) == before
            # Master switch governs video and procedural effects together.
            stack.enabled = False
            np.testing.assert_allclose(rgb(scene), [(1, 0, 0)])
            stack.enabled = True
            # Exactly two lighting panels; only one shared list appears in draw paths.
            panels = [cls for cls in dronesAnim.auto_load.ordered_classes
                      if issubclass(cls, bpy.types.Panel) and getattr(cls, "bl_category", "") == "灯光控制"]
            assert {cls.bl_label for cls in panels} == {"LED", "灯效"}, panels
            fake_context = SimpleNamespace(scene=scene, space_data=SimpleNamespace(
                shading=SimpleNamespace(type="SOLID", color_type="SINGLE", wireframe_color_type="THEME")))
            led_layout, effects_layout = LayoutProbe(), LayoutProbe()
            led_control.DroneAnimLEDControlPanel.draw(SimpleNamespace(layout=led_layout), fake_context)
            ui.DroneAnimLightEffectsPanel.draw(SimpleNamespace(layout=effects_layout), fake_context)
            assert not any(call[0] == "template_list" for call in led_layout.calls)
            lists = [call for call in effects_layout.calls if call[0] == "template_list"]
            assert len(lists) == 1 and lists[0][1][0] == "DRONESANIM_UL_light_effects"
            stack.active_entry_index = len(stack.entries) - 2
            ui.DroneAnimLightEffectsPanel.draw(SimpleNamespace(layout=LayoutProbe()), fake_context)
        # Legacy migration keeps procedural row paths and reverses old video's
        # top-priority-first order to match the shared bottom-priority-first list.
        with runtime.base_color_edit(scene):
            while stack.entries:
                stack.active_entry_index = len(stack.entries) - 1
                stack.remove_active()
            texture = bpy.data.textures.new("LegacyVideo", type="IMAGE")
            texture["drones_anim_video_fps"] = 24.0
            old_green = stack.append_effect("Legacy green")
            old_green.frame_start, old_green.duration = 1, 3
            for stop in old_green.texture.color_ramp.elements:
                stop.color = (0, 1, 0, 1)
            stack.list_version = 0
            legacy = scene.drones_anim_led
            for name in ("Legacy high", "Legacy low"):
                old = legacy.video_effects.add()
                old.name = name
                old.video_start = 1
                setup_video(old, texture)
                old.video_enabled = True
                old.video_targets.add().drone = drone
            old_path = "drones_anim_led.video_effects[0].video_start"
            scene.keyframe_insert(data_path=old_path, frame=1)
            legacy.video_effects[0].video_start = 2
            scene.keyframe_insert(data_path=old_path, frame=2)
            scene.frame_set(1)
            migration.migrate_scene(scene)
            assert [entry.name for entry in stack.entries] == ["Legacy high", "Legacy low", "Legacy green"]
            assert len(legacy.video_effects) == 0
            migration.migrate_scene(scene)
            assert len(stack.entries) == 3
        from bpy_extras import anim_utils
        ad = scene.animation_data
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        paths = [curve.data_path for curve in bag.fcurves]
        assert "drones_anim_light_effects.entries[0].frame_start" in paths, paths
        scene.frame_set(2)
        assert stack.entries[0].frame_start == 2
        # Legacy procedural master-off does not accidentally enable old programs.
        stack.enabled = False
        old = legacy.video_effects.add()
        old.name = "Legacy enabled video"
        old.video_enabled = True
        setup_video(old, texture)
        migration.migrate_scene(scene)
        assert stack.enabled and stack.entries[0].enabled
        assert all(not entry.enabled for entry in list(stack.entries)[1:])
        report = {"blender": bpy.app.version_string, "status": "passed",
                  "panels": ["LED", "灯效"], "list_priority": "upper rows overlay lower rows",
                  "checks": ["mixed order", "blend strength", "master switch", "duplicate/remove",
                             "video looping", "JSON round trip", "invalid JSON rejection",
                             "panel draw branches", "legacy migration", "keyframe remapping"]}
        output = ROOT / "tmp" / "non-numba-merge"
        output.mkdir(parents=True, exist_ok=True)
        (output / "unified-lights.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("UNIFIED_LIGHTS_SMOKE_PASSED", json.dumps(report))
    finally:
        dronesAnim.unregister()


if __name__ == "__main__":
    main()
