"""Probe the two RSS samples linked on the live Brew article."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from time import perf_counter, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


ARTICLE = "https://getbrew.app/blog/hacker-news-rss-feed/"
STORY, COMMENT, CHILD, GRANDCHILD = 50599458, 50627737, 50628237, 50628763
API = "https://hacker-news.firebaseio.com/v0/item/{}.json"
SAMPLES = {"item": STORY, "replies": COMMENT}
RSS_ACCEPT = "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1"
HTML_ACCEPT = "text/html,*/*;q=0.2"
CHECK_HEADERS = ("server", "via", "x-cache", "age", "date", "cache-control")


def fetch(url, accept, cap=512_000, referer=None):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": accept}
    if referer:
        headers["Referer"] = referer
    started = perf_counter()
    observation = {"requested": url}
    try:
        with urlopen(Request(url, headers=headers), timeout=12) as response:
            observation.update(status=response.status, effective=response.url,
                               content_type=response.headers.get("Content-Type"),
                               headers={key: response.headers[key] for key in CHECK_HEADERS if key in response.headers})
            body = response.read(cap + 1)
    except HTTPError as exc:
        observation.update(status=exc.code, effective=exc.url,
                           headers={key: exc.headers[key] for key in CHECK_HEADERS if key in exc.headers})
        body = b""
    except (TimeoutError, URLError, OSError) as exc:
        observation.update(status=None, error=type(getattr(exc, "reason", exc)).__name__)
        body = b""
    if len(body) > cap:
        observation["error"] = "body_exceeds_limit"
        body = b""
    observation["elapsed_seconds"] = round(perf_counter() - started, 3)
    if body:
        observation["sha256"] = sha256(body).hexdigest()
    return observation, body


class PublishedLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.matches = {kind: set() for kind in SAMPLES}
        self.anchor_seen = False

    def handle_starttag(self, name, attrs):
        attrs = dict(attrs)
        self.anchor_seen |= attrs.get("id") == "choose-a-hacker-news-feed"
        if name != "a" or not attrs.get("href"):
            return
        href = attrs["href"]
        parsed = urlsplit(href)
        kind = parsed.path.lstrip("/")
        if parsed.scheme != "https" or parsed.netloc != "hnrss.org" or kind not in SAMPLES:
            return
        query = dict(parse_qsl(parsed.query))
        if query.get("id") == str(SAMPLES[kind]) and query.get("count") == "25":
            self.matches[kind].add(href)


def item_id(raw):
    parsed = urlsplit(raw)
    value = dict(parse_qsl(parsed.query)).get("id", "")
    return int(value) if parsed.netloc == "news.ycombinator.com" and parsed.path == "/item" and value.isdigit() else None


def inspect_rss(kind, href, children):
    result, body = fetch(href, RSS_ACCEPT, referer=ARTICLE)
    result.update(kind=kind, passed=False)
    if result.get("status") != 200 or not body:
        return result
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        result["error"] = "invalid_xml"
        return result
    result["root"] = root.tag
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        return result
    ids = [item_id(item.findtext("guid", "")) or item_id(item.findtext("link", ""))
           or item_id(item.findtext("comments", "")) for item in channel.findall("item")]
    result.update(channel_title=channel.findtext("title"), channel_link=channel.findtext("link"),
                  item_ids=ids, item_count=len(ids))
    valid = 0 < len(ids) <= 25 and all(ids) and len(ids) == len(set(ids))
    if kind == "item":
        result["scope_matches"] = CHILD in ids and GRANDCHILD in ids
    else:
        result["scope_matches"] = set(ids) == set(children) and CHILD in ids and GRANDCHILD not in ids
    result["passed"] = bool(valid and result["scope_matches"])
    return result


def fetch_item(identifier):
    response, data = fetch(API.format(identifier), "application/json", 90_000)
    try:
        entry = json.loads(data) or {}
    except (json.JSONDecodeError, UnicodeError):
        entry = {}
    selected = {key: entry.get(key) for key in ("id", "type", "parent", "kids")}
    return selected, response


def run(environment):
    result = {"environment": environment, "started_utc": datetime.now(timezone.utc).isoformat(),
              "article": ARTICLE, "rounds": [], "pass": False}
    with ThreadPoolExecutor(max_workers=5) as pool:
        published = pool.submit(fetch, ARTICLE, HTML_ACCEPT, 1_500_000)
        official = {identifier: pool.submit(fetch_item, identifier)
                    for identifier in (STORY, COMMENT, CHILD, GRANDCHILD)}
        result["article_request"], page = published.result()
        parser = PublishedLinks()
        parser.feed(page.decode("utf-8"))
        result["anchor_found"] = parser.anchor_seen
        result["published_hrefs"] = {kind: sorted(found) for kind, found in parser.matches.items()}
        result["official_hn"] = {identifier: {"item": item, "response": response}
                                 for identifier, future in official.items() for item, response in [future.result()]}
        story, comment, child, grandchild = [result["official_hn"][identifier]["item"]
                                            for identifier in (STORY, COMMENT, CHILD, GRANDCHILD)]
        children = comment.get("kids") or []
        api_ok = (story.get("type") == "story" and comment.get("parent") == STORY
                  and COMMENT in (story.get("kids") or []) and child.get("parent") == COMMENT
                  and CHILD in children and grandchild.get("parent") == CHILD and GRANDCHILD in (child.get("kids") or []))
        result["official_relationships_match"] = api_ok
        if result["article_request"].get("status") != 200 or not parser.anchor_seen:
            result["stop"] = "live_article_or_anchor_missing"
        elif any(len(hrefs) != 1 for hrefs in parser.matches.values()):
            result["stop"] = "two_unique_exact_live_hrefs_not_found"
        else:
            for round_id in (1, 2):
                jobs = [pool.submit(inspect_rss, kind, next(iter(parser.matches[kind])), children) for kind in SAMPLES]
                result["rounds"].append({"number": round_id, "feeds": [job.result() for job in jobs]})
                if round_id == 1:
                    sleep(7)
            result["pass"] = bool(api_ok and all(feed["passed"] for sample in result["rounds"] for feed in sample["feeds"]))
    result["finished_utc"] = datetime.now(timezone.utc).isoformat()
    return result


if __name__ == "__main__":
    destination = Path(sys.argv[2])
    result = run(sys.argv[1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"environment": result["environment"], "pass": result["pass"], "rounds": [
        [{key: f.get(key) for key in ("kind", "status", "error", "item_count", "scope_matches", "passed")}
         for f in r["feeds"]] for r in result["rounds"]]}, ensure_ascii=False))

