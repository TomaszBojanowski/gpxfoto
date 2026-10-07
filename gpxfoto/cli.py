"""Command-line tool."""
import argparse
import bisect
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from gettext import gettext as _, ngettext

from gpxfoto import i18n
from gpxfoto.engine import checks
from gpxfoto.engine.clock import MAX_WITHOUT_DATE, ClockError, measure, parse_reading
from gpxfoto.engine.matching import corrected_times, match_photos, summarize
from gpxfoto.engine.photos import (
    TZ_CAMERA, TZ_MANUAL, TZ_SYSTEM, check_exiftool, find_photos, format_utc_offset,
    parse_utc_offset, photo_from_metadata, read_metadata, summarize_time_checks)
from gpxfoto.engine.track import (
    find_tracks, load_track, nearest_track, quick_span, tracks_needed)
from gpxfoto.engine.writer import BACKUP_DIR, write_location
from gpxfoto.i18n import N_

DEFAULT_MAX_GAP = 120
# At most this many pairs of photos are named in the warning about jumps
JUMP_EXAMPLES = 3

# Notes shown next to photos whose time zone did not come from the camera
TZ_NOTES = {
    # Translators: {option} is the command-line option --timezone
    TZ_MANUAL: N_("time zone from {option}"),
    TZ_SYSTEM: N_("computer’s time zone (not in EXIF)"),
}

