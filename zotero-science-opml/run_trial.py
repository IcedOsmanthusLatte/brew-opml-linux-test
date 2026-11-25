"""Run the real Zotero desktop importer on a dedicated virtual X11 display."""

from configparser import ConfigParser
from contextlib import contextmanager, closing, suppress
import csv
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

OPML_SHA256 = "9de06c05dc6a964934009ae83e3ea4e6a86a2cee58d91d31077c9703e2ab563e"


def expect(test, label, timeout=35):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        answer = test()
        if answer:
            return answer
        time.sleep(0.45)
    raise RuntimeError(f"Timed out: {label}")


def command(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE, timeout=15).strip()


def mouse(x, y, button=None):
    command("xdotool", "mousemove", str(x), str(y))
    if button:
        command("xdotool", "click", str(button))


def press(*keys):
    command("xdotool", "key", "--clearmodifiers", *keys)


def screenshot(path):
    command("scrot", "--overwrite", str(path))


def recognized_words(image):
    screenshot(image)
    tsv = command("tesseract", str(image), "stdout", "--psm", "11", "tsv")
    return [word for word in csv.DictReader(tsv.splitlines(), delimiter="\t") if (word.get("text") or "").strip()]


def lines(image):
    grouped = {}
    for word in recognized_words(image):
        key = tuple(word[field] for field in ("block_num", "par_num", "line_num"))
        grouped.setdefault(key, []).append(word)
    records = []
    for words in grouped.values():
        text = " ".join(word["text"] for word in words)
        first = words[0]
        x = int(first["left"]) + max(3, int(first["width"]) // 2)
        y = int(first["top"]) + int(first["height"]) // 2
        records.append((text, x, y))
    return records


def text_point(image, pattern, max_x=1380):
    return next(((x, y) for text, x, y in lines(image) if x < max_x and re.search(pattern, text, re.I)), None)


def set_clipboard(value):
    subprocess.run(["xclip", "-selection", "clipboard", "-in"], input=value, text=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=4)


def chooser_path_readback(path):
    command("xdotool", "windowactivate", "--sync", expect(file_picker_open, "native GTK file picker"))
    press("ctrl+l")
    time.sleep(0.85)
    set_clipboard(str(path))
    press("ctrl+a", "ctrl+v")
    time.sleep(0.65)
    # An unchanged clipboard must never be accepted as proof of focus and paste.
    set_clipboard("not the chooser path")
    press("ctrl+a", "ctrl+c")
    return expect(
        lambda: command("xclip", "-selection", "clipboard", "-out") == str(path),
        "GTK location entry returned the exact fixture path", 4,
    )


def file_picker_open():
    try:
        return command("xdotool", "search", "--onlyvisible", "--name", "^Import Feeds from OPML$")
    except subprocess.CalledProcessError:
        return False


@contextmanager
def capture_failure(output, scratch, evidence):
    try:
        yield
    except Exception:
        with suppress(OSError, subprocess.SubprocessError):
            screenshot(output / "failure.png")
            evidence["last_screen_text"] = [label for label, _, _ in lines(scratch)]
        raise


def read_db(db, query, params=()):
    if not db.exists():
        return []
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)) as con:
            con.row_factory = sqlite3.Row
            return [dict(row) for row in con.execute(query, params)]
    except sqlite3.OperationalError as error:
        if "locked" in str(error) or "no such table: feeds" in str(error):
            return []
        raise


def subscriptions(db):
    return read_db(db, "SELECT libraryID, name, url, lastCheck, lastCheckError FROM feeds ORDER BY url")


def journal_entries(db, library_id):
    return read_db(db, """
        SELECT items.itemID AS id,
               MAX(CASE WHEN fields.fieldName='title' THEN value END) AS title,
               MAX(CASE WHEN fields.fieldName='url' THEN value END) AS url,
               MAX(CASE WHEN fields.fieldName='date' THEN value END) AS date
        FROM items JOIN feedItems USING(itemID) JOIN itemData USING(itemID)
        JOIN fields USING(fieldID) JOIN itemDataValues USING(valueID)
        WHERE items.libraryID=? GROUP BY items.itemID
        HAVING title IS NOT NULL AND url IS NOT NULL ORDER BY date DESC
    """, (library_id,))


