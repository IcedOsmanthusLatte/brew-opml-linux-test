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
NETWORK = f"rsslab-{secrets.token_hex(4)}"
FIXTURE = "http://rss-fixture.test:8090"
FRESH_DIR = "/var/www/FreshRSS"
SOURCE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]).resolve()
ROUND = int(sys.argv[2])
EXPECTED_VERSION = "1.31.0"
IMAGES = {"freshrss": f"freshrss/freshrss:{EXPECTED_VERSION}", "fixture": "python:3.12-alpine"}
FEEDS = ("unchanged", "updated", "stale", "recovery")
HTTP = build_opener(ProxyHandler({}))


def docker(*args, check=True, timeout=60):
    proc = run(["docker", *args], capture_output=True, timeout=timeout, check=False)
    if check and proc.returncode:
        diagnostic = proc.stderr.decode(errors="replace").replace(PASSWORD, "[redacted]")
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


def picture(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), animations="disabled", full_page=True, timeout=10000)


def ui_step(page, url, selector):
    response = page.goto(url, wait_until="domcontentloaded", timeout=13000)
    if not response or response.status != 200:
        raise RuntimeError(f"Application did not render {url}.")
    locator = page.locator(selector)
    locator.first.wait_for(state="attached", timeout=6500)
    return locator


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
        if start["feeds"] or start["rows"] or start["version"] != EXPECTED_VERSION:
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



def write_inputs():
    fixture_dir = OUT / "fixtures"
    fixture_dir.mkdir()
    (fixture_dir / "origin.jsonl").touch()
    initial = datetime.fromisoformat("2026-11-27T04:00:00+00:00")
    manifest = {}
    for generation in ("warm", "new"):
        folder = fixture_dir / generation
        folder.mkdir()
        manifest[generation] = {}
        for name in FEEDS:
            feed = ET.Element("rss", version="2.0")
            channel = ET.SubElement(feed, "channel")
            for key, value in (
                ("title", f"RSS cache fixture: {name}"), ("link", FIXTURE),
                ("description", "Deterministic subscription test sample."),
            ):
                ET.SubElement(channel, key).text = value
            labels = ["baseline"] if generation == "warm" or name == "unchanged" else ["baseline", "latest"]
            guids = []
            for index, label in enumerate(labels):
                entry = ET.SubElement(channel, "item")
                guid = f"urn:brew-cache-lab:{name}:{label}"
                guids.append(guid)
                for key, value in (
                    ("title", f"{name} {label} article"), ("link", item_url(name, label)),
                    ("description", "Identifiable body for an isolated, synthetic test."),
                    ("pubDate", format_datetime(initial + timedelta(minutes=index), usegmt=True)),
                ):
                    ET.SubElement(entry, key).text = value
                ET.SubElement(entry, "guid", isPermaLink="false").text = guid
            payload = ET.tostring(feed, encoding="utf-8", xml_declaration=True)
            (folder / f"{name}.xml").write_bytes(payload)
            manifest[generation][name] = {"guids": guids, "sha256": sha256(payload).hexdigest()}
    write_json(fixture_dir / "manifest.json", manifest)
    opml = ET.Element("opml", version="2.0")
    ET.SubElement(ET.SubElement(opml, "head"), "title").text = "RSS cache diagnostics"
    body = ET.SubElement(opml, "body")
    for name in FEEDS:
        ET.SubElement(body, "outline", text=name, title=name, type="rss", xmlUrl=feed_url(name))
    path = OUT / "input.opml"
    ET.ElementTree(opml).write(path, encoding="utf-8", xml_declaration=True)
    return path, manifest


def phase(name):
    active = OUT / "fixtures" / "phase.txt"
    incoming = active.with_name("phase.next")
    incoming.write_text(name, encoding="ascii")
    incoming.replace(active)


def fresh_update(app, feed):
    key = app.ids[feed_url(feed)]
    ui_step(app.page, app.url + f"/i/?c=subscription&a=feed&id={key}", "#feed_update")
    control = app.page.locator('button[formaction*="a=actualize"]')
    label = control.inner_text(timeout=7000).strip()
    with app.page.expect_response(
        lambda reply: "a=actualize" in reply.url and reply.request.method == "POST", timeout=14000
    ) as capture:
        control.click(timeout=7000)
    response = capture.value
    if response.status not in {200, 302, 303}:
        raise RuntimeError(f"FreshRSS Update returned {response.status}.")
    return {"path": "FreshRSS Manage > Update", "button_label": label, "status": response.status}



def ordinary_refresh(app, feed):
    return fresh_update(app, feed)


