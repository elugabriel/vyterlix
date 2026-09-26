"""Development server for the static frontend (Python only, no Node).

    python serve.py            # http://localhost:5500

Same as `python -m http.server 5500`, but tells the browser not to cache anything, so a
changed .js or .css file is picked up on the next page load. Development only: production
hosting sets proper cache headers (see the deployment checklist).
"""

import functools
import http.server
import sys
from pathlib import Path


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5500
    handler = functools.partial(NoCacheHandler, directory=str(Path(__file__).parent))
    with http.server.ThreadingHTTPServer(("localhost", port), handler) as server:
        print(f"Vyterlix frontend on http://localhost:{port} (no caching)")
        server.serve_forever()


if __name__ == "__main__":
    main()
