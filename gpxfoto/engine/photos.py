"""Finding photos and reading their metadata and capture time."""
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from gettext import gettext as _

from gpxfoto.engine.writer import BACKUP_DIR, TEMP_PREFIX

EXTENSIONS = {".jpg", ".jpeg"}

# Where the time zone of a capture time comes from
TZ_CAMERA = "camera"     # OffsetTimeOriginal or OffsetTime in EXIF
TZ_MANUAL = "manual"     # given by the user
TZ_SYSTEM = "system"     # missing in EXIF, the computer's time zone is used


def find_photos(paths, recursive):
    """Return the photos in paths; directories are searched for JPEG files.

    The search skips backup directories and temporary files of gpxfoto, so
    that backups are never geotagged; paths given explicitly are taken as
    they are. A photo reached in several ways is returned only once.
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
                subdirs[:] = sorted(d for d in subdirs if d != BACKUP_DIR)
                for name in sorted(files):
                    if name.startswith(TEMP_PREFIX):
                        continue
                    if os.path.splitext(name)[1].lower() in EXTENSIONS:
                        add(os.path.join(directory, name))
                if not recursive:
                    break
        elif os.path.isfile(path):
            add(path)
        else:
            raise FileNotFoundError(_("No such file or directory: {path}").format(path=path))
    return found


def read_metadata(files):
    """Read the metadata of files with exiftool, in batches of 500.

    "SourceFile" of each entry is the path exactly as given, also when the
    file name is not valid UTF-8. Files exiftool could not read are left out.
    """
    result = []
    for start in range(0, len(files), 500):
        batch = files[start:start + 500]
        command = ["exiftool", "-json", "-n", "-DateTimeOriginal", "-CreateDate",
                   "-OffsetTimeOriginal", "-OffsetTime", "-SubSecTimeOriginal",
                   "-GPSLatitude", "-GPSLongitude", "--"] + batch
        process = subprocess.run(command, capture_output=True, text=True, errors="replace")
        if not process.stdout.strip():
            raise RuntimeError(_("exiftool returned no data:\n{errors}").format(
                errors=process.stderr))
        try:
            data = json.loads(process.stdout)
            if not (isinstance(data, list) and all(isinstance(e, dict) for e in data)):
                raise ValueError("not a list of objects")
        except ValueError as e:
            raise RuntimeError(_("exiftool returned data that cannot be read: {error}").format(
                error=e)) from e
        # exiftool keeps the order of the files
        entries = iter(data)
        entry = next(entries, None)
        for path in batch:
            if entry is not None and entry.get("SourceFile") == _as_exiftool_shows(path):
                entry["SourceFile"] = path
                result.append(entry)
                entry = next(entries, None)
    return result


def _as_exiftool_shows(path):
    """exiftool shows each byte of a file name that is not UTF-8 as "?"."""
    return re.sub("[\udc80-\udcff]", "?", path)


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
        # Translators: reason why a photo was skipped
        return None, _("no capture time in EXIF")
    try:
        time = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        # Translators: reason why a photo was skipped; {value} is the text found
        return None, _("invalid capture time in EXIF: {value}").format(value=raw)
    fraction = str(meta.get("SubSecTimeOriginal", "")).strip()
    if fraction.isascii() and fraction.isdigit():
        time += timedelta(seconds=float("0." + fraction))
    if manual_tz is not None:
        return time.replace(tzinfo=manual_tz), TZ_MANUAL
    for name in ("OffsetTimeOriginal", "OffsetTime"):
        try:
            return time.replace(tzinfo=parse_utc_offset(str(meta[name]))), TZ_CAMERA
        except (KeyError, ValueError):
            pass
    return time.astimezone(), TZ_SYSTEM
