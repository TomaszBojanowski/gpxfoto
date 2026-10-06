# gpxfoto

[Polski](README.pl.md)

gpxfoto adds locations to photos from a GPX track recorded with a watch or
a phone. It writes nothing but GPS metadata and never changes the image.

- **The image stays the same.** Only metadata is written, with exiftool;
  the image is never re-encoded.
- **Every write is checked.** Before and after writing, gpxfoto computes a
  SHA-256 of everything except the metadata segments. If they differ, the
  change is rejected and the original file is left untouched.
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
gpxfoto PHOTOS... -g TRACK.gpx [-g TRACK.gpx ...] [options]
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
| `--timezone +HH:MM` | camera time zone; read from the photo's EXIF data by default |
| `--max-gap SECONDS` | largest allowed time between a photo and the nearest track point (default: 120 s) |
| `--overwrite` | also change photos that already have a location |
| `--backup` | keep copies of the original files in the `originals` subdirectory |
| `-r`, `--recursive` | also look for photos in subdirectories |

The time zone of a photo is read from `OffsetTimeOriginal`, which cameras
such as the Panasonic LUMIX S5II record. Without it, `--timezone` or the
computer's time zone is used, and the preview says so.

A photo is skipped when it lies further than `--max-gap` from the track, or
in a break in recording longer than that, unless the position hardly
changed during the break (less than 100 m).

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
