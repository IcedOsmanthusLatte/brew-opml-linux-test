from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

APP, FIXTURES, EVIDENCE = (Path(arg).resolve() for arg in sys.argv[1:])
DRIVER = Path(__file__).with_name("guard.py").resolve()
FIXTURE_HASHES = {
    "control": "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd",
    "nested-duplicate": "c11a0e7578d170389b3c20f569841cf2dbb368b033034dd9031dc073ef5a9290",
}


def digest(file: Path) -> str:
    return hashlib.sha256(file.read_bytes()).hexdigest()


def inspect_opml(file: Path, *, allow_empty: bool = False) -> dict:
    root = ET.parse(file).getroot()
    body = root.find("body")
    if root.tag != "opml" or body is None:
        raise ValueError(f"Missing OPML root or body: {file}")

    def collect(element: ET.Element, folders: tuple[str, ...] = ()) -> list[dict]:
        feeds = []
        for child in element.findall("outline"):
            url = child.get("xmlUrl")
            if url:
                feeds.append({"url": url, "folders": list(folders), "category": child.get("category")})
            else:
                feeds.extend(collect(child, folders + (child.get("text") or child.get("title") or "",)))
        return feeds

    rows = collect(body)
    if not rows and not allow_empty:
        raise ValueError(f"Application did not export any subscription rows: {file}")
    return {
        "file": str(file.relative_to(EVIDENCE) if file.is_relative_to(EVIDENCE) else file.name),
        "sha256": digest(file),
        "version": root.get("version"),
        "rows": rows,
        "unique": len({row["url"] for row in rows}),
    }


def restore_titles(export: Path, source: Path, output: Path) -> None:
    titles = {
        row.attrib["xmlUrl"]: row.get("title") or row.attrib["text"]
        for row in ET.parse(source).findall(".//outline[@xmlUrl]")
    }
    xml = ET.parse(export)
    for row in xml.findall(".//outline[@xmlUrl]"):
        for attribute in ("title", "text"):
            if not row.get(attribute):
                row.set(attribute, titles[row.attrib["xmlUrl"]])
    xml.write(output, encoding="utf-8", xml_declaration=True)


def run(args: list[str], output: Path, *, timeout: int = 40) -> None:
    with output.open("wb") as stdout, output.with_suffix(output.suffix + ".stderr").open("wb") as stderr:
        subprocess.run(args, stdout=stdout, stderr=stderr, check=True, timeout=timeout)


def newsboat_roundtrip(source: Path, work: Path, output: Path) -> tuple[Path, Path]:
    work.mkdir()
    config = work / "config"
    urls = work / "urls"
    config.touch()
    urls.touch()
    isolated = ["newsboat", "-C", str(config), "-u", str(urls), "-c", str(work / "cache.db")]
    run(isolated + ["-i", str(source)], output / "newsboat-import.txt")
    (output / "newsboat-subscriptions.txt").write_bytes(urls.read_bytes())
    default = output / "newsboat-default.opml"
    tags = output / "newsboat-tags.opml"
    run(isolated + ["-e"], default)
    run(isolated + ["--export-to-opml2"], tags)
    return default, tags


def rss_guard_roundtrip(source: Path, output: Path, label: str) -> Path:
    result = output / f"{label}.opml"
    screenshots = output / label
    screenshots.mkdir()
    run([sys.executable, str(DRIVER), str(APP), str(source), str(result), str(screenshots)],
        output / f"{label}.log", timeout=95)
    return result


def signatures(profile: dict) -> list[tuple]:
    return sorted((row["url"], tuple(row["folders"]), row["category"] or "") for row in profile["rows"])


def experiment(case: str, label: str, scratch: Path, summary: dict) -> None:
    source = FIXTURES / f"{case}.opml"
    if digest(source) != FIXTURE_HASHES[case]:
        raise ValueError(f"Fixture bytes changed before the experiment: {source}")
    output = EVIDENCE / label
    output.mkdir()
    profiles = {"input": inspect_opml(source)}
    summary["cases"][label] = profiles
    default, tags = newsboat_roundtrip(source, scratch / label, output)
    profiles["newsboat_default"] = inspect_opml(default)
    profiles["newsboat_tags"] = inspect_opml(tags)
    profiles["rssguard_direct"] = inspect_opml(rss_guard_roundtrip(source, output, "rssguard-direct"))
    profiles["rssguard_from_default"] = inspect_opml(
        rss_guard_roundtrip(default, output, "rssguard-from-default"), allow_empty=True
    )
    profiles["rssguard_from_tags"] = inspect_opml(
        rss_guard_roundtrip(tags, output, "rssguard-from-tags"), allow_empty=True
    )
    if case == "nested-duplicate":
        for name, raw in (("default", default), ("tags", tags)):
            titled = output / f"newsboat-{name}-with-titles.opml"
            restore_titles(raw, source, titled)
            original = inspect_opml(raw)
            profiles[f"newsboat_{name}_with_titles"] = inspect_opml(titled)
            if signatures(original) != signatures(profiles[f"newsboat_{name}_with_titles"]):
                raise ValueError("Title repair changed subscription URLs, folder paths, or categories")
            profiles[f"rssguard_from_{name}_with_titles"] = inspect_opml(
                rss_guard_roundtrip(titled, output, f"rssguard-from-{name}-with-titles"), allow_empty=True
            )


def save(summary: dict) -> None:
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    (EVIDENCE / "comparison.json").write_text(text, encoding="utf-8")
    print(text, flush=True)


def main() -> None:
    EVIDENCE.mkdir(exist_ok=True)
    summary = {"complete": False, "cases": {}}
    wm = subprocess.Popen(["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        run(["newsboat", "--version"], EVIDENCE / "newsboat-version.txt")
        run([str(APP), "--version"], EVIDENCE / "rssguard-version.txt")
        summary["versions"] = {
            name: (EVIDENCE / f"{name}-version.txt").read_text(encoding="utf-8").strip()
            for name in ("newsboat", "rssguard")
        }
        with tempfile.TemporaryDirectory(prefix="brew-opml-") as tmp:
            work = Path(tmp)
            experiment("control", "control", work, summary)
            experiment("nested-duplicate", "nested-duplicate", work, summary)
            experiment("nested-duplicate", "nested-repeat", work, summary)

        cases = summary["cases"]
        nested = cases["nested-duplicate"]
        repeated = cases["nested-repeat"]
        stable = all(signatures(result) == signatures(repeated[key]) for key, result in nested.items())
        summary["nested_round_trip_reproduced"] = stable
        summary["observed"] = {
            label: {
                "direct_readers_differ": signatures(data["rssguard_direct"]) != signatures(data["newsboat_default"]),
                "rssguard_reimport_modes_differ": signatures(data["rssguard_from_default"]) != signatures(data["rssguard_from_tags"]),
                "source_signature_retained_after_tagged_round_trip": signatures(data["input"]) == signatures(data["rssguard_from_tags"]),
            }
            for label, data in cases.items()
        }
        summary["complete"] = stable
        if not stable:
            raise ValueError("Nested experiment did not reproduce")
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        save(summary)
        wm.terminate()
        wm.wait(timeout=3)


if __name__ == "__main__":
    main()
