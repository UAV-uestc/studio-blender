"""Independently verify files from blender_non_numba_merge_smoke.py using SciPy."""
import json
from pathlib import Path

import numpy as np
from scipy.io import loadmat


def main():
    root = Path(__file__).resolve().parents[1] / "tmp" / "non-numba-merge"
    expected_rgb = np.array([
        [[0, 0, 1], [0, 1, 0]],
        [[0, 0, 1], [0, 1, 0]],
        [[0, 1, 0], [0, 1, 0]],
        [[1, 0, 0], [1, 0, 0]],
    ]) * 255
    for filename in ("combined.mat", "procedural.mat"):
        data = loadmat(root / filename)
        assert {key for key in data if not key.startswith("__")} == set("xyzrgb") | {"Qn"}
        for channel in "xyzrgb":
            assert data[channel].shape == (2, 4), (filename, channel)
            assert data[channel].dtype == np.dtype("float64")
            assert np.isfinite(data[channel]).all()
        assert data["Qn"].dtype == np.dtype("float64")
        np.testing.assert_array_equal(data["Qn"], [[0], [1]])
        np.testing.assert_array_equal(data["x"], [[9] * 4, [11] * 4])
        np.testing.assert_array_equal(data["y"], np.zeros((2, 4)))
        np.testing.assert_array_equal(data["z"], np.full((2, 4), 3))
        rgb = np.stack([data[ch].T for ch in "rgb"], axis=-1)
        expected = expected_rgb.copy()
        if filename == "procedural.mat":
            expected[:3, 0] = (0, 255, 0)
        np.testing.assert_allclose(rgb, expected, atol=1e-6, rtol=0)
    report = {"status": "passed", "reader": "scipy.io.loadmat",
              "keys": ["x", "y", "z", "r", "g", "b", "Qn"],
              "channel_shape": [2, 4], "dtype": "float64", "tolerance": 1e-6}
    (root / "roundtrip.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("MAT_ROUNDTRIP_PASSED", json.dumps(report))


if __name__ == "__main__":
    main()
