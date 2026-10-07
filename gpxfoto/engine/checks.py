"""Signs of a suspicious match, which point at a wrong camera clock.

Pure functions of a loaded track and the photos' times and positions:
nothing here reads files or changes anything, so the checks can run
again, from any thread, after every change of the clock correction.
The thresholds were chosen on a real 5.5-hour mountain hike recorded
once a second, with photo times modelled on its stops, its short pauses
and its walking; see tests/test_checks.py and tests/test_private.py.
"""
import bisect
import math
from collections import namedtuple

from gpxfoto.engine.track import RECORDING_BREAK, STILL_SPEED, place

# A photo as the checks see it. time: the corrected capture time used for
# matching, as a Unix time. clock: the camera's reading with the same
# correction, in seconds, as if its local date and time were UTC; it
# orders the photos and tells how far apart they were taken even when
# their time zones differ, and clock - time is the photo's UTC offset.
# lat, lon: where the photo was placed, or None when it was not matched.
Shot = namedtuple("Shot", "time clock lat lon")

# Photos taken less than this apart in camera time are one moment: a
# burst, or a few frames of one view, is one piece of evidence, not many
MOMENT_GAP = 60.0                # s

# Whole-hour shift. A shift is tried when it moves the photos onto the
# stops of the track. Each condition stops a different false alarm that
# showed up on the real track with a correct clock:
SHIFTS = (1800, -1800) + tuple(sign * hours * 3600 for hours in range(1, 13)
                               for sign in (1, -1))
SHIFT_MIN_PHOTOS = 10            # matched photos after the shift
SHIFT_MIN_STOPS = 4              # different stops reached: one long stop or one burst is not enough
SHIFT_MORE_STOPS = 3             # stops reached beyond those reached now
# The shift must stand out from shifts 10, 20 and 30 minutes away, which
# land the photos on the same long stops and the same busy stretches
SHIFT_NEIGHBOURS = (-1800, -1200, -600, 600, 1200, 1800)
SHIFT_SHARPNESS = 3.0            # stops reached beyond the mean of the neighbours
# Shifts are counted on a grid of SHIFT_STEP; the shifts on the grid that
# are not whole or half hours, within SHIFT_NULL_WINDOW of a candidate,
# show how many stops photos reach by chance on this track with these
# photos. The candidate must stand SHIFT_Z spreads above their mean.
SHIFT_STEP = 600
SHIFT_NULL_WINDOW = 3 * 3600
SHIFT_Z = 4.0
SHIFT_MIN_SPREAD = 1.0           # stops; a floor for the spread of few photos
# Random times fall on the real track's stops 15% of the time; photos
# that mostly come from walking do not reach this share at any shift
SHIFT_MIN_SHARE = 0.35

# Photos in motion. The speed at a track point is measured over
# PACE_WINDOW seconds around it; full pace is at least FULL_PACE of the
# track's typical speed, the median speed while moving.
PACE_WINDOW = 10.0               # s
FULL_PACE = 0.6
MOTION_MIN_MOMENTS = 15
# With the photos moved by up to five minutes either way, they must not
# fall at full pace much more often than where they are now: photos taken
# where the walker slowed down lose that at once when moved
MOTION_NEARBY = tuple(minutes * 60 for minutes in (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5))
MOTION_MARGIN = 0.0
# Only on tracks at full pace at least this share of the time can "most
# photos at full pace" say anything about the clock
MOTION_MIN_BASE = 0.6

# The track's speeds for the motion check, from pace(): times of its
# points, slow as prefix counts of slow points (slow[j] - slow[i] slow
# points among i..j-1), typical in m/s or None when the track barely moves
Pace = namedtuple("Pace", "times slow typical fast_share top")

# shift: seconds to add to the photo times; pinned of matched photos fall
# during stops after the shift, at stops different stops; pinned_now
# fall during stops without it
ShiftHint = namedtuple("ShiftHint", "shift pinned matched stops pinned_now")
# fast of matched photos, and fast_moments of moments, at full pace
Motion = namedtuple("Motion", "fast matched fast_moments moments")


