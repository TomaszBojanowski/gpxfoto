"""Matching photos to tracks: the rule every interface uses."""
from collections import namedtuple
from datetime import timedelta, timezone
from gettext import gettext as _

from gpxfoto.engine.track import match

# The outcome for one photo. time is the corrected capture time in the
# photo's own time zone and time_utc the same in UTC; both are None when
# they were not worked out (the photo already has a location, has no
# capture time, or the correction moves it out of range). reason is None
# for a matched photo and otherwise says why it was skipped.
PhotoResult = namedtuple("PhotoResult", "photo time time_utc lat lon ele gap reason",
                         defaults=(None,) * 7)

Summary = namedtuple("Summary", "matched skipped")


def match_photo(photo, tracks, correction, max_gap, overwrite=False):
    """Return the PhotoResult of photo on tracks.

    correction is added to the capture time, in seconds. A photo that
    already has a location is skipped unless overwrite is set.
    """
    if photo.has_location and not overwrite:
        # Translators: reason why a photo was skipped
        return PhotoResult(photo, reason=_("already has a location"))
    if photo.taken is None:
        return PhotoResult(photo, reason=photo.reason)
    try:
        time = photo.taken + timedelta(seconds=correction)
        time_utc = time.astimezone(timezone.utc)
    except OverflowError:
        # Translators: reason why a photo was skipped
        return PhotoResult(photo, reason=_("the corrected capture time is out of range"))
    found = match(tracks, time.timestamp(), max_gap)
    if found.reason is not None:
        return PhotoResult(photo, time, time_utc, reason=found.reason)
    return PhotoResult(photo, time, time_utc, found.lat, found.lon, found.ele, found.gap)


def match_photos(photos, tracks, correction, max_gap, **options):
    """Return the PhotoResult of each photo, in the same order."""
    return [match_photo(photo, tracks, correction, max_gap, **options) for photo in photos]


def summarize(results):
    """Return how many photos were matched and how many skipped."""
    matched = sum(1 for result in results if result.reason is None)
    return Summary(matched, len(results) - matched)
