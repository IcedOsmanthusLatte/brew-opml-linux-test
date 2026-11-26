"""Serve public feed snapshots with publisher body text removed."""

import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


BASE = Path("/fixture")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not re.fullmatch(r"/(cbs-nhl|cbs-all|espn-nhl|league|islanders|mapleleafs)\.xml", self.path):
            self.send_error(404)
            return
        phase = (BASE / "active.txt").read_text().strip()
        payload = (BASE / phase / self.path.removeprefix("/")).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("ETag", f'"{digest}"')
        self.end_headers()
        with (BASE / "requests.jsonl").open("a") as log:
            log.write(json.dumps({"phase": phase, "path": self.path, "sha256": digest}) + "\n")
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