def recovery_refresh(app, feed):
    key = app.ids[feed_url(feed)]
    ui_step(app.page, app.url + f"/i/?c=subscription&a=feed&id={key}", "#feed_update")
    control = app.page.get_by_role("button", name="Clear cache", exact=False)
    if control.count() != 1:
        control = app.page.locator('button[formaction*="clearCache"], form[action*="clearCache"] button')
    if control.count() != 1:
        buttons = app.page.locator("button").all_text_contents()
        raise RuntimeError(f"FreshRSS per-feed clear cache control was not unique. Buttons: {buttons}")
    control.evaluate("""el => {for (let node = el.parentElement; node; node = node.parentElement) {
        if (node.tagName === 'DETAILS') node.open = true;
    }}""")
    label = control.inner_text(timeout=7000).strip()
    app.page.once("dialog", lambda dialog: dialog.accept())
    with app.page.expect_navigation(wait_until="domcontentloaded", timeout=14000), app.page.expect_response(
        lambda reply: "a=clearCache" in reply.url and reply.request.method == "POST", timeout=14000,
    ) as clear:
        control.click(timeout=7000)
    response = clear.value
    if response.status not in {200, 302, 303}:
        raise RuntimeError(f"FreshRSS Clear cache returned {response.status}.")
    return {"path": "FreshRSS Manage feed > Maintenance > Clear cache, then single-feed refresh",
            "clear_label": label, "clear_status": response.status, "clear_method": response.request.method,
            "refresh": fresh_update(app, feed)}


def view(app, feed):
    all_rows = app.view(feed, all_items=True)
    unread = app.view(feed, all_items=False)
    stored = {key: value["status"] for key, value in app.stored().items() if key.startswith(f"{feed}-")}
    all_status = {item_key(row["url"]): row["status"] for row in all_rows}
    unread_status = {item_key(row["url"]): row["status"] for row in unread}
    if all_status != stored or unread_status != {k: v for k, v in stored.items() if v == "unread"}:
        raise RuntimeError(f"{app.kind} {feed}: article view {all_status}, unread {unread_status}, DB {stored}")
    return {"all": all_status, "unread": unread_status}


def request_log(offset, feed):
    return [entry for entry in origin_log()[offset:] if entry["path"] == f"/{feed}.xml" and entry["method"] == "GET"]


def capture(app, feed, stage, refresh, find_origin=True):
    offset = len(origin_log())
    before_feeds = app.read()["feeds"]
    result = refresh(app, feed)
    records = request_log(offset, feed)
    # FreshRSS can keep a local RSS cache briefly. Let the regular path expire it naturally.
    if find_origin and isinstance(app, Fresh) and refresh is ordinary_refresh and not records:
        deadline = time.monotonic() + 100
        retry_count = 0
        while not records and time.monotonic() < deadline:
            time.sleep(5)
            retry_count += 1
            result = refresh(app, feed)
            records = request_log(offset, feed)
        result["waited_for_expiry_retries"] = retry_count
    after_feeds = app.read()["feeds"]
    if before_feeds != after_feeds:
        raise RuntimeError(f"FreshRSS altered existing subscriptions during {stage}/{feed}.")
    observed = {"refresh": result, "origin": records, "views": view(app, feed),
                "same_subscriptions": True, "subscription_count": len(after_feeds),
                "subscription": next(row for row in after_feeds if row["url"] == feed_url(feed))}
    write_json(OUT / f"{app.kind}-{stage}-{feed}.json", observed)
    if stage in {"ordinary", "recovery"} and feed in {"stale", "recovery"}:
        picture(app.page, f"{app.kind}-{stage}-{feed}")
    return observed


def direct_probe(fixture_public, feed, manifest):
    rounds = []
    for label, headers in (
        ("ordinary_get", {"User-Agent": "Brew synthetic probe"}),
        ("cached_reader_get", {"User-Agent": "Brew synthetic probe", "If-None-Match": f'"{feed}-warm"'}),
    ):
        request = Request(f"{fixture_public}/{feed}.xml", headers=headers)
        try:
            with HTTP.open(request, timeout=8) as response:
                row = {"name": label, "status": response.status, "etag": response.headers["ETag"],
                       "sha256": sha256(response.read()).hexdigest()}
        except HTTPError as error:
            if error.code != 304:
                raise
            row = {"name": label, "status": error.code, "etag": error.headers["ETag"],
                   "sha256": None if not error.read() else "unexpected body"}
        rounds.append(row)
    if rounds[0]["status"] != 200 or rounds[0]["sha256"] != manifest["new"][feed]["sha256"]:
        raise RuntimeError(f"Raw HTTP fixture XML did not match its saved bytes: {rounds}")
    conditional_status = 200 if feed == "updated" else 304
    if rounds[1]["status"] != conditional_status or (conditional_status == 304 and rounds[1]["sha256"] is not None):
        raise RuntimeError(f"Raw HTTP conditional fixture probe disagreed: {rounds}")
    return rounds


