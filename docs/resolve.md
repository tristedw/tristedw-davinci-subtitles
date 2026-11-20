# Using Subtitles inside DaVinci Resolve

`./install.sh` sets all of this up. This page covers what it installs, what works on which edition
of Resolve, and what to check when something doesnt show up.

## What gets installed

| where in Resolve | what it does | Studio | Free |
| --- | --- | --- | --- |
| Workspace > Workflow Integrations > **Subtitles** | the editor in its own window inside Resolve | yes | no, Resolve doesnt load these on free |
| Workspace > Scripts > **Subtitles** | starts the app if needed and opens it in your browser | yes | up to 21.0 |
| Workspace > Scripts > **Subtitles - Caption Timeline** | renders the open timeline, then opens New project with it ready to go | yes | up to 21.0 |
| Workspace > Scripts > **Subtitles - Place Captions** | puts your newest caption render on the open timeline (or swaps it into the existing clip) | yes | up to 21.0 |

Blackmagic moved Python scripting to Studio in Resolve 21.1, so on free 21.1 and later the Scripts entries
dont show up. The standalone app still works there: render your timeline yourself, start a project
from the file, and drag the caption render onto the timeline.

The Scripts entries live in your user Library and dont need admin:

```
~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/
  Subtitles.py
  Subtitles - Caption Timeline.py
  Subtitles - Place Captions.py
```

Each one is a three line stub that imports `subtitles/resolve_menu.py` from wherever you cloned the repo,
so pulling updates doesnt need a reinstall. Move the folder and you need to rerun the installer.

The panel lives in the system Library, which is why the installer asks for your password once:

```
/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.tristedw.subtitles/
```

It's built from Blackmagic's own `SamplePlugin`, which Resolve Studio ships under
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer`. The installer copies it,
renames it, and swaps its window code for one that opens the editor. Building from the installed sample
means the manifest format and the native module always match your version of Resolve.

## Install

```
git clone https://github.com/tristedw/tristedw-davinci-subtitles
cd tristedw-davinci-subtitles
./install.sh
```

Then restart Resolve.

- `./install.sh --no-panel` skips the Studio panel and the password prompt.
- `./scripts/install-resolve.sh` reinstalls just the Resolve parts, for example after moving the folder.
- `./scripts/uninstall-resolve.sh` takes them all out again. Your projects stay put.

## Studio: let the app talk to Resolve

The **From DaVinci Resolve** option in New project, and **Render to Resolve** in the editor, talk to Resolve
from outside it. Turn that on once:

1. DaVinci Resolve > Preferences > System > General
2. **External scripting using**: Local
3. Restart Resolve

Without it, both still work through the Scripts menu: Caption Timeline instead of From DaVinci Resolve,
and Place Captions after you hit Render in the editor.

## Typical flow

1. Cut your edit, leave the timeline open.
2. Workspace > Scripts > Subtitles - Caption Timeline. Resolve renders a light copy of the edit and the
   New project window opens with it filled in.
3. Set how many people talk and their names, hit Start transcribing.
4. Fix lines, speakers and style in the editor.
5. Render to Resolve (Studio with external scripting on) or Render, then Workspace > Scripts > Subtitles - Place Captions.
   The overlay lands on a new top track, or replaces the earlier version if it's already in the media pool.

## When something doesnt show up

**No Subtitles under Workspace > Scripts.** Restart Resolve, it only scans on startup. Make sure the three
files are in the Utility folder above. Resolve runs menu scripts with a Python 3 it finds on your system;
if none of your scripts run, install Python from python.org. On free 21.1+, see the note at the top.

**No Subtitles under Workspace > Workflow Integrations.** You need Studio (DaVinci Resolve > About shows it).
Check the panel folder above exists, and that the installer didnt print "skipped" for the panel.
That happens when it cant find the SamplePlugin. Reinstall Resolve Studio to get the Developer folder back.

**The panel opens but says Subtitles didnt start.** Run `./install.sh` again, then look at `editor.log` in the repo.

**"cant reach Resolve" in the app.** External scripting is off or you're on free. Use the Scripts menu
entries, or see the Studio section above.

**Nothing happens when I click a Scripts entry.** Open Workspace > Console in Resolve. Every entry prints
what it's doing with a `[subtitles]` prefix, and errors show there too.
