"""Stable drone ordering and single-formation vertex mappings."""

import re

import numpy as np

VERTEX_INDEX = "drones_anim_vertex_index"

GRADIENT_AXES = {
    "GRADIENT_XYZ": (0, 1, 2),
    "GRADIENT_XZY": (0, 2, 1),
    "GRADIENT_YXZ": (1, 0, 2),
    "GRADIENT_YZX": (1, 2, 0),
    "GRADIENT_ZXY": (2, 0, 1),
    "GRADIENT_ZYX": (2, 1, 0),
}


def natural_name_key(name):
    parts = tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", name)
    )
    return parts, name


def ordered_drones(scene):
    """Include tagged scene meshes, including detached and nested drones."""
    return sorted(
        (
            obj
            for obj in scene.objects
            if obj.type == "MESH" and VERTEX_INDEX in obj
        ),
        key=lambda obj: natural_name_key(obj.name),
    )


def vertex_indices(drones):
    indices = []
    for drone in drones:
        value = drone.get(VERTEX_INDEX)
        indices.append(
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )
    return indices


def formation_coordinates(indices):
    """Rank distinct valid vertex indices; invalid entries have no coordinate."""
    valid = sorted({index for index in indices if index is not None})
    denominator = max(len(valid) - 1, 1)
    ranks = {value: index / denominator for index, value in enumerate(valid)}
    return np.array(
        [ranks[index] if index is not None else np.nan for index in indices],
        dtype=np.float32,
    )


def output_values(
    output,
    positions,
    *,
    frame=0,
    start=0,
    duration=1,
    mapping="ORDERED",
    formation_indices=None,
    mesh_origin=None,
):
    """Compute per-drone ramp coordinates for built-in scalar output types."""
    positions = np.asarray(positions, dtype=np.float32)
    if positions.ndim != 2 or positions.shape[1] != 3:
        raise ValueError("Drone positions must have shape (N, 3)")
    count = len(positions)
    if duration < 1:
        raise ValueError("Effect duration must be at least one frame")

    if output == "FIRST_COLOR":
        return np.zeros(count, dtype=np.float32)
    if output == "LAST_COLOR":
        return np.ones(count, dtype=np.float32)
    if output == "TEMPORAL":
        return np.full(count, (frame - start) / max(duration - 1, 1), dtype=np.float32)
    if output == "INDEXED_BY_DRONES":
        if count == 1:
            return np.ones(1, dtype=np.float32)
        return np.linspace(0, 1, count, dtype=np.float32)
    if output == "INDEXED_BY_FORMATION":
        if formation_indices is None or len(formation_indices) != count:
            raise ValueError("Formation indices must match the drone count")
        return formation_coordinates(formation_indices)

    if output == "DISTANCE":
        if mesh_origin is None:
            raise ValueError("Distance output requires a mesh")
        origin = np.asarray(mesh_origin, dtype=np.float32)
        if origin.shape != (3,):
            raise ValueError("Mesh origin must have three coordinates")
        keys = ((positions - origin) ** 2).sum(axis=1)
    elif output in GRADIENT_AXES:
        axes = GRADIENT_AXES[output]
        keys = positions[:, axes[0]] if mapping == "PROPORTIONAL" else positions[:, axes]
    else:
        raise ValueError(f"Unknown output type: {output!r}")

    if mapping not in ("ORDERED", "PROPORTIONAL"):
        raise ValueError(f"Unknown mapping mode: {mapping!r}")
    if count < 2:
        return np.ones(count, dtype=np.float32)
    if mapping == "PROPORTIONAL":
        low, high = keys.min(), keys.max()
        if high <= low:
            return np.zeros(count, dtype=np.float32)
        return np.asarray((keys - low) / (high - low), dtype=np.float32)

    order = np.lexsort(np.rot90(keys)) if keys.ndim == 2 else np.argsort(keys)
    return np.asarray(np.argsort(order) / (count - 1), dtype=np.float32)
