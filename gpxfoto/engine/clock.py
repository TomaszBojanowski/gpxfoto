"""Working out the correction of the camera clock from a photo of a clock."""
import os
import re
from collections import namedtuple
from datetime import date, datetime, time, timedelta, timezone
from gettext import gettext as _

from gpxfoto.engine.photos import (
    TZ_CAMERA, capture_time, format_utc_offset, parse_utc_offset, read_metadata)
from gpxfoto.i18n import exact_duration

# From this difference on, the clock may have shown another time zone than
# the camera, which only the user can tell, so the clock's UTC offset must
# be given. 30 min is the smallest difference between time zones in use,
# and some three years of drift for a camera clock.
OFFSET_REQUIRED_FROM = timedelta(minutes=30)
# Up to this difference, the day of a time given without a date is the
# nearest one; beyond it, a wrong day could pass unnoticed.
MAX_WITHOUT_DATE = timedelta(hours=2)
# Time zones differ by whole quarters of an hour
OFFSET_STEP = timedelta(minutes=15)
# Without seconds the middle of the minute is used, so up to this is lost
WITHOUT_SECONDS = timedelta(seconds=30)

# [YYYY-MM-DD(T| )]H:MM[:SS][ ][Z|±HH:MM], 24-hour, ASCII digits only
_READING = re.compile(r"(?:([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt ])?([0-9]{1,2}):([0-9]{2})"
                      r"(?::([0-9]{2}))? ?([Zz]|[+-][0-9]{2}:[0-9]{2})?")


class ClockReading(namedtuple("ClockReading", "time_of_day has_seconds day utc_offset",
                              defaults=(None, None))):
    """The time read on a clock: a time of day (second 0 when only minutes
    were read), and the date and UTC offset when they were given."""
    __slots__ = ()

    def text(self, utc_offset=None, date_form=None):
        """The reading as parse_reading() accepts it.

        utc_offset (a timezone) replaces the reading's own offset;
        date_form, such as “YYYY-MM-DD”, stands in for the date.
        """
        clock = self.time_of_day
        text = f"{clock.hour:02}:{clock.minute:02}"
        if self.has_seconds:
            text += f":{clock.second:02}"
        if date_form is not None:
            text = f"{date_form}T{text}"
        elif self.day is not None:
            text = f"{self.day.year:04}-{self.day.month:02}-{self.day.day:02}T{text}"
        offset = utc_offset or self.utc_offset
        if offset is not None:
            text += format_utc_offset(offset.utcoffset(None))
        return text


class ClockCorrection(namedtuple("ClockCorrection",
                                 "seconds camera_time clock_time reading photo tz_source",
                                 defaults=("", TZ_CAMERA))):
    """The correction worked out from a clock photo.

    seconds is added to the capture time of every photo. camera_time is
    the clock photo's capture time, clock_time the moment the clock showed,
    in the clock's time zone. photo is the clock photo's path and tz_source
    where its time zone came from.
    """
    __slots__ = ()

    @property
    def uncertainty(self):
        """Seconds the correction may be off, because no seconds were read."""
        return 0.0 if self.reading.has_seconds else WITHOUT_SECONDS.total_seconds()


class ClockError(ValueError):
    """A correction that cannot be worked out; the text is for the user."""


def parse_reading(text):
    """Return the ClockReading of text, such as "14:03:27" or "2026-10-06T14:03:27+02:00".

    Raises ValueError with a message for the user.
    """
    match = _READING.fullmatch(text.strip())
    try:
        if not match:
            raise ValueError(text)
        year, month, day, hour, minute, second, offset = match.groups()
        clock = time(int(hour), int(minute), int(second or 0))
        when = date(int(year), int(month), int(day)) if year else None
        if offset in ("Z", "z"):
            offset = "+00:00"
        utc_offset = parse_utc_offset(offset) if offset else None
    except ValueError:
        # Translators: {value} is what the user gave as the time on the clock;
        # HH:MM:SS and HH:MM stand for hours, minutes and seconds
        raise ValueError(_("not a valid time: {value} (use HH:MM:SS or HH:MM, optionally with "
                           "a date and a UTC offset, for example 14:03:27, 2026-10-06T14:03:27 "
                           "or 14:03:27+02:00)").format(value=text)) from None
    return ClockReading(clock, second is not None, when, utc_offset)


