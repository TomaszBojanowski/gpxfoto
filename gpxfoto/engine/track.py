"""Loading GPX tracks and finding the position at a given moment."""
import bisect
import codecs
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import namedtuple
from datetime import datetime, timezone
from gettext import gettext as _
from itertools import accumulate

from gpxfoto.i18n import distance, duration

# Extensions of the track files looked for in directories, in any case
GPX_EXTENSIONS = {".gpx"}
# Larger files are not scanned quickly but read in full when needed
QUICK_SCAN_LIMIT = 256 * 1024 * 1024

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

# Direction of travel. A photo gets one only where the track clearly and
# simply passes through its place: it gets TRAVEL_DISTANCE away within
# TRAVEL_WINDOW both before and after the photo, and the way between those
# two points is at most TRAVEL_MAX_WINDING times the straight line, which
# leaves out stops, sharp turns, hairpins and switchbacks. Steps of the
# way count once they reach TRAVEL_STEP, so that GPS jitter adds nothing.
# Chosen on a real hike and on noisy tracks modelled on it.
TRAVEL_DISTANCE = 20.0   # m
TRAVEL_WINDOW = 60.0     # s
TRAVEL_STEP = 10.0       # m
TRAVEL_MAX_WINDING = 1.2

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
    __slots__ = ("files", "named", "points", "times", "stops", "sources", "_interval")

    def __init__(self, files, named, points, stops=(), sources=None):
        self.files = tuple(files)
        self.named = named
        self.points = points
        self.times = [p[0] for p in points]
        self.stops = list(stops)
        self.sources = sources
        self._interval = None

    @property
    def first(self):
        return self.times[0]

    @property
    def last(self):
        return self.times[-1]

    @property
    def interval(self):
        """The usual time between two points, in whole seconds, at least 1."""
        if self._interval is None:
            steps = [b - a for a, b in zip(self.times, self.times[1:]) if b > a]
            self._interval = max(1, round(_median(steps))) if steps else 1
        return self._interval

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


def find_tracks(paths, recursive):
    """Split the paths given for tracks into named files and found files.

    Returns (named, found): the paths that are not directories, as given,
    and the GPX files in the directories, with recursive also in their
    subdirectories, sorted by name. Hidden files and directories and
    symbolic links to directories inside are skipped. A file reached
    twice is used once; a found file that is also named counts as named.
    """
    named, found, seen = [], [], set()
    for path in paths:
        if not os.path.isdir(path):
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                named.append(path)
    for path in paths:
        if not os.path.isdir(path):
            continue
        for directory, subdirs, files in os.walk(path):
            subdirs[:] = sorted(d for d in subdirs if not d.startswith("."))
            for name in sorted(files):
                track = os.path.join(directory, name)
                if (name.startswith(".") or os.path.splitext(name)[1].lower() not in GPX_EXTENSIONS
                        or not os.path.isfile(track)):
                    continue
                real = os.path.realpath(track)
                if real not in seen:
                    seen.add(real)
                    found.append(track)
            if not recursive:
                break
    return named, found


# A <time> element, with any namespace prefix and attributes: whether it is
# empty, its text, and its end tag, which is missing when the text holds
# markup or a character reference
_NAME = rb"[^\s<>/!?=\"':]+"
_TIME = re.compile(rb"<(?:" + _NAME + rb":)?time(?=[\s/>])[^<>]*?(/?)>"
                   rb"(?:([^<>&]*)(</(?:" + _NAME + rb":)?time\s*>))?")
_DECLARATION = re.compile(rb"<\?xml\s[^>]*?\bencoding\s*=\s*[\"']([A-Za-z][\w.-]*)[\"']")
# Times all written the same way in UTC, as Garmin and most devices do
_CANONICAL = re.compile(rb"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\n)*")


