"""Verify optional proximity acceleration without requiring Blender or Numba."""

import importlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def backend(monkeypatch):
    name = "drones_anim_numba_test"
    package = types.ModuleType(name)
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "dronesAnim")]
    monkeypatch.setitem(sys.modules, name, package)
    for key in list(sys.modules):
        if key.startswith(name + "."):
            monkeypatch.delitem(sys.modules, key)
    return importlib.import_module(name + ".nearest_neighbors")


def fake_numba(monkeypatch, decorate=None):
    module = types.ModuleType("numba")
    module.njit = decorate or (lambda **kwargs: lambda function: function)
    monkeypatch.setitem(sys.modules, "numba", module)


def test_unavailable_numba_falls_back_and_is_not_retried(backend, monkeypatch):
    monkeypatch.setitem(sys.modules, "numba", None)
    assert not backend.initialize_numba_backend()
    fake_numba(monkeypatch)
    assert not backend.initialize_numba_backend()
    assert backend.find_proximity_pairs([[0, 0, 0], [1, 0, 0]], 2) == (
        (0, 1), 1.0, [(0, 1)]
    )


def test_compilation_failure_keeps_numpy_backend(backend, monkeypatch):
    def failed_compiler(**kwargs):
        def decorate(function):
            def fail(*args):
                raise RuntimeError("unsupported compiler")
            return fail
        return decorate

    fake_numba(monkeypatch, failed_compiler)
    assert not backend.initialize_numba_backend()
    assert backend.find_proximity_pairs([[0, 0, 0], [1, 0, 0]], 2)[2] == [(0, 1)]


def test_register_prewarms_and_unregister_releases_backend(backend, monkeypatch):
    fake_numba(monkeypatch)
    backend.register()
    assert backend._numba_kernels is not None
    backend.unregister()
    assert backend._numba_kernels is None
    assert not backend._numba_attempted


def test_runtime_failure_falls_back_for_current_and_future_checks(backend, monkeypatch):
    calls = []

    def fail(*args):
        calls.append(1)
        raise RuntimeError("execution failed")

    monkeypatch.setattr(backend, "_numba_kernels", (fail, fail), raising=False)
    points = [[0, 0, 0], [1, 0, 0]]
    assert backend.find_proximity_pairs(points, 2) == ((0, 1), 1.0, [(0, 1)])
    assert backend.find_proximity_pairs(points, 2) == ((0, 1), 1.0, [(0, 1)])
    assert len(calls) == 1
    assert backend._numba_kernels is None


@pytest.mark.parametrize("compiled", [False, True])
@pytest.mark.parametrize("dimensions", [2, 3])
def test_acceleration_matches_numpy_and_brute_force(
    backend, monkeypatch, compiled, dimensions
):
    fake_numba(monkeypatch)
    random = np.random.default_rng(37)
    points = random.normal(size=(135, dimensions))
    points[1] = points[0]
    threshold = 0.7
    numpy_result = backend.find_proximity_pairs(points, threshold)
    if compiled:
        assert backend.initialize_numba_backend()
    result = backend.find_proximity_pairs(points, threshold)
    expected_pairs = [
        (i, j) for i in range(len(points)) for j in range(i + 1, len(points))
        if np.sum((points[i] - points[j]) ** 2) < threshold**2
    ]
    assert result[1] == numpy_result[1] == 0
    assert result[2] == numpy_result[2] == expected_pairs
    assert result[0][0] != result[0][1]


@pytest.mark.parametrize("threshold", [0, -1, 1, np.nextafter(1.0, np.inf)])
def test_accelerated_threshold_boundaries(backend, monkeypatch, threshold):
    points = np.array([[0, 0, 0], [1, 0, 0], [np.nextafter(1.0, 0), 0, 0]])
    expected = backend.find_proximity_pairs(points, threshold)
    fake_numba(monkeypatch)
    assert backend.initialize_numba_backend()
    actual = backend.find_proximity_pairs(points, threshold)
    assert actual[1:] == expected[1:]


def test_accelerated_closest_only_and_empty_inputs(backend, monkeypatch):
    fake_numba(monkeypatch)
    assert backend.initialize_numba_backend()
    for points in ([], [[0, 0, 0]]):
        assert backend.find_proximity_pairs(points, 2) == (None, None, [])
    assert backend.find_proximity_pairs(
        [[0, 0, 0], [1, 0, 0]], 2, all_pairs=False
    ) == ((0, 1), 1.0, [])


def test_real_numba_compiles_and_matches_numpy(backend):
    pytest.importorskip("numba")
    points = np.random.default_rng(71).uniform(-4, 4, size=(300, 3))
    expected = backend.find_proximity_pairs(points, 1)
    assert backend.initialize_numba_backend()
    actual = backend.find_proximity_pairs(points, 1)
    assert actual[1] == pytest.approx(expected[1], rel=1e-14)
    assert actual[2] == expected[2]
    assert backend._numba_kernels[0].nopython_signatures
    assert backend._numba_kernels[1].nopython_signatures
