#!/bin/bash
# adds Subtitles to DaVinci Resolve. run ./install.sh first (or ./scripts/setup.sh), then this.
#   ./scripts/install-resolve.sh              scripts menu + the Studio panel (asks for your password once)
#   ./scripts/install-resolve.sh --no-panel   scripts menu only, no admin needed
#
# Workspace > Scripts                Subtitles, Subtitles - Caption Timeline, Subtitles - Place Captions
#                                    (Studio, and free up to 21.0. Lives in your user Library)
# Workspace > Workflow Integrations  Subtitles, the editor in its own window (Studio only.
#                                    Lives in /Library, which is why it needs your password)
#
# rerun it if you move this folder or change SUBS_PORT. ./scripts/uninstall-resolve.sh removes it all.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
PORT=${SUBS_PORT:-8765}
PANEL=1
[ "${1:-}" = "--no-panel" ] && PANEL=0

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "run ./install.sh first, there's no .venv yet"; exit 1; }
[ "$(uname -s)" = "Darwin" ] || { echo "the Resolve install is mac only for now"; exit 1; }

USER_RES="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve"
SYS_RES="/Library/Application Support/Blackmagic Design/DaVinci Resolve"
if [ ! -d "/Applications/DaVinci Resolve" ] && [ ! -d "$USER_RES" ]; then
  echo "cant find DaVinci Resolve. install it (and open it once) first"
  exit 1
fi

# ---------- Workspace > Scripts ----------
MENU="$USER_RES/Fusion/Scripts/Utility"
mkdir -p "$MENU"
stub() {
  # $1 menu title, $2 entry in subtitles/resolve_menu.py
  cat > "$MENU/$1.py" <<EOF
# Subtitles for DaVinci Resolve, from $REPO
# installed by scripts/install-resolve.sh. rerun that if you move the folder.
import sys
sys.path.insert(0, $(printf '%s' "$REPO" | "$PY" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'))
from subtitles import resolve_menu
resolve_menu.run("$2", globals())
EOF
  echo "  Workspace > Scripts > $1"
}
echo "scripts menu:"
stub "Subtitles" open
stub "Subtitles - Caption Timeline" caption
stub "Subtitles - Place Captions" place

# ---------- Workspace > Workflow Integrations (Studio) ----------
if [ $PANEL = 1 ]; then
  echo "workflow integration panel:"
  SAMPLE=$(find "$SYS_RES/Developer" -type f -path "*/Examples/SamplePlugin/manifest.xml" 2>/dev/null | head -1 || true)
  if [ -z "$SAMPLE" ]; then
    echo "  skipped: cant find Resolve's SamplePlugin under $SYS_RES/Developer"
    echo "  (it ships with Resolve Studio. the Scripts menu entries above still work)"
  else
    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT
    cp -R "$(dirname "$SAMPLE")" "$TMP/com.tristedw.subtitles"
    "$PY" resolve/workflow-integration/configure.py "$TMP/com.tristedw.subtitles" "$REPO" "$PORT" | sed 's/^/  /'
    DEST="$SYS_RES/Workflow Integration Plugins"
    echo "  copying into $DEST (needs your password)"
    sudo mkdir -p "$DEST"
    sudo rm -rf "$DEST/com.tristedw.subtitles"
    sudo cp -R "$TMP/com.tristedw.subtitles" "$DEST/"
    echo "  Workspace > Workflow Integrations > Subtitles"
  fi
fi

echo
echo "restart Resolve so it picks these up."
echo "for New project > From DaVinci Resolve and Render to Resolve from the editor, set"
echo "Preferences > System > General > External scripting using > Local (Studio)."
echo "on the free version use the Scripts menu entries instead, they do the same thing from inside Resolve."
