"""Folders for the page to choose from, and the folders chosen recently.

The page never sees the disk itself: it asks for the content of a folder
by its path and gets the names of its subfolders and track files, without
hidden ones. Reading photos and tracks happens only once a folder or file
is chosen.
"""
import json
import os
import re
import sys
import tempfile
from gettext import gettext as _

from gpxfoto.engine.photos import EXTENSIONS as PHOTO_EXTENSIONS
from gpxfoto.engine.track import GPX_EXTENSIONS

# A folder's photos and tracks are counted up to this many entries, so
# that a huge folder does not hold up the list
COUNT_LIMIT = 5000
# This many recently chosen folders are kept of each kind
RECENT = 8
RECENT_KINDS = ("photos", "tracks")
# The choices of the page kept between runs, each with its values, the
# first of them the default
PREFERENCES = {"map_style": ("light", "dark")}


class FolderError(Exception):
    """A folder that cannot be listed; the text is for the page."""


def listing(path):
    """What the page shows of the folder at path, "" for the home folder.

    Returns {"path", "parent", "folders", "tracks", "photos"}: folders are
    {"name", "photos", "tracks"} with the counts in each (None when it
    cannot be read), tracks the names of the GPX files, and photos the
    number of photos in the folder itself.
    """
    path = os.path.abspath(os.path.expanduser(path or "~"))
    try:
        entries = list(os.scandir(path))
    except FileNotFoundError:
        raise FolderError(_("No such folder: {path}").format(path=path)) from None
    except NotADirectoryError:
        raise FolderError(_("Not a folder: {path}").format(path=path)) from None
    except OSError as e:
        raise FolderError(_("Cannot open the folder {path}: {error}").format(
            path=path, error=e.strerror or e)) from None
    folders, tracks, photos = [], [], 0
    for entry in entries:
        if entry.name.startswith("."):
            continue
        extension = os.path.splitext(entry.name)[1].lower()
        try:
            if entry.is_dir():
                counts = _counts(entry.path)
                folders.append({"name": entry.name, "photos": counts[0], "tracks": counts[1]})
            elif entry.is_file():
                if extension in GPX_EXTENSIONS:
                    tracks.append(entry.name)
                elif extension in PHOTO_EXTENSIONS:
                    photos += 1
        except OSError:
            continue
    folders.sort(key=lambda folder: folder["name"].casefold())
    tracks.sort(key=str.casefold)
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None,
            "folders": folders, "tracks": tracks, "photos": photos}


def _counts(path):
    """(photos, tracks) directly in the folder at path, or (None, None)."""
    photos = tracks = 0
    try:
        with os.scandir(path) as entries:
            for k, entry in enumerate(entries):
                if k >= COUNT_LIMIT:
                    break
                if entry.name.startswith("."):
                    continue
                extension = os.path.splitext(entry.name)[1].lower()
                if extension in PHOTO_EXTENSIONS:
                    photos += 1
                elif extension in GPX_EXTENSIONS:
                    tracks += 1
    except OSError:
        return None, None
    return photos, tracks


def places():
    """Folders to start from: the home folder, Pictures, Desktop and
    removable media, as {"name", "path"}, without those that do not exist."""
    home = os.path.expanduser("~")
    found = [{"name": _("Home folder"), "path": home}]
    # Without user-dirs.dirs, as on macOS, the folders have English names
    # on disk, which the system shows translated
    for key, fallback, name in (("PICTURES", "Pictures", _("Pictures")),
                                ("DESKTOP", "Desktop", _("Desktop"))):
        path = _user_dir(key)
        if path is None:
            path = os.path.join(home, fallback)
        else:
            name = os.path.basename(path)
        if os.path.isdir(path) and path != home:
            found.append({"name": name, "path": path})
    user = os.path.basename(home)
    for media in ("/Volumes", f"/run/media/{user}", f"/media/{user}"):
        try:
            names = sorted(os.listdir(media), key=str.casefold)
        except OSError:
            continue
        for name in names:
            path = os.path.join(media, name)
            if not name.startswith(".") and os.path.isdir(path) and os.path.realpath(path) != "/":
                found.append({"name": name, "path": path})
    return found


def _user_dir(key):
    """A folder from the user-dirs.dirs of freedesktop.org, such as Obrazy."""
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        with open(os.path.join(config, "user-dirs.dirs"), encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(rf'^XDG_{key}_DIR="([^"]*)"', text, re.MULTILINE)
    if not match:
        return None
    return match[1].replace("$HOME", os.path.expanduser("~"))


def config_dir():
    """Where gpxfoto keeps its settings."""
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/gpxfoto")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "gpxfoto")


def _load(name):
    """The JSON object stored in the settings file name, or {}."""
    try:
        with open(os.path.join(config_dir(), name), encoding="utf-8") as f:
            stored = json.load(f)
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _save(name, data):
    """Replace the settings file name; a failure to save is not an error."""
    directory = config_dir()
    temp = None
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory,
                                         prefix=".gpxfoto-", delete=False) as f:
            temp = f.name
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(temp, os.path.join(directory, name))
    except OSError:
        if temp is not None:
            try:
                os.unlink(temp)
            except OSError:
                pass


def recent():
    """The folders and files chosen recently, by kind, newest first, of
    those that still exist."""
    stored = _load("recent.json")
    result = {}
    for kind in RECENT_KINDS:
        paths = stored.get(kind)
        paths = [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []
        result[kind] = [p for p in paths if os.path.exists(p)][:RECENT]
    return result


def remember(kind, path):
    """Note path as chosen just now. Folders that are missing now, such as
    those on a card that is out, are kept for when they are back."""
    stored = _load("recent.json")
    lists = {}
    for name in RECENT_KINDS:
        paths = stored.get(name)
        lists[name] = [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []
    lists[kind] = ([path] + [p for p in lists[kind] if p != path])[:RECENT]
    _save("recent.json", lists)


def preferences():
    """The choices of the page that are kept between runs."""
    stored = _load("preferences.json")
    return {name: stored[name] if stored.get(name) in values else values[0]
            for name, values in PREFERENCES.items()}


def set_preference(name, value):
    """Keep a choice of the page; ValueError for one that is not known."""
    if value not in PREFERENCES.get(name, ()):
        raise ValueError(f"{name}: {value!r} is not known")
    chosen = preferences()
    chosen[name] = value
    _save("preferences.json", chosen)
