"""Read-only public two-viewport check for the Linux OPML article."""
import hashlib
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from playwright.sync_api import sync_playwright

URL = "https://getbrew.app/blog/linux-rss-reader-opml/#newsboat-opml-test"
FIXTURE = (
    "https://raw.githubusercontent.com/IcedOsmanthusLatte/brew-opml-linux-test/"
    "f972b9392d3235c159c287f72e583886ca2db855/fixtures/nested-duplicate.opml"
)
OUTPUT = Path("its-foss-landing-proof")
OUTPUT.mkdir(exist_ok=True)
VIEWS = [
    {"name": "mobile", "width": 390, "height": 844, "mobile": True},
    {"name": "desktop", "width": 1440, "height": 900, "mobile": False},
]

MEASURE = r"""
({ fixture }) => {
  const heading = document.getElementById('newsboat-opml-test');
  const fullyVisible = element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const x = rect.left + Math.min(rect.width / 2, 30);
    const y = rect.top + rect.height / 2;
    const topmost = document.elementFromPoint(x, y);
    return rect.top >= 0 && rect.bottom <= innerHeight + 1 && rect.left >= 0 &&
      rect.right <= innerWidth + 1 && rect.width > 0 && rect.height > 0 &&
      style.display !== 'none' && style.visibility === 'visible' &&
      Boolean(topmost && (element === topmost || element.contains(topmost)));
  };
  const visibleBlocks = [];
  for (let node = heading?.nextElementSibling;
       node && !['H2', 'TABLE'].includes(node.tagName);
       node = node.nextElementSibling) {
    if (fullyVisible(node)) visibleBlocks.push(node);
  }
  const link = visibleBlocks.flatMap(node => Array.from(node.querySelectorAll('a[href]')))
    .find(candidate => candidate.href.split('?')[0] === fixture && fullyVisible(candidate));
  const intro = visibleBlocks.map(node => node.innerText).join(' ').replace(/\s+/g, ' ');
  return {
    anchored_heading: Boolean(heading?.matches('h2') && fullyVisible(heading)),
    heading: heading?.innerText ?? null,
    visible_intro: intro,
    has_export_3: /exported 3 subscription addresses/i.test(intro),
    has_initial_0: /imported 0 of 3/i.test(intro),
    has_corrected_3: /imported 3 of 3/i.test(intro),
    has_root_level: /all at the root level/i.test(intro),
    no_horizontal_overflow: document.documentElement.scrollWidth <= innerWidth + 1,
    actual_viewport_width: innerWidth,
    scroll_y: window.scrollY,
    link: link?.href ?? null,
  };
}
"""


def download_result(data):
    root = ET.fromstring(data)
    outline = root.findall(".//outline[@xmlUrl]")

    def max_outline_depth(node, depth=0):
        children = node.findall("outline")
        return max((max_outline_depth(child, depth + 1) for child in children), default=depth)

    return {
        "file_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "opml_version": root.get("version"),
        "xml_valid_opml": root.tag == "opml",
        "entries": len(outline),
        "unique_urls": len({entry.get("xmlUrl") for entry in outline}),
        "nested": max_outline_depth(root.find("body")) >= 3,
    }


def check(browser, view):
    context = browser.new_context(
        viewport={"width": view["width"], "height": view["height"]},
        is_mobile=view["mobile"],
        has_touch=view["mobile"],
        reduced_motion="reduce",
        service_workers="block",
        accept_downloads=True,
    )
    record = {"device": view["name"], "width": view["width"], "height": view["height"]}
    try:
        context.route(
            "**/*",
            lambda route: route.continue_() if route.request.method in {"GET", "HEAD"} else route.abort(),
        )
        page = context.new_page()
        page.set_default_timeout(7500)
        nav = page.goto(URL, wait_until="load", timeout=25000)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        record.update({"page_status": nav.status, "landed_url": page.url, **page.evaluate(MEASURE, {"fixture": FIXTURE})})
        page.screenshot(path=str(OUTPUT / f'{view["name"]}.png'), timeout=7500)
        if record["link"]:
            selector = page.locator(f'a[href^="{FIXTURE}"]').first
            with context.expect_event(
                "response", lambda response: response.url.split("?")[0] == FIXTURE and response.request.method == "GET",
                timeout=12000,
            ) as response:
                selector.click()
            reply = response.value
            data = reply.body()
            record["sample"] = {"http": reply.status, "url": reply.url, **download_result(data)}
        record["passed"] = all([
            record["page_status"] == 200,
            record["landed_url"] == URL,
            record["actual_viewport_width"] == view["width"],
            record["anchored_heading"],
            record["has_export_3"],
            record["has_initial_0"],
            record["has_corrected_3"],
            record["has_root_level"],
            record["no_horizontal_overflow"],
            record["scroll_y"] > 0,
            record.get("sample", {}).get("http") == 200,
            record.get("sample", {}).get("xml_valid_opml") is True,
            record.get("sample", {}).get("entries") == 4,
            record.get("sample", {}).get("unique_urls") == 3,
            record.get("sample", {}).get("nested") is True,
        ])
        return record
    except Exception as exc:
        record.update({"passed": False, "error": re.sub(r"\s+", " ", str(exc))[:500]})
        return record
    finally:
        context.close()


with sync_playwright() as playwright:
    chromium = playwright.chromium.launch(headless=True)
    try:
        results = [check(chromium, view) for view in VIEWS]
    finally:
        chromium.close()

both = all(view["passed"] for view in results)
same_bytes = both and len({view["sample"]["sha256"] for view in results}) == 1
report = {
    "target": URL,
    "environment": "GitHub-hosted Ubuntu, anonymous Chromium, isolated device contexts",
    "both_downloads_exact_match": same_bytes,
    "passed": both and same_bytes,
    "views": results,
}
serialized = json.dumps(report, indent=2)
(OUTPUT / "landing.json").write_text(serialized + "\n", encoding="utf-8")
print(serialized)
raise SystemExit(0 if report["passed"] else 1)
