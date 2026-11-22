"""Collect only initial UI metadata and non-content setup images."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from pywinauto import Desktop
import ctypes
import json
import os
import re
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

def feedflow_processes(exe):
    query = (
        "Get-CimInstance Win32_Process | Where-Object { "
        "$_.ExecutablePath -and $_.ExecutablePath.StartsWith($env:BREW_FF_INSTALL, [StringComparison]::OrdinalIgnoreCase) "
        "} | Select-Object Name,ProcessId,ParentProcessId | ConvertTo-Json -AsArray -Compress"
    )
    env = os.environ | {"BREW_FF_INSTALL": str(exe.parent) + "\\"}
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", query], env=env,
                            capture_output=True, text=True, timeout=12,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return json.loads(result.stdout or "[]")


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
    exe = Path(os.environ["BREW_WIN_FEEDFLOW"])
    profile = runtime / "feedflow-bootstrap"
    profile.mkdir(exist_ok=True)
    env = os.environ | {"JAVA_TOOL_OPTIONS": f"-Duser.home={profile} -Dskiko.renderApi=SOFTWARE",
                        "SKIKO_RENDER_API": "SOFTWARE"}
    stream = (runtime / "feedflow-bootstrap.log").open("wb")
    p = subprocess.Popen([str(exe)], cwd=exe.parent, env=env, stdout=stream, stderr=stream)
    observed = {}
    try:
        def lookup():
            processes = feedflow_processes(exe)
            observed["processes"] = processes
            pids = {p.pid, *(s["ProcessId"] for s in processes)}
            wins = [w for w in Desktop(backend="win32").windows(visible_only=False)
                    if w.process_id() in pids]
            observed["windows"] = [{"class":w.class_name(), "visible":w.is_visible(),
                "title_is_feedflow":w.window_text().strip().lower() == "feedflow",
                "rect_wh":[w.rectangle().width(),w.rectangle().height()]} for w in wins]
            possible = [w for w in wins if w.is_visible() and w.rectangle().width() >= 600
                        and (w.class_name().startswith("SunAwt") or w.window_text().strip().lower() == "feedflow")]
            return next(iter(possible), None)
        w = wait(lookup, timeout=70)
        if w.rectangle().width() < 800:
            w.maximize()
        time.sleep(4)
        uia = Desktop(backend="uia").window(handle=w.handle).wrapper_object()
        (evidence / "feedflow-start.json").write_text(json.dumps(
            dict(title=w.window_text(), controls=controls(uia)), indent=2), encoding="utf-8")
        w.capture_as_image().save(evidence / "feedflow-start.png")
    finally:
        before_cleanup = p.poll()
        subprocess.run(["taskkill", "/pid", str(p.pid), "/t", "/f"], capture_output=True)
        stream.close()
        collected = []
        allowed = re.compile(r"exception|error|caused by|JAVA_TOOL_OPTIONS|warning|failed|feedflow.bootstrap", re.I)
        logs = [runtime / "feedflow-bootstrap.log", *profile.rglob("startup-error.log")]
        default_roaming = Path(os.environ["APPDATA"]) / "FeedFlow" / "startup-error.log"
        if default_roaming.exists():
            logs.append(default_roaming)
        for log in logs:
            if log.is_file():
                for line in log.read_text("utf-8", errors="replace").splitlines()[-100:]:
                    if allowed.search(line):
                        clean = re.sub(r"https?://\S+", "(url)", line)
                        clean = re.sub(r"(?i)[A-Z]:[\\/][^\r\n]*", "(runner path)", clean)
                        collected.append(clean[:300])
        diagnostics = {"process_exit_before_cleanup":before_cleanup, "process_exit_after_cleanup":p.poll(), "safe_startup_events":collected[-30:], "observed": observed,
                       "java_runtime_exists":(exe.parent / "runtime").is_dir()}
        (evidence / "feedflow-diagnostics.json").write_text(
            json.dumps(diagnostics, indent=2), encoding="utf-8")


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
