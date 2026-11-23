"""Priority ordering with stable animation bindings."""
import re

import bpy
from bpy_extras import anim_utils

_PATTERN = re.compile(r"drones_anim_light_effects\.entries\[(\d+)\]")


def remap_paths(scene, mapping):
    def rewrite(path):
        return _PATTERN.sub(lambda m: f"drones_anim_light_effects.entries[{mapping.get(int(m[1]), int(m[1]))}]", path)

    animation = scene.animation_data
    if animation is not None:
        slots = []
        if animation.action is not None:
            slots.append((animation.action, animation.action_slot))
        for track in animation.nla_tracks:
            for strip in track.strips:
                if strip.action is not None:
                    slots.append((strip.action, strip.action_slot))
        seen = set()
        for action, slot in slots:
            if slot is None:
                continue
            bag = anim_utils.action_get_channelbag_for_slot(action, slot)
            if bag is not None:
                for curve in bag.fcurves:
                    if curve.as_pointer() not in seen:
                        seen.add(curve.as_pointer())
                        curve.data_path = rewrite(curve.data_path)
        for curve in animation.drivers:
            curve.data_path = rewrite(curve.data_path)
    # Driver variables on other IDs can also refer to these scene settings.
    owners = {scene} | bpy.data.user_map(subset={scene}).get(scene, set())
    for owner in owners:
        animation = getattr(owner, "animation_data", None)
        if animation is not None:
            for curve in animation.drivers:
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        if target.id == scene:
                            target.data_path = rewrite(target.data_path)


def reorder(scene, order):
    stack = scene.drones_anim_light_effects
    count = len(stack.entries)
    if sorted(order) != list(range(count)):
        raise ValueError("Invalid light effect order")
    active = stack.active_entry_index
    current = list(range(count))
    for destination, old in enumerate(order):
        source = current.index(old)
        if source != destination:
            stack.entries.move(source, destination)
            current.insert(destination, current.pop(source))
    mapping = {old: new for new, old in enumerate(order)}
    remap_paths(scene, mapping)
    stack.active_entry_index = mapping.get(active, 0)
    from .runtime import invalidate
    invalidate(scene)
    scene.update_tag()


def move(scene, source, destination):
    count = len(scene.drones_anim_light_effects.entries)
    if not 0 <= source < count or not 0 <= destination < count:
        raise ValueError("Light effect no longer exists")
    order = list(range(count))
    order.insert(destination, order.pop(source))
    reorder(scene, order)


def migrate_priority(scene):
    stack = scene.drones_anim_light_effects
    if stack.list_version < 2:
        reorder(scene, list(reversed(range(len(stack.entries)))))
        stack.list_version = 2
