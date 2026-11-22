"""Download exact F-Droid builds using the signed release index."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.request import Request, urlopen
from zipfile import ZipFile

PKG = {"com.nononsenseapps.feeder": 4191, "com.prof18.feedflow": 6239}
REPO = "https://f-droid.org/repo/"
FINGERPRINT = "43238d512c1e5eb2d6569f4a3afbf5523418b82e0a3ed1552770abb9a9c9ccab"
APPS = Path(os.environ["RUNNER_TEMP"]) / "brew-fdroid"
OUT = Path("android-offline-evidence")


def run(command):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=True)
    return result.stdout


def download(name, destination, limit):
    with urlopen(Request(REPO + name, headers={"User-Agent": "Brew public Android comparison"}), timeout=40) as response:
        data = response.read(limit + 1)
    assert len(data) <= limit
    destination.write_bytes(data)


def main():
    APPS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    fixture = Path(__file__).with_name("control.opml")
    fixture_hash = sha256(fixture.read_bytes()).hexdigest()
    assert fixture_hash == "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
    jar = APPS / "entry.jar"
    download("entry.jar", jar, 1_000_000)
    signer = run(["keytool", "-printcert", "-jarfile", str(jar)])
    match = re.findall(r"SHA256:\s*([0-9A-F:]+)", signer)
    assert len(match) == 1 and match[0].replace(":", "").lower() == FINGERPRINT, "Unexpected F-Droid repository signer"
    verified = run(["jarsigner", "-verify", "-verbose", str(jar)])
    assert "jar verified." in verified.lower(), verified[-1500:]
    assert re.search(r"^sm\s+.*\sentry\.json$", verified, re.MULTILINE), "The signed index entry must be verified"
    with ZipFile(jar) as z:
        entry_point = json.loads(z.read("entry.json"))["index"]
    assert entry_point["name"] == "/index-v2.json"
    index_path = APPS / "index-v2.json"
    download("index-v2.json", index_path, 125_000_000)
    index_bytes = index_path.read_bytes()
    assert len(index_bytes) == entry_point["size"] and sha256(index_bytes).hexdigest() == entry_point["sha256"]
    index = json.loads(index_bytes)
    proof = {"fixture_sha256": fixture_hash, "fdroid_repo_cert_sha256": FINGERPRINT,
             "fdroid_signed_index_sha256": entry_point["sha256"], "packages": {}}
    for package, code in PKG.items():
        filename = f"{package}_{code}.apk"
        matches = [x for x in index["packages"][package]["versions"].values()
                   if int(x["manifest"]["versionCode"]) == code and x["file"]["name"] == "/" + filename]
        assert len(matches) == 1, f"Missing signed index match for {package}"
        entry = matches[0]
        assert "x86_64" in entry["manifest"]["nativecode"]
        apk = APPS / filename
        download(filename, apk, 110_000_000)
        actual = sha256(apk.read_bytes()).hexdigest()
        assert actual == entry["file"]["sha256"], f"APK bytes differ from signed index for {package}"
        apksigner = sorted(Path(os.environ["ANDROID_HOME"], "build-tools").glob("*/apksigner"), reverse=True)[0]
        verification = run([str(apksigner), "verify", "--print-certs", str(apk)])
        cert = re.findall(r"certificate SHA-256 digest: ([0-9a-f]+)", verification)
        assert len(cert) == 1 and cert == entry["manifest"]["signer"]["sha256"]
        proof["packages"][package] = {"version": entry["manifest"]["versionName"], "version_code": code,
                                      "apk_sha256": actual, "apk_signer_sha256": cert[0]}
    (OUT / "apk-builds.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
