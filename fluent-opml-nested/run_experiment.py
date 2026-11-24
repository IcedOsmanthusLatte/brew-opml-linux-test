"""Import one OPML through Fluent Reader's native UI on a disposable Windows runner."""

import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import traceback
from urllib.request import urlopen
from xml.etree import ElementTree

from playwright.sync_api import sync_playwright
from pywinauto import Desktop, keyboard
import win32clipboard


CONTROL = "https://wordpress.org/news/feed/"
GROUPS = ("Brew Parent", "Brew Child", "Brew Control")
PORT = 9250
UI_TIMEOUT = 9_000


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_until(check, reason, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
        except (OSError, sqlite3.OperationalError):
            result = None
        if result is not None and result is not False:
            return result
        time.sleep(0.25)
    raise RuntimeError(f"Timed out: {reason}")


def read_opml(path):
    root = ElementTree.parse(path).getroot()
    body = root.find("body")
    if root.tag != "opml" or body is None:
        raise ValueError("The file is not an OPML document with a body")
    feeds = []
    groups = []

    def visit(parent, group_path=()):
        for outline in parent.findall("outline"):
            address = outline.get("xmlUrl")
            if address:
                feeds.append({"url": address, "group_path": list(group_path)})
            else:
                name = outline.get("text") or outline.get("title")
                if not name:
                    raise ValueError("Folder missing text/title")
                nested = group_path + (name,)
                groups.append(list(nested))
                visit(outline, nested)

    visit(body)
    return {
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "feeds": feeds,
        "groups": groups,
        "addresses": sorted(record["url"] for record in feeds),
    }


def read_database(profile):
    path = profile / "fluent-reader.db"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        addresses = [url for url, in db.execute("SELECT url FROM sources ORDER BY url")]
        article_count, = db.execute("SELECT COUNT(*) FROM items").fetchone()
    return {"addresses": addresses, "count": len(addresses), "article_count": article_count}


def open_windows(pid, kind=None):
    windows = Desktop(backend="win32").windows(class_name=kind) if kind else Desktop(backend="win32").windows()
    return [window for window in windows if window.process_id() == pid and window.is_visible()]


def file_picker(path, pid, save=False):
    def find_picker():
        for window in open_windows(pid, "#32770"):
            edits = [edit for edit in window.descendants(class_name="Edit") if edit.is_visible()]
            if edits:
                editable = next((edit for edit in edits if edit.control_id() == 1148), edits[-1])
                return window, editable
        return None

    window, edit = wait_until(find_picker, "Fluent Reader's native file picker")
    window.set_focus()
    edit.set_focus()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(path), win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    keyboard.send_keys("^a^v", pause=0.15)
    if edit.window_text().strip().strip('"') != str(path):
        raise RuntimeError("Native picker did not contain the designated file path")
    handle = window.handle
    picker_title = window.window_text()
    keyboard.send_keys("{ENTER}", pause=0.15)
    wait_until(
        lambda: (save and path.is_file()) or not any(w.handle == handle for w in open_windows(pid, "#32770")),
        "native file picker to close",
        15,
    )
    return {"handle": handle, "title": picker_title, "closed": True}


def endpoint_ready():
    with urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1) as result:
        return result.status == 200


def start_app(playwright, executable, profile):
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            str(executable),
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={PORT}",
            "--remote-debugging-address=127.0.0.1",
        ],
        cwd=executable.parent,
    )
    try:
        wait_until(endpoint_ready, "Fluent Reader renderer")
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}", timeout=UI_TIMEOUT)
        page = browser.contexts[0].pages[0]
        page.get_by_role("button", name="Settings", exact=True).wait_for(timeout=UI_TIMEOUT)
        return process, page
    except Exception:
        stop_app(process)
        raise


def stop_app(process):
    if process.poll() is not None:
        return "already_exited"
    for window in open_windows(process.pid, "Chrome_WidgetWin_1"):
        window.post_message(0x0010)  # WM_CLOSE
    try:
        process.wait(timeout=5)
        return "window_close"
    except subprocess.TimeoutExpired:
        # The application may leave a background Electron process alive after WM_CLOSE.
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=12,
        )
        process.wait(timeout=8)
        return "runner_process_tree_exit"


