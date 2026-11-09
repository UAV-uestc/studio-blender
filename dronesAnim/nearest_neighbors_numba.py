"""Numeric kernels for the optional Numba proximity backend.

No Numba import occurs during module discovery. The caller compiles and warms
these kernels at registration, with NumPy retained if that fails.
"""

import numpy as np


def _closest_pair(points, order, axis):
    first, second = int(order[0]), int(order[1])
    best_sq = 0.0
    for dimension in range(points.shape[1]):
        delta = points[first, dimension] - points[second, dimension]
        best_sq += delta * delta
    for i in range(len(order) - 1):
        p = int(order[i])
        for j in range(i + 1, len(order)):
            q = int(order[j])
            delta_axis = points[q, axis] - points[p, axis]
            if delta_axis * delta_axis > best_sq:
                break
            distance_sq = 0.0
            for dimension in range(points.shape[1]):
                delta = points[p, dimension] - points[q, dimension]
                distance_sq += delta * delta
            if distance_sq < best_sq:
                first, second, best_sq = p, q, distance_sq
                if best_sq == 0.0:
                    return min(first, second), max(first, second), best_sq
    return min(first, second), max(first, second), best_sq


def _close_pairs(points, order, ends, threshold_sq):
    # Two passes allocate exactly the output size, not an N-by-N matrix.
    count = 0
    for i in range(len(order)):
        p = int(order[i])
        for j in range(i + 1, ends[i]):
            q = int(order[j])
            distance_sq = 0.0
            for dimension in range(points.shape[1]):
                delta = points[p, dimension] - points[q, dimension]
                distance_sq += delta * delta
            if distance_sq < threshold_sq:
                count += 1
    pairs = np.empty((count, 2), dtype=np.int64)
    count = 0
    for i in range(len(order)):
        p = int(order[i])
        for j in range(i + 1, ends[i]):
            q = int(order[j])
            distance_sq = 0.0
            for dimension in range(points.shape[1]):
                delta = points[p, dimension] - points[q, dimension]
                distance_sq += delta * delta
            if distance_sq < threshold_sq:
                pairs[count, 0] = min(p, q)
                pairs[count, 1] = max(p, q)
                count += 1
    return pairs


def build_kernels():
    """Compile both kernels before exposing them to frame handlers."""
    from numba import njit

    # Keep strict floating-point comparisons; fastmath can change warnings.
    closest = njit(nogil=True)(_closest_pair)
    pairs = njit(nogil=True)(_close_pairs)
    points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    order = np.array([0, 1], dtype=np.int64)
    ends = np.array([2, 2], dtype=np.int64)
    closest(points, order, 0)
    pairs(points, order, ends, 4.0)
    return closest, pairs
