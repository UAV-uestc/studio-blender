"""Numerical helpers for procedural light effects.

Blending adapted from Skybrush Studio for Blender, copyright CollMot Robotics
Ltd. and contributors, distributed under GPL-3.0-or-later.
"""

from enum import IntEnum, auto
from hashlib import blake2b
from typing import NamedTuple

import numpy as np


class BlendMode(IntEnum):
    NORMAL = auto()
    MULTIPLY = auto()
    SCREEN = auto()
    DARKEN = auto()
    LIGHTEN = auto()
    OVERLAY = auto()
    SOFT_LIGHT = auto()
    HARD_LIGHT = auto()


class LightLayer(NamedTuple):
    colors: np.ndarray
    mask: np.ndarray
    influence: float = 1.0
    blend_mode: BlendMode = BlendMode.NORMAL
    enabled: bool = True


def frame_end(start, duration):
    if duration < 1:
        raise ValueError("Effect duration must be at least one frame")
    return start + duration - 1


def contains_frame(start, duration, frame):
    return start <= frame <= frame_end(start, duration)


def time_fraction(start, duration, frame):
    frame_end(start, duration)
    return (frame - start) / max(duration - 1, 1)


def effect_influence(start, duration, frame, influence, fade_in=0, fade_out=0):
    end = frame_end(start, duration)
    if fade_in > 0:
        elapsed = frame - start + 1
        if elapsed < fade_in:
            influence *= elapsed / fade_in
    if fade_out > 0:
        remaining = end - frame
        if remaining < fade_out:
            influence *= remaining / fade_out
    return min(max(influence, 0.0), 1.0)


def blend_in_place(source, backdrop, mode=BlendMode.NORMAL):
    """Composite source RGBA onto backdrop in-place using source alpha semantics."""
    alpha_source = source[:, 3]
    active = alpha_source > 0
    if not active.any():
        return

    if mode == BlendMode.NORMAL:
        opaque = alpha_source >= 1
        backdrop[opaque] = source[opaque]
        active &= ~opaque
        if not active.any():
            return

    source = source[active]
    work = backdrop[active]
    alpha_source = source[:, 3]
    alpha_backdrop = work[:, 3]
    alpha_overlay = np.where(
        alpha_backdrop >= 1,
        1.0,
        1.0 - (1.0 - alpha_source) * (1.0 - alpha_backdrop),
    )
    alpha = np.where(
        alpha_backdrop >= 1, alpha_source, alpha_source / alpha_overlay
    )[:, None]
    src = source[:, :3]
    dst = work[:, :3]
    source_part = alpha * src
    backdrop_part = (1.0 - alpha) * dst

    if mode == BlendMode.NORMAL:
        work[:, :3] = source_part + backdrop_part
    elif mode == BlendMode.MULTIPLY:
        work[:, :3] = source_part * dst + backdrop_part
    elif mode == BlendMode.SCREEN:
        work[:, :3] = alpha * (1.0 - (1.0 - dst) * (1.0 - src)) + backdrop_part
    elif mode == BlendMode.DARKEN:
        work[:, :3] = alpha * np.minimum(dst, src) + backdrop_part
    elif mode == BlendMode.LIGHTEN:
        work[:, :3] = alpha * np.maximum(dst, src) + backdrop_part
    elif mode == BlendMode.OVERLAY:
        work[:, :3] = np.where(
            dst >= 0.5,
            alpha * (1.0 - (2.0 - 2.0 * dst) * (1.0 - src)) + backdrop_part,
            alpha * (2.0 * dst) * src + backdrop_part,
        )
    elif mode == BlendMode.HARD_LIGHT:
        work[:, :3] = np.where(
            src <= 0.5,
            alpha * dst * (2.0 * src) + backdrop_part,
            alpha * (1.0 - (1.0 - dst) * (2.0 - 2.0 * src)) + backdrop_part,
        )
    elif mode == BlendMode.SOFT_LIGHT:
        lower = dst - (1.0 - 2.0 * src) * dst * (1.0 - dst)
        d = np.where(
            dst <= 0.25,
            ((16.0 * dst - 12.0) * dst + 4.0) * dst,
            dst**0.5,
        )
        upper = dst + (2.0 * src - 1.0) * (d - dst)
        work[:, :3] = alpha * np.where(src <= 0.5, lower, upper) + backdrop_part
    else:
        raise ValueError(f"Unknown blend mode: {mode!r}")

    work[:, 3] = alpha_overlay
    backdrop[active] = work


def random_offsets(seed, identities, amount):
    """Return stable offsets keyed by persistent drone identity, not row order."""
    offsets = np.empty(len(identities), dtype=np.float32)
    for index, identity in enumerate(identities):
        payload = f"{seed}:{identity}".encode("utf-8")
        value = int.from_bytes(blake2b(payload, digest_size=8).digest(), "big")
        offsets[index] = (value / (2**64 - 1) - 0.5) * amount
    return offsets


class IdentityRandomSequence:
    """Deterministic random values keyed by drone identity for custom functions."""

    max = 0xFFFFFFFF

    def __init__(self, seed, identities):
        self.seed = seed
        self.identities = tuple(identities)

    def get(self, index):
        identity = self.identities[index] if index < len(self.identities) else f"index:{index}"
        digest = blake2b(f"{self.seed}:{identity}".encode("utf-8"), digest_size=4).digest()
        return int.from_bytes(digest, "big")

    def __getitem__(self, index):
        if isinstance(index, slice):
            if index.stop is None:
                raise ValueError("Random sequence slices need a stop")
            return [self.get(i) for i in range(index.start or 0, index.stop, index.step or 1)]
        return self.get(index)

    def get_float(self, index):
        return self.get(index) / self.max

    def get_array(self, start, length):
        return np.array([self.get(i) for i in range(start, start + length)], dtype=np.int64)

    def get_array_01(self, start, length):
        return (self.get_array(start, length).astype(np.float64) / self.max).astype(np.float32)


def randomize_axes(x, y, offsets):
    """Apply the same wrapped offset to each available output axis."""
    return tuple(
        None if axis is None else np.remainder(axis + offsets, 1.0)
        for axis in (x, y)
    )


def compose_layers(base_colors, layers):
    """Return a new RGBA array with enabled, selected layers applied in order."""
    backdrop = np.asarray(base_colors, dtype=np.float32).copy()
    if backdrop.ndim != 2 or backdrop.shape[1] != 4:
        raise ValueError("Base colors must have shape (N, 4)")
    for layer in layers:
        if not layer.enabled:
            continue
        colors = np.asarray(layer.colors, dtype=np.float32)
        mask = np.asarray(layer.mask, dtype=bool)
        if colors.shape != backdrop.shape or mask.shape != (len(backdrop),):
            raise ValueError("Layer colors and mask must match the base drone count")
        influence = float(layer.influence)
        if not np.isfinite(influence):
            raise ValueError("Layer influence must be finite")
        active = mask & np.isfinite(colors).all(axis=1)
        prepared = np.zeros_like(colors)
        prepared[active] = colors[active]
        prepared[:, 3] *= min(max(influence, 0.0), 1.0)
        blend_in_place(prepared, backdrop, layer.blend_mode)
    return backdrop