def native_modals(pid):
    dialogs = []
    for window in open_windows(pid, "#32770"):
        statics = window.descendants(class_name="Static")
        buttons = window.descendants(class_name="Button")
        dialogs.append(
            {
                "handle": window.handle,
                "title": window.window_text(),
                "text": [control.window_text() for control in statics if control.window_text()],
                "buttons": [button.window_text() for button in buttons if button.window_text()],
            }
        )
    return dialogs


def renderer_state(page):
    return page.evaluate(
        """(groups) => {
          const visible = el => Boolean(el.getClientRects().length);
          const text = el => (el.innerText || el.getAttribute('aria-label') || '').trim();
          const candidates = [...document.querySelectorAll('span,div,li,td,button')];
          const labels = groups.filter(label =>
            candidates.some(el => !el.children.length && visible(el) && text(el) === label));
          return {
            known_group_labels: labels,
            alerts: [...document.querySelectorAll('[role="alert"],[role="alertdialog"]')]
              .filter(visible).map(text).filter(Boolean),
            busy_indicators: [...document.querySelectorAll('[role="progressbar"],progress,[aria-busy="true"]')]
              .filter(visible).length
          };
        }""",
        list(GROUPS),
    )


def track_feed_requests(page, addresses):
    observed = {}

    def root_url(request):
        while request.redirected_from is not None:
            request = request.redirected_from
        return request.url

    def register(request):
        address = root_url(request)
        if address in addresses:
            observed[request] = {"input_url": address, "state": "pending"}

    def settle(request, status):
        if request in observed:
            observed[request]["state"] = status

    page.on("request", register)
    page.on("requestfinished", lambda request: settle(request, "finished"))
    page.on("requestfailed", lambda request: settle(request, "failed"))
    return observed


def observe_import(page, process, profile, requests, trial, evidence):
    deadline = time.monotonic() + 80
    quiet_since = None
    previous_addresses = None
    modal_handles = set()
    while time.monotonic() < deadline:
        page.wait_for_timeout(300)
        runtime_ui = renderer_state(page)
        for modal in native_modals(process.pid):
            if modal["handle"] not in modal_handles:
                modal_handles.add(modal["handle"])
                trial["native_errors"].append(modal)
        for alert in runtime_ui["alerts"]:
            if alert not in trial["renderer_alerts"]:
                trial["renderer_alerts"].append(alert)
        if trial["native_errors"]:
            trial["after"] = read_database(profile)
            trial["after_ui"] = runtime_ui
            raise RuntimeError("Native dialog appeared after OPML submission")
        snapshot = read_database(profile)
        if not snapshot:
            continue
        unsettled = any(item["state"] == "pending" for item in requests.values())
        addresses = snapshot["addresses"]
        if CONTROL not in addresses or unsettled or runtime_ui["busy_indicators"]:
            quiet_since = None
            continue
        if addresses != previous_addresses or quiet_since is None:
            quiet_since = time.monotonic()
        previous_addresses = addresses
        if time.monotonic() - quiet_since >= 8:
            trial["after"] = snapshot
            trial["after_ui"] = runtime_ui
            trial["import_completion"] = {
                "native_picker_closed": True,
                "wordPress_control_present": True,
                "quiet_window_seconds": 8,
                "pending_feed_requests": 0,
                "busy_indicators": 0,
            }
            save_json(evidence / "trial.json", trial)
            return
    trial["after"] = read_database(profile)
    trial["after_ui"] = renderer_state(page)
    raise RuntimeError("Import did not reach the control-plus-quiescence completion gate")


def verify_fixture(fixture, condition):
    if len(fixture["addresses"]) != 3 or len(set(fixture["addresses"])) != 3:
        raise ValueError("Each fixture must have three distinct addresses")
    target = ["Brew Parent"] if condition == "single" else ["Brew Parent", "Brew Child"]
    for feed in fixture["feeds"]:
        expected = ["Brew Control"] if feed["url"] == CONTROL else target
        if feed["group_path"] != expected:
            raise ValueError(f"Fixture depth mismatch for {feed['url']}")
    if CONTROL not in fixture["addresses"]:
        raise ValueError("Common WordPress completion control was missing")


