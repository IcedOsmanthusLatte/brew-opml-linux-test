"""Compare the same frozen publisher XML in clean native RSS Guard profiles."""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

import guard

SOURCES = {
    "WordPress RSS": ("https://wordpress.org/news/feed/", "RSS"),
    "WordPress Atom": ("https://wordpress.org/news/feed/atom/", "Atom"),
    "Ubuntu RSS": ("https://ubuntu.com/security/notices/rss.xml", "RSS"),
    "Ubuntu Atom": ("https://ubuntu.com/security/notices/atom.xml", "Atom"),
}
ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
MODES = ("RSS", "Atom", "both")
PUBLISHERS = ("WordPress", "Ubuntu")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def url_key(value):
    parts = urlsplit(value.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, parts.query, ""))


class RedirectLog(HTTPRedirectHandler):
    def __init__(self):
        self.events = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.events.append({"status": code, "location": newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_source(source):
    title, (url, kind) = source
    request = Request(url, headers={"User-Agent": "BrewFeedAudit/1.0 (+https://getbrew.app/)", "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.1"})
    last_error = None
    for attempt in range(3):
        try:
            redirects = RedirectLog()
            with build_opener(redirects).open(request, timeout=18) as response:
                raw = response.read(5_000_001)
                if len(raw) > 5_000_000:
                    raise ValueError(f"Feed too large: {title}")
                meta = {"url": url, "final_url": response.url, "status": response.status,
                        "content_type": response.headers.get_content_type(),
                        "sha256": sha256(raw).hexdigest(),
                        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                        "redirects": redirects.events}
            break
        except OSError as exc:
            last_error = exc
            time.sleep(attempt + 1)
    else:
        raise RuntimeError(f"Could not fetch {title}") from last_error

    root = ET.fromstring(raw)
    expected = {"RSS": "rss", "Atom": ATOM + "feed"}[kind]
    requested_kind = kind
    if root.tag != expected:
        if (title == "Ubuntu Atom" and root.tag == "rss" and meta["final_url"] == SOURCES["Ubuntu RSS"][0]
                and len(meta["redirects"]) == 1):
            kind = "RSS"
        else:
            raise ValueError(f"{title}: unexpected root {root.tag!r}")
    entries = []
    for item in root.findall("./channel/item" if kind == "RSS" else ATOM + "entry"):
        if kind == "RSS":
            link = item.findtext("link", "").strip()
            fields = [item.findtext(tag, "") for tag in ("title", "guid", "pubDate", "description", CONTENT + "encoded")]
            published = fields[2]
        else:
            link = next((element.get("href", "").strip() for element in item.findall(ATOM + "link")
                         if element.get("rel", "alternate") == "alternate"), "")
            fields = [item.findtext(ATOM + tag, "") for tag in ("title", "id", "updated", "summary", "content")]
            published = item.findtext(ATOM + "published", "")
        if not link:
            raise ValueError(f"{title}: source entry has no alternate URL")
        entry_title, identifier, timestamp, summary, content = fields
        entries.append({"url": link, "key": url_key(link), "title": entry_title, "source_id": identifier,
                        "published": published, "source_timestamp": timestamp,
                        "summary_chars": len(summary), "content_chars": len(content)})
    if not entries:
        raise ValueError(f"{title}: source is empty")
    return title, {**meta, "kind": kind, "requested_kind": requested_kind, "count": len(entries), "entries": entries}, raw


def run_fixture_server(fixtures):
    routes = {"/" + title.lower().replace(" ", "-") + ".xml": (meta, data)
              for title, meta, data in fixtures}

    fetched_paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            value = routes.get(self.path)
            if value is None:
                self.send_error(404)
                return
            meta, data = value
            fetched_paths.append(self.path)
            if self.path == "/ubuntu-atom.xml" and meta["kind"] == "RSS":
                self.send_response(meta["redirects"][0]["status"])
                self.send_header("Location", "/ubuntu-rss.xml")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            mime = "application/rss+xml" if meta["kind"] == "RSS" else "application/atom+xml"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.fetched_paths = fetched_paths
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def make_opml(path, server, mode, meta):
    root = ET.Element("opml", version="2.0")
    ET.SubElement(ET.SubElement(root, "head"), "title").text = "Same-publisher frozen sample"
    body = ET.SubElement(root, "body")
    chosen = {}
    for title, (origin, kind) in SOURCES.items():
        if mode != "both" and kind != mode:
            continue
        fixture = "http://127.0.0.1:" + str(server.server_port) + "/" + title.lower().replace(" ", "-") + ".xml"
        chosen[title] = fixture
        # RSS Guard 5.3.0 reads its OPML version attribute when online
        # discovery is disabled. Feed type must follow the returned XML format.
        ET.SubElement(body, "outline", text=title, title=title, type="rss", version=meta[title]["kind"].upper(),
                      xmlUrl=fixture, htmlUrl=origin.rsplit("/", 1)[0])
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)
    return chosen


def db_schema(db):
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {table: [row[1].casefold() for row in db.execute(f'PRAGMA table_info("{table}")')] for table in tables}


def read_messages(data_dir, schema_path):
    databases = list(data_dir.rglob("*.db")) + list(data_dir.rglob("*.sqlite")) + list(data_dir.rglob("*.sqlite3"))
    for path in databases:
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2)) as db:
                schema = db_schema(db)
                if not schema:
                    continue
                write_json(schema_path, schema)
                table = next((name for name in schema if name.casefold() == "messages"), None)
                if not table or not {"title", "url", "is_read"} <= set(schema[table]):
                    continue
                article_id = "id" if "id" in schema[table] else "rowid"
                feed_column = next((column for column in ("feed", "feed_id", "source_id") if column in schema[table]), None)
                if not feed_column:
                    continue
                rows = db.execute(f"SELECT {article_id}, title, url, is_read, {feed_column} FROM {table}").fetchall()
                if rows:
                    return [{"row": row, "title": title, "url": url, "key": url_key(url),
                             "is_read": bool(is_read), "feed": feed}
                            for row, title, url, is_read, feed in rows if url]
        except sqlite3.OperationalError:
            continue
    return []


