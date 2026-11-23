"""Anonymous cloud acceptance for the ITPro email's exact public destination."""
from contextlib import closing
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

LANDING = "https://getbrew.app/blog/cve-rss-feeds/#ubuntu-cve-comparison"
TARGETS = (
    "https://ubuntu.com/security/notices/USN-7604-1",
    "https://ubuntu.com/security/CVE-2025-32462",
    "https://ubuntu.com/security/CVE-2025-32463",
)
OUT = Path("layout-proof")
OUT.mkdir(exist_ok=True)
states = []
with sync_playwright() as pw:
    with closing(pw.chromium.launch()) as browser:
        for width in (390, 1440):
            with closing(browser.new_context(viewport={"width": width, "height": 900},
                                             reduced_motion="reduce", service_workers="block")) as context:
                page = context.new_page()
                response = page.goto(LANDING, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_function("document.fonts.status === 'loaded'")
                head = page.locator("#ubuntu-cve-comparison")
                table = page.locator("article.article-body table")
                row_text = table.get_by_role("row").all_text_contents()
                visible = head.evaluate("""el => {
                    const rect = el.getBoundingClientRect();
                    return rect.top >= 0 && rect.top < 260 &&
                        el.contains(document.elementFromPoint(rect.left + 8, rect.top + 8));
                }""")
                fits = table.evaluate("""el => {
                    const rect = el.getBoundingClientRect();
                    return rect.left >= -1 && rect.right <= innerWidth + 1 && rect.bottom < innerHeight &&
                        [...el.querySelectorAll('td,th')].every(td => td.scrollWidth <= td.clientWidth + 1);
                }""")
                links = [page.locator(f'article.article-body a[href^="{url}"]').count() == 1 for url in TARGETS]
                expected = ("CVE-2025-32462", "1.9.9-1ubuntu2.5", "Absent from the checked catalog",
                            "CVE-2025-32463", "unaffected", "September 29, 2025")
                correct = len(row_text) == 3 and all(t in ' '.join(row_text) for t in expected)
                page.screenshot(path=OUT / f"kate-{width}.jpg", type="jpeg", quality=72)
                states.append({"width": width, "http_200": response.status == 200 and page.url == LANDING,
                               "heading_visible": visible, "table_fits_in_first_view": fits,
                               "correct_rows": correct, "canonical_source_links_present": all(links)})
result = {"passed": all(all(v for k, v in s.items() if k != "width") for s in states),
          "states": states, "url": LANDING, "cloud_runner": True, "run_id": os.environ.get("GITHUB_RUN_ID")}
(OUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
raise SystemExit(0 if result["passed"] else 2)
