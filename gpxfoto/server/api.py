"""The requests of the page under /api/, answered from the session."""
from http import HTTPStatus

from gpxfoto import i18n
from gpxfoto.server import files
from gpxfoto.server.app import RequestError


class Api:
    """Answers the page's requests; the work is done in background threads.

    events sends what the work finds to the page as it goes.
    """

    def __init__(self, events):
        self.events = events
        self.routes = {
            ("GET", "i18n"): self.get_i18n,
            ("GET", "places"): self.get_places,
            ("GET", "browse"): self.get_browse,
        }

    def handle(self, handler, method, name, query):
        route = self.routes.get((method, name))
        if route is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "not found")
        route(handler, query)

    def get_i18n(self, handler, query):
        handler.send_json(i18n.catalog())

    def get_places(self, handler, query):
        handler.send_json({"places": files.places(), "recent": files.recent()})

    def get_browse(self, handler, query):
        try:
            handler.send_json(files.listing(query.get("path", [""])[0]))
        except files.FolderError as e:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(e)) from None

    def close(self):
        """Stop the work in the background."""
