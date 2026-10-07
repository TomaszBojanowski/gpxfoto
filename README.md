<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.tomaszbojanowski.Gpxfoto.svg" width="128" alt="gpxfoto icon">
</p>

# gpxfoto

[Polski](README.pl.md)

gpxfoto adds locations to photos from a GPX track recorded with a watch or
a phone. It writes nothing but GPS metadata and never changes the image.

- **The image stays the same.** Only metadata is written, with exiftool;
  the image is never re-encoded.
- **Every write is checked.** Before and after writing, gpxfoto computes a
  SHA-256 checksum of everything except the metadata segments. If the
  checksums differ, the change is rejected and the original file is left
  untouched.
- **Writes are atomic.** The result goes to a temporary file in the same
  directory and replaces the original only after the check. File
  permissions and the modification time are kept.
- **Preview by default.** Nothing is changed without `--write`.
- **No guessing.** A photo without a reliable track point gets no location,
  and the reason is shown.

## Requirements

- Python 3.11 or newer
- [ExifTool](https://exiftool.org/) (on Fedora: `sudo dnf install perl-Image-ExifTool`)
- for installing: `msgfmt` from GNU gettext (on Fedora: `sudo dnf install gettext`)

## Installation

```
pip install --user .
```

## Usage

```
gpxfoto PHOTO… -g TRACK [-g TRACK …] [options]
```

Show what would be written:

```
gpxfoto ~/Pictures/2026-10-06 -g activity.gpx
```

Write the locations:

```
gpxfoto ~/Pictures/2026-10-06 -g activity.gpx --write
```

| Option | Meaning |
|---|---|
| `-g`, `--gpx TRACK` | GPX file with the track, or a directory with GPX files; can be given more than once |
| `--write` | write the locations; without it only a preview is shown |
| `--offset SECONDS` | camera clock correction, added to the capture time |
| `--timezone +HH:MM` | camera time zone for all photos (default: read from each photo’s EXIF data) |
| `--clock-photo FILE` | photo of an accurate clock, such as the watch that records the track; with `--clock-time`, it gives the camera clock correction |
| `--clock-time TIME` | time shown on the clock in that photo, 24-hour: `14:03:27`, `14:03:27+02:00` or `2026-10-06T14:03:27+02:00` |
| `--max-gap SECONDS` | largest allowed time between a photo and the nearest track point (default: 120 s) |
| `--no-stops` | do not look for stops; every photo gets the track’s position at its time |
| `--overwrite` | also change photos that already have a location |
| `--travel-direction` | also add the direction of travel from the track (EXIF `GPSTrack`); see below |
| `--backup` | keep copies of the original files in an `originals` subdirectory next to each photo; an existing copy is never replaced |
| `-r`, `--recursive` | also look for photos and tracks in subdirectories |

The time zone of a photo is read from `OffsetTimeOriginal` (or
`OffsetTime`), which cameras such as the Panasonic LUMIX S5II record.
`--timezone` overrides it for all photos. Without either, the computer’s
time zone is used, and the list of photos says so.

A photo is skipped when its capture time is more than `--max-gap` away from
the nearest track point: before the track starts, after it ends, or in a
break in recording. A photo taken during such a break still gets a location
if the recorded position moved less than 100 m during the break.

With `--backup`, a copy of each photo as it was before goes into an
`originals` directory next to it. gpxfoto marks that directory with a
`.gpxfoto` file, never changes the copies in it, and leaves it out when
searching with `-r`. An existing copy must hold the same image as the
photo; if something else is there, the photo is not written.

The program is in English with a Polish translation; the language follows
the system settings.

### Correcting the camera clock

A camera clock drifts, while the times of the track come from GPS. To find
out how far off the camera is, take a photo of the watch, or another
accurate clock, with the camera, read the time on it, and give both:

```
gpxfoto ~/Pictures/2026-10-06 -g activity.gpx --clock-photo ~/Pictures/2026-10-06/P1000123.JPG --clock-time 14:03:27
```

gpxfoto subtracts the capture time of that photo from the time on the
clock and adds the difference to the capture time of every photo, as
`--offset` does; `--offset` cannot be given as well. The preview shows the
correction, the `--offset` value it equals, and both readings.

A difference of 30 minutes or more may mean that the clock and the camera
show the time of different time zones, for example when the camera was not
switched to summer time. gpxfoto cannot tell, so it then asks for the
clock’s UTC offset, such as `14:03:27+02:00`. A difference of more than two
hours also needs the date shown on the clock, such as
`2026-10-06T14:03:27+02:00`. A time given without seconds stands for the
middle of the minute.

Use a clock photo from the same days as the other photos: a camera that
does not switch to summer time by itself is off by a different amount after
the change.

### Checking the time zone of S5II photos

The Panasonic LUMIX S5II also records the capture time in UTC, in its maker
note (`Panasonic:TimeStamp`). For photos from this camera, gpxfoto compares
that time with the capture time it uses, before any clock correction; a
difference that the correction makes up is not reported, and photos that
already have a location and are not overwritten are not checked. If
they are more than two minutes apart, the photo’s line gets a note, and a
warning after the list gives the likely cause: a wrong `--timezone`, the
computer’s time zone used for photos without one in EXIF, or a capture time
that another program changed in EXIF. Where the difference fits a time zone
in use, the warning gives the `--timezone` value that would make both times
match, unless that would break photos whose times already match. The check never changes a time or a location. It cannot notice a
wrong time zone setting in the camera itself, because the camera works out
both times from that setting.

### Several tracks

GPX files given with `-g` form one track together, and the track line
names the file when there is only one. With several files, each photo’s
line names the file its position comes from.

`-g` can also be a directory, such as one where all activities are kept.
Every `.gpx` file in it, and with `-r` in its subdirectories, is a track of
its own, and each photo gets the track that covers its time; a position is
never made up from two different files. If several tracks cover a photo,
the one with recorded points closest to its time wins, then a file given by
name, then the one that records more often. If two tracks from the
directory with points equally close to a photo’s time put it more than 200
m apart, it is skipped. A file in the directory that cannot be read is
skipped with a message, and the other tracks are used as if it were not
there; a photo that only that file would cover is skipped, and gpxfoto
exits with status 1 at the end. A file given by name that cannot be read
stops the run. A photo that no track covers is shown with the nearest track
within a day. To stay quick with many files, gpxfoto first scans each file
for its times and reads in full only the files the photos need.

### Stops

A photo taken while standing still gets a steadier position. gpxfoto
finds the stops of a track: stretches of about half a minute or more in
which the track moves slower than 0.2 m/s and, when the track has
barometric elevations, climbs or descends slower than 0.03 m/s, so that
slow, steep climbing does not count. A break in recording whose two ends
are within 10 m is a stop too. A photo taken during a stop, while the track
is within 20 m of it, and within 5 m of height when the elevations are
barometric, gets the stop’s position, the median of its points, and its
line shows the times of the stop. The
positions of other photos do not change. Pauses shorter than about a
minute are usually not stops.

After the list, “During stops: N of M matched photos” says how many photos
were taken during stops. Photos are mostly taken while standing, so with a
wrong camera clock this number is usually lower. `--no-stops` turns stops
off.

### Warnings

In the preview, gpxfoto points out signs that the camera clock is off. It
never changes anything by itself.

- **A shift of whole hours.** If, with the photo times moved by half an
  hour or by whole hours (up to 12), clearly more photos fall during stops,
  at four or more different stops, gpxfoto proposes that shift with the
  option that applies it: `--offset`; `--timezone` as well when no
  correction was given, all photos have the same time zone and none comes
  from a camera that records UTC, such as the S5II; or `--clock-time` with
  another UTC offset when the correction comes from a clock photo. A difference of exactly one hour usually means a camera not
  switched to or from summer time.
- **Photos in motion.** If most photos fall where the track moves at its
  full pace, not at stops or where it slows down, the camera clock may be
  off by a few minutes. Someone who takes most photos while walking on can
  get this warning with a right clock.
- **Implausible jumps.** Photos taken less than a minute apart but placed
  farther apart than anything could travel in that time (100 m/s, more on
  a faster track) usually have different time zones in EXIF, or come from
  GPX files of different trips recorded at the same time.

The first two need the stops and speeds of one track: they are left out
when the matched photos come from several tracks, when photos are skipped
for another track, and with `--no-stops`. When no track of a directory
covers the photos, they look at the nearest one. The thresholds were chosen
on a real hike; with the right clock, they gave no warning for any of 900
modelled sets of photos taken mostly at stops and short pauses.

### Direction of travel

With `--travel-direction`, a matched photo also gets the direction in which
the track passed its place, in whole degrees from true north, written as
EXIF `GPSTrack` with `GPSTrackRef` T. It is the direction of travel, not
the direction the camera faced, which gpxfoto cannot know, so
`GPSImgDirection` is never written. A photo gets a direction only where the
track clearly passes through its place: within a minute before and after
the photo the track gets 20 m away, and the way between those two points is
at most 20% longer than a straight line. Photos taken at a stop, at a sharp
turn, on switchbacks or in a break in recording get none, nor do photos
between the points of two files recorded at the same time, and the preview
says why. With the option,
an older direction of travel in a written photo, also in XMP, is removed,
so that it cannot pass for one from the track. On the author’s hike, 84% of
the photos taken while walking got a direction.

## Development

```
pip install -e .[test]
pytest
```

Your own photos and tracks can be put into `tests/prywatne/`. Git ignores
that directory; `tests/test_private.py` uses the files when they are there.

### Translations

Messages are written in English and translated with gettext; the Polish
translation is in `po/pl.po`. After changing messages, update the template
and the translations:

```
xgettext --files-from=po/POTFILES.in --from-code=UTF-8 --language=Python \
    --keyword=N_ --add-comments=Translators: --package-name=gpxfoto \
    --package-version=0.1.0 \
    --msgid-bugs-address=https://github.com/tomaszbojanowski/gpxfoto/issues \
    --output=po/gpxfoto.pot
msgmerge --update --backup=none po/pl.po po/gpxfoto.pot
```

An editable install compiles the translations once. To see changes to
`po/pl.po` without reinstalling, compile it again:

```
msgfmt --check -o gpxfoto/locale/pl/LC_MESSAGES/gpxfoto.mo po/pl.po
```

## License

gpxfoto is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option)
any later version. See [LICENSE](LICENSE).
