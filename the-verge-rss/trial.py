"""Replay two official Atom snapshots through Miniflux on a disposable runner."""

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from base64 import b64encode
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM)
SOURCE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
USERNAME = "vergelab"
PASSWORD = secrets.token_urlsafe(28)
BASIC = b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
PREFIX = f"verge-{secrets.token_hex(4)}"
NETWORK = f"{PREFIX}-network"
POSTGRES = f"{PREFIX}-db"
ORIGIN = "http://verge-fixture.test:8090"
WORKER = build_opener(ProxyHandler({}))
IMAGES = {
    "db": "postgres:16.13-alpine",
    "app": "miniflux/miniflux:2.3.5",
    "fixture": "python:3.12-alpine",
}
NAMES = ("main", "ai", "reviews")


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=140, check=False)
    if check and result.returncode:
        detail = result.stderr.decode(errors="replace").replace(PASSWORD, "[redacted]")
        raise RuntimeError(f"docker {args[0]} failed: {detail[:1800]}")
    return result.stdout


def ready(function):
    last = None
    for _ in range(55):
        try:
            result = function()
            if result:
                return result
        except (RuntimeError, OSError) as error:
            last = error
        time.sleep(1)
    raise RuntimeError(f"Readiness check failed: {last}")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make_fixture(feed, feed_name):
    root = ET.Element(f"{{{ATOM}}}feed")
    for name, value in (("title", feed["feed_title"]), ("id", feed["feed_id"]),
                        ("updated", feed["feed_updated"])):
        ET.SubElement(root, f"{{{ATOM}}}{name}").text = value
    ET.SubElement(root, f"{{{ATOM}}}link", rel="self", href=f"{ORIGIN}/{feed_name}.xml")
    for item in feed["entries"]:
        entry = ET.SubElement(root, f"{{{ATOM}}}entry")
        for name, field in (("id", "atom_id"), ("title", "title"),
                            ("published", "published"), ("updated", "updated")):
            ET.SubElement(entry, f"{{{ATOM}}}{name}").text = item[field]
        ET.SubElement(entry, f"{{{ATOM}}}link", rel="alternate", href=item["original_url"])
        ET.SubElement(entry, f"{{{ATOM}}}content").text = "Body omitted from public experiment."
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def opml(feed_urls):
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = "The Verge three-feed trial"
    body = ET.SubElement(root, "body")
    for name, url in feed_urls.items():
        ET.SubElement(body, "outline", type="rss", text=name, xmlUrl=url)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class Reader:
    def __init__(self, cleanup, label, image):
        self.label = label
        self.name = f"{PREFIX}-{label}"
        docker("exec", POSTGRES, "createdb", "-U", USERNAME, label)
        docker("run", "-d", "--name", self.name, "--network", NETWORK, "-p", "127.0.0.1::8080",
               "-e", f"DATABASE_URL=postgres://{USERNAME}:{PASSWORD}@{POSTGRES}/{label}?sslmode=disable",
               "-e", "RUN_MIGRATIONS=1", "-e", "CREATE_ADMIN=1", "-e", f"ADMIN_USERNAME={USERNAME}",
               "-e", f"ADMIN_PASSWORD={PASSWORD}", "-e", "POLLING_FREQUENCY=3600",
               "-e", "FETCHER_ALLOW_PRIVATE_NETWORKS=1", image)
        cleanup.callback(docker, "rm", "-fv", self.name, check=False)
        port = docker("port", self.name, "8080/tcp").decode().strip().rsplit(":", 1)[-1]
        self.base = f"http://127.0.0.1:{port}"
        self.version = ready(lambda: self.request("GET", "/v1/version"))
        if isinstance(self.version, dict):
            self.version = self.version["version"]
        if self.version != "2.3.5" or self.feed_map() or self.visible():
            raise RuntimeError(f"Reader {label} did not start with an empty 2.3.5 store")

    def request(self, method, path, data=None, content_type=None):
        hdr = {"Authorization": f"Basic {BASIC}"}
        if content_type:
            hdr["Content-Type"] = content_type
        request = Request(self.base + path, data=data, headers=hdr, method=method)
        with WORKER.open(request, timeout=35) as response:
            body = response.read()
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body.decode().strip()

    def feed_map(self):
        return {row["feed_url"]: row["id"] for row in self.request("GET", "/v1/feeds")}

    def visible(self):
        result = self.request("GET", "/v1/entries?limit=100")
        return [
            {key: row[key] for key in ("id", "feed_id", "url", "title", "hash", "published_at", "status")}
            for row in result["entries"]
        ]

    def load(self, sample, urls):
        self.request("POST", "/v1/import", sample, "application/xml")
        ids = ready(lambda: self.feed_map() if set(self.feed_map()) == set(urls.values()) else None)
        return {name: ids[url] for name, url in urls.items()}

    def refresh(self, feed_ids):
        for feed in feed_ids.values():
            self.request("PUT", f"/v1/feeds/{feed}/refresh")


def compare_expected(rows, ids, expected, exact):
    for name, feed_id in ids.items():
        keys = [row["url"] for row in rows if row["feed_id"] == feed_id]
        observed, target = Counter(keys), Counter(expected[name])
        if (exact and observed != target) or (not exact and target - observed):
            raise RuntimeError(f"{name} differs: expected {len(target)}, observed {len(observed)}")


def source_urls(snapshot):
    return {name: [entry["original_url"] for entry in snapshot[name]["entries"]] for name in NAMES}


