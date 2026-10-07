"""Command-line tool."""
import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from gettext import gettext as _, ngettext

from gpxfoto import i18n
from gpxfoto.engine.photos import (
    TZ_MANUAL, TZ_SYSTEM, capture_time, find_photos, parse_utc_offset, read_metadata)
from gpxfoto.engine.track import load_gpx, locate
from gpxfoto.engine.writer import BACKUP_DIR, write_location
from gpxfoto.i18n import N_

DEFAULT_MAX_GAP = 120

# Notes shown next to photos whose time zone did not come from the camera
TZ_NOTES = {
    # Translators: {option} is the command-line option --timezone
    TZ_MANUAL: N_("time zone from {option}"),
    TZ_SYSTEM: N_("computer’s time zone (not in EXIF)"),
}

# Messages printed by argparse itself. They are listed here so that they
# end up in the translation template; argparse looks them up at run time
# in the program's translation domain.
ARGPARSE_MESSAGES = (
    N_("usage: "),
    N_("%(heading)s:"),
    N_("positional arguments"),
    N_("options"),
    N_("show this help message and exit"),
    N_("%(prog)s: error: %(message)s\n"),
    N_("argument %(argument_name)s: %(message)s"),
    N_("the following arguments are required: %s"),
    N_("unrecognized arguments: %s"),
    N_("ambiguous option: %(option)s could match %(matches)s"),
    N_("ignored explicit argument %r"),
    N_("expected one argument"),
    N_("invalid %(type)s value: %(value)r"),
)


def seconds(text):
    """argparse type: a finite number of seconds that fits a time span."""
    try:
        value = float(text)
        timedelta(seconds=value)        # fails for NaN, infinity and huge values
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(
            _("not a valid number of seconds: {value}").format(value=text)) from None
    return value


def non_negative_seconds(text):
    value = seconds(text)
    if value < 0:
        raise argparse.ArgumentTypeError(_("must not be negative: {value}").format(value=text))
    return value


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gpxfoto",
        description=_("Adds locations from GPX tracks to photos without changing the "
                      "image data."))
    # Translators: placeholder for arguments in the usage line of --help;
    # keep it a single word
    parser.add_argument("photos", nargs="+", metavar=_("PHOTO"),
                        help=_("JPEG files or directories with photos"))
    # Translators: placeholder for a file name in --help; keep it a single word
    parser.add_argument("-g", "--gpx", action="append", required=True, metavar=_("FILE"),
                        help=_("GPX file with the track (can be given more than once)"))
    parser.add_argument("--write", action="store_true",
                        help=_("write the locations to the files (without this option "
                               "only a preview is shown)"))
    # Translators: placeholder for a number in --help; keep it a single word
    parser.add_argument("--offset", type=seconds, default=0.0, metavar=_("SECONDS"),
                        help=_("camera clock correction in seconds, added to the capture time"))
    # Translators: placeholder in --help; HH stands for hours, MM for minutes
    parser.add_argument("--timezone", metavar=_("+HH:MM"),
                        help=_("camera time zone for all photos (default: read from each "
                               "photo’s EXIF data)"))
    parser.add_argument("--max-gap", type=non_negative_seconds, default=float(DEFAULT_MAX_GAP),
                        metavar=_("SECONDS"),
                        help=_("largest allowed time between a photo and the nearest track "
                               "point (default: {seconds} s)").format(seconds=DEFAULT_MAX_GAP))
    parser.add_argument("--overwrite", action="store_true",
                        help=_("also change photos that already have a location"))
    # Translators: {directory} is the name of the directory, which is not translated
    parser.add_argument("--backup", action="store_true",
                        help=_("keep copies of the original files in a “{directory}” "
                               "subdirectory next to each photo; an existing copy is never "
                               "replaced").format(directory=BACKUP_DIR))
    parser.add_argument("-r", "--recursive", action="store_true",
                        help=_("also look for photos in subdirectories"))
    return parser


