"""Run each matched trial on one disposable GitHub-hosted Docker machine."""
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
from subprocess import run
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler
import xml.etree.ElementTree as ET
from playwright.sync_api import sync_playwright

USER = "filterlab"
PASSWORD = secrets.token_urlsafe(27)
BASIC = b64encode(f"{USER}:{PASSWORD}".encode()).decode()
NETWORK = f"rsslab-{secrets.token_hex(4)}"
DATABASE = f"{NETWORK}-db"
FIXTURE = "http://rss-fixture.test:8090"
FRESH_DIR = "/var/www/FreshRSS"
SOURCE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
ROUND = int(sys.argv[2])
APPS = {"freshrss": "freshrss/freshrss:1.30.4", "miniflux": "miniflux/miniflux:2.3.5"}
IMAGES = {**APPS, "database": "postgres:16.13-alpine", "fixture": "python:3.12-alpine"}
FEEDS = ("retained", "removed")
ITEMS = ("old-title", "old-plain", "new-title", "new-body", "new-web", "new-plain", "after-title")
FRESH_RULE = "intitle:lacuna\nintext:lacuna"
MINI_RULE = "EntryTitle=(?i)lacuna\nEntryContent=(?i)lacuna"
HTTP = build_opener(ProxyHandler({}))


def docker(*args, check=True, timeout=60):
    proc = run(["docker", *args], capture_output=True, timeout=timeout, check=False)
    if check and proc.returncode:
        diagnostic = proc.stderr.decode(errors="replace").replace(PASSWORD, "[redacted]").replace(BASIC, "[redacted]")
        raise RuntimeError(f"docker {args[0]} failed ({proc.returncode}): {diagnostic[-1200:]}")
    return proc


def write_json(file, data):
    file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ready(fn, label, seconds=65):
    deadline = time.monotonic() + seconds
    failure = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, ValueError, RuntimeError) as error:
            failure = error
        time.sleep(0.75)
    raise RuntimeError(f"{label} did not initialize: {failure}")


def pull(image):
    docker("pull", image, timeout=170)
    digests = json.loads(docker("image", "inspect", "--format", "{{json .RepoDigests}}", image).stdout)
    if not digests or "@sha256:" not in digests[0]:
        raise RuntimeError(f"Unable to resolve an immutable image for {image}.")
    return digests[0]


def port(name, inside):
    return int(docker("port", name, f"{inside}/tcp").stdout.decode().strip().split(":")[-1])


def feed_url(name):
    return f"{FIXTURE}/{name}.xml"


def item_url(feed, item):
    return f"{FIXTURE}/article/{feed}-{item}"


def item_key(url):
    if not url.startswith(f"{FIXTURE}/article/"):
        raise RuntimeError(f"Unexpected non-fixture entry URL: {url!r}")
    return urlsplit(url).path.rsplit("/", 1)[-1]


def origin_log():
    return [json.loads(line) for line in (OUT / "fixtures" / "origin.jsonl").read_text().splitlines()]


def make_fixture():
    fixture_dir = OUT / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "origin.jsonl").touch()
    dates = {}
    # These publication times and identifiers stay stable for both repetitions.
    initial = datetime.fromisoformat("2026-11-26T00:00:00+00:00")
    write_json(fixture_dir / "date-anchor.json", {"item_publish_utc": initial.isoformat()})
    manifests = {}
    for number, phase in enumerate(("seed", "filter", "undo")):
        phase_dir = fixture_dir / phase
        phase_dir.mkdir()
        manifest = {}
        modified = format_datetime(initial + timedelta(minutes=number), usegmt=True)
        for feed in FEEDS:
            subset = list(ITEMS[:2] if phase == "seed" else (ITEMS[:-1] if phase == "filter" else ITEMS))
            if phase == "undo" and feed == "removed":
                subset = [item for item in subset if item not in {"new-title", "new-body"}]
            root = ET.Element("rss", version="2.0")
            channel = ET.SubElement(root, "channel")
            for key, value in (
                ("title", f"{feed.title()} example feed"), ("link", FIXTURE), ("description", "Deterministic lab fixture"),
                ("lastBuildDate", modified),
            ):
                ET.SubElement(channel, key).text = value
            for item in subset:
                entry = ET.SubElement(channel, "item")
                suffix = " lacuna" if item.endswith("-title") else ""
                content = "lacuna exists only in the feed body." if item.endswith("-body") else "Fixture body with meadow."
                publish = initial + timedelta(seconds=ITEMS.index(item))
                for key, value in (
                    ("title", f"{feed} {item}{suffix}"), ("link", item_url(feed, item)),
                    ("description", content), ("pubDate", format_datetime(publish, usegmt=True)),
                ):
                    ET.SubElement(entry, key).text = value
                ET.SubElement(entry, "guid", isPermaLink="false").text = f"urn:filter-lab:{feed}:{item}"
            payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            (phase_dir / f"{feed}.xml").write_bytes(payload)
            manifest[feed] = {"guids": [f"urn:filter-lab:{feed}:{item}" for item in subset],
                              "sha256": sha256(payload).hexdigest(), "items": [f"{feed}-{item}" for item in subset]}
        manifests[phase] = manifest
        dates[phase] = modified
    write_json(fixture_dir / "manifest.json", manifests)
    return dates, manifests


