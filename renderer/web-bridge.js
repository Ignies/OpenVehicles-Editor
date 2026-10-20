// The app's door to the server, in the browser: the same window.cmu the desktop app had, so app.js
// needn't know it runs on the web. Backend calls go over a WebSocket to the tab's own Python backend
// (backend/web.py). Projects are kept in the browser (IndexedDB); a picture to trace or a .cmuov to
// open comes from a file picker; what the desktop app wrote to disk is downloaded instead.
//
// Paths it hands out stand for where a file lives:
//   store:<name>       a project saved in this browser
//   upload:<n>/<name>  a file just picked from the computer
//   download:<name>    a file to download
//   /audio/...         a sound served by the server

(() => {
  const listeners = new Map();
  const emit = (channel, data) => (listeners.get(channel) || []).forEach((cb) => cb(data));

  // -- the socket ---------------------------------------------------------------------------------

  let socket = null;
  let session = null;
  let nextId = 1;
  const pending = new Map();
  const queued = [];

  function connect() {
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    socket.addEventListener('open', () => queued.splice(0).forEach((m) => socket.send(m)));
    socket.addEventListener('message', (e) => {
      const msg = JSON.parse(e.data);
      if (msg.session) { session = msg.session; return; }
      if (msg.log != null) { emit('backend:log', msg.log); return; }
      if (msg.event) { emit('backend:event', msg); return; }
      const waiting = pending.get(msg.id);
      if (!waiting) return;
      pending.delete(msg.id);
      if (msg.error) waiting.reject(new Error(msg.error));
      else waiting.resolve(msg.result);
    });
    socket.addEventListener('close', () => {
      for (const { reject } of pending.values()) reject(new Error('Lost the connection to the server.'));
      pending.clear();
      emit('backend:event', { event: 'exit', data: { code: -1 } });
    });
  }

  async function call(method, params) {
    const id = nextId++;
    const text = JSON.stringify({ id, method, params: params || {} });
    const result = await new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      if (socket.readyState === WebSocket.OPEN) socket.send(text);
      else queued.push(text);
    });
    // A file the backend wrote for the page, in the session's folder on the server.
    if (result && result.download && session) {
      fetchDownload(`/download/${session}/${encodeURIComponent(result.download)}`, result.download);
    }
    return result;
  }

  function fetchDownload(url, name) {
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
  }

  function download(name, data, type = 'application/octet-stream') {
    const blob = data instanceof Blob ? data : new Blob([data], { type });
    const url = URL.createObjectURL(blob);
    fetchDownload(url, name);
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // -- projects kept in the browser -------------------------------------------------------------------

  const DB = 'cmu-open-vehicles';
  let dbPromise = null;

  function db() {
    dbPromise ||= new Promise((resolve, reject) => {
      const req = indexedDB.open(DB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore('projects', { keyPath: 'name' });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  async function store(mode, run) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const tx = d.transaction('projects', mode);
      const req = run(tx.objectStore('projects'));
      tx.oncomplete = () => resolve(req?.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error || new Error('The browser refused to save it (out of space?)'));
    });
  }

  const projects = {
    list: () => store('readonly', (s) => s.getAll()).then((all) => all
      .map(({ name, saved, size }) => ({ name, saved, size }))
      .sort((a, b) => b.saved - a.saved)),
    get: (name) => store('readonly', (s) => s.get(name)),
    put: (name, text) => store('readwrite', (s) => s.put({ name, text, saved: Date.now(), size: text.length })),
    remove: (name) => store('readwrite', (s) => s.delete(name)),
  };

  // -- picked files --------------------------------------------------------------------------------------

  const uploads = new Map();
  let uploadCount = 0;

  function pickFiles(accept) {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = accept;
      input.addEventListener('change', () => resolve(input.files[0] || null));
      input.addEventListener('cancel', () => resolve(null));
      input.click();
    });
  }

  function remember(file) {
    const path = `upload:${++uploadCount}/${file.name}`;
    uploads.set(path, file);
    return path;
  }

  const readDataUrl = (blob) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(blob);
  });

  // -- dialogs -------------------------------------------------------------------------------------

  function modal(build) {
    return new Promise((resolve) => {
      const back = document.createElement('div');
      back.className = 'modal-back';
      const box = document.createElement('div');
      box.className = 'modal';
      back.append(box);
      const close = (value) => { back.remove(); document.removeEventListener('keydown', onKey, true); resolve(value); };
      const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(null); } };
      document.addEventListener('keydown', onKey, true);
      back.addEventListener('mousedown', (e) => { if (e.target === back) close(null); });
      build(box, close);
      document.body.append(back);
      box.querySelector('[autofocus]')?.focus();
    });
  }

  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const when = (t) => new Date(t).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });

  /** The projects saved here, to open one, delete one, or open a .cmuov from the computer. */
  function openProjectDialog() {
    return modal(async (box, close) => {
      box.innerHTML = '<header><b>Open a vehicle</b><span>Saved in this browser</span></header>'
        + '<div class="modal-list"></div>'
        + '<footer><button class="btn subtle" data-up>Open a .cmuov file…</button><button class="btn" data-cancel>Cancel</button></footer>';
      const list = box.querySelector('.modal-list');
      const render = async () => {
        const all = await projects.list();
        list.innerHTML = all.length ? '' : '<p class="note">Nothing saved here yet. New vehicles save themselves as you work.</p>';
        for (const p of all) {
          const row = document.createElement('div');
          row.className = 'modal-row';
          row.innerHTML = `<button class="open"><b>${esc(p.name)}</b><span>${when(p.saved)} · ${Math.max(1, Math.round(p.size / 1024))} KB</span></button>`
            + '<button class="del" data-tip="Delete it from this browser">Delete</button>';
          row.querySelector('.open').addEventListener('click', () => close({ canceled: false, filePaths: [`store:${p.name}`] }));
          const del = row.querySelector('.del');
          del.addEventListener('click', async () => {
            if (!del.dataset.armed) { del.dataset.armed = '1'; del.textContent = 'Sure?'; return; }
            await projects.remove(p.name);
            render();
          });
          list.append(row);
        }
      };
      box.querySelector('[data-cancel]').addEventListener('click', () => close({ canceled: true, filePaths: [] }));
      box.querySelector('[data-up]').addEventListener('click', async () => {
        const file = await pickFiles('.cmuov,application/json');
        if (file) close({ canceled: false, filePaths: [remember(file)] });
      });
      await render();
    }).then((r) => r || { canceled: true, filePaths: [] });
  }

  /** A name to save a project under. */
  function nameDialog(suggested) {
    return modal(async (box, close) => {
      const taken = new Set((await projects.list()).map((p) => p.name));
      box.innerHTML = '<header><b>Save in this browser</b><span>Under what name?</span></header>'
        + `<input class="modal-input" autofocus spellcheck="false" value="${esc(suggested)}">`
        + '<p class="note warn" hidden>That name is taken: saving replaces it.</p>'
        + '<footer><button class="btn subtle" data-cancel>Cancel</button><button class="btn primary" data-ok>Save</button></footer>';
      const input = box.querySelector('input');
      const warn = box.querySelector('.warn');
      const check = () => { warn.hidden = !taken.has(input.value.trim()); };
      const ok = () => { const v = input.value.trim(); if (v) close(v); };
      input.addEventListener('input', check);
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') ok(); });
      box.querySelector('[data-ok]').addEventListener('click', ok);
      box.querySelector('[data-cancel]').addEventListener('click', () => close(null));
      input.select();
      check();
    });
  }

  /** A finished sprite to import: its files (an Aseprite file, or a PNG per layer), the way its nose
   * points in the picture and its name. Null if cancelled. */
  function importDialog() {
    return modal(async (box, close) => {
      box.innerHTML = '<header><b>Import a sprite</b><span>One picture, turned to each direction</span></header>'
        + '<div class="import-body">'
        + '<button class="btn" data-pick>Choose an Aseprite file or PNGs…</button><p class="note" data-files>Nothing chosen.</p>'
        + '<p class="note">Layers by name: <b>base</b>, <b>overlay</b> (over the riders), and if drawn <b>wreck</b>, <b>damaged</b>, '
        + '<b>wheels</b>, <b>door_driver</b>, <b>door_passenger</b>, <b>hood</b>, <b>tailgate</b> (each with <b>_open</b>), '
        + '<b>headlight_left</b>, <b>taillight_right</b>, <b>signal_left</b>, <b>searchlight</b>… A PNG is the layer its file is named.</p>'
        + '<label class="import-row"><span>Its front points</span><div class="segmented" data-nose>'
        + '<button data-value="N" class="on">Up</button><button data-value="E">Right</button><button data-value="S">Down</button><button data-value="W">Left</button></div></label>'
        + '<label class="import-row"><span>Name</span><input class="modal-input" data-name spellcheck="false" placeholder="armored car"></label>'
        + '</div><footer><button class="btn subtle" data-cancel>Cancel</button><button class="btn primary" data-ok disabled>Import</button></footer>';
      let files = [];
      let nose = 'N';
      const ok = box.querySelector('[data-ok]');
      const name = box.querySelector('[data-name]');
      for (const b of box.querySelectorAll('[data-nose] button')) {
        b.addEventListener('click', () => {
          nose = b.dataset.value;
          for (const o of box.querySelectorAll('[data-nose] button')) o.classList.toggle('on', o === b);
        });
      }
      box.querySelector('[data-pick]').addEventListener('click', () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.accept = '.aseprite,.ase,.png,image/png';
        input.addEventListener('change', () => {
          files = [...input.files];
          box.querySelector('[data-files]').textContent = files.length ? files.map((f) => f.name).join(', ') : 'Nothing chosen.';
          if (files.length && !name.value) name.value = files[0].name.replace(/\.[^.]*$/, '').replace(/[_-]+/g, ' ');
          ok.disabled = !files.length;
        });
        input.click();
      });
      ok.addEventListener('click', async () => {
        const read = await Promise.all(files.map(async (f) => ({ name: f.name, data: await readDataUrl(f) })));
        close({ files: read, nose, name: name.value.trim() || 'vehicle' });
      });
      box.querySelector('[data-cancel]').addEventListener('click', () => close(null));
    });
  }

  const base = (path) => String(path || '').replace(/^(store:|download:|upload:\d+\/)/, '').split(/[\\/]/).pop();

  // -- the API app.js uses ---------------------------------------------------------------------------

  window.cmu = {
    web: true,
    call,
    on(channel, callback) {
      if (!listeners.has(channel)) listeners.set(channel, []);
      listeners.get(channel).push(callback);
      return () => listeners.set(channel, listeners.get(channel).filter((c) => c !== callback));
    },
    settings: {
      get() {
        try { return Promise.resolve({ generator: 'server', ...JSON.parse(localStorage.getItem('cmuov.settings') || '{}') }); }
        catch { return Promise.resolve({ generator: 'server' }); }
      },
      set(patch) {
        let next = {};
        try {
          next = { ...JSON.parse(localStorage.getItem('cmuov.settings') || '{}'), ...patch };
          localStorage.setItem('cmuov.settings', JSON.stringify(next));
        } catch { /* settings are a convenience */ }
        return Promise.resolve({ generator: 'server', ...next });
      },
    },
    dialog: {
      async open(options = {}) {
        const exts = (options.filters || []).flatMap((f) => f.extensions || []);
        if (exts.includes('cmuov')) return openProjectDialog();
        if ((options.properties || []).includes('openDirectory') || exts.includes('py')) return { canceled: true, filePaths: [] };
        const file = await pickFiles(exts.length ? exts.map((e) => `.${e}`).join(',') : '');
        return file ? { canceled: false, filePaths: [remember(file)] } : { canceled: true, filePaths: [] };
      },
      async save(options = {}) {
        const def = options.defaultPath || 'file';
        if (/\.cmuov$/i.test(def)) {
          const name = await nameDialog(def.replace(/\.cmuov$/i, '') === 'vehicle' ? 'My vehicle' : base(def).replace(/\.cmuov$/i, ''));
          return name ? { canceled: false, filePath: `store:${name}` } : { canceled: true };
        }
        return { canceled: false, filePath: `download:${base(def)}` };
      },
    },
    fs: {
      async read(path) {
        if (path.startsWith('store:')) {
          const p = await projects.get(path.slice(6));
          if (!p) throw new Error(`${path.slice(6)} isn't saved in this browser any more.`);
          return p.text;
        }
        const file = uploads.get(path);
        if (!file) throw new Error(`Can't read ${base(path)}: pick it again.`);
        return file.text();
      },
      async write(path, text) {
        if (path.startsWith('store:')) return projects.put(path.slice(6), text);
        return download(base(path), text, 'application/json');
      },
      async writeBase64(path, data) {
        const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
        download(base(path), new Blob([bytes], { type: 'image/png' }));
      },
      async readDataUrl(path) {
        if (path.startsWith('/audio/') || path.startsWith('data:')) return path;
        const file = uploads.get(path);
        if (!file) throw new Error(`Can't read ${base(path)}: pick it again.`);
        return readDataUrl(file);
      },
      watch() { return Promise.resolve(); },
    },
    open(target) { if (/^https?:/.test(target)) window.open(target, '_blank', 'noopener'); },
    win: { minimize() {}, maximize() {}, close() {} },
    ready() {},
    // The web's own: a project's name from its path, a new untitled one's path, a download.
    displayName: (path) => base(path).replace(/\.cmuov$/i, ''),
    async untitledPath(name) {
      const taken = new Set((await projects.list()).map((p) => p.name));
      const stem = (name || '').trim() || 'Untitled vehicle';
      let n = 1;
      while (taken.has(n === 1 ? stem : `${stem} ${n}`)) n++;
      return `store:${n === 1 ? stem : `${stem} ${n}`}`;
    },
    download,
    importDialog,
  };

  connect();
})();