def correction_from(camera_time, reading):
    """Return the ClockCorrection of a clock photo taken at camera_time.

    camera_time is the aware capture time of the clock photo, reading the
    ClockReading of the time on the clock. A time without a UTC offset is
    in the camera's time zone, and a time without a date on the nearest
    day. Raises ClockError when that could be wrong.
    """
    camera_offset = camera_time.utcoffset()
    clock_zone = reading.utc_offset or timezone(camera_offset)
    extra = timedelta() if reading.has_seconds else WITHOUT_SECONDS
    if reading.day is not None:
        days = [reading.day]
    else:
        nearest = camera_time.astimezone(clock_zone).date()
        days = []
        for step in (-1, 0, 1):
            try:
                days.append(nearest + timedelta(days=step))
            except OverflowError:
                pass
    moments = []
    for day in days:
        try:
            moment = datetime.combine(day, reading.time_of_day, clock_zone) + extra
            moment.astimezone(timezone.utc)
        except OverflowError:
            continue
        moments.append(moment)
    if not moments:
        # Translators: {value} is the time on the clock given by the user
        raise ClockError(_("The time on the clock is out of range: {value}").format(
            value=reading.text()))
    clock_time = min(moments, key=lambda moment: abs(moment - camera_time))
    difference = clock_time - camera_time
    if reading.utc_offset is None and abs(difference) >= OFFSET_REQUIRED_FROM:
        same = reading.text(utc_offset=timezone(camera_offset))
        if abs(difference) <= timedelta(hours=14):
            near = _suggested_reading(reading, camera_offset, difference).text()
            # Translators: {duration} is a time span such as “1 h 2 min 12 s”;
            # {near} and {same} are times such as “14:03:27+02:00”
            raise ClockError(_(
                "The time on the clock is {duration} away from the capture time of the clock "
                "photo, so the clock may have shown a different time zone than the camera. Add "
                "the UTC offset of the time on the clock, for example “{near}” or “{same}”."
            ).format(duration=exact_duration(difference.total_seconds()), near=near, same=same))
        # Translators: {duration} is a time span such as “15 h 2 min 12 s”;
        # {same} is a time such as “14:03:27+02:00”
        raise ClockError(_(
            "The time on the clock is {duration} away from the capture time of the clock "
            "photo. Add the UTC offset of the time on the clock as well, for example “{same}”."
        ).format(duration=exact_duration(difference.total_seconds()), same=same))
    if reading.day is None and abs(difference) > MAX_WITHOUT_DATE:
        # Translators: a form of a date, shown to the user in an example of
        # the time on the clock; Y is a digit of the year, M of the month and
        # D of the day
        form = reading.text(date_form=_("YYYY-MM-DD"))
        # Translators: {duration} is a time span such as “7 h 2 min 12 s”;
        # {form} is a pattern such as “YYYY-MM-DDT14:03:27+09:00”
        raise ClockError(_(
            "The time on the clock is {duration} away from the capture time of the clock "
            "photo. If the camera’s clock really is that far off, give the date shown on the "
            "clock as well, in the form “{form}”."
        ).format(duration=exact_duration(difference.total_seconds()), form=form))
    return ClockCorrection(round(difference.total_seconds(), 3), camera_time, clock_time, reading)


def measure(path, reading, manual_tz):
    """Return the ClockCorrection from the clock photo at path.

    The photo is only read. manual_tz is the time zone given for every
    photo, as in capture_time(). Raises ClockError.
    """
    if not os.path.lexists(path):
        reason = _("no such file or directory")
    elif not os.path.isfile(path):
        reason = _("not a regular file")
    else:
        try:
            meta = read_metadata([path])[0]
        except RuntimeError as e:
            reason = str(e)
        else:
            taken, reason = capture_time(meta, manual_tz)
            if taken is not None:
                return correction_from(taken, reading)._replace(photo=path, tz_source=reason)
    # Translators: {path} is the file given as the clock photo; {reason} says
    # what is wrong with it
    raise ClockError(_("Cannot use the clock photo {path}: {reason}").format(
        path=path, reason=reason))


def _suggested_reading(reading, camera_offset, difference):
    """The reading with the UTC offset that brings it closest to the camera's time."""
    offset = camera_offset + round(difference / OFFSET_STEP) * OFFSET_STEP
    days = 0
    while offset > timedelta(hours=14):
        offset -= timedelta(days=1)
        days -= 1
    while offset < timedelta(hours=-12):
        offset += timedelta(days=1)
        days += 1
    # A day less in the offset is a day earlier on the clock, for the same moment
    if reading.day is not None and days:
        try:
            reading = reading._replace(day=reading.day + timedelta(days=days))
        except OverflowError:
            pass
    return reading._replace(utc_offset=timezone(offset))