def activate(phase, modified):
    active = OUT / "fixtures" / "active.json"
    incoming = active.with_name("incoming.json")
    write_json(incoming, {"phase": phase, "last_modified": modified})
    incoming.replace(active)


def picture(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), animations="disabled", full_page=True, timeout=10000)


def ui_step(page, url, selector):
    response = page.goto(url, wait_until="domcontentloaded", timeout=13000)
    if not response or response.status != 200:
        raise RuntimeError(f"Application did not render {url}.")
    locator = page.locator(selector)
    locator.first.wait_for(state="attached", timeout=6500)
    return locator


def apply_edit(page, field, value, submit):
    field.evaluate("""el => { for (let node = el.parentElement; node; node = node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
    } }""")
    field.fill(value, timeout=6000)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=14000):
        submit.click(timeout=7000)


class Fresh:
    kind = "freshrss"

    def __init__(self, cleanup, tag, image):
        self.name = f"{NETWORK}-fresh-{tag}"
        docker("run", "-d", "--name", self.name, "--network", NETWORK, "-p", "127.0.0.1::80",
               "-e", "FRESHRSS_INSTALL=--db-type sqlite --default-user filterlab --language en",
               "-e", "INTERNAL_HOST_ALLOWLIST=rss-fixture.test:8090",
               "-e", f"FRESHRSS_USER=--user {USER} --password {PASSWORD} --no-default-feeds", image)
        cleanup.callback(docker, "rm", "-fv", self.name, check=False)
        self.url = f"http://127.0.0.1:{port(self.name, 80)}"
        docker("cp", str(SOURCE / "fresh_db.php"), f"{self.name}:/tmp/fresh-db.php")
        start = ready(self.read, "FreshRSS")
        if start["feeds"] or start["rows"] or start["version"] != "1.30.4":
            raise RuntimeError("FreshRSS did not start with the expected clean version.")
        self.version = start["version"]

    def cli(self, *args, **kwargs):
        return docker("exec", "-u", "www-data", "-w", FRESH_DIR, self.name, "php", *args, **kwargs)

    def read(self):
        return json.loads(self.cli("/tmp/fresh-db.php").stdout)

    def import_opml(self, sample):
        docker("cp", str(sample), f"{self.name}:/tmp/input.opml")
        self.cli("cli/import-for-user.php", "--user", USER, "--filename", "/tmp/input.opml", timeout=90)
        self.ids = {row["url"]: row["id"] for row in self.read()["feeds"]}
        if set(self.ids) != {feed_url(feed) for feed in FEEDS}:
            raise RuntimeError(f"FreshRSS OPML import differed: {self.ids!r}")

    def login(self, browser):
        self.context = browser.new_context(viewport={"width": 1200, "height": 800})
        self.page = self.context.new_page()
        field = ui_step(self.page, self.url + "/i/?c=auth&a=login", "input[name=username]")
        field.fill(USER)
        self.page.locator("#passwordPlain").fill(PASSWORD)
        with self.page.expect_navigation(wait_until="domcontentloaded", timeout=14000):
            self.page.locator("#loginButton").click(timeout=7000)

    def rule(self, feed, value):
        key = self.ids[feed_url(feed)]
        url = self.url + f"/i/?c=subscription&a=feed&id={key}"
        field = ui_step(self.page, url, "#filteractions_read")
        submit = self.page.locator("#feed_update form").first.locator("button.btn-important[type=submit]").last
        apply_edit(self.page, field, value, submit)
        saved = next(row["filters"] for row in self.read()["feeds"] if row["id"] == key)
        if set(saved) != set(value.splitlines()):
            raise RuntimeError(f"FreshRSS saved rule differs: {saved!r}.")
        checked = ui_step(self.page, url, "#filteractions_read").input_value().splitlines()
        if set(checked) != set(saved):
            raise RuntimeError("FreshRSS UI did not render the saved rule.")

    def refresh(self, feed):
        key = self.ids[feed_url(feed)]
        self.cli("/tmp/fresh-db.php", "clear-cache", str(key))
        result = self.cli("cli/actualize-user.php", "--user", USER, "--feed-id", str(key), timeout=60)
        return result.stdout.decode(errors="replace").strip()

    def stored(self):
        return {item_key(row["link"]): {"status": "read" if int(row["is_read"]) else "unread",
                                        "source_id": row["guid"], "title": row["title"]}
                for row in self.read()["rows"]}

    def view(self, feed, all_items):
        key = self.ids[feed_url(feed)]
        state = 3 if all_items else 2
        ui_step(self.page, self.url + f"/i/?get=f_{key}&state={state}", "#stream")
        return self.page.locator("#stream .flux[data-link]").evaluate_all(
            "nodes => nodes.map(node => ({url: node.dataset.link, status: node.classList.contains('not_read') ? 'unread' : 'read'}))"
        )


