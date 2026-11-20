# davinci-subtitles

Per speaker captions for short form video. Whisper transcript, speaker split, a local editor
to fix it all up, and a ProRes 4444 overlay that drops straight onto a DaVinci Resolve timeline.

I built this because the caption tools I tried either couldnt tell two people apart, or made
me fix every line in a tiny box inside Resolve. This does the boring 90% on its own and gives
you a proper editor for the rest.

![editor](docs/editor.png)

## What it does

- **Transcribes locally** with Whisper large v3 on Apple Silicon (mlx), word level timestamps,
  plus an optional second pass on words it wasnt sure about.
- **Splits by speaker** with pyannote segmentation + TitaNet embeddings (sherpa-onnx). Every
  sentence gets one speaker by vote, and it only splits mid sentence when diarization is sure.
  There's an extra pass for people far from the mic.
- **Builds caption lines** that respect a max length and on screen time, and flags the lines
  it wasnt confident about so you know where to look.
- **Editor** in the browser: line list, waveform timeline with drag to retime, drag captions on
  the video to place them, find / replace, undo, per speaker overrides, presets, and a pixel
  exact preview from the same renderer that makes the final file.
- **Renders** a 1080x1920 ProRes 4444 with alpha and swaps it into Resolve for you, or imports
  it into a Captions bin and puts it on a new top track.
- **Exports** SRT (combined, plain and per speaker), VTT, ASS and a plain transcript.
- **Lives in Resolve**: a Workflow Integrations panel on Studio, and Workspace > Scripts entries to
  caption the open timeline and drop the finished captions back on it.

## Install

Needs an Apple Silicon Mac (transcription runs on mlx-whisper) and DaVinci Resolve.

```
git clone https://github.com/tristedw/tristedw-davinci-subtitles
cd tristedw-davinci-subtitles
./install.sh
```

Then restart Resolve. The installer:

1. makes `.venv`, installs ffmpeg through Homebrew if it's missing, and downloads the two speaker
   models into `.models/sherpa`. Whisper weights download themselves the first time you transcribe.
2. adds Subtitles to Resolve's **Workspace > Scripts** menu, and on Studio a
   **Workspace > Workflow Integrations > Subtitles** panel (asks for your password once, use
   `--no-panel` to skip it).
3. builds `Subtitles.app` you can keep in the dock.

On Studio, also set Preferences > System > General > **External scripting using: Local** so the
app can pull timelines from Resolve and push renders back on its own.

[docs/resolve.md](docs/resolve.md) has the full Resolve setup, what works on Studio vs free, and
troubleshooting.

### In Resolve

| menu | |
| --- | --- |
| Workspace > Workflow Integrations > Subtitles | the editor in its own window (Studio) |
| Workspace > Scripts > Subtitles | open the app |
| Workspace > Scripts > Subtitles - Caption Timeline | render the open timeline and start a project on it |
| Workspace > Scripts > Subtitles - Place Captions | put the newest caption render on the timeline |

### Without Resolve

```
.venv/bin/python -m subtitles          # http://localhost:8765
.venv/bin/python -m subtitles.demo     # sample project, no whisper needed
```

The editor, renderer and exporters run anywhere with Python 3.11+ and ffmpeg. Only transcription
needs Apple Silicon.

![projects](docs/projects.png)

## Using it

**New project** (Cmd+N, or Workspace > Scripts > Subtitles - Caption Timeline in Resolve): pick a
video file, a clip from your open Resolve timeline, or the whole timeline (it renders a light copy of your edit first so captions match the cut). Say how many
people talk or leave it on Auto, give their names in the order they first talk, and add any
names or slang Whisper should spell right. It runs in the background and opens the editor when
it's done.

In the editor:

- **Lines**: text, timing and speaker per line. `Show: Flagged` lists the lines to double check.
- **Timeline**: drag edges to retime, drag blocks to move them or change speaker.
- **Style**: font, outline, shadow, box, name tag, position, animation, word highlight and timing
  rules. Save the ones you like as presets in `presets/`.
- **Speakers**: rename, recolor, per person font / color / size, and **Merge into** when auto
  detection split one voice into two.
- **Project settings**: relink a moved video, set the Resolve clip name, or rebuild the lines,
  the speakers or the whole transcript. Rebuilds keep the old version in `.history/`.

The Shortcuts tab has every key.

## Config

| env var | default | |
| --- | --- | --- |
| `SUBS_PROJECTS` | `./projects` | where projects live |
| `SUBS_PORT` | `8765` | editor port |
| `SUBS_SYNC` | `auto` | git sync for the projects folder: `auto`, `on`, `off` |

Projects are never committed to this repo. If you want them backed up or shared between
machines, make the projects folder its own **private** repo with a remote. With `SUBS_SYNC=auto`
the app pulls it on start and commits + pushes about 20s after each save.

```
projects/<name>/
  project.json         name, source video, clip name, transcribe settings
  captions_edit.json   lines, words, speakers, style
  work/                audio, whisper + diarization output, poster
  renders/ export/ .history/
```

## How it works

```
subtitles/
  server.py          http server + json api for both pages, job runner, git sync
  pipeline.py        video -> captions_edit.json, runs as a subprocess and reports progress
  render.py          pillow renderer, used for the exact preview and the final prores
  exporters.py       srt / vtt / ass / txt
  resolve_tasks.py   everything asked of Resolve, written against a live resolve object
  resolve_bridge.py  runs resolve_tasks from outside Resolve (external scripting, Studio)
  resolve_menu.py    runs resolve_tasks from Workspace > Scripts inside Resolve
  demo.py            sample project
resolve/
  workflow-integration/  the Studio panel: window code + the script that builds it from Resolve's sample
web/
  home.html          project list + new project flow
  editor.html        the editor
  app.css            shared tokens and controls
```

The pipeline steps, in order. Each one caches its output in `work/` so rebuilds only redo what
they need (`python -m subtitles.pipeline projects/<name> --from speakers`).

| step | what |
| --- | --- |
| resolve | timeline projects only: render a light h264 of the edit out of Resolve |
| probe, audio | ffprobe, 16k mono + a loudness normed copy |
| transcribe | whisper large v3 (turbo on Fast), word timestamps, spelling hints as the prompt |
| refine | Best only: redo segments with very low confidence words, keep whichever pass is surer |
| speakers | pyannote seg 3.0 + titanet, fixed cluster count if you gave one |
| quiet | optional: whisper on each speaker turn of the normed audio |
| lines | sentences get one speaker by vote, split only where diarization is sure, wrap to max chars |

All the Resolve logic lives in `resolve_tasks.py` and gets reached two ways. From outside, the
scripting module only loads in some Python builds, so `resolve_bridge.py` runs each task in
whichever system Python can import it, not the venv. From inside, the Scripts menu stubs hand
`resolve_menu.py` the live `resolve` object, which works without external scripting and on the free
version up to 21.0. The tests run both paths against a fake Resolve.

The server only answers localhost and rejects requests from other origins, so a random web
page cant poke at your files through it.

## Dev

```
pip install -r requirements-dev.txt
ruff check .
pytest
```

CI runs both on every push. Transcription isnt tested in CI since it needs Apple Silicon.

## License

MIT. Bundled fonts are from Google Fonts under the SIL Open Font License, see `fonts/README.md`.
