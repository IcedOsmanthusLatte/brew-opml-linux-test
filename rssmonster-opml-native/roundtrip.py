"""Test a published RSSMonster image through the app's own OPML HTTP endpoints."""

import hashlib
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = "http://127.0.0.1:3000"
FIXTURES = {
    "flat": "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd",
    "nested": "d4fcb29b25f290042b9cd38441878ead9e4d9f0b289437b7861bfe0c68a810d7",
}
RENAMES = {"https://feeds.bbci.co.uk/news/world/rss.xml": "Brew Alpha - Brew Shared",
           "https://www.nasa.gov/news-release/feed/": "Brew Beta - Brew Shared"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def opml_paths(blob):
    root = ET.fromstring(blob)
    assert root.tag == "opml"
    body = root.find("body")
    assert body is not None
    paths, folders = {}, []

    def traverse(parent, names):
        for node in parent.findall("outline"):
            address = node.get("xmlUrl")
            if address:
                paths.setdefault(address, []).append(names)
            else:
                label = node.get("text") or node.get("title")
                assert label
                children = [*names, label]
                folders.append(children)
                traverse(node, children)

    traverse(body, [])
    return {"feeds": paths, "folders": folders, "version": root.get("version")}


def request(route, method="GET", body=None, token=None, raw=None, content_type=None):
    assert route.startswith("/api/")
    headers = {"Accept": "application/json, application/xml"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        raw = json.dumps(body).encode()
    if content_type:
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + route, data=raw, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=28) as reply:
            return reply.status, reply.headers, reply.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {route.split('/status')[0]} returned HTTP {exc.code}") from None


def document(route, method="GET", body=None, token=None, expected=(200,)):
    status, _, payload = request(route, method, body, token)
    assert status in expected, (method, route, status, expected)
    return json.loads(payload)


def put_json(output, name, data):
    (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(case, input_file, output):
    output.mkdir(parents=True, exist_ok=True)
    source = input_file.read_bytes()
    assert sha(source) == FIXTURES["flat" if case == "flat" else "nested"]
    original = opml_paths(source)
    assert len(original["feeds"]) == 3 and all(len(x) == 1 for x in original["feeds"].values())
    (output / "fixture.opml").write_bytes(source)

    for attempt in range(65):
        try:
            if request("/api/health")[0] == 200:
                break
        except (OSError, RuntimeError):
            pass
        assert attempt < 64, "Released container did not become ready"
        time.sleep(1)

    login = {"username": f"brewtest{secrets.token_hex(4)}", "password": secrets.token_urlsafe(18)}
    document("/api/auth/register", "POST", {**login, "password_repeat": login["password"]}, expected=(200, 201))
    token = document("/api/auth/login", "POST", login)["token"]
    assert token
    status, _, empty = request("/api/opml/export", token=token)
    assert status == 200 and not opml_paths(empty)["feeds"]
    (output / "before-empty.opml").write_bytes(empty)

    boundary = secrets.token_hex(16)
    wire = (f"--{boundary}\r\nContent-Disposition: form-data; "
            'name="opmlFile"; filename="fixture.opml"\r\n'
            "Content-Type: application/xml\r\n\r\n").encode()
    wire += source + f"\r\n--{boundary}--\r\n".encode()
    status, _, response = request("/api/opml/preview", "POST", token=token, raw=wire,
                                  content_type=f"multipart/form-data; boundary={boundary}")
    assert status == 202
    preview_id = urllib.parse.quote(json.loads(response)["previewId"], safe="")
    for attempt in range(115):
        state = document(f"/api/opml/preview/{preview_id}/status", token=token)
        if state["status"] == "completed":
            break
        assert state["status"] == "running" and attempt < 114
        time.sleep(1)

    preview = state["preview"]
    put_json(output, "preview.json", preview)
    subscriptions = preview["subscriptions"]
    assert len(subscriptions) == 3
    assert {row["inputUrl"] for row in subscriptions} == set(original["feeds"])
    assert all(row["selectedForImport"] is True for row in subscriptions)
    _, _, after_preview = request("/api/opml/export", token=token)
    assert not opml_paths(after_preview)["feeds"], "Preview must leave the clean library empty"
    (output / "after-preview-empty.opml").write_bytes(after_preview)
    if case == "renamed":
        assert RENAMES.keys() <= original["feeds"].keys()
        for row in subscriptions:
            if row["inputUrl"] in RENAMES:
                row["categoryName"] = RENAMES[row["inputUrl"]]
        preview["categories"] = [{"name": row["categoryName"], "subscriptionCount": 1} for row in subscriptions]
        put_json(output, "reviewed-import.json", preview)
    imported = document("/api/opml/import", "POST", preview, token=token, expected=(200, 201))
    put_json(output, "import.json", imported)
    status, headers, exported = request("/api/opml/export", token=token)
    assert status == 200 and ".opml" in headers.get("Content-Disposition", "")
    assert "xml" in headers.get("Content-Type", "")
    actual = opml_paths(exported)
    (output / "native-export.opml").write_bytes(exported)
    summary = {
        "case": case,
        "fixture_sha256": sha(source),
        "native_export_sha256": sha(exported),
        "export_content_type": headers.get("Content-Type"),
        "preview_leaves_library_empty": True,
        "fixture": original,
        "export": actual,
        "preview_rows": [{k: row.get(k) for k in ("inputUrl", "categoryName", "connectionStatus", "selectedForImport")}
                         for row in subscriptions],
        "missing_exact_urls": sorted(set(original["feeds"]) - set(actual["feeds"])),
        "new_exact_urls": sorted(set(actual["feeds"]) - set(original["feeds"])),
        "path_changes": {url: {"before": original["feeds"][url], "after": actual["feeds"][url]}
                         for url in original["feeds"].keys() & actual["feeds"].keys()
                         if original["feeds"][url] != actual["feeds"][url]},
        "import_response": imported,
    }
    put_json(output, "roundtrip.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    label, sample, directory = sys.argv[1:]
    assert label in {"flat", "nested1", "nested2", "renamed"}
    run(label, Path(sample), Path(directory))
