"""The requests of the page under /api/, answered from the session."""
from http import HTTPStatus

from gpxfoto import i18n
from gpxfoto.server import files
from gpxfoto.server.app import MAX_BODY, RequestError
from gpxfoto.server.session import Session, SessionError


_MISSING = object()


def _field(data, name, kind, default=_MISSING):
    """A field of a JSON request, which must be of kind; bool is no number.

    Without a default the field is required; a field with default None
    may also be null.
    """
    if not isinstance(data, dict):
        raise RequestError(HTTPStatus.BAD_REQUEST, "an object expected")
    if name not in data:
        if default is _MISSING:
            raise RequestError(HTTPStatus.BAD_REQUEST, f"{name} expected")
        return default
    value = data[name]
    if value is None and default is None:
        return None
    if kind is bool:
        valid = isinstance(value, bool)
    else:
        valid = isinstance(value, kind) and not isinstance(value, bool)
    if not valid:
        raise RequestError(HTTPStatus.BAD_REQUEST, f"{name}: wrong type")
    return value


class Api:
    """Answers the page's requests; the work is done in background threads.

    events sends what the work finds to the page as it goes.
    """

    def __init__(self, events):
        self.events = events
        self.session = Session(events)
        self.routes = {
            ("GET", "i18n"): self.get_i18n,
            ("GET", "places"): self.get_places,
            ("GET", "browse"): self.get_browse,
            ("GET", "state"): self.get_state,
            ("GET", "thumbnail"): self.get_thumbnail,
            ("POST", "photos"): self.post_photos,
            ("POST", "tracks"): self.post_tracks,
            ("POST", "tracks/drop"): self.post_dropped_tracks,
            ("POST", "correction"): self.post_correction,
            ("POST", "options"): self.post_options,
            ("POST", "preferences"): self.post_preferences,
        }

    def handle(self, handler, method, name, query):
        route = self.routes.get((method, name))
        if route is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "not found")
        try:
            route(handler, query)
        except SessionError as e:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(e)) from None

    def close(self):
        """Stop the work in the background."""
        self.session.close()

    def get_i18n(self, handler, query):
        handler.send_json(i18n.catalog())

    def get_places(self, handler, query):
        handler.send_json({"places": files.places(), "recent": files.recent()})

    def get_browse(self, handler, query):
        try:
            handler.send_json(files.listing(query.get("path", [""])[0]))
        except files.FolderError as e:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(e)) from None

    def get_state(self, handler, query):
        handler.send_json({**self.session.state(), "preferences": files.preferences()})

    def get_thumbnail(self, handler, query):
        try:
            generation = int(query.get("generation", [""])[0])
            index = int(query.get("id", [""])[0])
        except ValueError:
            raise RequestError(HTTPStatus.BAD_REQUEST, "generation and id expected") from None
        data = self.session.thumbnail(generation, index)
        if data is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "no thumbnail")
        # The address names the photo of one generation, so it never changes
        handler.send_body(data, "image/jpeg", cache="private, max-age=86400")

    def post_photos(self, handler, query):
        data = handler.read_json()
        generation = self.session.choose_photos(_field(data, "folder", str),
                                                _field(data, "recursive", bool, False))
        handler.send_json({"generation": generation}, HTTPStatus.ACCEPTED)

    def post_tracks(self, handler, query):
        data = handler.read_json()
        if isinstance(data, dict) and "files" in data:
            paths = _field(data, "files", list)
            if not all(isinstance(p, str) for p in paths):
                raise RequestError(HTTPStatus.BAD_REQUEST, "files: wrong type")
            generation = self.session.choose_track_files(paths)
        else:
            generation = self.session.choose_track_folder(_field(data, "folder", str),
                                                          _field(data, "recursive", bool, False))
        handler.send_json({"generation": generation}, HTTPStatus.ACCEPTED)

    def post_dropped_tracks(self, handler, query):
        data = handler.read_json(limit=MAX_BODY)
        dropped = _field(data, "files", list)
        pairs = []
        for item in dropped:
            pairs.append((_field(item, "name", str), _field(item, "data", str)))
        generation = self.session.drop_tracks(pairs)
        handler.send_json({"generation": generation}, HTTPStatus.ACCEPTED)

    def post_correction(self, handler, query):
        seconds = _field(handler.read_json(), "seconds", (int, float))
        self.session.set_correction(seconds)
        handler.send_json({"correction": seconds}, HTTPStatus.ACCEPTED)

    def post_options(self, handler, query):
        data = handler.read_json()
        self.session.set_options(overwrite=_field(data, "overwrite", bool, None),
                                 stops=_field(data, "stops", bool, None))
        handler.send_json({}, HTTPStatus.ACCEPTED)

    def post_preferences(self, handler, query):
        data = handler.read_json()
        if not isinstance(data, dict) or not data:
            raise RequestError(HTTPStatus.BAD_REQUEST, "an object expected")
        for name, value in data.items():
            try:
                files.set_preference(name, value)
            except ValueError as e:
                raise RequestError(HTTPStatus.BAD_REQUEST, str(e)) from None
        chosen = files.preferences()
        # Other open pages follow
        self.events.publish("preferences", chosen)
        handler.send_json(chosen)
