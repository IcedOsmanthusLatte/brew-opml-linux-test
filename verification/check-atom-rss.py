"""Check the approved Atom and RSS comparison in isolated GitHub-hosted Chromium."""

from contextlib import closing
import json
import os
from pathlib import Path
import re
import sys

from playwright.sync_api import sync_playwright


LIVE = "https://getbrew.app/blog/atom-vs-rss/"
PREVIEW = re.compile(r"https://getbrew\.app/blog/p/[0-9a-f-]{36}/")
OUT = Path("layout-proof")


def check(browser, url, phase):
    with closing(browser.new_context(viewport={"width": 1440, "height": 920},
                                     reduced_motion="reduce", service_workers="block")) as context:
        page = context.new_page()
        page.set_default_timeout(8000)
        context.route("**/*", lambda route: route.continue_()
                      if route.request.method in {"GET", "HEAD"} else route.abort())
        response = page.goto(url, wait_until="domcontentloaded", timeout=18000)
        assert response and response.status == 200 and page.url == url
        body = page.locator("article.article-body")
        cover = page.locator(".article-illustration img").first
        table = body.locator("table")
        anchor = body.locator("#atom-rss-test-results")
        states = []
        for width in (390, 768, 1440):
            for theme in ("light", "dark"):
                page.set_viewport_size({"width": width, "height": 920})
                page.emulate_media(color_scheme=theme)
                page.locator(f'button[data-theme-value="{theme}"]').evaluate("button => button.click()")
                page.evaluate("window.scrollTo(0,0)")
                cover_ok = cover.evaluate("""async img => {
                  await img.decode();
                  return Boolean(img.naturalWidth && img.naturalHeight && img.alt);
                }""")
                make_screenshot = phase == "live" and (width, theme) in ((390, "light"), (1440, "dark"))
                if make_screenshot:
                    page.screenshot(path=str(OUT / f"{width}-{theme}-top.jpg"), type="jpeg", quality=80)
                heading_ok = anchor.evaluate("""heading => {
                    heading.scrollIntoView({block: 'start', behavior: 'instant'});
                    const box = heading.getBoundingClientRect();
                    const hit = document.elementFromPoint(box.left + 12, box.top + box.height / 2);
                    return Boolean(hit && heading.contains(hit));
                }""")
                layout = table.evaluate("""table => ({
                    fits: table.getBoundingClientRect().right <= innerWidth + 1
                      && [...table.querySelectorAll('th, td')].every(td => td.scrollWidth <= td.clientWidth + 1)
                      && document.documentElement.scrollWidth <= innerWidth + 1,
                    rows: table.querySelectorAll('tbody tr').length,
                    headers: table.querySelectorAll('th[scope="col"]').length,
                    h1: document.querySelectorAll('h1').length,
                    theme: document.documentElement.dataset.theme
                })""")
                okay = (cover_ok and heading_ok and layout["fits"] and layout["rows"] == 2
                        and layout["headers"] == 3 and layout["h1"] == 1 and layout["theme"] == theme)
                states.append({"width": width, **layout, "unobscured": heading_ok, "passed": okay})
                if make_screenshot:
                    page.screenshot(path=str(OUT / f"{width}-{theme}-table.jpg"), type="jpeg", quality=80)
        page.set_viewport_size({"width": 320, "height": 760})
        narrow_fit = table.evaluate("""table => table.getBoundingClientRect().right <= innerWidth + 1
            && document.documentElement.scrollWidth <= innerWidth + 1
            && [...table.querySelectorAll('th,td')].every(td => td.scrollWidth <= td.clientWidth + 1)""")
        source_links = all(body.locator(f'a[href^="{href}"]:not([href^="{href}atom/"])').count() == 1 for href in (
            "https://ubuntu.com/security/notices/rss.xml",
            "https://ubuntu.com/security/notices/atom.xml",
            "https://wordpress.org/news/feed/",
            "https://wordpress.org/news/feed/atom/",
        ))
        return {"passed": narrow_fit and source_links and all(state["passed"] for state in states),
                "states": states, "narrow_320_fits": narrow_fit, "source_links_present": source_links,
                "cloud_runner": True, "browser": browser.version}


def main():
    phase = sys.argv[1]
    digest = None
    OUT.mkdir(exist_ok=True)
    try:
        if phase == "preview":
            file = Path(sys.argv[2])
            url, digest = file.read_text().splitlines()
            file.unlink()
            assert PREVIEW.fullmatch(url) and re.fullmatch("[0-9a-f]{64}", digest)
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
        result = {"passed": False, "error_type": type(error).__name__}
    result.update(phase=phase, run_id=os.environ.get("GITHUB_RUN_ID"))
    if phase == "preview":
        result["body_sha256"] = digest
    (OUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Layout {phase}: {'PASS' if result['passed'] else 'FAIL'}")
    return int(not result["passed"])


if __name__ == "__main__":
    sys.exit(main())
