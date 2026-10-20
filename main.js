// CMU Open Vehicles: draws a vehicle in orthographic views and previews its game sprites live.
// The main process owns the window, the Python backend (backend/server.py, JSON lines on stdio),
// settings, dialogs and files; the renderer talks to all of it through preload.js.

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const DEFAULT_GENERATOR = 'C:\\cmu\\jeeps\\Content.CMU\\Design\\Vehicles\\JeepPixelArt\\jeep_pixel_art.py';
const capture = (process.argv.find((a) => a.startsWith('--capture=')) || '').slice('--capture='.length);
// For testing with --capture: a script file run in the page once it's ready, before the screenshot.
const script = (process.argv.find((a) => a.startsWith('--script=')) || '').slice('--script='.length);
const settingsPath = () => path.join(app.getPath('userData'), 'settings.json');

let win = null;
let backend = null;

function readSettings() {
  try {
    return JSON.parse(fs.readFileSync(settingsPath(), 'utf8'));
  } catch {
    return {};
  }
}

function writeSettings(patch) {
  const next = { ...readSettings(), ...patch };
  fs.mkdirSync(path.dirname(settingsPath()), { recursive: true });
  fs.writeFileSync(settingsPath(), JSON.stringify(next, null, 2));
  return next;
}

class Backend {
  constructor() {
    const settings = readSettings();
    this.pending = new Map();
    this.nextId = 1;
    this.proc = spawn(settings.python || process.env.CMUOV_PYTHON || 'python',
      ['-u', path.join(__dirname, 'backend', 'server.py')],
      { cwd: path.join(__dirname, 'backend'), windowsHide: true, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    readline.createInterface({ input: this.proc.stdout }).on('line', (line) => this.receive(line));
    readline.createInterface({ input: this.proc.stderr }).on('line', (line) => send('backend:log', line));
    this.proc.on('exit', (code) => {
      for (const { reject } of this.pending.values()) reject(new Error('The Python backend stopped.'));
      this.pending.clear();
      send('backend:event', { event: 'exit', data: { code } });
    });
    this.proc.on('error', (err) => send('backend:event', { event: 'exit', data: { code: -1, error: String(err) } }));
  }

  receive(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      send('backend:log', line);
      return;
    }
    if (msg.event) {
      send('backend:event', msg);
      return;
    }
    const waiting = this.pending.get(msg.id);
    if (!waiting) return;
    this.pending.delete(msg.id);
    if (msg.error) waiting.reject(new Error(msg.error));
    else waiting.resolve(msg.result);
  }

  call(method, params) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.proc.stdin.write(JSON.stringify({ id, method, params: params || {} }) + '\n');
    });
  }

  stop() {
    try { this.proc.kill(); } catch { /* already gone */ }
  }
}

function send(channel, data) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, data);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1520,
    height: 940,
    minWidth: 1200,
    minHeight: 740,
    frame: false,
    backgroundColor: '#0b0c0e',
    title: 'CMU Open Vehicles',
    icon: path.join(__dirname, 'icon.ico'),
    show: false,
    ...(capture ? { x: -4000, y: 0 } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  if (capture || process.env.CMUOV_DEBUG) {
    win.webContents.on('console-message', (event) => {
      const { level, message, lineNumber, sourceId } = event;
      console.log(`[renderer:${level}] ${message} (${path.basename(sourceId || '')}:${lineNumber})`);
    });
  }
  win.once('ready-to-show', () => win.show());
  win.on('maximize', () => send('win:state', { maximized: true }));
  win.on('unmaximize', () => send('win:state', { maximized: false }));
}

ipcMain.handle('backend:call', (_e, method, params) => backend.call(method, params));
ipcMain.handle('settings:get', () => ({ generator: DEFAULT_GENERATOR, ...readSettings() }));
ipcMain.handle('settings:set', (_e, patch) => writeSettings(patch));
ipcMain.handle('dialog:open', (_e, opts) => dialog.showOpenDialog(win, opts));
ipcMain.handle('dialog:save', (_e, opts) => dialog.showSaveDialog(win, opts));
ipcMain.handle('fs:read', (_e, file) => fs.promises.readFile(file, 'utf8'));
ipcMain.handle('fs:write', (_e, file, text) => fs.promises.writeFile(file, text, 'utf8'));
ipcMain.handle('fs:readDataUrl', async (_e, file) => {
  const ext = path.extname(file).slice(1).toLowerCase();
  const type = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif', bmp: 'image/bmp',
    ogg: 'audio/ogg', wav: 'audio/wav', mp3: 'audio/mpeg' }[ext] || 'image/png';
  return `data:${type};base64,${(await fs.promises.readFile(file)).toString('base64')}`;
});
ipcMain.handle('fs:writeBase64', (_e, file, data) => fs.promises.writeFile(file, Buffer.from(data, 'base64')));
ipcMain.handle('shell:open', (_e, target) => shell.openPath(target));
// The open project, watched so a script drawing on it (cmuov.py) shows in the app. Polled, since a
// file saved by replacing it, as scripts do, ends a native watch.
let watched = null;
ipcMain.handle('fs:watch', (_e, file) => {
  if (watched === file) return;
  if (watched) fs.unwatchFile(watched);
  watched = file;
  if (!file) return;
  fs.watchFile(file, { interval: 400 }, (cur, prev) => {
    if (cur.mtimeMs !== prev.mtimeMs && cur.size > 0) send('file:changed', file);
  });
});
ipcMain.on('win:minimize', () => win.minimize());
ipcMain.on('win:maximize', () => (win.isMaximized() ? win.unmaximize() : win.maximize()));
ipcMain.on('win:close', () => win.close());
ipcMain.on('app:ready', async () => {
  if (!capture) return;
  // Capture mode, for checking the interface: wait for the preview to settle, save a screenshot, quit.
  let wait = 2500;
  if (script) {
    try {
      wait = (await win.webContents.executeJavaScript(fs.readFileSync(script, 'utf8'))) || wait;
    } catch (err) {
      console.log(`[script] ${err}`);
    }
  }
  setTimeout(async () => {
    const image = await win.webContents.capturePage();
    fs.writeFileSync(capture, image.toPNG());
    app.quit();
  }, wait);
});

app.whenReady().then(() => {
  backend = new Backend();
  createWindow();
});

app.on('window-all-closed', () => {
  if (backend) backend.stop();
  app.quit();
});
