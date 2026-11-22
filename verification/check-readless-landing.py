"""Verify the exact two public links in the Readless editorial email."""
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright

URL = "https://getbrew.app/blog/windows-rss-reader-migration/#what-the-restore-kept"
SAMPLE = "https://getbrew.app/collections/downloads/opml-migration-normal.opml"
SHA = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
OUTPUT = Path("readless-landing-proof")
OUTPUT.mkdir(exist_ok=True)
VIEWS = [("mobile", 390, 844, True), ("desktop", 1440, 900, False)]
ROUTES = [
    ["FeedFlow · OPML only", "Missing"],
    ["FeedFlow · OPML, then article CSV", "Restored"],
    ["FeedFlow · article CSV before feeds, then OPML", "Missing"],
    ["FeedFlow · retry the same CSV after OPML", "Restored"],
    ["Fluent Reader · OPML only", "Missing"],
    ["Fluent Reader · native .frdata backup", "Restored"],
]
FEEDS = {
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.nasa.gov/news-release/feed/",
    "https://wordpress.org/news/feed/",
}
MEASURE = r"""
() => {
  const heading = document.getElementById('what-the-restore-kept');
  const table = heading?.nextElementSibling;
  const visible = element => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const top = document.elementFromPoint(rect.left + Math.min(25, rect.width / 2),
      (rect.top + rect.bottom) / 2);
    return rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 &&
      rect.bottom <= innerHeight + 1 && rect.width > 0 && rect.height > 0 &&
      getComputedStyle(element).visibility === 'visible' &&
      Boolean(top && (top === element || element.contains(top)));
  };
  const rows = Array.from(table?.tBodies?.[0]?.rows ?? []);
  return {
    width: innerWidth,
    scroll_y: scrollY,
    heading: heading?.innerText,
    heading_visible: visible(heading),
    table_visible: visible(table),
    every_row_visible: rows.every(visible),
    no_horizontal_overflow: document.documentElement.scrollWidth <= innerWidth + 1,
    rows: rows.map(row => Array.from(row.cells, cell => cell.innerText.trim())),
  };
}
"""


def check(browser, view):
    name, width, height, mobile = view
    context = browser.new_context(
        viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile,
        service_workers="block", reduced_motion="reduce", accept_downloads=True,
    )
    result = {"device": name, "expected_width": width, "height": height}
    try:
        context.route("**/*", lambda route: route.continue_() if route.request.method in {"GET", "HEAD"} else route.abort())
        page = context.new_page()
        response = page.goto(URL, wait_until="load", timeout=22000)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        result.update(status=response.status, url=page.url, **page.evaluate(MEASURE))
        page.screenshot(path=str(OUTPUT / f"{name}.png"))
        preview = context.new_page()
        preview.set_content(
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<p><a href="{escape(URL)}">Six-route results</a></p>'
            f'<p><a href="{escape(SAMPLE)}">Direct three-source OPML sample</a></p>'
        )
        sample_link = preview.get_by_role("link", name="Direct three-source OPML sample")
        assert sample_link.get_attribute("href") == SAMPLE
        with preview.expect_download(timeout=12000) as waiting:
            sample_link.click()
        download = waiting.value
        data = download.path().read_bytes()
        opml = ET.fromstring(data)
        feed_urls = {entry.get("xmlUrl") for entry in opml.iter("outline") if entry.get("xmlUrl")}
        result["download"] = {
            "from_separate_email_preview_link": True, "url": download.url,
            "filename": download.suggested_filename, "bytes": len(data),
            "sha256": sha256(data).hexdigest(), "sample_three_exact_urls": feed_urls == FEEDS,
        }
        result["passed"] = (
            response.status == 200 and page.url == URL and result["width"] == width
            and result["heading"] == "What the restore kept" and result["scroll_y"] > 0
            and result["heading_visible"] and result["table_visible"] and result["every_row_visible"]
            and result["no_horizontal_overflow"] and result["rows"] == ROUTES
            and download.url == SAMPLE and len(data) == 755 and sha256(data).hexdigest() == SHA
            and opml.tag == "opml" and feed_urls == FEEDS
        )
    except Exception as error:
        result.update(passed=False, error=re.sub(r"\s+", " ", str(error))[:400])
    finally:
        context.close()
    return result


with sync_playwright() as playwright:
    chromium = playwright.chromium.launch(headless=True)
    try:
        views = [check(chromium, size) for size in VIEWS]
    finally:
        chromium.close()

report = {
    "target": URL, "separate_sample_url": SAMPLE,
    "environment": "GitHub-hosted Ubuntu, anonymous Chromium, independent mobile and desktop contexts",
    "passed": all(view["passed"] for view in views), "views": views,
}
body = json.dumps(report, ensure_ascii=False, indent=2)
(OUTPUT / "landing.json").write_text(body + "\n", encoding="utf-8")
print(body)
raise SystemExit(0 if report["passed"] else 1)
