"""Evaluated-world-space target predicates for light effects."""

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

_EPSILON = 1e-6
_RAY = Vector((0.731, 0.509, 0.455)).normalized()


def _evaluated_geometry(obj, depsgraph):
    if obj is None or obj.type != "MESH":
        raise ValueError("Spatial targeting requires a mesh")
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    if mesh is None:
        raise ValueError("Target mesh is unavailable")
    try:
        vertices = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        if not vertices or not polygons:
            raise ValueError("Target mesh has no faces")
        return vertices, polygons
    finally:
        evaluated.to_mesh_clear()


def _plane(vertices, polygon):
    origin = vertices[polygon[0]]
    for index in range(1, len(polygon) - 1):
        normal = (vertices[polygon[index]] - origin).cross(
            vertices[polygon[index + 1]] - origin
        )
        if normal.length_squared > _EPSILON**2:
            return origin, normal.normalized()
    raise ValueError("Target mesh has a degenerate first face")


def _contains(tree, point, max_intersections):
    point = Vector(point)
    nearest = tree.find_nearest(point)
    if nearest[3] is not None and nearest[3] <= _EPSILON:
        return True
    hits = 0
    origin = point
    for _ in range(max_intersections):
        location, _normal, _face, distance = tree.ray_cast(origin, _RAY)
        if distance is None:
            break
        hits += 1
        origin = location + _RAY * _EPSILON
    return bool(hits % 2)


def target_mask(effect, drones, positions, depsgraph):
    """Return selected rows; invalid required geometry raises a diagnostic."""
    positions = np.asarray(positions, dtype=np.float32)
    if positions.shape != (len(drones), 3):
        raise ValueError("Drone positions must match the drone count")
    allowed = np.ones(len(drones), dtype=bool)
    if effect.drone_group is not None:
        members = {obj.as_pointer() for obj in effect.drone_group.all_objects}
        allowed = np.array([obj.as_pointer() in members for obj in drones], dtype=bool)

    if effect.target == "ALL":
        spatial = np.ones(len(drones), dtype=bool)
    elif effect.target in {"INSIDE_MESH", "FRONT_SIDE"}:
        vertices, polygons = _evaluated_geometry(effect.mesh, depsgraph)
        if effect.target == "FRONT_SIDE":
            origin, normal = _plane(vertices, polygons[0])
            spatial = np.array(
                [(Vector(point) - origin).dot(normal) >= -_EPSILON for point in positions],
                dtype=bool,
            )
        else:
            tree = BVHTree.FromPolygons(vertices, polygons)
            if tree is None:
                raise ValueError("Target mesh cannot form a containment tree")
            spatial = np.array(
                [_contains(tree, point, len(polygons) * 2 + 1) for point in positions],
                dtype=bool,
            )
    else:
        raise ValueError(f"Unknown spatial target: {effect.target!r}")
    if effect.invert_target:
        spatial = ~spatial
    return allowed & spatial
