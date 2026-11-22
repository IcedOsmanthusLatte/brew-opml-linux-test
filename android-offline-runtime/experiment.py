"""Operate the two installed readers only through their native public UI."""
import json
import os
import re
import shlex
import subprocess
import time

from bootstrap import APPS, OUT, probe, shell, wait, xml
from source_evidence import digest, load_items, normalized

FEEDER, FLOW = APPS
SOURCES = {"BBC": "BBC News — World", "NASA": "NASA News Releases", "WordPress": "WordPress News"}
FEEDER_ADV = "Fetch full articles by default"
FLOW_ADV = "Make offline copies when feeds refresh"
FLOW_MENU = ("drawer_menu_button", "Open navigation menu")
STEPS = []
WIDTH, HEIGHT = [int(x) for x in shell("wm", "size").splitlines()[-1].split()[-1].split("x")]


def note(name, **value):
    STEPS.append({"step": name, **value})
    (OUT / "native-navigation.json").write_text(json.dumps(STEPS, indent=2) + "\n")
    print(name, flush=True)


def fields(node):
    return tuple(node.get(x, "") for x in ("text", "content-desc", "resource-id"))


def by(*options):
    wanted = {x.casefold() for x in options}
    return lambda node: any(v.casefold() in wanted or v.split(":id/")[-1].casefold() in wanted
                            for v in fields(node) if v)


def find(tree, matcher):
    return next((x for x in tree.iter("node") if matcher(x) and x.get("enabled") != "false"), None)


def spot(matcher):
    node = find(xml(), matcher)
    return node if node is not None else False


