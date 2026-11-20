#!/bin/bash
# one command install on an apple silicon mac:
#   git clone https://github.com/tristedw/tristedw-davinci-subtitles && cd tristedw-davinci-subtitles && ./install.sh
#
# 1. python env, ffmpeg and the speaker models   (scripts/setup.sh)
# 2. the DaVinci Resolve menu entries + panel     (scripts/install-resolve.sh)
# 3. Subtitles.app for the dock, if you want it    (scripts/make-app.sh)
#
# pass --no-panel to skip the Studio panel (and the password prompt that comes with it).
set -euo pipefail
cd "$(dirname "$0")"

./scripts/setup.sh
echo
./scripts/install-resolve.sh "$@"
echo
./scripts/make-app.sh
echo
echo "all set. restart Resolve, then look under Workspace > Scripts and Workspace > Workflow Integrations."
echo "or open Subtitles.app, or run: .venv/bin/python -m subtitles"
