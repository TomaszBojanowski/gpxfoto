"""Loading GPX tracks and finding the position at a given moment."""
import bisect
import math
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import datetime, timezone
from gettext import gettext as _
from itertools import accumulate

from gpxfoto.i18n import duration

# Elevations further from sea level, in metres, are errors in the track:
# balloons rise to about 40 km, and the deepest sea is 11 km deep
MAX_ELEVATION = 100_000.0
# Track times within two days of the ends of the calendar are errors in
# the track; Python cannot show them in every local time zone
EARLIEST = datetime(1, 1, 3, tzinfo=timezone.utc).timestamp()
LATEST = datetime(9999, 12, 30, tzinfo=timezone.utc).timestamp()

# Stops. A point is still when the mean positions of the STILL_WINDOW
# seconds before it and after it are close together: GPS jitter cancels
# out in the means, while walking, however slow, adds up. Height tells
# slow, steep climbing, during which watches often freeze the position,
# from standing. The values were calibrated on a real mountain hike.
STILL_WINDOW = 30.0      # s
STILL_SPEED = 0.2        # m/s (0.7 km/h); walking is at least 0.5 m/s
STILL_CLIMB = 0.03       # m/s (108 m/h); slow, steep climbing is 150 m/h or more
# Longer times between two points are breaks in recording (e.g. auto-pause)
RECORDING_BREAK = STILL_WINDOW
# A stop needs this much still time; still parts this close in time and
# within STOP_RADIUS and STOP_HEIGHT of each other are one stop
STOP_MIN_STILL = STILL_WINDOW
STOP_MERGE = 2 * STILL_WINDOW
# The edges of a stop: where the track comes this close to its position;
# also how far apart the two ends of a break in recording may be. While
# standing, 99% of the points were within 9 m of the stop's position.
STOP_RADIUS = 10.0       # m
STOP_HEIGHT = 3.0        # m
# A photo taken during a stop gets the stop's position only when the track
# is no further from it at that moment; while standing, a point was up to
# 18 m away and the barometric elevation 3 m above or below
PIN_RADIUS = 20.0        # m
PIN_HEIGHT = 5.0         # m
# Elevations that jump more than this from point to point (the median of
# the second differences) come from GPS, not a barometer, and are too
# noisy to tell climbing from standing
ELEVATION_NOISE = 0.5    # m

_M_PER_DEGREE = math.pi * 6371000.0 / 180


# A stretch of the track without movement: start and end are the Unix
# times of its first and last point (both included), lat, lon and
# elevation (None if unknown) the median position of its still points,
# first and last the indices of its first and last point in the track
Stop = namedtuple("Stop", "start end lat lon elevation first last")


def _median(values):
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _parse_time(text):
    text = text.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    time = datetime.fromisoformat(text)
    if time.tzinfo is None:          # GPX times are UTC by definition
        time = time.replace(tzinfo=timezone.utc)
    return time.astimezone(timezone.utc)


class Track:
    """The points of one or more GPX files, used together as one track.

    named: the files were given by the user, not found in a directory.
    stops: the stops of the track, from find_stops(). sources: for each
    point, the index in files of the file it comes from, or None when
    there is one file.
    """
    __slots__ = ("files", "named", "points", "times", "stops", "sources")

    def __init__(self, files, named, points, stops=(), sources=None):
        self.files = tuple(files)
        self.named = named
        self.points = points
        self.times = [p[0] for p in points]
        self.stops = list(stops)
        self.sources = sources

    @property
    def first(self):
        return self.times[0]

    @property
    def last(self):
        return self.times[-1]

    def files_at(self, t):
        """The files of the points that the position at Unix time t comes from."""
        if self.sources is None:
            return self.files
        i = bisect.bisect_left(self.times, t)
        if i < len(self.times) and self.times[i] == t:
            used = {self.sources[i]}
        else:
            used = {self.sources[j] for j in (i - 1, i) if 0 <= j < len(self.times)}
        return tuple(self.files[j] for j in sorted(used))


def load_track(paths, named=True, stops=True):
    """Return the Track of the GPX files at paths, or None if they hold no points.

    Without stops, no stops are looked for. Raises ValueError like
    load_gpx().
    """
    points, sources = _load_points(paths)
    if not points:
        return None
    return Track(paths, named, points, find_stops(points) if stops else (),
                 sources if len(paths) > 1 else None)


# Where a moment lies on the tracks: a position, or the reason there is
# none; stop is the stop whose position it is, if any
Match = namedtuple("Match", "lat lon ele gap track reason stop", defaults=(None,) * 7)


