# configure.py
# turns a copy of Blackmagic's own SamplePlugin (shipped with Resolve in Developer/Workflow Integrations)
# into the Subtitles panel. starting from the sample means the manifest format and the native
# WorkflowIntegration.node always match the Resolve version that's installed.
#   python3 configure.py <copied plugin dir> <repo> <port>
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PLUGIN_ID = "com.tristedw.subtitles"
NAME = "Subtitles"
DESCRIPTION = "Per speaker captions: transcribe, edit and render caption overlays for this timeline"
VERSION = "1.0.0"
# sample UI bits we dont use. the window loads the local editor instead
SAMPLE_UI = ["index.html", "js", "css", "preload.js", "renderer.js"]


def tag(el):
    return el.tag.rsplit("}", 1)[-1].lower()


def main(plugin, repo, port):
    plugin = Path(plugin)
    manifest = plugin / "manifest.xml"
    if not manifest.exists():
        sys.exit(f"no manifest.xml in {plugin}")

    tree = ET.parse(manifest)
    old_id = None
    wanted = {"id": PLUGIN_ID, "name": NAME, "description": DESCRIPTION, "version": VERSION}
    for el in tree.iter():
        t = tag(el)
        # first of each only, so nested things like an author name stay as they are
        if t in wanted and (el.text or "").strip():
            if t == "id":
                old_id = el.text.strip()
            el.text = wanted.pop(t)
    if old_id is None:
        # no <Id> tag in this build, fall back to the reverse dns id anywhere in the file
        m = re.search(r"com\.blackmagicdesign\.[\w.]+", manifest.read_text())
        old_id = m.group(0) if m else None
    tree.write(manifest, encoding="UTF-8", xml_declaration=True)
    if old_id:
        txt = manifest.read_text()
        manifest.write_text(txt.replace(old_id, PLUGIN_ID))

    entry = "main.js"
    pkg = plugin / "package.json"
    if pkg.exists():
        data = json.loads(pkg.read_text())
        entry = data.get("main") or entry
        data["name"] = "subtitles"
        data["productName"] = NAME
        data["description"] = DESCRIPTION
        data["version"] = VERSION
        pkg.write_text(json.dumps(data, indent=2) + "\n")

    for name in SAMPLE_UI:
        p = plugin / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists() and name != entry:
            p.unlink()

    src = (Path(__file__).parent / "main.js").read_text()
    js = src.replace('"__REPO__"', json.dumps(str(Path(repo).resolve()))).replace("__PORT__", str(int(port)))
    (plugin / entry).write_text(js)
    print(f"panel id {PLUGIN_ID} (was {old_id}), entry {entry}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__ or "usage: configure.py <plugin dir> <repo> <port>")
    main(*sys.argv[1:])