def groups(rows):
    by_key = defaultdict(list)
    for row in rows:
        by_key[row["key"]].append(row)
    return by_key


def lookup_feed(title):
    for node in guard.hits(roles={"tree item", "table cell", "list item"}, only_enabled=True):
        if re.match(r"^" + re.escape(title) + r"(?:\s|$)", node.name or "", re.IGNORECASE):
            try:
                guard.bounds(node)
                return node
            except RuntimeError:
                continue
    return None


def lookup_article(title):
    surname = guard.normalized(title)
    for node in guard.hits(roles={"table cell", "tree item", "list item"}, only_enabled=True):
        candidate = guard.normalized(node.name)
        if surname and (candidate == surname or candidate.endswith(surname)):
            try:
                guard.bounds(node)
                return node
            except RuntimeError:
                continue
    return None


def mark_article(publisher, before, data_dir, evidence, meta):
    read_rows = lambda: read_messages(data_dir, evidence / "database-schema.json")
    seen = groups(before)
    other_keys = {entry["key"] for entry in meta[publisher + " Atom"]["entries"]}
    rss_entries = meta[publisher + " RSS"]["entries"]
    feed_title = publisher + " RSS"
    shared = {entry["key"] for entry in rss_entries} & other_keys
    if not any(len(seen[key]) > 1 for key in shared):
        return {"publisher": publisher, "skipped": "no duplicate among matching stored articles"}
    feed_node = guard.wait_for(f"Cannot locate {feed_title!r}", lambda: lookup_feed(feed_title), seconds=10)
    guard.click(feed_node)

    def one_unread_pair():
        current = groups(read_rows())
        for rss_entry in rss_entries:
            key = rss_entry["key"]
            pair = current.get(key, [])
            if key not in other_keys or len(seen[key]) != 2 or len(pair) != 2 or any(item["is_read"] for item in pair):
                continue
            node = lookup_article(rss_entry["title"])
            if node is not None:
                return rss_entry, pair, node
        return None

    entry, target_before, item_node = guard.wait_for(f"No visible unread duplicate for {publisher}", one_unread_pair, seconds=14)
    key = entry["key"]
    guard.click(item_node)
    time.sleep(0.3)
    after_select = groups(read_rows()).get(key, [])
    if any(row["is_read"] for row in after_select):
        action = "Select the RSS-side article (automatic mark read)"
    else:
        action = "Select the RSS-side article, then use Mark articles read"
        guard.click(guard.find("Mark articles read", guard.BUTTONS))

    def one_or_more_read():
        pair = groups(read_rows()).get(key, [])
        return pair if len(pair) == 2 and any(row["is_read"] for row in pair) else None

    target_after = guard.wait_for(f"Mark read did not update {entry['url']}", one_or_more_read, seconds=7)
    time.sleep(0.8)
    target_after = groups(read_rows())[key]
    if len(target_after) != 2:
        raise RuntimeError("Mark read changed the number of article entries")
    guard.capture(evidence, "marked-" + publisher.lower())
    return {"publisher": publisher, "original_url": entry["url"], "title": entry["title"],
            "selection": feed_title, "native_action": action,
            "before": target_before, "after": target_after}