def _distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = f2 - f1, math.radians(lon2 - lon1)
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _moments(shots):
    """Shots, sorted by clock, split where MOMENT_GAP or more passes."""
    moments = []
    for shot in shots:
        if moments and shot.clock - moments[-1][-1].clock < MOMENT_GAP:
            moments[-1].append(shot)
        else:
            moments.append([shot])
    return moments


# --- whole-hour shift -------------------------------------------------

def _inside(spans, ordered, shift):
    """(stops reached, photos within a stop's time span) with times moved by shift."""
    reached = inside = 0
    for start, end in spans:
        k = bisect.bisect_right(ordered, end - shift) - bisect.bisect_left(ordered, start - shift)
        reached += k > 0
        inside += k
    return reached, inside


def _exact(points, times, stops, ordered, shift, max_gap):
    """(matched, pinned, stops reached) exactly as place() decides."""
    matched = pinned = 0
    reached = set()
    for t in ordered:
        found = place(points, times, stops, t + shift, max_gap)
        if found[0] is not None:
            matched += 1
            if found[4] is not None:
                pinned += 1
                reached.add(found[4].first)
    return matched, pinned, len(reached)


def whole_hour_shift(points, times, stops, shots, max_gap):
    """The shift from SHIFTS that clearly puts more photos at stops, or None."""
    ordered = sorted(shot.time for shot in shots)
    if not stops or len(ordered) < SHIFT_MIN_PHOTOS:
        return None
    spans = [(s.start, s.end) for s in stops]
    reach = max(map(abs, SHIFTS)) + SHIFT_NULL_WINDOW
    grid = {d: _inside(spans, ordered, d)
            for d in range(-reach, reach + 1, SHIFT_STEP)}
    candidates = set(SHIFTS) | {0}
    stops_now, inside_now = grid[0]
    best = None
    for shift in SHIFTS:
        reached, inside = grid[shift]
        if not clearly_more_stops(reached, stops_now) or inside < inside_now + SHIFT_MORE_STOPS:
            continue
        nearby = sum(grid[shift + d][0] for d in SHIFT_NEIGHBOURS) / len(SHIFT_NEIGHBOURS)
        null = [grid[d][0] for d in grid if d not in candidates
                and 2 * SHIFT_STEP < abs(d - shift) <= SHIFT_NULL_WINDOW and abs(d) > 2 * SHIFT_STEP]
        mean = sum(null) / len(null)
        spread = max(SHIFT_MIN_SPREAD, math.sqrt(sum((x - mean) ** 2 for x in null) / len(null)))
        z = (reached - mean) / spread
        if reached - nearby < SHIFT_SHARPNESS or z < SHIFT_Z:
            continue
        key = (z, reached, inside, -abs(shift), shift)
        if best is None or key > best[0]:
            best = (key, shift)
    if best is None:
        return None
    shift = best[1]
    matched, pinned, reached = _exact(points, times, stops, ordered, shift, max_gap)
    pinned_now = _exact(points, times, stops, ordered, 0, max_gap)[1]
    if not shift_stands_out(reached, 0.0, pinned, matched) or pinned < pinned_now + SHIFT_MORE_STOPS:
        return None
    return ShiftHint(shift, pinned, matched, reached, pinned_now)


def clearly_more_stops(reached, reached_now):
    """Whether a shift reaches enough stops, and clearly more than now."""
    return reached >= max(SHIFT_MIN_STOPS, reached_now + SHIFT_MORE_STOPS)


def shift_stands_out(reached, nearby, pinned, matched):
    """Whether a shift that reaches clearly more stops is worth proposing.

    nearby: the mean number of stops reached with the shift changed by
    each of SHIFT_NEIGHBOURS; pinned of matched photos fall during stops.
    """
    return (reached - nearby >= SHIFT_SHARPNESS and matched >= SHIFT_MIN_PHOTOS
            and pinned >= SHIFT_MIN_SHARE * matched)


# --- photos in motion -------------------------------------------------

