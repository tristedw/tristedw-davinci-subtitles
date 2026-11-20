// Subtitles panel for DaVinci Resolve Studio (Workspace > Workflow Integrations > Subtitles).
// Resolve launches this as an Electron app. It starts the local caption server if it isnt running
// and shows the editor in its own window. All the real work happens in the python app, this is
// just a window into it, so it stays the same across Resolve versions.
//
// scripts/install-resolve.sh fills in REPO and PORT when it installs the plugin.
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");

const REPO = "__REPO__";
const PORT = __PORT__;
const URL = `http://localhost:${PORT}/`;

function isUp() {
  return new Promise(done => {
    const req = http.get(URL + "mtime", { timeout: 800 }, res => { res.resume(); done(res.statusCode === 200); });
    req.on("error", () => done(false));
    req.on("timeout", () => { req.destroy(); done(false); });
  });
}

async function ensureServer() {
  if (await isUp()) return true;
  const py = path.join(REPO, ".venv", "bin", "python");
  if (!fs.existsSync(py)) return false;
  const log = fs.openSync(path.join(REPO, "editor.log"), "a");
  const env = { ...process.env };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  delete env.ELECTRON_RUN_AS_NODE;
  spawn(py, ["-m", "subtitles", String(PORT)], { cwd: REPO, env, detached: true, stdio: ["ignore", log, log] }).unref();
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 250));
    if (await isUp()) return true;
  }
  return false;
}

function failPage(msg) {
  const body = `<body style="margin:0;height:100vh;display:grid;place-items:center;background:#1b1c1e;color:#ece9e3;font:14px -apple-system,sans-serif;text-align:center">
    <div><h2 style="margin:0 0 8px;font-size:18px">Subtitles didnt start</h2><p style="color:#9a968e;line-height:1.6;max-width:440px">${msg}</p></div></body>`;
  return "data:text/html;charset=utf-8," + encodeURIComponent(body);
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1440, height: 900, minWidth: 900, minHeight: 600,
    title: "Subtitles", backgroundColor: "#1b1c1e", show: false,
    webPreferences: { contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  win.once("ready-to-show", () => win.show());
  // links that leave the app open in the normal browser, not inside Resolve
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(URL)) shell.openExternal(url);
    return { action: "deny" };
  });
  if (await ensureServer()) {
    win.loadURL(URL);
  } else {
    win.loadURL(failPage(`Couldnt start the editor from ${REPO}. Run ./scripts/install.sh in that folder, then check editor.log there.`));
  }
}

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
