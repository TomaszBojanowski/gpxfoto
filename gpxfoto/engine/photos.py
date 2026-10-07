"""Finding photos and reading their metadata and capture time."""
import json
import os
import re
import subprocess
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from gettext import gettext as _

from gpxfoto.engine.writer import TEMP_PREFIX, is_backup_dir

EXTENSIONS = {".jpg", ".jpeg"}

# Where the time zone of a capture time comes from
TZ_CAMERA = "camera"     # OffsetTimeOriginal or OffsetTime in EXIF
TZ_MANUAL = "manual"     # given by the user
TZ_SYSTEM = "system"     # missing in EXIF, the computer's time zone is used

# Camera models known to record the UTC time in Panasonic:TimeStamp
CAMERA_UTC_MODELS = frozenset({"DC-S5M2"})
# A capture time and the camera's UTC time this far apart still agree: the
# sub-seconds, and a long exposure followed by an equally long dark frame
# for noise reduction, can part them by up to about two minutes
TIME_CHECK_TOLERANCE = 120
# Every time zone in use is a whole number of quarter hours from UTC
ZONE_STEP = 900
# The time zones in use that are not a whole number of half hours from UTC
_QUARTER_HOUR_ZONES = frozenset(timedelta(hours=h, minutes=45) for h in (5, 8, 12, 13))
_CAMERA_UTC = re.compile(r"([0-9]{4}):([0-9]{2}):([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})"
                         r"(\.[0-9]+)?")

# What the matching needs to know about a photo. taken is the capture
# time from capture_time(), before any correction, and tz_source where its
# time zone came from; without a usable time, taken and tz_source are None
# and reason says why. has_location: the photo already has a location.
# camera_utc: the UTC time the camera recorded, from camera_utc_time().
Photo = namedtuple("Photo", "path taken tz_source reason has_location camera_utc",
                   defaults=(None,))


def find_photos(paths, recursive):
    """Return the photos in paths; directories are searched for JPEG files.

    The search skips the backup directories of gpxfoto and its temporary
    files and directories; paths given explicitly are taken as they are.
    A photo reached in several ways is returned only once.
    """
    found, seen = [], set()

    def add(photo):
        real = os.path.realpath(photo)
        if real not in seen:
            seen.add(real)
            found.append(photo)

    for path in paths:
        if os.path.isdir(path):
            for directory, subdirs, files in os.walk(path):
                subdirs[:] = sorted(
                    d for d in subdirs if not d.startswith(TEMP_PREFIX)
                    and not is_backup_dir(os.path.join(directory, d)))
                for name in sorted(files):
                    if name.startswith(TEMP_PREFIX):
                        continue
                    photo = os.path.join(directory, name)
                    # Not a broken symbolic link or a special file
                    if os.path.splitext(name)[1].lower() in EXTENSIONS and os.path.isfile(photo):
                        add(photo)
                if not recursive:
                    break
        elif os.path.isfile(path):
            add(path)
        else:
            raise FileNotFoundError(_("No such file or directory: {path}").format(path=path))
    return found


def check_exiftool():
    """Raise RuntimeError with exiftool's messages if it does not run."""
    process = subprocess.run(["exiftool", "-ver"], capture_output=True, text=True,
                             errors="replace")
    if process.returncode != 0 or not process.stdout.strip():
        raise RuntimeError(_("exiftool does not work:\n{errors}").format(errors=process.stderr))


def read_metadata(files):
    """Read the metadata of files with exiftool, in batches of 500.

    Each entry has "SourceFile" set to the path exactly as given. A file
    exiftool cannot read gets an entry with only "SourceFile" and "Error".
    """
    result = []
    for start in range(0, len(files), 500):
        batch = files[start:start + 500]
        result += _assign(batch, *_run_exiftool(batch))
    return result


