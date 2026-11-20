#!/bin/bash
# builds "Subtitles.app" next to the repo so you can start the editor from Finder or the dock.
# the app starts the server if it isnt already running, then opens the browser.
#   ./scripts/make-app.sh            -> ./Subtitles.app
#   ./scripts/make-app.sh ~/Applications
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
OUT=${1:-$REPO}
APP="$OUT/Subtitles.app"
PORT=${SUBS_PORT:-8765}

command -v osacompile >/dev/null || { echo "osacompile not found, this only works on macOS"; exit 1; }
[ -x "$REPO/.venv/bin/python" ] || { echo "run ./scripts/setup.sh first"; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/main.applescript" <<EOF
-- Subtitles: starts the local caption editor (if it isnt already) and opens it
on isUp()
  try
    do shell script "curl -s -o /dev/null -m 1 http://localhost:$PORT/mtime"
    return true
  on error
    return false
  end try
end isUp

if not isUp() then
  do shell script "cd " & quoted form of "$REPO" & "; nohup .venv/bin/python -m subtitles $PORT > editor.log 2>&1 < /dev/null &"
  repeat 40 times
    delay 0.25
    if isUp() then exit repeat
  end repeat
end if

if isUp() then
  open location "http://localhost:$PORT"
else
  display dialog "The editor didnt start. Check editor.log in " & "$REPO" buttons {"OK"} default button 1 with icon stop
end if
EOF

rm -rf "$APP"
osacompile -o "$APP" "$TMP/main.applescript"
# swap the generic script icon for ours if rsvg-convert (brew install librsvg) + iconutil are around
if command -v rsvg-convert >/dev/null && command -v iconutil >/dev/null; then
  SET="$TMP/icon.iconset"
  mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    rsvg-convert -w $s -h $s web/favicon.svg -o "$SET/icon_${s}x${s}.png"
    rsvg-convert -w $((s * 2)) -h $((s * 2)) web/favicon.svg -o "$SET/icon_${s}x${s}@2x.png"
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/applet.icns"
  touch "$APP"
fi
echo "built $APP"
