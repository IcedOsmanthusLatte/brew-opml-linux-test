import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
import xml.etree.ElementTree as ET

FRESH_IMAGE = "freshrss/freshrss:1.30.4"
MINI_IMAGE = "miniflux/miniflux:2.3.5"
DB_IMAGE = "postgres:16.13-alpine"
FRESH_DIR = "/var/www/FreshRSS"
USER = "opmllab"
TARGET = "https://feeds.bbci.co.uk/news/world/rss.xml"
URL = "https://getbrew.app/collections/downloads/opml-migration-normal.opml"
SHA = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
FRESH_TTL = "{https://freshrss.org/opml}ttl"
MINI_DISABLED = "{https://miniflux.app/opml}disabled"
OUTPUT = Path(sys.argv[1])
PROBE = Path(__file__).with_name("fresh-state.php")
LOCAL_HTTP = build_opener(ProxyHandler({}))
RUN_PREFIX = "opml-" + secrets.token_hex(4)
SECRET = secrets.token_urlsafe(24)
BASIC = base64.b64encode(f"{USER}:{SECRET}".encode()).decode()
SOURCE_MODES = {
    "freshrss": "FreshRSS normal CLI OPML export; web export_opml invokes the same generateOpml() service",
    "miniflux": "Miniflux GET /v1/export (native OPML)",
}


def docker(*args, timeout=60, check=True):
    cmd = subprocess.run(
        ["docker", *args], capture_output=True, timeout=timeout, check=False
    )
    if check and cmd.returncode:
        error = cmd.stderr.decode(errors="replace").replace(SECRET, "[redacted]").replace(BASIC, "[redacted]")
        raise RuntimeError(f"docker {args[0]} failed ({cmd.returncode}): {error[-900:]}")
    return cmd


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pause_until(fn, timeout=65):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        try:
            value = fn()
            if value:
                return value
        except (RuntimeError, ValueError, URLError, TimeoutError):
            pass
        time.sleep(1)
    return fn()


def outline_rows(payload):
    root = ET.fromstring(payload)
    if root.tag != "opml":
        raise ValueError("The response did not contain an OPML document.")
    body = root.find("body")
    if body is None:
        raise ValueError("OPML body not found.")
    rows = []
    folders = []

    def visit(node, parents):
        for outline in node.findall("outline"):
            url = outline.get("xmlUrl")
            if url:
                rows.append({
                    "url": url,
                    "title": outline.get("title") or outline.get("text"),
                    "path": list(parents),
                    "fresh_ttl_attribute": outline.get(FRESH_TTL),
                    "miniflux_disabled_attribute": outline.get(MINI_DISABLED),
                })
            else:
                folder = outline.get("text") or outline.get("title") or ""
                path = (*parents, folder)
                folders.append(list(path))
                visit(outline, path)

    visit(body, ())
    return root, sorted(rows, key=lambda row: row["url"]), sorted(folders)


def safe_export(payload, expected_urls):
    root, rows, folders = outline_rows(payload)
    if {row["url"] for row in rows} != expected_urls or len(rows) != len(expected_urls):
        raise RuntimeError("The exported address set did not match the public sample.")
    forbidden = re.compile(r"auth|cookie|password|proxy|token|header|useragent|apikey", re.I)
    for node in root.iter():
        for attribute, value in node.attrib.items():
            parsed = urlsplit(value) if value.startswith(("http:", "https:")) else None
            if (forbidden.search(attribute) or SECRET in value or BASIC in value
                    or (parsed and (
                        parsed.username is not None or parsed.password is not None
                        or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                        or bool(parsed.hostname and parsed.hostname.endswith(".invalid"))
                    ))):
                raise RuntimeError("Sensitive-looking attribute in export. Raw export withheld.")
    if SECRET.encode() in payload or BASIC.encode() in payload:
        raise RuntimeError("A generated credential appeared in export. Raw export withheld.")
    return {"feeds": rows, "folders": folders, "sha256": hashlib.sha256(payload).hexdigest()}


def state_check(rows, folders):
    observed = {row["url"]: row["folder"] for row in rows}
    if observed != folders or len(rows) != len(folders):
        raise RuntimeError(f"Unexpected imported feed count or folder mapping: {observed!r}")


