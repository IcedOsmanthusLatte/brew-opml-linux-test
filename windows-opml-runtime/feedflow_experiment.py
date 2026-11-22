"""Drive FeedFlow 1.21.0 through its Windows desktop UI with private pixel recognition."""
from contextlib import closing, contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from PIL import ImageGrab
from pywinauto import Desktop, keyboard, mouse
from rapidocr_onnxruntime import RapidOCR
from xml.etree import ElementTree
import csv
import ctypes
import hashlib
import json
import numpy
import os
import re
import sqlite3
import subprocess
import time
import traceback
import win32clipboard

import probe
from fluent_experiment import file_dialog, wait


ROOT = Path(os.environ["BREW_WIN_RUNTIME"])
EXE = Path(os.environ["BREW_WIN_FEEDFLOW"])
OPML = Path(__file__).with_name("control.opml").resolve()
OUT = Path("windows-opml-evidence")
FEED_URLS = {x.attrib["xmlUrl"] for x in ElementTree.parse(OPML).iter("outline") if "xmlUrl" in x.attrib}
USER32 = ctypes.windll.user32
USER32.SetProcessDPIAware()
OCR = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)
PHASE = "startup"
LAST_RECOGNIZED_LABEL = None


def put(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def data(profile):
    db_file = profile / "AppData/Roaming/FeedFlow/FeedFlow.db"
    if not db_file.is_file():
        return None
    with closing(sqlite3.connect(db_file.as_uri() + "?mode=ro", uri=True, timeout=3)) as db:
        feeds = set(row[0] for row in db.execute("SELECT url FROM feed_source"))
        items = {}
        for feed, url, title, read, saved in db.execute(
            "SELECT s.url,i.url,i.title,i.is_read,i.is_bookmarked FROM feed_item i "
            "JOIN feed_source s ON s.url_hash=i.feed_source_id"
        ):
            items.setdefault((feed, url), (title, bool(read), bool(saved)))
        return feeds, items


class FeedFlow:
    def __init__(self, name):
        self.profile = ROOT / name
        self.p = None
        self.pid = None
        self.main = None

    def start(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        env = os.environ | {"JAVA_TOOL_OPTIONS": f"-Duser.home={self.profile} -Dskiko.renderApi=SOFTWARE",
                            "SKIKO_RENDER_API": "SOFTWARE"}
        self.p = subprocess.Popen([str(EXE)], cwd=EXE.parent, env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def native():
            pids = {self.p.pid, *(item["ProcessId"] for item in probe.feedflow_processes(EXE))}
            return next((w for w in Desktop(backend="win32").windows()
                         if w.process_id() in pids and w.is_visible()
                         and w.rectangle().width() >= 600 and w.class_name() == "SunAwtFrame"), None)

        self.main = wait(native, "FeedFlow main window", 50)
        self.pid = self.main.process_id()
        self.main.set_focus()
        time.sleep(1.2)
        wait(lambda: data(self.profile), "FeedFlow local database", 15)

    def stop(self):
        if self.p is None:
            return
        try:
            if self.main and self.main.is_visible():
                self.main.set_focus()
                keyboard.send_keys("%{F4}")
                self.p.wait(10)
        except Exception:
            pass
        if self.p.poll() is None:
            subprocess.run(["taskkill", "/pid", str(self.p.pid), "/t", "/f"], capture_output=True)
        time.sleep(0.5)
        self.p = None

    def windows(self):
        windows = [w for w in Desktop(backend="win32").windows()
                   if w.process_id() == self.pid and w.is_visible()
                   and 70 <= w.rectangle().width() <= 2200
                   and w.rectangle().height() >= 45]
        foreground = USER32.GetForegroundWindow()
        return sorted(windows, key=lambda w: (w.handle != foreground, w.handle == self.main.handle))

    @staticmethod
    def recognize(win):
        rect = win.rectangle()
        image = ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom))
        result, _ = OCR(numpy.asarray(image.convert("RGB"))[:, :, ::-1], use_cls=False)
        if result is None:
            return []
        found = []
        for box, line, confidence in result:
            mid_x = int(sum(point[0] for point in box)/4)
            mid_y = int(sum(point[1] for point in box)/4)
            found.append((line, float(confidence), (rect.left + mid_x, rect.top + mid_y), mid_y))
        return found

    def point(self, label, seconds=15, below=0):
        global LAST_RECOGNIZED_LABEL
        LAST_RECOGNIZED_LABEL = label if below == 0 else "(private article title)"
        wanted = normal(label)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            best = (0, None)
            for window in self.windows():
                options = self.recognize(window)
                for line, confidence, pos, y in options:
                    if y < below or confidence < 0.56:
                        continue
                    measured = normal(line)
                    measured = re.sub(r" ctrl [a-z]$", "", measured)
                    if len(wanted) >= 20:
                        similarity = max(SequenceMatcher(None, wanted, measured).ratio(),
                                         SequenceMatcher(None, wanted[:len(measured)], measured).ratio() - 0.11)
                    else:
                        similarity = SequenceMatcher(None, wanted, measured).ratio()
                    if similarity > best[0]:
                        best = (similarity, pos)
                if best[0] >= 0.86:
                    return best[1]
            time.sleep(0.35)
        raise RuntimeError("FeedFlow label not located")

    def click(self, label, seconds=15, below=0, button="left"):
        coords = self.point(label, seconds=seconds, below=below)
        mouse.click(coords=coords, button=button)
        time.sleep(0.35)

    def open_tools(self):
        self.main.set_focus()
        stored = data(self.profile)
        if stored and not stored[0]:
            try:
                self.click("Import and export", seconds=4)
            except RuntimeError:
                # Confirmed initial 800x600 view on both hosted Windows images.
                frame = self.main.rectangle()
                coords = (frame.left + round(frame.width()*0.25), frame.top + round(frame.height()*0.66))
                mouse.click(coords=coords)
            time.sleep(0.6)
            return
        keyboard.send_keys("^i", pause=0.18)
        time.sleep(0.6)
        try:
            self.point("Subscriptions (OPML)", seconds=3)
        except RuntimeError:
            frame = self.main.rectangle()
            mouse.click(coords=(frame.left+25, frame.top+42))
            self.click("Import and export", seconds=7)

    def import_opml(self, panel_open=False, keep_panel=False):
        if not panel_open:
            self.open_tools()
        try:
            self.click("Import feed from OPML")
        except RuntimeError:
            if PHASE == "source OPML and refreshed matched IDs" and not data(self.profile)[0]:
                for idx, window in enumerate(self.windows()[:3],1):
                    rect=window.rectangle()
                    image=ImageGrab.grab((rect.left,rect.top,rect.right,rect.bottom))
                    image.save(OUT/f"feedflow-empty-opml-panel-{idx}.png")
            raise
        file_dialog(OPML, self.pid)
        self.click("OK", seconds=60)
        if not keep_panel:
            self.click("Done", seconds=70)
        wait(lambda: (d := data(self.profile)) and d[0] == FEED_URLS,
             "all FeedFlow expected subscriptions", 50)

    def articles_csv(self, file, export=False, panel_open=False, keep_panel=False):
        if not panel_open:
            self.open_tools()
        if export:
            self.click("Export articles to CSV")
            self.click("Confirm")
            file_dialog(file, self.pid, saving=True)
            self.point("Articles export done", seconds=40)
        else:
            self.click("Import articles from CSV")
            file_dialog(file, self.pid)
            self.point("Articles import done", seconds=40)
        self.click("OK")
        if not keep_panel:
            self.click("Done")
        return True

    def mark(self, title, action):
        assert title
        self.main.set_focus()
        try:
            self.click(title, seconds=3, below=82, button="right")
        except RuntimeError:
            self.search_title(title)
        self.click(action, seconds=6)
        keyboard.send_keys("{ESC}", pause=0.15)

    def search_title(self, title):
        keyboard.send_keys("^f", pause=0.20)
        time.sleep(0.65)
        phrase = " ".join(re.findall(r"[a-zA-Z0-9]+", title)[:6])
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(phrase, win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        keyboard.send_keys("^a^v", pause=0.15)
        time.sleep(0.85)
        short_title = title.strip().splitlines()[0]
        self.click(short_title, seconds=6, below=80, button="right")


def normal(value):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", value.lower()).split())


def snapshot(app, keys):
    feeds, articles = data(app.profile)
    out = {}
    for label, key in keys.items():
        item = articles.get(key)
        out[label] = dict(feed_hash=digest(key[0]), article_url_hash=digest(key[1]),
                          present=item is not None, read=item[1] if item else None, saved=item[2] if item else None)
    return {"feed_count":len(feeds), "exact_fixture_feed_urls":feeds==FEED_URLS,
            "article_count":len(articles), "items":out}


def flags(s):
    return [(item["present"], item["read"], item["saved"]) for item in s["items"].values()]


@contextmanager
def run(name):
    app = FeedFlow(name)
    try:
        app.start()
        yield app
    finally:
        app.stop()


def load_pristine_source(app, keys):
    app.import_opml()
    if not set(keys.values()) <= set(data(app.profile)[1]):
        keyboard.send_keys("{F5}")
    featured = wait(lambda: d[1] if (d := data(app.profile)) and set(keys.values()) <= set(d[1])
                    else None, "same articles as Fluent Reader", 50)
    assert flags(snapshot(app, keys)) == [(True, False, False)] * 3
    return featured


def seed_closed_source(profile, keys):
    """Set exactly two source-test flags. The released app owns every import and export."""
    assert os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_OS") == "Windows"
    assert profile.resolve().parent == ROOT.resolve() and profile.name.endswith("-source-seeded")
    db_path = profile / "AppData/Roaming/FeedFlow/FeedFlow.db"
    assert db_path.is_file()
    assert not probe.feedflow_processes(EXE), "Source setup requires the app to be closed"
    initial = data(profile)
    assert initial[0] == FEED_URLS and set(keys.values()) <= set(initial[1])
    assert all(initial[1][key][1:] == (False, False) for key in keys.values())
    with closing(sqlite3.connect(db_path.as_uri()+"?mode=rw", uri=True, timeout=5)) as db, db:
        for label, field in (("read", "is_read"), ("saved", "is_bookmarked")):
            feed, article = keys[label]
            count = db.execute(
                f"UPDATE feed_item SET {field}=1 "
                "WHERE feed_source_id=(SELECT url_hash FROM feed_source WHERE url=?) AND url=? "
                "AND is_read=0 AND is_bookmarked=0", (feed, article)
            ).rowcount
            assert count == 1
        assert db.total_changes == 2
    updated = data(profile)
    changed = {key for key in initial[1] if initial[1][key] != updated[1][key]}
    assert initial[0] == updated[0] and set(initial[1]) == set(updated[1])
    assert changed == {keys["read"], keys["saved"]}
    assert updated[1][keys["untouched"]][1:] == (False, False)


def native_export(source, keys, csv_file, prefix, target):
    global PHASE
    source_flags = wait(lambda: s if flags(s := snapshot(source, keys)) == target else None,
                        "marked read, bookmark and unchanged control", 8)
    put(f"{prefix}-source-state.json", source_flags)
    PHASE = "source export native CSV"
    assert source.articles_csv(csv_file, export=True)
    assert csv_file.is_file() and csv_file.stat().st_size > 0
    with csv_file.open("r", encoding="utf-8-sig", newline="") as stream:
        native = {row["url"]: (row["is_read"].lower() in ("true","1"), row["is_bookmarked"].lower() in ("true","1"))
                  for row in csv.DictReader(stream)}
    native_csv_flags = {name: native[key[1]] for name,key in keys.items()}
    assert list(native_csv_flags.values()) == [(True,False), (False,True), (False,False)]
    put(f"{prefix}-native-export-state.json", native_csv_flags)
    return source_flags, native_csv_flags


def case(number, keys):
    global PHASE
    prefix = f"feedflow-round-{number}"
    target = [(True, True, False), (True, False, True), (True, False, False)]
    baseline_flags = [(True, False, False)] * 3
    csv_file = ROOT / f"{prefix}-source.csv"
    PHASE = "source OPML and refreshed matched IDs"
    seeded_method = "native GUI context menu"
    with run(f"{prefix}-source") as source:
        featured = load_pristine_source(source, keys)
        put(f"{prefix}-real-opml-and-shared-article-ids.json", snapshot(source, keys))
        PHASE = "native source read and bookmark"
        try:
            source.mark(featured[keys["read"]][0], "Mark as read")
            source.mark(featured[keys["saved"]][0], "Add to bookmarks")
            wait(lambda: flags(snapshot(source, keys)) == target, "the two native GUI state flags", 5)
        except RuntimeError:
            seeded_method = "two targeted SQLite flags in a closed fresh disposable source only"
        if seeded_method == "native GUI context menu":
            source_flags, native_csv_flags = native_export(source, keys, csv_file, prefix, target)
    if seeded_method != "native GUI context menu":
        PHASE = "set controlled read/bookmark test inputs in closed disposable source"
        seeded_name = f"{prefix}-source-seeded"
        with run(seeded_name) as source:
            load_pristine_source(source, keys)
            put(f"{prefix}-fresh-seed-before.json", snapshot(source, keys))
        seed_closed_source(ROOT / seeded_name, keys)
        with run(seeded_name) as source:
            source_flags, native_csv_flags = native_export(source, keys, csv_file, prefix, target)
    source_sha = hashlib.sha256(csv_file.read_bytes()).hexdigest()
    PHASE = "OPML-only control"
    with run(f"{prefix}-control") as control:
        control.import_opml()
        initial = wait(lambda: s if flags(s := snapshot(control, keys)) == baseline_flags else None,
                       "matched control article flags", 45)
    with run(f"{prefix}-control") as control:
        control_restart = snapshot(control, keys)
        assert flags(control_restart) == baseline_flags
    put(f"{prefix}-control.json", initial)
    PHASE = "correct order OPML then native CSV"
    with run(f"{prefix}-correct") as correct:
        correct.import_opml(keep_panel=True)
        correct_before = snapshot(correct, keys)
        assert correct.articles_csv(csv_file, panel_open=True)
        correct_after = snapshot(correct, keys)
        assert flags(correct_after) == target
    with run(f"{prefix}-correct") as correct:
        correct_restart = snapshot(correct, keys)
        assert flags(correct_restart) == target
    put(f"{prefix}-correct.json", dict(before=correct_before, after=correct_after, restarted=correct_restart))
    PHASE = "native CSV first with no subscriptions"
    with run(f"{prefix}-incorrect") as incorrect:
        wrong_empty = snapshot(incorrect, keys)
        assert wrong_empty["article_count"] == wrong_empty["feed_count"] == 0
        assert incorrect.articles_csv(csv_file, keep_panel=True)
        wrong_csv = snapshot(incorrect, keys)
        assert wrong_csv["article_count"] == wrong_csv["feed_count"] == 0
        put(f"{prefix}-csv-first.json", dict(ui_import_done=True, before=wrong_empty, after=wrong_csv))
        PHASE = "wrong order then OPML and official CSV retry"
        incorrect.import_opml(panel_open=True, keep_panel=True)
        wrong_opml = wait(lambda: s if flags(s := snapshot(incorrect, keys)) == baseline_flags else None,
                          "OPML after skipped CSV flags", 45)
        assert incorrect.articles_csv(csv_file, panel_open=True)
        wrong_retry = snapshot(incorrect, keys)
        assert flags(wrong_retry) == target
    with run(f"{prefix}-incorrect") as incorrect:
        wrong_restart = snapshot(incorrect, keys)
        assert flags(wrong_restart) == target
    record = dict(source=source_flags, source_state_setup=seeded_method, native_csv_state=native_csv_flags, native_csv_sha256=source_sha, native_csv_bytes=csv_file.stat().st_size,
                  opml_only=initial, opml_only_after_restart=control_restart, proper_before=correct_before, proper_after=correct_after,
                  proper_restart=correct_restart, wrong_order_success_ui=True, wrong_after_csv=wrong_csv,
                  wrong_after_opml=wrong_opml, wrong_retry=wrong_retry, wrong_restart=wrong_restart,
                  app_outbound_block_verified=False)
    put(f"{prefix}.json", record)
    return record


def experiment():
    global PHASE
    assert os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_OS") == "Windows"
    PHASE = "load exact private article keys from the paired native Fluent study"
    private_keys = ROOT / "matched-article-keys.json"
    assert private_keys.is_file()
    keys = {k: tuple(v) for k, v in json.loads(private_keys.read_text(encoding="utf-8")).items()}
    assert tuple(keys) == ("read", "saved", "untouched") and len(set(keys.values())) == 3
    rounds = [case(1, keys), case(2, keys)]
    put("feedflow-summary.json", dict(two_rounds=True, same_article_hashes_as_fluent=True, rounds=rounds))


if __name__ == "__main__":
    try:
        experiment()
    except Exception as exc:
        loc = [dict(line=t.lineno, function=t.name) for t in traceback.extract_tb(exc.__traceback__)
               if Path(t.filename).name == Path(__file__).name]
        # No publisher content, GUID, URL string or screenshot is written to the artifact.
        put("feedflow-failure.json", dict(phase=PHASE, expected_ui_label=LAST_RECOGNIZED_LABEL,
                                          type=type(exc).__name__, locations=loc))
        print("FeedFlow route failed", PHASE, type(exc).__name__, loc)
        raise SystemExit(1)
