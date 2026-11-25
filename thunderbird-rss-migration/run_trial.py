"""Use the actual Thunderbird release and native import/export in a fresh cloud job."""
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree as ET
import zipfile


VERSION = "161.0"
FEED_PORT = 18765
MARIONETTE_PORT = 2828
BASE = f"http://127.0.0.1:{FEED_PORT}"
FEEDS = {
    "alpha": ("Research", "Archive", "Alpha"),
    "beta": ("Research", "Weekly", "Beta"),
    "gamma": ("Benchmarks", "Gamma"),
}
POSTS = {
    "archived": "Archive story removed from its feed",
    "current": "Weekly story still in its feed",
    "control": "Unread control from Gamma",
    "replacement": "New Alpha entry after the withdrawal",
}
MARKED = sorted([POSTS["archived"], POSTS["current"]])
JS = Path(__file__).with_name("native-actions.js").read_text(encoding="utf-8")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def rss(feed, item):
    channel = ET.Element("channel")
    ET.SubElement(channel, "title").text = FEEDS[feed][-1]
    ET.SubElement(channel, "link").text = BASE + "/"
    ET.SubElement(channel, "description").text = "Synthetic Thunderbird backup trial"
    node = ET.SubElement(channel, "item")
    ET.SubElement(node, "title").text = POSTS[item]
    ET.SubElement(node, "link").text = f"{BASE}/articles/{item}"
    ET.SubElement(node, "guid", isPermaLink="false").text = f"urn:brew-backup-test:{item}"
    ET.SubElement(node, "description").text = f"Body proof BREW-LOCAL-ARTICLE-{item.upper()}."
    root = ET.Element("rss", version="2.0")
    root.append(channel)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def make_fixture(path):
    root = ET.Element("opml", version="2.0")
    ET.SubElement(ET.SubElement(root, "head"), "title").text = "Brew synthetic benchmark"
    body = ET.SubElement(root, "body")
    nodes = {(): body}
    for slug, folder_path in FEEDS.items():
        for depth, name in enumerate(folder_path, start=1):
            folder_key = folder_path[:depth]
            if folder_key not in nodes:
                nodes[folder_key] = ET.SubElement(nodes[folder_key[:-1]], "outline", text=name)
        parent = nodes[folder_path]
        ET.SubElement(parent, "outline", text=folder_path[-1], title=folder_path[-1],
                      type="rss", xmlUrl=f"{BASE}/{slug}.xml", htmlUrl=BASE + "/")
    ET.indent(root)
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))


class FeedHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def do_GET(self):
        state = self.server.phase
        feed = self.path.removeprefix("/").removesuffix(".xml")
        if self.path != f"/{feed}.xml" or feed not in FEEDS:
            self.send_error(404)
            return
        item = {"alpha": "archived" if state == "initial" else "replacement",
                "beta": "current", "gamma": "control"}[feed]
        if state.endswith("_blocked"):
            self.server.log.append([state, feed, 503, None])
            self.send_error(503)
            return
        body = rss(feed, item)
        self.server.log.append([state, feed, 200, item])
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def wait_for(check, label, seconds=25):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = check()
        if value:
            return value
        time.sleep(0.3)
    raise RuntimeError(f"Timed out waiting for {label}")


class Marionette:
    def __init__(self, channel):
        self.channel = channel
        self.reader = channel.makefile("rb")
        self.serial = 0
        greeting = self.receive()
        if greeting.get("marionetteProtocol") != 3:
            raise RuntimeError(f"Unexpected protocol: {greeting}")
        self.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
        self.call("Marionette:SetContext", {"value": "chrome"})

    def receive(self):
        size = bytearray()
        while True:
            char = self.reader.read(1)
            if char == b":":
                break
            if not char or not char.isdigit():
                raise RuntimeError("Invalid Marionette length frame")
            size.extend(char)
        return json.loads(self.reader.read(int(size)))

    def call(self, method, params):
        self.serial += 1
        data = json.dumps([0, self.serial, method, params]).encode()
        self.channel.sendall(str(len(data)).encode() + b":" + data)
        response = self.receive()
        if response[0:2] != [1, self.serial] or response[2]:
            raise RuntimeError(f"{method}: {response}")
        return response[3]

    def run(self, action, value=None):
        response = self.call("WebDriver:ExecuteAsyncScript", {
            "script": JS,
            "args": [action, value],
            "newSandbox": False,
            "sandbox": "system",
            "scriptTimeout": 30000,
        })["value"]
        if "error" in response:
            raise RuntimeError(f"{action}: {response['error']}\n{response.get('stack', '')}")
        return response.get("value")

    def close(self):
        self.reader.close()
        self.channel.close()


