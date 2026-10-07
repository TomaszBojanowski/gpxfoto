"""Matching photos to tracks: the rule every interface uses."""
import os
from collections import namedtuple
from datetime import timedelta, timezone
from gettext import gettext as _

from gpxfoto.engine.photos import check_against_camera_utc
from gpxfoto.engine.track import match

# The outcome for one photo. time is the corrected capture time in the
# photo's own time zone and time_utc the same in UTC; both are None when
# they were not worked out (the photo already has a location, has no
# capture time, or the correction moves it out of range). reason is None
# for a matched photo and otherwise says why it was skipped. time_check
# is a TimeCheck when the capture time does not match the camera's UTC time.
# stop is the Stop the photo was taken at, whose position it then has.
# files are the track files its position comes from, or that leave a gap
# at its time.
PhotoResult = namedtuple("PhotoResult",
                         "photo time time_utc lat lon ele gap reason time_check stop files",
                         defaults=(None,) * 9 + ((),))

# How many photos were matched and skipped, and how many of the matched
# ones were taken during stops
Summary = namedtuple("Summary", "matched skipped at_stops")


def match_photo(photo, tracks, correction, max_gap, overwrite=False, label=os.path.basename):
    """Return the PhotoResult of photo on tracks.

    correction is added to the capture time, in seconds. A photo that
    already has a location is skipped unless overwrite is set. label
    gives the name of a track file in reasons.
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
    found = match(tracks, time.timestamp(), max_gap, label)
    if found.reason is not None:
        return PhotoResult(photo, time, time_utc, reason=found.reason, time_check=time_check,
                           files=found.files)
    return PhotoResult(photo, time, time_utc, found.lat, found.lon, found.ele, found.gap,
                       time_check=time_check, stop=found.stop, files=found.files)


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