def quick_span(path):
    """The earliest and latest time anywhere in the GPX file at path, quickly.

    Returns (first, last) as Unix times, () when the file has no time,
    or None when a quick look cannot tell, for example for an encoding
    other than UTF-8 or Latin, a DOCTYPE, or a time with markup in it.
    The span holds every time load_gpx() would read, and possibly more.
    """
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            if size > QUICK_SCAN_LIMIT:
                return None
            data = f.read(size + 1)
    except OSError:
        return None
    if len(data) > size:           # the file grew while it was read
        return None
    if not data:
        return ()
    return _scan(data)


def _scan(data):
    if not _ascii_compatible(data[:1024]) or b"<!DOCTYPE" in data:
        return None
    texts = []
    for empty, text, end in _TIME.findall(data):
        if end:
            texts.append(text)
        elif not empty:
            return None            # markup or a reference in the time
    joined = b"\n".join(texts) + b"\n"
    if not joined.isascii():
        return None
    try:
        if len({len(text) for text in texts}) == 1 and _CANONICAL.fullmatch(joined):
            # Written the same way, the earliest time sorts first as text
            return (_parse_time(min(texts).decode()).timestamp(),
                    _parse_time(max(texts).decode()).timestamp())
    except (ValueError, OverflowError):
        pass
    times = []
    for text in texts:
        try:
            times.append(_parse_time(text.decode()).timestamp())
        except (ValueError, OverflowError):
            pass
    return (min(times), max(times)) if times else ()


def _ascii_compatible(head):
    """Whether a file starting with head is in an encoding that keeps ASCII as it is."""
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if head.startswith((b"\xfe\xff", b"\xff\xfe")) or b"\x00" in head[:4]:
        return False
    match = _DECLARATION.match(head)
    if not match:
        return True
    try:
        name = codecs.lookup(match[1].decode("ascii")).name
    except LookupError:
        return False
    return name in ("utf-8", "ascii") or name.startswith(("iso8859-", "cp125"))


def tracks_needed(spans, times, max_gap):
    """The paths whose span, widened by max_gap, holds one of times, or is unknown.

    spans maps paths to quick_span() results; times must be sorted.
    """
    needed = []
    for path, span in spans.items():
        if span is None:
            needed.append(path)
        elif span:
            i = bisect.bisect_left(times, span[0] - max_gap)
            if i < len(times) and times[i] <= span[1] + max_gap:
                needed.append(path)
    return needed


# A photo no track covers gets the nearest track within this time, in s
HINT_LIMIT = 24 * 3600


def nearest_track(t, tracks, spans, load):
    """The track file nearest in time to Unix time t, which no track covers.

    tracks are the loaded Tracks of one file each, spans the quick spans
    of the other files, and load(path) reads one of them (a Track, None,
    or ValueError); only files that may be nearer than the best so far
    are read. Returns (seconds, path, after), where after tells whether t
    is after the end of the file's track, or None when no track lies
    within HINT_LIMIT.
    """
    def distance(first, last):
        return max(first - t, t - last, 0.0)

    candidates = [(distance(track.first, track.last), track.files[0], track) for track in tracks]
    candidates += [(distance(*span), path, None) for path, span in spans.items() if span]
    candidates.sort(key=lambda c: c[0])
    best = None
    for bound, path, track in candidates:
        if bound > HINT_LIMIT or (best is not None and bound > best[0]):
            break
        if track is None:
            try:
                track = load(path)
            except ValueError:
                continue
            if track is None:
                continue
        after = t > track.last
        found = (distance(track.first, track.last), path, after)
        # On a tie, after the end of a track is the likelier mistake
        if best is None or (found[0], not found[2]) < (best[0], not best[2]):
            best = found
    return best if best is not None and best[0] <= HINT_LIMIT else None


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
# none; stop is the stop whose position it is, if any, and files the
# track files the position comes from, or that leave a gap at that moment.
# covered is false when no track covers the moment, widened by --max-gap,
# and conflict is the other track when two tracks disagree.
Match = namedtuple("Match", "lat lon ele gap track reason stop files covered conflict",
                   defaults=(None,) * 7 + ((), True, None))

# Two found tracks that put a photo further apart than this disagree, and
# the photo is skipped: recordings of one walk by two devices stay within
# about 150 m of each other, while a clock error of 10 min puts a walk
# 400 m off
DISAGREEMENT = 200.0     # m

