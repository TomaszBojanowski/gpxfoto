"""Loading GPX tracks and finding the position at a given moment."""
import bisect
import math
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import datetime, timezone
from gettext import gettext as _

from gpxfoto.i18n import duration

# Elevations further from sea level, in metres, are errors in the track:
# balloons rise to about 40 km, and the deepest sea is 11 km deep
MAX_ELEVATION = 100_000.0
# Track times within two days of the ends of the calendar are errors in
# the track; Python cannot show them in every local time zone
EARLIEST = datetime(1, 1, 3, tzinfo=timezone.utc).timestamp()
LATEST = datetime(9999, 12, 30, tzinfo=timezone.utc).timestamp()


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
    """
    __slots__ = ("files", "named", "points", "times")

    def __init__(self, files, named, points):
        self.files = tuple(files)
        self.named = named
        self.points = points
        self.times = [p[0] for p in points]

    @property
    def first(self):
        return self.times[0]

    @property
    def last(self):
        return self.times[-1]


def load_track(paths, named=True):
    """Return the Track of the GPX files at paths, or None if they hold no points.

    Raises ValueError like load_gpx().
    """
    points = load_gpx(paths)
    return Track(paths, named, points) if points else None


# Where a moment lies on the tracks: a position, or the reason there is none
Match = namedtuple("Match", "lat lon ele gap track reason", defaults=(None,) * 6)


def match(tracks, t, max_gap):
    """Return the Match of Unix time t on tracks."""
    track = tracks[0]
    result = locate(track.points, track.times, t, max_gap)
    if result[0] is None:
        return Match(track=track, reason=result[1])
    lat, lon, ele, gap = result
    return Match(lat, lon, ele, gap, track)


def load_gpx(paths):
    """Return a sorted list of (unix_time, lat, lon, elevation|None).

    Raises ValueError naming the file when a file cannot be read, is not
    valid XML or uses an encoding that cannot be decoded.
    """
    points = []
    for path in paths:
        try:
            points += _read_points(path)
        except (OSError, ET.ParseError, ValueError, LookupError) as e:
            error = e.strerror if isinstance(e, OSError) and e.strerror else e
            # Translators: {error} describes the problem, e.g. “No such file or directory”
            raise ValueError(_("Cannot read the GPX file {path}: {error}").format(
                path=path, error=error)) from e
    points.sort(key=lambda p: p[0])
    return points


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


def _between(a, b, u):
    """The value at fraction u of the way from a to b, kept between them despite rounding."""
    return min(max(a + (b - a) * u, min(a, b)), max(a, b))
