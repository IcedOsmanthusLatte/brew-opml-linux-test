"""Confirm a real AVD, exact apps, real isolation, and native startup UI."""
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
from xml.etree import ElementTree as ET

OUT = Path("android-offline-evidence")
APPS = ("com.nononsenseapps.feeder", "com.prof18.feedflow")


def adb(*args, check=True, timeout=20):
    a = subprocess.run(["adb", *args], text=True, capture_output=True, timeout=timeout, check=check)
    return a.stdout.strip().replace("\r", "")


def shell(*args, check=True, timeout=20):
    return adb("shell", *args, check=check, timeout=timeout)


def wait(check, timeout=26):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        value = check()
        if value is not None and value is not False:
            return value
        time.sleep(0.6)
    raise TimeoutError("Android condition not reached")


def state(offline):
    shell("cmd", "connectivity", "airplane-mode", "enable" if offline else "disable")
    shell("svc", "wifi", "disable" if offline else "enable")
    shell("svc", "data", "disable" if offline else "enable")
    return wait(lambda: shell("settings", "get", "global", "airplane_mode_on") == ("1" if offline else "0"))


def probe(offline, change_state=True):
    if change_state:
        state(offline)
    time.sleep(3)
    nonce = secrets.token_hex(8)
    shell("am", "force-stop", "lab.brew.netprobe")
    shell("am", "start", "-n", "lab.brew.netprobe/.Probe", "--es", "nonce", nonce)

    def read():
        raw = shell("run-as", "lab.brew.netprobe", "cat", "files/proof.json", check=False)
        if not raw.startswith("{"):
            return
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return
        if result.get("nonce") == nonce:
            return result

    result = wait(read, timeout=30)
    result["airplane_mode"] = shell("settings", "get", "global", "airplane_mode_on")
    result["wifi_setting"] = shell("settings", "get", "global", "wifi_on")
    result["mobile_data_setting"] = shell("settings", "get", "global", "mobile_data")
    if offline:
        assert result["http_status"] == 0 and not result["any_active_network"]
        assert result["airplane_mode"] == "1" and result["wifi_setting"] == "0"
        assert result["mobile_data_setting"] == "0"
        assert not result["wifi"] and not result["cellular"] and not result["validated"]
    else:
        assert result["http_status"] == 200 and result["any_active_network"]
    return result


def xml():
    shell("uiautomator", "dump", "/sdcard/brew-ui.xml", timeout=18)
    return ET.fromstring(shell("cat", "/sdcard/brew-ui.xml"))


def stages():
    host = {"api": shell("getprop", "ro.build.version.sdk"),
            "abi": shell("getprop", "ro.product.cpu.abi"),
            "android": shell("getprop", "ro.build.version.release")}
    assert host["api"] == "35" and host["abi"] == "x86_64"
    host["installed_apps"] = {}
    pinned = {"com.nononsenseapps.feeder": ("4191", "2.25.0"), "com.prof18.feedflow": ("6239", "1.21.0")}
    for package, (code, expected_version) in pinned.items():
        dump = shell("dumpsys", "package", package)
        build = re.search(r"versionCode=(\d+)", dump)
        version = re.search(r"versionName=(\S+)", dump)
        assert (build[1], version[1]) == (code, expected_version)
        host["installed_apps"][package] = {"version_code": code, "version": expected_version}
    host["fixture_sha256_in_guest"] = shell("sha256sum", "/sdcard/Download/brew-control.opml").split()[0]
    assert host["fixture_sha256_in_guest"] == "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
    control = {"avd": host, "online": probe(False), "offline": probe(True), "restored": probe(False)}
    (OUT / "host.json").write_text(json.dumps(control, indent=2) + "\n")
    for package in APPS:
        shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(4)
        tree = xml()
        rows = [{k: n.attrib.get(k, "") for k in ("package", "text", "content-desc", "resource-id", "bounds")}
                for n in tree.iter("node") if n.attrib.get("text") or n.attrib.get("content-desc") or n.attrib.get("resource-id")]
        assert any(x["package"]==package for x in rows)
        (OUT / f"{package}-startup-ui.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        shell("am", "force-stop", package)


if __name__ == "__main__":
    stages()