class Mini:
    kind = "miniflux"

    def __init__(self, cleanup, tag, image):
        self.name = f"{NETWORK}-mini-{tag}"
        self.database = f"feeds_{tag}"
        docker("exec", DATABASE, "createdb", "-U", USER, self.database)
        docker("run", "-d", "--name", self.name, "--network", NETWORK, "-p", "127.0.0.1::8080",
               "-e", f"DATABASE_URL=postgres://{USER}:{PASSWORD}@{DATABASE}/{self.database}?sslmode=disable",
               "-e", "RUN_MIGRATIONS=1", "-e", "CREATE_ADMIN=1", "-e", f"ADMIN_USERNAME={USER}",
               "-e", f"ADMIN_PASSWORD={PASSWORD}", "-e", "POLLING_FREQUENCY=3600",
               "-e", "FETCHER_ALLOW_PRIVATE_NETWORKS=1", image)
        cleanup.callback(docker, "rm", "-fv", self.name, check=False)
        self.url = f"http://127.0.0.1:{port(self.name, 8080)}"
        self.version = ready(lambda: self.request("GET", "/v1/version").decode().strip().strip('"'), "Miniflux")
        if self.version.startswith("{"):
            self.version = json.loads(self.version)["version"]
        if self.version != "2.3.5" or self.read_feeds() or self.stored():
            raise RuntimeError(f"Miniflux did not start with a clean expected version: {self.version!r}.")

    def request(self, method, path, data=None, media=None):
        headers = {"Authorization": f"Basic {BASIC}"}
        if media:
            headers["Content-Type"] = media
        request = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with HTTP.open(request, timeout=25) as response:
                return response.read()
        except HTTPError as error:
            detail = error.read().decode(errors="replace").replace(PASSWORD, "[redacted]").replace(BASIC, "[redacted]")
            raise RuntimeError(f"Miniflux {method} {path}: HTTP {error.code} {detail[:260]}") from None

    def read_feeds(self):
        return json.loads(self.request("GET", "/v1/feeds"))

    def import_opml(self, sample):
        self.request("POST", "/v1/import", sample.read_bytes(), "application/xml")
        def imported():
            ids = {row["feed_url"]: row["id"] for row in self.read_feeds()}
            return ids if set(ids) == {feed_url(feed) for feed in FEEDS} else None
        self.ids = ready(imported, "Miniflux OPML import", seconds=28)
        for key in self.ids.values():
            settings = json.dumps({"ignore_http_cache": True, "crawler": False}).encode()
            self.request("PUT", f"/v1/feeds/{key}", settings, "application/json")

    def login(self, browser):
        self.context = browser.new_context(viewport={"width": 1200, "height": 800})
        self.page = self.context.new_page()
        field = ui_step(self.page, self.url + "/", "input[name=username]")
        field.fill(USER)
        self.page.locator("input[name=password]").fill(PASSWORD)
        with self.page.expect_navigation(wait_until="domcontentloaded", timeout=14000):
            field.locator("xpath=ancestor::form").locator("button[type=submit], input[type=submit]").first.click(timeout=7000)

    def rule(self, feed, value):
        key = self.ids[feed_url(feed)]
        url = self.url + f"/feed/{key}/edit"
        field = ui_step(self.page, url, "#form-block-filter-rules")
        submit = field.locator("xpath=ancestor::form").locator("button[type=submit], input[type=submit]").last
        apply_edit(self.page, field, value, submit)
        saved = next(row for row in self.read_feeds() if row["id"] == key)
        if saved["block_filter_entry_rules"].replace("\r\n", "\n").strip() != value:
            raise RuntimeError(f"Miniflux saved rule differs: {saved['block_filter_entry_rules']!r}.")
        if saved["crawler"] or not saved["ignore_http_cache"]:
            raise RuntimeError("Miniflux default source-only or HTTP cache controls changed.")
        if ui_step(self.page, url, "#form-block-filter-rules").input_value().strip() != value:
            raise RuntimeError("Miniflux UI did not render the saved rule.")

    def refresh(self, feed):
        key = self.ids[feed_url(feed)]
        self.request("PUT", f"/v1/feeds/{key}/refresh")
        return "synchronous ordinary feed refresh"

    def stored(self):
        query = ("BEGIN READ ONLY; SELECT COALESCE(json_agg(rows),'[]'::json) FROM "
                 "(SELECT url,title,status,hash AS source_id FROM entries ORDER BY url) AS rows; COMMIT;")
        result = docker("exec", DATABASE, "psql", "-XqtA", "-v", "ON_ERROR_STOP=1", "-U", USER, "-d", self.database, "-c", query)
        return {item_key(row.pop("url")): row for row in json.loads(result.stdout)}

    def view(self, feed, all_items):
        key = self.ids[feed_url(feed)]
        ui_step(self.page, self.url + f"/feed/{key}/entries" + ("/all" if all_items else ""), "main")
        rows = self.page.locator("article.entry-item[data-id]").evaluate_all(
            """nodes => nodes.map(node => ({
                title: node.querySelector('.item-title a').textContent.trim(),
                status: node.classList.contains('item-status-unread') ? 'unread' : 'read'
            }))"""
        )
        known = {row["title"]: f"{FIXTURE}/article/{key}" for key, row in self.stored().items()}
        return [{"url": known[row["title"]], "status": row["status"]} for row in rows]


