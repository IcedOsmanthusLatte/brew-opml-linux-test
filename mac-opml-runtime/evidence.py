"""Read actual native export and preserve folder paths by unique feed URL."""
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

proof = Path("proof")
observed = Path("/tmp/brew-opml-runtime/export")
outputs = [p for p in observed.iterdir() if p.is_file() and p.name.lower().endswith((".opml", ".xml"))]
assert len(outputs) == 1, [p.name for p in outputs]
shutil.copyfile(outputs[0], proof / "native-export.opml")

def feed_paths(p):
    result = {}
    def visit(node, path):
        for item in node:
            if item.tag.rsplit('}', 1)[-1] != 'outline':
                continue
            url = item.get("xmlUrl") or item.get("xmlurl") or item.get("xmlURL")
            if url:
                result.setdefault(url, []).append(path)
            else:
                name = item.get("text") or item.get("title")
                visit(item, path + [name] if name else path)
    visit(ET.parse(p).find("body"), [])
    return result

before = feed_paths(proof / "fixture.opml")
assert len(before) == 3
after = feed_paths(proof / "native-export.opml")
assert not set(before) - set(after), {"missing": sorted(set(before) - set(after))}
read = {key: after[key] for key in before}
result = {"expected_paths": before, "actual_paths": read, "native_extras": sorted(set(after) - set(before)),
          "all_folders_preserved": read == before}
(proof / "comparison.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
