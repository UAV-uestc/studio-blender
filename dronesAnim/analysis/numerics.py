"""Whole-animation motion curves, envelopes and unfiltered separation."""

from array import array
from dataclasses import dataclass
from itertools import product
from math import dist, hypot, isfinite, nan

try:
    from mathutils import Vector
    from mathutils.kdtree import KDTree
except ImportError:
    KDTree = None


METRICS = (("vx", "m/s"), ("ax", "m/s^2"),
           ("vy", "m/s"), ("ay", "m/s^2"),
           ("vz", "m/s"), ("az", "m/s^2"),
           ("vxy", "m/s"), ("distance", "m"))
_NEIGHBOR_CELLS = tuple(product((-1, 0, 1), repeat=3))


@dataclass
class MotionPlot:
    name: str
    unit: str
    curves: list
    lower: array
    upper: array
    minimum: float | None
    maximum: float | None


@dataclass
class MotionAnalysis:
    start: int
    end: int
    fps: float
    names: tuple
    plots: tuple
    closest_frame: int | None
    closest_pair: tuple | None


def _brute_pair(points, count):
    """Find a seed pair among a prefix, or scan all points as a fallback."""
    pair = (0, 1)
    best = dist(points[0], points[1])
    if best == 0:
        return best, pair
    for first in range(count):
        point = points[first]
        for second in range(first + 1, count):
            distance = dist(point, points[second])
            if distance < best:
                best, pair = distance, (first, second)
                if best == 0:
                    return best, pair
    return best, pair


