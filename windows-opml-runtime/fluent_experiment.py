"""Drive the released Fluent Reader UI and inspect only persisted state, not article bodies."""
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from playwright.sync_api import sync_playwright
from pywinauto import Desktop, keyboard
from urllib.request import urlopen
from xml.etree import ElementTree
import json
import os
import re
import sqlite3
import subprocess
import time
import traceback
import win32clipboard


ROOT = Path(os.environ["BREW_WIN_RUNTIME"])
EXE = Path(os.environ["BREW_WIN_FLUENT"])
EVIDENCE = Path("windows-opml-evidence")
OPML = Path(__file__).with_name("control.opml").resolve()
WANTED_FEEDS = {n.attrib["xmlUrl"] for n in ElementTree.parse(OPML).iter("outline") if "xmlUrl" in n.attrib}


def put(name, obj):
    (EVIDENCE / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def wait(fn, description, seconds=45):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            answer = fn()
            if answer:
                return answer
        except (sqlite3.OperationalError, OSError, AttributeError):
            pass
        time.sleep(0.35)
    raise RuntimeError(f"Timed out waiting for {description}")


def file_dialog(value, pid, saving=False):
    def pick():
        for w in Desktop(backend="win32").windows(class_name="#32770"):
            if w.process_id() != pid or not w.is_visible():
                continue
            edits = [e for e in w.descendants(class_name="Edit") if e.is_visible()]
            if edits:
                return w, next((e for e in edits if e.control_id() == 1148), edits[-1])
        return None
    window, edit = wait(pick, "Fluent Reader file dialog")
    window.set_focus()
    edit.set_focus()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(value), win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    keyboard.send_keys("^a^v", pause=0.15)
    assert edit.window_text().strip().strip('"') == str(value)
    keyboard.send_keys("{ENTER}", pause=0.15)
    handle = window.handle
    try:
        wait(lambda: (saving and value.is_file()) or not any(
            w.handle == handle and w.is_visible() for w in Desktop(backend="win32").windows(class_name="#32770")
        ), "file picker dismissal", 15)
    except RuntimeError:
        boxes = []
        for w in Desktop(backend="win32").windows(class_name="#32770"):
            if w.process_id() == pid and w.is_visible():
                buttons = [b.window_text() for b in w.descendants(class_name="Button")]
                boxes.append({"control_ids": [b.control_id() for b in w.descendants(class_name="Button")],
                              "button_labels": [s for s in buttons if s in ("&Save", "&Open", "Cancel", "OK", "Yes", "No")]})
        put("fluent-file-dialog-diagnostics.json", {"saving": saving, "boxes": boxes})
        raise


def confirm_restore(pid):
    def choose():
        for window in Desktop(backend="uia").windows():
            if window.class_name() != "#32770" or window.process_id() != pid:
                continue
            buttons = [b for b in window.descendants(control_type="Button") if b.window_text().replace("&", "") == "Confirm"]
            if buttons:
                return buttons[0]
        return None
    wait(choose, "Fluent Reader confirmation button").invoke()


def endpoint(port):
    with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as result:
        return result.status == 200


def start(pw, profile, port):
    profile.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen([str(EXE), f"--user-data-dir={profile}",
                          f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"], cwd=EXE.parent)
    wait(lambda: endpoint(port), "Fluent Reader renderer endpoint", 25)
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=9000)
    browser.contexts[0].pages[0].wait_for_load_state("domcontentloaded")
    return p, browser.contexts[0].pages[0]


def stop(process, port):
    # The application's own Restore relaunch can replace the original PID.
    env = os.environ | {"BREW_FLUENT_PORT": str(port)}
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='Fluent Reader.exe'\" | "
        "Where-Object { $_.ExecutablePath -eq $env:BREW_WIN_FLUENT -and $_.CommandLine.Contains("
        "('--remote-debugging-port=' + $env:BREW_FLUENT_PORT)) } | "
        "ForEach-Object { taskkill.exe /pid $_.ProcessId /t /f | Out-Null }"
    )
    subprocess.run(["pwsh", "-NoProfile", "-Command", command], env=env,
                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if process.poll() is None:
        subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"], capture_output=True)
    time.sleep(0.8)


def rows(profile):
    path = profile / "fluent-reader.db"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
        feeds = {x[0] for x in db.execute("SELECT url FROM sources")}
        articles = {}
        for iid, url, link, read, saved in db.execute(
            "SELECT i._id, s.url, i.link, i.hasRead, i.starred "
            "FROM items i JOIN sources s ON i.source = s.sid "
            "WHERE i.link <> '' ORDER BY i.date DESC, i._id DESC"
        ):
            articles.setdefault((url, link), (iid, bool(read), bool(saved)))
        return feeds, articles


def digest(word):
    return sha256(word.encode("utf-8")).hexdigest()


def snapshot(profile, items):
    feeds, articles = rows(profile)
    states = {}
    for label, key in items.items():
        row = articles.get(key)
        states[label] = dict(feed_hash=digest(key[0]), article_url_hash=digest(key[1]),
                             present=row is not None, read=row[1] if row else None,
                             saved=row[2] if row else None)
    return dict(feed_count=len(feeds), expected_feeds_present=WANTED_FEEDS <= feeds,
                article_count=len(articles), items=states)


def states(snap):
    return [(r["present"], r["read"], r["saved"]) for r in snap["items"].values()]


def enter_settings(page):
    page.get_by_role("button", name="Settings", exact=True).click(timeout=9000)


def close_settings(page):
    page.get_by_role("button", name="Exit settings", exact=True).click(timeout=9000)


def import_opml(page, process, profile):
    enter_settings(page)
    page.get_by_role("button", name="Import", exact=True).click(timeout=9000)
    file_dialog(OPML, process.pid)
    wait(lambda: (r := rows(profile)) and WANTED_FEEDS == r[0] and len(r[1]) >= 3, "all three OPML subscriptions and refreshed articles", 70)
    close_settings(page)
    time.sleep(1)


def visible_candidates(page, profile):
    visible = page.locator("[data-iid]").evaluate_all(
        "(cards) => cards.map(n=>parseInt(n.dataset.iid,10)).filter(Number.isFinite)")
    values = rows(profile)[1]
    by_id = {row[0]: key for key, row in values.items() if not row[1] and not row[2] and key[0] in WANTED_FEEDS}
    return [by_id[i] for i in visible if i in by_id]


def mark(page, iid, action):
    page.locator(f'[data-iid="{iid}"]').first.click(button="right", timeout=8000)
    page.get_by_text(action, exact=True).last.click(timeout=8000)


def preferences(page):
    enter_settings(page)
    page.get_by_text("Preferences", exact=True).first.click(timeout=9000)


@contextmanager
def block_app_network():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Windows":
        raise RuntimeError("This isolation rule belongs only to the disposable GitHub runner")
    name = "BrewFluentReadingStateOffline"
    add = ("New-NetFirewallRule -Name '%s' -Direction Outbound -Action Block -Enabled True "
           "-Profile Any -Program $env:BREW_WIN_FLUENT -ErrorAction Stop | Out-Null" % name)
    subprocess.run(["pwsh", "-NoProfile", "-Command", add], check=True, capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        check = ("$r=Get-NetFirewallRule -Name '%s' -ErrorAction Stop; "
                 "if ($r.Enabled -ne 'True' -or $r.Action -ne 'Block') {exit 1}; "
                 "if (($r|Get-NetFirewallApplicationFilter).Program -ne $env:BREW_WIN_FLUENT) {exit 2}; "
                 "if (@(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object Enabled -ne True).Count) {exit 3}" % name)
        subprocess.run(["pwsh", "-NoProfile", "-Command", check], check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        yield
    finally:
        subprocess.run(["pwsh", "-NoProfile", "-Command", f"Remove-NetFirewallRule -Name '{name}'"], check=True,
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_OS") == "Windows"
    all_rounds = []
    chosen = None
    with sync_playwright() as pw:
        for number in (1, 2):
            base = ROOT / f"fluent-round-{number}"
            paths = [base / name for name in ("source", "opml-control", "native-restore")]
            assert not any(path.exists() for path in paths)
            source, control, restore = paths
            backup = base / "bundle.frdata"
            proc, page = start(pw, source, 9250)
            try:
                import_opml(page, proc, source)
                candidates = wait(lambda: visible_candidates(page, source)[:8], "Fluent Reader cards")
                if len(candidates) < 3:
                    raise RuntimeError("Fewer than three visible clean articles")
                if chosen is None:
                    chosen = dict(zip(("read", "saved", "untouched"), candidates[:3]))
                values = rows(source)[1]
                if not set(chosen.values()) <= set(candidates):
                    raise RuntimeError("Published feed items changed across repeats")
                mark(page, values[chosen["read"]][0], "Mark as read")
                mark(page, values[chosen["saved"]][0], "Star")
                target = [(True, True, False), (True, False, True), (True, False, False)]
                seeded = wait(lambda: s if states(s := snapshot(source, chosen)) == target else None,
                              "both intended article states and unchanged control")
                put(f"fluent-round-{number}-source.json", seeded)
                preferences(page)
                page.get_by_role("button", name="Backup", exact=True).click(timeout=9000)
                file_dialog(backup, proc.pid, saving=True)
                wait(lambda: backup.is_file() and backup.stat().st_size > 0, "native .frdata output")
                packed = json.loads(backup.read_text(encoding="utf-8"))
                assert "sources" in packed["sqlite"] and "items" in packed["sqlite"]
            finally:
                stop(proc, 9250)
            proc, page = start(pw, control, 9251)
            try:
                import_opml(page, proc, control)
                baseline = snapshot(control, chosen)
                put(f"fluent-round-{number}-opml-only.json", baseline)
                assert states(baseline) == [(True, False, False)] * 3
            finally:
                stop(proc, 9251)
            proc, page = start(pw, control, 9254)
            try:
                baseline_after_restart = snapshot(control, chosen)
                assert states(baseline_after_restart) == states(baseline)
            finally:
                stop(proc, 9254)
            with block_app_network():
                proc, page = start(pw, restore, 9252)
                try:
                    assert rows(restore) == (set(), {})
                    preferences(page)
                    page.get_by_role("button", name="Restore", exact=True).click(timeout=9000)
                    file_dialog(backup, proc.pid)
                    confirm_restore(proc.pid)
                    wait(lambda: (db := rows(restore)) and len(db[1]) >= 3, "restored article database", 45)
                    after = snapshot(restore, chosen)
                    assert states(after) == target
                finally:
                    stop(proc, 9252)
                proc, page = start(pw, restore, 9253)
                try:
                    after_restart = snapshot(restore, chosen)
                    assert states(after_restart) == target
                    assert after_restart == after
                finally:
                    stop(proc, 9253)
            round_result = dict(source=seeded, opml_only=baseline, opml_only_after_restart=baseline_after_restart, native_restore=after,
                                native_restore_after_restart=after_restart, with_app_outbound_block=True,
                                native_backup_sha256=sha256(backup.read_bytes()).hexdigest(),
                                native_backup_bytes=backup.stat().st_size)
            put(f"fluent-round-{number}.json", round_result)
            all_rounds.append(round_result)
    assert [states(x["opml_only"]) for x in all_rounds] == [[(True,False,False)]*3]*2
    assert len({str(states(x["native_restore_after_restart"])) for x in all_rounds}) == 1
    put("fluent-summary.json", dict(two_rounds=True, state_identity="paired URL SHA-256",
                                   app="Fluent Reader", version="1.3.2", evidence=all_rounds))


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        # Error details from Playwright may include article titles. Keep only the exception type and code positions.
        loc = [dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
               for frame in traceback.extract_tb(err.__traceback__) if frame.filename.endswith("fluent_experiment.py")]
        put("fluent-failure.json", {"type": type(err).__name__, "locations": loc})
        print("Fluent Reader route failed", type(err).__name__, loc)
        raise SystemExit(1)