# How well a track places a moment, best first: between two of its points
# that are both within --max-gap, from one point within --max-gap, or
# across a break in recording without movement
INSIDE, NEAR, ACROSS_BREAK = 0, 1, 2


def placement_rank(track, t, max_gap):
    """How well track places Unix time t (INSIDE, NEAR or ACROSS_BREAK)."""
    times = track.times
    i = bisect.bisect_left(times, t)
    if i < len(times) and times[i] == t:
        return INSIDE
    if 0 < i < len(times) and t - times[i - 1] <= max_gap and times[i] - t <= max_gap:
        return INSIDE
    nearest = min(abs(times[j] - t) for j in (i - 1, i) if 0 <= j < len(times))
    return NEAR if nearest <= max_gap else ACROSS_BREAK


def match(tracks, t, max_gap, label=os.path.basename, unreadable=()):
    """Return the Match of Unix time t on tracks.

    Each track that covers t, widened by max_gap, places it on its own;
    positions are never interpolated between tracks. The best placement
    wins (see placement_rank); among equal ones, a track the user named,
    then the one recorded more often, then the one that started earlier.
    When the winner was found in a directory and another track places t
    as well but more than DISAGREEMENT away, nothing is placed. label
    gives the name of a track file in reasons. unreadable holds (path,
    span) of the track files that could not be read: where their span,
    widened by max_gap, holds t, nothing is placed either.
    """
    for path, (first, last) in unreadable:
        if first - max_gap <= t <= last + max_gap:
            # Translators: reason why a photo was skipped; {name} is a GPX file
            return Match(reason=_("the track {name} cannot be read").format(name=label(path)))
    if not tracks:
        # Translators: reason why a photo was skipped
        return Match(reason=_("no track covers this time"), covered=False)
    placed, rejected = [], []
    for track in tracks:
        if not track.first - max_gap <= t <= track.last + max_gap:
            continue
        result = place(track.points, track.times, track.stops, t, max_gap)
        if result[0] is None:
            rejected.append(((not track.named, track.first, track.files), track, result[1]))
        else:
            rank = placement_rank(track, t, max_gap)
            placed.append(((rank, not track.named, track.interval, track.first, track.files),
                           track, result))
    if placed:
        placed.sort(key=lambda p: p[0])
        key, track, (lat, lon, ele, gap, stop) = placed[0]
        if not track.named:
            for other_key, other, other_result in placed[1:]:
                apart = _distance_m((t, lat, lon), (t, *other_result[:2]))
                if other_key[0] == key[0] and apart > DISAGREEMENT:
                    first = ", ".join(map(label, track.files_at(t)))
                    second = ", ".join(map(label, other.files_at(t)))
                    # Translators: reason why a photo was skipped; {first} and
                    # {second} are track files, {distance} is such as “1.8 km”
                    reason = _("the tracks {first} and {second} put this photo {distance} "
                               "apart").format(first=first, second=second,
                                               distance=distance(apart))
                    return Match(track=track, reason=reason, conflict=other)
        return Match(lat, lon, ele, gap, track, stop=stop, files=track.files_at(t))
    if rejected:
        _key, track, reason = min(rejected, key=lambda r: r[0])
        files = track.files_at(t) if track.first <= t <= track.last else ()
        return Match(track=track, reason=reason, files=files)
    # No track covers t: the reason the nearest one gives
    track = min(tracks, key=lambda track: max(track.first - t, t - track.last))
    return Match(track=track, reason=locate(track.points, track.times, t, max_gap)[1],
                 covered=False)


def load_gpx(paths):
    """Return a sorted list of (unix_time, lat, lon, elevation|None).

    Raises ValueError naming the file when a file cannot be read, is not
    valid XML or uses an encoding that cannot be decoded.
    """
    return _load_points(paths)[0]


