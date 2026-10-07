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
gpxfoto PHOTO… -g FILE [-g FILE …] [options]
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
| `-g`, `--gpx FILE` | GPX file with the track; can be given more than once |
| `--write` | write the locations; without it only a preview is shown |
| `--offset SECONDS` | camera clock correction, added to the capture time |
| `--timezone +HH:MM` | camera time zone for all photos (default: read from each photo’s EXIF data) |
| `--clock-photo FILE` | photo of an accurate clock, such as the watch that records the track; with `--clock-time`, it gives the camera clock correction |
| `--clock-time TIME` | time shown on the clock in that photo, 24-hour: `14:03:27`, `14:03:27+02:00` or `2026-10-06T14:03:27+02:00` |
| `--max-gap SECONDS` | largest allowed time between a photo and the nearest track point (default: 120 s) |
| `--overwrite` | also change photos that already have a location |
| `--backup` | keep copies of the original files in an `originals` subdirectory next to each photo; an existing copy is never replaced |
| `-r`, `--recursive` | also look for photos in subdirectories |

The time zone of a photo is read from `OffsetTimeOriginal` (or
`OffsetTime`), which cameras such as the Panasonic LUMIX S5II record.
`--timezone` overrides it for all photos. Without either, the computer’s
time zone is used, and the list of photos says so.

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
that time with the capture time it uses, before any clock correction. If
they are more than two minutes apart, the photo’s line gets a note, and a
warning after the list gives the likely cause: a wrong `--timezone`, the
computer’s time zone used for photos without one in EXIF, or a capture time
that another program changed in EXIF. Where the difference fits a time zone
in use, the warning gives the `--timezone` value that would make both times
match. The check never changes a time or a location. It cannot notice a
wrong time zone setting in the camera itself, because the camera works out
both times from that setting.

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