def match(tracks, t, max_gap):
    """Return the Match of Unix time t on tracks."""
    track = tracks[0]
    result = place(track.points, track.times, track.stops, t, max_gap)
    if result[0] is None:
        return Match(track=track, reason=result[1])
    lat, lon, ele, gap, stop = result
    return Match(lat, lon, ele, gap, track, stop=stop)


def load_gpx(paths):
    """Return a sorted list of (unix_time, lat, lon, elevation|None).

    Raises ValueError naming the file when a file cannot be read, is not
    valid XML or uses an encoding that cannot be decoded.
    """
    return _load_points(paths)[0]


def _load_points(paths):
    """Return the points of the files at paths sorted by time, and for each
    point the index of its file in paths."""
    found = []
    for index, path in enumerate(paths):
        try:
            found += [(point, index) for point in _read_points(path)]
        except (OSError, ET.ParseError, ValueError, LookupError) as e:
            error = e.strerror if isinstance(e, OSError) and e.strerror else e
            # Translators: {error} describes the problem, e.g. “No such file or directory”
            raise ValueError(_("Cannot read the GPX file {path}: {error}").format(
                path=path, error=error)) from e
    found.sort(key=lambda pair: pair[0][0])
    return [point for point, _index in found], [index for _point, index in found]


def _read_points(path):
    points = []
    for _event, el in ET.iterparse(path):
        if _local_name(el.tag) != "trkpt":
            continue
        time_text = ele_text = None
        for child in el:
            name = _local_name(child.tag)
            if name == "time" and child.text:
                time_text = child.text
            elif name == "ele" and child.text:
                ele_text = child.text
        if time_text is not None:
            try:
                point = (_parse_time(time_text).timestamp(), float(el.attrib["lat"]),
                         float(el.attrib["lon"]), _elevation(ele_text))
            except (ValueError, KeyError, OverflowError):    # overflow: beyond year 9999
                pass
            else:
                # Also false for NaN; rules out infinity
                if (-90 <= point[1] <= 90 and -180 <= point[2] <= 180
                        and EARLIEST <= point[0] < LATEST):
                    points.append(point)
        el.clear()
    return points


def _elevation(text):
    """Elevation in metres, or None when it is missing or not a usable number."""
    if text is None:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    # Also false for NaN and infinity
    return value if abs(value) <= MAX_ELEVATION else None


def _distance_m(a, b):
    r = 6371000.0
    f1, f2 = math.radians(a[1]), math.radians(b[1])
    df, dl = f2 - f1, math.radians(b[2] - a[2])
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def locate(points, times, t, max_gap):
    """Return (lat, lon, elevation, gap_s) or (None, reason)."""
    i = bisect.bisect_left(times, t)
    if i == 0:
        before, after = None, points[0]
    elif i == len(points):
        before, after = points[-1], None
    else:
        before, after = points[i - 1], points[i]

    if before is None or after is None:
        p = after or before
        gap = abs(p[0] - t)
        if gap > max_gap:
            if before is None:
                # Translators: reason why a photo was skipped; {duration} is
                # a time span such as “10 min”
                reason = _("{duration} before the start of the track")
            else:
                # Translators: reason why a photo was skipped; {duration} is
                # a time span such as “10 min”
                reason = _("{duration} after the end of the track")
            return None, reason.format(duration=duration(gap))
        return p[1], p[2], p[3], gap

    gap = min(t - before[0], after[0] - t)
    # A longer break in recording (e.g. auto-pause) is fine as long as
    # the position changed by less than 100 m during it.
    if gap > max_gap and _distance_m(before, after) >= 100:
        # Translators: reason why a photo was skipped; {duration} is a time
        # span such as “10 min”
        return None, _("gap in the track recording, nearest point {duration} away").format(
            duration=duration(gap))
    span = after[0] - before[0]
    u = (t - before[0]) / span if span > 0 else 0.0
    lat = _between(before[1], after[1], u)
    # The shorter way round, which crosses the 180° meridian when needed
    lon_change = after[2] - before[2]
    if lon_change > 180:
        lon_change -= 360
    elif lon_change < -180:
        lon_change += 360
    lon = before[2] + lon_change * u
    if lon > 180:
        lon -= 360
    elif lon < -180:
        lon += 360
    if before[3] is not None and after[3] is not None:
        ele = _between(before[3], after[3], u)
    else:
        ele = before[3] if before[3] is not None else after[3]
    return lat, lon, ele, gap


