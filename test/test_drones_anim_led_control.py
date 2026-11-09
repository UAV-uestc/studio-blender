"""Check the UV operator's playhead handling without importing Blender."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

path = Path(__file__).resolve().parents[1] / "dronesAnim" / "led_control.py"
tree = ast.parse(path.read_text(encoding="utf-8"))
operator = next(
    node
    for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "DroneAnimSampleUVColorsOperator"
)
execute_node = next(
    node
    for node in operator.body
    if isinstance(node, ast.FunctionDef) and node.name == "execute"
)


def run_operator(subframe=0, *, error=None, drones=True):
    drone = Mock()
    props = SimpleNamespace(
        uv_scope="ALL", uv_image=object(), uv_map="", uv_extension="REPEAT"
    )
    scene = SimpleNamespace(
        drones_anim_led=props,
        frame_current=37,
        frame_subframe=subframe,
        frame_start=1,
        frame_set=Mock(),
    )
    context = SimpleNamespace(scene=scene, evaluated_depsgraph_get=Mock())
    namespace = {
        "get_drones": Mock(return_value=[drone] if drones else []),
        "sample_attached_colors": Mock(
            return_value=[(drone, (1, 0, 0))], side_effect=error
        ),
        "insert_color": Mock(),
        "_redraw": Mock(),
    }
    code = compile(ast.Module(body=[execute_node], type_ignores=[]), str(path), "exec")
    exec(code, namespace)
    instance = SimpleNamespace(report=Mock())
    result = namespace["execute"](instance, context)
    return result, namespace, scene, drone


@pytest.mark.parametrize("subframe", [0, 0.25])
def test_uv_sampling_keys_current_playhead_without_moving_it(subframe):
    result, namespace, scene, drone = run_operator(subframe)
    assert result == {"FINISHED"}
    namespace["insert_color"].assert_called_once_with(
        drone,
        (1, 0, 0),
        37 + subframe,
        step=False,
        frame_start=1,
        hold=True,
    )
    scene.frame_set.assert_not_called()
    namespace["_redraw"].assert_called_once()


def test_invalid_image_leaves_playhead_and_keys_unchanged():
    result, namespace, scene, _drone = run_operator(
        0.25, error=ValueError("Missing image")
    )
    assert result == {"CANCELLED"}
    namespace["insert_color"].assert_not_called()
    scene.frame_set.assert_not_called()
    namespace["_redraw"].assert_called_once()


def test_empty_scope_does_not_sample_or_move_playhead():
    result, namespace, scene, _drone = run_operator(drones=False)
    assert result == {"CANCELLED"}
    namespace["sample_attached_colors"].assert_not_called()
    scene.frame_set.assert_not_called()


def has_chinese(text):
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def test_panel_labels_and_tooltips_are_chinese():
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg not in {
            "name",
            "text",
            "description",
        }:
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            text = node.value.value
            assert not text or has_chinese(text), text
    panel = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DroneAnimLEDControlPanel"
    )
    labels = {
        target.id: node.value.value
        for node in panel.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in {"bl_label", "bl_category"}
    }
    assert labels == {"bl_label": "灯光控制", "bl_category": "灯光控制"}


def test_enum_option_labels_and_tooltips_are_chinese():
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "EnumProperty":
            continue
        items = next(
            keyword.value for keyword in node.keywords if keyword.arg == "items"
        )
        for item in items.elts:
            assert has_chinese(item.elts[1].value)
            assert has_chinese(item.elts[2].value)
