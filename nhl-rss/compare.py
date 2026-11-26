"""Compare a fixed, closed Eastern game day against the original NHL articles."""

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import json


ET = ZoneInfo("America/New_York")
START = datetime(2026, 11, 23, tzinfo=ET)
END = datetime(2026, 11, 24, tzinfo=ET)


def when(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def content_updates(first, second):
    results = {}
    for name, feed in first["feeds"].items():
        old_items = feed["items"]
        new_items = second["feeds"][name]["items"]
        newest = max(when(row["published_utc"]) for row in old_items)
        originals = {row["canonical"] for row in old_items}
        updates = [
            {"canonical": row["canonical"], "published_utc": row["published_utc"]}
            for row in new_items
            if row["canonical"] not in originals
            and newest < when(row["published_utc"]) <= when(second["feeds"][name]["retrieved_utc"])
        ]
        results[name] = {
            "new_published_urls": updates,
            "original_payload_equal": feed["sha256"] == second["feeds"][name]["sha256"],
            "first_read": feed["retrieved_utc"],
            "second_read": second["feeds"][name]["retrieved_utc"],
        }
    return results


def snapshot_data(snapshot, originals):
    output = {}
    for slug, feed in snapshot["feeds"].items():
        dates = [when(item["published_utc"]) for item in feed["items"]]
        closed_rows = [
            row for row in feed["items"] if START <= when(row["published_utc"]) < END
        ]
        closed_urls = {row["canonical"] for row in closed_rows}
        cbs_nonhockey = [
            row["canonical"] for row in feed["items"]
            if row["canonical"].startswith("https://www.cbssports.com/")
            and row["canonical"].split("/")[3]
            in ("nfl", "college-football", "nba", "college-basketball", "mlb", "soccer")
        ]
        output[slug] = {
            "all_feed_items": len(feed["items"]),
            "start_utc": min(dates).isoformat(),
            "end_utc": max(dates).isoformat(),
            "brackets_closed_day": min(dates) <= START and max(dates) >= END,
            "closed_day_entries": len(closed_rows),
            "matching_originals": {
                name: row["canonical"] in closed_urls for name, row in originals.items()
            },
            "definite_cbs_non_hockey_urls_entire_public_sample": cbs_nonhockey,
            "nhl_path_urls_entire_public_sample": sum(
                row["canonical"].startswith("https://www.cbssports.com/nhl/") for row in feed["items"]
            ),
        }
    output["independent_club_indexes"] = {
        key: sorted(set(snapshot["club_indexes"][key]["article_urls"])
                    & {row["canonical"] for row in originals.values() if row["publisher"] == key})
        for key in snapshot["club_indexes"]
    }
    return output


def read(path):
    return json.loads(path.read_text("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-only", action="store_true")
    parser.add_argument("--second", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    first_file = root / "snapshot-a.json"
    second_file = args.second or (root / "snapshot-b.json")
    first, second = read(first_file), read(second_file)
    updates = content_updates(first, second)
    if not any(row["new_published_urls"] for row in updates.values()):
        print("No distinct later publication in the six feed snapshots yet.")
        return 3
    if args.update_only:
        print(json.dumps({k: row["new_published_urls"] for k, row in updates.items()
                          if row["new_published_urls"]}))
        return 0
    originals = read(root / "club-originals.json")
    if any(row["status"] != 200 or not START <= when(row["published"]) < END
           for row in originals.values()):
        raise RuntimeError("An original was not verified inside the closed Eastern game day")
    records = {phase: snapshot_data(snapshot, originals)
               for phase, snapshot in (("a", first), ("b", second))}
    expected = {
        "league": {"league_preview"},
        "islanders": {"league_preview", "islanders_preview", "islanders_takeaways"},
        "mapleleafs": {"league_preview", "mapleleafs_game_day", "mapleleafs_recap"},
    }
    for record in records.values():
        for name, present in expected.items():
            row = record[name]
            actual = {key for key, found in row["matching_originals"].items() if found}
            if not row["brackets_closed_day"] or actual != present:
                raise RuntimeError(f"Coverage or canonical sample has changed for {name}: {actual}")
        if not record["cbs-nhl"]["definite_cbs_non_hockey_urls_entire_public_sample"]:
            raise RuntimeError("CBS NHL non-hockey example was not repeated")
    result = {
        "closed_eastern_game_day": {"begin": START.isoformat(), "end_exclusive": END.isoformat()},
        "originals": originals,
        "source_snapshot_sha256": {p.name: sha256(p.read_bytes()).hexdigest()
                                   for p in (first_file, second_file)},
        "later_publications": updates,
        "timeline_records": records,
    }
    (root / "comparison.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print("All coverage controls passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
