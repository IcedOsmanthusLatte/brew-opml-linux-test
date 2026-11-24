"""Verify the NFL guide in a fresh, read-only cloud browser context."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright


PORTABLE_URL = "https://layout-preview.invalid/"


def canonicalize(url):
    parts = urlsplit(url)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if (k, v) != ("ref", "getbrew.app")])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def same_path(first, second):
    a, b = urlsplit(first), urlsplit(second)
    return (
        a.scheme == b.scheme and a.netloc == b.netloc
        and a.path.rstrip("/") == b.path.rstrip("/")
        and a.query == b.query
    )


def validate_request(payload):
    phase = payload["phase"]
    expected = payload["expected"]
    if phase not in ("preview", "live", "portable"):
        raise ValueError("Unexpected verification phase")
    if not all(isinstance(expected[key], str) and expected[key] for key in ("title", "description", "alt")):
        raise ValueError("Missing metadata expectations")
    if len(expected["headings"]) != 5 or len(set(expected["feeds"])) != 4:
        raise ValueError("Missing structure expectations")
    if phase == "portable":
        html = payload["html"]
        if len(html) > 650_000 or 'src="data:image/' not in html:
            raise ValueError("Expected a small self-contained preview with an embedded cover")
        return phase, PORTABLE_URL, expected, html

    url = payload["url"]
    parts = urlsplit(url)
    approved_path = r"/blog/p/[a-zA-Z0-9_-]{20,}/?" if phase == "preview" else r"/blog/[a-z0-9-]+/"
    if (
        parts.scheme != "https" or parts.netloc != "getbrew.app" or parts.query
        or parts.fragment or not re.fullmatch(approved_path, parts.path)
    ):
        raise ValueError("Unexpected verification URL")
    return phase, url, expected, None


def verify_view(browser, width, theme, phase, url, expected, html, output):
    mobile = width < 768
    portable = phase == "portable"
    article_selector = "main article" if portable else "article.article-body"
    feature_selector = "main > figure img" if portable else "figure.article-illustration img"
    context = browser.new_context(
        viewport={"width": width, "height": 850 if mobile else 960},
        is_mobile=mobile,
        has_touch=mobile,
        device_scale_factor=1,
        color_scheme=theme,
        reduced_motion="reduce",
        service_workers="block",
    )
    step = "navigation"
    try:
        def route_read_only(route):
            request = route.request
            remote = urlsplit(request.url)
            if portable:
                if request.is_navigation_request() and request.url == PORTABLE_URL and request.method == "GET":
                    route.fulfill(
                        status=200,
                        headers={"content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:"},
                        content_type="text/html; charset=utf-8",
                        body=html,
                    )
                else:
                    route.abort()
            elif (
                request.method in ("GET", "HEAD")
                and ((remote.scheme, remote.netloc) == ("https", "getbrew.app") or remote.scheme == "data")
            ):
                route.continue_()
            else:
                route.abort()

        context.route("**/*", route_read_only)
        page = context.new_page()
        page.set_default_timeout(7000)
        response = page.goto(url, wait_until="domcontentloaded", timeout=18000)
        if not response or response.status != 200 or not same_path(page.url, url):
            return {"passed": False, "step": step, "status": response.status if response else None}

        step = "theme"
        theme_button = page.locator(f'button[data-theme-value="{theme}"]')
        if theme_button.count() == 1:
            theme_button.evaluate("(node) => node.click()")
        page.evaluate("document.fonts.ready")
        step = "article"
        body = page.locator(article_selector)
        body.wait_for(state="visible")
        feature = page.locator(feature_selector)
        feature.wait_for(state="visible")
        feature.evaluate("(image) => {image.loading='eager'; return image.decode();}")

        snapshot = body.evaluate("""(body, featureSelector) => {
            const simple = (t) => (t || "").replace(/\\s+/g, " ").trim();
            const scroll = document.documentElement.scrollWidth;
            const tables = [...body.querySelectorAll("table")];
            const table = tables[0];
            const rect = table?.getBoundingClientRect();
            const feature = document.querySelector(featureSelector);
            const headings = Array.from(body.querySelectorAll("h2")).map(n => simple(n.textContent));
            const meta = name => document.querySelector('meta[name="' + name + '"]')?.content ?? null;
            const property = name => document.querySelector('meta[property="' + name + '"]')?.content ?? null;
            return {
                viewport: window.innerWidth,
                scroll_width: scroll,
                doc_h1: document.querySelectorAll("h1").length,
                h1: simple(document.querySelector("main h1")?.textContent),
                body_h1: body.querySelectorAll("h1").length,
                headings,
                body_text: simple(body.textContent),
                all_article_links: [...body.querySelectorAll("a[href]")].length,
                table_count: tables.length,
                table_columns: table?.querySelector("tr")?.cells.length || 0,
                table_rows: table?.querySelectorAll("tbody tr")?.length || 0,
                table_width: rect?.width || 0,
                table_in_viewport: Boolean(rect && rect.left >= -1 && rect.right <= innerWidth + 1),
                table_links: [...(table?.querySelectorAll("a[href]") || [])].map(link => ({
                  href: link.href, text: simple(link.textContent), visible: Boolean(link.getClientRects().length)
                })),
                cover: {
                    width: feature.naturalWidth,
                    height: feature.naturalHeight,
                    alt: feature.alt,
                    source: feature.currentSrc,
                    complete: feature.complete
                },
                document_title: document.title,
                meta_description: meta("description"),
                og_title: property("og:title"),
                twitter_title: meta("twitter:title") || property("twitter:title"),
                canonical: document.querySelector('link[rel="canonical"]')?.href || "",
                robots: meta("robots") || "",
                rendered_theme: document.documentElement.dataset.theme || ""
            };
        }""", feature_selector)

        body_text_sha256 = sha256(snapshot.pop("body_text").encode("utf-8")).hexdigest()
        snapshot["body_text_sha256"] = body_text_sha256
        table = snapshot["table_links"]
        headers = response.headers
        checks = {
            "requested_viewport": abs(snapshot["viewport"] - width) <= 1,
            "one_title": (
                snapshot["doc_h1"] == 1 and snapshot["body_h1"] == int(portable)
                and snapshot["h1"] == expected["title"]
            ),
            "headings": snapshot["headings"] == expected["headings"],
            "exactly_13_article_links": snapshot["all_article_links"] == 13,
            "page_no_horizontal_overflow": snapshot["scroll_width"] <= width + 1,
            "usable_two_column_table": (
                snapshot["table_count"] == 1 and snapshot["table_columns"] == 2
                and snapshot["table_rows"] == 4 and snapshot["table_in_viewport"]
            ),
            "four_correct_feed_links": (
                len(table) == 4 and {canonicalize(x["href"]) for x in table} == set(expected["feeds"])
                and all(x["text"] and x["visible"] for x in table)
            ),
            "cover_loaded_with_reviewed_alt": (
                snapshot["cover"]["complete"] and snapshot["cover"]["width"] > 0
                and snapshot["cover"]["height"] > 0 and snapshot["cover"]["alt"] == expected["alt"]
                and bool(snapshot["cover"]["source"])
            ),
            "metadata_title_description": (
                expected["title"] in snapshot["document_title"]
                and snapshot["meta_description"] == expected["description"]
            ),
        }
        if not portable:
            checks["theme"] = snapshot["rendered_theme"] == theme
        if phase == "live":
            checks |= {
                "canonical_correct": snapshot["canonical"] == url,
                "indexable": "noindex" not in (
                    snapshot["robots"] + " " + headers.get("x-robots-tag", "")
                ).lower(),
                "share_titles_correct": snapshot["og_title"] == snapshot["twitter_title"] == expected["title"],
            }
        step = "screenshots"
        if theme == "light" or width in (390, 1440):
            prefix = f"{width}-{theme}"
            page.screenshot(path=str(output / f"{prefix}-hero.jpg"), type="jpeg", quality=77)
            body.locator("table").first.evaluate(
                "(table) => table.scrollIntoView({block:'center',behavior:'instant'})"
            )
            page.screenshot(path=str(output / f"{prefix}-table.jpg"), type="jpeg", quality=77)
        if phase != "live":
            # Keep the preview UUID and embedded image data out of the sealed receipt.
            snapshot.pop("canonical")
            snapshot["cover"].pop("source")
        return {"passed": all(checks.values()), "checks": checks, **snapshot}
    except Exception as exc:
        # Playwright errors include visited URLs and selectors by default.
        return {"passed": False, "step": step, "error_type": type(exc).__name__}
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.request.read_text(encoding="utf-8-sig"))
        phase, url, expected, html = validate_request(payload)
    except (KeyError, TypeError, ValueError, OSError):
        raise SystemExit("Invalid encrypted QA request.") from None
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            themes = ("light",) if phase == "portable" else ("light", "dark")
            for width in (320, 390, 1440):
                for theme in themes:
                    outcome = verify_view(browser, width, theme, phase, url, expected, html, args.output)
                    cases.append({"width": width, "theme": theme, **outcome})
            browser_version = browser.version
        finally:
            browser.close()
    body_hashes = {x.get("body_text_sha256") for x in cases}
    passed = all(case["passed"] for case in cases) and len(body_hashes) == 1 and None not in body_hashes
    result = {
        "phase": phase, "passed": passed, "browser": "Chromium",
        "version": browser_version, "cases": cases, "run_id": os.getenv("GITHUB_RUN_ID"),
    }
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": phase, "passed": passed, "passing_scenarios": sum(c["passed"] for c in cases)}))
    raise SystemExit(not passed)


if __name__ == "__main__":
    main()