def _run_exiftool(files):
    """Return exiftool's entries for files and its error output."""
    # -Panasonic:TimeStamp names its group: a bare -TimeStamp also reads
    # XMP tags, and -MakerNotes:TimeStamp those of other makers. No other
    # tag read here is called TimeStamp, so the -json key is unambiguous.
    command = ["exiftool", "-json", "-n", "-DateTimeOriginal", "-CreateDate",
               "-OffsetTimeOriginal", "-OffsetTime", "-SubSecTimeOriginal",
               "-GPSLatitude", "-GPSLongitude", "-Model", "-Panasonic:TimeStamp", "-Error",
               "--"] + files
    process = subprocess.run(command, capture_output=True, text=True, errors="replace")
    if not process.stdout.strip():
        return [], process.stderr
    try:
        entries = json.loads(process.stdout)
    except ValueError:
        entries = None
    if not (isinstance(entries, list) and all(isinstance(e, dict) for e in entries)):
        raise RuntimeError(_("exiftool returned data that cannot be read"))
    return entries, process.stderr


def _assign(files, entries, errors):
    """Pair exiftool's entries with files.

    exiftool keeps the order of the files but leaves out files it cannot
    read, and the names it shows are not reliable (bytes that are not
    UTF-8 become "?"). With one entry per file they pair up in order;
    otherwise the files are read again in halves until they do.
    """
    if len(entries) == len(files):
        for entry, path in zip(entries, files):
            entry["SourceFile"] = path
        return entries
    if len(files) == 1:
        lines = errors.strip().splitlines()
        error = lines[-1] if lines else _("exiftool could not read the file")
        return [{"SourceFile": files[0], "Error": error}]
    half = len(files) // 2
    return (_assign(files[:half], *_run_exiftool(files[:half]))
            + _assign(files[half:], *_run_exiftool(files[half:])))


def parse_utc_offset(text):
    """Time zone from an offset such as "+02:00" or "-05:30".

    Raises ValueError for anything else, including offsets of more than
    14 hours, which no time zone on Earth uses.
    """
    match = re.fullmatch(r"([+-]?)([0-9]{2}):([0-5][0-9])", text)
    if not match:
        raise ValueError(f"not a UTC offset: {text!r}")
    offset = timedelta(hours=int(match[2]), minutes=int(match[3]))
    if offset > timedelta(hours=14):
        raise ValueError(f"not a UTC offset: {text!r}")
    return timezone(-offset if match[1] == "-" else offset)


def format_utc_offset(offset):
    """The text of a UTC offset given as a timedelta, e.g. "+02:00" or "-05:30".

    parse_utc_offset() reads it back.
    """
    minutes = round(offset.total_seconds() / 60)
    hours, minutes = divmod(abs(minutes), 60)
    return f"{'-' if offset < timedelta(0) else '+'}{hours:02}:{minutes:02}"


def photo_from_metadata(meta, manual_tz):
    """Return the Photo described by exiftool's metadata of one file."""
    taken, detail = capture_time(meta, manual_tz)
    if taken is None:
        return Photo(meta["SourceFile"], None, None, detail, "GPSLatitude" in meta,
                     camera_utc_time(meta))
    return Photo(meta["SourceFile"], taken, detail, None, "GPSLatitude" in meta,
                 camera_utc_time(meta))


# A capture time that does not match the camera's UTC time: difference is
# how many seconds the capture time is later, and suggested_tz the time
# zone in use that would make them match, or None
TimeCheck = namedtuple("TimeCheck", "difference suggested_tz")
# The time checks of one source of time zones (TZ_*): how many photos, and
# the time zone that would make all of them match, or None
TimeCheckSummary = namedtuple("TimeCheckSummary", "source count suggested_tz")


def is_real_utc_offset(offset):
    """Whether some time zone in use is offset (a timedelta) from UTC."""
    return (abs(offset) <= timedelta(hours=14)
            and (offset % timedelta(minutes=30) == timedelta(0) or offset in _QUARTER_HOUR_ZONES))


