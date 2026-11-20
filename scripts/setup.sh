#!/bin/bash
# one time setup on an apple silicon mac
#   ./scripts/setup.sh
# makes .venv, installs the python deps, grabs the two speaker models (~100MB) into .models/sherpa
# whisper weights download on their own the first time you transcribe.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "heads up: transcription uses mlx-whisper, which needs an apple silicon mac."
  echo "the editor and renderer still work elsewhere, the pipeline wont."
fi

if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then
    echo "installing ffmpeg"
    brew install ffmpeg
  else
    echo "need ffmpeg: install homebrew (https://brew.sh) then run: brew install ffmpeg"
    exit 1
  fi
fi

# want 3.11+. homebrew's comes first since the macOS one is too old
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for c in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11 python3; do
    if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then PY=$c; break; fi
  done
fi
[ -n "$PY" ] || { echo "need python 3.11 or newer: brew install python@3.12"; exit 1; }
if [ ! -x .venv/bin/python ]; then
  echo "making .venv"
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

M=.models/sherpa
mkdir -p "$M"
REL=https://github.com/k2-fsa/sherpa-onnx/releases/download
if [ ! -f "$M/sherpa-onnx-pyannote-segmentation-3-0/model.onnx" ]; then
  echo "downloading speaker segmentation model"
  curl -fL# "$REL/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" | tar xj -C "$M"
fi
if [ ! -f "$M/nemo_en_titanet_large.onnx" ]; then
  echo "downloading speaker embedding model"
  # the typo in the release tag is upstream's
  curl -fL# -o "$M/nemo_en_titanet_large.onnx" "$REL/speaker-recongition-models/nemo_en_titanet_large.onnx"
fi

echo
echo "done. start it with:  .venv/bin/python -m subtitles"
echo "want a sample project first?  .venv/bin/python -m subtitles.demo"
echo "want a dock app?  ./scripts/make-app.sh"
