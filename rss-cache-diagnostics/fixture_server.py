"""Serve two immutable feed generations with independently controlled ETags."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

DATA = Path("/fixture")
FEEDS = {"unchanged", "updated", "stale", "recovery"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.respond(True)

    def do_HEAD(self):
        self.respond(False)

    def respond(self, want_body):
        path = urlsplit(self.path).path
        if path == "/" or path.startswith("/article/"):
            body = b"<!doctype html><title>RSS cache test fixture</title><p>Synthetic origin.</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if want_body:
                self.wfile.write(body)
            return
        feed = path.removeprefix("/").removesuffix(".xml")
        if not path.endswith(".xml") or feed not in FEEDS:
            self.send_error(404)
            return
        phase = (DATA / "phase.txt").read_text().strip()
        generation = "warm" if phase == "warm" or feed == "unchanged" else "new"
        body = (DATA / generation / f"{feed}.xml").read_bytes()
        etag_generation = "new" if phase == "new" and feed == "updated" else "warm"
        etag = f'"{feed}-{etag_generation}"'
        conditional_tag = self.headers.get("If-None-Match")
        status = 304 if conditional_tag == etag else 200
        record = {
            "phase": phase, "path": path, "generation": generation, "method": self.command,
            "status": status, "etag": etag, "if_none_match": conditional_tag,
            "if_modified_since": self.headers.get("If-Modified-Since"),
            "cache_control_request": self.headers.get("Cache-Control"),
            "user_agent": self.headers.get("User-Agent"),
            "sent_body": want_body and status == 200, "sha256": sha256(body).hexdigest(),
            "source_guids": [node.text for node in ET.fromstring(body).findall("./channel/item/guid")],
        }
        with (DATA / "origin.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(record) + "\n")
        self.send_response(status)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
        if status == 200:
            self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if record["sent_body"]:
            self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
