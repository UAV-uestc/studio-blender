"""Migrate legacy independent lists into one shared stack."""

from .runtime import base_color_edit


def migrate_scene(scene):
    stack = scene.drones_anim_light_effects
    legacy = scene.drones_anim_led
    # Existing procedural rows keep both their order and animation paths.
    stack.list_version = 1
    if not legacy.video_effects:
        return
    # Preserve the old video-over-procedural behavior, including when the old
    # procedural master switch was off, by disabling those individual rows.
    was_enabled = stack.enabled
    active = stack.active_entry_index
    original_count = len(stack.entries)
    with base_color_edit(scene):
        try:
            for old in reversed(legacy.video_effects):
                new = stack.append_effect(old.name, select=False, effect_type="VIDEO", at_top=False)
                new.frame_start = old.video_start
                new.duration = max(1, old.video_end - old.video_start + 1)
                new.video_texture = old.video_texture
                new.video_duration = old.video_duration
                new.video_view_matrix = tuple(old.video_view_matrix)
                new.video_viewport_size = tuple(old.video_viewport_size)
                new.video_view_captured = old.video_view_captured
                new.video_end_locked = old.video_end_locked
                # Unlock callbacks can recalculate duration; restore explicit range.
                new.duration = max(1, old.video_end - old.video_start + 1)
                new.enabled = old.video_enabled
                for target in old.video_targets:
                    new.video_targets.add().drone = target.drone
        except Exception:
            while len(stack.entries) > original_count:
                stack.active_entry_index = len(stack.entries) - 1
                stack.remove_active()
            stack.active_entry_index = active
            raise
        count = len(legacy.video_effects)
        remap_video_animation(scene, original_count, count)
        if not was_enabled:
            for entry in list(stack.entries)[:original_count]:
                entry.enabled = False
            stack.enabled = True
        stack.active_entry_index = original_count + count - 1 - min(legacy.video_effect_index, count - 1)
        legacy.video_effects.clear()


def remap_video_animation(scene, start, count):
    """Keep scene keyframes and drivers bound to the migrated video entries."""
    import re
    from bpy_extras import anim_utils

    animation = scene.animation_data
    if animation is None:
        return
    aliases = {"video_start": "frame_start", "video_enabled": "enabled"}
    pattern = re.compile(r"drones_anim_led\.video_effects\[(\d+)\]\.(\w+)")

    def rewrite(path):
        def replace(match):
            old = int(match[1])
            if old >= count:
                return match[0]
            field = aliases.get(match[2], match[2])
            # The legacy video_end setter used a stored range; animated ends
            # need a getter/setter property in the new model as well.
            return f"drones_anim_light_effects.entries[{start + count - 1 - old}].{field}"
        path = pattern.sub(replace, path)
        single_fields = {"video_start", "video_enabled", "video_texture", "video_duration",
                         "video_view_captured", "video_view_matrix", "video_viewport_size"}
        for field in single_fields:
            prefix = f"drones_anim_led.{field}"
            if path == prefix or path.startswith(prefix + "["):
                return path.replace(prefix, f"drones_anim_light_effects.entries[{start + count - 1}].{aliases.get(field, field)}", 1)
        return path

    curves = []
    if animation.action is not None and animation.action_slot is not None:
        bag = anim_utils.action_get_channelbag_for_slot(animation.action, animation.action_slot)
        if bag is not None:
            curves.extend(bag.fcurves)
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.action is not None and strip.action_slot is not None:
                bag = anim_utils.action_get_channelbag_for_slot(strip.action, strip.action_slot)
                if bag is not None:
                    curves.extend(bag.fcurves)
    curves.extend(animation.drivers)
    for curve in curves:
        curve.data_path = rewrite(curve.data_path)
        if curve.driver is not None:
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id == scene:
                        target.data_path = rewrite(target.data_path)
