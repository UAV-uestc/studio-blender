"""Verify Skybrush teardown cannot break another add-on's RNA path lookup.

Run in an isolated Blender --background --factory-startup process. The source
add-on needs natsort; --deps-dir can point to an existing vendor directory.
"""
import argparse
import sys
from pathlib import Path

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--deps-dir", type=Path)
options = parser.parse_args(args)
if options.deps_dir:
    sys.path.insert(0, str(options.deps_dir))
for path in (ROOT / "src/modules", ROOT / "src/addons", ROOT):
    sys.path.insert(0, str(path))


def main():
    for cycle in range(2):
        skybrush = addon_utils.enable("ui_skybrush_studio", default_set=False)
        assert skybrush is not None
        assert Path(skybrush.__file__).resolve() == ROOT / "src/addons/ui_skybrush_studio.py"
        # Create persisted system-property groups, including the offending child.
        bpy.context.scene.skybrush.led_control.primary_color = (0.25, 0.5, 0.75)
        bpy.context.active_object.skybrush.formation_vertex_group = "LifecycleTest"
        addon_utils.disable("ui_skybrush_studio", default_set=False)
        assert not hasattr(bpy.types.Scene, "skybrush"), "Dangling Scene.skybrush"
        assert not hasattr(bpy.types.Object, "skybrush"), "Dangling Object.skybrush"
        drone_anim = addon_utils.enable("dronesAnim", default_set=False)
        assert drone_anim is not None
        try:
            assert bpy.context.scene.drones_anim_panel.path_from_id() == "drones_anim_panel"
            assert bpy.context.scene.drones_anim_led.path_from_id() == "drones_anim_led"
            # Re-enable Skybrush alongside DroneAnim; stale .blend data remains readable.
            skybrush = addon_utils.enable("ui_skybrush_studio", default_set=False)
            assert skybrush is not None
            assert bpy.context.scene.skybrush.led_control.path_from_id() == "skybrush.led_control"
            assert tuple(bpy.context.scene.skybrush.led_control.primary_color) == (0.25, 0.5, 0.75)
            assert bpy.context.active_object.skybrush.formation_vertex_group == "LifecycleTest"
            addon_utils.disable("ui_skybrush_studio", default_set=False)
            assert not hasattr(bpy.types.Scene, "skybrush")
            assert bpy.context.scene.drones_anim_panel.path_from_id() == "drones_anim_panel"
        finally:
            addon_utils.disable("dronesAnim", default_set=False)
        print("LIFECYCLE_CYCLE_PASSED", cycle + 1, flush=True)
    print("SKYBRUSH_LIFECYCLE_SMOKE_PASSED", flush=True)


if __name__ == "__main__":
    main()