def click(node):
    x1, y1, x2, y2 = [int(x) for x in re.findall(r"\d+", node.get("bounds"))]
    assert x2 > x1 and y2 > y1
    shell("input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))


def tap(*options, timeout=12):
    node = wait(lambda: spot(by(*options)), timeout=timeout)
    click(node)
    return node


def optional(*options):
    node = spot(by(*options))
    if node is not False:
        click(node)
        return True
    return False


def back():
    shell("input", "keyevent", "4")


def swipe(down):
    x = str(WIDTH // 2)
    upper, lower = str(HEIGHT * 27 // 100), str(HEIGHT * 74 // 100)
    shell("input", "swipe", x, upper if down else lower, x, lower if down else upper, "360")


def launch(package):
    shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")


def dismiss_system_dialog():
    optional("Don't allow", "Don’t allow", "permission_deny_button", "Dismiss")


def fixture_picker():
    if not optional("brew-control.opml"):
        if not optional("Downloads", "Download"):
            tap("Show roots", "roots")
            tap("Downloads", "Download")
        if not optional("brew-control.opml"):
            tap("Search", "option_menu_search")
            shell("input", "text", "brew-control.opml")
            tap("brew-control.opml", timeout=16)


def feedflow_home():
    for _ in range(5):
        if spot(by(*FLOW_MENU)) is not False:
            return
        back()
    raise AssertionError("FeedFlow timeline not observed")


def feedflow_settings():
    tap(*FLOW_MENU)
    tap("drawer_settings_button", "Settings")


def import_fixture(package):
    if package == FEEDER:
        tap("Open menu")
        tap("Import feeds from OPML")
        fixture_picker()
        dismiss_system_dialog()
        wait(lambda: spot(by("Open navigation drawer")), timeout=30)
    else:
        feedflow_settings()
        tap("settings_feeds_accounts", "Feeds & accounts")
        tap("settings_import_export", "Import and export")
        tap("import_export_import_feeds", "Import feed from OPML")
        fixture_picker()
        tap("import_export_done", "Done", timeout=30)
        feedflow_home()
    note("native-fixture-import", package=package)


def drawer(package):
    tap(*(("Open navigation drawer",) if package == FEEDER else FLOW_MENU))


def source_label(node, source):
    expected = normalized(SOURCES[source])
    titles = [normalized(v) for v in fields(node)[:2] if v]
    return any(x == expected or x.startswith(expected + " ") for x in titles)


def source_timeline(package, source):
    drawer(package)
    matcher = lambda node: source_label(node, source)
    category = "News" if source == "BBC" else "Science and technology"
    for turn in range(6):
        tree = xml()
        node = find(tree, matcher)
        if node is not None:
            click(node)
            return
        category_node = find(tree, by(category))
        if turn < 2 and category_node is not None:
            # The category controls have the category label and a separate expand target in both apps.
            bounds = [int(x) for x in re.findall(r"\d+", category_node.get("bounds"))]
            y = (bounds[1] + bounds[3]) // 2
            expand = find(tree, lambda n: n.get("content-desc", "").lower().startswith(("expand", "toggle"))
                          and (b := [int(x) for x in re.findall(r"\d+", n.get("bounds", ""))])
                          and b[1] <= y <= b[3])
            if expand is not None:
                click(expand)
            elif package == FLOW:
                shell("input", "tap", str(WIDTH * 78 // 100), str(y))
            else:
                click(category_node)
                # Feeder category timeline itself contains the one BBC source or both controls.
                return
        else:
            swipe(False)
    raise AssertionError("Imported source could not be selected: " + source)


def read_switch(package, label):
    tree = xml()
    row = find(tree, by(label))
    if row is None:
        return None
    ys = [int(x) for x in re.findall(r"\d+", row.get("bounds"))]
    y = (ys[1] + ys[3]) // 2
    for node in tree.iter("node"):
        if node.get("checkable") != "true":
            continue
        bounds = [int(x) for x in re.findall(r"\d+", node.get("bounds"))]
        if bounds[1] - 6 <= y <= bounds[3] + 6:
            return node.get("checked") == "true"
    # Feeder publishes On/Off as accessibility stateDescription; UIAutomator XML omits it.
    raw = shell("am", "instrument", "-w", "-r", "-e", "label", shlex.quote(label), "-e", "package", package,
                "lab.brew.netprobe/.UiProbe", timeout=22)
    match = re.search(r"^INSTRUMENTATION_RESULT: switch_state=(on|off)$", raw, re.MULTILINE)
    return match[1] == "on" if match else None


def scroll_to(label):
    for _ in range(6):
        if spot(by(label)) is not False:
            return
        swipe(False)
    raise AssertionError("Settings control not shown: " + label)


def setup_prefetch(package, enabled):
    if package == FEEDER:
        source_timeline(package, "BBC")
        tap("Open menu")
        tap("Edit feed")
        optional(SOURCES["BBC"])
        scroll_to(FEEDER_ADV)
        initial = read_switch(package, FEEDER_ADV)
        assert initial is False, "Feeder fresh feed had unexpected switch state"
        if enabled:
            tap(FEEDER_ADV)
        assert read_switch(package, FEEDER_ADV) == enabled
        if enabled:
            scroll_to("OK")
            tap("OK")
        else:
            back()
        label = FEEDER_ADV
    else:
        feedflow_settings()
        tap("settings_reading_behavior", "Reading behavior")
        # Pinned FeedFlow's fresh default is Reader (website); leave it unchanged.
        scroll_to(FLOW_ADV)
        initial = read_switch(package, FLOW_ADV)
        assert initial is False, "FeedFlow fresh profile had unexpected switch state"
        if enabled:
            tap(FLOW_ADV)
            tap("Confirm")
        assert read_switch(package, FLOW_ADV) == enabled
        feedflow_home()
        label = FLOW_ADV
    note("verified-offline-setting", package=package, setting=label, enabled=enabled,
         initial_enabled=initial, common_reader_mode="website" if package == FLOW else "feed default")


def refresh(package):
    if package == FEEDER:
        tap("Open menu")
        tap("Sync feeds")
    elif optional("Refresh feeds"):
        pass
    else:
        swipe(True)


def title_node(tree, items):
    for node in tree.iter("node"):
        label = normalized(node.get("text", "")) or normalized(node.get("content-desc", ""))
        for item in items:
            if item.title == label or (len(item.title) > 26 and item.title in label):
                return node, item
    return None


def visible_item(package, source, items):
    source_timeline(package, source)
    for _ in range(7):
        match = title_node(xml(), items)
        if match:
            return match
        swipe(False)
    raise AssertionError("Matched item not visible in ordinary feed timeline: " + source)


def reader_context(tree, package):
    in_native_app = any(n.get("package") == package for n in tree.iter("node"))
    if not in_native_app:
        return False
    if package == FLOW:
        return find(tree, by("reader_back_button", "reader_bookmark_button")) is not None or flow_content(tree)
    has_back = find(tree, by("Go back")) is not None
    has_reader_control = find(tree, by("Fetch full article", "Open in web view", "Open menu")) is not None
    return has_back and has_reader_control


def flow_content(tree):
    return any(n.get("resource-id", "").rsplit(":id/", 1)[-1].startswith("reader_article_") for n in tree.iter("node"))


def reader_activity(package):
    dump = shell("dumpsys", "activity", "activities")
    match = re.search(r"(?:topResumedActivity|mResumedActivity)[=:].*?\b(u\d+\s+)?(" +
                      re.escape(package) + r"/\S+)", dump)
    assert match, "Reader app is not the resumed activity"
    return match[2]


def view_text(tree, package):
    return normalized(" ".join(node.get("text", "") for node in tree.iter("node") if node.get("package") == package))


def ocr():
    png = subprocess.check_output(["adb", "exec-out", "screencap", "-p"], timeout=10)
    result = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11"], input=png,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True)
    return normalized(result.stdout.decode("utf-8", "replace"))


def measure(package, node, item):
    click(node)

    def opened_reader():
        tree = xml()
        if not reader_context(tree, package):
            return None
        if package == FLOW:
            unavailable = find(tree, by("Unable to parse article", "No content available")) is not None
            if not (flow_content(tree) or unavailable):
                return None
        else:
            fetching = find(tree, lambda n: n.get("text", "").startswith("Fetching full article"))
            if fetching is not None:
                return None
        return tree

    tree = wait(opened_reader, timeout=32)
    activity = reader_activity(package)
    recorded_keys = sorted(v for node in tree.iter("node") for v in fields(node)[1:]
                           if v in {"Go back", "Fetch full article", "Open menu", "reader_back_button",
                                    "reader_bookmark_button", "reader_content_source_button"})
    selected_id = node.get("resource-id", "")
    source_button = find(tree, by("reader_content_source_button"))
    feed_content_selected = source_button.get("checked") == "true" if source_button is not None else None
    visible = [view_text(tree, package), ocr()]
    for _ in range(4):
        result = item.classify(visible)
        if result["kind"] in {"origin-body-beyond-feed", "feed-provided-body"}:
            break
        swipe(False)
        tree = xml()
        assert reader_context(tree, package), "Native reader switched away during first open"
        visible.extend([view_text(tree, package), ocr()])
    result = item.classify(visible)
    assert result["title_observed"], "Measured title identity failed inside native reader"
    result["resumed_activity"] = activity
    result["reader_ui_controls"] = recorded_keys
    result["reader_feed_content_switch_selected"] = feed_content_selected
    result["timeline_item_resource_id_sha256"] = digest(selected_id) if selected_id else None
    back()
    return result


def measure_route(package, condition, round_num, candidates, selected):
    name = package.split(".")[-1] + "-" + condition + "-" + str(round_num)
    enabled = condition == "prefetch"
    before = probe(False)
    assert shell("pm", "clear", package).strip() == "Success"
    launch(package)
    dismiss_system_dialog()
    import_fixture(package)
    setup_prefetch(package, enabled)
    shell("logcat", "-c", check=False)
    refresh(package)
    refreshed_at = time.monotonic()
    # Let standard foreground/background refresh and indexing finish equally across profiles.
    time.sleep(53)
    phrases = ("Performing full text parse work", "Finished full text parse work", "Failed to parse missing full article")
    if package == FLOW:
        phrases = ("items for immediate prefetch", "items for background prefetch", "Prefetched successfully:", "Parse failed for:")
    log = shell("logcat", "-d", check=False, timeout=25)
    scheduler_events = {phrase: log.count(phrase) for phrase in phrases}
    note("prefetch-scheduler-observed", package=package, counts=scheduler_events)
    pair = {}
    for source in ("BBC", "WordPress" if candidates["WordPress"] else "NASA"):
        list_items = [selected[source]] if source in selected else candidates[source]
        _, item = visible_item(package, source, list_items)
        if source == "BBC":
            item.prove_origin()
        pair[source] = item
        selected[source] = item
    shell("am", "force-stop", package)
    until_cutoff = round(time.monotonic() - refreshed_at, 1)
    cutoff = probe(True)
    launch(package)
    results = {}
    for source, item in pair.items():
        node, _ = visible_item(package, source, [item])
        results[source] = measure(package, node, item)
        note("first-open-observed", package=package, condition=condition, source=source, round=round_num)
    after = probe(True, change_state=False)
    restored = probe(False)
    result = {"round": round_num, "package": package, "route": condition,
              "online_initial": before, "offline_before_first_open": cutoff,
              "offline_after_first_open": after, "reconnected": restored,
              "native_first_open_no_online_open": True, "online_elapsed_seconds_after_refresh": until_cutoff,
              "prefetch_scheduler_event_counts": scheduler_events, "items": results}
    (OUT / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n")


def main():
    candidates = load_items()
    selected = {}
    bbc_verified = []
    for item in candidates["BBC"][:5]:
        try:
            item.prove_origin()
        except (OSError, ValueError):
            continue
        bbc_verified.append(item)
    assert bbc_verified, "Cloud runner could not verify BBC original article body"
    candidates["BBC"] = bbc_verified
    note("public-source-reference-controls", candidates={source: len(items) for source, items in candidates.items()})
    package = os.environ["BREW_TEST_PACKAGE"]
    assert package in APPS
    for round_number in (1, 2):
        for condition in ("baseline", "prefetch"):
            note("begin-clean-profile", package=package, condition=condition, round=round_number)
            measure_route(package, condition, round_number, candidates, selected)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            safe_labels = {"OK", "Done", "Confirm", "Save", "Downloads", "Recent", "Show roots", "Settings",
                           "Open menu", "Open navigation drawer", "Open navigation menu", "Reading behavior",
                           "Feeds & accounts", "Import and export", "Import feeds from OPML", "Edit feed",
                           "Sync feeds", "Refresh feeds", FEEDER_ADV, FLOW_ADV, *SOURCES.values()}
            rows = []
            for n in xml().iter("node"):
                label, description, resource = fields(n)
                if not any((label, description, resource)):
                    continue
                if "article_row_" in resource or "reader_article_" in resource:
                    resource = "article-item-hash:" + digest(resource)
                rows.append({"package": n.get("package"), "bounds": n.get("bounds"),
                             "known_label": label if label in safe_labels else "",
                             "known_description": description if description in safe_labels else "",
                             "resource_id": resource, "class": n.get("class"),
                             "checkable": n.get("checkable"), "checked": n.get("checked")})
            (OUT / "ui-failure.json").write_text(json.dumps(rows, indent=2) + "\n")
        except Exception:
            pass
        raise