def test_mode(app, directory, server, meta, mode):
    directory.mkdir(parents=True)
    opml = directory / "fixture.opml"
    selected = make_opml(opml, server, mode, meta)
    content_titles = {
        title: "Ubuntu RSS" if title == "Ubuntu Atom" and meta[title]["kind"] == "RSS" else title
        for title in selected
    }
    expected_requests = {urlsplit(address).path for address in selected.values()}
    if content_titles.get("Ubuntu Atom") == "Ubuntu RSS":
        expected_requests.add("/ubuntu-rss.xml")
    server.fetched_paths.clear()
    with tempfile.TemporaryDirectory(prefix="rssguard-brew-atom-") as profile:
        data_dir = Path(profile) / "rssguard-data"
        (data_dir / "config").mkdir(parents=True)
        env = {**os.environ, "QT_ACCESSIBILITY": "1", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
               "QT_QPA_PLATFORM": "xcb", "QT_STYLE_OVERRIDE": "Fusion", "QT_QPA_PLATFORMTHEME": "",
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        env.pop("NO_AT_BRIDGE", None)
        with (directory / "rssguard.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(app), "--data", str(data_dir)], env=env, stdout=log, stderr=subprocess.STDOUT)
            operation = "account creation"
            try:
                guard.create_account()
                operation = "native OPML import"
                status = guard.run_import(opml, directory)
                if status != guard.IMPORT_SUCCESS:
                    raise RuntimeError(f"Unexpected import result: {status}")
                operation = "native Fetch all"
                # Click the actual toolbar button. The OPML dialog can leave focus
                # away from the main window, causing a shortcut to do nothing.
                guard.click(guard.find("Fetch all", guard.BUTTONS))
                write_json(directory / "requested-fixture-routes.json", {"expected": sorted(expected_requests)})


                guard.wait_for("The reader did not request every expected fixture",
                               lambda: expected_requests <= set(server.fetched_paths), seconds=24)
                stop = guard.find("Stop ongoing fetching", guard.BUTTONS, enabled=False)
                guard.wait_for("The reader still reports an active refresh",
                               lambda: not stop.getState().contains(guard.pyatspi.STATE_ENABLED), seconds=18)
                time.sleep(1)
                before = read_messages(data_dir, directory / "database-schema.json")
                if not before and not (directory / "database-schema.json").is_file():
                    raise RuntimeError("The native local database is missing")
                operation = "enumerate before marking"
                write_json(directory / "articles-before.json", before)
                guard.capture(directory, "fetched")
                guard.record_accessibility_tree(directory / "window-before-mark.txt")
                operation = "marking selected duplicate read in the native interface"
                marks = [mark_article(publisher, before, data_dir, directory, meta)
                         for publisher in PUBLISHERS] if mode == "both" else []
                guard.record_accessibility_tree(directory / "window.txt")
                counts = {}
                for publisher in PUBLISHERS:
                    keys = {entry["key"] for title in selected if title.startswith(publisher)
                            for entry in meta[content_titles[title]]["entries"]}
                    rows = [row for row in before if row["key"] in keys]
                    counts[publisher] = {"rows": len(rows), "unique_original_urls": len(groups(rows)),
                                         "duplicate_original_urls": sum(len(copies) > 1 for copies in groups(rows).values())}
                result = {"mode": mode, "profile_started_empty": True, "import_status": status,
                          "frozen_sha256": {title: meta[content_titles[title]]["sha256"] for title in selected},
                          "served_actual_kinds": {title: meta[content_titles[title]]["kind"] for title in selected},
                          "counts": counts, "read_checks": marks,
                          "requested_fixture_paths": sorted(set(server.fetched_paths)),
                          "native_refresh_idle": True}
                write_json(directory / "result.json", result)
                print(json.dumps(result), flush=True)
                return result
            except Exception as exc:
                (directory / "failure.txt").write_text(f"{operation}: {type(exc).__name__}: {exc}\n", encoding="utf-8")
                try:
                    guard.capture(directory, "failure")
                    guard.record_accessibility_tree(directory / "accessibility.txt")
                    write_json(directory / "articles-on-failure.json", read_messages(data_dir, directory / "database-schema.json"))
                    write_json(directory / "requested-fixture-routes.json", {"expected": sorted(expected_requests), "observed": server.fetched_paths})
                except Exception as diagnostic:
                    print(f"Failed diagnostic capture: {diagnostic}", flush=True)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        fixtures = list(executor.map(fetch_source, SOURCES.items()))
    meta = {title: details for title, details, _ in fixtures}
    write_json(evidence / "public-fixtures.json", meta)
    print(json.dumps({title: {"count": details["count"], "sha256": details["sha256"]}
                      for title, details in meta.items()}), flush=True)
    server = run_fixture_server(fixtures)
    try:
        results = []
        for turn in (1, 2):
            for mode in MODES:
                results.append(test_mode(args.app.resolve(), evidence / f"round-{turn}-{mode.lower()}", server, meta, mode))
        write_json(evidence / "summary.json", {"version": "RSS Guard 5.3.0 text Qt5 Linux",
                                               "rounds": 2, "modes": results})
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
