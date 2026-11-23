"""Scene export and dependency-free Level 5 MAT writing."""

import os
import struct
import sys
import tempfile
import zlib
from array import array
from dataclasses import dataclass
from math import floor, isfinite
from pathlib import Path

from .light_effects.indexing import natural_name_key
from .sampling import PatternStudioSamplingPlan, pattern_studio_sampling_plan


def _element(data_type, data):
    """Build a MAT data element."""
    return (
        struct.pack("<II", data_type, len(data))    # [Type:4B] [Size:4B]
        + data                                      # [Payload:Size B]
        + b"\0" * (-len(data) % 8)                  # [Padding:0-7B]
    )


def write_mat(stream, rows, columns, samples):
    """Write little-endian, zlib-compressed Level 5 int32 matrices (MAT v7)."""
    count = rows * columns
    if rows <= 0 or columns <= 0:
        raise ValueError("无人机数量和帧数必须大于零")
    if set(samples) != set("xyzrgb") or any(
        len(values) != count for values in samples.values()
    ):
        raise ValueError("坐标或颜色矩阵尺寸不一致")
    if rows > 0x7FFFFFFF or columns > 0x7FFFFFFF or count * 4 + 72 >= 2**31:
        raise ValueError("矩阵过大，超出当前 MAT 格式限制")
    if any(values.typecode != "i" or values.itemsize != 4 for values in samples.values()):
        raise ValueError("坐标和颜色必须使用四字节 int32 数组")
    if any(value < 0 or value > 255 for name in "rgb" for value in samples[name]):
        raise ValueError("颜色必须为 0–255 整数")

    header = b"MATLAB 5.0 MAT-file, Created by Drone Animations"
    stream.write(
        header.ljust(116, b" ")     # [Description:116B]
        + b"\0" * 8                 # [Subsystem offset:8B]
        + struct.pack("<H", 0x0100) # [Version:2B]
        + b"IM"                     # [Endian:2B]
    )
    for name in ("x", "y", "z", "r", "g", "b", "Qn"):
        is_index = name == "Qn"
        matrix_columns = 1 if is_index else columns
        matrix_count = rows * matrix_columns
        metadata = (
            _element(6, struct.pack("<II", 12, 0))                # [Class:4B] [Reserved:4B]; int32=12
            + _element(5, struct.pack("<ii", rows, matrix_columns)) # [Rows:4B] [Columns:4B]
            + _element(1, name.encode("ascii"))                   # [Name:1-2B] [Padding:6-7B]
        )
        byte_count = matrix_count * 4
        padding = -byte_count % 8
        tag_offset = stream.tell()
        stream.write(struct.pack("<II", 15, 0))  # [Compressed type:4B] [Compressed size:4B]
        compressor = zlib.compressobj()

        def compress(data):
            stream.write(compressor.compress(data))

        compress(struct.pack("<II", 14, len(metadata) + 8 + byte_count + padding))
        compress(metadata)
        compress(struct.pack("<II", 5, byte_count))  # [Storage type:4B] [Data size:4B]; int32=5
        if is_index:
            # One index per drone, in the same order as coordinate rows.
            for start in range(0, rows, 8192):
                indices = range(start + 1, min(start + 8192, rows) + 1)
                compress(struct.pack("<" + "i" * len(indices), *indices)) # [Index:4B/drone]
        else:
            values = samples[name]
            if sys.byteorder != "little":
                values = values[:]  # Preserve the input array
                values.byteswap()  # Convert to little-endian
            data = memoryview(values).cast("B")
            for start in range(0, len(data), 1024 * 1024):
                compress(data[start:start + 1024 * 1024])
        compress(b"\0" * padding)  # [Padding:0-7B]
        stream.write(compressor.flush())
        end = stream.tell()
        stream.seek(tag_offset + 4)
        stream.write(struct.pack("<I", end - tag_offset - 8))
        stream.seek(end)  # Compressed elements have no trailing alignment padding.


