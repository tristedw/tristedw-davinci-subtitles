# config.py
# paths and settings shared by the server, pipeline and renderer.
# everything can be overridden with env vars so the app code and your caption projects
# dont have to live in the same folder.
#
#   SUBS_PROJECTS   where projects live (default: ./projects, ignored by git)
#   SUBS_PORT       port for the local editor (default: 8765)
#   SUBS_SYNC       git sync for the projects folder: auto (default), on, off
#                   auto = only when the projects folder is its own git repo with a remote
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
FONTS = ROOT / "fonts"
PRESETS = ROOT / "presets"
MODELS = ROOT / ".models"

PROJECTS = Path(os.environ.get("SUBS_PROJECTS") or ROOT / "projects").expanduser().resolve()
TRASH = PROJECTS / ".trash"
PORT = int(os.environ.get("SUBS_PORT") or 8765)
SYNC = (os.environ.get("SUBS_SYNC") or "auto").lower()