def normalize(value):
    return "".join(char for char in value.lower() if char.isalnum())


def find_article(entries, image):
    view = lines(image)
    for entry in entries[:12]:
        expected = normalize(entry["title"])
        for label, x, y in view:
            seen = normalize(label)
            if 210 < x < 940 and y > 86 and len(seen) >= 14 and (
                expected.startswith(seen) or SequenceMatcher(None, expected[:len(seen)], seen).ratio() > 0.89
            ):
                return entry, x, y
    return None


def opened_article(url):
    assert urlsplit(url).scheme == "https" and urlsplit(url).hostname in {
        "journals.plos.org", "www.nature.com", "elifesciences.org", "doi.org", "dx.doi.org"
    }, url
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"})
    with urlopen(request, timeout=18) as response:
        body = response.read(2_000_000).decode("utf-8", errors="replace")
        meta = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        return {"status": response.status, "url": response.geturl(), "title": meta.group(1).strip() if meta else None}


def window():
    try:
        result = command("xdotool", "search", "--onlyvisible", "--name", "Zotero")
        wins = result.splitlines()
        return wins[-1] if wins else None
    except subprocess.CalledProcessError:
        return None


@contextmanager
def launch(binary, profile, data, home, log):
    profile.mkdir(exist_ok=True)
    (profile / "user.js").write_text(
        'user_pref("app.update.auto", false);\n'
        'user_pref("extensions.zotero.firstRun2", false);\n'
        'user_pref("extensions.zotero.pane.persist", "");\n'
        'user_pref("widget.use-xdg-desktop-portal.file-picker", 0);\n'
        'user_pref("extensions.zotero.useDataDir", true);\n'
        f'user_pref("extensions.zotero.dataDir", {json.dumps(str(data))});\n', encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / "config"),
           "XDG_DATA_HOME": str(home / "share"), "MOZ_ENABLE_WAYLAND": "0", "LC_ALL": "C.UTF-8"}
    with log.open("ab") as output:
        process = subprocess.Popen([binary, "-no-remote", "-profile", str(profile)], env=env,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            win = expect(window, "Zotero window")
            command("xdotool", "windowactivate", "--sync", win)
            command("xdotool", "windowsize", win, "1370", "860")
            command("xdotool", "windowmove", win, "0", "0")
            yield win
        finally:
            if process.poll() is None:
                with suppress(subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    press("Escape")
                    if win := window():
                        command("xdotool", "windowactivate", win)
                        press("ctrl+q")
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=10)


def run(binary, number, output, temp):
    sample = output / "science.opml"
    assert sha256(sample.read_bytes()).hexdigest() == OPML_SHA256
    outlines = ElementTree.parse(sample).findall(".//outline[@xmlUrl]")
    expected = {item.get("xmlUrl") for item in outlines}
    assert len(expected) == len(outlines) == 9
    evidence = {"round": number, "fixture_sha256": OPML_SHA256, "expected_urls": sorted(expected),
                "preexisting_subscriptions": None, "passed": False}
    report = output / "trial.json"
    report.write_text(json.dumps(evidence, indent=2) + "\n")
    profile, data, home = (temp / name for name in ("profile", "data", "home"))
    home.mkdir()
    data.mkdir()
    picker_copy = home / "science.opml"
    picker_copy.write_bytes(sample.read_bytes())
    assert sha256(picker_copy.read_bytes()).hexdigest() == OPML_SHA256
    db = data / "zotero.sqlite"
    log = output / "zotero-runtime.txt"
    scratch = temp / "screen.png"

    def imported():
        feeds = subscriptions(db)
        return feeds if len(feeds) == len(expected) and {row["url"] for row in feeds} == expected else None

    try:
        config = ConfigParser()
        install = Path(binary).parent
        config.read([install / "application.ini", install / "app" / "application.ini"])
        release_url = (output / "release-url.txt").read_text().strip()
        release_version = re.search(r"/release/([0-9.]+)/", release_url).group(1)
        evidence["version"] = config.get("App", "Version", fallback=release_version)
        evidence["build_id"] = config.get("App", "BuildID", fallback=None)
        assert evidence["version"] == release_version
        with launch(binary, profile, data, home, log) as win, capture_failure(output, scratch, evidence):
            expect(lambda: db.exists(), "new library database")
            time.sleep(2)
            evidence["preexisting_subscriptions"] = subscriptions(db)
            assert not evidence["preexisting_subscriptions"]
            # Click the real File menu in the app client rather than assuming its keyboard mnemonic.
            command("xdotool", "mousemove", "--window", win, "26", "17", "click", "1")
            time.sleep(0.65)
            screenshot(output / "file-menu.png")
            menu = lines(scratch)
            feed = next(((x, y) for text, x, y in menu if x < 600 and re.search(r"\bNew\s+Feed\b", text, re.I)), None)
            if not feed:
                library = next(((x, y) for text, x, y in menu if x < 530 and re.search(r"\bNew\s+Library\b", text, re.I)), None)
                if library:
                    mouse(*library)
                    time.sleep(0.65)
                screenshot(output / "library-menu.png")
                feed = expect(lambda: text_point(scratch, r"\bNew\s+Feed\b", 700), "New Feed menu", 12)
            screenshot(output / "import-menu.png")
            # The opened File menu shows the actual `New Feed` row and its right arrow.
            mouse(*feed, button=1)
            time.sleep(0.65)
            screenshot(output / "submenu.png")
            opml = text_point(scratch, r"\b(?:from\s+)?[O0Q]PML\b")
            if opml:
                mouse(*opml, button=1)
            else:
                # The released New Feed submenu has From URL first and From OPML last.
                # The actual file chooser below gates any typing, including this keyboard route.
                press("Right", "End", "Return")
            time.sleep(1.5)
            screenshot(output / "import-picker.png")
            expect(file_picker_open, "visible native dialog title", 7)
            assert chooser_path_readback(picker_copy)
            evidence["native_picker_file"] = {"name": picker_copy.name, "exact_path_readback": True}
            screenshot(output / "selected-opml.png")
            press("Return")
            expect(lambda: not file_picker_open(), "native file picker closed", 12)
            evidence["imported"] = expect(imported, "all nine exact subscriptions", 75)
        expect(lambda: window() is None, "previous Zotero window to close", 10)
        with launch(binary, profile, data, home, log), capture_failure(output, scratch, evidence):
            time.sleep(2)
            evidence["after_restart"] = expect(imported, "all nine subscriptions after restart", 18)
            screenshot(output / "after-restart.png")
            journal_patterns = [
                ("https://journals.plos.org/plosbiology/feed/atom", r"\bPLOS\s+Biology\b"),
                ("https://www.nature.com/nature.rss", r"^Nature\b"),
                ("https://elifesciences.org/rss/subject/evolutionary-biology.xml", r"\beLife\b"),
            ]

            def loaded_journal():
                ids = {row["url"]: row["libraryID"] for row in subscriptions(db)}
                for url, pattern in journal_patterns:
                    if entries := journal_entries(db, ids[url]):
                        return url, pattern, entries
                return None

            url, pattern, entries = expect(loaded_journal, "items in a scholarly feed", 85)
            point = expect(lambda: text_point(scratch, pattern, 260), "visible journal feed", 12)
            mouse(*point, button=1)
            item, x, y = expect(lambda: find_article(entries, scratch), "visible journal title", 18)
            mouse(x, y, button=1)
            time.sleep(0.7)
            screenshot(output / "journal-item.png")
            evidence["selected_journal_feed"] = url
            evidence["selected_item"] = item
            evidence["publisher_entry"] = opened_article(item["url"])
            assert evidence["publisher_entry"]["status"] == 200 and evidence["publisher_entry"]["title"]
        evidence["passed"] = True
    except Exception as error:
        evidence["failure"] = f"{type(error).__name__}: {error}"
        evidence["db_readback_on_failure"] = subscriptions(db)
        raise
    finally:
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    tool, round_value, evidence = sys.argv[1:]
    manager = subprocess.Popen(["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.4)
        with tempfile.TemporaryDirectory(prefix="zotero-science-") as session:
            run(tool, int(round_value), Path(evidence), Path(session))
    finally:
        manager.terminate()
        manager.wait(timeout=5)