@contextmanager
def launch(binary, profile, log):
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "user.js").write_text(
        'user_pref("marionette.port", 2828);\n'
        'user_pref("mail.provider.enabled", false);\n'
        'user_pref("mailnews.start_page.enabled", false);\n'
        'user_pref("mail.shell.checkDefaultClient", false);\n'
        'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
        'user_pref("app.update.auto", false);\n', encoding="utf-8")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        process = subprocess.Popen([binary, "-no-remote", "-profile", str(profile),
                                    "-marionette", "-remote-allow-system-access"],
                                   stdout=stream, stderr=subprocess.STDOUT,
                                   env={**os.environ, "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"})
        client = None
        try:
            def connect():
                if process.poll() is not None:
                    raise RuntimeError(f"Thunderbird exited during startup, status {process.returncode}")
                try:
                    return socket.create_connection(("127.0.0.1", MARIONETTE_PORT), 0.5)
                except OSError:
                    return None
            channel = wait_for(connect, "isolated Marionette server")
            channel.settimeout(45)
            client = Marionette(channel)
            def ready():
                return client.run("ready").get("window")
            wait_for(ready, "mail:3pane", seconds=30)
            yield client
        finally:
            if client:
                if process.poll() is None:
                    try:
                        client.run("shutdown")
                    except (OSError, RuntimeError, KeyError):
                        pass
                client.close()
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


def observe(client, count):
    def snapshot():
        result = client.run("snapshot")
        return result if len(result["messages"]) == count else None
    return wait_for(snapshot, f"{count} messages")


def paths(snapshot):
    return {url: row["path"] for row in snapshot["folders"] for url in row["urls"]}


def state(snapshot):
    return {row["subject"]: [row["read"], row["starred"]] for row in snapshot["messages"]}


def read_body(profile, item):
    token = f"BREW-LOCAL-ARTICLE-{item.upper()}".encode()
    return any(token in path.read_bytes() for path in profile.glob("Mail/**/*")
               if path.is_file() and not path.suffix and path.stat().st_size < 2000000)


def screenshot(client, subject, path):
    try:
        if not client.run("visible_folder", subject):
            return False
        subprocess.run(["scrot", "--overwrite", str(path)], check=True, timeout=8)
    except (RuntimeError, subprocess.SubprocessError) as error:
        print(f"Native screenshot unavailable: {type(error).__name__}", flush=True)
        return False
    return True


