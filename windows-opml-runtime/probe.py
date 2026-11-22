"""Collect only initial UI metadata and non-content setup images."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from pywinauto import Desktop
import ctypes
import json
import os
import subprocess
import time
import traceback


evidence = Path("windows-opml-evidence")
runtime = Path(os.environ["BREW_WIN_RUNTIME"])
user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def wait(lookup, timeout=45):
    end = time.time() + timeout
    while time.time() < end:
        value = lookup()
        if value:
            return value
        time.sleep(0.4)
    raise TimeoutError("Expected UI did not appear")


def find_windows(pid):
    return [
        w for w in Desktop(backend="uia").windows()
        if w.process_id() == pid and w.is_visible() and w.rectangle().width() > 250
    ]


def controls(window):
    result = []
    for el in window.descendants():
        name = el.element_info.name
        if el.element_info.control_type == "Edit":
            name = "(edit)"
        b = el.rectangle()
        if b.width() and b.height():
            result.append(dict(name=name, kind=el.element_info.control_type,
                               bounds=[b.left, b.top, b.right, b.bottom]))
    return result


def feedflow():
    p = subprocess.Popen([os.environ["BREW_WIN_FEEDFLOW"]], cwd=runtime)
    try:
        w = wait(lambda: next(iter(find_windows(p.pid)), None))
        if w.rectangle().width() < 800:
            w.maximize()
        time.sleep(4)
        (evidence / "feedflow-start.json").write_text(json.dumps(
            dict(title=w.window_text(), controls=controls(w)), indent=2), encoding="utf-8")
        w.capture_as_image().save(evidence / "feedflow-start.png")
    finally:
        subprocess.run(["taskkill", "/pid", str(p.pid), "/t", "/f"], capture_output=True)


def fluent():
    exe = os.environ["BREW_WIN_FLUENT"]
    p = subprocess.Popen([exe, "--remote-debugging-port=9234",
                          f"--user-data-dir={runtime / 'fluent-bootstrap'}"], cwd=runtime)
    try:
        w = wait(lambda: next(iter(find_windows(p.pid)), None))
        w.maximize()
        time.sleep(3)
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9234", timeout=10000)
            page = browser.contexts[0].pages[0]
            page.wait_for_load_state("domcontentloaded")
            selectors = page.locator("button, a, [role=button], [role=tab]").evaluate_all(
                "(items) => items.filter(x=>x.getBoundingClientRect().width).map(x=>"
                "({role:x.getAttribute('role'),title:x.title, aria:x.getAttribute('aria-label'),"
                "text:x.textContent.trim(), data:x.getAttribute('data-icon-name')}))")
            page.screenshot(path=str(evidence / "fluent-start.png"))
            (evidence / "fluent-start.json").write_text(json.dumps(
                dict(title=page.title(), controls=selectors), indent=2), encoding="utf-8")
    finally:
        subprocess.run(["taskkill", "/pid", str(p.pid), "/t", "/f"], capture_output=True)


def main():
    ok = {}
    for name, test in (("feedflow", feedflow), ("fluent", fluent)):
        try:
            test()
            ok[name] = "passed"
        except Exception as exc:
            ok[name] = str(type(exc).__name__)
            traceback.print_exc()
    (evidence / "probe.json").write_text(json.dumps(ok, indent=2), encoding="utf-8")
    if set(ok.values()) != {"passed"}:
        raise SystemExit("One or both desktop UI probes failed")


if __name__ == "__main__":
    main()