def verify_refresh(app, phase, feed, manifests):
    offset = len(origin_log())
    receipt = app.refresh(feed)
    new_records = origin_log()[offset:]
    matching = [row for row in new_records if row["phase"] == phase and row["path"] == f"/{feed}.xml"
                and row["method"] == "GET" and row["sent_body"] and row["response_status"] == 200]
    expected = manifests[phase][feed]
    if not matching or any(row["item_guids"] != expected["guids"] or row["sha256"] != expected["sha256"] for row in matching):
        raise RuntimeError(f"{app.kind} {phase} {feed}: no verified source HTTP 200 with expected GUIDs: {new_records!r}")
    return {"app_refresh": receipt, "origin_receipts": matching}


def snapshot(app, phase, manifests, name):
    start = app.stored()
    observed = {}
    for feed in FEEDS:
        all_items = {item_key(row["url"]): row["status"] for row in app.view(feed, all_items=True)}
        expected = {key: row["status"] for key, row in start.items() if key.startswith(f"{feed}-") and row["status"] != "removed"}
        if all_items != expected:
            raise RuntimeError(f"{app.kind} {phase} {feed}: all-items UI {all_items!r} vs database {expected!r}")
        if phase in {"filter", "undo"} and name == "primary":
            picture(app.page, f"{app.kind}-{phase}-{feed}-all")
        unread = {item_key(row["url"]): row["status"] for row in app.view(feed, all_items=False)}
        if unread != {key: value for key, value in expected.items() if value == "unread"}:
            raise RuntimeError(f"{app.kind} {phase} {feed}: unread UI {unread!r} inconsistent with all-items view.")
        if phase == "filter" and name == "primary":
            picture(app.page, f"{app.kind}-{phase}-{feed}-unread")
        observed[feed] = {"all": all_items, "unread": unread, "source_items": manifests[phase][feed]["items"]}
    if start != app.stored():
        raise RuntimeError("Opening article list unexpectedly changed stored read status.")
    return {"database": start, "views": observed}


