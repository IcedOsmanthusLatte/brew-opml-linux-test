"""Independently read native reader artifacts and build the concise evidence record."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
import json

from capture import FEEDS, normalize
from compare import START, END, when


BASE = Path(__file__).resolve().parent


def read(name):
    return json.loads((BASE / name).read_text("utf-8"))


def digest(data):
    return sha256(data).hexdigest()


def archive(details):
    path = BASE / details["file"]
    assert digest(path.read_bytes()) == details["digest"]
    with ZipFile(path) as z:
        result = json.loads(z.read("result.json"))
        exact = (BASE / "nhl-six-sources.opml").read_bytes()
        assert z.read("public-six-sources.opml") == exact
        outline = list(ElementTree.fromstring(exact).iter("outline"))
        assert [row.attrib["xmlUrl"] for row in outline] == list(FEEDS.values())
        assert result["public_urls_native_imported"] == 6
        assert result["public_opml_sha256"] == digest(exact)
        assert result["native_reader_version"] == "2.3.5"
        expected = {(phase, path) for phase, entries in result["fixture_sha256"].items()
                    for path in entries}
        seen = set()
        for hit in result["verified_origin_requests"]:
            phase, origin_path = hit["phase"], hit["path"]
            archived_xml = z.read(f"fixture/{phase}{origin_path}")
            assert hit["sha256"] == digest(archived_xml)
            seen.add((phase, origin_path))
        assert seen == expected
    return result


def matrix(cohort, original_locations):
    reverse = {value: key for key, value in cohort["source_feed_ids"].items()}
    output = {k: set() for k in FEEDS}
    tracked = {row["canonical"] for row in original_locations.values()}
    for row in cohort["reader_native_api_rows"]:
        source = reverse[row["feed_id"]]
        if START <= when(row["published_at"]) < END:
            url = normalize(row["url"], FEEDS[source])
            if url in tracked:
                output[source].add(url)
    return output


def main():
    receipts = read("workflow-receipts.json")
    snapshots = read("comparison.json")
    native_a = archive(receipts["first"])
    native_ab = archive(receipts["both"])
    assert native_a["initial_empty_stores_verified"] == 1
    assert native_ab["initial_empty_stores_verified"] == 2
    assert native_a["replay_opml_sha256"] == native_ab["replay_opml_sha256"]
    assert native_a["public_opml_sha256"] == native_ab["public_opml_sha256"]
    cohorts = {"a-live": native_a["cohorts"]["live_official"],
               "a-replay": native_ab["cohorts"]["fresh_first"],
               "b-replay": native_ab["cohorts"]["fresh_second"],
               "b-live": native_ab["cohorts"]["live_official"]}
    expected = {}
    for phase in ("a", "b"):
        original_refs = snapshots["originals"]
        expected[phase] = {
            k: {original_refs[key]["canonical"] for key, occurs in v["matching_originals"].items() if occurs}
            for k, v in snapshots["timeline_records"][phase].items()
            if k in FEEDS
        }
        assert matrix(cohorts[f"{phase}-replay"], original_refs) == expected[phase]
    assert matrix(cohorts["b-live"], original_refs) == expected["b"]
    original_preview = snapshots["originals"]["league_preview"]["canonical"]
    for result in cohorts.values():
        dupes = result["cross_feed_duplicate_urls"][original_preview]
        assert len(dupes) == 3 and sorted(x["source"] for x in dupes) == [
            "islanders", "league", "mapleleafs",
        ]
    new_articles = {item["canonical"] for details in snapshots["later_publications"].values()
                    for item in details["new_published_urls"]}
    observed = {key: {normalize(row["url"], "https://www.nhl.com") for row in cohort["reader_native_api_rows"]}
                for key, cohort in cohorts.items()}
    assert not any(new_articles & observed[key] for key in ("a-live", "a-replay"))
    assert all(new_articles <= observed[key] for key in ("b-live", "b-replay"))
    summary = {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "first_capture": snapshots["later_publications"]["league"]["first_read"],
        "second_capture": snapshots["later_publications"]["league"]["second_read"],
        "closed_date": snapshots["closed_eastern_game_day"],
        "new_public_urls": {k: v["new_published_urls"] for k, v in snapshots["later_publications"].items()
                            if v["new_published_urls"]},
        "official_article_urls": {name: {key: value.get(key) for key in (
            "canonical", "status", "publisher", "purpose", "published",
        )} for name, value in snapshots["originals"].items()},
        "confirmed_reader_originals": {phase: {key: sorted(values) for key, values in x.items()}
                                       for phase, x in expected.items()},
        "verified_club_index_urls": {phase: v["independent_club_indexes"]
                                    for phase, v in snapshots["timeline_records"].items()},
        "confirmed_cbs_non_hockey": {
            k: v["cbs-nhl"]["definite_cbs_non_hockey_urls_entire_public_sample"]
            for k, v in snapshots["timeline_records"].items()
        },
        "reader_version": native_ab["native_reader_version"],
        "exact_public_opml_sha256": native_ab["public_opml_sha256"],
        "matching_league_preview_instances": 3,
        "new_public_urls_verified_in_live_and_replay": {"a": False, "b": True},
        "cohorts": {k: {"per_source": v["per_source"], "rows": v["count"]} for k, v in cohorts.items()},
        "github_actions": {key: value["url"] for key, value in receipts.items()},
    }
    (BASE / "READER-EVIDENCE.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", "utf-8",
    )
    print(json.dumps({"readback_ok": True, "publish_gate": True, "counts": summary["cohorts"]}))


if __name__ == "__main__":
    main()
