"""UV pixel sampling checks that do not require importing Blender."""

import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "dronesAnim" / "uv_sampling.py"
spec = importlib.util.spec_from_file_location("drones_anim_uv_sampling", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sample_pixel = module.sample_pixel

PIXELS = (
    1,
    0,
    0,
    1,
    0,
    1,
    0,
    1,
    0,
    0,
    1,
    1,
    1,
    1,
    1,
    1,
)


@pytest.mark.parametrize(
    "uv, expected",
    [
        ((0.25, 0.25), (1, 0, 0)),
        ((0.75, 0.25), (0, 1, 0)),
        ((0.25, 0.75), (0, 0, 1)),
        ((0.75, 0.75), (1, 1, 1)),
        ((0.5, 0.5), (0.5, 0.5, 0.5)),
    ],
)
def test_pixel_centers_and_bilinear_filter(uv, expected):
    assert sample_pixel(PIXELS, 2, 2, uv) == pytest.approx(expected)


def test_repeat_wraps_negative_uv_and_filters_across_seam():
    assert sample_pixel(PIXELS, 2, 2, (-0.25, 1.25)) == (0, 1, 0)
    assert sample_pixel(PIXELS, 2, 2, (0, 0.25)) == (0.5, 0.5, 0)


def test_extend_clamps_borders():
    assert sample_pixel(PIXELS, 2, 2, (-2, -2), repeat=False) == (1, 0, 0)
    assert sample_pixel(PIXELS, 2, 2, (2, 2), repeat=False) == (1, 1, 1)


def test_srgb_conversion_precedes_filtering():
    pixels = (0, 0, 0, 1, 0.5, 0.5, 0.5, 1)
    linear = ((0.5 + 0.055) / 1.055) ** 2.4
    assert sample_pixel(pixels, 2, 1, (0.5, 0.5), srgb=True) == pytest.approx(
        (linear / 2,) * 3
    )


@pytest.mark.parametrize("uv", [(float("nan"), 0), (0, float("inf"))])
def test_invalid_uv_is_rejected(uv):
    with pytest.raises(ValueError, match="非有限"):
        sample_pixel(PIXELS, 2, 2, uv)


def test_hdr_values_are_clamped_to_led_range():
    assert sample_pixel((-1, 2, 0.5, 1), 1, 1, (0.5, 0.5)) == (0, 1, 0.5)
