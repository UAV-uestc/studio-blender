"""Color interpolation checks without requiring Blender."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

path = Path(__file__).resolve().parents[1] / "dronesAnim" / "led_animation.py"
spec = importlib.util.spec_from_file_location("drones_anim_led_animation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def point(frame, value, interpolation="BEZIER"):
    return SimpleNamespace(
        co=SimpleNamespace(x=frame, y=value),
        interpolation=interpolation,
        handle_left=SimpleNamespace(y=-1),
        handle_right=SimpleNamespace(y=2),
    )


@pytest.mark.parametrize("hold, expected", [(True, "CONSTANT"), (False, "LINEAR")])
def test_new_color_interpolation_preserves_other_keys(monkeypatch, hold, expected):
    previous, sampled, following = point(1, 0), point(10, 1), point(20, 0)
    curve = SimpleNamespace(
        keyframe_points=[previous, sampled, following],
        update=Mock(),
    )
    drone = SimpleNamespace(
        animation_data=None, color=(0, 0, 0, 1), keyframe_insert=Mock()
    )
    monkeypatch.setattr(module, "color_curves", lambda _drone: [curve])
    module.insert_color(drone, (1, 0.5, 0), 10, step=False, frame_start=1, hold=hold)
    assert sampled.interpolation == expected
    assert previous.interpolation == following.interpolation == "BEZIER"
    assert sampled.handle_left.y == sampled.handle_right.y == 1
    assert drone.color == (1, 0.5, 0, 1)
    drone.keyframe_insert.assert_called_once_with(data_path="color", frame=10)


def test_default_fade_remains_linear(monkeypatch):
    sampled = point(10, 1)
    curve = SimpleNamespace(keyframe_points=[sampled], update=Mock())
    drone = SimpleNamespace(
        animation_data=None, color=(0, 0, 0, 1), keyframe_insert=Mock()
    )
    monkeypatch.setattr(module, "color_curves", lambda _drone: [curve])
    module.insert_color(drone, (1, 0, 0), 10, step=False, frame_start=1)
    assert sampled.interpolation == "LINEAR"
