"""Validated Skybrush-compatible, name-keyed JSON without executable imports."""

import json
import math
import os
import tempfile
from pathlib import Path

import bpy

from .model import BLEND_ITEMS, MAPPING_ITEMS, OUTPUT_ITEMS
from .presets import get_preset_enum_items
from .runtime import base_color_edit, trust_store

FIELDS = {
    "enabled": "enabled", "frameStart": "frame_start", "duration": "duration",
    "fadeInDuration": "fade_in_duration", "fadeOutDuration": "fade_out_duration",
    "output": "output", "outputY": "output_y", "influence": "influence",
    "target": "target", "randomness": "randomness",
    "outputMappingMode": "output_mapping_mode", "outputMappingModeY": "output_mapping_mode_y",
    "blendMode": "blend_mode", "type": "type", "invertTarget": "invert_target",
    "presetId": "preset_id",
}
FUNCTIONS = {
    "colorFunction": "color_function", "outputFunction": "output_function",
    "outputFunctionY": "output_function_y",
}
ENUMS = {
    "output": {item[0] for item in OUTPUT_ITEMS},
    "outputY": {item[0] for item in OUTPUT_ITEMS},
    "outputMappingMode": {item[0] for item in MAPPING_ITEMS},
    "outputMappingModeY": {item[0] for item in MAPPING_ITEMS},
    "blendMode": {item[0] for item in BLEND_ITEMS},
    "type": {"COLOR_RAMP", "IMAGE", "FUNCTION", "VIDEO"},
    "target": {"ALL", "INSIDE_MESH", "FRONT_SIDE"},
}
RAMP_ENUMS = {
    "colorMode": ("color_mode", {"RGB", "HSV", "HSL"}),
    "interpolation": ("interpolation", {"EASE", "CARDINAL", "LINEAR", "B_SPLINE", "CONSTANT"}),
    "hueInterpolation": ("hue_interpolation", {"NEAR", "FAR", "CW", "CCW"}),
}


class UnsupportedEffect(ValueError):
    pass


def ramp_data(ramp):
    return {
        "colorMode": ramp.color_mode, "interpolation": ramp.interpolation,
        "hueInterpolation": ramp.hue_interpolation,
        "elements": [
            {"position": item.position, "color": list(item.color), "alpha": item.alpha}
            for item in ramp.elements
        ],
    }


def effect_data(effect):
    data = {key: getattr(effect, attr) for key, attr in FIELDS.items()}
    for key, attr in FUNCTIONS.items():
        function = getattr(effect, attr)
        data[key] = {"path": function.path, "name": function.name}
    data["meshName"] = effect.mesh.name if effect.mesh else effect.missing_mesh_name or None
    data["droneGroupName"] = effect.drone_group.name if effect.drone_group else effect.missing_group_name or None
    texture = effect.texture
    data["texture"] = {
        "useColorRamp": texture.use_color_ramp if texture else True,
        "colorRamp": ramp_data(texture.color_ramp) if texture else None,
        "imageName": texture.image.name if texture and texture.image else effect.missing_image_name or None,
    }
    if effect.type == "VIDEO":
        data["video"] = {
            "textureName": effect.video_texture.name if effect.video_texture else None,
            "sourceDuration": effect.video_duration,
            "viewCaptured": effect.video_view_captured,
            "viewMatrix": list(effect.video_view_matrix),
            "viewportSize": list(effect.video_viewport_size),
            "targetNames": [target.drone.name if target.drone else None for target in effect.video_targets],
            "endLocked": effect.video_end_locked,
        }
    return data


def export_data(stack, *, filter="ALL"):
    if filter not in {"ALL", "ENABLED", "SELECTED"}:
        raise ValueError("Unknown export filter")
    entries = (
        [stack.active_entry] if stack.active_entry is not None else []
    ) if filter == "SELECTED" else list(stack.entries)
    if filter == "ENABLED":
        entries = [entry for entry in entries if entry.enabled]
    result = {}
    for entry in entries:
        if entry.name in result:
            raise ValueError(f"Duplicate effect name: {entry.name}")
        result[entry.name] = effect_data(entry)
        # Per-entry metadata remains ignorable by Skybrush's field-based reader.
        result[entry.name]["droneAnim"] = {"version": 1, "randomSeed": stack.random_seed}
    return result


