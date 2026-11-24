"""Check the reviewed podcast example in Brew's public article template."""

from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright


LIVE = "https://getbrew.app/blog/find-podcast-rss-feed/"
SELECTOR = "article.article-body"
CANDIDATE_ID = "patreon-public-preview"
ANCHOR = "For regular episodes, paste the feed address"
OUT = Path("layout-proof")


def test_view(browser, width, theme, html, phase, engine):
    mobile = width < 768
    context = browser.new_context(
        viewport={"width": width, "height": 820 if mobile else 920},
        is_mobile=mobile, has_touch=mobile, color_scheme=theme,
        reduced_motion="reduce", service_workers="block",
    )
    try:
        context.route("**/*", lambda route: route.continue_() if route.request.method in ("GET", "HEAD") else route.abort())
        page = context.new_page()
        page.set_default_timeout(7000)
        response = page.goto(LIVE, wait_until="domcontentloaded", timeout=18000)
        if not response or response.status != 200 or page.url != LIVE:
            return {"passed": False, "error": "public article did not return a direct 200"}
        page.locator(f'button[data-theme-value="{theme}"]').evaluate("button => button.click()")
        page.evaluate("document.fonts.ready")
        body = page.locator(SELECTOR)
        checks = body.evaluate("""(body, input) => {
            const {id, anchorStart, candidateSource, preview, theme} = input;
            const normalize = value => value.replace(/\\s+/g, ' ').trim();
            const text = node => normalize(node.textContent);
            const anchors = [...body.querySelectorAll('p')].filter(node => text(node).startsWith(anchorStart));
            const template = document.createElement('template');
            template.innerHTML = candidateSource;
            const expected = template.content.firstElementChild;
            const expectedTag = template.content.children.length === 1 && expected?.matches(`p#${id}`);
            if (anchors.length !== 1 || !expectedTag) {
                return { passed: false, anchors: anchors.length, expectedTag: Boolean(expectedTag) };
            }
            const anchor = anchors[0];
            const previous = [];
            for (let node = anchor.previousElementSibling; node && previous.length < 10; node = node.previousElementSibling) {
                previous.push(node);
            }
            const previousContext = previous.some(node => /planet money/i.test(text(node))) ||
                text(body).slice(0, text(body).indexOf(text(anchor))).includes('Planet Money');
            const publishedMatches = body.querySelectorAll(`#${id}`).length;
            if (preview && publishedMatches === 0) {
                anchor.before(expected.cloneNode(true));
            }
            const matches = body.querySelectorAll(`#${id}`);
            const actual = matches[0];
            if (matches.length !== 1 || !actual.matches('p')) {
                return { passed: false, matches: matches.length, original_count: publishedMatches };
            }
            const links = (element, stripGhostRef = false) => [...element.querySelectorAll('a')].map(link => ({
                href: stripGhostRef ? link.href.replace(/[?&]ref=getbrew[.]app$/, '') : link.href,
                text: text(link)
            }));
            const actualLinks = [...actual.querySelectorAll('a')];
            const actualLinkDetails = links(actual);
            const exactLinks = actualLinks.length === 2 &&
                JSON.stringify(links(actual, !preview)) === JSON.stringify(links(expected));
            actual.scrollIntoView({block: 'center', behavior: 'instant'});
            const visible = node => [...node.getClientRects()].some(rect => {
                const y = rect.top + rect.height / 2;
                const x = rect.left + Math.min(8, rect.width / 2);
                const hit = document.elementFromPoint(x, y);
                return rect.width > 0 && rect.height > 0 && y >= 0 && y < innerHeight &&
                    x >= 0 && x < innerWidth && Boolean(hit && node.contains(hit));
            });
            const rect = actual.getBoundingClientRect();
            const noOverflow = document.documentElement.scrollWidth <= innerWidth + 1 &&
                rect.left >= 0 && rect.right <= innerWidth + 1 && actual.scrollWidth <= actual.clientWidth + 1;
            const enoughSpace = rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight;
            const title = actual.querySelector('strong');
            const headingCount = document.querySelectorAll('h1').length;
            const appearance = document.documentElement.dataset.theme === theme && noOverflow && enoughSpace &&
                Boolean(title && visible(title)) && actualLinks.every(visible) && headingCount === 1;
            const canonical = document.querySelector('link[rel="canonical"]')?.href ?? null;
            const robots = document.querySelector('meta[name="robots"]')?.content ?? '';
            const appleLinksVisible = actualLinks.filter(visible).length;
            const placement = actual.nextElementSibling === anchor && previousContext;
            const content = text(actual) === text(expected) &&
                [...actual.querySelectorAll('code')].map(text).join('|') === [...expected.querySelectorAll('code')].map(text).join('|');
            const phaseValid = preview ? publishedMatches === 0 : publishedMatches === 1;
            return {
                passed: appearance && exactLinks && placement && content && phaseValid &&
                    canonical === location.href && !/noindex/i.test(robots),
                canonical, robots, exact_links: exactLinks, paragraph_text_matches: content,
                ...(preview ? {} : {rendered_outbound_links: actualLinkDetails}),
                paragraph_above_existing_followup: actual.nextElementSibling === anchor,
                prior_planet_money_examples: previousContext, phase_valid: phaseValid,
                visible_links: appleLinksVisible, no_horizontal_overflow: noOverflow,
                fully_visible: enoughSpace, h1_count: headingCount, rendered_theme: document.documentElement.dataset.theme,
                viewport: innerWidth, paragraph_height: Math.round(rect.height),
                paragraph_font_px: getComputedStyle(actual).fontSize,
            };
        }""", {"id": CANDIDATE_ID, "anchorStart": ANCHOR, "candidateSource": html,
               "preview": phase == "preview", "theme": theme})
        if phase == "live" and (width, theme) in ((320, "light"), (390, "light"), (1440, "dark")):
            page.screenshot(path=str(OUT / f"{engine}-{width}-{theme}.jpg"), type="jpeg", quality=78)
        return checks
    finally:
        context.close()


def main():
    phase = sys.argv[1]
    assert phase in ("preview", "live")
    payload_path = Path(sys.argv[2])
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if phase == "preview":
        payload_path.unlink()
    html = payload["paragraph"]
    paragraph_sha = sha256(html.encode("utf-8")).hexdigest()
    assert paragraph_sha == payload["sha256"], "Candidate bytes did not match the approved hash"
    OUT.mkdir(exist_ok=True)
    states = []
    versions = {}
    with sync_playwright() as playwright:
        for engine in ("chromium", "webkit"):
            browser = getattr(playwright, engine).launch()
            try:
                versions[engine] = browser.version
                for width in (320, 390, 1440):
                    for theme in ("light", "dark"):
                        details = test_view(browser, width, theme, html, phase, engine)
                        states.append({"browser": engine, "width": width, "theme": theme, **details})
            finally:
                browser.close()

    result = {
        "passed": len(states) == 12 and all(state["passed"] for state in states),
        "article": LIVE, "phase": phase, "run_id": os.environ.get("GITHUB_RUN_ID"),
        "candidate_source_sha256": paragraph_sha, "browsers": versions, "states": states,
    }
    (OUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sys.exit(not result["passed"])


if __name__ == "__main__":
    main()
