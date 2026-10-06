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
    TZ_MANUAL: N_("manual time zone"),
    TZ_SYSTEM: N_("system time zone (not in EXIF)"),
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gpxfoto",
        description=_("Adds locations from a GPX track to photos without changing the image."))
    parser.add_argument("photos", nargs="+", metavar=_("photos"),
                        help=_("JPEG files or directories with photos"))
    parser.add_argument("-g", "--gpx", action="append", required=True,
                        help=_("GPX file with the track (can be given more than once)"))
    parser.add_argument("--write", action="store_true",
                        help=_("write the locations to the files (without this option "
                               "only a preview is shown)"))
    parser.add_argument("--offset", type=float, default=0.0, metavar=_("SECONDS"),
                        help=_("camera clock correction in seconds, added to the capture time"))
    parser.add_argument("--timezone", metavar=_("+HH:MM"),
                        help=_("camera time zone; read from the photo's EXIF data by default"))
    parser.add_argument("--max-gap", type=float, default=float(DEFAULT_MAX_GAP),
                        metavar=_("SECONDS"),
                        help=_("largest allowed time between a photo and the nearest track "
                               "point (default: {seconds} s)").format(seconds=DEFAULT_MAX_GAP))
    parser.add_argument("--overwrite", action="store_true",
                        help=_("also change photos that already have a location"))
    parser.add_argument("--backup", action="store_true",
                        help=_("keep copies of the original files in the “{directory}” "
                               "subdirectory").format(directory=BACKUP_DIR))
    parser.add_argument("-r", "--recursive", action="store_true",
                        help=_("also look for photos in subdirectories"))
    return parser


def main():
    i18n.setup()
    args = build_parser().parse_args()

    if shutil.which("exiftool") is None:
        sys.exit(_("exiftool is not installed. On Fedora, install it with: "
                   "sudo dnf install perl-Image-ExifTool"))
    try:
        manual_tz = parse_utc_offset(args.timezone) if args.timezone else None
    except (ValueError, IndexError):
        sys.exit(_("The time zone must be given as +02:00 or -05:00."))

    points = load_gpx(args.gpx)
    if not points:
        sys.exit(ngettext("The GPX file contains no track points with a time.",
                          "The GPX files contain no track points with a time.",
                          len(args.gpx)))
    times = [p[0] for p in points]
    start = datetime.fromtimestamp(times[0]).astimezone()
    end = datetime.fromtimestamp(times[-1]).astimezone()
    print(ngettext("Track: {count} point, {start} – {end} (local time of this computer)",
                   "Track: {count} points, {start} – {end} (local time of this computer)",
                   len(points)).format(count=i18n.number(len(points)),
                                       start=f"{start:%x %X}", end=f"{end:%X}"))

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
            print(f"  {name:<16} " + _("skipped: {reason}").format(
                reason=_("already has a location")))
            skipped += 1
            continue
        taken, detail = capture_time(meta, manual_tz)
        if taken is None:
            print(f"  {name:<16} " + _("skipped: {reason}").format(reason=detail))
            skipped += 1
            continue
        taken += timedelta(seconds=args.offset)
        result = locate(points, times, taken.timestamp(), args.max_gap)
        if result[0] is None:
            print(f"  {name:<16} {taken:%X}  "
                  + _("skipped: {reason}").format(reason=result[1]))
            skipped += 1
            continue
        lat, lon, ele, gap = result
        # Translators: latitude and longitude in degrees. If your language
        # uses a comma as the decimal separator, separate them with something
        # else, for example a semicolon.
        position = _("{latitude}, {longitude}").format(
            latitude=i18n.number(lat, 6), longitude=i18n.number(lon, 6))
        if ele is not None:
            # Translators: elevation in metres
            ele_text = _("{elevation} m").format(elevation=i18n.number(ele, width=6))
        else:
            ele_text = "       —"
        note = f"  [{_(TZ_NOTES[detail])}]" if detail in TZ_NOTES else ""
        print(f"  {name:<16} {taken:%X}  {position} {ele_text}{note}")
        plan.append((path, lat, lon, ele, taken.astimezone(timezone.utc)))

    print(_("Matched: {matched}, skipped: {skipped}").format(
        matched=i18n.number(len(plan)), skipped=i18n.number(skipped)))
    if not args.write:
        if plan:
            print(_("This was a preview; nothing was written. "
                    "Use --write to write the locations."))
        return

    written = errors = 0
    for path, lat, lon, ele, time_utc in plan:
        try:
            write_location(path, lat, lon, ele, time_utc, args.backup)
            written += 1
        except (RuntimeError, ValueError, OSError) as e:
            errors += 1
            print("  " + _("ERROR {name}: {error} (file unchanged)").format(
                name=os.path.basename(path), error=e))
    print(_("Written: {written}, errors: {errors}. "
            "Image data checked in every written file: unchanged.").format(
                written=i18n.number(written), errors=i18n.number(errors)))
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