def _rounded_int32(value, scale, label):
    """Round ties away from zero after validating the scaled value."""
    scaled = value * scale
    if not isfinite(scaled):
        raise ValueError(f"{label}: 数值不是有限数")
    result = floor(abs(scaled) + 0.5) * (-1 if scaled < 0 else 1)
    if not -2**31 <= result < 2**31:
        raise ValueError(f"{label}: 数值超出 int32 范围")
    return result


@dataclass(frozen=True)
class MATExportResult:
    destination: Path
    drone_count: int
    plan: PatternStudioSamplingPlan


def _export_drones(scene, *, ordered=True):
    import bpy

    collection = bpy.data.collections.get("Drones")
    drones = []
    if collection is not None and collection in scene.collection.children_recursive:
        drones = [
            obj for obj in collection.all_objects
            if obj.type == "MESH" and "drones_anim_vertex_index" in obj
            and obj.name in scene.objects
        ]
    # Tagged objects remain exportable even if their attachment is removed.
    if ordered:
        drones.sort(key=lambda obj: natural_name_key(obj.name))
    return drones


def _sample_drones(context, drones, plan):
    """Sample world positions and final procedural RGB at identical times."""
    from . import safety
    from .light_effects.runtime import final_colors

    scene = context.scene
    samples = {name: array("i") for name in "xyzrgb"}
    original_frame, original_subframe = scene.frame_current, scene.frame_subframe
    previous_scanning = safety._scanning
    try:
        safety._scanning = True
        for column, frame in enumerate(plan):
            integer_frame = floor(frame)
            scene.frame_set(integer_frame, subframe=frame - integer_frame)
            depsgraph = context.evaluated_depsgraph_get()
            ordered, colors, errors = final_colors(scene, depsgraph)
            if errors:
                raise ValueError("灯效采样失败：" + "; ".join(errors))
            final_by_id = {drone.as_pointer(): color for drone, color in zip(ordered, colors)}
            for drone in drones:
                evaluated = drone.evaluated_get(depsgraph)
                for axis, value in zip("xyz", evaluated.matrix_world.translation):
                    samples[axis].append(_rounded_int32(value, 100, f"{drone.name} / 帧 {frame:g} / {axis}"))
                for channel, value in zip("rgb", final_by_id[drone.as_pointer()]):
                    label = f"{drone.name} / 帧 {frame:g} / {channel}"
                    if not isfinite(value):
                        raise ValueError(f"{label}: 颜色不是有限数")
                    samples[channel].append(_rounded_int32(min(1.0, max(0.0, value)), 255, label))
            context.window_manager.progress_update(column + 1)
    finally:
        try:
            scene.frame_set(original_frame, subframe=original_subframe)
        finally:
            safety._scanning = previous_scanning
        if not previous_scanning:
            safety._update(scene, context.evaluated_depsgraph_get())
    return samples


def _write_mat_file(destination, rows, columns, samples):
    """Replace the destination only after a complete write."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, suffix=".mat.tmp", delete=False
        ) as stream:
            temporary_path = stream.name
            write_mat(stream, rows, columns, samples)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            Path(temporary_path).unlink(missing_ok=True)


def export_mat(context, filepath):
    """Sample exportable drones and write MAT, restoring the scene on failure."""
    import bpy

    from .video_projection import validate_video_export

    props = context.scene.drones_anim_panel
    plan = pattern_studio_sampling_plan(
        props.start_frame, props.end_frame,
        context.scene.render.fps / context.scene.render.fps_base,
    )
    drones = _export_drones(context.scene)
    if not drones:
        raise ValueError("没有可导出的无人机")
    if len(drones) * plan.count * 4 + 72 >= 2**31:
        raise ValueError("矩阵过大，超出当前 MAT 格式限制")
    validate_video_export(context, plan)
    destination = Path(bpy.path.abspath(bpy.path.ensure_ext(filepath, ".mat")))
    wm = context.window_manager
    wm.progress_begin(0, plan.count)
    try:
        samples = _sample_drones(context, drones, plan)
        _write_mat_file(destination, len(drones), plan.count, samples)
    finally:
        wm.progress_end()
    return MATExportResult(destination, len(drones), plan)