def _cell_key(point, width_numerator, width_denominator):
    """Floor exact float ratios so cell boundaries cannot round away a pair."""
    x, xd = point[0].as_integer_ratio()
    y, yd = point[1].as_integer_ratio()
    z, zd = point[2].as_integer_ratio()
    return (x * width_denominator // (xd * width_numerator),
            y * width_denominator // (yd * width_numerator),
            z * width_denominator // (zd * width_numerator))


def nearest_pair_buckets(points, previous_pair=None):
    """Find the exact nearest pair using a current-frame bound and 3D buckets."""
    count = len(points)
    if count < 2:
        return nan, None
    # Small clouds cost less to scan than to hash 27 neighbors per point.
    if count <= 64:
        return _brute_pair(points, count)
    if previous_pair is None:
        best, pair = _brute_pair(points, 32)
    else:
        pair = previous_pair
        best = dist(points[pair[0]], points[pair[1]])
    if best == 0:
        return best, pair
    if not isfinite(best):
        return _brute_pair(points, count)

    # Keep this width fixed; every closer pair lies in neighboring cells.
    numerator, denominator = best.as_integer_ratio()
    buckets = {}
    for second, point in enumerate(points):
        x, y, z = _cell_key(point, numerator, denominator)
        for dx, dy, dz in _NEIGHBOR_CELLS:
            for first in buckets.get((x + dx, y + dy, z + dz), ()):
                distance = dist(points[first], point)
                if distance < best:
                    best, pair = distance, (first, second)
                    if best == 0:
                        return best, pair
        buckets.setdefault((x, y, z), []).append(second)
    return best, pair


def nearest_pair_kdtree(points, previous_pair=None):
    """Search native KD ranges, then measure candidates in original precision."""
    count = len(points)
    if count < 2:
        return nan, None
    if KDTree is None:
        return nearest_pair_buckets(points, previous_pair)
    if previous_pair is None:
        best, pair = _brute_pair(points, min(count, 32))
    else:
        pair = previous_pair
        best = dist(points[pair[0]], points[pair[1]])
    if best == 0:
        return best, pair
    if not 1e-18 <= best <= 1e18:
        return nearest_pair_buckets(points, previous_pair)

    tree = KDTree(count)
    coordinates = []
    error = 0.0
    for index, point in enumerate(points):
        coordinate = Vector(point)
        # Native squared distances require a safe single-precision scale.
        if any(not isfinite(value) or abs(value) > 1e18 for value in coordinate):
            return nearest_pair_buckets(points, previous_pair)
        error = max(error, dist(point, coordinate))
        coordinates.append(coordinate)
        tree.insert(coordinate, index)
    if error > best * 1e-4:
        return nearest_pair_buckets(points, previous_pair)
    tree.balance()
    for first, coordinate in enumerate(coordinates):
        # Pad quantization and float arithmetic; equal native distances can tie.
        radius = max(1e-18, (best + 2 * error) * (1 + 1e-6))
        for _coordinate, second, _distance in tree.find_range(coordinate, radius):
            if second <= first:
                continue
            distance = dist(points[first], points[second])
            if distance < best:
                best, pair = distance, (first, second)
                if best == 0:
                    return best, pair
    return best, pair


def nearest_pair(points, previous_pair=None):
    """Use native KD searches for every cloud size."""
    return nearest_pair_kdtree(points, previous_pair)


def derivatives(values, fps):
    """Central differences; one-sided endpoints; undefined short traces use NaN."""
    count = len(values)
    velocity = array("d", [nan]) * count
    acceleration = array("d", [nan]) * count
    if count < 2:
        return velocity, acceleration
    if count == 2:
        velocity[0] = velocity[1] = (values[1] - values[0]) * fps
        return velocity, acceleration
    velocity[0] = (-3 * values[0] + 4 * values[1] - values[2]) * fps / 2
    velocity[-1] = (3 * values[-1] - 4 * values[-2] + values[-3]) * fps / 2
    for index in range(1, count - 1):
        velocity[index] = (values[index + 1] - values[index - 1]) * fps / 2
        acceleration[index] = (
            values[index + 1] - 2 * values[index] + values[index - 1]
        ) * fps * fps
    if count == 3:
        acceleration[0] = acceleration[-1] = acceleration[1]
    else:
        acceleration[0] = (2 * values[0] - 5 * values[1] + 4 * values[2] - values[3]) * fps * fps
        acceleration[-1] = (2 * values[-1] - 5 * values[-2] + 4 * values[-3] - values[-4]) * fps * fps
    return velocity, acceleration


def make_plot(name, unit, curves, count):
    lower, upper = array("d", [nan]) * count, array("d", [nan]) * count
    minimum = maximum = None
    for index in range(count):
        values = [curve[index] for curve in curves if isfinite(curve[index])]
        if values:
            low, high = min(values), max(values)
            lower[index], upper[index] = low, high
            minimum = low if minimum is None else min(minimum, low)
            maximum = high if maximum is None else max(maximum, high)
    return MotionPlot(name, unit, curves, lower, upper, minimum, maximum)


class AnalysisBuilder:
    def __init__(self, start, end, fps, names):
        if end < start or not isfinite(fps) or fps <= 0 or not names:
            raise ValueError("需要有效的帧范围、场景帧率和无人机")
        self.start, self.end, self.fps = start, end, fps
        self.names = tuple(names)
        self.positions = [[array("d") for _ in range(3)] for _ in names]
        self.distances = array("d")
        self.closest_frame = self.closest_pair = None
        self.best_distance = float("inf")
        self._previous_pair = None
        self.result = None

    def append(self, points):
        if len(points) != len(self.names):
            raise ValueError("采样期间无人机数量发生变化")
        if len(self.distances) >= self.end - self.start + 1:
            raise ValueError("采样点超出帧范围")
        if any(len(point) != 3 or not all(isfinite(value) for value in point) for point in points):
            raise ValueError("无人机坐标包含非有限值或无效维度")
        distance, pair = nearest_pair(points, self._previous_pair)
        self._previous_pair = pair
        if pair is not None and distance < self.best_distance:
            self.best_distance = distance
            self.closest_frame = self.start + len(self.distances)
            self.closest_pair = tuple(self.names[index] for index in pair)
        for traces, point in zip(self.positions, points):
            for trace, value in zip(traces, point):
                trace.append(value)
        self.distances.append(distance)

    def finish_steps(self):
        """Yield progress between drones and plots to keep Blender responsive."""
        count = self.end - self.start + 1
        if len(self.distances) != count:
            raise ValueError("动画采样不完整")
        curves = {name: [] for name, _ in METRICS}
        total = len(self.names) + len(METRICS)
        for index, traces in enumerate(self.positions):
            velocities = []
            for axis, trace in zip("xyz", traces):
                velocity, acceleration = derivatives(trace, self.fps)
                curves[f"v{axis}"].append(velocity)
                curves[f"a{axis}"].append(acceleration)
                velocities.append(velocity)
            curves["vxy"].append(array("d", (
                hypot(x, y) for x, y in zip(velocities[0], velocities[1])
            )))
            yield (index + 1) / total
        curves["distance"].append(self.distances)
        plots = []
        for index, (name, unit) in enumerate(METRICS):
            plots.append(make_plot(name, unit, curves[name], count))
            yield (len(self.names) + index + 1) / total
        self.result = MotionAnalysis(self.start, self.end, self.fps, self.names,
                                     tuple(plots), self.closest_frame, self.closest_pair)
        self.positions.clear()
