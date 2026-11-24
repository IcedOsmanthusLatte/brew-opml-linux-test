"""Confirm the per-feed category workaround in the released web interface."""

import json
import os
import secrets
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rssmonster-opml-native"))
from roundtrip import BASE, FIXTURES, RENAMES, document, opml_paths, request, sha


def main(sample, proof):
    proof.mkdir(parents=True, exist_ok=True)
    source = sample.read_bytes()
    assert sha(source) == FIXTURES["nested"]
    (proof / "fixture.opml").write_bytes(source)
    for attempt in range(65):
        try:
            if request("/api/health")[0] == 200:
                break
        except (OSError, RuntimeError):
            pass
        assert attempt < 64
        time.sleep(1)

    login = {"username": f"brewtest{secrets.token_hex(4)}", "password": secrets.token_urlsafe(18)}
    document("/api/auth/register", "POST", {**login, "password_repeat": login["password"]}, expected=(200, 201))
    with sync_playwright() as pw:
        executable = os.environ.get("CHROME_BINARY")
        options = {"headless": True, "args": ["--no-sandbox"]}
        if executable:
            options["executable_path"] = executable
        browser = pw.chromium.launch(**options)
        page = browser.new_page(accept_downloads=True, viewport={"width": 1250, "height": 950})
        page.goto(BASE)
        page.locator("#username").fill(login["username"])
        page.locator("#password").fill(login["password"])
        page.locator("button.auth-submit[type=submit]").click()
        page.get_by_role("button", name="Open settings", exact=True).click(timeout=45_000)
        page.get_by_role("button", name="Feeds", exact=True).click(timeout=25_000)
        with page.expect_file_chooser() as chooser:
            page.get_by_role("button", name="Import OPML", exact=True).click()
        chooser.value.set_files(sample)
        titles = ("BBC News — World", "NASA News Releases")
        labels = tuple(RENAMES.values())
        table = page.locator(".opml-preview__table-wrapper")
        for title in titles:
            row = table.get_by_role("row").filter(has_text=title)
            assert "Brew Shared" in row.inner_text(timeout=100_000)
        table.screenshot(path=proof / "before-category-edit.png")
        for title, label in zip(titles, labels, strict=True):
            page.get_by_role("button", name=f"Edit category for {title}", exact=True).click()
            page.get_by_role("combobox", name=f"Category for {title}", exact=True).select_option("new")
            page.get_by_role("textbox", name="New category name", exact=True).fill(label)
            page.locator(".opml-preview__category-save").click()
            assert label in table.get_by_role("row").filter(has_text=title).inner_text()
        table.screenshot(path=proof / "after-category-edit.png")
        page.get_by_role("button", name="Import 3 subscriptions", exact=True).click(timeout=25_000)
        with page.expect_download(timeout=45_000) as pending:
            page.get_by_role("button", name="Export OPML", exact=True).click(timeout=40_000)
        assert pending.value.suggested_filename.endswith(".opml")
        exported = proof / "native-export.opml"
        pending.value.save_as(exported)
        browser.close()

    original = opml_paths(source)["feeds"]
    after = opml_paths(exported.read_bytes())["feeds"]
    assert set(after) == set(original)
    changes = {url: paths for url, paths in after.items() if paths != original[url]}
    assert changes == {url: [[name]] for url, name in RENAMES.items()}
    result = {
        "fixture_sha256": sha(source),
        "native_export_sha256": sha(exported.read_bytes()),
        "observed_preview_category_on_both": "Brew Shared",
        "ui_actions": ["Import OPML", "Edit category", "Create new category…", "Import 3 subscriptions", "Export OPML"],
        "export": after,
    }
    (proof / "ui-roundtrip.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(*(Path(value).resolve() for value in sys.argv[1:]))