def main():
    binary, round_number, evidence = sys.argv[1:]
    evidence = Path(evidence).resolve()
    evidence.mkdir(exist_ok=True)
    fixture = evidence / "initial.opml"
    make_fixture(fixture)
    feed = ThreadingHTTPServer(("127.0.0.1", FEED_PORT), FeedHandler)
    feed.phase = "initial"
    feed.log = []
    Thread(target=feed.serve_forever, daemon=True).start()
    result = {"round": int(round_number), "runner": platform.platform(),
              "fixture_sha256": sha256(fixture.read_bytes()).hexdigest(), "samples": {}}
    snapshots = result["samples"]
    logs = evidence / "logs"
    try:
        with tempfile.TemporaryDirectory(prefix="brew-thunderbird-test-") as directory:
            work = Path(directory)
            source = work / "source"
            backup = work / "native-profile.zip"
            opml = evidence / "native-export.opml"
            with launch(binary, source, logs / "source.log") as client:
                result["release"] = client.run("ready")
                assert result["release"]["version"] == VERSION
                assert result["release"]["account_count"] == 0
                client.run("create_account")
                snapshots["source_import"] = client.run("import_opml", str(fixture))
                client.run("refresh")
                observe(client, 3)
                assert client.run("mark", MARKED) == MARKED
                snapshots["source_marked"] = client.run("snapshot")
                assert len(paths(snapshots["source_marked"])) == 3
                assert state(snapshots["source_marked"]) == {
                    POSTS["archived"]: [True, True],
                    POSTS["current"]: [True, True],
                    POSTS["control"]: [False, False],
                }
                screenshot(client, POSTS["archived"], evidence / "source-archive.png")
                client.run("export_opml", str(opml))
                assert opml.is_file()
            with launch(binary, source, logs / "source-backup.log") as client:
                assert state(observe(client, 3)) == state(snapshots["source_marked"])
                client.run("export_zip", str(backup))
            assert read_body(source, "archived")
            assert backup.is_file()
            result["native_opml_sha256"] = sha256(opml.read_bytes()).hexdigest()
            result["native_zip_sha256"] = sha256(backup.read_bytes()).hexdigest()
            with zipfile.ZipFile(backup) as zipped:
                result["native_zip_feed_store_entries"] = sorted(
                    n for n in zipped.namelist() if "/Mail/Feeds/" in "/" + n and not n.endswith("/"))
                assert result["native_zip_feed_store_entries"]

            feed.phase = "opml_blocked"
            transfer = work / "opml"
            with launch(binary, transfer, logs / "opml-blocked.log") as client:
                assert client.run("ready")["account_count"] == 0
                client.run("create_account")
                client.run("import_opml", str(opml))
                snapshots["opml_blocked"] = observe(client, 0)
                assert paths(snapshots["opml_blocked"]) == paths(snapshots["source_marked"])
                feed.phase = "opml_refetch"
                client.run("refresh")
                snapshots["opml_refetch"] = observe(client, 3)
                assert state(snapshots["opml_refetch"]) == {
                    POSTS["replacement"]: [False, False],
                    POSTS["current"]: [False, False],
                    POSTS["control"]: [False, False],
                }
                screenshot(client, POSTS["replacement"], evidence / "opml-refetched.png")
            feed.phase = "opml_restart_blocked"
            with launch(binary, transfer, logs / "opml-restarted.log") as client:
                snapshots["opml_after_restart"] = observe(client, 3)
                assert state(snapshots["opml_after_restart"]) == state(snapshots["opml_refetch"])
            assert not read_body(transfer, "archived") and read_body(transfer, "replacement")

            feed.phase = "zip_blocked"
            restored = work / "zip"
            with launch(binary, restored, logs / "zip-import.log") as client:
                assert client.run("ready")["account_count"] == 0
                result["native_zip_requested_restart"] = client.run("import_zip", str(backup))
            with launch(binary, restored, logs / "zip-restarted.log") as client:
                snapshots["zip_blocked_after_restart"] = observe(client, 3)
                assert paths(snapshots["zip_blocked_after_restart"]) == paths(snapshots["source_marked"])
                assert state(snapshots["zip_blocked_after_restart"]) == state(snapshots["source_marked"])
                result["archived_body_in_imported_store_while_blocked"] = read_body(restored, "archived")
                assert result["archived_body_in_imported_store_while_blocked"]
                screenshot(client, POSTS["archived"], evidence / "zip-archived-offline.png")
                feed.phase = "zip_refetch"
                client.run("refresh")
                snapshots["zip_after_refetch"] = observe(client, 4)
                assert state(snapshots["zip_after_refetch"]) == {
                    **state(snapshots["source_marked"]),
                    POSTS["replacement"]: [False, False],
                }
            for stage in ("opml_refetch", "zip_refetch"):
                assert [stage, "alpha", 200, "replacement"] in feed.log
                assert [stage, "alpha", 200, "archived"] not in feed.log
            assert not [row for row in feed.log if row[0].endswith("_blocked") and row[2] == 200]
            result["passed"] = True
    finally:
        feed.shutdown()
        feed.server_close()
        result["feed_requests"] = feed.log
        write_json(evidence / "trial.json", result)
    print(f"Native Thunderbird three-feed comparison passed for independent round {round_number}")


if __name__ == "__main__":
    main()
