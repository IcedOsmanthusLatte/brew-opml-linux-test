"""Verify original club pages for the closed Nov 23 Eastern matchup."""

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

from capture import get, normalize


BASE = "https://www.nhl.com"
ORIGINALS = {
    "league_preview": (
        "league", "preview",
        "/news/toronto-maple-leafs-new-york-islanders-game-preview-november-23-2026",
    ),
    "islanders_preview": (
        "islanders", "preview",
        "/islanders/news/game-preview-islanders-vs-maple-leafs-nov-23-2026",
    ),
    "islanders_takeaways": (
        "islanders", "postgame",
        "/islanders/news/takeaways-islanders-7-game-point-streak-ends-in-4-2-loss-to-maple-leafs",
    ),
    "mapleleafs_game_day": (
        "mapleleafs", "preview", "/mapleleafs/news/game-day-islanders-nov-23-2026",
    ),
    "mapleleafs_recap": (
        "mapleleafs", "postgame", "/mapleleafs/news/recap-leafs-islanders-nov-23-2026",
    ),
}


class ArticleMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = None
        self.meta = {}
        self.schemas = []
        self._json_ld = False
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href")
        if tag == "meta" and attrs.get("content"):
            name = attrs.get("property") or attrs.get("name")
            if name:
                self.meta[name] = attrs["content"]
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._json_ld = True
            self._chunks = []

    def handle_data(self, data):
        if self._json_ld:
            self._chunks.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._json_ld:
            self._json_ld = False
            try:
                value = json.loads("".join(self._chunks))
            except ValueError:
                return
            self.schemas.extend(value if isinstance(value, list) else [value])


def read(item):
    name, (publisher, purpose, path) = item
    url = BASE + path
    receipt, body = get(url)
    parser = ArticleMetadata()
    parser.feed(body.decode("utf-8", "replace"))
    schema_date = next((x.get("datePublished") for x in parser.schemas
                       if isinstance(x, dict) and x.get("datePublished")), None)
    canonical = normalize(parser.canonical or receipt["final_url"], url)
    if canonical != normalize(url, url) or receipt["status"] != 200:
        raise RuntimeError(f"{name} redirects away from the intended club original")
    return name, {
        **receipt, "canonical": canonical, "publisher": publisher, "purpose": purpose,
        "title": parser.meta.get("og:title"),
        "published": parser.meta.get("article:published_time") or schema_date,
    }


if __name__ == "__main__":
    with ThreadPoolExecutor(5) as executor:
        data = dict(executor.map(read, ORIGINALS.items()))
    Path(sys.argv[1]).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({key: [row["status"], row["published"], row["title"]] for key, row in data.items()}))