def main():
    i18n.setup()
    args = build_parser().parse_args()

    if shutil.which("exiftool") is None:
        sys.exit(_("exiftool is not installed. On Fedora, install it with: {command}").format(
            command="sudo dnf install perl-Image-ExifTool"))
    try:
        manual_tz = parse_utc_offset(args.timezone) if args.timezone else None
    except (ValueError, IndexError):
        sys.exit(_("The time zone must be in the form +HH:MM, for example +02:00 or -05:00."))

    try:
        points = load_gpx(args.gpx)
    except ValueError as e:
        sys.exit(str(e))
    if not points:
        sys.exit(ngettext("The GPX file contains no track points with timestamps.",
                          "The GPX files contain no track points with timestamps.",
                          len(args.gpx)))
    times = [p[0] for p in points]
    start = datetime.fromtimestamp(times[0]).astimezone()
    end = datetime.fromtimestamp(times[-1]).astimezone()
    # Translators: {start} and {end} are the date and time of the first and the
    # last track point
    print(ngettext("Track: {count} point, {start} – {end} (this computer’s time zone)",
                   "Track: {count} points, {start} – {end} (this computer’s time zone)",
                   len(points)).format(count=i18n.number(len(points)),
                                       start=f"{start:%x %X}", end=f"{end:%x %X}"))

    try:
        files = find_photos(args.photos, args.recursive)
    except FileNotFoundError as e:
        sys.exit(str(e))
    if not files:
        sys.exit(_("No JPEG photos found."))
    try:
        metadata = read_metadata(files)
    except RuntimeError as e:
        sys.exit(str(e))

    plan, skipped = [], 0
    for meta in metadata:
        path = meta["SourceFile"]
        name = os.path.basename(path)
        if "GPSLatitude" in meta and not args.overwrite:
            # Translators: reason why a photo was skipped
            reason = _("already has a location")
            # Translators: shown after the file name of a photo; {reason} says
            # why the photo was skipped, e.g. “already has a location”
            print(f"  {name:<16} " + _("skipped: {reason}").format(reason=reason))
            skipped += 1
            continue
        taken, detail = capture_time(meta, manual_tz)
        if taken is None:
            print(f"  {name:<16} " + _("skipped: {reason}").format(reason=detail))
            skipped += 1
            continue
        try:
            taken += timedelta(seconds=args.offset)
        except OverflowError:
            # Translators: reason why a photo was skipped
            reason = _("the corrected capture time is out of range")
            print(f"  {name:<16} " + _("skipped: {reason}").format(reason=reason))
            skipped += 1
            continue
        result = locate(points, times, taken.timestamp(), args.max_gap)
        if result[0] is None:
            print(f"  {name:<16} {taken:%X}  "
                  + _("skipped: {reason}").format(reason=result[1]))
            skipped += 1
            continue
        lat, lon, ele, gap = result
        position = i18n.coordinates(lat, lon)
        if ele is not None:
            # Translators: elevation in metres
            ele_text = _("{elevation} m").format(elevation=i18n.number(ele, width=6))
        else:
            ele_text = "       —"
        note = ""
        if detail in TZ_NOTES:
            note = "  [" + _(TZ_NOTES[detail]).format(option="--timezone") + "]"
        print(f"  {name:<16} {taken:%X}  {position} {ele_text}{note}")
        plan.append((path, lat, lon, ele, taken.astimezone(timezone.utc)))

    print(_("Matched: {matched}, skipped: {skipped}").format(
        matched=i18n.number(len(plan)), skipped=i18n.number(skipped)))
    if not args.write:
        if plan:
            # Translators: {option} is the command-line option --write
            print(_("This was a preview; no files were changed. "
                    "Use {option} to write the locations.").format(option="--write"))
        return

    written = errors = 0
    for path, lat, lon, ele, time_utc in plan:
        try:
            write_location(path, lat, lon, ele, time_utc, args.backup)
            written += 1
        except (RuntimeError, ValueError, OSError) as e:
            errors += 1
            print("  " + _("Could not write {name}: {error} (file unchanged)").format(
                name=os.path.basename(path), error=e))
    print(_("Written: {written}, errors: {errors}").format(
        written=i18n.number(written), errors=i18n.number(errors)))
    if written:
        print(_("The image data of every written file was verified as unchanged."))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
