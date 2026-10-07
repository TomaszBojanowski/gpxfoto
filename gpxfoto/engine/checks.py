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

from gpxfoto.engine.track import place

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

# shift: seconds to add to the photo times; pinned of matched photos fall
# during stops after the shift, at stops different stops; pinned_now
# fall during stops without it
ShiftHint = namedtuple("ShiftHint", "shift pinned matched stops pinned_now")


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
