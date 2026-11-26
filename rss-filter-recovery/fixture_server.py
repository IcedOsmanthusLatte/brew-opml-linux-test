"""Serve controlled feed versions and keep a complete synthetic origin receipt."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

DATA = Path("/fixture")
KEYWORD = "lacuna"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.serve(True)

    def do_HEAD(self):
        self.serve(False)

    def serve(self, include_body):
        path = urlsplit(self.path).path
        manifest = json.loads((DATA / "active.json").read_text())
        name = path.removeprefix("/")
        if name in {"retained.xml", "removed.xml"}:
            body = (DATA / manifest["phase"] / name).read_bytes()
            root = ET.fromstring(body)
            guids = [item.text for item in root.findall("./channel/item/guid")]
            content_type = "application/rss+xml; charset=utf-8"
        elif name.startswith("article/"):
            has_keyword = name.endswith("-web")
            body = (
                "<!doctype html><html><head><title>Fixture article</title></head>"
                "<body><article><h1>Synthetic experiment article</h1><p>"
                + (KEYWORD if has_keyword else "meadow")
                + " appears in this example HTML page.</p></article></body></html>"
            ).encode()
            guids = None
            content_type = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        etag = '"' + sha256(body).hexdigest() + '"'
        modified = manifest["last_modified"]
        status = 200
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", modified)
        self.end_headers()
        # One write per record keeps concurrent observations separate.
        with (DATA / "origin.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"phase": manifest["phase"], "path": path, "method": self.command,
                                  "response_status": status, "sent_body": include_body,
                                  "sha256": sha256(body).hexdigest(), "etag": etag,
                                  "last_modified": modified, "if_none_match": self.headers.get("If-None-Match"),
                                  "if_modified_since": self.headers.get("If-Modified-Since"),
                                  "item_guids": guids}, separators=(",", ":")) + "\n")
        if include_body:
            self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