def _load_points(paths):
    """Return the points of the files at paths sorted by time, and for each
    point the index of its file in paths."""
    per_file = []
    for path in paths:
        try:
            per_file.append(_read_points(path))
        except (OSError, ET.ParseError, ValueError, LookupError) as e:
            error = e.strerror if isinstance(e, OSError) and e.strerror else e
            # Translators: {error} describes the problem, e.g. “No such file or directory”
            raise ValueError(_("Cannot read the GPX file {path}: {error}").format(
                path=path, error=error)) from e
    if len(per_file) == 1:
        points = sorted(per_file[0], key=lambda p: p[0])
        return points, [0] * len(points)
    found = sorted(((point, index) for index, points in enumerate(per_file) for point in points),
                   key=lambda pair: pair[0][0])
    return [point for point, _index in found], [index for _point, index in found]


def _read_points(path):
    points = []
    for _event, el in ET.iterparse(path):
        # endswith() first: it is much quicker, and most elements are not points
        if not el.tag.endswith("trkpt") or _local_name(el.tag) != "trkpt":
            continue
        time_text = ele_text = None
        for child in el:
            tag = child.tag
            if tag.endswith("time") and child.text and _local_name(tag) == "time":
                time_text = child.text
            elif tag.endswith("ele") and child.text and _local_name(tag) == "ele":
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


def travel_direction(points, times, t, lat, lon):
    """Return (degrees, None) or (None, reason).

    degrees is the direction of travel through the photo's place, a whole
    number from 0 to 359 clockwise from true north. points and times are
    what locate() got, t the same time and (lat, lon) the position it
    gave.
    """
    if not times or not times[0] <= t <= times[-1]:
        return None, _too_close_to_an_end()
    here = (t, lat, lon, None)
    i = bisect.bisect_left(times, t)
    first, reason = _away(points, times, t, here, range(i - 1, -1, -1))
    if first is None:
        return None, reason
    last, reason = _away(points, times, t, here, range(i, len(points)))
    if last is None:
        return None, reason
    if _length(points, first, last) > TRAVEL_MAX_WINDING * _distance_m(points[first], points[last]):
        # Translators: why a photo gets no direction of travel
        return None, _("the track winds too much here")
    return round(_bearing(points[first], points[last])) % 360, None


def _too_close_to_an_end():
    # Translators: why a photo gets no direction of travel
    return _("too close to the start or end of the track")


def _away(points, times, t, here, indices):
    """(index, None) of the first point in indices, going away from t in
    time, that is TRAVEL_DISTANCE from here within TRAVEL_WINDOW, or
    (None, reason)."""
    seen = False
    for j in indices:
        if abs(times[j] - t) > TRAVEL_WINDOW:
            break
        seen = True
        if _distance_m(points[j], here) >= TRAVEL_DISTANCE:
            return j, None
    else:
        return None, _too_close_to_an_end()
    if seen:
        # Translators: why a photo gets no direction of travel; {distance}
        # is a distance such as “20 m” and {duration} a time span such as
        # “60 s”
        reason = _("the track stays within {distance} of this place for {duration} before or "
                   "after the photo")
    else:
        # Translators: why a photo gets no direction of travel; {duration}
        # is a time span such as “60 s”
        reason = _("no track points within {duration} before or after the photo")
    return None, reason.format(distance=distance(TRAVEL_DISTANCE),
                               duration=duration(TRAVEL_WINDOW))


def _length(points, first, last):
    """Metres along the track from first to last, in steps of TRAVEL_STEP or more."""
    total, mark = 0.0, points[first]
    for j in range(first + 1, last + 1):
        step = _distance_m(mark, points[j])
        if step >= TRAVEL_STEP or j == last:
            total += step
            mark = points[j]
    return total


def _bearing(a, b):
    """The initial great-circle bearing from point a to point b, 0 to 360°."""
    f1, f2 = math.radians(a[1]), math.radians(b[1])
    dl = math.radians(b[2] - a[2])
    return math.degrees(math.atan2(math.sin(dl) * math.cos(f2),
                                   math.cos(f1) * math.sin(f2)
                                   - math.sin(f1) * math.cos(f2) * math.cos(dl))) % 360


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
