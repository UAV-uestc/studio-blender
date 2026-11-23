"""Local evaluation context and typing for procedural light effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from .model import DroneAnimLightEffect as LightEffect


class ObjectPositions:
    """Position access compatible with dependency-free V1 and V2 functions."""

    def __init__(self, positions):
        self.as_array = np.asarray(positions, dtype=np.float32)

    def __len__(self):
        return len(self.as_array)

    @property
    def as_coordinate_sequence(self):
        return [tuple(row) for row in self.as_array]

    @property
    def as_vectors(self):
        from mathutils import Vector

        return [Vector(row) for row in self.as_array]


@dataclass(frozen=True)
class LightEffectEvaluationContext:
    """Inputs and mutable per-evaluation arrays shared with effect functions."""

    drones: object
    positions: object
    mapping: object
    random_seq: object
    mask: np.ndarray
    backdrop: np.ndarray
    colors: np.ndarray
    _cache: dict = field(default_factory=dict)

    @property
    def active_drones(self):
        return np.flatnonzero(~self.mask)

    @property
    def num_drones(self):
        return len(self.positions.as_array)

    @property
    def swarm_center(self):
        if "swarm_center" not in self._cache:
            positions = self.positions.as_array
            self._cache["swarm_center"] = (
                positions.mean(axis=0).astype(np.float32)
                if len(positions)
                else np.zeros(3, dtype=np.float32)
            )
        return self._cache["swarm_center"]


class LightEffectOutputFunctionV2(Protocol):
    def __call__(self, effect: LightEffect, context: LightEffectEvaluationContext, frame, *, out):
        ...
