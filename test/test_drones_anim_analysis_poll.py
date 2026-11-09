"""Ensure motion-analysis polling does not perform full drone collection."""

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

path = Path(__file__).resolve().parents[1] / "dronesAnim" / "analysis" / "ui.py"
tree = ast.parse(path.read_text(encoding="utf-8"))
functions = [
    node for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name in {"_has_drones", "_drones"}
]
operator = next(
    node for node in tree.body
    if isinstance(node, ast.ClassDef) and node.name == "DroneMotionCalculateOperator"
)
poll = next(
    node for node in operator.body
    if isinstance(node, ast.FunctionDef) and node.name == "poll"
)


def harness(objects=(), *, present=True, linked=True):
    collection = SimpleNamespace(all_objects=objects) if present else None
    scene = SimpleNamespace(
        collection=SimpleNamespace(children_recursive=[collection] if linked else [])
    )
    namespace = {
        "bpy": SimpleNamespace(
            data=SimpleNamespace(collections={"Drones": collection})
        ),
        "re": re,
        "_jobs": {},
    }
    functions_module = ast.Module(body=functions, type_ignores=[])
    exec(compile(functions_module, str(path), "exec"), namespace)
    # Compile the actual method as a standalone callable, preserving its body.
    method = ast.FunctionDef(
        name="poll",
        args=poll.args,
        body=poll.body,
        decorator_list=[],
        returns=poll.returns,
    )
    method_module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(method_module, str(path), "exec"), namespace)
    return namespace, scene


def test_poll_stops_at_first_mesh_without_full_collection():
    def objects():
        yield SimpleNamespace(type="EMPTY")
        yield SimpleNamespace(type="MESH")
        raise AssertionError("Polling must not scan the rest of the collection")

    namespace, scene = harness(objects())
    namespace["_drones"] = Mock(side_effect=AssertionError("Full collection in poll"))
    assert namespace["poll"](None, SimpleNamespace(scene=scene))
    namespace["_drones"].assert_not_called()


@pytest.mark.parametrize(
    "present,linked,objects",
    [
        (False, True, []),
        (True, False, [SimpleNamespace(type="MESH")]),
        (True, True, []),
        (True, True, [SimpleNamespace(type="EMPTY")]),
    ],
)
def test_poll_rejects_missing_or_ineligible_drones(present, linked, objects):
    namespace, scene = harness(objects, present=present, linked=linked)
    assert not namespace["poll"](None, SimpleNamespace(scene=scene))


def test_poll_does_not_access_collection_without_scene_or_during_job():
    namespace, scene = harness()
    namespace["_has_drones"] = Mock(side_effect=AssertionError("Unexpected scene scan"))
    assert not namespace["poll"](None, SimpleNamespace(scene=None))
    namespace["_jobs"][1] = object()
    assert not namespace["poll"](None, SimpleNamespace(scene=scene))
    namespace["_has_drones"].assert_not_called()


def test_full_collection_keeps_mesh_filter_and_natural_name_order():
    objects = [
        SimpleNamespace(type="MESH", name="Drone 10"),
        SimpleNamespace(type="EMPTY", name="Drone 0"),
        SimpleNamespace(type="MESH", name="Drone 2"),
        SimpleNamespace(type="MESH", name="Drone 1"),
    ]
    namespace, scene = harness(objects)
    # No scene.objects is provided: scene membership must not be scanned.
    assert [obj.name for obj in namespace["_drones"](scene)] == [
        "Drone 1", "Drone 2", "Drone 10"
    ]


def test_full_collection_rejects_collection_in_another_scene():
    namespace, scene = harness(
        [SimpleNamespace(type="MESH", name="Drone 1")], linked=False
    )
    assert namespace["_drones"](scene) == []
