"""Command-line tool."""
import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from gettext import gettext as _, ngettext

from gpxfoto import i18n
from gpxfoto.engine.matching import match_photos, summarize
from gpxfoto.engine.photos import (
    TZ_MANUAL, TZ_SYSTEM, check_exiftool, find_photos, parse_utc_offset, photo_from_metadata,
    read_metadata)
from gpxfoto.engine.track import load_track
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
    N_("ignored explicit argument %r"),
    N_("expected one argument"),
    N_("invalid %(type)s value: %(value)r"),
)


def printable(text):
    """Text for the terminal; bytes of a file name that are not UTF-8 become �."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


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


def join_negative_time_zone(argv):
    """Let "--timezone -05:00" work like "--timezone=-05:00".

    argparse takes a value that starts with "-" and is not a plain number
    for an option.
    """
    result = []
    i = 0
    while i < len(argv):
        if argv[i] == "--":
            return result + argv[i:]
        if (argv[i] == "--timezone" and i + 1 < len(argv)
                and re.match(r"-\d", argv[i + 1])):
            result.append("--timezone=" + argv[i + 1])
            i += 2
        else:
            result.append(argv[i])
            i += 1
    return result


def build_parser():
    # Options must be given in full: an abbreviation such as --over would
    # change files, and new options would change what abbreviations mean
    parser = argparse.ArgumentParser(
        prog="gpxfoto", allow_abbrev=False,
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


def photo_line(result):
    """The preview line of one photo."""
    name = printable(os.path.basename(result.photo.path))
    time = "" if result.time is None else f"{result.time:%X}  "
    if result.reason is not None:
        # Translators: shown after the file name of a photo; {reason} says
        # why the photo was skipped, e.g. “already has a location”
        return f"  {name:<16} {time}" + _("skipped: {reason}").format(reason=result.reason)
    position = i18n.coordinates(result.lat, result.lon)
    if result.ele is not None:
        # Translators: elevation in metres
        ele_text = _("{elevation} m").format(elevation=i18n.number(result.ele, width=6))
    else:
        ele_text = "       —"
    note = ""
    if result.photo.tz_source in TZ_NOTES:
        note = "  [" + _(TZ_NOTES[result.photo.tz_source]).format(option="--timezone") + "]"
    return f"  {name:<16} {time}{position} {ele_text}{note}"


def main():
    i18n.setup()
    args = build_parser().parse_args(join_negative_time_zone(sys.argv[1:]))

    if shutil.which("exiftool") is None:
        sys.exit(_("exiftool is not installed. On Fedora, install it with: {command}").format(
            command="sudo dnf install perl-Image-ExifTool"))
    try:
        check_exiftool()
    except RuntimeError as e:
        sys.exit(str(e))
    try:
        manual_tz = parse_utc_offset(args.timezone) if args.timezone is not None else None
    except ValueError:
        sys.exit(_("The time zone must be in the form +HH:MM, for example +02:00 or -05:00."))

    try:
        track = load_track(args.gpx)
    except ValueError as e:
        sys.exit(str(e))
    if track is None:
        sys.exit(ngettext("The GPX file contains no track points with timestamps.",
                          "The GPX files contain no track points with timestamps.",
                          len(args.gpx)))
    start = datetime.fromtimestamp(track.first).astimezone()
    end = datetime.fromtimestamp(track.last).astimezone()
    # Translators: date and time of a track point as a strftime format. %x
    # and %X follow the system's regional settings; replace them only if
    # those are wrong for your language, as they are for Polish on macOS
    # (%-d is the day without a leading zero).
    # xgettext:no-python-format
    time_format = _("%x %X")
    # Translators: {start} and {end} are the date and time of the first and the
    # last track point
    print(ngettext("Track: {count} point, {start} – {end} (this computer’s time zone)",
                   "Track: {count} points, {start} – {end} (this computer’s time zone)",
                   len(track.points)).format(count=i18n.number(len(track.points)),
                                       start=start.strftime(time_format),
                                       end=end.strftime(time_format)))
    if args.offset:
        # Translators: {correction} is a time span with a sign, such as
        # “+2 min 12 s”, added to the capture time of every photo
        print(_("Clock correction: {correction}").format(
            correction=i18n.exact_duration(args.offset, sign=True)))

    try:
        files = find_photos(args.photos, args.recursive)
    except FileNotFoundError as e:
        sys.exit(str(e))
    if not files:
        sys.exit(_("No JPEG photos found."))
    # Taken before exiftool reads the photos: for their access times, and
    # to find photos that another program changes before they are written
    seen = {}
    for path in files:
        try:
            seen[path] = os.stat(path)
        except OSError:
            pass
    try:
        metadata = read_metadata(files)
    except RuntimeError as e:
        sys.exit(str(e))

    photos = [photo_from_metadata(meta, manual_tz) for meta in metadata]
    results = match_photos(photos, [track], args.offset, args.max_gap,
                           overwrite=args.overwrite)
    for result in results:
        print(photo_line(result))
    summary = summarize(results)
    plan = [result for result in results if result.reason is None]

    print(_("Matched: {matched}, skipped: {skipped}").format(
        matched=i18n.number(summary.matched), skipped=i18n.number(summary.skipped)))
    if not args.write:
        if plan:
            # Translators: {option} is the command-line option --write
            print(_("This was a preview; no files were changed. "
                    "Use {option} to write the locations.").format(option="--write"))
        return

    written = errors = 0
    for result in plan:
        path = result.photo.path
        try:
            write_location(path, result.lat, result.lon, result.ele, result.time_utc,
                           args.backup, replace=result.photo.has_location,
                           seen=seen.get(path))
            written += 1
        except (RuntimeError, ValueError, OSError) as e:
            errors += 1
            print("  " + _("Could not write {name}: {error} (file unchanged)").format(
                name=printable(os.path.basename(path)), error=printable(str(e))))
    print(_("Written: {written}, errors: {errors}").format(
        written=i18n.number(written), errors=i18n.number(errors)))
    if written:
        print(_("The image data of every written file was verified as unchanged."))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
