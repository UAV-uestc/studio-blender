"""Built-in light effects adapted from Skybrush Studio (GPL-3.0-or-later).

Copyright CollMot Robotics Ltd. and contributors.
"""

# Preserve the registration order and identifiers of the source catalog.
from . import (  # noqa: F401, I001
    basic,
    chasing_tails,
    filling,
    generic,
    groups,
    radar_scan,
    radial,
    sweep,
    wave,
)
from .base import get_preset_enum_items, get_preset_function, iter_preset_mapping

__all__ = ("get_preset_enum_items", "get_preset_function", "iter_preset_mapping")
