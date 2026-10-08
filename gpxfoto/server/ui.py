"""gpxfoto --ui: start the local server and open the page in the browser."""
import shutil
import sys
import threading
import webbrowser
from gettext import gettext as _

from gpxfoto.server.app import Server


def run(open_browser=True):
    """Serve the browser interface until its page is closed or Ctrl+C."""
    if shutil.which("exiftool") is None:
        sys.exit(_("exiftool is not installed. On Fedora, install it with: {command}").format(
            command="sudo dnf install perl-Image-ExifTool"))
    from gpxfoto.server.api import Api     # the engine, loaded once the page is coming
    server = Server()
    server.api = Api(server.events)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=server.watch_pages, daemon=True).start()
    # Translators: {url} is the address of the page of the browser interface
    print(_("gpxfoto runs at {url}").format(url=server.url))
    print(_("If no browser opens, open that address. Press Ctrl+C to quit."))
    sys.stdout.flush()
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True).start()
    try:
        while not server.stopping.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    server.api.close()
    server.close()
