"""Create independent nested and one-level workaround controls from original bytes."""
import copy
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

scratch, proof = map(Path, sys.argv[1:3])
app, mode, trial, bundle, bundle_id, version, build, release_url, digest = sys.argv[3:]
normal = Path(__file__).with_name("normal.opml").read_bytes()
normal_sha = "822295536b4776a25750d7dc4b707afff1fe9086d33382e6148b984195ed1afd"
assert hashlib.sha256(normal).hexdigest() == normal_sha
root = ET.fromstring(normal)
source = [node for node in root.iter("outline") if node.get("xmlUrl")]
assert len(source) == 3 and len({node.get("xmlUrl") for node in source}) == 3

chosen = normal
if mode in {"nested", "flattened", "unique"}:
    body = root.find("body")
    body.clear()
    if mode in {"nested", "unique"}:
        parents = []
        for outer, suffix in (("Brew Alpha", " A"), ("Brew Beta", " B")):
            group = ET.SubElement(body, "outline", {"text": outer})
            label = "Brew Shared" + (suffix if mode == "unique" else "")
            parents.append(ET.SubElement(group, "outline", {"text": label}))
    else:
        parents = [
            ET.SubElement(body, "outline", {"text": f"{outer} - Brew Shared"})
            for outer in ("Brew Alpha", "Brew Beta")
        ]
    parents.append(ET.SubElement(body, "outline", {"text": "Brew Shallow"}))
    for parent, leaf in zip(parents, source, strict=True):
        parent.append(copy.deepcopy(leaf))
    ET.indent(root, space="  ")
    chosen = ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"

sample = scratch / "fixture.opml"
sample.write_bytes(chosen)
(proof / "fixture.opml").write_bytes(chosen)
data = {
    "app": app, "mode": mode, "trial": int(trial), "bundle_path": bundle,
    "bundle_id": bundle_id, "version": version, "build": build,
    "release_url": release_url, "release_sha256": digest,
    "original_sha256": normal_sha, "fixture_sha256": hashlib.sha256(chosen).hexdigest(),
    "fixture_path": str(sample), "proof_path": str(proof),
    "export_dir": str(scratch / "export"),
    "titles": [node.attrib["title"] for node in source],
}
selection = os.environ.get("BREW_VIENNA_SELECTION")
if selection:
    assert app == "vienna" and mode == "nested" and selection in {"selected", "unselected"}
    assert data["fixture_sha256"] == "d4fcb29b25f290042b9cd38441878ead9e4d9f0b289437b7861bfe0c68a810d7"
    data["selection_mode"] = selection
    (scratch / "target-only.opml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<opml version="2.0"><head><title>Setup only</title></head>'
        '<body><outline text="Brew Import Target"/></body></opml>\n',
        encoding="utf-8",
    )
(scratch / "config.json").write_text(json.dumps(data), encoding="utf-8")
internal = {"bundle_path", "fixture_path", "proof_path", "export_dir"}
(proof / "environment.json").write_text(json.dumps({k:v for k,v in data.items() if k not in internal}, indent=2) + "\n", encoding="utf-8")
