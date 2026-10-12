"""Synchronous scene sampling and motion calculation."""

from .numerics import AnalysisBuilder
from .. import safety


def calculate_motion(scene, view_layer, drones):
    """Calculate all curves and restore the source frame and safety state."""
    builder = AnalysisBuilder(scene.frame_start, scene.frame_end,
                              scene.render.fps / scene.render.fps_base,
                              [obj.name for obj in drones])
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    previous_scanning = safety._scanning
    safety._scanning = True
    try:
        for frame in range(builder.start, builder.end + 1):
            scene.frame_set(frame)
            graph = view_layer.depsgraph
            builder.append([tuple(obj.evaluated_get(graph).matrix_world.translation) for obj in drones])
        for _ in builder.finish_steps():
            pass
        return builder.result
    finally:
        try:
            scene.frame_set(original_frame, subframe=original_subframe)
        finally:
            safety._scanning = previous_scanning
        if not previous_scanning:
            safety._update(scene, view_layer.depsgraph)