class FreshRSS:
    def __init__(self, cleanup, name, network):
        self.name = name
        docker(
            "run", "-d", "--name", name, "--network", network,
            "-e", "FRESHRSS_INSTALL=--db-type sqlite --default-user opmllab --language en",
            "-e", f"FRESHRSS_USER=--user {USER} --password {SECRET} --no-default-feeds",
            FRESH_IMAGE,
        )
        cleanup.callback(docker, "rm", "-fv", name, check=False)
        docker("cp", str(PROBE), f"{name}:/tmp/fresh-state.php")
        initial = pause_until(self.state)
        if initial["feeds"] or initial["version"] != "1.30.4":
            raise RuntimeError("FreshRSS instance is not a clean 1.30.4 setup.")
        self.version = initial["version"]

    def cli(self, *args, timeout=85):
        return docker(
            "exec", "-u", "www-data", "-w", FRESH_DIR, self.name, "php", *args, timeout=timeout
        ).stdout

    def state(self, target=None):
        args = ["/tmp/fresh-state.php", target] if target else ["/tmp/fresh-state.php"]
        return json.loads(self.cli(*args))

    def import_opml(self, source):
        docker("cp", str(source), f"{self.name}:/tmp/roundtrip.opml")
        self.cli("cli/import-for-user.php", "--user", USER, "--filename", "/tmp/roundtrip.opml")
        return self.state()["feeds"]

    def pause(self, target):
        return self.state(target)["feeds"]

    def export(self):
        return self.cli("cli/export-opml-for-user.php", "--user", USER)


class Miniflux:
    def __init__(self, cleanup, name, network, db, database):
        self.name = name
        docker("exec", db, "createdb", "-U", USER, database)
        docker(
            "run", "-d", "--name", name, "--network", network, "-p", "127.0.0.1::8080",
            "-e", f"DATABASE_URL=postgres://{USER}:{SECRET}@{db}/{database}?sslmode=disable",
            "-e", "RUN_MIGRATIONS=1", "-e", "CREATE_ADMIN=1", "-e", f"ADMIN_USERNAME={USER}",
            "-e", f"ADMIN_PASSWORD={SECRET}", "-e", "POLLING_FREQUENCY=3600",
            MINI_IMAGE,
        )
        cleanup.callback(docker, "rm", "-fv", name, check=False)
        port = docker("port", name, "8080/tcp").stdout.decode().strip().split(":")[-1]
        self.url = f"http://127.0.0.1:{port}"
        self.version = pause_until(lambda: self.request("GET", "/v1/version").decode().strip())
        if self.version.startswith(("{", '"')):
            decoded = json.loads(self.version)
            self.version = decoded["version"] if isinstance(decoded, dict) else decoded
        if self.snapshot() or self.version != "2.3.5":
            raise RuntimeError(f"Miniflux instance is not a clean 2.3.5 setup (version {self.version!r}).")

    def request(self, method, path, body=None, media_type=None):
        headers = {"Authorization": f"Basic {BASIC}"}
        if media_type:
            headers["Content-Type"] = media_type
        req = Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with LOCAL_HTTP.open(req, timeout=15) as response:
                return response.read()
        except HTTPError as error:
            raise RuntimeError(f"Miniflux {method} {path}: HTTP {error.code}") from None

    def get_feeds(self):
        return json.loads(self.request("GET", "/v1/feeds"))

    def snapshot(self):
        return [{
            "url": feed["feed_url"],
            "title": feed["title"],
            "folder": feed["category"]["title"],
            "paused": feed["disabled"],
        } for feed in self.get_feeds()]

    def import_opml(self, source):
        self.request("POST", "/v1/import", source.read_bytes(), "application/xml")
        return self.snapshot()

    def pause(self, target):
        feed, = [row for row in self.get_feeds() if row["feed_url"] == target]
        update = json.dumps({"disabled": True}).encode()
        self.request("PUT", f"/v1/feeds/{feed['id']}", update, "application/json")
        return self.snapshot()

    def export(self):
        return self.request("GET", "/v1/export")


def ordered(rows):
    return sorted(rows, key=lambda row: row["url"])