def check_against_camera_utc(taken, source, camera_utc, correction=0.0):
    """Compare a capture time with the UTC time the camera recorded.

    taken is the capture time from capture_time(), before any correction,
    with its time zone from source (TZ_*); both times come from the same
    camera clock, so a difference comes from the time zone. Returns None
    when they match, or when correction (in seconds) makes up for the
    difference; otherwise a TimeCheck. A time zone is suggested only when
    it was given by the user or taken from the computer: when it comes from
    EXIF, a difference means that another program changed EXIF, and which
    value is right is not known.
    """
    difference = (taken - camera_utc).total_seconds()
    if abs(difference) <= TIME_CHECK_TOLERANCE:
        return None
    # The correction moves the time used to within half a step of the camera's UTC time
    if correction and abs(difference + correction) < ZONE_STEP / 2:
        return None
    suggested = None
    steps = round(difference / ZONE_STEP)
    whole_steps = abs(difference - steps * ZONE_STEP) <= TIME_CHECK_TOLERANCE
    if source != TZ_CAMERA and steps and whole_steps:
        offset = taken.utcoffset() + timedelta(seconds=steps * ZONE_STEP)
        if is_real_utc_offset(offset):
            suggested = timezone(offset)
    return TimeCheck(difference, suggested)


def summarize_time_checks(checks, agreeing=frozenset()):
    """Return a TimeCheckSummary per source of time zones, for (source, TimeCheck) pairs.

    The order is TZ_MANUAL, TZ_SYSTEM, TZ_CAMERA, without sources that have
    no checks. agreeing holds the sources of photos whose capture time
    matches the camera's UTC time: no time zone is suggested for them,
    as it would break those photos, e.g. across a change to summer time.
    """
    groups = {}
    for source, check in checks:
        groups.setdefault(source, []).append(check)
    summaries = []
    for source in (TZ_MANUAL, TZ_SYSTEM, TZ_CAMERA):
        group = groups.get(source)
        if group:
            zones = {check.suggested_tz for check in group}
            one = zones.pop() if len(zones) == 1 and source not in agreeing else None
            summaries.append(TimeCheckSummary(source, len(group), one))
    return summaries


def camera_utc_time(meta):
    """The UTC time recorded by the camera, as an aware datetime, or None.

    Only for the models in CAMERA_UTC_MODELS, whose Panasonic:TimeStamp
    holds the capture time in UTC.
    """
    if str(meta.get("Model", "")).strip() not in CAMERA_UTC_MODELS:
        return None
    # str(): exiftool gives a value that looks like a number as a number
    match = _CAMERA_UTC.fullmatch(str(meta.get("TimeStamp", "")).strip())
    if not match:
        return None
    try:
        time = datetime(*map(int, match.groups()[:6]))
        if match[7]:
            time += timedelta(seconds=float("0" + match[7]))
    except (ValueError, OverflowError):
        return None
    # As for capture times: too close to the ends of the calendar to convert
    if not datetime(1, 1, 2) <= time < datetime(9999, 12, 31):
        return None
    return time.replace(tzinfo=timezone.utc)


def capture_time(meta, manual_tz):
    """Return (aware datetime, TZ_* source) or (None, reason)."""
    raw = meta.get("DateTimeOriginal") or meta.get("CreateDate")
    if not raw:
        if meta.get("Error"):
            # Translators: reason why a photo was skipped; {error} comes from exiftool
            return None, _("cannot be read: {error}").format(error=meta["Error"])
        # Translators: reason why a photo was skipped
        return None, _("no capture time in EXIF")
    try:
        time = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
        fraction = str(meta.get("SubSecTimeOriginal", "")).strip()
        if fraction.isascii() and fraction.isdigit():
            time += timedelta(seconds=float("0." + fraction))
    except (ValueError, OverflowError):     # overflow: rounded up beyond the calendar
        time = None
    # Python cannot convert a time within a day of the ends of the calendar
    # between local time and UTC
    if time is None or not datetime(1, 1, 2) <= time < datetime(9999, 12, 31):
        # Translators: reason why a photo was skipped; {value} is the text found
        return None, _("invalid capture time in EXIF: {value}").format(value=raw)
    if manual_tz is not None:
        return time.replace(tzinfo=manual_tz), TZ_MANUAL
    for name in ("OffsetTimeOriginal", "OffsetTime"):
        try:
            return time.replace(tzinfo=parse_utc_offset(str(meta[name]))), TZ_CAMERA
        except (KeyError, ValueError):
            pass
    return time.astimezone(), TZ_SYSTEM
