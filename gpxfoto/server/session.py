"""What the page works on: the photos, the tracks, the clock correction and
the match of the photos on the tracks.

Everything that reads files runs in background threads, and the page hears
of the results through events, as they come. The state is guarded by one
lock, held only for short moments. Choosing new photos or tracks starts a
new generation: work for an older one stops and its results are dropped.
"""
import base64
import binascii
import os
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from gettext import gettext as _, ngettext

from gpxfoto.engine.matching import corrected_times, match_photos, summarize
from gpxfoto.engine.photos import (
    exif_thumbnail, find_photos, photo_from_metadata, read_metadata)
from gpxfoto.engine.track import find_tracks, load_track, quick_span, tracks_needed
from gpxfoto.server import files
from gpxfoto.server.geometry import track_lines

# Photos are read in batches of this many, so that the list fills as it goes
PHOTO_BATCH = 100
# Thumbnails kept in memory
THUMBNAIL_CACHE = 500
MAX_GAP = 120.0          # s, as the default of --max-gap
# While photos are read, matches come at most this often, in seconds
LOADING_MATCHES = 1.0
# How far the clock correction may go, in seconds: a day either way
MAX_CORRECTION = 86400.0


class SessionError(Exception):
    """A choice that cannot be used; the text is for the page."""


def _time_text(moment):
    return None if moment is None else moment.isoformat(timespec="milliseconds")


