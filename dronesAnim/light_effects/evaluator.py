"""Evaluate a scene's effect stack without changing animation or base colors."""

import bpy
import numpy as np

from ..video_projection import projected_colors, video_is_active
from .context import LightEffectEvaluationContext, ObjectPositions
from .functions import color_function_version, evaluate_custom
from .geometry import target_mask
from .images import evaluate_ramp, sample_pixels
from .indexing import output_values, vertex_indices
from .math import (
    BlendMode,
    IdentityRandomSequence,
    LightLayer,
    compose_layers,
    contains_frame,
    effect_influence,
    random_offsets,
    randomize_axes,
)
from .presets import get_preset_function


def _load_function(trust, properties):
    if not properties.path or not properties.name:
        raise ValueError("A custom function file and name are required")
    return trust.load(bpy.path.abspath(properties.path), properties.name)


def _output(effect, axis, context, frame, depsgraph, trust):
    output = effect.output_y if axis == "y" else effect.output
    properties = effect.output_function_y if axis == "y" else effect.output_function
    if output in {"CUSTOM", "CUSTOM_V2"}:
        function = _load_function(trust, properties)
        return evaluate_custom(
            function, effect, context, frame,
            version=1 if output == "CUSTOM" else 2, color=False,
        )
    if output == "LIGHT_PRESET":
        function = get_preset_function(effect.preset_id)
        if function is None:
            raise ValueError("Select a valid light preset")
        values = np.zeros(context.num_drones, dtype=np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            common = function(effect, context, frame, out=values)
        if common is not None:
            values.fill(common)
        return values

    origin = None
    if output == "DISTANCE" and effect.mesh is not None:
        origin = tuple(effect.mesh.evaluated_get(depsgraph).matrix_world.translation)
    return output_values(
        output,
        context.positions.as_array,
        frame=frame,
        start=effect.frame_start,
        duration=effect.duration,
        mapping=effect.output_mapping_mode_y if axis == "y" else effect.output_mapping_mode,
        formation_indices=context.mapping,
        mesh_origin=origin,
    )


def evaluate_stack(scene, drones, positions, base_colors, depsgraph, image_cache, trust):
    """Return final colors and per-effect diagnostics for one scene snapshot."""
    count = len(drones)
    positions = np.asarray(positions, dtype=np.float32).reshape((count, 3))
    result = np.asarray(base_colors, dtype=np.float32).reshape((count, 4)).copy()
    stack = scene.drones_anim_light_effects
    if not stack.enabled:
        return result, []
    frame = scene.frame_current + scene.frame_subframe
    identities = [getattr(drone, "session_uid", drone.as_pointer()) for drone in drones]
    random_sequence = IdentityRandomSequence(stack.random_seed, identities)
    mapping = vertex_indices(drones)
    errors = []

    # Later rows overlay earlier rows, regardless of effect type.
    for effect in stack.entries:
        active = video_is_active(effect, frame) if effect.type == "VIDEO" else contains_frame(effect.frame_start, effect.duration, frame)
        if not effect.enabled or not active:
            continue
        try:
            if effect.missing_group_name and effect.drone_group is None:
                raise ValueError(f"Required drone group is missing: {effect.missing_group_name}")
            selected = target_mask(effect, drones, positions, depsgraph)
            if not selected.any():
                continue
            working_backdrop = result.copy()
            context = LightEffectEvaluationContext(
                drones=drones,
                positions=ObjectPositions(positions),
                mapping=mapping,
                random_seq=random_sequence,
                mask=~selected,
                backdrop=working_backdrop,
                colors=np.zeros((count, 4), dtype=np.float32),
            )
            if effect.type == "VIDEO":
                from ..mat_export import _export_drones

                exportable = set(_export_drones(scene, ordered=False))
                indices = [index for index, drone in enumerate(drones) if drone in exportable]
                if not indices:
                    continue
                projected, affected = projected_colors(
                    [effect], frame, positions[indices], result[indices],
                    drones=[drones[index] for index in indices], return_mask=True,
                )
                colors = result.copy()
                colors[indices] = projected
                video_mask = np.zeros(count, dtype=bool)
                video_mask[indices] = affected
                selected &= video_mask
            elif effect.type == "FUNCTION":
                function = _load_function(trust, effect.color_function)
                colors = evaluate_custom(
                    function, effect, context, frame,
                    version=color_function_version(function), color=True,
                )
            else:
                texture = effect.texture
                if texture is None:
                    raise ValueError("Effect texture is missing")
                x = _output(effect, "x", context, frame, depsgraph, trust)
                y = _output(effect, "y", context, frame, depsgraph, trust) if effect.type == "IMAGE" else None
                if effect.randomness:
                    offsets = random_offsets(stack.random_seed, identities, effect.randomness)
                    x, y = randomize_axes(x, y, offsets)
                if effect.type == "COLOR_RAMP":
                    colors = evaluate_ramp(texture.color_ramp, x)
                elif effect.type == "IMAGE":
                    if texture.image is None:
                        raise ValueError("Effect image is missing")
                    pixels = image_cache.get(texture.image, frame, texture=texture)
                    colors = sample_pixels(pixels, x, y)
                else:
                    raise ValueError(f"Unknown effect type: {effect.type!r}")
            influence = effect_influence(
                effect.frame_start, effect.frame_end - effect.frame_start + 1, frame, effect.influence,
                effect.fade_in_duration, effect.fade_out_duration,
            )
            result = compose_layers(
                working_backdrop,
                [LightLayer(colors, selected, influence, BlendMode[effect.blend_mode])],
            )
        except Exception as exc:
            errors.append(f"{effect.name}: {exc}")
    return result, errors