def number(value, name, *, integer=False, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite numeric data")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"{name} is outside the supported range")


def validate_entry(name, data):
    if not isinstance(name, str) or not name.strip() or not isinstance(data, dict):
        raise ValueError("Each effect must have a nonempty name and object settings")
    for key in ("enabled", "invertTarget"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(f"{name}: {key} must be boolean")
    for key, minimum in (("frameStart", -2147483648), ("duration", 1), ("fadeInDuration", 0), ("fadeOutDuration", 0)):
        if key in data:
            number(data[key], key, integer=True, minimum=minimum, maximum=2147483647)
    for key in ("influence", "randomness"):
        if key in data:
            number(data[key], key, minimum=0)
    for key, allowed in ENUMS.items():
        if key in data and (not isinstance(data[key], str) or data[key] not in allowed):
            raise UnsupportedEffect(f"{name}: unsupported {key}: {data[key]!r}")
    if "presetId" in data:
        allowed = {item[0] for item in get_preset_enum_items(None, None)}
        if not isinstance(data["presetId"], str) or data["presetId"] not in allowed:
            raise UnsupportedEffect(f"{name}: unsupported preset ID: {data['presetId']!r}")
    for key in ("meshName", "droneGroupName"):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ValueError(f"{name}: {key} must be a name or null")
    for key in FUNCTIONS:
        function = data.get(key)
        if function is not None:
            if not isinstance(function, dict) or any(
                not isinstance(function.get(field, ""), str) for field in ("path", "name")
            ):
                raise ValueError(f"{name}: invalid function reference")
    metadata = data.get("droneAnim")
    if metadata is not None:
        if not isinstance(metadata, dict) or metadata.get("version", 1) != 1:
            raise UnsupportedEffect(f"{name}: unsupported DroneAnim metadata")
        if "randomSeed" in metadata:
            number(metadata["randomSeed"], "randomSeed", integer=True, minimum=-2147483648, maximum=2147483647)
    if data.get("type") == "VIDEO":
        validate_video_data(name, data.get("video"))
    texture = data.get("texture")
    if texture is None:
        return
    if not isinstance(texture, dict):
        raise ValueError(f"{name}: texture must be an object")
    if "useColorRamp" in texture and not isinstance(texture["useColorRamp"], bool):
        raise ValueError(f"{name}: useColorRamp must be boolean")
    if texture.get("imageName") is not None and not isinstance(texture["imageName"], str):
        raise ValueError(f"{name}: imageName must be a name or null")
    ramp = texture.get("colorRamp")
    if ramp is None:
        return
    if not isinstance(ramp, dict):
        raise ValueError(f"{name}: colorRamp must be an object")
    for key, (_, allowed) in RAMP_ENUMS.items():
        if key in ramp and (not isinstance(ramp[key], str) or ramp[key] not in allowed):
            raise UnsupportedEffect(f"{name}: unsupported ramp {key}")
    if "elements" in ramp:
        elements = ramp["elements"]
        if not isinstance(elements, list) or not 2 <= len(elements) <= 32:
            raise ValueError(f"{name}: ramps require 2 through 32 stops")
        for item in elements:
            if not isinstance(item, dict):
                raise ValueError(f"{name}: invalid ramp stop")
            number(item.get("position"), "position", minimum=0, maximum=1)
            color = item.get("color")
            if not isinstance(color, list) or len(color) != 4:
                raise ValueError(f"{name}: ramp colors require four channels")
            for channel in color:
                number(channel, "color", minimum=0)
            number(color[3], "color alpha", minimum=0, maximum=1)
            if "alpha" in item:
                number(item["alpha"], "alpha", minimum=0, maximum=1)


def validate_video_data(name, video):
    if not isinstance(video, dict):
        raise ValueError(f"{name}: video settings are required")
    if video.get("textureName") is not None and not isinstance(video["textureName"], str):
        raise ValueError(f"{name}: invalid video texture reference")
    number(video.get("sourceDuration"), "sourceDuration", integer=True, minimum=0, maximum=2147483647)
    for key in ("viewCaptured", "endLocked"):
        if not isinstance(video.get(key), bool):
            raise ValueError(f"{name}: {key} must be boolean")
    for key, count in (("viewMatrix", 16), ("viewportSize", 2)):
        values = video.get(key)
        if not isinstance(values, list) or len(values) != count:
            raise ValueError(f"{name}: invalid {key}")
        for value in values:
            number(value, key, integer=key == "viewportSize", minimum=1 if key == "viewportSize" else None,
                   maximum=2147483647 if key == "viewportSize" else None)
    targets = video.get("targetNames")
    if not isinstance(targets, list) or any(value is not None and not isinstance(value, str) for value in targets):
        raise ValueError(f"{name}: invalid video targets")


def assign_entry(effect, data, warnings):
    for key, attr in FIELDS.items():
        if key in data:
            setattr(effect, attr, data[key])
    for key, attr in FUNCTIONS.items():
        if data.get(key) is not None:
            function = getattr(effect, attr)
            function.path = data[key].get("path", "")
            function.name = data[key].get("name", "")
    for key, attr, collection, missing in (
        ("meshName", "mesh", bpy.data.objects, "missing_mesh_name"),
        ("droneGroupName", "drone_group", bpy.data.collections, "missing_group_name"),
    ):
        name = data.get(key)
        if name:
            asset = collection.get(name)
            if asset is not None and (attr != "mesh" or asset.type == "MESH"):
                setattr(effect, attr, asset)
            else:
                setattr(effect, missing, name)
                effect.enabled = False
                warnings.append(f"{effect.name}: unresolved {key}: {name}")
    if effect.type == "VIDEO":
        video = data["video"]
        effect.video_texture = bpy.data.textures.get(video.get("textureName") or "")
        effect.video_duration = video["sourceDuration"]
        effect.video_view_matrix = video["viewMatrix"]
        effect.video_viewport_size = video["viewportSize"]
        effect.video_view_captured = video["viewCaptured"]
        effect.video_end_locked = video["endLocked"]
        effect.duration = data.get("duration", 1)
        if effect.video_texture is None:
            effect.enabled = False
            warnings.append(f"{effect.name}: video texture is missing; reselect the video")
        for name in video["targetNames"]:
            drone = bpy.data.objects.get(name or "")
            effect.video_targets.add().drone = drone
            if drone is None:
                effect.enabled = False
                warnings.append(f"{effect.name}: video target is missing: {name}")
        return
    if any("storyboard" in key.lower() or key.endswith("Offset") for key in data):
        warnings.append(f"{effect.name}: storyboard linkage discarded; absolute timing retained")
    texture = data.get("texture") or {}
    if "useColorRamp" in texture:
        effect.texture.use_color_ramp = texture["useColorRamp"]
    if texture.get("imageName"):
        name = texture["imageName"]
        image = bpy.data.images.get(name)
        if image is None:
            effect.missing_image_name = name
            effect.enabled = False
            warnings.append(f"{effect.name}: unresolved imageName: {name}")
        else:
            effect.texture.image = image
    ramp = texture.get("colorRamp") or {}
    target = effect.texture.color_ramp
    for key, (attr, _) in RAMP_ENUMS.items():
        if key in ramp:
            setattr(target, attr, ramp[key])
    if "elements" in ramp:
        source = sorted(ramp["elements"], key=lambda item: item["position"])
        while len(target.elements) < len(source):
            target.elements.new(1)
        while len(target.elements) > len(source):
            target.elements.remove(target.elements[-1])
        # Position assignment can reorder the collection; retain stop handles.
        for item, value in zip(tuple(target.elements), source):
            item.position, item.color = value["position"], value["color"]
            item.alpha = value.get("alpha", value["color"][3])


def import_data(scene, data, *, filter="ALL"):
    if filter not in {"ALL", "ENABLED"} or not isinstance(data, dict):
        raise ValueError("Expected a name-keyed JSON object and a valid import filter")
    warnings, planned = [], []
    for name, settings in data.items():
        if filter == "ENABLED" and isinstance(settings, dict) and settings.get("enabled") is False:
            continue
        try:
            validate_entry(name, settings)
        except UnsupportedEffect as exc:
            warnings.append(str(exc))
            continue
        planned.append((name, settings))
    stack = scene.drones_anim_light_effects
    start, active, seed = len(stack.entries), stack.active_entry_index, stack.random_seed
    seeds = {(settings.get("droneAnim") or {}).get("randomSeed") for _, settings in planned}
    seeds.discard(None)
    if len(seeds) > 1:
        warnings.append("Conflicting imported random seeds; current scene seed retained")
    paths = {
        settings[key]["path"]
        for _, settings in planned for key in FUNCTIONS
        if isinstance(settings.get(key), dict) and settings[key].get("path")
    }
    for path in paths:
        trust_store().revoke(bpy.path.abspath(path))
        warnings.append(f"Imported custom file requires renewed approval: {path}")
    with base_color_edit(scene):
        try:
            for name, settings in planned:
                assign_entry(stack.append_effect(name, effect_type=settings.get("type", "COLOR_RAMP"), at_top=False), settings, warnings)
            if len(seeds) == 1:
                stack.random_seed = seeds.pop()
        except Exception:
            while len(stack.entries) > start:
                stack.active_entry_index = len(stack.entries) - 1
                stack.remove_active()
            stack.active_entry_index, stack.random_seed = active, seed
            raise
    return len(planned), warnings


def unique_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate JSON key: {name}")
        result[name] = value
    return result


def import_file(scene, path, *, filter="ALL"):
    with Path(path).open("r", encoding="utf-8-sig") as stream:
        data = json.load(stream, object_pairs_hook=unique_pairs)
    return import_data(scene, data, filter=filter)


def export_file(stack, path, *, filter="ALL"):
    data = export_data(stack, filter=filter)
    content = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix="." + destination.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(data)