def analysis(name, rows, feed_ids):
    names = {value: key for key, value in feed_ids.items()}
    counts = Counter(names[row["feed_id"]] for row in rows)
    duplicates = {}
    for url, count in Counter(row["url"] for row in rows).items():
        if count > 1:
            duplicates[url] = [{"source": names[row["feed_id"]], "reader_entry_id": row["id"]}
                               for row in rows if row["url"] == url]
    print(json.dumps({"cohort": name, "count": len(rows), "per_source": counts,
                      "cross_feed_duplicate_urls": len(duplicates)}), flush=True)
    return {"reader_native_api_rows": rows, "count": len(rows), "per_source": counts,
            "cross_feed_duplicate_urls": duplicates}


def run():
    fixture = OUT / "fixture"
    fixture.mkdir()
    snapshots = {}
    manifest = {}
    for phase in ("a", "b"):
        snapshots[phase] = json.loads((SOURCE / f"snapshot-{phase}.json").read_text("utf-8"))
        manifest[phase] = {}
        (fixture / phase).mkdir()
        for name in NAMES:
            payload = make_fixture(snapshots[phase][name], name)
            (fixture / phase / f"{name}.xml").write_bytes(payload)
            manifest[phase][f"/{name}.xml"] = hashlib.sha256(payload).hexdigest()
    (fixture / "active.txt").write_text("a")
    replay_urls = {name: f"{ORIGIN}/{name}.xml" for name in NAMES}
    live_urls = {name: snapshots["a"][name]["url"] for name in NAMES}
    sample = opml(replay_urls)
    (OUT / "official-sources.opml").write_bytes(opml(live_urls))
    (OUT / "replay-sources.opml").write_bytes(sample)
    digest = {}
    for name, tag in IMAGES.items():
        docker("pull", tag)
        digest[name] = json.loads(docker("image", "inspect", tag, "--format", "{{json .RepoDigests}}"))[0]
    result = {"runner": os.environ.get("ImageOS"), "runner_image": os.environ.get("ImageVersion"),
              "images": digest, "fixture_sha256": manifest, "cohorts": {}}
    events = []
    with ExitStack() as cleanup:
        docker("network", "create", NETWORK)
        cleanup.callback(docker, "network", "rm", NETWORK, check=False)
        docker("run", "-d", "--name", POSTGRES, "--network", NETWORK,
               "-e", f"POSTGRES_USER={USERNAME}", "-e", f"POSTGRES_PASSWORD={PASSWORD}", digest["db"])
        cleanup.callback(docker, "rm", "-fv", POSTGRES, check=False)
        ready(lambda: docker("exec", POSTGRES, "pg_isready", "-U", USERNAME).decode())
        server = f"{PREFIX}-fixture"
        docker("run", "-d", "--name", server, "--network", NETWORK,
               "--network-alias", "verge-fixture.test", "-v", f"{fixture}:/fixture",
               "-v", f"{SOURCE / 'fixture_server.py'}:/fixture_server.py:ro",
               digest["fixture"], "python", "/fixture_server.py")
        cleanup.callback(docker, "rm", "-fv", server, check=False)
        early = Reader(cleanup, "early", digest["app"])
        early_ids = early.load(sample, replay_urls)
        early.refresh(early_ids)
        early_a = early.visible()
        first_urls = source_urls(snapshots["a"])
        compare_expected(early_a, early_ids, first_urls, exact=True)
        result["cohorts"]["fresh_a"] = analysis("fresh_a", early_a, early_ids)
        (fixture / "next").write_text("b")
        (fixture / "next").replace(fixture / "active.txt")
        early.refresh(early_ids)
        early_b = early.visible()
        later_urls = source_urls(snapshots["b"])
        compare_expected(early_b, early_ids,
                         {name: set(first_urls[name]) | set(later_urls[name]) for name in NAMES}, exact=True)
        result["cohorts"]["refreshed_b"] = analysis("refreshed_b", early_b, early_ids)
        late = Reader(cleanup, "late", digest["app"])
        late_ids = late.load(sample, replay_urls)
        late.refresh(late_ids)
        late_b = late.visible()
        compare_expected(late_b, late_ids, later_urls, exact=True)
        result["cohorts"]["fresh_b"] = analysis("fresh_b", late_b, late_ids)
        for line in (fixture / "requests.jsonl").read_text().splitlines():
            event = json.loads(line)
            if event["sha256"] != manifest[event["phase"]][event["path"]]:
                raise RuntimeError("Reader retrieved unexpected source fixture bytes")
            events.append(event)
        if {(event["phase"], event["path"]) for event in events} != {
            (phase, f"/{name}.xml") for phase in snapshots for name in NAMES
        }:
            raise RuntimeError("Missing matching origin receipts for one or more sources")
        result["native_reader_version"] = early.version
        result["verified_origin_requests"] = events
        result["initial_empty_stores_verified"] = 2
        # Save the controlled result even if publisher network reachability differs.
        write_json(OUT / "result.json", result)
        live = Reader(cleanup, "live", digest["app"])
        try:
            live_ids = live.load(opml(live_urls), live_urls)
            live.refresh(live_ids)
            result["cohorts"]["live_official"] = analysis("live_official", live.visible(), live_ids)
            result["official_urls_native_imported"] = 3
        except (OSError, RuntimeError) as error:
            result["official_urls_live_probe"] = str(error).replace(PASSWORD, "[redacted]")
        write_json(OUT / "result.json", result)


if __name__ == "__main__":
    run()
