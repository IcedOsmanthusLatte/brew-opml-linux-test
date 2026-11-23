"""Verify the exact Atom/RSS table recipients of the Tad email will open."""
from contextlib import closing
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright


ORIGIN = "https://getbrew.app"
LANDING = ORIGIN + "/blog/atom-vs-rss/#atom-rss-test-results"
FEEDS = {
    "https://wordpress.org/news/feed/": "rss",
    "https://wordpress.org/news/feed/atom/": "feed",
    "https://ubuntu.com/security/notices/rss.xml": "rss",
    "https://ubuntu.com/security/notices/atom.xml": "rss",
}
PUBLISHERS = ("WordPress News", "Ubuntu Security Notices")
OUT = Path("tad-landing-proof")
OUT.mkdir(exist_ok=True)


def bare_url(url):
    parsed = urlsplit(url)
    return parsed.scheme + "://" + parsed.netloc + parsed.path


def feed_status(client):
    records = {}
    for url, tag in FEEDS.items():
        response = client.get(url, timeout=18000)
        root = ET.fromstring(response.body())
        local_name = root.tag.split("}")[-1]
        items = root.findall("./channel/item") if tag == "rss" else root.findall("{*}entry")
        assert response.ok and local_name == tag and items, url
        records[url] = {"status": response.status, "actual_type": tag,
                        "final_url": bare_url(response.url)}
    assert records["https://ubuntu.com/security/notices/atom.xml"]["final_url"] == (
        "https://ubuntu.com/security/notices/rss.xml"
    )
    return records


def viewport_check(browser, width, height):
    with closing(browser.new_context(viewport={"width": width, "height": height},
                                     reduced_motion="reduce", service_workers="block")) as context:
        page = context.new_page()
        response = page.goto(LANDING, wait_until="load", timeout=26000)
        page.wait_for_function("document.fonts.status === 'loaded'")
        heading = page.locator("#atom-rss-test-results")
        heading.wait_for(state="visible")
        # Re-evaluate once fallback fonts and responsive images have settled.
        page.wait_for_timeout(300)
        table = page.locator("article.article-body table")
        rows = [" ".join(s.split()) for s in table.get_by_role("row").all_inner_texts()]
        anchors = page.locator("article.article-body a").evaluate_all("(nodes) => nodes.map(node => node.href)")
        visible_heading = heading.evaluate("""el => {
            const r = el.getBoundingClientRect();
            return r.top >= 0 && r.top < 260 &&
                el.contains(document.elementFromPoint(r.left + 8, r.top + 8));
        }""")
        visible_table = table.evaluate("""el => {
            const r = el.getBoundingClientRect();
            return r.top >= 0 && r.bottom <= innerHeight && r.left >= -1 &&
                r.right <= innerWidth + 1 &&
                [...el.querySelectorAll('td,th')].every(td => td.scrollWidth <= td.clientWidth + 1);
        }""")
        paragraph = heading.locator("xpath=following-sibling::p[1]").inner_text()
        table_matches = len(rows) == 3 and all(
            publisher in row and "10 entries" in row and "20 entries, 10 original links" in row
            for publisher, row in zip(PUBLISHERS, rows[1:], strict=True)
        )
        checks = {"http_and_exact_anchor": response is not None and response.status == 200 and page.url == LANDING,
                  "heading_visible": visible_heading, "table_in_first_view": visible_table,
                  "both_publishers_and_counts": table_matches,
                  "test_context_present": ("two" in paragraph.lower() or "both rounds" in paragraph.lower())
                      and "RSS Guard 5.3.0" in paragraph,
                  "all_four_primary_sources_linked": all(url in set(map(bare_url, anchors)) for url in FEEDS)}
        page.screenshot(path=OUT / f"tad-{width}.jpg", type="jpeg", quality=74, animations="disabled")
        return {"width": width, "height": height, "checks": checks,
                "passed": all(checks.values()), "rows": rows}


with sync_playwright() as pw:
    with closing(pw.chromium.launch()) as browser:
        with closing(browser.new_context()) as probe:
            live_sources = feed_status(probe.request)
        views = [viewport_check(browser, 390, 844), viewport_check(browser, 1440, 900)]

passed = all(view["passed"] for view in views)
report = {"passed": passed, "url": LANDING, "primary_feeds": live_sources,
          "views": views, "cloud_run": os.environ.get("GITHUB_RUN_ID"),
          "anonymous": True, "browser": "Chromium"}
(OUT / "verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
raise SystemExit(0 if passed else 2)
