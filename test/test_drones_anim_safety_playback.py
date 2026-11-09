"""Exercise safety playback handlers without importing Blender."""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

path = Path(__file__).resolve().parents[1] / "dronesAnim" / "safety.py"
tree = ast.parse(path.read_text(encoding="utf-8"))
names = {
    "_SceneState",
    "_is_playing",
    "_update",
    "_on_scene_update",
    "_on_playback_stopped",
    "_settings_changed",
}
nodes = [
    node
    for node in tree.body
    if (
        isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ) or (
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_PLAYBACK_CHECK_INTERVAL"
            for target in node.targets
        )
    )
]


def harness():
    scene = SimpleNamespace(
        frame_current=1,
        drones_anim_safety=SimpleNamespace(enabled=True),
        render=SimpleNamespace(fps=24, fps_base=1),
        as_pointer=lambda: 42,
    )
    screen = SimpleNamespace(is_animation_playing=True)
    window = SimpleNamespace(scene=scene, screen=screen)
    context = SimpleNamespace(window_manager=SimpleNamespace(windows=[window]))
    evaluate = Mock(
        side_effect=lambda current, limits, fps, previous, earlier: SimpleNamespace(
            frame=current.frame,
            close_pairs=[(str(current.frame), "warning")],
        )
    )
    namespace = {
        "bpy": SimpleNamespace(context=context),
        "dataclass": dataclass,
        "field": field,
        "Snapshot": object,
        "Result": object,
        "persistent": lambda function: function,
        "_states": {},
        "_scanning": False,
        "_snapshot": lambda scene, depsgraph: SimpleNamespace(
            frame=scene.frame_current,
            positions={"drone": (scene.frame_current, 0, 0)},
        ),
        "_limits": Mock(),
        "_redraw": Mock(),
        "evaluate_frame": evaluate,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace, scene, screen, evaluate


def test_playback_checks_every_five_frames_and_retains_warnings():
    namespace, scene, _, evaluate = harness()
    for frame in range(1, 12):
        scene.frame_current = frame
        namespace["_on_scene_update"](scene, None)
        namespace["_on_scene_update"](scene, None)
        state = namespace["_states"][42]
        checked_frame = 1 + ((frame - 1) // 5) * 5
        assert state.result.frame == checked_frame
        assert state.result.close_pairs == [(str(checked_frame), "warning")]
        assert state.snapshots[-1].positions["drone"] == (frame, 0, 0)

    assert [call.args[0].frame for call in evaluate.call_args_list] == [1, 6, 11]
    assert evaluate.call_args.kwargs["previous"].frame == 10
    assert evaluate.call_args.kwargs["earlier"].frame == 9


def test_pause_and_paused_frame_changes_force_current_frame_check():
    namespace, scene, screen, evaluate = harness()
    namespace["_on_scene_update"](scene, None)
    scene.frame_current = 2
    namespace["_on_scene_update"](scene, None)
    assert namespace["_states"][42].result.frame == 1

    # The callback must force a check even if Blender still reports playback.
    namespace["_on_playback_stopped"](scene, None)
    assert namespace["_states"][42].result.frame == 2
    screen.is_animation_playing = False
    scene.frame_current = 3
    namespace["_on_scene_update"](scene, None)
    assert namespace["_states"][42].result.frame == 3
    assert [call.args[0].frame for call in evaluate.call_args_list] == [1, 2, 3]


def test_settings_change_forces_check_and_disabling_clears_state():
    namespace, scene, _, evaluate = harness()
    namespace["_on_scene_update"](scene, None)
    scene.frame_current = 2
    namespace["_on_scene_update"](scene, None)
    context = SimpleNamespace(scene=scene, evaluated_depsgraph_get=lambda: None)
    namespace["_settings_changed"](None, context)
    assert namespace["_states"][42].result.frame == 2
    assert evaluate.call_count == 2

    scene.drones_anim_safety.enabled = False
    namespace["_settings_changed"](None, context)
    state = namespace["_states"][42]
    assert state.result is None
    assert state.snapshots == []


def test_reverse_playback_and_frame_jumps():
    namespace, scene, _, evaluate = harness()
    frames = (11, 10, 9, 8, 7, 6, 5, 1, 30)
    for frame in frames:
        scene.frame_current = frame
        namespace["_on_scene_update"](scene, None)
    assert [call.args[0].frame for call in evaluate.call_args_list] == [11, 6, 1, 30]


def test_playback_detection_is_scoped_to_the_scene():
    namespace, scene, screen, _ = harness()
    assert namespace["_is_playing"](scene)
    assert not namespace["_is_playing"](SimpleNamespace())
    screen.is_animation_playing = False
    assert not namespace["_is_playing"](scene)
