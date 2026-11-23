"""Copy the deliberately retained app evidence from Apple's result bundle export."""
import json
import re
import shutil
import sys
from pathlib import Path

src, dst = map(Path, sys.argv[1:3])
items = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
names = ("before-import-tree", "before-import-window", "imported-tree", "restarted-tree",
         "final-tree", "import-menu", "export-menu", "file-chooser", "save-chooser",
         "imported-window", "restarted-window", "default-starters-window", "clear-menu",
         "default-sidebar", "before-import-sidebar", "imported-sidebar", "restarted-sidebar")
pattern = re.compile(r"^brew-(" + "|".join(names) + r")(?:$|[_.-])")
for test in items:
    for attachment in test.get("attachments", []):
        match = pattern.match(attachment.get("suggestedHumanReadableName") or "")
        if not match:
            continue
        name = match[1]
        path = src / Path(attachment["exportedFileName"]).name
        suffix = ".png" if name.endswith("-window") else ".txt"
        shutil.copyfile(path, dst / (name + suffix))
