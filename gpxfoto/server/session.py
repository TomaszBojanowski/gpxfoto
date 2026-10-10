"""What the page works on: the photos, the tracks, the clock correction and
the match of the photos on the tracks.

Everything that reads files runs in background threads, and the page hears
of the results through events, as they come. The state is guarded by one
lock, held only for short moments. Choosing new photos or tracks starts a
new generation: work for an older one stops and its results are dropped.
While the photos are written, nothing can be chosen anew.
"""
import base64
import binascii
import math
import os
import re
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from gettext import gettext as _, ngettext

from gpxfoto.engine.journal import Journal
from gpxfoto.engine.matching import corrected_times, match_photos, placed_by_hand, summarize
from gpxfoto.engine.photos import (
    exif_thumbnail, find_photos, photo_from_metadata, read_metadata)
from gpxfoto.engine.track import find_tracks, load_track, quick_span, tracks_needed
from gpxfoto.server import files
from gpxfoto.server.geometry import track_lines
from gpxfoto.server.warnings import warnings_of
from gpxfoto.server.writing import Job, UndoJob, Writing

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
        self.seen = []                   # os.stat() of each photo before it was read, or None
        self.placed = {}                 # index -> (lat, lon) of the photos placed by hand
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
        self.results = []                # its PhotoResult of each photo
        self.warnings = None             # what was last sent as "warnings"
        self._match_number = 0
        self.writing = None              # Writing, while the photos are written
        self.journals = []               # Journal of each write that can be undone, the last last
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
            self._not_writing()
            self.photo_generation += 1
            generation = self.photo_generation
            self.photo_folder = folder
            self.photos, self.looks, self.seen = [], [], []
            self.placed = {}
            self.photos_loading = True
            self._thumbnails.clear()
            self.match, self.results, self.warnings = None, [], None
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
            self._not_writing()
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
            self._not_writing()
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
            self.match, self.results, self.warnings = None, [], None
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
            self._not_writing()
            self.correction = seconds
            self._want_match()

    def set_options(self, overwrite=None, stops=None):
        reload = None
        with self.lock:
            self._not_writing()
            if overwrite is not None:
                self.overwrite = bool(overwrite)
            if stops is not None and bool(stops) != self.stops:
                self.stops = bool(stops)
                reload = self.track_choice
            self._want_match()
        if reload is not None:
            self._choose_tracks(reload)

    def write(self, version):
        """Start writing the locations of the match the page shows, numbered version."""
        with self.lock:
            self._not_writing()
            match = self.match
            if (self.photos_loading or self.tracks_loading or match is None
                    or match["version"] != version or match["correction"] != self.correction
                    or match["overwrite"] != self.overwrite):
                raise SessionError(_("The locations changed in the meantime. Check them and "
                                     "write again."))
            jobs = [Job(i, r.photo.path, r.lat, r.lon, r.ele, r.time_utc, r.photo.has_location,
                        self.seen[i])
                    for i, r in enumerate(self.results) if r.reason is None]
            if not jobs:
                raise SessionError(_("No photo has a location to write."))
            try:
                journal = Journal.create()
            except OSError as e:
                raise SessionError(_("The journal for undoing the write cannot be made: "
                                     "{error}").format(error=e.strerror or e)) from None
            self.writing = Writing("write", jobs, self.events.publish, self._written, journal)
            writing = self.writing
        self.events.publish("write-start", writing.progress())
        writing.start()
        return len(jobs)

    def undo(self):
        """Start undoing the last write that is not undone yet."""
        with self.lock:
            self._not_writing()
            journal = self.journals[-1] if self.journals else None
            entries = journal.entries() if journal is not None else []
            if not entries:
                raise SessionError(_("There is no write to undo."))
            jobs = [UndoJob(k, path, before, written)
                    for k, (path, before, written) in enumerate(entries)]
            self.writing = Writing("undo", jobs, self.events.publish, self._undone, journal)
            writing = self.writing
        self.events.publish("write-start", writing.progress())
        writing.start()
        return len(jobs)

    def undoable(self):
        """How many photos undoing the last write would restore; the lock is held."""
        return len(self.journals[-1].entries()) if self.journals else 0

    def cancel_write(self):
        """Change no more photos; those being changed are finished."""
        with self.lock:
            writing = self.writing
        if writing is not None:
            writing.cancel()
            self.events.publish("write-progress", writing.progress())

    def _not_writing(self):
        """Nothing may change while the photos are written; the lock is held."""
        if self.writing is not None:
            raise SessionError(_("Wait until the locations are written, or cancel the "
                                 "writing."))

    def _written(self, writing):
        """The writing ended: the written photos have a location now."""
        with self.lock:
            if writing.written:
                self.journals.append(writing.journal)
            else:
                writing.journal.keep([])
            names = {index: os.path.basename(self.photos[index].path)
                     for index, _message in writing.failed}
            changed = [(index, True, seen) for index, seen in writing.written]
        self._ended(writing, changed, names)

    def _undone(self, writing):
        """The undoing ended: the restored photos are as they were before the write."""
        jobs = writing.jobs
        restored = {k for k, _seen in writing.written}
        # The others can still be undone
        writing.journal.keep([(job.path, job.before, job.written) for job in jobs
                              if job.id not in restored])
        with self.lock:
            if not writing.journal.entries():
                self.journals.remove(writing.journal)
            index = {os.path.realpath(photo.path): i for i, photo in enumerate(self.photos)}
            changed = [(index[jobs[k].path], bool(jobs[k].before.get("had_location")), seen)
                       for k, seen in writing.written if jobs[k].path in index]
            names = {k: os.path.basename(jobs[k].path) for k, _message in writing.failed}
        self._ended(writing, changed, names)

    def _ended(self, writing, changed, names):
        """Photos were written or restored: changed holds (index, whether it has
        a location now, its os.stat()) of each; names the names of the failed."""
        with self.lock:
            generation = self.photo_generation
            summaries = []
            for index, has_location, seen in sorted(changed, key=lambda item: item[0]):
                self.photos[index] = self.photos[index]._replace(has_location=has_location)
                # Its location is the photo's own now
                self.placed.pop(index, None)
                self.seen[index] = seen
                # The thumbnail is the same, but the file is a new one
                self._thumbnails.pop(self.photos[index].path, None)
                summaries.append(self._photo_summary(index, self.photos[index],
                                                     self.looks[index]))
            self.writing = None
            undoable = self.undoable()
            if not self._closed:
                self._want_match()
        summary = writing.summary()
        for failure in summary["failed"]:
            failure["name"] = names[failure["id"]]
        self.events.publish("photos-changed", {"generation": generation, "photos": summaries})
        self.events.publish("undo", {"count": undoable})
        self.events.publish("write-done", summary)

    def place(self, generation, index, lat=None, lon=None):
        """Place a photo by hand at lat, lon; without them, it is matched on the track again."""
        if lat is not None or lon is not None:
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                lat = lon = math.nan
            # Comparisons with NaN are false, so this also rules out NaN and infinity
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise SessionError(_("invalid location: {location}").format(
                    location=f"{lat}, {lon}"))
        with self.lock:
            self._not_writing()
            if generation != self.photo_generation or not 0 <= index < len(self.photos):
                raise SessionError(_("This photo is no longer among the chosen ones."))
            if lat is None:
                self.placed.pop(index, None)
            else:
                self.placed[index] = (lat, lon)
            self._want_match()

    def close(self):
        """Stop the work in the background; photos being written are finished first."""
        with self.lock:
            self._closed = True
            self.photo_generation += 1
            self.track_generation += 1
            self._wake.notify_all()
            directory = self._upload_dir
            writing = self.writing
        if writing is not None:
            writing.cancel()
            writing.wait()
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
                "stops": self.stops, "matches": self.match, "warnings": self.warnings,
                "writing": None if self.writing is None else self.writing.progress(),
                "undo": self.undoable(),
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
            # Taken before exiftool reads the photos: a photo that another
            # program changes after that is not written
            seen = []
            for path in batch:
                try:
                    seen.append(os.stat(path))
                except OSError:
                    seen.append(None)
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
                self.seen += seen
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
            placed = dict(self.placed)
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
        for index, (lat, lon) in placed.items():
            if index < len(results):
                results[index] = placed_by_hand(photos[index], correction, lat, lon)
        summary = summarize(results)
        match = {
            "version": None, "generation": photo_generation, "tracks": track_generation,
            "correction": correction, "overwrite": overwrite,
            "results": [self._result(i, r, overwrite, i in placed)
                        for i, r in enumerate(results)],
            "matched": summary.matched, "skipped": summary.skipped,
            "at_stops": summary.at_stops,
        }
        with self.lock:
            # Matches during a drag of the slider are shown as they come, but
            # not those of photos or tracks that were replaced
            if (photo_generation != self.photo_generation
                    or track_generation != self.track_generation):
                return
            self._match_number += 1
            match["version"] = self._match_number
            self.match = match
            self.results = results
            # Signs of a suspicious match are looked for only in a match
            # that stays: during a drag of the slider, a newer one is wanted
            stays = not self._wanted
        self.events.publish("matches", match)
        if stays:
            self._warn(match, results, tracks, stops, placed)

    def _warn(self, match, results, tracks, stops, placed):
        """Tell the page of the signs of a suspicious match; they change nothing."""
        warnings = {"version": match["version"], "generation": match["generation"],
                    "tracks": match["tracks"],
                    "warnings": warnings_of(results, tracks, MAX_GAP, stops, placed,
                                            match["correction"], MAX_CORRECTION)}
        with self.lock:
            if self.match is not match:
                return
            self.warnings = warnings
        self.events.publish("warnings", warnings)

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
    def _result(index, result, overwrite, manual=False):
        entry = {"id": index, "time": _time_text(result.time), "reason": result.reason,
                 "files": [os.path.basename(p) for p in result.files]}
        if manual:
            entry.update(state="manual", lat=round(result.lat, 7), lon=round(result.lon, 7),
                         ele=None, overwrites=result.photo.has_location)
        elif result.reason is None:
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
