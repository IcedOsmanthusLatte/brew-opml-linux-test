"""Independently verify the Mac round-trip evidence accepted from GitHub Actions."""
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FIXTURE_SHA = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
APP_VERSIONS = {"netnewswire": "7.3.1", "vienna": "3.10.8"}
EXPECTED = {(app, mode, run) for app in APP_VERSIONS for mode in ("baseline", "nested") for run in (1, 2)}
ADAPTATIONS = {(app, mode, run) for app, mode in (("netnewswire", "flattened"), ("vienna", "unique")) for run in (1, 2)}
RELEASES = {
    "netnewswire": ("280ffd15e2df86a39146410d362f2bafca2c559f33cab78cd538669e96245ab9", "M8L2WTLA8W"),
    "vienna": ("6833fecb70c23c1ba98868086e2245c9a05a4f5a4960b019b64ca3728aaa85bd", "KUU2LM7U9K"),
}
CLEARED_LABELS = {
    "netnewswire": {"Smart Feeds", "Today", "All Unread", "Starred", "On My Mac"},
    "vienna": {"Today's Articles", "Unread Articles", "Marked Articles", "Trash"},
}
SCREENSHOTS = {phase: f"{phase}-window.png" for phase in ("before-import", "imported", "restarted")}


def screenshot_digests(directory):
    raw_receipt = directory / "screenshot-receipt.json"
    digests = json.loads(raw_receipt.read_text("utf-8")) if raw_receipt.exists() else {}
    for phase, name in SCREENSHOTS.items():
        path = directory / name
        if path.exists():
            assert path.stat().st_size > 1_000
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert not raw_receipt.exists() or digests.get(phase) == digest
            digests[phase] = digest
        else:
            assert phase != "before-import" and re.fullmatch(r"[0-9a-f]{64}", digests.get(phase, ""))
    return digests

def subscriptions(path):
    root = ET.parse(path).getroot()
    urls = {}
    groups = []
    def flatten(parent, folders):
        for item in parent.findall("outline"):
            url = item.get("xmlUrl") or item.get("xmlURL") or item.get("xmlurl")
            if url:
                assert url not in urls, f"Subscription repeated: {url}"
                urls[url] = {"path": folders, "metadata": dict(item.attrib)}
            else:
                name = item.get("text") or item.get("title")
                group = folders + [name] if name else folders
                if name:
                    groups.append(group)
                flatten(item, group)
    flatten(root.find("body"), [])
    return root.findtext("head/title"), urls, sorted(groups)

def parse_case(directory):
    setup = json.loads((directory / "environment.json").read_text("utf-8"))
    receipt = json.loads((directory / "artifact-receipt.json").read_text("utf-8"))
    app, mode, trial = setup["app"], setup["mode"], setup["trial"]
    assert (app, mode, trial) in EXPECTED | ADAPTATIONS
    assert setup["version"].split()[0] == APP_VERSIONS[app]
    release_hash, team = RELEASES[app]
    assert setup["release_sha256"] == release_hash
    assert f"TeamIdentifier={team}" in (directory / "signature.txt").read_text("utf-8")
    assert "Mach-O 64-bit executable arm64" in (directory / "architecture.txt").read_text("utf-8")
    assert setup["original_sha256"] == FIXTURE_SHA
    assert hashlib.sha256((directory / "fixture.opml").read_bytes()).hexdigest() == setup["fixture_sha256"]
    if mode == "baseline":
        assert setup["fixture_sha256"] == FIXTURE_SHA
    screenshots = screenshot_digests(directory)
    cleared = (directory / "before-import-sidebar.txt").read_text("utf-8").splitlines()
    non_ui_labels = [line for line in cleared if not any(label in line for label in CLEARED_LABELS[app])]
    assert not non_ui_labels, (app, mode, trial, non_ui_labels)
    assert all(any(label in line for line in cleared) for label in CLEARED_LABELS[app]), (app, mode, trial, cleared)
    for phase in ("imported", "restarted"):
        sidebar = (directory / f"{phase}-sidebar.txt").read_text("utf-8").lower()
        assert all(marker in sidebar for marker in ("bbc", "nasa", "wordpress")), (phase, app, mode, trial)
    title, before, expected_groups = subscriptions(directory / "fixture.opml")
    _, after, observed_groups = subscriptions(directory / "native-export.opml")
    assert before.keys() == after.keys() and len(before) == 3, (before.keys(), after.keys())
    expected_paths = {k: v["path"] for k, v in before.items()}
    observed_paths = {k: v["path"] for k, v in after.items()}
    result = {
        "run_id": receipt["run_id"], "artifact_id": receipt["artifact_id"], "artifact_sha256": receipt["archive_sha256"],
        "app": app, "release": setup["version"], "build": setup["build"], "mode": mode, "trial": trial,
        "release_sha256": release_hash, "architecture": "arm64",
        "macos": (directory / "macos.txt").read_text("utf-8").splitlines(),
        "fixture_sha256": setup["fixture_sha256"], "fixture_title": title, "input_metadata": {k: v["metadata"] for k, v in before.items()},
        "screenshot_sha256": screenshots,
        "expected_paths": expected_paths, "native_export_paths": observed_paths,
        "expected_groups": expected_groups, "native_export_groups": observed_groups,
        "preserved_every_path": expected_paths == observed_paths,
    }
    return (app, mode, trial), result

def compare(directories):
    runs = {}
    for directory in directories:
        key, value = parse_case(directory)
        assert key not in runs, f"Duplicate selected run: {key}"
        runs[key] = value
    missing = EXPECTED - runs.keys()
    untested_adaptations = ADAPTATIONS - runs.keys()
    baseline = next((r for k,r in runs.items() if k[1] == "baseline"), None)
    if baseline:
        assert all(r["input_metadata"] == baseline["input_metadata"] and r["fixture_title"] == baseline["fixture_title"] for r in runs.values())
    variants = {mode for _, mode, _ in runs}
    for mode in variants:
        cases = [r for k,r in runs.items() if k[1] == mode]
        assert len({r["fixture_sha256"] for r in cases}) == 1
        for app in APP_VERSIONS:
            a, b = runs.get((app, mode, 1)), runs.get((app, mode, 2))
            if a and b:
                assert a["native_export_paths"] == b["native_export_paths"], (app, mode)
                assert a["native_export_groups"] == b["native_export_groups"], (app, mode)
    assert len({(r["architecture"], *r["macos"]) for r in runs.values()}) <= 1, "Runner environments differ"
    return {"complete_required_matrix": not missing, "missing": sorted(missing),
            "complete_adaptations": not untested_adaptations, "untested_adaptations": sorted(untested_adaptations),
            "runs": [runs[k] for k in sorted(runs)]}

if __name__ == "__main__":
    print(json.dumps(compare(map(Path, sys.argv[1:])), indent=2, ensure_ascii=False))
