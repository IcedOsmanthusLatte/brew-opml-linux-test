"""Click the Help → iPhone import → existing sample downloads path in cloud Chromium."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright

ROOT = "https://getbrew.app"
HELP = ROOT + "/help/"
ARTICLE = ROOT + "/blog/feedly-export-opml-iphone/"
ANCHOR = "#2-open-the-add-menu-in-brew"
GUIDE = ARTICLE + ANCHOR
OPML = ROOT + "/blog/opml-file/"
LABEL = "OPML import steps for iPhone"
SAMPLES = {
    "opml-migration-normal.opml": ("822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd", 3, 3),
    "opml-migration-duplicate.opml": ("8a6eab8fc5fd32fa35c4e8115667e31f923159dc6ed983fa3eaefda944c8f4ec", 4, 3),
    "opml-migration-missing.opml": ("92a9c72081beee3b90a99c9cf7b18df1d564b522b3fee3ad354e9eff9bcd6a5e", 2, 2),
}
OUT = Path("help-opml-proof")
OUT.mkdir(exist_ok=True)


def open_route(page, width, theme, samples):
    response = page.goto(HELP, wait_until="domcontentloaded", timeout=20000)
    assert response and response.status == 200
    assert page.locator("link[rel=canonical]").get_attribute("href") == HELP
    toggle = page.locator(f'button[data-theme-value="{theme}"]').first
    toggle.evaluate("(el) => el.click()")
    faq = page.locator("#opml")
    assert not faq.evaluate("(el) => el.open")
    faq.locator("summary").click()
    link = faq.get_by_role("link", name=LABEL, exact=True)
    link.scroll_into_view_if_needed()
    assert faq.evaluate("(el) => el.open") and link.count() == 1
    assert link.get_attribute("href") == GUIDE.removeprefix(ROOT)
    help_layout = link.evaluate("""el => {
      const box = el.getBoundingClientRect();
      const line = el.getClientRects()[0];
      const hit = document.elementFromPoint(line.left + Math.min(6,line.width / 2), line.top + 5);
      return {
        fits: document.documentElement.scrollWidth <= innerWidth + 1 && box.left >= 0 && box.right <= innerWidth + 1,
        reachable: !!hit && el.contains(hit),
        underline: getComputedStyle(el).textDecorationLine.includes('underline'),
        theme: document.documentElement.dataset.theme
      };
    }""")
    if (width, theme) in ((390, "light"), (1440, "dark")):
        page.screenshot(path=OUT / f"help-{width}-{theme}.jpg", type="jpeg", quality=76)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=20000) as navigation:
        link.click()
    response = navigation.value
    page.wait_for_url(GUIDE, timeout=8000)
    assert response and response.status == 200 and page.url == GUIDE
    assert page.locator("link[rel=canonical]").get_attribute("href") == ARTICLE
    heading = page.locator(f'[id="{ANCHOR[1:]}"]')
    assert heading.count() == 1 and "Open the add menu in Brew" in heading.inner_text()
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(250)
    guide_layout = heading.evaluate("""el => {
      const box = el.getBoundingClientRect();
      const hit = document.elementFromPoint(box.left + 8, box.top + Math.min(14,box.height / 2));
      const next = el.nextElementSibling;
      const bodyBox = next ? next.getBoundingClientRect() : null;
      return {
        top: Math.round(box.top),
        heading_visible: box.top >= 0 && box.bottom < innerHeight && !!hit && el.contains(hit),
        next_visible: !!bodyBox && bodyBox.top >= 0 && bodyBox.top < innerHeight,
        fits: document.documentElement.scrollWidth <= innerWidth + 1,
        theme: document.documentElement.dataset.theme
      };
    }""")
    if (width, theme) in ((390, "light"), (1440, "dark")):
        page.screenshot(path=OUT / f"guide-{width}-{theme}.jpg", type="jpeg", quality=76)
    passed = (help_layout["fits"] and help_layout["reachable"] and help_layout["underline"]
              and help_layout["theme"] == theme and guide_layout["heading_visible"]
              and guide_layout["next_visible"] and guide_layout["fits"])
    if samples is not None:
        downstream = page.locator('article.article-body a[href*="/blog/opml-file/"]').first
        with page.expect_navigation(wait_until="domcontentloaded", timeout=20000) as next_page:
            downstream.click()
        assert next_page.value and next_page.value.status == 200 and page.url.startswith(OPML)
        for name, expected in SAMPLES.items():
            target = page.locator(f'article.article-body a[href*="/collections/downloads/{name}"]')
            assert target.count() == 1
            with page.expect_download(timeout=15000) as event:
                target.click()
            download = event.value
            raw = download.path().read_bytes()
            file = ET.fromstring(raw)
            urls = [item.attrib["xmlUrl"] for item in file.iter("outline") if "xmlUrl" in item.attrib]
            observed = hashlib.sha256(raw).hexdigest(), len(urls), len(set(urls))
            assert file.tag == "opml" and observed == expected and download.suggested_filename == name
            samples[name] = {"filename":download.suggested_filename, "sha256":observed[0],
                             "items":observed[1], "unique_sources":observed[2]}
    return {"width":width,"theme":theme,"help":help_layout,"guide":guide_layout,"passed":passed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("candidate","live"))
    phase = parser.parse_args().phase
    candidate = Path("help-opml-path/candidate.html").read_bytes() if phase == "candidate" else None
    results = {"phase":phase,"candidate_sha256":hashlib.sha256(candidate).hexdigest() if candidate is not None else None,
               "states":[], "samples":{}, "cloud_runner":True, "run_id":os.environ.get("GITHUB_RUN_ID")}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for width in (390,320,1440):
                for theme in ("light","dark"):
                    context = browser.new_context(viewport={"width":width,"height":900},
                                                  color_scheme=theme, reduced_motion="reduce",
                                                  accept_downloads=True, service_workers="block",
                                                  is_mobile=width<768, has_touch=width<768)
                    try:
                        if candidate:
                            context.route(HELP, lambda route: route.fulfill(
                                status=200, content_type="text/html; charset=utf-8", body=candidate))
                        page = context.new_page()
                        page.set_default_timeout(8000)
                        request_samples = {} if (width, theme) == (390,"light") else None
                        state = open_route(page, width, theme, request_samples)
                        results["states"].append(state)
                        if request_samples is not None:
                            results["samples"] = request_samples
                    except Exception as exc:
                        results["states"].append({"width":width,"theme":theme,"passed":False,"error":str(exc)[:600]})
                    finally:
                        context.close()
        finally:
            browser.close()
    results["passed"] = len(results["samples"]) == 3 and all(row["passed"] for row in results["states"])
    (OUT / "verification.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results))
    return 0 if results["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
