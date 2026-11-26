"""Compare matched RSS snapshots in independent clean Miniflux stores."""

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
from capture import FEEDS, normalize


SOURCE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
USERNAME = "nhllab"
PASSWORD = secrets.token_urlsafe(28)
BASIC = b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
PREFIX = f"nhl-{secrets.token_hex(4)}"
NETWORK = f"{PREFIX}-network"
POSTGRES = f"{PREFIX}-db"
ORIGIN = "http://nhl-fixture.test:8090"
WORKER = build_opener(ProxyHandler({}))
IMAGES = {
    "db": "postgres:16.13-alpine",
    "app": "miniflux/miniflux:2.3.5",
    "fixture": "python:3.12-alpine",
}
NAMES = tuple(FEEDS)


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
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = feed["feed_title"]
    ET.SubElement(channel, "link").text = feed["final_url"]
    ET.SubElement(channel, "description").text = "Public feed metadata replay."
    for row in feed["items"]:
        item = ET.SubElement(channel, "item")
        for name, field in (("title", "title"), ("link", "url"), ("pubDate", "published_rss")):
            ET.SubElement(item, name).text = row[field]
        ET.SubElement(item, "guid", isPermaLink="false").text = row["guid"]
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

def opml(feed_urls):
    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = "NHL six-feed trial"
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
        output = []
        for offset in range(0, 1000, 100):
            response = self.request("GET", f"/v1/entries?limit=100&offset={offset}&order=id&direction=asc")
            rows = response["entries"]
            output.extend({key: row[key] for key in (
                "id", "feed_id", "url", "title", "hash", "published_at", "status",
            )} for row in rows)
            if len(rows) < 100:
                if len(output) != response["total"]:
                    raise RuntimeError("Reader pagination count did not match its reported total")
                return output
        raise RuntimeError("Reader pagination reached its experiment safety limit")

    def load(self, sample, urls):
        self.request("POST", "/v1/import", sample, "application/xml")
        ids = ready(lambda: self.feed_map() if set(self.feed_map()) == set(urls.values()) else None)
        return {name: ids[url] for name, url in urls.items()}

    def refresh(self, feed_ids):
        for feed in feed_ids.values():
            self.request("PUT", f"/v1/feeds/{feed}/refresh")


def compare_expected(rows, ids, expected, exact):
    for name, feed_id in ids.items():
        keys = [normalize(row["url"], FEEDS[name]) for row in rows if row["feed_id"] == feed_id]
        expected_keys = [normalize(url, FEEDS[name]) for url in expected[name]]
        observed, target = Counter(keys), Counter(expected_keys)
        if (exact and observed != target) or (not exact and target - observed):
            raise RuntimeError(
                f"{name} normalized URLs differ: missing {list(target - observed)}, "
                f"extra {list(observed - target)}"
            )


def source_urls(snapshot):
    result = {}
    for name in NAMES:
        rows = list({row["guid"]: row for row in snapshot[name]["items"]}.values())
        result[name] = [row["url"] for row in rows]
    return result

def analysis(name, rows, feed_ids):
    names = {value: key for key, value in feed_ids.items()}
    counts = Counter(names[row["feed_id"]] for row in rows)
    duplicates = {}
    by_url = {}
    for row in rows:
        key = normalize(row["url"], FEEDS[names[row["feed_id"]]])
        by_url.setdefault(key, []).append({
            "source": names[row["feed_id"]], "reader_entry_id": row["id"],
        })
    for key, items in by_url.items():
        if len({row["source"] for row in items}) > 1:
            duplicates[key] = items
    print(json.dumps({"cohort": name, "count": len(rows), "per_source": counts,
                      "cross_feed_duplicate_urls": len(duplicates)}), flush=True)
    return {"reader_native_api_rows": rows, "source_feed_ids": feed_ids,
            "count": len(rows), "per_source": counts,
            "cross_feed_duplicate_urls": duplicates}


def run():
    source_order = "ab" if (SOURCE / "snapshot-b.json").exists() else "a"
    fixture = OUT / "fixture"
    fixture.mkdir()
    snapshots = {}
    manifest = {}
    for phase, source_phase in zip(("a", "b"), source_order):
        snapshots[phase] = json.loads((SOURCE / f"snapshot-{source_phase}.json").read_text("utf-8"))["feeds"]
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
    public_sample = (SOURCE / "nhl-six-sources.opml").read_bytes()
    public_addrs = [row.attrib["xmlUrl"] for row in ET.fromstring(public_sample).iter("outline")]
    if public_addrs != list(live_urls.values()):
        raise RuntimeError("Published OPML addresses no longer match all literal captured URLs")
    (OUT / "public-six-sources.opml").write_bytes(public_sample)
    (OUT / "replay-six-sources.opml").write_bytes(sample)
    digest = {}
    for name, tag in IMAGES.items():
        docker("pull", tag)
        digest[name] = json.loads(docker("image", "inspect", tag, "--format", "{{json .RepoDigests}}"))[0]
    result = {"runner": os.environ.get("ImageOS"), "runner_image": os.environ.get("ImageVersion"),
              "images": digest, "fixture_sha256": manifest, "source_order": list(source_order),
              "public_opml_sha256": hashlib.sha256(public_sample).hexdigest(),
              "replay_opml_sha256": hashlib.sha256(sample).hexdigest(),
              "cohorts": {}}
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
               "--network-alias", "nhl-fixture.test", "-v", f"{fixture}:/fixture",
               "-v", f"{SOURCE / 'fixture_server.py'}:/fixture_server.py:ro",
               digest["fixture"], "python", "/fixture_server.py")
        cleanup.callback(docker, "rm", "-fv", server, check=False)
        early = Reader(cleanup, "early", digest["app"])
        early_ids = early.load(sample, replay_urls)
        early.refresh(early_ids)
        early_a = early.visible()
        first_urls = source_urls(snapshots["a"])
        compare_expected(early_a, early_ids, first_urls, exact=True)
        result["cohorts"]["fresh_first"] = analysis("fresh_first", early_a, early_ids)
        if "b" in snapshots:
            (fixture / "next").write_text("b")
            (fixture / "next").replace(fixture / "active.txt")
            later_urls = source_urls(snapshots["b"])
            late = Reader(cleanup, "late", digest["app"])
            late_ids = late.load(sample, replay_urls)
            late.refresh(late_ids)
            late_b = late.visible()
            compare_expected(late_b, late_ids, later_urls, exact=True)
            result["cohorts"]["fresh_second"] = analysis("fresh_second", late_b, late_ids)
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
        result["initial_empty_stores_verified"] = len(snapshots)
        # Save the controlled result even if publisher network reachability differs.
        write_json(OUT / "result.json", result)
        live = Reader(cleanup, "live", digest["app"])
        try:
            live_ids = live.load(public_sample, live_urls)
            live.refresh(live_ids)
            result["cohorts"]["live_official"] = analysis("live_official", live.visible(), live_ids)
            result["public_urls_native_imported"] = len(NAMES)
        except (OSError, RuntimeError) as error:
            result["public_urls_live_probe"] = str(error).replace(PASSWORD, "[redacted]")
        write_json(OUT / "result.json", result)


if __name__ == "__main__":
    run()