class Session:
    """The state of one page. events receives what the page must hear of."""

    def __init__(self, events):
        self.events = events
        self.lock = threading.Lock()
        self.photo_generation = 0
        self.photo_folder = None
        self.photos = []                 # Photo
        self.looks = []                  # (EXIF Orientation or None, has a thumbnail) of each photo
        self.photos_loading = False
        self.track_generation = 0
        self.track_choice = None         # {"files": [...]} or {"folder": ..., "recursive": ...}
        self.named = []                  # Track of the files chosen together
        self.spans = {}                  # quick spans of the files of a chosen folder
        self.loaded = OrderedDict()      # path -> Track or None, of those files
        self.unreadable = []             # (path, span) of those that cannot be read
        self.tracks_loading = False
        self.summaries = {}              # id(Track) -> what the page gets of it
        self._track_number = 0
        self.correction = 0.0
        self.overwrite = False
        self.stops = True
        self.match = None                # what was last sent as "matches"
        self._thumbnails = OrderedDict()
        self._upload_dir = None
        self._wanted = False
        self._closed = False
        self._wake = threading.Condition(self.lock)
        self._matcher = None

    # --- choices of the page --------------------------------------------

    def choose_photos(self, folder, recursive=False):
        """Start reading the photos in folder; the old ones are dropped."""
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            raise SessionError(_("Not a folder: {path}").format(path=folder))
        with self.lock:
            self.photo_generation += 1
            generation = self.photo_generation
            self.photo_folder = folder
            self.photos, self.looks = [], []
            self.photos_loading = True
            self._thumbnails.clear()
            self.match = None
        files.remember("photos", folder)
        self.events.publish("photos-reset", {"generation": generation, "folder": folder,
                                             "recursive": recursive})
        threading.Thread(target=self._load_photos, args=(generation, folder, recursive),
                         daemon=True).start()
        return generation

    def choose_track_files(self, paths):
        """Start reading the GPX files at paths, which form one track together."""
        paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
        if not paths:
            raise SessionError(_("No GPX files found."))
        for path in paths:
            if not os.path.isfile(path):
                raise SessionError(_("No such file or directory: {path}").format(path=path))
        for path in paths:
            if not path.startswith(self._upload_dir or "\0"):
                files.remember("tracks", path)
        return self._choose_tracks({"files": paths})

    def choose_track_folder(self, folder, recursive=False):
        """Use the GPX files in folder, each a track of its own."""
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            raise SessionError(_("Not a folder: {path}").format(path=folder))
        files.remember("tracks", folder)
        return self._choose_tracks({"folder": folder, "recursive": recursive})

    def drop_tracks(self, dropped):
        """Use GPX files that were dropped on the page, as [(name, base64 of the file)]."""
        with self.lock:
            # Copies of the user's tracks are kept no longer than needed
            if self._upload_dir is not None:
                shutil.rmtree(self._upload_dir, ignore_errors=True)
            self._upload_dir = tempfile.mkdtemp(prefix="gpxfoto-tracks-")
            directory = self._upload_dir
        paths = []
        for name, encoded in dropped:
            try:
                data = base64.b64decode(str(encoded), validate=True)
            except (binascii.Error, ValueError):
                raise SessionError(_("Cannot read the GPX file {path}: {error}").format(
                    path=name, error="base64")) from None
            name = re.sub(r"[^\w.\- ]", "_", os.path.basename(str(name))).strip(". ") or "track"
            if not name.lower().endswith(".gpx"):
                name += ".gpx"
            path = os.path.join(directory, name)
            stem, extension = os.path.splitext(path)
            number = 1
            while os.path.exists(path):
                number += 1
                path = f"{stem} ({number}){extension}"
            with open(path, "xb") as f:
                f.write(data)
            paths.append(path)
        return self.choose_track_files(paths)

    def _choose_tracks(self, choice):
        with self.lock:
            self.track_generation += 1
            generation = self.track_generation
            self.track_choice = choice
            self.named, self.spans, self.unreadable = [], {}, []
            self.loaded = OrderedDict()
            self.summaries = {}
            self.tracks_loading = True
            stops = self.stops
            # Until the new tracks are read, no photo is placed; a choice
            # that cannot be read leaves them so
            self.match = None
            self._want_match()
        self.events.publish("tracks-reset", {"generation": generation, "choice": choice})
        threading.Thread(target=self._load_tracks, args=(generation, choice, stops),
                         daemon=True).start()
        return generation

    def set_correction(self, seconds):
        seconds = float(seconds)
        if not abs(seconds) <= MAX_CORRECTION:          # also false for NaN
            raise SessionError(_("not a valid number of seconds: {value}").format(value=seconds))
        with self.lock:
            self.correction = seconds
            self._want_match()

    def set_options(self, overwrite=None, stops=None):
        reload = None
        with self.lock:
            if overwrite is not None:
                self.overwrite = bool(overwrite)
            if stops is not None and bool(stops) != self.stops:
                self.stops = bool(stops)
                reload = self.track_choice
            self._want_match()
        if reload is not None:
            self._choose_tracks(reload)

    def close(self):
        with self.lock:
            self._closed = True
            self.photo_generation += 1
            self.track_generation += 1
            self._wake.notify_all()
            directory = self._upload_dir
        if directory:
            shutil.rmtree(directory, ignore_errors=True)

    # --- what the page asks for -------------------------------------------

    def state(self):
        """Everything the page shows, for a page that just connected."""
        with self.lock:
            return {
                "photos": {"generation": self.photo_generation, "folder": self.photo_folder,
                           "loading": self.photos_loading,
                           "photos": [self._photo_summary(i, p, self.looks[i])
                                      for i, p in enumerate(self.photos)]},
                "tracks": {"generation": self.track_generation, "choice": self.track_choice,
                           "loading": self.tracks_loading,
                           "tracks": [self.summaries[id(t)] for t in self._tracks()]},
                "correction": self.correction, "overwrite": self.overwrite,
                "stops": self.stops, "matches": self.match,
            }

    def thumbnail(self, generation, index):
        """The EXIF thumbnail of a photo as JPEG bytes, or None."""
        with self.lock:
            if generation != self.photo_generation or not 0 <= index < len(self.photos):
                return None
            path = self.photos[index].path
            if path in self._thumbnails:
                self._thumbnails.move_to_end(path)
                return self._thumbnails[path]
        data = exif_thumbnail(path)
        with self.lock:
            self._thumbnails[path] = data
            while len(self._thumbnails) > THUMBNAIL_CACHE:
                self._thumbnails.popitem(last=False)
        return data

    # --- background work --------------------------------------------------

    def _load_photos(self, generation, folder, recursive):
        try:
            found = find_photos([folder], recursive)
        except (FileNotFoundError, OSError) as e:
            self._photos_failed(generation, str(e))
            return
        self.events.publish("photos-found", {"generation": generation, "total": len(found)})
        for start in range(0, len(found), PHOTO_BATCH):
            batch = found[start:start + PHOTO_BATCH]
            try:
                metadata = read_metadata(batch)
            except (RuntimeError, OSError) as e:
                # These photos are shown as unreadable; the others are read
                self.events.publish("failure", {"message": str(e)})
                metadata = [{"SourceFile": path, "Error": str(e)} for path in batch]
            new = [photo_from_metadata(meta, None) for meta in metadata]
            looks = [(meta.get("Orientation"), bool(meta.get("ThumbnailLength")))
                     for meta in metadata]
            with self.lock:
                if generation != self.photo_generation:
                    return
                first = len(self.photos)
                self.photos += new
                self.looks += looks
                self._want_match()
            self.events.publish("photos", {
                "generation": generation, "done": first + len(new), "total": len(found),
                "photos": [self._photo_summary(first + k, p, look)
                           for k, (p, look) in enumerate(zip(new, looks))]})
        with self.lock:
            if generation != self.photo_generation:
                return
            self.photos_loading = False
        self.events.publish("photos-done", {"generation": generation, "count": len(found)})

    def _photos_failed(self, generation, message):
        with self.lock:
            if generation != self.photo_generation:
                return
            self.photos_loading = False
        self.events.publish("failure", {"message": message})
        self.events.publish("photos-done", {"generation": generation, "count": None})

    def _load_tracks(self, generation, choice, stops):
        if "files" in choice:
            try:
                track = load_track(choice["files"], stops=stops)
            except ValueError as e:
                self._tracks_failed(generation, str(e))
                return
            if track is None:
                self._tracks_failed(generation, ngettext(
                    "The GPX file contains no track points with timestamps.",
                    "The GPX files contain no track points with timestamps.",
                    len(choice["files"])))
                return
            summary = self._track_summary(track)
            with self.lock:
                if generation != self.track_generation:
                    return
                self.named = [track]
                self.summaries[id(track)] = summary
                self._want_match()
            self.events.publish("track", {"generation": generation, "track": summary})
        else:
            _named, found = find_tracks([choice["folder"]], choice["recursive"])
            if not found:
                self._tracks_failed(generation, _("No GPX files found."))
                return
            spans = {path: quick_span(path) for path in found}
            with self.lock:
                if generation != self.track_generation:
                    return
                self.spans = spans
                self._want_match()
            self.events.publish("tracks-found", {"generation": generation, "total": len(found)})
        with self.lock:
            if generation != self.track_generation:
                return
            self.tracks_loading = False
        self.events.publish("tracks-done", {"generation": generation})

    def _tracks_failed(self, generation, message):
        with self.lock:
            if generation != self.track_generation:
                return
            self.tracks_loading = False
        self.events.publish("failure", {"message": message})
        self.events.publish("tracks-done", {"generation": generation})

    def _want_match(self):
        """Ask for the photos to be matched again; the lock is held."""
        self._wanted = True
        if self._matcher is None and not self._closed:
            self._matcher = threading.Thread(target=self._match_loop, daemon=True)
            self._matcher.start()
        self._wake.notify_all()

    def _match_loop(self):
        last = 0.0
        while True:
            with self.lock:
                while not self._wanted and not self._closed:
                    self._wake.wait()
                # While photos are read, a match of all of them after each
                # batch would grow with their square: one a second is enough
                while (self.photos_loading and not self._closed
                       and time.monotonic() - last < LOADING_MATCHES):
                    self._wake.wait(LOADING_MATCHES - (time.monotonic() - last))
                if self._closed:
                    return
                self._wanted = False
            last = time.monotonic()
            try:
                self._match_once()
            except Exception as e:      # the page must hear of it; the loop goes on
                self.events.publish("failure", {"message": str(e)})

    def _match_once(self):
        with self.lock:
            photo_generation, track_generation = self.photo_generation, self.track_generation
            photos = list(self.photos)
            correction, overwrite, stops = self.correction, self.overwrite, self.stops
            spans, loaded = dict(self.spans), dict(self.loaded)
        # Of a folder of tracks, the files the photos need are read now
        for path in tracks_needed(spans, corrected_times(photos, correction, overwrite), MAX_GAP):
            if path in loaded:
                continue
            try:
                track = load_track([path], named=False, stops=stops)
                failure = None
            except ValueError as e:
                track, failure = None, str(e)
            summary = None if track is None else self._track_summary(track)
            with self.lock:
                if track_generation != self.track_generation:
                    return
                self.loaded[path] = loaded[path] = track
                if summary is not None:
                    self.summaries[id(track)] = summary
                if failure is not None and spans[path] is not None:
                    self.unreadable.append((path, spans[path]))
            if failure is not None:
                self.events.publish("failure", {"message": failure})
            if summary is not None:
                self.events.publish("track", {"generation": track_generation, "track": summary})
        with self.lock:
            if track_generation != self.track_generation:
                return
            tracks = self._tracks()
            unreadable = list(self.unreadable)
        results = match_photos(photos, tracks, correction, MAX_GAP, overwrite=overwrite,
                               label=os.path.basename, unreadable=unreadable)
        summary = summarize(results)
        match = {
            "generation": photo_generation, "tracks": track_generation,
            "correction": correction, "overwrite": overwrite,
            "results": [self._result(i, r, overwrite) for i, r in enumerate(results)],
            "matched": summary.matched, "skipped": summary.skipped,
            "at_stops": summary.at_stops,
        }
        with self.lock:
            # Matches during a drag of the slider are shown as they come, but
            # not those of photos or tracks that were replaced
            if (photo_generation != self.photo_generation
                    or track_generation != self.track_generation):
                return
            self.match = match
        self.events.publish("matches", match)

    # --- what the page gets ------------------------------------------------

    def _tracks(self):
        return self.named + [t for t in self.loaded.values() if t is not None]

    def _track_summary(self, track):
        """What the page gets of a track; slow for a long one, so the lock is not held."""
        with self.lock:
            self._track_number += 1
            number = self._track_number
        return {"id": number, "files": [os.path.basename(p) for p in track.files],
                "points": len(track.points), "first": track.first, "last": track.last,
                "lines": track_lines(track.points),
                "stops": [[round(s.lon, 6), round(s.lat, 6)] for s in track.stops]}

    @staticmethod
    def _photo_summary(index, photo, look):
        orientation, thumbnail = look
        return {"id": index, "name": os.path.basename(photo.path),
                "taken": _time_text(photo.taken), "tz": photo.tz_source,
                "reason": photo.reason, "has_location": photo.has_location,
                "orientation": orientation if orientation in range(1, 9) else 1,
                "thumbnail": thumbnail}

    @staticmethod
    def _result(index, result, overwrite):
        entry = {"id": index, "time": _time_text(result.time), "reason": result.reason,
                 "files": [os.path.basename(p) for p in result.files]}
        if result.reason is None:
            entry.update(state="stop" if result.stop is not None else "matched",
                         lat=round(result.lat, 7), lon=round(result.lon, 7),
                         ele=None if result.ele is None else round(result.ele, 1),
                         overwrites=result.photo.has_location)
            if result.stop is not None:
                entry["stop"] = [result.stop.start, result.stop.end]
        elif result.photo.has_location and not overwrite:
            entry["state"] = "has_location"
        else:
            entry["state"] = "skipped"
        return entry
