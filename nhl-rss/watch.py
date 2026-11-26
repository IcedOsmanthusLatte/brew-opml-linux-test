"""Watch the same six literal public URLs for a distinct later publication."""

from pathlib import Path
import json
import sys
import time
from urllib.error import URLError

from capture import now, snapshot
from compare import content_updates


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", "utf-8")


def main():
    root = Path(__file__).resolve().parent
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((root / "snapshot-a.json").read_text("utf-8"))
    first_cloud = None
    rounds = []
    for attempt in range(10):
        try:
            observed = snapshot()
        except (OSError, ValueError, URLError) as error:
            rounds.append({"time": now(), "error": f"{type(error).__name__}: {error}"})
        else:
            if first_cloud is None:
                first_cloud = observed
                write(out / "first-cloud.json", first_cloud)
            fresh = content_updates(baseline, observed)
            cloud_fresh = content_updates(first_cloud, observed)
            change = {key: row["new_published_urls"] for key, row in fresh.items()
                      if row["new_published_urls"]}
            cloud_change = {key: row["new_published_urls"] for key, row in cloud_fresh.items()
                            if row["new_published_urls"]}
            rounds.append({"time": now(), "strict_updates": change, "cloud_updates": cloud_change})
            write(out / "last-cloud.json", observed)
            if change:
                write(out / "snapshot-b.json", observed)
                write(out / "observations.json", rounds)
                print(json.dumps(change), flush=True)
                return
        write(out / "observations.json", rounds)
        print(f"Observation {attempt + 1} complete without a proven newer article.", flush=True)
        if attempt < 9:
            time.sleep(45)


if __name__ == "__main__":
    main()