def one_round(index, sample, expected_folders):
    directory = OUTPUT / f"round-{index}"
    directory.mkdir()
    network = f"{RUN_PREFIX}-r{index}"
    database = f"{network}-db"
    print(f"Round {index}: starting clean isolated instances.", flush=True)
    with ExitStack() as cleanup:
        docker("network", "create", network)
        cleanup.callback(docker, "network", "rm", network, check=False)
        docker(
            "run", "-d", "--name", database, "--network", network,
            "-e", f"POSTGRES_USER={USER}", "-e", f"POSTGRES_PASSWORD={SECRET}", DB_IMAGE,
        )
        cleanup.callback(docker, "rm", "-fv", database, check=False)
        if not pause_until(lambda: docker("exec", database, "pg_isready", "-h", database, "-U", USER, check=False).returncode == 0):
            raise RuntimeError("The disposable PostgreSQL instance did not start.")

        def create(app, suffix):
            name = f"{network}-{app}-{suffix}"
            if app == "freshrss":
                return FreshRSS(cleanup, name, network)
            return Miniflux(cleanup, name, network, database, f"feeds_{suffix}")

        source_exports = []
        versions = {}
        for app in SOURCE_MODES:
            instance = create(app, "source")
            versions[app] = instance.version
            initial = instance.import_opml(sample)
            state_check(initial, expected_folders)
            if any(row["paused"] for row in initial):
                raise RuntimeError("A baseline subscription started paused.")
            paused = instance.pause(TARGET)
            state_check(paused, expected_folders)
            if {row["url"] for row in paused if row["paused"]} != {TARGET}:
                raise RuntimeError("The application did not persist exactly one paused subscription.")
            raw = instance.export()
            summary = safe_export(raw, set(expected_folders))
            export = directory / f"{app}-original.opml"
            export.write_bytes(raw)
            dump(directory / f"{app}-source-state.json", {
                "before_pause": ordered(initial),
                "after_pause": ordered(paused),
                "export": summary,
                "export_mode": SOURCE_MODES[app],
            })
            source_exports.append((app, export))
            print(f"Round {index}: recorded source {app} state and original OPML.", flush=True)

        results = []
        for app, export in source_exports:
            origin = "fresh" if app == "freshrss" else "mini"
            for destination in SOURCE_MODES:
                actual = create(destination, origin).import_opml(export)
                state_check(actual, expected_folders)
                if any(row["paused"] for row in actual if row["url"] != TARGET):
                    raise RuntimeError("A non-target control subscription was paused.")
                result = {
                    "source": app,
                    "destination": destination,
                    "target_paused_after_import": next(row["paused"] for row in actual if row["url"] == TARGET),
                    "state": ordered(actual),
                }
                results.append(result)
                dump(directory / f"{app}-to-{destination}.json", result)
                print(f"Round {index}: {app} → {destination} paused={result['target_paused_after_import']}.", flush=True)

    return {"application_versions": versions, "results": results}


def load_sample():
    payload = Path(__file__).with_name("control.opml").read_bytes()
    if hashlib.sha256(payload).hexdigest() != SHA:
        raise RuntimeError("The checked-in sample differed from the approved SHA-256.")
    _, rows, folders = outline_rows(payload)
    if len(rows) != 3 or len(folders) != 2 or TARGET not in {row["url"] for row in rows}:
        raise RuntimeError("The control sample did not contain three feeds and two folders.")
    copy = OUTPUT / "control.opml"
    copy.write_bytes(payload)
    return copy, {row["url"]: row["path"][0] for row in rows}


def pull(image):
    print(f"Pulling {image}.", flush=True)
    docker("pull", image, timeout=150)
    result = docker("image", "inspect", "--format", "{{json .RepoDigests}}", image)
    return json.loads(result.stdout)


def main():
    OUTPUT.mkdir(parents=True, exist_ok=False)
    sample, folders = load_sample()
    images = [FRESH_IMAGE, MINI_IMAGE, DB_IMAGE]
    with ThreadPoolExecutor(max_workers=3) as pool:
        digests = dict(zip(images, pool.map(pull, images), strict=True))
    dump(OUTPUT / "setup.json", {
        "images_and_digests": digests,
        "runner_os": os.environ.get("ImageOS"),
        "runner_image_version": os.environ.get("ImageVersion"),
        "input_url": URL,
        "input_sha256": SHA,
        "paused_target": TARGET,
        "controls": {url: folder for url, folder in folders.items() if url != TARGET},
        "fresh_mute_method": "FreshRSS model _mute(true), DAO updateFeed() with signed TTL; new DAO reads back",
        "miniflux_mute_method": "PUT /v1/feeds/{id} disabled:true, GET /v1/feeds readback",
        "source_export_modes": SOURCE_MODES,
    })
    rounds = [one_round(i, sample, folders) for i in (1, 2)]
    dump(OUTPUT / "comparison.json", {
        "rounds": rounds,
        "identical_rounds": rounds[0] == rounds[1],
        "paused_target": TARGET,
    })
    if rounds[0] != rounds[1]:
        raise RuntimeError("Fresh-instance rounds produced differing observations.")


if __name__ == "__main__":
    main()
