"""Verify the approved Brew article using an ephemeral Chromium runner."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys

from playwright.sync_api import sync_playwright


LIVE = "https://getbrew.app/blog/mac-rss-reader-opml/"
SAMPLE = "https://getbrew.app/collections/downloads/opml-migration-normal.opml"
SAMPLE_SHA = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
SELECTOR = "article.article-body"
OUT = Path("layout-proof")
PREVIEW = re.compile(r"https://getbrew\.app/blog/p/[0-9a-f-]{36}/")


def check(browser, url, phase):
    context = browser.new_context(viewport={"width": 1440, "height": 920},
                                  reduced_motion="reduce", service_workers="block")
    states = []
    try:
        page = context.new_page()
        page.set_default_timeout(8000)
        context.route("**/*", lambda route: route.continue_() if route.request.method in ("GET", "HEAD") else route.abort())
        response = page.goto(url, wait_until="domcontentloaded", timeout=18000)
        assert response and response.status == 200 and page.url == url
        body = page.locator(SELECTOR)
        cover = page.locator(".article-illustration img").first
        table = body.locator("table")
        for width in (390, 768, 1440):
            for theme in ("light", "dark"):
                page.set_viewport_size({"width": width, "height": 920})
                page.emulate_media(color_scheme=theme)
                page.locator(f'button[data-theme-value="{theme}"]').evaluate("button => button.click()")
                page.evaluate("window.scrollTo(0,0)")
                cover_info = cover.evaluate("""async img => {
                  await img.decode();
                  return {width: img.naturalWidth, height: img.naturalHeight,
                    visible_width: img.getBoundingClientRect().width, has_alt: Boolean(img.alt)};
                }""")
                table_info = table.evaluate("""table => ({
                    fits: table.getBoundingClientRect().right <= innerWidth + 1
                      && [...table.querySelectorAll('th, td')].every(td => td.scrollWidth <= td.clientWidth + 1),
                    rows: table.querySelectorAll('tbody tr').length,
                    headers: table.querySelectorAll('th[scope="col"]').length,
                    h1: document.querySelectorAll('h1').length,
                    ui_theme: document.documentElement.dataset.theme,
                    overflow: document.documentElement.scrollWidth > innerWidth + 1
                })""")
                if phase == "live" and (width, theme) in ((390, "light"), (1440, "dark")):
                    page.screenshot(path=str(OUT / f"{width}-{theme}-top.jpg"), type="jpeg", quality=80)
                anchor_heading = body.locator("#mac-folder-test-results")
                heading_info = anchor_heading.evaluate("""heading => {
                    heading.scrollIntoView({block: 'start', behavior: 'instant'});
                    const box = heading.getBoundingClientRect();
                    const hit = document.elementFromPoint(box.left + Math.min(12, box.width / 2),
                                                          box.top + box.height / 2);
                    return {top: Math.round(box.top), unobscured: Boolean(hit && heading.contains(hit))};
                }""")
                okay = (all(cover_info.values()) and table_info["fits"] and 2 <= table_info["rows"] <= 8
                        and table_info["headers"] >= 2 and table_info["h1"] == 1
                        and table_info["ui_theme"] == theme and not table_info["overflow"]
                        and heading_info["unobscured"])
                states.append({"width": width, "theme": theme, "cover": cover_info, "table": table_info,
                               "comparison_heading": heading_info, "passed": okay})
                if phase == "live" and (width, theme) in ((390, "light"), (1440, "dark")):
                    page.screenshot(path=str(OUT / f"{width}-{theme}-table.jpg"), type="jpeg", quality=80)
        page.set_viewport_size({"width": 320, "height": 760})
        narrow_fit = table.evaluate("table => document.documentElement.scrollWidth <= innerWidth + 1 && table.getBoundingClientRect().right <= innerWidth + 1")
        sample_link = body.locator(f'a[href^="{SAMPLE}"]').first
        downloads = []
        for width in (390, 1440):
            page.set_viewport_size({"width": width, "height": 920})
            with page.expect_download(timeout=15000) as event:
                sample_link.click()
            download = event.value
            exact = sha256(download.path().read_bytes()).hexdigest() == SAMPLE_SHA
            downloads.append({"width": width, "match": exact and download.suggested_filename.lower().endswith(".opml")})
        good_sample = all(row["match"] for row in downloads)
        anchor = body.locator("#mac-folder-test-results").count() == 1
        direct = []
        for width in (390, 1440):
            fresh = context.new_page()
            try:
                fresh.set_viewport_size({"width": width, "height": 900})
                target = url + "#mac-folder-test-results"
                opened = fresh.goto(target, wait_until="load", timeout=20000)
                fresh.wait_for_function("document.fonts.status === 'loaded'")
                heading = fresh.locator("#mac-folder-test-results")
                visible = heading.evaluate("""el => {
                    const r = el.getBoundingClientRect();
                    const hit = document.elementFromPoint(r.left + 10, r.top + r.height / 2);
                    return r.top >= 0 && r.top < 240 && Boolean(hit && el.contains(hit));
                }""")
                direct.append({"width": width, "passed": opened.status == 200 and fresh.url == target and visible})
            finally:
                fresh.close()
        passed = len(states) == 6 and all(state["passed"] for state in states) and narrow_fit and good_sample and anchor
        return {"passed": passed and all(one["passed"] for one in direct),
                "anchor_present": anchor, "direct_anchor": direct, "sample_hash_matches": good_sample,
                "downloads": downloads, "narrow_320_fits": narrow_fit, "states": states,
                "browser": browser.version, "cloud_runner": True}
    finally:
        context.close()


def main():
    phase = sys.argv[1]
    body_hash = None
    OUT.mkdir(exist_ok=True)
    try:
        if phase == "preview":
            capsule = Path(sys.argv[2])
            url, body_hash = capsule.read_text().splitlines()
            capsule.unlink()
            assert PREVIEW.fullmatch(url) and re.fullmatch("[0-9a-f]{64}", body_hash)
        else:
            assert phase == "live"
            url = LIVE
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                result = check(browser, url, phase)
            finally:
                browser.close()
        result["profile_cleaned"] = True
    except Exception as error:
        # A Playwright error or URL is sensitive before publication.
        result = {"passed": False, "error_type": type(error).__name__}
    result.update(phase=phase, run_id=os.environ.get("GITHUB_RUN_ID"))
    if phase == "preview":
        result["body_sha256"] = body_hash
    (OUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Layout {phase}: {'PASS' if result['passed'] else 'FAIL'}")
    sys.exit(not result["passed"])


if __name__ == "__main__":
    main()