# Why capture times may not match the camera's UTC time, by the source of
# their time zone
TIME_CHECK_CAUSES = {
    # Translators: {option} is the command-line option --timezone
    TZ_MANUAL: N_("Either the time zone given with {option} or the camera’s time zone setting "
                  "is wrong."),
    TZ_SYSTEM: N_("EXIF has no time zone, so this computer’s time zone was used; either it or "
                  "the camera’s time zone setting is wrong."),
    TZ_CAMERA: N_("Another program may have changed the capture time or the time zone in EXIF; "
                  "the locations follow the time in EXIF."),
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


def clock_reading(text):
    """argparse type: the time read on a clock."""
    try:
        return parse_reading(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def offset_value(seconds):
    """seconds as the value of --offset, e.g. "132", "-3468" or "131.52"."""
    text = f"{seconds:.3f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def date_and_time(moment):
    """The date and time of moment as the regional settings show them."""
    # Translators: date and time of a track point as a strftime format. %x
    # and %X follow the system's regional settings; replace them only if
    # those are wrong for your language, as they are for Polish on macOS
    # (%-d is the day without a leading zero).
    # xgettext:no-python-format
    return moment.strftime(_("%x %X"))


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
    # Translators: placeholder for a track file or directory in --help; keep
    # it a single word
    parser.add_argument("-g", "--gpx", action="append", required=True, metavar=_("TRACK"),
                        help=_("GPX file with the track, or a directory with GPX files: each "
                               "photo then gets the track that covers its time (can be given "
                               "more than once)"))
    parser.add_argument("--write", action="store_true",
                        help=_("write the locations to the files (without this option "
                               "only a preview is shown)"))
    # Translators: placeholder for a number in --help; keep it a single word
    parser.add_argument("--offset", type=seconds, metavar=_("SECONDS"),
                        help=_("camera clock correction in seconds, added to the capture time"))
    # Translators: placeholder in --help; HH stands for hours, MM for minutes
    parser.add_argument("--timezone", metavar=_("+HH:MM"),
                        help=_("camera time zone for all photos (default: read from each "
                               "photo’s EXIF data)"))
    parser.add_argument("--clock-photo", metavar=_("FILE"),
                        help=_("photo of an accurate clock, such as the watch that records the "
                               "track, for working out the camera clock correction (with "
                               "{option})").format(option="--clock-time"))
    # Translators: placeholder for a time in --help; keep it a single word
    parser.add_argument("--clock-time", type=clock_reading, metavar=_("TIME"),
                        help=_("time shown on the clock in the {option} photo, 24-hour, for "
                               "example 14:03:27, 14:03:27+02:00 or 2026-10-06T14:03:27+02:00"
                               ).format(option="--clock-photo"))
    parser.add_argument("--max-gap", type=non_negative_seconds, default=float(DEFAULT_MAX_GAP),
                        metavar=_("SECONDS"),
                        help=_("largest allowed time between a photo and the nearest track "
                               "point (default: {seconds} s)").format(seconds=DEFAULT_MAX_GAP))
    parser.add_argument("--no-stops", action="store_true",
                        help=_("do not look for stops; every photo gets the track’s position "
                               "at its time"))
    parser.add_argument("--overwrite", action="store_true",
                        help=_("also change photos that already have a location"))
    # Translators: {directory} is the name of the directory, which is not translated
    parser.add_argument("--backup", action="store_true",
                        help=_("keep copies of the original files in a “{directory}” "
                               "subdirectory next to each photo; an existing copy is never "
                               "replaced").format(directory=BACKUP_DIR))
    parser.add_argument("-r", "--recursive", action="store_true",
                        help=_("also look for photos and tracks in subdirectories"))
    return parser


def time_check_note(check):
    """The note on a photo whose capture time does not match the camera's UTC time."""
    if check.suggested_tz is not None:
        # Translators: a note on the line of a photo; {zone} is a UTC offset
        # such as +02:00
        return _("camera’s UTC time suggests {zone}").format(
            zone=format_utc_offset(check.suggested_tz.utcoffset(None)))
    # Translators: a note on the line of a photo; {duration} is a time span
    # such as “10 min”
    return _("differs by {duration} from the camera’s UTC time").format(
        duration=i18n.duration(abs(check.difference)))


def time_check_lines(results):
    """The summary of the capture times that do not match the camera's UTC time."""
    checks = [(r.photo.tz_source, r.time_check) for r in results if r.time_check is not None]
    # The computer's time zone is not advised when some photos have one in EXIF
    zone_in_exif = any(r.photo.tz_source == TZ_CAMERA for r in results)
    lines = []
    for summary in summarize_time_checks(checks):
        lines.append(ngettext(
            "Warning: {count} photo has a capture time that does not match the UTC time "
            "recorded by the camera.",
            "Warning: {count} photos have capture times that do not match the UTC time "
            "recorded by the camera.", summary.count).format(count=i18n.number(summary.count)))
        lines.append("  " + _(TIME_CHECK_CAUSES[summary.source]).format(option="--timezone"))
        if summary.suggested_tz is not None and not (summary.source == TZ_SYSTEM
                                                     and zone_in_exif):
            option = "--timezone=" + format_utc_offset(summary.suggested_tz.utcoffset(None))
            # Translators: {option} is a command-line option with its value,
            # such as --timezone=+02:00
            lines.append("  " + _("Both times would match with {option}.").format(option=option))
    return lines


def stop_note(stop, zone):
    """The note on a photo taken during a stop, with its times in the time zone zone."""
    start = datetime.fromtimestamp(stop.start, zone)
    end = datetime.fromtimestamp(stop.end, zone)
    if start.date() == end.date():
        start, end = f"{start:%X}", f"{end:%X}"
    else:
        start, end = date_and_time(start), date_and_time(end)
    # Translators: a note on the line of a photo taken during a stop, which
    # gets the stop's position; {start} and {end} are times such as
    # “12:31:05”, with the date when the stop lasts over midnight
    return _("stop {start} – {end}").format(start=start, end=end)


def file_labels(paths):
    """How files are named to the user: by base name, or by the whole path
    where two of them have the same base name."""
    names = [os.path.basename(path) for path in paths]
    return {path: printable(path if names.count(name) > 1 else name)
            for path, name in zip(paths, names)}


def photo_line(result, labels=None, width=0):
    """The preview line of one photo.

    With labels, a dict of file labels, the line names the track files
    its position comes from, in a column width characters wide.
    """
    name = printable(os.path.basename(result.photo.path))
    time = "" if result.time is None else f"{result.time:%X}  "
    notes = []
    if result.reason is None and result.photo.tz_source in TZ_NOTES:
        notes.append(_(TZ_NOTES[result.photo.tz_source]).format(option="--timezone"))
    if result.time_check is not None:
        notes.append(time_check_note(result.time_check))
    if result.stop is not None:
        notes.append(stop_note(result.stop, result.time.tzinfo))
    notes = "  [" + "; ".join(notes) + "]" if notes else ""
    files = ", ".join(labels[path] for path in result.files) if labels else ""
    if result.reason is not None:
        # Translators: shown after the file name of a photo; {reason} says
        # why the photo was skipped, e.g. “already has a location”
        line = f"  {name:<16} {time}" + _("skipped: {reason}").format(reason=result.reason)
        return line + (f" ({files})" if files else "") + notes
    position = i18n.coordinates(result.lat, result.lon)
    if result.ele is not None:
        # Translators: elevation in metres
        ele_text = _("{elevation} m").format(elevation=i18n.number(result.ele, width=6))
    else:
        ele_text = "       —"
    source = f"  {files:<{width}}" if labels else ""
    return f"  {name:<16} {time}{position} {ele_text}{source}{notes}".rstrip()


def clock_lines(clock):
    """The preview lines about a correction worked out from a clock photo."""
    # Translators: {correction} is a time span with a sign, such as
    # “+2 min 12 s”; {option} is the same as a command-line option, such as
    # “--offset=132”
    lines = [_("Clock correction: {correction} (equivalent to {option})").format(
        correction=i18n.exact_duration(clock.seconds, sign=True),
        option="--offset=" + offset_value(clock.seconds))]

    def moment(when):
        # Translators: a UTC offset as shown to the user; {offset} is, for
        # example, “+02:00”
        return date_and_time(when) + " " + _("UTC{offset}").format(
            offset=format_utc_offset(when.utcoffset()))

    # Translators: {name} is the file name of the clock photo; {camera} and
    # {clock} are the date and time the camera and the clock showed
    line = _("Clock photo {name}: camera {camera}, clock {clock}").format(
        name=printable(os.path.basename(clock.photo)), camera=moment(clock.camera_time),
        clock=moment(clock.clock_time))
    if clock.tz_source in TZ_NOTES:
        line += "  [" + _(TZ_NOTES[clock.tz_source]).format(option="--timezone") + "]"
    lines.append(line)
    if clock.uncertainty:
        lines.append(_("The time on the clock was given without seconds, so the middle of the "
                       "minute was used; the correction may be off by up to 30 s."))
    return lines


def track_line(track, labels, by_name):
    """The line about one track; by_name names it even when it has several files."""
    start = datetime.fromtimestamp(track.first).astimezone()
    end = datetime.fromtimestamp(track.last).astimezone()
    if by_name or len(track.files) == 1:
        # Translators: {name} is the file name of the track; {start} and {end}
        # are the date and time of its first and last point
        line = ngettext(
            "Track {name}: {count} point, {start} – {end} (this computer’s time zone)",
            "Track {name}: {count} points, {start} – {end} (this computer’s time zone)",
            len(track.points))
    else:
        # Translators: {start} and {end} are the date and time of the first and the
        # last track point
        line = ngettext("Track: {count} point, {start} – {end} (this computer’s time zone)",
                        "Track: {count} points, {start} – {end} (this computer’s time zone)",
                        len(track.points))
    return line.format(name=", ".join(labels[path] for path in track.files),
                       count=i18n.number(len(track.points)), start=date_and_time(start),
                       end=date_and_time(end))


def correction_lines(clock, correction):
    """The lines about the clock correction, if there is one."""
    if clock is not None:
        return clock_lines(clock)
    if correction:
        # Translators: {correction} is a time span with a sign, such as
        # “+2 min 12 s”, added to the capture time of every photo
        return [_("Clock correction: {correction}").format(
            correction=i18n.exact_duration(correction, sign=True))]
    return []


def covering(tracks, results, max_gap):
    """The tracks whose time, widened by max_gap, holds some photo's, by start."""
    times = sorted(result.time.timestamp() for result in results if result.time is not None)
    return sorted((track for track in tracks
                   if bisect.bisect_right(times, track.last + max_gap)
                   > bisect.bisect_left(times, track.first - max_gap)),
                  key=lambda track: track.first)


def with_nearest_tracks(results, tracks, spans, stops):
    """results, where a photo no track covers names the nearest track file."""
    loaded = {}

    def load(path):
        if path not in loaded:
            loaded[path] = load_track([path], named=False, stops=stops)
        return loaded[path]

    singles = [track for track in tracks if len(track.files) == 1]
    unread = {path: span for path, span in spans.items()
              if not any(track.files[0] == path for track in singles)}
    changed = []
    for result in results:
        if not result.covered:
            nearest = nearest_track(result.time.timestamp(), singles, unread, load)
            if nearest is not None:
                seconds, path, after = nearest
                if after:
                    # Translators: reason why a photo was skipped; {duration} is
                    # a time span such as “10 min”
                    reason = _("{duration} after the end of the nearest track")
                else:
                    # Translators: reason why a photo was skipped; {duration} is
                    # a time span such as “10 min”
                    reason = _("{duration} before the start of the nearest track")
                result = result._replace(reason=reason.format(duration=i18n.duration(seconds)),
                                         files=(path,))
        changed.append(result)
    return changed


def usable_offset(offset):
    """Whether a timedelta is a UTC offset that --timezone and --clock-time take."""
    return abs(offset) <= timedelta(hours=14) and not offset % timedelta(minutes=1)


def shots_of(results):
    """The photos with a capture time as the checks of a suspicious match
    see them, and their names."""
    shots, names = [], []
    for result in results:
        if result.time is not None:
            time = result.time.timestamp()
            shots.append(checks.Shot(time, time + result.time.utcoffset().total_seconds(),
                                     result.lat, result.lon))
            names.append(printable(os.path.basename(result.photo.path)))
    return shots, names


def suspicion(results, tracks, shots, args):
    """The signs of a suspicious match.

    The whole-hour shift and the photos in motion are looked for only on
    the track that placed every matched photo (or the only track when
    none was matched), as they need its stops and speeds.
    """
    owner = {path: track for track in tracks for path in track.files}
    used = []
    for result in results:
        if result.reason is None and owner[result.files[0]] not in used:
            used.append(owner[result.files[0]])
    one = used or tracks
    if len(one) == 1 and not args.no_stops:
        track = one[0]
        top = None if track.sources is None else checks.top_speed(track.points, track.sources)
        return checks.suspicious_match(track.points, track.times, track.stops, shots,
                                       args.max_gap, top=top)
    top = max((checks.top_speed(track.points, track.sources) for track in used), default=0.0)
    return checks.Suspicion(None, None, checks.jumps(shots, top))


def shift_lines(hint, shots, correction, clock):
    """The warning about a whole-hour shift, with the option that applies it."""
    # Translators: {shift} is a whole number of hours or half an hour with a
    # sign, such as “+1 h” or “-30 min”; {pinned}, {matched} and {now} are
    # numbers of photos
    lines = [_("Warning: with the photo times shifted by {shift}, clearly more photos fall "
               "during stops: {pinned} of {matched} instead of {now}.").format(
                   shift=i18n.exact_duration(hint.shift, sign=True),
                   pinned=i18n.number(hint.pinned), matched=i18n.number(hint.matched),
                   now=i18n.number(hint.pinned_now))]
    if abs(hint.shift) == 3600:
        lines.append("  " + _("A difference of exactly one hour usually means that the camera "
                              "was not switched to or from summer time, or that its time zone "
                              "is set wrong."))
    else:
        lines.append("  " + _("A difference of whole hours or of half an hour usually means "
                              "that the time zone set in the camera is wrong, for example "
                              "still the home one while travelling."))
    shifted = correction + hint.shift
    option = "--offset=" + offset_value(shifted)
    other = None
    if clock is not None:
        # --offset cannot be used with --clock-photo: the clock's UTC offset
        # moves by the shift instead
        zone = clock.clock_time.utcoffset() - timedelta(seconds=hint.shift)
        if usable_offset(zone):
            reading = clock.reading
            if reading.day is None and abs(shifted) > MAX_WITHOUT_DATE.total_seconds():
                reading = reading._replace(day=clock.clock_time.date())
            option = "--clock-time=" + reading.text(utc_offset=timezone(zone))
    elif not correction:
        zones = {round(shot.clock - shot.time) for shot in shots}
        if len(zones) == 1:
            zone = timedelta(seconds=zones.pop() - hint.shift)
            if usable_offset(zone):
                other = "--timezone=" + format_utc_offset(zone)
    if other is None:
        # Translators: {option} is a command-line option with its value, such
        # as --offset=3600
        lines.append("  " + _("To apply this correction, run again with {option}.").format(
            option=option))
    else:
        # Translators: {option} and {other} are command-line options with
        # their values, such as --offset=3600 and --timezone=+01:00
        lines.append("  " + _("To apply this correction, run again with {option} or "
                              "{other}.").format(option=option, other=other))
    return lines


def motion_lines(motion, clock):
    """The warning about photos taken while the track moves at full pace."""
    # Translators: {fast} and {matched} are numbers of photos
    lines = [ngettext("Warning: the camera clock may be off. {fast} of {matched} matched photo "
                      "was taken while the track shows movement at full pace.",
                      "Warning: the camera clock may be off. {fast} of {matched} matched photos "
                      "were taken while the track shows movement at full pace.",
                      motion.matched).format(fast=i18n.number(motion.fast),
                                             matched=i18n.number(motion.matched))]
    if clock is None:
        # Translators: {photo} and {time} are the command-line options
        # --clock-photo and --clock-time
        advice = _("Photos are usually taken at stops or while slowing down. Check the camera "
                   "clock, for example with a photo of the watch that records the track and the "
                   "options {photo} and {time}.").format(photo="--clock-photo",
                                                         time="--clock-time")
    else:
        # Translators: {option} is the command-line option --clock-time
        advice = _("Photos are usually taken at stops or while slowing down. Check the time on "
                   "the clock given with {option}, and its UTC offset.").format(
                       option="--clock-time")
    return lines + ["  " + advice]


def jump_lines(jumps, shots, names):
    """The warning about photos taken one after the other but placed far apart."""
    lines = [_("Warning: photos taken less than a minute apart are placed implausibly far "
               "apart:")]
    for jump in jumps[:JUMP_EXAMPLES]:
        first, second = shots[jump.first], shots[jump.second]
        zones = [timedelta(seconds=round(shot.clock - shot.time)) for shot in (first, second)]
        values = dict(first=names[jump.first], second=names[jump.second],
                      interval=i18n.exact_duration(jump.clock_gap),
                      distance=i18n.distance(jump.distance))
        if zones[0] == zones[1]:
            # Translators: {first} and {second} are file names; {interval} is
            # a time span such as “4 s”; {distance} is a distance such as “1.3 km”
            line = _("{first} and {second}: taken {interval} apart, placed {distance} "
                     "apart").format(**values)
        else:
            # Translators: {first_zone} and {second_zone} are UTC offsets as
            # shown to the user, such as “UTC+02:00”
            line = _("{first} and {second}: taken {interval} apart, placed {distance} apart, "
                     "time zones {first_zone} and {second_zone}").format(
                         first_zone=_("UTC{offset}").format(offset=format_utc_offset(zones[0])),
                         second_zone=_("UTC{offset}").format(offset=format_utc_offset(zones[1])),
                         **values)
        lines.append("  " + line)
    more = len(jumps) - JUMP_EXAMPLES
    if more > 0:
        lines.append("  " + ngettext("and {count} more pair", "and {count} more pairs",
                                     more).format(count=i18n.number(more)))
    lines.append("  " + _("Check the time zones of these photos, and whether the GPX files "
                          "record different trips at the same time."))
    return lines


def warning_lines(found, shots, names, correction, clock):
    """The lines about the signs of a suspicious match; they change nothing."""
    lines = []
    if found.shift is not None:
        lines += shift_lines(found.shift, shots, correction, clock)
    if found.motion is not None:
        lines += motion_lines(found.motion, clock)
    if found.jumps:
        lines += jump_lines(found.jumps, shots, names)
    return lines


def no_points(count):
    return ngettext("The GPX file contains no track points with timestamps.",
                    "The GPX files contain no track points with timestamps.", count)


def main():
    i18n.setup()
    parser = build_parser()
    args = parser.parse_args(join_negative_time_zone(sys.argv[1:]))
    if (args.clock_photo is None) != (args.clock_time is None):
        given, other = (("--clock-photo", "--clock-time") if args.clock_time is None
                        else ("--clock-time", "--clock-photo"))
        # Translators: {option} and {other} are command-line options
        parser.error(_("{option} must be used together with {other}").format(
            option=given, other=other))
    if args.clock_photo is not None and args.offset is not None:
        # Translators: {option} and {other} are command-line options
        parser.error(_("{option} cannot be used together with {other}").format(
            option="--offset", other="--clock-photo"))

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
    clock = None
    if args.clock_photo is not None:
        try:
            clock = measure(args.clock_photo, args.clock_time, manual_tz)
        except ClockError as e:
            sys.exit(printable(str(e)))
    correction = clock.seconds if clock is not None else (args.offset or 0.0)

    named, found = find_tracks(args.gpx, args.recursive)
    if not named and not found:
        sys.exit(_("No GPX files found."))
    tracks = []
    if named:
        try:
            track = load_track(named, stops=not args.no_stops)
        except ValueError as e:
            sys.exit(str(e))
        if track is not None:
            tracks.append(track)
        elif not found:
            sys.exit(no_points(len(named)))
    if not found:
        # Without a directory of tracks, the track is known before the photos
        labels = file_labels(named)
        print(track_line(tracks[0], labels, by_name=False))
        for line in correction_lines(clock, correction):
            print(line)
    # Only the found files that some photo needs are read in full
    spans = {path: quick_span(path) for path in found}

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
    unreadable = []
    if found:
        # Only when the scans are sure that no file has a time
        if not tracks and all(span == () for span in spans.values()):
            sys.exit(no_points(len(named) + len(found)))
        times = corrected_times(photos, correction, args.overwrite)
        for path in tracks_needed(spans, times, args.max_gap):
            try:
                track = load_track([path], named=False, stops=not args.no_stops)
            except ValueError as e:
                if spans[path] is None:
                    sys.exit(str(e))        # which photos it would cover is not known
                # Only the photos within its time are left without a location
                print(str(e), file=sys.stderr)
                unreadable.append((path, spans[path]))
                continue
            if track is not None:
                tracks.append(track)
        labels = file_labels(named + found)
    results = match_photos(photos, tracks, correction, args.max_gap, overwrite=args.overwrite,
                           label=labels.get, unreadable=unreadable)
    if found:
        results = with_nearest_tracks(results, tracks, spans, not args.no_stops)
    if found:
        used = covering(tracks, results, args.max_gap)
        total = len(named) + len(found)
        # Translators: {used} is how many of the {total} GPX files given or
        # found have a track at the time of some photo
        print(ngettext("Tracks covering the photos: {used} of {total} GPX file",
                       "Tracks covering the photos: {used} of {total} GPX files", total).format(
            used=i18n.number(sum(len(track.files) for track in used)),
            total=i18n.number(total)))
        for track in used:
            print(track_line(track, labels, by_name=True))
        for line in correction_lines(clock, correction):
            print(line)
    # With several track files, each photo line names its own
    if found or len(named) > 1:
        width = max((len(", ".join(labels[path] for path in result.files))
                     for result in results if result.reason is None), default=0)
        for result in results:
            print(photo_line(result, labels, width))
    else:
        for result in results:
            print(photo_line(result))
    summary = summarize(results)
    plan = [result for result in results if result.reason is None]

    print(_("Matched: {matched}, skipped: {skipped}").format(
        matched=i18n.number(summary.matched), skipped=i18n.number(summary.skipped)))
    if any(track.stops for track in tracks) and summary.matched:
        # Translators: how many of the matched photos were taken during stops;
        # a wrong camera clock puts fewer of them there
        print(ngettext("During stops: {count} of {matched} matched photo",
                       "During stops: {count} of {matched} matched photos",
                       summary.matched).format(count=i18n.number(summary.at_stops),
                                               matched=i18n.number(summary.matched)))
    for line in time_check_lines(results):
        print(line)
    if not args.write:
        shots, names = shots_of(results)
        found = suspicion(results, tracks, shots, args)
        for line in warning_lines(found, shots, names, correction, clock):
            print(line)
        if plan:
            # Translators: {option} is the command-line option --write
            print(_("This was a preview; no files were changed. "
                    "Use {option} to write the locations.").format(option="--write"))
        if unreadable:
            sys.exit(1)
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
    if errors or unreadable:
        sys.exit(1)


if __name__ == "__main__":
    main()