def find_stops(points):
    """Return the stops of a track from load_gpx, in order of time.

    Stops do not overlap. A stop at either end of the track covers only
    its recorded part: what happened before or after is not known.
    """
    n = len(points)
    if n < 2:
        return []
    times = [p[0] for p in points]
    lons = _unwrapped_longitudes(points)
    heights = [p[3] for p in points]
    if _elevation_noise(heights) > ELEVATION_NOISE:
        heights = [None] * n
    # Positions in metres east and north of the first point, and prefix
    # sums for quick means; relative values keep the sums precise
    k = _M_PER_DEGREE
    rad = math.pi / 180
    t0, lon0, lat0 = times[0], lons[0], points[0][1]
    xs = [(lon - lon0) * k * math.cos(p[1] * rad) for lon, p in zip(lons, points)]
    ys = [(p[1] - lat0) * k for p in points]
    st = [0.0, *accumulate(t - t0 for t in times)]
    sx = [0.0, *accumulate(xs)]
    sy = [0.0, *accumulate(ys)]
    sz = [0.0, *accumulate(0.0 if z is None else z for z in heights)]
    cz = [0, *accumulate(z is not None for z in heights)]

    def mean(prefix, first, last):
        return (prefix[last + 1] - prefix[first]) / (last + 1 - first)

    def height_change(f1, l1, f2, l2):
        """Mean height of points f2..l2 less that of f1..l1, or 0 if unknown."""
        c1, c2 = cz[l1 + 1] - cz[f1], cz[l2 + 1] - cz[f2]
        if not (c1 and c2):
            return 0.0
        return (sz[l2 + 1] - sz[f2]) / c2 - (sz[l1 + 1] - sz[f1]) / c1

    def moves(first, last):
        """Whether the points first..last show steady movement as a whole:
        the mean positions of their two halves in time are further apart
        than the still speeds allow over the time between them."""
        middle = bisect.bisect_right(times, (times[first] + times[last]) / 2, first, last)
        middle = min(max(middle, first + 1), last)
        span = mean(st, middle, last) - mean(st, first, middle - 1)
        dx = mean(sx, middle, last) - mean(sx, first, middle - 1)
        dy = mean(sy, middle, last) - mean(sy, first, middle - 1)
        return (dx * dx + dy * dy > (STILL_SPEED * span) ** 2
                or abs(height_change(first, middle - 1, middle, last)) > STILL_CLIMB * span)

    # 1. Still points: the mean positions of the STILL_WINDOW seconds
    # before and after each point; the windows end at breaks in recording.
    # This runs for every point, so it is written out for speed.
    still = [False] * n
    speed2 = STILL_SPEED ** 2
    begin = before = after = 0
    for i in range(n):
        t = times[i]
        if i and t - times[i - 1] > RECORDING_BREAK:
            begin = i
        if before < begin:
            before = begin
        while times[before] < t - STILL_WINDOW:
            before += 1
        if after < i:
            after = i
        while (after + 1 < n and times[after + 1] <= t + STILL_WINDOW
               and times[after + 1] - times[after] <= RECORDING_BREAK):
            after += 1
        n1, n2 = i + 1 - before, after + 1 - i
        span = (st[after + 1] - st[i]) / n2 - (st[i + 1] - st[before]) / n1
        dx = (sx[after + 1] - sx[i]) / n2 - (sx[i + 1] - sx[before]) / n1
        dy = (sy[after + 1] - sy[i]) / n2 - (sy[i + 1] - sy[before]) / n1
        if dx * dx + dy * dy > speed2 * span * span:
            continue
        c1, c2 = cz[i + 1] - cz[before], cz[after + 1] - cz[i]
        if c1 and c2 and abs((sz[after + 1] - sz[i]) / c2
                             - (sz[i + 1] - sz[before]) / c1) > STILL_CLIMB * span:
            continue
        still[i] = True

    # 2. Still parts: runs of still points and of breaks in recording
    # whose ends are close, as with auto-pause
    def still_break(i):
        span = times[i + 1] - times[i]
        if math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) > min(STILL_SPEED * span, STOP_RADIUS):
            return False
        a, b = heights[i], heights[i + 1]
        return a is None or b is None or abs(b - a) <= STILL_CLIMB * span

    parts = []
    i = 0
    while i < n - 1:
        j = i
        while j < n - 1 and (still[j] and still[j + 1]
                             if times[j + 1] - times[j] <= RECORDING_BREAK
                             else still_break(j)):
            j += 1
        if j > i:
            parts.append((i, j))
        i = max(j, i + 1)

    # 3. Parts close in time and place are one stop that jitter split
    def one_stop(f1, l1, f2, l2):
        return (times[f2] - times[l1] <= STOP_MERGE
                and math.hypot(mean(sx, f2, l2) - mean(sx, f1, l1),
                               mean(sy, f2, l2) - mean(sy, f1, l1)) <= STOP_RADIUS
                and abs(height_change(f1, l1, f2, l2)) <= STOP_HEIGHT)

    merged = []
    for first, last in parts:
        if merged and one_stop(*merged[-1], first, last):
            merged[-1] = (merged[-1][0], last)
        else:
            merged.append((first, last))
    # 4. A stop lasts long enough and, as a whole, shows no steady movement
    cores = [(f, l) for f, l in merged
             if times[l] - times[f] >= STOP_MIN_STILL and not moves(f, l)]

    # 5. Position and edges. The edges move out to where the track comes
    # within STOP_RADIUS and STOP_HEIGHT of the stop, by up to STILL_WINDOW:
    # the still points start and end up to that much inside the stop.
    stops = []
    for index, (first, last) in enumerate(cores):
        lat = _median(p[1] for p in points[first:last + 1])
        lon = _median(lons[first:last + 1])
        elevations = [p[3] for p in points[first:last + 1] if p[3] is not None]
        elevation = _median(elevations) if elevations else None
        scale = k * math.cos(lat * rad)

        def near(j, edge):
            dx = (lons[j] - lon) * scale
            dy = (points[j][1] - lat) * k
            if dx * dx + dy * dy > STOP_RADIUS ** 2:
                return False
            a, b = heights[edge], heights[j]
            return a is None or b is None or abs(b - a) <= STOP_HEIGHT

        low = stops[-1].last + 1 if stops else 0
        start = first
        while (start > low and times[start] - times[start - 1] <= RECORDING_BREAK
               and times[first] - times[start - 1] <= STILL_WINDOW and near(start - 1, first)):
            start -= 1
        high = cores[index + 1][0] - 1 if index + 1 < len(cores) else n - 1
        end = last
        while (end < high and times[end + 1] - times[end] <= RECORDING_BREAK
               and times[end + 1] - times[last] <= STILL_WINDOW and near(end + 1, last)):
            end += 1
        lon = (lon + 180.0) % 360.0 - 180.0
        stops.append(Stop(times[start], times[end], lat, lon, elevation, start, end))
    return stops


