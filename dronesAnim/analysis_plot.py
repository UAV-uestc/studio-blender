"""GPU-only eight-chart canvas; no plotting packages or exported files."""

from colorsys import hsv_to_rgb
from math import ceil, isfinite, log10

_geometry = {}


def clear_cache():
    _geometry.clear()


def graph_rectangles(width, height):
    margin, gap = 16, 16
    columns, rows = 2, 4
    cell_width = (width - 2 * margin - (columns - 1) * gap) / columns
    cell_height = (height - 96 - (rows - 1) * gap) / rows
    result = []
    for index in range(columns * rows):
        left = margin + (index % columns) * (cell_width + gap)
        bottom = height - 48 - (index // columns + 1) * cell_height - (index // columns) * gap
        result.append((left + 66, bottom + 28, cell_width - 82, cell_height - 60))
    return result


def clicked_frame(data, width, height, x, y):
    if width < 680 or height < 540:
        return None
    for left, bottom, plot_width, plot_height in graph_rectangles(width, height):
        if left <= x <= left + plot_width and bottom <= y <= bottom + plot_height:
            ratio = (x - left) / plot_width
            return round(data.start + ratio * (data.end - data.start))
    return None


def _number(value):
    return f"{value:.4g}"


def _tick_number(value, span):
    precision = max(0, min(9, ceil(-log10(span / 4)) + 1))
    return f"{value:.{precision}f}"


def _selected_indices(values, pixels):
    """Retain each pixel bucket's extrema rather than silently dropping spikes."""
    stride = max(1, ceil(len(values) / max(1, int(pixels))))
    if stride == 1:
        return range(len(values))
    indices = []
    for start in range(0, len(values), stride):
        end = min(start + stride, len(values))
        valid = [index for index in range(start, end) if isfinite(values[index])]
        if not valid:
            indices.append(start)
        else:
            indices.extend(sorted({valid[0], valid[-1],
                                   min(valid, key=values.__getitem__),
                                   max(valid, key=values.__getitem__)}))
    return indices


def _build(data, width, height, style, seconds, interactive):
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    batches, labels = [], []

    def batch(kind, vertices, color):
        if vertices:
            batches.append((batch_for_shader(shader, kind, {"pos": vertices}), color, kind))

    def rectangle(left, bottom, w, h, color):
        a, b = (left, bottom), (left + w, bottom)
        c, d = (left + w, bottom + h), (left, bottom + h)
        batch("TRIS", (a, b, c, a, c, d), color)

    rectangle(0, 0, width, height, (0.055, 0.07, 0.10, 1))
    labels.append((18, height - 27,
                   f"Drone motion  |  {len(data.names)} drones  |  frames {data.start} - {data.end}  |  {data.fps:g} fps",
                   16, (0.92, 0.95, 1, 1)))
    count = data.end - data.start + 1
    for plot, rect in zip(data.plots, graph_rectangles(width, height)):
        left, bottom, plot_width, plot_height = rect
        rectangle(left, bottom, plot_width, plot_height, (0.085, 0.105, 0.145, 1))
        low, high = plot.minimum, plot.maximum
        if low is None:
            low, high = -1, 1
            title = f"{plot.name} [{plot.unit}]  |  no data"
        else:
            limits = f"{low:.7f} .. {high:.7f}" if plot.name == "distance" else f"{_number(low)} .. {_number(high)}"
            title = f"{plot.name} [{plot.unit}]  |  {limits}"
        labels.append((left - 55, bottom + plot_height + 13, title, 13, (0.9, 0.93, 0.98, 1)))
        padding = (high - low) * 0.08 if high > low else max(abs(low) * 0.05, 0.5)
        low, high = low - padding, high + padding

        def point(index, value):
            return (left + (index / (count - 1) if count > 1 else 0.5) * plot_width,
                    bottom + (value - low) / (high - low) * plot_height)

        grid = []
        for tick in range(5):
            fraction = tick / 4
            x, y = left + fraction * plot_width, bottom + fraction * plot_height
            grid.extend(((x, bottom), (x, bottom + plot_height),
                         (left, y), (left + plot_width, y)))
            frame = data.start + fraction * (data.end - data.start)
            value = (frame - data.start) / data.fps if seconds else frame
            labels.append((x - 12, bottom - 19, _number(value), 10, (0.60, 0.66, 0.75, 1)))
            labels.append((left - 61, y - 4, _tick_number(low + fraction * (high - low), high - low),
                           10, (0.60, 0.66, 0.75, 1)))
        batch("LINES", grid, (0.24, 0.29, 0.36, 0.7))
        if low < 0 < high:
            batch("LINES", (point(0, 0), point(count - 1, 0)), (0.43, 0.49, 0.59, 0.8))

        def curve_line(values, color):
            vertices, previous = [], None
            indices = _selected_indices(values, plot_width)
            for index in indices:
                if not isfinite(values[index]):
                    previous = None
                    continue
                current = point(index, values[index])
                if previous is not None:
                    vertices.extend((previous, current))
                previous = current
            batch("LINES", vertices, color)
            if count == 1 and isfinite(values[0]):
                batch("POINTS", (point(0, values[0]),), color)

        if plot.name != "distance" and style in {"ENVELOPE", "BOTH"}:
            stride = max(1, ceil(count / max(1, int(plot_width))))
            band = []
            for start in range(0, count, stride):
                end = min(start + stride, count)
                valid = [index for index in range(start, end) if isfinite(plot.lower[index])]
                if valid:
                    band.append(((start + end - 1) / 2,
                                 min(plot.lower[index] for index in valid),
                                 max(plot.upper[index] for index in valid)))
            vertices, outlines = [], []
            for a, b in zip(band, band[1:]):
                al, au, bl, bu = point(a[0], a[1]), point(a[0], a[2]), point(b[0], b[1]), point(b[0], b[2])
                vertices.extend((al, bl, bu, al, bu, au))
                outlines.extend((al, bl, au, bu))
            batch("TRIS", vertices, (0.15, 0.77, 0.92, 0.18))
            batch("LINES", outlines, (0.25, 0.84, 0.98, 1))
            if count == 1 and band:
                batch("LINES", (point(0, band[0][1]), point(0, band[0][2])), (0.25, 0.84, 0.98, 1))
        if plot.name == "distance" or style in {"CURVES", "BOTH"}:
            for index, values in enumerate(plot.curves):
                color = (0.28, 0.83, 1, 1) if plot.name == "distance" else (*hsv_to_rgb((index * 0.618034) % 1, 0.66, 0.95), 0.8)
                curve_line(values, color)
    unit = "seconds from start" if seconds else "scene frame"
    footer = f"X: {unit}"
    if interactive:
        footer += "  |  click a chart to seek  |  Esc to close"
    labels.append((18, 22, footer, 11, (0.60, 0.66, 0.75, 1)))
    if data.closest_frame is not None:
        footer = f"Minimum distance: {_number(data.plots[-1].minimum)} m  |  frame {data.closest_frame}  |  {' / '.join(data.closest_pair)}"
        labels.append((18, 6, footer, 11, (0.28, 0.83, 1, 1)))
    return shader, batches, labels


def draw_analysis(data, width, height, style="ENVELOPE", seconds=True, current_frame=None,
                  stale=False, interactive=True):
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader

    if width < 680 or height < 540:
        blf.size(0, 16)
        blf.color(0, 1, 0.8, 0.4, 1)
        blf.position(0, 16, height - 40, 0)
        blf.draw(0, "Enlarge this view to at least 680 x 540, or open a chart window.")
        return
    key = (id(data), width, height, style, seconds, interactive)
    if key not in _geometry:
        if len(_geometry) >= 8:
            _geometry.clear()
        _geometry[key] = _build(data, width, height, style, seconds, interactive)
    shader, batches, labels = _geometry[key]
    old_blend = gpu.state.blend_get()
    old_depth = gpu.state.depth_test_get()
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    try:
        for batch, color, kind in batches:
            shader.bind()
            shader.uniform_float("color", color)
            if kind == "POINTS":
                gpu.state.point_size_set(5)
            batch.draw(shader)
        if current_frame is not None and data.start <= current_frame <= data.end:
            fraction = (current_frame - data.start) / (data.end - data.start) if data.end > data.start else 0.5
            vertices = []
            for left, bottom, w, h in graph_rectangles(width, height):
                x = left + fraction * w
                vertices.extend(((x, bottom), (x, bottom + h)))
            shader.bind()
            shader.uniform_float("color", (1, 0.69, 0.25, 0.9))
            batch_for_shader(shader, "LINES", {"pos": vertices}).draw(shader)
        for x, y, text, size, color in labels:
            blf.size(0, size)
            blf.color(0, *color)
            blf.position(0, x, y, 0)
            blf.draw(0, text)
        if stale:
            blf.size(0, 12)
            blf.color(0, 1, 0.48, 0.32, 1)
            blf.position(0, 18, height - 44, 0)
            blf.draw(0, "Scene frame rate or range changed. Recalculate to refresh.")
    finally:
        gpu.state.blend_set(old_blend)
        gpu.state.depth_test_set(old_depth)


def export_png(data, filepath, width=1600, height=1200, style="ENVELOPE", seconds=True,
               current_frame=None, stale=False):
    """Render all eight plots independently of the viewport and its UI regions."""
    import bpy
    import gpu
    from mathutils import Matrix

    viewport, scissor = gpu.state.viewport_get(), gpu.state.scissor_get()
    offscreen = gpu.types.GPUOffScreen(width, height)
    image = None
    try:
        with offscreen.bind():
            gpu.state.viewport_set(0, 0, width, height)
            gpu.state.scissor_set(0, 0, width, height)
            with gpu.matrix.push_pop():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix((
                    (2 / width, 0, 0, -1), (0, 2 / height, 0, -1),
                    (0, 0, -1, 0), (0, 0, 0, 1))))
                framebuffer = gpu.state.active_framebuffer_get()
                framebuffer.clear(color=(0, 0, 0, 1))
                draw_analysis(data, width, height, style, seconds, current_frame, stale,
                              interactive=False)
                buffer = framebuffer.read_color(0, 0, width, height, 4, 0, "FLOAT")
                buffer.dimensions = width * height * 4
                image = bpy.data.images.new("Drone motion export", width=width, height=height,
                                            alpha=False)
                image.pixels.foreach_set(buffer)
        image.filepath_raw = filepath
        image.file_format = "PNG"
        image.save()
    finally:
        if image is not None:
            bpy.data.images.remove(image)
        offscreen.free()
        gpu.state.viewport_set(*viewport)
        gpu.state.scissor_set(*scissor)
