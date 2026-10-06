"""Finding photos and reading their metadata and capture time."""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

EXTENSIONS = {".jpg", ".jpeg"}

# Where the time zone of a capture time comes from
TZ_CAMERA = "camera"     # OffsetTimeOriginal or OffsetTime in EXIF
TZ_MANUAL = "manual"     # given by the user
TZ_SYSTEM = "system"     # missing in EXIF, the computer's time zone is used


def find_photos(paths, recursive):
    found = []
    for path in paths:
        if os.path.isdir(path):
            for directory, subdirs, files in os.walk(path):
                subdirs.sort()
                for name in sorted(files):
                    if os.path.splitext(name)[1].lower() in EXTENSIONS:
                        found.append(os.path.join(directory, name))
                if not recursive:
                    break
        elif os.path.isfile(path):
            found.append(path)
        else:
            raise FileNotFoundError(f"Nie ma takiego pliku ani katalogu: {path}")
    return found


def read_metadata(files):
    result = []
    for start in range(0, len(files), 500):
        command = ["exiftool", "-json", "-n", "-DateTimeOriginal", "-CreateDate",
                   "-OffsetTimeOriginal", "-OffsetTime", "-SubSecTimeOriginal",
                   "-GPSLatitude", "-GPSLongitude", "--"] + files[start:start + 500]
        process = subprocess.run(command, capture_output=True, text=True)
        if not process.stdout.strip():
            raise RuntimeError("exiftool nie zwrócił danych:\n" + process.stderr)
        result.extend(json.loads(process.stdout))
    return result


def parse_utc_offset(text):
    sign = -1 if text[0] == "-" else 1
    hours, minutes = text.lstrip("+-").split(":")
    return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))


def capture_time(meta, manual_tz):
    """Return (aware datetime, TZ_* source) or (None, reason)."""
    raw = meta.get("DateTimeOriginal") or meta.get("CreateDate")
    if not raw:
        return None, "brak daty wykonania w EXIF"
    try:
        time = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, f"nieczytelna data: {raw}"
    fraction = str(meta.get("SubSecTimeOriginal", "")).strip()
    if fraction.isdigit():
        time += timedelta(seconds=float("0." + fraction))
    offset = meta.get("OffsetTimeOriginal") or meta.get("OffsetTime")
    if manual_tz is not None:
        return time.replace(tzinfo=manual_tz), TZ_MANUAL
    if offset:
        try:
            return time.replace(tzinfo=parse_utc_offset(str(offset))), TZ_CAMERA
        except (ValueError, IndexError):
            pass
    return time.astimezone(), TZ_SYSTEM