def stop_at(stops, t, lat, lon, elevation):
    """The stop a photo taken at time t belongs to, or None.

    lat, lon and elevation are the photo's position on the track from
    locate. The photo belongs to a stop when it was taken during the stop
    and the track is then within PIN_RADIUS and PIN_HEIGHT of the stop.
    """
    i = bisect.bisect_right(stops, t, key=lambda s: s.start) - 1
    if i < 0 or t > stops[i].end:
        return None
    stop = stops[i]
    lon_change = lon - stop.lon
    if lon_change > 180:
        lon_change -= 360
    elif lon_change < -180:
        lon_change += 360
    dx = lon_change * _M_PER_DEGREE * math.cos(math.radians(stop.lat))
    dy = (lat - stop.lat) * _M_PER_DEGREE
    if dx * dx + dy * dy > PIN_RADIUS ** 2:
        return None
    if elevation is not None and stop.elevation is not None:
        if abs(elevation - stop.elevation) > PIN_HEIGHT:
            return None
    return stop


def place(points, times, stops, t, max_gap):
    """Like locate, with the position of the stop the photo was taken at.

    Returns (lat, lon, elevation, gap_s, stop) where stop is None when
    the photo is not pinned to a stop, or (None, reason).
    """
    result = locate(points, times, t, max_gap)
    if result[0] is None:
        return result
    lat, lon, ele, gap = result
    stop = stop_at(stops, t, lat, lon, ele)
    if stop is None:
        return lat, lon, ele, gap, None
    return stop.lat, stop.lon, ele if stop.elevation is None else stop.elevation, gap, stop


def _elevation_noise(heights):
    """Median distance of an elevation from the mean of its neighbours."""
    known = [z for z in heights if z is not None]
    if len(known) < 3:
        return 0.0
    return _median(abs(b - (a + c) / 2) for a, b, c in zip(known, known[1:], known[2:]))


def _unwrapped_longitudes(points):
    """Longitudes that change continuously along the track, also across 180°."""
    lons = []
    previous = unwrapped = points[0][2]
    for p in points:
        change = p[2] - previous
        if change > 180:
            change -= 360
        elif change < -180:
            change += 360
        unwrapped += change
        previous = p[2]
        lons.append(unwrapped)
    return lons


def _between(a, b, u):
    """The value at fraction u of the way from a to b, kept between them despite rounding."""
    return min(max(a + (b - a) * u, min(a, b)), max(a, b))
