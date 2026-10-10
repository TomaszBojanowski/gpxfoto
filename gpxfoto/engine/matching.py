"""Matching photos to tracks: the rule every interface uses."""
import os
from collections import namedtuple
from datetime import timedelta, timezone
from gettext import gettext as _

from gpxfoto import i18n
from gpxfoto.engine import checks
from gpxfoto.engine.photos import check_against_camera_utc
from gpxfoto.engine.track import load_track, match, nearest_track

# The outcome for one photo. time is the corrected capture time in the
# photo's own time zone and time_utc the same in UTC; both are None when
# they were not worked out (the photo already has a location, has no
# capture time, or the correction moves it out of range). reason is None
# for a matched photo and otherwise says why it was skipped. time_check
# is a TimeCheck when the capture time does not match the camera's UTC time.
# stop is the Stop the photo was taken at, whose position it then has.
# files are the track files its position comes from, or that leave a gap
# at its time. covered is false when no track covers its time.
PhotoResult = namedtuple("PhotoResult",
                         "photo time time_utc lat lon ele gap reason time_check stop files "
                         "covered", defaults=(None,) * 9 + ((), True))

# How many photos were matched and skipped, and how many of the matched
# ones were taken during stops
Summary = namedtuple("Summary", "matched skipped at_stops")


def match_photo(photo, tracks, correction, max_gap, overwrite=False, label=os.path.basename,
                unreadable=()):
    """Return the PhotoResult of photo on tracks.

    correction is added to the capture time, in seconds. A photo that
    already has a location is skipped unless overwrite is set. label and
    unreadable are as for track.match().
    """
    if photo.has_location and not overwrite:
        # Translators: reason why a photo was skipped
        return PhotoResult(photo, reason=_("already has a location"))
    if photo.taken is None:
        return PhotoResult(photo, reason=photo.reason)
    time_check = None
    if photo.camera_utc is not None:
        time_check = check_against_camera_utc(photo.taken, photo.tz_source, photo.camera_utc,
                                              correction)
    try:
        time = photo.taken + timedelta(seconds=correction)
        time_utc = time.astimezone(timezone.utc)
    except OverflowError:
        # Translators: reason why a photo was skipped
        return PhotoResult(photo, reason=_("the corrected capture time is out of range"),
                           time_check=time_check)
    found = match(tracks, time.timestamp(), max_gap, label, unreadable)
    if found.reason is not None:
        return PhotoResult(photo, time, time_utc, reason=found.reason, time_check=time_check,
                           files=found.files, covered=found.covered)
    return PhotoResult(photo, time, time_utc, found.lat, found.lon, found.ele, found.gap,
                       time_check=time_check, stop=found.stop, files=found.files)


def placed_by_hand(photo, correction, lat, lon):
    """Return the PhotoResult of photo placed by hand at lat, lon.

    It has no elevation and no track files. Its time is the corrected
    capture time, when the photo has one.
    """
    time = time_utc = None
    if photo.taken is not None:
        try:
            time = photo.taken + timedelta(seconds=correction)
            time_utc = time.astimezone(timezone.utc)
        except OverflowError:
            time = None
    return PhotoResult(photo, time, time_utc, lat, lon)


def corrected_times(photos, correction, overwrite=False):
    """The sorted Unix times of the photos that would be matched, after correction."""
    times = []
    for photo in photos:
        if photo.taken is None or (photo.has_location and not overwrite):
            continue
        try:
            times.append((photo.taken + timedelta(seconds=correction)).timestamp())
        except OverflowError:
            pass
    return sorted(times)


def match_photos(photos, tracks, correction, max_gap, **options):
    """Return the PhotoResult of each photo, in the same order."""
    return [match_photo(photo, tracks, correction, max_gap, **options) for photo in photos]


def summarize(results):
    """Return the Summary of results."""
    matched = sum(1 for result in results if result.reason is None)
    at_stops = sum(1 for result in results if result.stop is not None)
    return Summary(matched, len(results) - matched, at_stops)


def with_nearest_tracks(results, tracks, spans, stops, skipped=()):
    """results, where a photo no track covers names the nearest track file;
    the tracks loaded for that; and the (path, error) of the files among
    them that cannot be read. skipped are files already known to be so."""
    loaded = {}
    failed = []

    def load(path):
        if path not in loaded:
            try:
                loaded[path] = load_track([path], named=False, stops=stops)
            except ValueError as e:
                failed.append((path, e))
                loaded[path] = None     # not read again for the next photo
        return loaded[path]

    singles = [track for track in tracks if len(track.files) == 1]
    unread = {path: span for path, span in spans.items() if path not in skipped
              and not any(track.files[0] == path for track in singles)}
    changed = []
    for result in results:
        if not result.covered:
            nearest = nearest_track(result.time.timestamp(), singles, unread, load)
            if nearest is not None:
                seconds, path, after = nearest
                if after:
                    # Translators: reason why a photo was skipped; {duration} is
                    # a time span such as “10 min”
                    reason = _("{duration} after the end of the nearest track")
                else:
                    # Translators: reason why a photo was skipped; {duration} is
                    # a time span such as “10 min”
                    reason = _("{duration} before the start of the nearest track")
                result = result._replace(reason=reason.format(duration=i18n.duration(seconds)),
                                         files=(path,))
        changed.append(result)
    return changed, [track for track in loaded.values() if track is not None], failed


def shots_of(results):
    """The photos with a capture time as the checks of a suspicious match
    see them, and the index of each in results."""
    shots, indices = [], []
    for index, result in enumerate(results):
        if result.time is not None:
            time = result.time.timestamp()
            shots.append(checks.Shot(time, time + result.time.utcoffset().total_seconds(),
                                     result.lat, result.lon))
            indices.append(index)
    return shots, indices


def suspicion(results, tracks, shots, max_gap, stops=True, nearby=()):
    """The signs of a suspicious match.

    The whole-hour shift and the photos in motion are looked for only on
    the track that placed every matched photo, as they need its stops and
    speeds. When no photo was matched, that is the only track loaded, or
    the only one of nearby, the tracks loaded to name the nearest one.
    Photos skipped for another track would not move onto this track's
    stops with any shift. Without stops, only jumps are looked for.
    """
    owner = {path: track for track in tracks for path in track.files}
    used = []
    for result in results:
        if result.reason is None and owner[result.files[0]] not in used:
            used.append(owner[result.files[0]])
    others = {owner.get(path) for result in results if result.reason is not None
              and result.covered for path in result.files}
    one = used or tracks + [track for track in nearby if track not in tracks]
    if len(one) == 1 and others <= set(one) and stops:
        track = one[0]
        top = None if track.sources is None else checks.top_speed(track.points, track.sources)
        return checks.suspicious_match(track.points, track.times, track.stops, shots,
                                       max_gap, top=top)
    top = max((checks.top_speed(track.points, track.sources) for track in used), default=0.0)
    return checks.Suspicion(None, None, checks.jumps(shots, top))
