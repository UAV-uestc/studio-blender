"""Ramp evaluation and cached pixel sampling for light effects.

Adapted from Skybrush Studio for Blender (GPL-3.0-or-later).
"""

import numpy as np


def evaluate_ramp(ramp, coordinates):
    """Evaluate a Blender color ramp, marking non-finite outputs inactive."""
    coordinates = np.asarray(coordinates, dtype=np.float32)
    result = np.full((len(coordinates), 4), np.nan, dtype=np.float32)
    for index in np.flatnonzero(np.isfinite(coordinates)):
        result[index] = ramp.evaluate(float(coordinates[index]))
    return result


def linearize_pixels(pixels, colorspace):
    """Apply the source's sRGB-to-linear approximation while preserving alpha."""
    result = np.asarray(pixels, dtype=np.float32).copy()
    if colorspace == "sRGB":
        result[..., :3] = np.power(result[..., :3], 2.2)
    elif colorspace not in ("", "Linear", "Linear Rec.709", "Non-Color", "Raw"):
        raise ValueError(f"Unsupported image color space: {colorspace}")
    return result


def sample_pixels(pixels, x, y):
    """Sample row-major RGBA pixels using source-compatible nearest coordinates."""
    pixels = np.asarray(pixels, dtype=np.float32)
    x = np.asarray(x, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)
    if x.ndim != 1 or x.shape != y.shape:
        raise ValueError("Image coordinates must be matching one-dimensional arrays")
    if pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError("Image pixels must have shape (height, width, 4)")
    result = np.full((len(x), 4), np.nan, dtype=np.float32)
    height, width = pixels.shape[:2]
    if not height or not width:
        return result
    active = np.isfinite(x) & np.isfinite(y)
    cols = np.minimum((np.clip(x[active], 0, 1) * width).astype(int), width - 1)
    rows = np.minimum((np.clip(y[active], 0, 1) * height).astype(int), height - 1)
    result[active] = pixels[rows, cols]
    return result


class ImagePixelCache:
    """Cache image pixels by identity, dimensions, color space and frame."""

    def __init__(self):
        self._entries = {}

    def clear(self):
        self._entries.clear()

    def invalidate(self, image):
        self._entries.pop(image.as_pointer(), None)

    def get(self, image, frame=None, *, texture=None):
        identity = image.as_pointer()
        settings = image.colorspace_settings
        colorspace = "" if settings.is_data else settings.name
        dynamic = image.source in {"SEQUENCE", "MOVIE"}
        if dynamic and (texture is None or texture.image != image or frame is None):
            raise ValueError("Animated images require their texture and a frame")
        if dynamic:
            key = (texture.as_pointer(), colorspace, int(frame))
        else:
            key = (tuple(image.size), colorspace)
        cached = self._entries.get(identity)
        if cached is None or cached[0] != key:
            previous_frame = None
            if dynamic:
                previous_frame = texture.image_user.frame_current
                texture.image_user.frame_current = int(frame)
            try:
                if dynamic:
                    # Image.pixels reads the buffer most recently loaded for an
                    # image user; texture evaluation loads this user's frame.
                    texture.evaluate((0.0, 0.0, 0.0))
                width, height = image.size
                expected = width * height * 4
                if width < 0 or height < 0 or len(image.pixels) != expected:
                    raise ValueError(f"Image {image.name!r} has invalid RGBA pixels")
                pixels = np.empty(expected, dtype=np.float32)
                image.pixels.foreach_get(pixels)
                pixels = linearize_pixels(pixels.reshape((height, width, 4)), colorspace)
            finally:
                if dynamic:
                    texture.image_user.frame_current = previous_frame
            self._entries[identity] = key, pixels
        return self._entries[identity][1]