def pace(points, stops):
    """The track's speeds, for photos_in_motion(); O(n), once per track.

    Points during stops are slow, whatever GPS jitter makes of their
    speed; the typical speed is the median speed of the other points.
    """
    n = len(points)
    times = [p[0] for p in points]
    speeds = [None] * n
    half = PACE_WINDOW / 2
    begin = 0           # first point of the recorded part that holds i
    end = -1            # last point of that part
    a = b = 0
    for i in range(n):
        if i > end:
            begin = end = i
            while end + 1 < n and times[end + 1] - times[end] <= RECORDING_BREAK:
                end += 1
            a = b = i
        a = max(a, begin)
        while times[a] < times[i] - half:
            a += 1
        b = max(b, i)
        while b + 1 <= end and times[b + 1] <= times[i] + half:
            b += 1
        lo, hi = a, b
        if lo == hi:        # sparse recording: the neighbours in the same part
            lo, hi = max(begin, i - 1), min(end, i + 1)
        if hi > lo and times[hi] > times[lo]:
            p, q = points[lo], points[hi]
            speeds[i] = _distance_m(p[1], p[2], q[1], q[2]) / (times[hi] - times[lo])
    standing = [False] * n
    for stop in stops:
        standing[stop.first:stop.last + 1] = [True] * (stop.last + 1 - stop.first)
    moving = sorted(v for v, still in zip(speeds, standing) if v is not None and not still)
    typical = moving[len(moving) // 2] if moving else None
    if typical is not None and typical < STILL_SPEED:
        typical = None      # the track barely moves outside its stops
    slow = [0]
    for v, still in zip(speeds, standing):
        slow.append(slow[-1] + (still or typical is None or v is None or v < FULL_PACE * typical))
    fast = recorded = 0.0
    for j in range(n - 1):
        step = times[j + 1] - times[j]
        if step <= RECORDING_BREAK:
            recorded += step
            fast += step * (slow[j + 2] == slow[j])
    fast_share = fast / recorded if recorded else 0.0
    top = max((v for v in speeds if v is not None), default=0.0)
    return Pace(times, slow, typical, fast_share, top)


def at_full_pace(pace_, t):
    """Whether the track shows movement at full pace at time t.

    Both track points around t must be at full pace and recorded without a
    break between them; outside the track this is False.
    """
    times = pace_.times
    j = bisect.bisect_right(times, t) - 1
    if j < 0 or j + 1 >= len(times):
        return False
    if times[j + 1] - times[j] > RECORDING_BREAK:
        return False
    return pace_.slow[j + 2] == pace_.slow[j]


def _share_fast(pace_, moments, shift):
    """Share of moments whose photos are mostly at full pace, moved by shift."""
    first, last = pace_.times[0], pace_.times[-1]
    counted = fast = 0
    for moment in moments:
        flags = [at_full_pace(pace_, s.time + shift) for s in moment
                 if first <= s.time + shift <= last]
        if flags:
            counted += 1
            fast += 2 * sum(flags) > len(flags)
    return fast / counted if counted else 0.0


def photos_in_motion(pace_, shots):
    """Motion when most matched photos fall at full pace, or None."""
    if pace_.typical is None or pace_.fast_share < MOTION_MIN_BASE:
        return None
    matched = sorted((s for s in shots if s.lat is not None), key=lambda s: s.clock)
    moments = _moments(matched)
    fast = sum(at_full_pace(pace_, s.time) for s in matched)
    fast_moments = sum(2 * sum(at_full_pace(pace_, s.time) for s in m) > len(m)
                       for m in moments)
    if not mostly_fast(fast, len(matched), fast_moments, len(moments)):
        return None
    nearby = sum(_share_fast(pace_, moments, d) for d in MOTION_NEARBY) / len(MOTION_NEARBY)
    if fast_moments / len(moments) < nearby - MOTION_MARGIN:
        return None
    return Motion(fast, len(matched), fast_moments, len(moments))


def mostly_fast(fast, matched, fast_moments, moments):
    """Whether more than half of the photos and of the moments are at full pace."""
    return moments >= MOTION_MIN_MOMENTS and 2 * fast > matched and 2 * fast_moments > moments