def check_slice(app, feed, observation, expected_status, expected_latest, expect_condition, manifest):
    required_entries = {f"{feed}-baseline": "unread"}
    if expected_latest:
        required_entries[f"{feed}-latest"] = "unread"
    origin = observation["origin"]
    expected_count = 1
    if len(origin) != expected_count:
        raise RuntimeError(f"{app.kind} {feed}: expected {expected_count} controlled feed GET(s), got {origin}")
    record = origin[0]
    generation = "warm" if record["phase"] == "warm" or feed == "unchanged" else "new"
    source = manifest[generation][feed]
    requested_tag = f'"{feed}-warm"' if expect_condition else None
    if (
        record["status"] != expected_status or record["if_none_match"] != requested_tag
        or record["sent_body"] != (expected_status == 200)
        or record["sha256"] != source["sha256"] or record["source_guids"] != source["guids"]
        or observation["views"]["all"] != required_entries
        or any(entry["status"] != 200 or entry["if_none_match"] is not None or
               entry["sha256"] != source["sha256"] or entry["source_guids"] != source["guids"]
               for entry in origin[1:])
    ):
        raise RuntimeError(f"{app.kind} {feed}: expectation disagrees with recorded test: {observation}")


def main():
    OUT.mkdir(parents=True)
    sample, manifest = write_inputs()
    phase("warm")
    progress = {"round": ROUND, "status": "running", "runner_image": os.environ.get("ImageVersion"), "observations": {}}
    summary_path = OUT / "summary.json"
    write_json(summary_path, progress)
    with ThreadPoolExecutor(max_workers=4) as pool:
        images = dict(zip(IMAGES, pool.map(pull, IMAGES.values()), strict=True))
    progress["images"] = images
    write_json(summary_path, progress)
    try:
        with sync_playwright() as playwright, ExitStack() as cleanup:
            docker("network", "create", NETWORK)
            cleanup.callback(docker, "network", "rm", NETWORK, check=False)
            fixture = f"{NETWORK}-fixture"
            docker("run", "-d", "--name", fixture, "--network", NETWORK, "--network-alias", "rss-fixture.test",
                   "-p", "127.0.0.1::8090", "-v", f"{OUT / 'fixtures'}:/fixture",
                   "-v", f"{SOURCE / 'fixture_server.py'}:/lab/fixture_server.py:ro",
                   images["fixture"], "python", "/lab/fixture_server.py")
            cleanup.callback(docker, "rm", "-fv", fixture, check=False)
            fixture_public = f"http://127.0.0.1:{port(fixture, 8090)}"
            ready(lambda: HTTP.open(fixture_public + "/", timeout=3).status == 200, "Fixture server")
            browser = playwright.chromium.launch(headless=True)
            cleanup.callback(browser.close)
            apps = (Fresh(cleanup, "test", images["freshrss"]),)
            progress["versions"] = {app.kind: app.version for app in apps}
            for app in apps:
                app.import_opml(sample)
                app.login(browser)
                app_warm = {}
                for feed in FEEDS:
                    observation = capture(app, feed, "warm", ordinary_refresh, find_origin=False)
                    matches = observation["views"]["all"] == {f"{feed}-baseline": "unread"}
                    if not matches:
                        raise RuntimeError(f"{app.kind} {feed} did not warm expected baseline: {observation}")
                    app_warm[feed] = observation
                progress["observations"][app.kind] = {"warm": app_warm}
                write_json(summary_path, progress)
            phase("new")
            progress["direct_fixture_probes"] = {name: direct_probe(fixture_public, name, manifest) for name in FEEDS}
            write_json(summary_path, progress)
            for app in apps:
                app_observations = progress["observations"][app.kind]
                app_observations["ordinary"] = {
                    feed: capture(app, feed, "ordinary", ordinary_refresh)
                    for feed in ("unchanged", "updated", "stale")
                }
                app_observations["recovery"] = {}
                for feed in ("recovery", "stale"):
                    try:
                        app_observations["recovery"][feed] = capture(app, feed, "recovery", recovery_refresh)
                    except Exception as error:
                        app_observations["recovery"][feed] = {"diagnostic": str(error)}
                checks = {}
                for step, observations in (("ordinary", app_observations["ordinary"]),
                                           ("recovery", app_observations["recovery"])):
                    for feed, observation in observations.items():
                        expected_latest = step == "recovery" or feed == "updated"
                        try:
                            check_slice(app, feed, observation, 200 if expected_latest else 304,
                                        expected_latest, step == "ordinary", manifest)
                            checks[f"{step}/{feed}"] = "matches controlled expectation"
                        except (KeyError, RuntimeError) as error:
                            checks[f"{step}/{feed}"] = str(error)
                app_observations["checks"] = checks
                write_json(summary_path, progress)
                if any(value != "matches controlled expectation" for value in checks.values()):
                    raise RuntimeError(f"Some observations did not meet expectations: {checks}")
                print(f"{app.kind} {app.version}: recorded origin and article views", flush=True)
        progress["status"] = "complete"
    except Exception as error:
        progress["status"] = "failed"
        progress["diagnostic"] = str(error).replace(PASSWORD, "[redacted]")
        raise
    finally:
        write_json(summary_path, progress)


if __name__ == "__main__":
    main()
