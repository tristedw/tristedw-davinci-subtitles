#!/bin/bash
# takes Subtitles back out of DaVinci Resolve. your projects and this folder stay put.
set -euo pipefail
MENU="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
PANEL="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.tristedw.subtitles"

for f in "Subtitles" "Subtitles - Caption Timeline" "Subtitles - Place Captions"; do
  if [ -f "$MENU/$f.py" ]; then
    rm "$MENU/$f.py"
    echo "removed Workspace > Scripts > $f"
  fi
done
if [ -d "$PANEL" ]; then
  echo "removing the Workflow Integrations panel (needs your password)"
  if [ -t 0 ]; then
    sudo rm -rf "$PANEL"
  else
    osascript -e "do shell script \"rm -rf '$PANEL'\" with administrator privileges"
  fi
fi
echo "done. restart Resolve to drop the menu entries."
