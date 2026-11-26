"""Capture the six public sources and club news indexes, retaining metadata only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import sys
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


FEEDS = {
    "cbs-nhl": "https://www.cbssports.com/rss/headlines/nhl/",
    "cbs-all": "https://www.cbssports.com/rss/headlines/",
    "espn-nhl": "https://www.espn.com/espn/rss/nhl/news",
    "league": "https://www.to-rss.xyz/nhl/news/",
    "islanders": "https://www.to-rss.xyz/nhl/islanders/",
    "mapleleafs": "https://www.to-rss.xyz/nhl/mapleleafs/",
}
CLUBS = {
    club: f"https://www.nhl.com/{club}/news/" for club in ("islanders", "mapleleafs")
}


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(url, root):
    details = urlsplit(urljoin(root, url))
    if details.scheme not in ("http", "https") or not details.netloc:
        return ""
    return urlunsplit(("https", details.netloc.lower(), details.path.rstrip("/") or "/", "", ""))


def get(url):
    start = now()
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Cache-Control": "no-cache",
        "Accept": "*/*",
    })
    with urlopen(request, timeout=22) as response:
        payload = response.read(9_000_000)
        receipt = {
            "url": url,
            "final_url": response.url,
            "status": response.status,
            "request_started": start,
            "retrieved_utc": now(),
            "sha256": sha256(payload).hexdigest(),
            "headers": {k.lower(): response.headers.get(k) for k in
                        ("Content-Type", "Date", "Age", "ETag", "Last-Modified")},
        }
    return receipt, payload


def date(s):
    try:
        v = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        v = datetime.fromisoformat(s)
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat()


def feed(key, url):
    record, payload = get(url)
    channel = ET.fromstring(payload).find("channel")
    if channel is None:
        raise ValueError(f"{key} did not deliver RSS XML")
    record["feed_title"] = (channel.findtext("title") or "").strip()
    record["items"] = []
    for element in channel.findall("item"):
        link = (element.findtext("link") or "").strip()
        raw_date = (element.findtext("pubDate") or "").strip()
        guid = (element.findtext("guid") or "").strip()
        if not (link and raw_date and guid):
            raise ValueError(f"{key} entry missing link, GUID or publication time")
        record["items"].append({
            "url": link,
            "canonical": normalize(link, record["final_url"]),
            "guid": guid,
            "title": (element.findtext("title") or "").strip(),
            "published_rss": raw_date,
            "published_utc": date(raw_date),
        })
    if not record["items"]:
        raise ValueError(f"{key} delivered zero items")
    return key, record


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def club_index(club, url):
    record, payload = get(url)
    html = payload.decode("utf-8", "replace")
    handler = Anchors()
    handler.feed(html)
    single = re.compile(rf"^https://www\.nhl\.com/{club}/news/[^/]+$")
    record["article_urls"] = sorted({
        link for href in handler.hrefs if single.fullmatch(link := normalize(href, url))
    })
    if not record["article_urls"]:
        raise ValueError(f"{club} did not expose any public article links")
    return club, record


def snapshot():
    with ThreadPoolExecutor(8) as pool:
        feeds = [pool.submit(feed, key, url) for key, url in FEEDS.items()]
        clubs = [pool.submit(club_index, key, url) for key, url in CLUBS.items()]
        return {
            "feeds": dict(job.result() for job in feeds),
            "club_indexes": dict(job.result() for job in clubs),
        }


def run():
    result = snapshot()
    target = Path(sys.argv[1])
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps({
        name: {"items": len(row["items"]), "head": row["items"][0]["canonical"],
               "first_published": row["items"][0]["published_utc"], "sha256": row["sha256"]}
        for name, row in result["feeds"].items()
    }))


if __name__ == "__main__":
    run()