def execute(args, evidence, trial):
    executable = Path(os.environ["BREW_WIN_FLUENT"]).resolve(strict=True)
    fixture = read_opml(args.fixture)
    verify_fixture(fixture, args.condition)
    trial["fixture_sha256"] = fixture["sha256"]
    trial["fixture_urls"] = fixture["addresses"]
    trial["fixture_groups"] = fixture["groups"]
    trial["github_run_id"] = os.environ.get("GITHUB_RUN_ID")
    trial["runner_image"] = os.environ.get("ImageVersion")
    trial["executable_sha256"] = sha256(executable.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="fluent-opml-", dir=os.environ["RUNNER_TEMP"]) as temp:
        profile = Path(temp) / "electron"
        with sync_playwright() as playwright:
            process, page = start_app(playwright, executable, profile)
            try:
                trial["initial_pid"] = process.pid
                trial["before"] = wait_until(lambda: read_database(profile), "fresh library database")
                trial["empty_before"] = trial["before"]["count"] == trial["before"]["article_count"] == 0
                if not trial["empty_before"]:
                    raise RuntimeError("The new profile did not start from an empty library")
                page.get_by_role("button", name="Settings", exact=True).click(timeout=UI_TIMEOUT)
                trial["before_ui"] = renderer_state(page)
                requests = track_feed_requests(page, set(fixture["addresses"]))
                page.get_by_role("button", name="Import", exact=True).click(timeout=UI_TIMEOUT)
                trial["native_picker"] = file_picker(args.fixture, process.pid)
                try:
                    observe_import(page, process, profile, requests, trial, evidence)
                finally:
                    trial["feed_requests"] = list(requests.values())
            finally:
                trial["exit_method"] = stop_app(process)
            process, page = start_app(playwright, executable, profile)
            try:
                trial["restarted_pid"] = process.pid
                if process.pid == trial["initial_pid"]:
                    raise RuntimeError("The restart did not open a new application process")
                page.get_by_role("button", name="Settings", exact=True).click(timeout=UI_TIMEOUT)
                trial["after_restart"] = wait_until(lambda: read_database(profile), "restarted library database")
                trial["after_restart_ui"] = renderer_state(page)
                trial["restart_persisted"] = trial["after_restart"]["addresses"] == trial["after"]["addresses"]
                if not trial["restart_persisted"]:
                    raise RuntimeError("Persisted address set differed after restart")
                output = evidence / "native-export.opml"
                page.get_by_role("button", name="Export", exact=True).click(timeout=UI_TIMEOUT)
                trial["native_export_picker"] = file_picker(output, process.pid, save=True)
                wait_until(lambda: output.is_file() and output.stat().st_size > 0, "native export file")
                trial["native_export"] = read_opml(output)
                if trial["native_export"]["addresses"] != trial["after_restart"]["addresses"]:
                    raise RuntimeError("Native export and restarted read-only SQLite address set disagreed")
            finally:
                trial["restart_exit_method"] = stop_app(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", required=True, choices=("single", "double"))
    parser.add_argument("--repeat", required=True, type=int, choices=(1, 2))
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        parser.error("The experiment can only use its isolated GitHub Actions Windows runner")
    args.fixture = args.fixture.resolve(strict=True)
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    if any((evidence / name).exists() for name in ("trial.json", "native-export.opml")):
        parser.error("The evidence folder already contains a trial result or export")
    trial = {
        "condition": args.condition,
        "repeat": args.repeat,
        "status": "running",
        "native_errors": [],
        "renderer_alerts": [],
    }
    try:
        execute(args, evidence, trial)
    except Exception as error:
        trial["status"] = "incomplete"
        trial["failure"] = {
            "type": type(error).__name__,
            "message": str(error) if isinstance(error, (ValueError, RuntimeError)) else None,
            "positions": [
                {"line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(error.__traceback__)
                if Path(frame.filename).name == Path(__file__).name
            ],
        }
        save_json(evidence / "trial.json", trial)
        print(json.dumps(trial["failure"], ensure_ascii=True))
        return 1
    trial["status"] = "complete"
    save_json(evidence / "trial.json", trial)
    print(json.dumps(trial, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
