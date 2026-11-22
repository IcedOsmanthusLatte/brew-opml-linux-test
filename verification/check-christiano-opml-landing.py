"""Verify Christiano outreach's exact live landing and click to download in two devices."""

from hashlib import sha256
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright

URL = "https://getbrew.app/blog/self-hosted-rss-reader-opml/#opml-migration-test-results"
SAMPLE = "https://getbrew.app/collections/downloads/opml-migration-normal.opml"
SHA = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
FILENAME = "brew-opml-migration-normal.opml"
OUTPUT = Path("christiano-landing-proof")
OUTPUT.mkdir(exist_ok=True)
VIEWS = [("mobile", 390, 844, True), ("desktop", 1440, 900, False)]
ROUTES = [
    ["FreshRSS → FreshRSS", "Kept", "Paused"],
    ["FreshRSS → Miniflux", "Kept", "Active"],
    ["Miniflux → FreshRSS", "Kept", "Active"],
    ["Miniflux → Miniflux", "Kept", "Paused"],
]

MEASURE = r"""
(sample) => {
  const heading = document.getElementById('opml-migration-test-results');
  const table = document.getElementById('rss-addresses');
  const link = Array.from(heading.closest('article').querySelectorAll('a[href]'))
    .find(a => a.href.split('?')[0] === sample);
  const visible = element => {
    if (!element) return false;
    const rects = Array.from(element.getClientRects());
    return rects.length > 0 && getComputedStyle(element).visibility === 'visible' &&
      rects.every(rect => {
        const x = rect.left + Math.min(rect.width / 2, 25);
        const y = rect.top + rect.height / 2;
        const top = document.elementFromPoint(x, y);
        return rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 &&
          rect.bottom <= innerHeight + 1 && rect.width > 0 && rect.height > 0 &&
          Boolean(top && (top === element || element.contains(top)));
      });
  };
  const routes = Array.from(table.tBodies[0].rows);
  return {
    width: innerWidth,
    scroll_y: scrollY,
    heading: heading.innerText,
    heading_visible: visible(heading),
    whole_table_visible: visible(table),
    every_row_visible: routes.every(visible),
    sample_visible: visible(link),
    no_horizontal_overflow: document.documentElement.scrollWidth <= innerWidth + 1,
    rows: routes.map(row => Array.from(row.cells, cell => cell.innerText.trim())),
    sample_link: link?.href ?? null,
  };
}
"""


def check(browser, view):
    name, width, height, mobile = view
    context = browser.new_context(
        viewport={"width": width, "height": height},
        is_mobile=mobile,
        has_touch=mobile,
        reduced_motion="reduce",
        service_workers="block",
        accept_downloads=True,
    )
    result = {"device": name, "expected_width": width, "height": height}
    try:
        context.route("**/*", lambda route: route.continue_() if route.request.method in {"GET", "HEAD"} else route.abort())
        page = context.new_page()
        page.set_default_timeout(7500)
        response = page.goto(URL, wait_until="load", timeout=22000)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        result.update(status=response.status, url=page.url, **page.evaluate(MEASURE, SAMPLE))
        page.screenshot(path=str(OUTPUT / f"{name}.png"))

        landing_pass = (
            result["status"] == 200 and result["url"] == URL and result["width"] == width
            and result["heading_visible"] and result["whole_table_visible"] and result["every_row_visible"]
            and result["sample_visible"] and result["no_horizontal_overflow"]
            and result["scroll_y"] > 0 and result["rows"] == ROUTES
        )
        if result["sample_link"]:
            sample = page.locator(f'article a[href^="{SAMPLE}"]').first
            with page.expect_download(timeout=12000) as waiting:
                sample.click()
            file = waiting.value
            data = file.path().read_bytes()
            opml = ET.fromstring(data)
            urls = {entry.get("xmlUrl") for entry in opml.iter("outline") if entry.get("xmlUrl")}
            result["download"] = {
                "url": file.url,
                "filename": file.suggested_filename,
                "bytes": len(data),
                "sha256": sha256(data).hexdigest(),
                "is_opml": opml.tag == "opml",
                "distinct_sources": len(urls),
            }
        download = result.get("download", {})
        result["passed"] = (
            landing_pass and download.get("url", "").split("?")[0] == SAMPLE
            and download.get("filename") == FILENAME and download.get("sha256") == SHA
            and download.get("is_opml") is True and download.get("distinct_sources") == 3
        )
    except Exception as error:
        result.update(passed=False, error=re.sub(r"\s+", " ", str(error))[:400])
    finally:
        context.close()
    return result


with sync_playwright() as playwright:
    chromium = playwright.chromium.launch(headless=True)
    try:
        views = [check(chromium, dimensions) for dimensions in VIEWS]
    finally:
        chromium.close()

report = {
    "target": URL,
    "environment": "GitHub-hosted Ubuntu, anonymous Chromium, separate mobile and desktop contexts",
    "passed": all(view["passed"] for view in views),
    "views": views,
}
display = json.dumps(report, ensure_ascii=False, indent=2)
(OUTPUT / "landing.json").write_text(display + "\n", encoding="utf-8")
print(display)
raise SystemExit(0 if report["passed"] else 1)