def capture_snapshot(app, phase, manifests, name):
    receipts = {feed: verify_refresh(app, phase, feed, manifests) for feed in FEEDS}
    state = snapshot(app, phase, manifests, name)
    path = OUT / f"{app.kind}-{name}-{phase}.json"
    write_json(path, {"refresh": receipts, "observation": state})
    return state


def main():
    OUT.mkdir(parents=True)
    dates, manifests = make_fixture()
    fixture_export = OUT / "sample.opml"
    fixture_export.write_bytes((SOURCE / "sample.opml").read_bytes())
    activate("seed", dates["seed"])
    progress = {"round": ROUND, "status": "started", "sample_sha256": sha256(fixture_export.read_bytes()).hexdigest(),
                "platform": os.environ.get("ImageOS"), "runner_image": os.environ.get("ImageVersion"), "phases": []}
    write_json(OUT / "summary.json", progress)
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = dict(zip(IMAGES, pool.map(pull, IMAGES.values()), strict=True))
    progress["images"] = images
    write_json(OUT / "summary.json", progress)
    try:
        with sync_playwright() as playwright, ExitStack() as cleanup:
            docker("network", "create", NETWORK)
            cleanup.callback(docker, "network", "rm", NETWORK, check=False)
            names = {letter: f"{NETWORK}-{letter}" for letter in ("fixture", "db")}
            docker("run", "-d", "--name", names["fixture"], "--network", NETWORK, "--network-alias", "rss-fixture.test", "-v",
                   f"{OUT / 'fixtures'}:/fixture", "-v", f"{SOURCE / 'fixture_server.py'}:/lab/fixture_server.py:ro",
                   images["fixture"], "python", "/lab/fixture_server.py")
            cleanup.callback(docker, "rm", "-fv", names["fixture"], check=False)
            docker("run", "-d", "--name", DATABASE, "--network", NETWORK,
                   "-e", f"POSTGRES_USER={USER}", "-e", f"POSTGRES_PASSWORD={PASSWORD}", images["database"])
            cleanup.callback(docker, "rm", "-fv", DATABASE, check=False)
            ready(lambda: docker("exec", DATABASE, "pg_isready", "-h", DATABASE, "-U", USER,
                                 check=False).returncode == 0, "PostgreSQL")
            browser = playwright.chromium.launch(headless=True)
            cleanup.callback(browser.close)
            instances = (Fresh(cleanup, "primary", images["freshrss"]), Mini(cleanup, "primary", images["miniflux"]))
            progress["versions"] = {app.kind: app.version for app in instances}
            for app in instances:
                app.import_opml(fixture_export)
                app.login(browser)
            all_states = {}
            for phase in ("seed", "filter", "undo"):
                for app in instances:
                    if phase != "seed":
                        rule = (FRESH_RULE if isinstance(app, Fresh) else MINI_RULE) if phase == "filter" else ""
                        for feed in FEEDS:
                            app.rule(feed, rule)
                activate(phase, dates[phase])
                all_states[phase] = {}
                for app in instances:
                    all_states[phase][app.kind] = capture_snapshot(app, phase, manifests, "primary")
                    print(f"Round {ROUND}: confirmed {app.kind} {phase} origin, storage and list readback.", flush=True)
                progress["phases"].append(phase)
                write_json(OUT / "summary.json", progress)
            controls = (Fresh(cleanup, "control", images["freshrss"]), Mini(cleanup, "control", images["miniflux"]))
            for app in controls:
                app.import_opml(fixture_export)
                app.login(browser)
                observed = capture_snapshot(app, "undo", manifests, "clean-control")
                expected = {item for feed in FEEDS for item in manifests["undo"][feed]["items"]}
                if set(observed["database"]) != expected or any(row["status"] != "unread" for row in observed["database"].values()):
                    raise RuntimeError(f"{app.kind} zero-rule positive control disagrees with source.")
            progress["states"] = all_states
            progress["controls"] = "Both clean rule-free instances stored every item in the final XML, all unread."
            progress["status"] = "complete"
    except Exception as error:
        progress["failure"] = str(error).replace(PASSWORD, "[redacted]").replace(BASIC, "[redacted]")
        raise
    finally:
        write_json(OUT / "summary.json", progress)


if __name__ == "__main__":
    main()
