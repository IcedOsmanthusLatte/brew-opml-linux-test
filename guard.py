#!/usr/bin/env python3
"""Drive the released RSS Guard GUI and preserve its actual OPML export."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pyatspi


ACCOUNT = "Brew OPML test"
BUTTONS = {"button", "push button", "toggle button"}
EDITORS = {"text", "entry"}
MENU_ITEMS = {"menu item", "check menu item", "menu", "popup menu"}


def xdo(*args):
    subprocess.run(["xdotool", *map(str, args)], check=True, timeout=8)


def normalized(value):
    return re.sub(r"\s+", " ", (value or "").replace("&", "").strip()).casefold()


def desktop():
    return pyatspi.Registry.getDesktop(0)


def descendants(node, depth=0):
    if depth > 28:
        return
    yield node, depth
    try:
        count = node.childCount
    except Exception:
        return
    for index in range(count):
        try:
            child = node.getChildAtIndex(index)
        except Exception:
            continue
        if child:
            yield from descendants(child, depth + 1)


def application():
    root = desktop()
    for child in root:
        if "rssguard" in normalized(child.name).replace(" ", ""):
            return child
    return None


def hits(name=None, roles=None, *, root=None, only_showing=True, only_enabled=False):
    root = root or application()
    if root is None:
        return []
    names = name if isinstance(name, (list, tuple)) else (name,)
    names = {normalized(value) for value in names if value is not None}
    matches = []
    for node, _ in descendants(root):
        try:
            states = node.getState()
            if only_showing and not states.contains(pyatspi.STATE_SHOWING):
                continue
            if only_enabled and not states.contains(pyatspi.STATE_ENABLED):
                continue
            if names and normalized(node.name) not in names:
                continue
            if roles and node.getRoleName() not in roles:
                continue
            matches.append(node)
        except Exception:
            continue
    return matches


def wait_for(label, condition, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.15)
    raise TimeoutError(label)


def find(name, roles=None, *, root=None, seconds=15, enabled=True):
    def first():
        nodes = hits(name, roles, root=root, only_enabled=enabled)
        return nodes[0] if nodes else None

    return wait_for(f"Could not find RSS Guard control {name!r}", first, seconds)


def bounds(node):
    box = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
    if box.width <= 0 or box.height <= 0 or box.x < 0 or box.y < 0:
        raise RuntimeError(f"Unusable geometry for {node.name!r}: {box}")
    return box


def move(node):
    box = bounds(node)
    xdo("mousemove", box.x + box.width // 2, box.y + box.height // 2)


def click(node, count=1):
    move(node)
    xdo("click", "--repeat", count, "--delay", 50, 1)


def type_into(node, value):
    try:
        node.queryComponent().grabFocus()
        node.queryEditableText().setTextContents(str(value))
        if node.queryText().getText(0, -1) == str(value):
            return
    except Exception:
        pass
    click(node)
    xdo("key", "--clearmodifiers", "ctrl+a")
    xdo("type", "--clearmodifiers", "--delay", 0, value)


def edit_for_label(dialog, label_name):
    labels = hits(label_name, {"label"}, root=dialog)
    for label in labels:
        for relation in label.getRelationSet():
            if relation.getRelationType() == pyatspi.RELATION_LABEL_FOR:
                return relation.getTarget(0)
    editors = hits(roles=EDITORS, root=dialog, only_enabled=True)
    for editor in editors:
        if normalized(editor.name) == normalized(label_name):
            return editor
    if len(editors) != 1:
        raise RuntimeError(f"Could not disambiguate the input for {label_name!r}")
    return editors[0]


def choose_file(title, filepath, action):
    dialog = find(title, {"dialog", "frame"})
    editor = edit_for_label(dialog, "File name:")
    type_into(editor, filepath)
    button = find(action, BUTTONS, root=dialog)
    click(button)


def capture(directory, name):
    screenshots = directory / f"{name}.png"
    subprocess.run(
        ["import", "-window", "root", "-resize", "1280x960", str(screenshots)],
        check=True,
        timeout=10,
    )


def record_accessibility_tree(path):
    lines = []
    try:
        for node, depth in descendants(desktop()):
            try:
                states = node.getState()
                if states.contains(pyatspi.STATE_SHOWING) or depth < 3:
                    lines.append(f"{'  ' * depth}{node.getRoleName()} {node.name!r}")
            except Exception:
                continue
    finally:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_account():
    try:
        selection = find("Local RSS/RDF/ATOM/JSON", {"list item"}, seconds=8, enabled=False)
    except TimeoutError:
        click(find("Accounts", MENU_ITEMS, seconds=25))
        click(find("Add account", MENU_ITEMS))
        selection = find("Local RSS/RDF/ATOM/JSON", {"list item"}, enabled=False)
    click(selection, count=2)
    title_label = find("Title", {"label"})
    setup = title_label
    while setup and setup.getRoleName() not in {"dialog", "frame"}:
        setup = setup.parent
    if not setup:
        raise RuntimeError("Could not locate Local RSS account setup dialog")
    type_into(edit_for_label(setup, "Title"), ACCOUNT)
    click(find("OK", BUTTONS, root=setup))
    click(find("Load from OPML file", BUTTONS))


def disable_online_metadata(dialog):
    label = find("Do not fetch titles", {"check box", "toggle button"}, root=dialog, enabled=False)
    if not label.getState().contains(pyatspi.STATE_ENABLED):
        return
    group = find("Fetch online metadata", root=dialog, enabled=False)
    box = bounds(group)
    xdo("mousemove", box.x + 12, box.y + 8)
    xdo("click", 1)
    wait_for(
        "Fetch online metadata is still enabled",
        lambda: not label.getState().contains(pyatspi.STATE_ENABLED),
        seconds=5,
    )


def run_import(input_opml, directory):
    stage = find("Import feeds", {"dialog", "frame"})
    disable_online_metadata(stage)
    click(find("Select file", BUTTONS, root=stage))
    choose_file("Select file for feeds import", input_opml, "Open")
    click(find("Import from file", BUTTONS, root=stage))
    result = find(
        ["Import was completely successful.",
         "Some feeds/folders were not imported due to error, check debug log for more details."],
        root=stage,
    )
    capture(directory, "import")
    result_text = result.name
    click(find("Close", BUTTONS, root=stage))
    return result_text


def run_export(output_opml, directory):
    click(find("Accounts", MENU_ITEMS))
    account = find(ACCOUNT, {"menu", "menu item"})
    move(account)
    try:
        export_item = find("Export feeds", MENU_ITEMS, seconds=3)
    except TimeoutError:
        click(account)
        export_item = find("Export feeds", MENU_ITEMS)
    click(export_item)
    stage = find("Export feeds", {"dialog", "frame"})
    click(find("Select file", BUTTONS, root=stage))
    choose_file("Select file for feeds export", output_opml, "Save")
    click(find("Export to file", BUTTONS, root=stage))
    find("Feeds were exported successfully.", root=stage)
    wait_for("No OPML written by RSS Guard", lambda: output_opml.is_file())
    capture(directory, "export")


def get_feeds(file):
    tree = ET.parse(file)
    if tree.getroot().tag != "opml" or tree.find("body") is None:
        raise ValueError(f"Not a valid OPML file: {file.name}")
    return [item.get("xmlUrl") for item in tree.findall(".//outline") if item.get("xmlUrl")]


def run_guard(app, input_opml, output_opml, directory):
    input_feeds = set(get_feeds(input_opml))
    if not input_feeds:
        raise ValueError("Input OPML has no feed URLs")
    if output_opml.exists():
        raise FileExistsError(output_opml)
    directory.mkdir(parents=True, exist_ok=True)
    output_opml.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "QT_ACCESSIBILITY": "1", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
           "QT_QPA_PLATFORM": "xcb", "QT_STYLE_OVERRIDE": "Fusion", "QT_QPA_PLATFORMTHEME": "",
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    with tempfile.TemporaryDirectory(prefix="rssguard-brew-") as run_dir:
        data_dir = Path(run_dir) / "rssguard-data"
        (data_dir / "config").mkdir(parents=True)
        env.pop("NO_AT_BRIDGE", None)
        with (directory / "rssguard.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(app), "--data", str(data_dir)],
                env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            operation = "create_local_account"
            try:
                create_account()
                operation = "import_opml"
                import_status = run_import(input_opml, directory)
                operation = "export_opml"
                run_export(output_opml, directory)
                operation = "check_export"
                output_feeds = get_feeds(output_opml)
                if not output_feeds or not set(output_feeds) <= input_feeds:
                    raise ValueError("Actual export is empty or contains feeds outside the fixture")
                report = {
                    "input": input_opml.name, "output": output_opml.name,
                    "fetch_online_metadata": False, "import_status": import_status,
                    "export_entries": len(output_feeds), "unique_exported": len(set(output_feeds)),
                    "missing_input_addresses": sorted(input_feeds - set(output_feeds)),
                }
                (directory / "result.json").write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8",
                )
                print(json.dumps(report), flush=True)
            except Exception as exc:
                (directory / "failure.txt").write_text(
                    f"{operation}: {type(exc).__name__}: {exc}\n", encoding="utf-8",
                )
                try:
                    capture(directory, "failure")
                    record_accessibility_tree(directory / "accessibility.txt")
                except Exception as diagnostic:
                    print(f"Could not capture all diagnostics: {diagnostic}", flush=True)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("app", "input", "output", "screenshots"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    run_guard(args.app.resolve(), args.input.resolve(), args.output.resolve(), args.screenshots.resolve())


if __name__ == "__main__":
    main()
