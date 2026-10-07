"""Finding photos and reading their metadata and capture time."""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from gettext import gettext as _

from gpxfoto.engine.writer import TEMP_PREFIX, is_backup_dir

EXTENSIONS = {".jpg", ".jpeg"}

# Where the time zone of a capture time comes from
TZ_CAMERA = "camera"     # OffsetTimeOriginal or OffsetTime in EXIF
TZ_MANUAL = "manual"     # given by the user
TZ_SYSTEM = "system"     # missing in EXIF, the computer's time zone is used


def find_photos(paths, recursive):
    """Return the photos in paths; directories are searched for JPEG files.

    The search skips the backup directories and temporary files of
    gpxfoto; paths given explicitly are taken as they are. A photo reached
    in several ways is returned only once.
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
                    d for d in subdirs if not is_backup_dir(os.path.join(directory, d)))
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
    command = ["exiftool", "-json", "-n", "-DateTimeOriginal", "-CreateDate",
               "-OffsetTimeOriginal", "-OffsetTime", "-SubSecTimeOriginal",
               "-GPSLatitude", "-GPSLongitude", "-Error", "--"] + files
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
