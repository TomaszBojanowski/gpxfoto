"""Working out the correction of the camera clock from a photo of a clock."""
import re
from collections import namedtuple
from datetime import date, time
from gettext import gettext as _

from gpxfoto.engine.photos import format_utc_offset, parse_utc_offset

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
