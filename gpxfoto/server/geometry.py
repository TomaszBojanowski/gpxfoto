"""Tracks made lighter for drawing on the map; matching uses every point."""
import math

# Points the line keeps closer than this to its simplified course are left out
TOLERANCE = 2.0          # m
# The line is broken where the recording stopped for this long
LINE_BREAK = 300.0       # s

_M_PER_DEGREE = math.pi * 6371000.0 / 180


def track_lines(points, tolerance=TOLERANCE, line_break=LINE_BREAK):
    """The parts of a track as lists of [lon, lat], each simplified.

    points are (unix_time, lat, lon, elevation) sorted by time.
    """
    parts, start = [], 0
    for i in range(1, len(points) + 1):
        if i == len(points) or points[i][0] - points[i - 1][0] > line_break:
            part = points[start:i]
            if len(part) > 1:
                parts.append([[round(p[2], 6), round(p[1], 6)]
                              for p in simplify(part, tolerance)])
            start = i
    return parts


def simplify(points, tolerance):
    """The points of a line that keep its shape to within tolerance metres
    (Douglas–Peucker), with the first and the last always kept."""
    n = len(points)
    if n < 3:
        return list(points)
    lat0 = math.radians(sum(p[1] for p in points) / n)
    scale = _M_PER_DEGREE * math.cos(lat0)
    lon0 = points[0][2]

    def xy(p):
        change = (p[2] - lon0 + 180) % 360 - 180      # also across the 180° meridian
        return change * scale, p[1] * _M_PER_DEGREE

    coordinates = [xy(p) for p in points]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    limit = tolerance * tolerance
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = coordinates[first], coordinates[last]
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        worst, index = -1.0, None
        for i in range(first + 1, last):
            px, py = coordinates[i]
            if length:
                u = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
                ex, ey = ax + u * dx - px, ay + u * dy - py
            else:
                ex, ey = ax - px, ay - py
            distance = ex * ex + ey * ey
            if distance > worst:
                worst, index = distance, i
        if index is not None and worst > limit:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, kept in zip(points, keep) if kept]
