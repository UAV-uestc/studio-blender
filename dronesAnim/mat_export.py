"""Dependency-free Level 5 MAT writer for the six drone show matrices."""

import struct
import sys


def _element(data_type, data):
    """Build a MAT data element."""
    return (
        struct.pack("<II", data_type, len(data))    # [Type:4B] [Size:4B]
        + data                                      # [Payload:Size B]
        + b"\0" * (-len(data) % 8)                  # [Padding:0-7B]
    )


def write_mat(stream, rows, columns, coordinates):
    """Write six matrices; input coordinates use frame-major / column-major order."""
    count = rows * columns
    if rows <= 0 or columns <= 0:
        raise ValueError("无人机数量和帧数必须大于零")
    if set(coordinates) != {"x", "y", "z"} or any(
        len(values) != count for values in coordinates.values()
    ):
        raise ValueError("坐标矩阵尺寸不一致")
    if rows > 0x7FFFFFFF or columns > 0x7FFFFFFF or count * 8 + 64 >= 2**31:
        raise ValueError("矩阵过大，超出当前 MAT 格式限制")
    if any(values.typecode != "d" or values.itemsize != 8 for values in coordinates.values()):
        raise ValueError("坐标必须使用八字节 double 数组")

    header = b"MATLAB 5.0 MAT-file, Created by Drone Animations"
    stream.write(
        header.ljust(116, b" ")     # [Description:116B]
        + b"\0" * 8                 # [Subsystem offset:8B]
        + struct.pack("<H", 0x0100) # [Version:2B]
        + b"IM"                     # [Endian:2B]
    )
    for name in ("r", "b", "g", "x", "y", "z"):
        is_color = name in "rbg"
        metadata = (
            _element(6, struct.pack("<II", 6, 0))                 # [Class:4B] [Reserved:4B]; double=6
            + _element(5, struct.pack("<ii", rows, columns))        # [Rows:4B] [Columns:4B]
            + _element(1, name.encode("ascii"))                   # [Name:1B] [Padding:7B]
        )
        byte_count = count * 8
        padding = -byte_count % 8
        stream.write(struct.pack("<II", 14, len(metadata) + 8 + byte_count + padding))  # [Matrix type:4B] [Content size:4B]
        stream.write(metadata)
        stream.write(struct.pack("<II", 9, byte_count))                               # [Storage type:4B] [Data size:4B]; double=9
        if is_color:
            chunk_count = min(count, 8192)
            chunk = struct.pack("<d", 255.0) * chunk_count  # [White:8B/sample], in bounded chunks
            for offset in range(0, count, chunk_count):
                stream.write(chunk[:min(chunk_count, count - offset) * 8])
        else:
            values = coordinates[name]
            if sys.byteorder != "little":
                values = values[:]  # Preserve the input array
                values.byteswap()  # Convert to little-endian
            stream.write(memoryview(values).cast("B"))  # [Coordinates:count*8B]
        stream.write(b"\0" * padding)  # [Padding:0-7B]
