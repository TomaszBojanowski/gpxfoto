"""Tracks made lighter for the map (gpxfoto.server.geometry)."""
import math

from gpxfoto.server.geometry import simplify, track_lines

M = math.pi * 6371000.0 / 180


def points_along(offsets, lat=50.0, lon=20.0, step=1.0):
    """Points one a second, offsets in metres (east, north) from (lat, lon)."""
    scale = M * math.cos(math.radians(lat))
    return [(k * step, lat + north / M, lon + east / scale, None)
            for k, (east, north) in enumerate(offsets)]


def distance_to_line(p, line, lat=50.0):
    scale = M * math.cos(math.radians(lat))
    best = math.inf
    for a, b in zip(line, line[1:]):
        ax, ay, bx, by = a[2] * scale, a[1] * M, b[2] * scale, b[1] * M
        px, py = p[2] * scale, p[1] * M
        dx, dy = bx - ax, by - ay
        u = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)))
        best = min(best, math.hypot(ax + u * dx - px, ay + u * dy - py))
    return best


def test_a_straight_walk_keeps_its_ends():
    points = points_along([(1.2 * k, 0.0) for k in range(1000)])
    assert simplify(points, 2.0) == [points[0], points[-1]]


def test_every_point_stays_within_the_tolerance():
    offsets = [(10 * math.cos(k / 30) * k / 10, 10 * math.sin(k / 30) * k / 10)
               for k in range(2000)]
    points = points_along(offsets)
    kept = simplify(points, 2.0)
    assert len(kept) < len(points) // 2
    assert kept[0] == points[0] and kept[-1] == points[-1]
    assert max(distance_to_line(p, kept) for p in points) <= 2.0 + 1e-6


def test_a_turn_is_kept():
    points = points_along([(k, 0.0) for k in range(50)] + [(49, k) for k in range(1, 50)])
    assert points[49] in simplify(points, 2.0)


def test_lines_are_broken_where_the_recording_stopped():
    first = points_along([(k, 0.0) for k in range(10)])
    second = [(t + 400, lat, lon, ele) for t, lat, lon, ele in first[1:]]
    parts = track_lines(first + second)
    assert len(parts) == 2
    assert parts[0] == [[20.0, 50.0], [round(first[9][2], 6), 50.0]]


def test_a_single_point_draws_no_line():
    assert track_lines(points_along([(0.0, 0.0)])) == []
    assert track_lines([]) == []


def test_across_the_180th_meridian():
    points = [(k, 0.0, 179.9999 + k * 0.00001, None) for k in range(20)]
    points = [(t, lat, lon - 360 if lon > 180 else lon, ele) for t, lat, lon, ele in points]
    kept = simplify(points, 2.0)
    assert kept == [points[0], points[-1]]
