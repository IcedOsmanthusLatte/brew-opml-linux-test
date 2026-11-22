"""Classify feed-provided body and origin-only markers without saving text."""
from dataclasses import dataclass
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
import unicodedata
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

FEED_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
WORD = re.compile(r"[a-z0-9]+")


def normalized(text):
    return " ".join(WORD.findall(unicodedata.normalize("NFKC", unescape(text)).lower()))


def digest(text):
    return sha256(text.encode()).hexdigest()


class HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.articles = []
        self.bbc_paragraphs = []
        self.paragraph = None
        self.article_depth = 0
        self.hidden = 0
        self.stack = []
        self.bbc_text_block = 0
        self.paragraph_in_bbc_block = False

    def handle_starttag(self, tag, attrs):
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                       "param", "source", "track", "wbr"}:
            text_block = any(v == "text-block" for k, v in attrs if k in {"data-component", "data-testid"})
            self.stack.append((tag, text_block))
            self.bbc_text_block += text_block
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag == "article":
            self.article_depth += 1
        if tag == "p" and self.article_depth and not any(t in {"nav", "aside", "header", "footer"} for t, _ in self.stack):
            self.paragraph = []
            self.paragraph_in_bbc_block = bool(self.bbc_text_block)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "article":
            self.article_depth = max(0, self.article_depth - 1)
        if tag == "p" and self.paragraph is not None:
            paragraph = normalized(" ".join(self.paragraph))
            self.articles.append(paragraph)
            if self.paragraph_in_bbc_block:
                self.bbc_paragraphs.append(paragraph)
            self.paragraph = None
        for index in reversed(range(len(self.stack))):
            if self.stack[index][0] == tag:
                self.bbc_text_block -= sum(marked for _, marked in self.stack[index:])
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.hidden:
            return
        self.text.append(data)
        if self.paragraph is not None:
            self.paragraph.append(data)


def parse_html(raw):
    parser = HTML()
    parser.feed(raw)
    return parser


def clear(raw):
    return normalized(" ".join(parse_html(raw).text))


def get(url, limit):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 Brew Feed Reader Test",
                                "Cache-Control": "no-cache", "Accept-Language": "en-US,en;q=0.8"})
    with urlopen(req, timeout=10) as response:
        data = response.read(limit + 1)
        if response.status != 200 or len(data) > limit:
            raise ValueError("Public source response outside control")
    return data


def markers(text, excluded=()):
    words = text.split()
    picks = []
    for pos in range(0, len(words) - 6, 7):
        phrase = " ".join(words[pos:pos + 7])
        if not any(phrase in item for item in excluded):
            picks.append(phrase)
    return tuple(picks)


@dataclass
class Item:
    title: str
    url: str
    guid: str
    source: str
    snippet: str
    body: str
    origin: tuple = ()
    origin_verified: bool = False

    def prove_origin(self):
        if self.source != "BBC" or self.origin_verified:
            return
        matches = []
        page = parse_html(get(self.url, 6_000_000).decode("utf-8", "replace"))
        paragraphs = page.bbc_paragraphs if len(page.bbc_paragraphs) >= 6 else page.articles
        for paragraph in paragraphs[1:]:
            if len(paragraph.split()) >= 17:
                matches.extend(markers(paragraph, (self.title, self.snippet, self.body)))
        if len(matches) < 8:
            raise ValueError("BBC origin marker control insufficient")
        self.origin = tuple(matches)
        self.origin_verified = True

    def classify(self, observed):
        def hits(candidates):
            return sorted({digest(phrase) for phrase in candidates if any(phrase in text for text in observed)})

        title = any(self.title in text for text in observed)
        snippet = hits(markers(self.snippet))
        content = hits(markers(self.body, (self.title, self.snippet)))
        origin = hits(self.origin)
        label = "no-verified-body"
        if title and len(origin) >= 2:
            label = "origin-body-beyond-feed"
        elif title and len(content) >= 2:
            label = "feed-provided-body"
        elif title and snippet:
            label = "feed-excerpt"
        return {"source": self.source, "title_sha256": digest(self.title), "url_sha256": digest(self.url),
                "guid_sha256": digest(self.guid), "title_observed": title, "kind": label,
                "feed_excerpt_words": len(self.snippet.split()), "feed_encoded_words": len(self.body.split()),
                "bbc_origin_validated": self.origin_verified, "bbc_origin_candidate_count": len(self.origin),
                "excerpt_marker_sha256": snippet, "feed_body_marker_sha256": content, "origin_marker_sha256": origin}


def load_items():
    control = ET.parse(Path(__file__).with_name("control.opml"))
    feeds = [x.attrib["xmlUrl"] for x in control.iter("outline") if "xmlUrl" in x.attrib]
    observed = {}
    for source, feed in zip(("BBC", "NASA", "WordPress"), feeds, strict=True):
        try:
            channel = ET.fromstring(get(feed, 5_000_000)).find("channel")
        except (OSError, ET.ParseError, ValueError):
            observed[source] = []
            continue
        items = []
        for node in channel.findall("item"):
            item = Item(clear(node.findtext("title", "")), node.findtext("link", ""), node.findtext("guid", ""),
                        source, clear(node.findtext("description", "")), clear(node.findtext(FEED_CONTENT, "")))
            if not (item.title and item.url.startswith("https://") and item.guid):
                continue
            if (source == "BBC" and len(item.snippet.split()) >= 7 and len(item.body.split()) < 35
                or source != "BBC" and len(markers(item.body, (item.title, item.snippet))) >= 5):
                items.append(item)
        observed[source] = items
    assert observed["BBC"] and (observed["NASA"] or observed["WordPress"])
    return observed
