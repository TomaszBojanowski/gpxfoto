"""The requests of the page under /api/, answered from the session."""
from http import HTTPStatus

from gpxfoto.server.app import RequestError


class Api:
    """Answers the page's requests; the work is done in background threads.

    events sends what the work finds to the page as it goes.
    """

    def __init__(self, events):
        self.events = events
        self.routes = {}

    def handle(self, handler, method, name, query):
        route = self.routes.get((method, name))
        if route is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "not found")
        route(handler, query)

    def close(self):
        """Stop the work in the background."""
