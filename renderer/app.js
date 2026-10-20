// CMU Open Vehicles: the controller. Holds the project (a stack of layers drawn in four orthographic
// views), wires the tools, the layer list, the inspector and the preview, and talks to the Python
// backend that turns the drawing into the game's sprites.

import { mountIcons } from './icons.js';
import { EMPTY, KINDS, Layer, Project, VIEWS, ViewCanvas, slicePlane } from './editor.js';
import { DepthPanel } from './depth.js';
import { VehiclePreview } from './preview.js';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const api = window.cmu;

const DEFAULT_BOX = { length: 72, width: 48, height: 36 };
const KIND_COLOURS = {
  body: '#b8e26a', wheel: '#8b93a1', door: '#6cb7ff', hood: '#ffcf5c', tailgate: '#ff9f5c', hatch: '#ff7aa8', boot: '#e58cff',
  windshield: '#7de3e0', fueldoor: '#c39bff', lamp: '#fff07a', part: '#a3a7ae',
};
const PRESETS = [
  { name: 'Factory', body: null },
  { name: 'Apple red', body: '#D7262E' },
  { name: 'Navy', body: '#2E4F8A' },
  { name: 'Electric yellow', body: '#FFE41A' },
  { name: 'Orange', body: '#F07A1A' },
  { name: 'Gunmetal', body: '#6A737B' },
  { name: 'UPP green', body: '#4E5B3A', seats: '#8A7B55' },
  { name: 'Desert', body: '#B49A6A' },
];
const BASE_HEX = '#767e50';
const TOOL_KEYS = { b: 'pencil', e: 'eraser', g: 'fill', l: 'line', r: 'rect', o: 'ellipse', d: 'depth', i: 'picker' };
const TABS = ['layers', 'draw', 'paint', 'vehicle'];
// How long after a change the project saves itself into the browser.
const AUTOSAVE_MS = 2500;
// Damage types offered first, by what usually marks a vehicle; the rest follow.
const COMMON_DAMAGE = ['Piercing', 'Blunt', 'Slash', 'Heat', 'Caustic', 'Shock', 'Structural'];
// The levels a damage state is drawn at: how much of its damage type the part has taken.
const LEVELS = [20, 40, 60, 80, 100];
// What a new damage state is first called, and its type.
const STATE_GUESSES = [['Bullet holes', 'Piercing'], ['Dented', 'Blunt'], ['Burnt', 'Heat'], ['Slashed', 'Slash'],
  ['Corroded', 'Caustic'], ['Wrecked', 'Structural']];

// Kit a layer can be in the game: the item that fills its slot and the tag the slot takes.
const KIT_PRESETS = {
  spare: { label: 'Spare wheel', item: 'CMUJeepWheel', tag: 'CMUJeepWheel' },
  jerrycan: { label: 'Jerry can', item: 'RMCFuelCan', tag: 'CMUJerryCan' },
  shovel: { label: 'Shovel', item: 'CMEntrenchingTool', tag: 'Shovel' },
  axe: { label: 'Fire axe', item: 'RMCFireAxe', tag: 'FireAxe' },
  jack: { label: 'Jack', item: 'AU14MaintenanceJack', tag: 'MaintenanceJack' },
  extinguisher: { label: 'Extinguisher', item: 'CMFireExtinguisherPortable', tag: 'CMFireExtinguisherPortable' },
};

// What a vehicle drives like and sounds like, as the jeep does unless it's given its own.
const STATS = [
  ['maxSpeed', 'Top speed', 8], ['acceleration', 'Acceleration', 5], ['integrity', 'Hull', 400],
  ['fuel', 'Fuel tank', 60], ['fuelStart', 'Fuel in it', 45], ['crashDamage', 'Crash damage', 30],
];
const SOUNDS = [
  ['start', 'Start', '/Audio/CMU14/Jeep/jeep_engine_start.wav'], ['idle', 'Idle', '/Audio/CMU14/Jeep/jeep_engine_idle.wav'],
  ['drive', 'Drive', '/Audio/CMU14/Jeep/jeep_engine_drive.wav'], ['rough', 'Rough idle', '/Audio/CMU14/Jeep/jeep_engine_rough.wav'],
  ['roughDrive', 'Rough drive', '/Audio/CMU14/Jeep/jeep_engine_rough_drive.wav'], ['rev', 'Rev', '/Audio/CMU14/Jeep/jeep_engine_rev.wav'],
  ['stall', 'Stall', '/Audio/CMU14/Jeep/jeep_engine_sputter.wav'], ['horn', 'Horn', '/Audio/CMU14/Jeep/jeep_horn.wav'],
  ['hornLong', 'Crash horn', '/Audio/CMU14/Jeep/jeep_horn_long.wav'], ['blinker', 'Blinker', '/Audio/CMU14/Jeep/jeep_blinker.wav'],
  ['door', 'Door opens', '/Audio/CMU14/Jeep/jeep_hood_open.wav'], ['doorClose', 'Door shuts', '/Audio/CMU14/Jeep/jeep_hood_close.wav'],
  ['lid', 'Lid opens', '/Audio/CMU14/Jeep/jeep_hood_open.wav'], ['lidClose', 'Lid shuts', '/Audio/CMU14/Jeep/jeep_hood_close.wav'],
  ['windshield', 'Windshield', '/Audio/CMU14/Jeep/jeep_windshield.wav'],
];

const hexToRgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

class App {
  constructor() {
    this.project = null;
    this.palette = [];
    this.materials = [];
    this.material = 0;
    this.brushDepth = 0;
    this.depthMode = 'auto';
    this.brushSize = 1;
    // On a voxel layer: how far a stroke fills in along each line of sight (off, extrudeN voxels, or
    // through to the far side), and whether the eraser cuts through or takes off the surface.
    this.extrude = 'off';
    this.extrudeN = 4;
    this.eraseMode = 'through';
    this.tool = 'pencil';
    this.lastTool = 'pencil';
    this.mirror = true;
    this.depthView = false;
    this.showGhosts = true;
    // A slice shows the slices either side of it, the nearer warm and the deeper cool.
    this.onion = false;
    this.paint = { body: null, interior: null, seats: null, dirt: 0 };
    this.undoStack = [];
    this.redoStack = [];
    this.dirty = false;
    this.file = null;
    this.info = null;
    this.previewTimer = null;
    this.previewSeq = 0;
    this.swings = new Map();
    this.toneCache = {};
    this.views = {};
    this.activeView = null;
    this.readySent = false;
    this.generating = false;
    this.showDamage = false;
    this.damageTypes = null;
    this.drives = new Map();
    this.tab = null;
    this.ownWrite = 0;
  }

  // -- start ---------------------------------------------------------------------------------------

  async start() {
    mountIcons();
    this.setupChrome();
    this.setupTools();
    this.setupInspector();
    this.setupPreview();
    this.setupTabs();
    this.setupStages();
    this.setupGame();
    this.setupCamera();
    this.setupKeys();
    for (const section of $$('.view')) this.views[section.dataset.view] = new ViewCanvas(section, section.dataset.view, this);
    this.depthPanel = new DepthPanel($('#depthPanel'), this);
    this.layoutBoard();
    api.on('backend:event', (msg) => this.backendEvent(msg));
    api.on('backend:log', (line) => console.debug('[python]', line));
    api.on('file:changed', (path) => this.fileChanged(path));
    this.settings = await api.settings.get();
    if (this.settings.woke) document.documentElement.classList.add('woke');
    this.setTab(TABS.includes(this.settings.sideTab) ? this.settings.sideTab : 'layers', true);
    await this.openGenerator(this.settings.generator, true);
  }

  async openGenerator(path, first = false) {
    this.setBackend('busy', 'Loading the generator…');
    try {
      this.info = await api.call('open_generator', { path });
    } catch (err) {
      this.setBackend('error', 'Generator failed to load');
      this.toast(`<b>Generator</b> ${this.short(err.message)}`, true);
      return;
    }
    this.palette = this.info.materials;
    this.materials = this.palette.map((m) => m.name);
    this.toneCache = {};
    $('#watchText').textContent = 'Saved in this browser as you work';
    this.buildSwatches();
    this.previewer.wheelDelay = Math.round((this.info.wheelDelay || 0.1) * 1000);
    api.call('damage_types').then((t) => { this.damageTypes = t; this.renderStages(); }).catch(() => {});
    this.setBackend('ready', `Python ready · generator ${this.info.seconds}s`);
    if (first) {
      const last = this.settings.lastFile;
      if (last) {
        try { await this.openFile(last); return; } catch { /* fall through to the jeep */ }
      }
      await this.loadVehicle(this.info.variants[0], true);
    } else if (this.project) {
      this.project.materials = this.materials;
      this.repaintViews();
      this.schedulePreview(0);
    }
  }

  // -- chrome: window, tooltips, toasts ------------------------------------------------------------

  setupChrome() {
    $('#glyph').addEventListener('click', () => this.setWoke(!document.documentElement.classList.contains('woke')));
    for (const b of $$('[data-action]')) b.addEventListener('click', () => this.action(b.dataset.action, b));
    for (const b of $$('[data-clear]')) b.addEventListener('click', () => this.clearView(b.dataset.clear));
    const tip = $('#tip');
    let timer = null;
    document.addEventListener('mouseover', (e) => {
      const el = e.target.closest('[data-tip]');
      clearTimeout(timer);
      if (!el) { tip.classList.remove('on'); return; }
      timer = setTimeout(() => {
        tip.textContent = el.dataset.tip;
        const r = el.getBoundingClientRect();
        const beside = el.closest('.rail, .tabs');
        const left = el.closest('.rail') ? r.right + 10 : el.closest('.tabs') ? r.left - tip.offsetWidth - 10
          : Math.min(innerWidth - tip.offsetWidth - 10, r.left);
        const top = beside ? r.top + r.height / 2 - 12 : r.bottom + 8;
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
        tip.classList.add('on');
      }, 380);
    });
    document.addEventListener('mousedown', () => { clearTimeout(timer); tip.classList.remove('on'); });
  }

  /** The CMU emblem's colours, and the app's theme with them. */
  setWoke(on) {
    document.documentElement.classList.toggle('woke', on);
    const btn = $('#glyph');
    btn.classList.remove('pop');
    void btn.offsetWidth;
    if (on) btn.classList.add('pop');
    api.settings.set({ woke: on });
  }

  toast(html, error = false, ms = 3200) {
    const el = document.createElement('div');
    el.className = `toast${error ? ' error' : ''}`;
    el.innerHTML = html;
    $('#toasts').append(el);
    setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 240); }, ms);
  }

  short(message) {
    return String(message).split('\n')[0].replace(/^Error invoking remote method '[^']+': (Error: )?/, '');
  }

  setBackend(state, text) {
    $('#backendDot').className = `dot ${state}`;
    $('#backendText').textContent = text;
  }

  setDirty(on) {
    if (on) this.scheduleChecks();
    this.dirty = on;
    $('#docDirty').classList.toggle('on', on);
    clearTimeout(this.autosaveTimer);
    if (on) this.autosaveTimer = setTimeout(() => this.autosave(), AUTOSAVE_MS);
  }

  setFile(path) {
    this.file = path;
    $('#docName').textContent = path ? api.displayName(path) : 'untitled';
  }

  /** Saves into the browser a little after each change, so closing the tab loses nothing: under its
   * own name once it has one, else as a new untitled project. */
  async autosave() {
    if (!this.dirty || !this.project) return;
    try {
      if (!this.file) this.setFile(await api.untitledPath(this.vehicle().name));
      await api.fs.write(this.file, this.project.toFile());
      this.setDirty(false);
      api.settings.set({ lastFile: this.file });
    } catch (err) {
      this.toast(`<b>Autosave</b> ${this.escape(this.short(err.message))}`, true, 4000);
    }
  }

  /** The project as a .cmuov file, to keep or share outside the browser. */
  downloadProject() {
    if (!this.project) return;
    const name = (this.file ? api.displayName(this.file) : this.vehicle().name || 'vehicle').replace(/[^\w .-]+/g, '_');
    api.download(`${name}.cmuov`, this.project.toFile(), 'application/json');
  }

  // -- segmented controls with a sliding pill --------------------------------------------------------

  segmented(el, onPick) {
    let pill = el.querySelector('.seg-pill');
    if (!pill) {
      pill = document.createElement('span');
      pill.className = 'seg-pill';
      el.prepend(pill);
    }
    const place = () => {
      const on = el.querySelector('button.on');
      if (!on) { pill.style.width = '0px'; return; }
      pill.style.width = `${on.offsetWidth}px`;
      pill.style.transform = `translateX(${on.offsetLeft}px)`;
    };
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || !el.contains(b)) return;
      for (const x of el.querySelectorAll('button')) x.classList.toggle('on', x === b);
      place();
      onPick?.(b.dataset.value, b);
    });
    el.setValue = (value) => {
      for (const x of el.querySelectorAll('button')) x.classList.toggle('on', x.dataset.value === String(value));
      place();
    };
    new ResizeObserver(place).observe(el);
    requestAnimationFrame(place);
    return el;
  }

  slider(input, output, format, onInput) {
    const sync = () => {
      const min = +input.min;
      const max = +input.max;
      input.style.setProperty('--fill', `${((+input.value - min) / Math.max(1, max - min)) * 100}%`);
      output.textContent = format(+input.value);
    };
    input.addEventListener('input', () => { sync(); onInput(+input.value); });
    input.sync = sync;
    sync();
  }

  // -- tools -------------------------------------------------------------------------------------

  setupTools() {
    for (const b of $$('.tool')) b.addEventListener('click', () => this.setTool(b.dataset.tool));
    $('#mirrorToggle').addEventListener('click', () => this.toggle('mirror'));
    $('#depthViewToggle').addEventListener('click', () => this.toggle('depthView'));
    $('#ghostToggle').addEventListener('click', () => this.toggle('showGhosts'));
    $('#mirrorToggle').classList.toggle('on', this.mirror);
    this.setTool('pencil');
  }

  setTool(tool) {
    if (tool !== 'picker') this.lastTool = tool;
    this.tool = tool;
    const btn = $(`.tool[data-tool="${tool}"]`);
    for (const b of $$('.tool')) b.classList.toggle('on', b === btn);
    const ind = $('#railIndicator');
    ind.style.transform = `translateY(${btn.offsetTop}px)`;
  }

  toggle(name) {
    this[name] = !this[name];
    const id = { mirror: '#mirrorToggle', depthView: '#depthViewToggle', showGhosts: '#ghostToggle', onion: '#onionToggle' }[name];
    $(id).classList.toggle('on', this[name]);
    this.repaintViews();
    this.depthPanel.refresh();
    if (name === 'mirror') this.toast(`Mirror <b>${this.mirror ? 'on' : 'off'}</b>`, false, 1400);
  }

  // -- inspector -----------------------------------------------------------------------------------

  setupInspector() {
    this.slider($('#depthRange'), $('#depthOut'), (v) => v, (v) => { this.brushDepth = v; this.setDepthMode('fixed'); });
    this.segmented($('#depthModeSeg'), (v) => this.setDepthMode(v));
    this.segmented($('#sizeSeg'), (v) => { this.brushSize = +v; });
    this.segmented($('#extrudeSeg'), (v) => this.setExtrude(v));
    this.slider($('#extrudeRange'), $('#extrudeOut'), (v) => v, (v) => {
      this.extrudeN = v;
      $('#extrudeN').textContent = `${v} voxel${v === 1 ? '' : 's'}`;
      this.setExtrude('n');
    });
    this.segmented($('#eraseSeg'), (v) => { this.eraseMode = v; });
    this.segmented($('#speedSeg'), (v) => { this.previewer.speed = +v; });
    this.slider($('#dirtRange'), $('#dirtOut'), (v) => `${v}%`, (v) => { this.paint.dirt = v / 100; this.drives.clear(); this.schedulePreview(160); });
    this.slider($('#thickRange'), $('#thickOut'), (v) => (v ? `${v}` : 'full'), (v) => this.setLayerProp('thickness', v));
    this.segmented($('#sidesSeg'), (v) => this.setLayerProp('sides', v));
    this.segmented($('#modelSeg'), (v) => this.setModel(v));
    this.segmented($('#resizeSeg'));
    $('#matSearch').addEventListener('input', (e) => {
      const q = e.target.value.trim().toLowerCase();
      for (const s of $$('.swatch')) s.classList.toggle('hidden', q && !s.dataset.name.includes(q));
    });
    for (const [id, key] of [['#paintBody', 'body'], ['#paintInterior', 'interior'], ['#paintSeats', 'seats']]) {
      const input = $(id);
      input.addEventListener('input', () => {
        this.paint[key] = hexToRgb(input.value);
        input.parentElement.classList.remove('factory');
        this.schedulePreview(120);
      });
    }
    $('#paintReset').addEventListener('click', () => this.applyPreset(PRESETS[0]));
    const presets = $('#presets');
    for (const p of PRESETS) {
      const b = document.createElement('button');
      b.className = 'preset';
      b.innerHTML = `<i style="background:${p.body || BASE_HEX}"></i>${p.name}`;
      b.addEventListener('click', () => this.applyPreset(p));
      presets.append(b);
    }
    this.applyPreset(PRESETS[0], false);

    const kind = $('#layerKind');
    for (const [k, v] of Object.entries(KINDS)) kind.append(new Option(v.label, k));
    kind.addEventListener('change', () => this.setLayerProp('kind', kind.value));
    $('#layerRides').addEventListener('change', (e) => this.setLayerProp('rides', e.target.value || null));
    this.segmented($('#hingeSeg'), (v) => this.setHinge({ edge: v === 'auto' ? null : v }));
    this.slider($('#openRange'), $('#openOut'), (v) => (+v ? `${v}°` : 'auto'), (v) => this.setHinge({ angle: +v || null }));
    const kit = $('#layerKit');
    kit.append(new Option('None', ''));
    for (const [id, k] of Object.entries(KIT_PRESETS)) kit.append(new Option(k.label, id));
    kit.addEventListener('change', () => {
      const k = KIT_PRESETS[kit.value];
      this.setLayerProp('kit', k ? { preset: kit.value, item: k.item, tag: k.tag, host: this.project.layer.rides || null } : null);
    });
    const menu = $('#kindMenu');
    for (const [k, v] of Object.entries(KINDS)) {
      const b = document.createElement('button');
      b.innerHTML = `<i style="background:${KIND_COLOURS[k]}"></i>${v.label}`;
      b.addEventListener('click', () => { menu.classList.remove('on'); this.addLayer(k); });
      menu.append(b);
    }
    $('#layerAdd').addEventListener('click', (e) => { e.stopPropagation(); menu.classList.toggle('on'); });
    document.addEventListener('click', (e) => { if (!menu.contains(e.target)) menu.classList.remove('on'); });
  }

  /** Auto depth follows the surface drawn on; fixed puts every pixel at the slider's depth. */
  setDepthMode(mode) {
    if (mode === this.depthMode) return;
    this.depthMode = mode;
    $('#depthModeSeg').setValue(mode);
    $('#depthField').classList.toggle('dim', mode === 'auto');
  }

  /** On a voxel layer a stroke can fill in along each line of sight instead of drawing a surface:
   * extrudeN voxels from its depth, or through to the far side. */
  setExtrude(mode) {
    this.extrude = mode;
    $('#extrudeSeg').setValue(mode);
    $('#extrudeField').classList.toggle('dim', mode !== 'n');
  }

  applyPreset(p, preview = true) {
    this.paint.body = p.body ? hexToRgb(p.body) : null;
    this.paint.interior = p.interior ? hexToRgb(p.interior) : null;
    this.paint.seats = p.seats ? hexToRgb(p.seats) : null;
    for (const [id, key] of [['#paintBody', 'body'], ['#paintInterior', 'interior'], ['#paintSeats', 'seats']]) {
      const v = p[key];
      $(id).value = v || BASE_HEX;
      $(id).parentElement.classList.toggle('factory', !v);
    }
    if (preview) this.schedulePreview(0);
  }

  tones(kind) {
    if (!this.toneCache[kind]) this.toneCache[kind] = this.palette.map((m) => m.tones[kind] || m.tones.top);
    return this.toneCache[kind];
  }

  buildSwatches() {
    const box = $('#swatches');
    box.innerHTML = '';
    this.palette.forEach((m, i) => {
      const s = document.createElement('button');
      s.className = 'swatch';
      s.dataset.name = m.name;
      s.dataset.tip = m.name.replace(/_/g, ' ');
      const t = m.tones;
      s.style.background = `linear-gradient(140deg, rgb(${t.hi}) 0%, rgb(${t.top}) 46%, rgb(${t.front}) 100%)`;
      s.addEventListener('click', () => this.setMaterial(i));
      if (this.project?.customMaterials.some((c) => c.name === m.name)) s.classList.add('own');
      box.append(s);
    });
    this.setMaterial(Math.max(0, this.materials.indexOf(this.materials[this.material] || 'od')));
  }

  setMaterial(i) {
    this.material = i;
    $$('.swatch').forEach((s, n) => s.classList.toggle('on', n === i));
    const t = this.palette[i]?.tones;
    if (t) $('#matChip').style.background = `rgb(${t.top})`;
    $('#matName').textContent = (this.materials[i] || '—').replace(/_/g, ' ');
    if (this.tool === 'eraser' || this.tool === 'picker') this.setTool('pencil');
  }

  pick(mat, depth) {
    this.setMaterial(mat);
    this.brushDepth = depth;
    $('#depthRange').value = depth;
    $('#depthRange').sync();
    this.setTool(this.lastTool === 'eraser' ? 'pencil' : this.lastTool);
    this.toast(`Picked <b>${this.materials[mat].replace(/_/g, ' ')}</b> at depth ${depth}`, false, 1500);
  }


  // -- layers --------------------------------------------------------------------------------------

  renderLayers() {
    const list = $('#layerList');
    list.innerHTML = '';
    const p = this.project;
    // Topmost first, like an image editor; a layer's damage states under it.
    for (let n = p.layers.length - 1; n >= 0; n--) {
      const l = p.layers[n];
      if (l.stage && p.parentOf(l)) continue;
      const row = document.createElement('div');
      row.className = `layer${n === p.active ? ' on' : ''}${l.visible ? '' : ' hidden-layer'}`;
      row.dataset.index = n;
      row.draggable = true;
      row.innerHTML = `
        <i class="grip" data-icon="grip"></i>
        <span class="kdot" style="background:${KIND_COLOURS[l.kind] || KIND_COLOURS.part}"></span>
        <span class="lname">${this.escape(l.name)}</span>${l.voxels ? '<span class="vbadge" data-tip="A voxel layer: its voxels are its shape">voxels</span>' : ''}
        <span class="lkind">${l.rides ? `on ${this.escape(l.rides)}` : (KINDS[l.kind] || KINDS.part).label}</span>
        <button class="lbtn dim" data-act="dup" data-tip="Duplicate"><i data-icon="copy"></i></button>
        <button class="lbtn dim" data-act="del" data-tip="Delete"><i data-icon="trash"></i></button>
        <button class="lbtn ${l.locked ? 'keep' : 'dim'}" data-act="lock" data-tip="Lock"><i data-icon="${l.locked ? 'lock' : 'unlock'}"></i></button>
        <button class="lbtn ${l.visible ? '' : 'keep'}" data-act="eye" data-tip="Show or hide"><i data-icon="${l.visible ? 'eye' : 'eyeoff'}"></i></button>`;
      row.addEventListener('click', (e) => {
        const act = e.target.closest('[data-act]')?.dataset.act;
        if (act) { e.stopPropagation(); this.layerAction(n, act, e.target.closest('[data-act]')); return; }
        this.selectLayer(n);
      });
      row.querySelector('.lname').addEventListener('dblclick', (e) => { e.stopPropagation(); this.renameLayer(n, row); });
      row.addEventListener('dragstart', (e) => { row.classList.add('dragging'); e.dataTransfer.setData('text/plain', String(n)); });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
      row.addEventListener('dragover', (e) => {
        e.preventDefault();
        const r = row.getBoundingClientRect();
        const above = e.clientY < r.top + r.height / 2;
        row.classList.toggle('drop-above', above);
        row.classList.toggle('drop-below', !above);
      });
      row.addEventListener('dragleave', () => row.classList.remove('drop-above', 'drop-below'));
      row.addEventListener('drop', (e) => {
        e.preventDefault();
        const from = +e.dataTransfer.getData('text/plain');
        const above = row.classList.contains('drop-above');
        row.classList.remove('drop-above', 'drop-below');
        // Above in the list is later in the stack.
        this.moveLayer(from, above ? n + (from > n ? 1 : 0) : n - (from < n ? 1 : 0));
      });
      list.append(row);
      for (const s of p.statesOf(l)) list.append(this.stateRow(l, s));
    }
    mountIcons(list);
    list.querySelector('.layer.on')?.scrollIntoView({ block: 'nearest' });
    const l = p.layer;
    const rides = $('#layerRides');
    rides.innerHTML = '';
    rides.append(new Option('Nothing', ''));
    for (const other of p.layers) {
      if (other !== l && other.hinged) rides.append(new Option(other.name, other.name));
    }
    rides.value = l.rides || '';
    $('#layerKind').value = l.kind;
    $('#sidesSeg').setValue(l.sides);
    $('#thickRange').value = l.thickness;
    $('#thickRange').sync();
    // A voxel layer is its voxels, however thick its views would carve it.
    $('#thickField').classList.toggle('dim', !!l.voxels);
    $('#modelSeg [data-value="generator"]').style.display = l.native ? '' : 'none';
    $('#hingeField').style.display = l.hinged ? '' : 'none';
    $('#openField').style.display = l.hinged ? '' : 'none';
    $('#hingeSeg').setValue(l.hinge?.edge || 'auto');
    $('#openRange').value = l.hinge?.angle || 0;
    $('#openRange').sync();
    $('#kitField').style.display = l.kind === 'part' && !l.stage ? '' : 'none';
    $('#layerKit').value = l.kit?.preset || '';
    $('#modelSeg').setValue(l.voxels ? 'voxels' : l.native ? 'generator' : 'drawing');
    this.renderStages();
  }

  /** A damage state under its layer: its name, type and the levels drawn, each a click away. */
  stateRow(owner, s) {
    const p = this.project;
    const row = document.createElement('div');
    const on = s.levels.find((lv) => lv.layer === p.layer);
    row.className = `layer stage-row${on ? ' on' : ''}`;
    row.innerHTML = `<i data-icon="crack"></i><span class="lname">${this.escape(s.name)}</span>
      <span class="dtype">${this.escape(this.typeLabel(s.type))}</span><span class="lvls"></span>`;
    const lvls = row.querySelector('.lvls');
    for (const lv of s.levels) {
      const b = document.createElement('button');
      b.className = `lvl${lv.layer === p.layer ? ' on' : ''}`;
      b.textContent = lv.from;
      b.dataset.tip = `Draw it at ${lv.from}`;
      b.addEventListener('click', (e) => { e.stopPropagation(); this.selectLayer(p.layers.indexOf(lv.layer)); });
      lvls.append(b);
    }
    row.addEventListener('click', () => this.selectLayer(p.layers.indexOf((on || s.levels[0]).layer)));
    row.querySelector('.lname').addEventListener('dblclick', (e) => {
      e.stopPropagation();
      const span = row.querySelector('.lname');
      const input = document.createElement('input');
      input.value = s.name;
      span.innerHTML = '';
      span.append(input);
      input.focus();
      input.select();
      let finished = false;
      const done = (save) => {
        if (finished) return;
        finished = true;
        if (save) this.renameState(owner, s.levels[0].layer, input.value.trim());
        this.renderLayers();
      };
      input.addEventListener('keydown', (k) => {
        k.stopPropagation();
        if (k.key === 'Enter') done(true);
        if (k.key === 'Escape') done(false);
      });
      input.addEventListener('blur', () => done(true));
    });
    return row;
  }

  /** A damage type as it reads: "Piercing", "Any brute" for a group. */
  typeLabel(id) {
    if (!id) return '';
    if (id.startsWith('group:')) return `Any ${id.slice(6).toLowerCase()}`;
    return id.replace(/([a-z])([A-Z])/g, '$1 $2');
  }

  escape(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  selectLayer(n) {
    if (n === this.project.active) return;
    const was = this.project.layer;
    this.project.active = n;
    this.renderLayers();
    this.repaintViews();
    // Drawing a stage shows it in the preview, in its layer's place.
    if (was?.stage || this.project.layer.stage) this.schedulePreview(0);
  }

  layerAction(n, act, btn) {
    const p = this.project;
    const l = p.layers[n];
    if (act === 'eye') {
      this.beginStroke();
      l.visible = !l.visible;
      this.structureChanged();
    } else if (act === 'lock') {
      l.locked = !l.locked;
      this.renderLayers();
    } else if (act === 'dup') {
      this.beginStroke();
      p.layers.splice(n + 1, 0, l.clone(p.box));
      p.active = n + 1;
      this.structureChanged();
      this.toast(`Duplicated <b>${this.escape(l.name)}</b>`, false, 1500);
    } else if (act === 'del') {
      if (p.layers.length === 1) { this.toast('A vehicle needs at least one layer.', true); return; }
      if (!btn.classList.contains('confirm')) {
        btn.classList.add('confirm', 'keep');
        btn.style.color = 'var(--danger)';
        btn.dataset.tip = 'Click again to delete';
        setTimeout(() => { btn.classList.remove('confirm', 'keep'); btn.style.color = ''; btn.dataset.tip = 'Delete'; }, 1800);
        return;
      }
      this.beginStroke();
      // Its damage states go with it.
      const gone = new Set([l, ...p.stagesOf(l)]);
      const active = p.layer;
      p.layers = p.layers.filter((x) => !gone.has(x));
      p.active = Math.max(0, gone.has(active) ? Math.min(n, p.layers.length - 1) : p.layers.indexOf(active));
      this.structureChanged();
      this.toast(`Deleted <b>${this.escape(l.name)}</b>${gone.size > 1 ? ` and its ${gone.size - 1} damage level${gone.size > 2 ? 's' : ''}` : ''} · Ctrl+Z to undo`, false, 2200);
    }
  }

  renameLayer(n, row) {
    const l = this.project.layers[n];
    const span = row.querySelector('.lname');
    const input = document.createElement('input');
    input.value = l.name;
    span.innerHTML = '';
    span.append(input);
    input.focus();
    input.select();
    const done = (save) => {
      const name = input.value.trim();
      if (save && name && name !== l.name) {
        if (this.project.layers.some((x) => x !== l && x.name === name)) {
          this.toast(`There's already a layer called <b>${this.escape(name)}</b>.`, true, 1800);
        } else {
          this.beginStroke();
          // What rides on it and its damage states follow the name.
          for (const x of this.project.layers) {
            if (x.rides === l.name) x.rides = name;
            if (x.stage?.of === l.name) x.stage.of = name;
          }
          l.name = name;
          this.setDirty(true);
          this.project.version++;
          this.schedulePreview(0);
        }
      }
      this.renderLayers();
    };
    input.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') done(true);
      if (e.key === 'Escape') done(false);
    });
    input.addEventListener('blur', () => done(true));
  }

  moveLayer(from, to) {
    const p = this.project;
    to = Math.max(0, Math.min(p.layers.length - 1, to));
    if (from === to) return;
    this.beginStroke();
    const [l] = p.layers.splice(from, 1);
    p.layers.splice(to, 0, l);
    p.active = to;
    this.structureChanged();
  }

  addLayer(kind) {
    const p = this.project;
    this.beginStroke();
    const count = p.layers.filter((l) => l.kind === kind).length;
    const label = KINDS[kind].label;
    const layer = new Layer(p.box, kind, count ? `${label} ${count + 1}` : label);
    p.layers.splice(p.active + 1, 0, layer);
    p.active += 1;
    this.structureChanged();
    this.toast(`Added a <b>${label.toLowerCase()}</b> layer. Draw it in the views; blank views don't limit it.`, false, 2600);
  }

  /** The active layer's hinge changed: an edge or an angle, the rest kept. */
  setHinge(change) {
    const l = this.project?.layer;
    if (!l) return;
    const next = { ...(l.hinge || {}), ...change };
    for (const k of Object.keys(next)) if (next[k] == null) delete next[k];
    this.setLayerProp('hinge', Object.keys(next).length ? next : null);
  }

  setLayerProp(prop, value) {
    const l = this.project?.layer;
    if (!l || l[prop] === value) return;
    this.beginStroke();
    l[prop] = value;
    if (prop === 'sides' || prop === 'thickness') l.native = null;
    if (prop === 'kind') {
      const k = KINDS[value];
      l.sides = k.sides;
      l.thickness = k.thickness;
    }
    this.structureChanged();
  }

  /** Generator keeps the generator's model with the drawing applied over it; drawing lets the layer
   * go, to be carved from its views alone; voxels bakes it into voxels of its own, drawn on along
   * each line of sight. A voxel layer made a drawing again keeps what its views show of it. */
  setModel(value) {
    const l = this.project?.layer;
    if (!l) return;
    const now = l.voxels ? 'voxels' : l.native ? 'generator' : 'drawing';
    if (value === now) return;
    if (value === 'voxels') { this.bakeLayer(l); return; }
    if (value !== 'drawing') { this.renderLayers(); return; }
    this.beginStroke();
    if (l.voxels) {
      for (const v of VIEWS) {
        const { mat, depth } = l.projection(this.project.box, v);
        l.views[v].mat.set(mat);
        l.views[v].depth.set(depth);
      }
      l.voxels = null;
      l.rev++;
      this.toast(`<b>${this.escape(l.name)}</b> is carved from its views again: what they can't show is gone · Ctrl+Z to undo`, false, 2600);
    } else {
      l.native = null;
      this.toast(`<b>${this.escape(l.name)}</b> is now only its drawing · Ctrl+Z to undo`, false, 2200);
    }
    this.structureChanged();
  }

  /** A layer made voxels by the backend (a drawn layer carved, one from the generator its model as
   * it faces east with what's drawn on it): from then on its voxels are its shape. */
  async bakeLayer(l) {
    const p = this.project;
    this.setBackend('busy', `Baking ${l.name}…`);
    try {
      const r = await api.call('bake', { project: p.toBackend(), box: p.box, layer: p.layers.indexOf(l), materials: this.materials });
      if (this.project !== p || !p.layers.includes(l) || l.voxels) return;
      this.beginStroke();
      l.voxels = Project.decodeVoxels(r.voxels, p.box);
      l.native = null;
      l.rev++;
      this.structureChanged();
      this.toast(`<b>${this.escape(l.name)}</b> is ${r.count.toLocaleString()} voxels now. Drawing on a view works along each line of sight; slice a view to reach inside, or paint it on the preview · Ctrl+Z to undo`, false, 4200);
    } catch (err) {
      this.toast(`<b>Bake</b> ${this.escape(this.short(err.message))}`, true, 5000);
      this.renderLayers();
    } finally {
      this.setBackend('ready', 'Python ready');
    }
  }

  structureChanged() {
    this.project.version++;
    this.swings.clear();
    this.drives.clear();
    this.setDirty(true);
    this.renderLayers();
    this.repaintViews();
    this.schedulePreview(0);
  }

  /** Empties one view of the active layer. On a voxel layer everything a view sees is all of it, so
   * it empties the layer, or with the view sliced, the slice. */
  clearView(view) {
    const l = this.project?.layer;
    if (!l || !this.canEdit()) return;
    if (l.voxels) {
      const vc = this.views[view];
      const cells = [];
      if (vc.slicing) {
        const at = slicePlane(this.project.box, view, vc.slice);
        const { length: L, width: W, height: H } = this.project.box;
        const span = (v, n) => (v != null ? [v] : [...Array(n).keys()]);
        for (const i of span(at.i, L)) for (const j of span(at.j, W)) for (const k of span(at.k, H)) cells.push((i * W + j) * H + k);
      }
      const gone = vc.slicing ? cells.filter((n) => l.voxels[n] !== EMPTY) : null;
      if (gone ? !gone.length : l.isEmpty()) return;
      this.beginStroke();
      const vox = l.writable();
      if (gone) for (const n of gone) vox[n] = EMPTY;
      else vox.fill(EMPTY);
      this.focusView(vc);
      this.changed(true);
      this.toast(`Cleared ${gone ? `the slice of <b>${this.escape(l.name)}</b>` : `every voxel of <b>${this.escape(l.name)}</b>`} · Ctrl+Z to undo`, false, 1800);
      return;
    }
    if (l.views[view].mat.every((m) => m === 255)) return;
    this.beginStroke();
    l.views[view].mat.fill(255);
    l.views[view].depth.fill(0);
    this.focusView(this.views[view]);
    this.changed(true);
    this.toast(`Cleared <b>${this.escape(l.name)}</b>'s ${view} view · Ctrl+Z to undo`, false, 1800);
  }

  canEdit() {
    const l = this.project.layer;
    if (l.locked) { this.toast(`<b>${this.escape(l.name)}</b> is locked.`, true, 1600); return false; }
    if (!l.visible) { this.toast(`<b>${this.escape(l.name)}</b> is hidden.`, true, 1600); return false; }
    return true;
  }

  // -- the board of views ------------------------------------------------------------------------------

  /** Whether the board shows the imported picture alone: a sprite project, out of the 4 dir workspace. */
  spriteOnly() {
    return this.project?.vehicle?.camera?.mode === 'sprite' && !this.fourDir;
  }

  layoutBoard() {
    const box = this.project?.box || DEFAULT_BOX;
    const board = $('#board');
    const only = this.spriteOnly();
    board.classList.toggle('sprite-only', only);
    for (const s of $$('#board > [data-view]')) s.hidden = only && s.dataset.view !== 'top';
    // The top view is drawn nose right: turned so the picture shows as it was drawn.
    const nose = this.project?.vehicle?.camera?.nose;
    this.views.top?.setTurn(only ? ({ S: 90, W: 180, N: 270 }[nose] || 0) : 0);
    if (only) {
      // The picture, seen from above, takes the whole board: seats, lamps and parts are placed on it.
      board.style.gridTemplateColumns = '1fr';
      board.style.gridTemplateRows = '1fr';
      const top = $('[data-view="top"]');
      top.style.gridRow = 1;
      top.style.gridColumn = 1;
      for (const v of Object.values(this.views)) v.fit();
      return;
    }
    board.style.gridTemplateColumns = `${box.length}fr ${box.width}fr`;
    board.style.gridTemplateRows = `${box.height}fr ${box.height}fr ${box.width}fr`;
    // Side, left and top share the length; front beside the side and back beside the left share
    // their height.
    // The depth panel takes the corner beside the top.
    const at = { side: [1, 1], front: [1, 2], left: [2, 1], back: [2, 2], top: [3, 1], depth: [3, 2] };
    for (const [v, [r, c]] of Object.entries(at)) {
      const s = $(`[data-view="${v}"]`);
      s.style.gridRow = r;
      s.style.gridColumn = c;
    }
    for (const v of Object.values(this.views)) v.fit();
  }

  repaintViews() {
    for (const v of Object.values(this.views)) v.paint();
  }

  focusView(view) {
    if (this.activeView === view) return;
    this.activeView?.section.classList.remove('active');
    this.activeView = view;
    view.section.classList.add('active');
    this.depthPanel.refresh();
    this.syncDepthRange();
  }

  /** The depth slider for the active view: from as far out as the box can still grow (below 0, up to
   * the game's sprite frame) to its far side. */
  syncDepthRange() {
    const view = this.activeView;
    if (!view || !this.project) return;
    const range = $('#depthRange');
    range.max = view.shape.maxDepth;
    range.min = -this.outward(view.view);
    const v = Math.max(+range.min, Math.min(+range.max, this.brushDepth));
    if (v !== this.brushDepth) this.brushDepth = v;
    range.value = v;
    range.sync();
  }

  /** How many voxels the box can grow out on a view's side before the vehicle leaves the 96 px frame. */
  outward(view) {
    const r = this.room;
    const b = this.project?.box;
    if (!r || !b) return 0;
    if (view === 'top') return Math.max(0, r.height - b.height);
    if (view === 'front' || view === 'back') return Math.max(0, Math.floor((r.length - b.length) / 2));
    return Math.max(0, Math.floor((r.width - b.width) / 2));
  }

  /** The room the box has to grow, asked again when it changes. */
  async refreshRoom() {
    const box = this.project?.box;
    if (!box) return;
    try {
      this.room = await api.call('room', { box });
    } catch {
      this.room = null;
    }
    this.syncDepthRange();
  }

  /** Grows the box n voxels out on a view's side (and the opposite one, the box staying centred; the
   * top grows upwards), keeping the drawing in place, so a surface drawn below 0 lands on its new
   * edge. Resolves true once grown. */
  async growFor(view, n) {
    n = Math.min(n, this.outward(view));
    if (n <= 0) { this.toast('The vehicle is at the edge of the game\'s 96 px sprite there.', true, 2200); return false; }
    const b = { ...this.project.box };
    if (view === 'top') b.height += n;
    else if (view === 'front' || view === 'back') b.length += 2 * n;
    else b.width += 2 * n;
    const grew = await this.resizeBox(b, 'keep', true);
    if (!grew) return false;
    this.brushDepth = 0;
    this.syncDepthRange();
    this.toast(`The box grew <b>${n}</b> out on the ${view === 'top' ? 'top' : view === 'side' || view === 'left' ? 'sides' : 'front and back'}: drawing at its new edge · Ctrl+Z to undo`, false, 2400);
    return true;
  }

  hoverAt(source, p) {
    for (const v of Object.values(this.views)) {
      v.link = p && v !== source ? v.linkFor(p) : null;
      if (v !== source) v.paintOverlay();
    }
    if (!p) { $('#coords').textContent = '—'; return; }
    const b = this.project.box;
    const fmt = (label, val, off) => (val == null ? null : `${label} ${val + off}`);
    $('#coords').textContent = [
      fmt('f', p.i, -Math.floor(b.length / 2)), fmt('r', p.j, -Math.floor(b.width / 2)), fmt('z', p.k, 0),
    ].filter(Boolean).join(' · ');
  }

  // -- history -------------------------------------------------------------------------------------

  beginStroke() {
    this.undoStack.push(this.project.snapshot());
    if (this.undoStack.length > 120) this.undoStack.shift();
    this.redoStack = [];
  }

  changed(final) {
    // A layer from the generator keeps its model: what's drawn is applied over it.
    this.project.version++;
    this.swings.clear();
    this.drives.clear();
    this.setDirty(true);
    this.activeView?.paint();
    // A blank side shows the other mirrored, so drawing on one repaints both; every view shows a
    // voxel layer's voxels.
    if (this.mirror || this.activeView?.view === 'side' || this.activeView?.view === 'left' || this.project.layer.voxels) {
      this.repaintViews();
    }
    this.schedulePreview(final ? 0 : 140);
  }

  undo() {
    const snap = this.undoStack.pop();
    if (!snap) return;
    this.redoStack.push(this.project.snapshot());
    this.restore(snap);
    this.afterHistory();
  }

  redo() {
    const snap = this.redoStack.pop();
    if (!snap) return;
    this.undoStack.push(this.project.snapshot());
    this.restore(snap);
    this.afterHistory();
  }

  /** A snapshot back in place; one from before a resize brings back the project it was taken of. */
  restore(snap) {
    if (snap.project && snap.project !== this.project) this.project = snap.project;
    this.project.restore(snap);
  }

  afterHistory() {
    this.swings.clear();
    this.drives.clear();
    // Undoing a resize brings the old box back.
    const b = this.project.box;
    if (+$('#boxL').value !== b.length || +$('#boxW').value !== b.width || +$('#boxH').value !== b.height) {
      $('#boxL').value = b.length;
      $('#boxW').value = b.width;
      $('#boxH').value = b.height;
      this.layoutBoard();
    }
    this.setDirty(true);
    this.renderLayers();
    this.repaintViews();
    this.schedulePreview(0);
  }

  // -- preview -------------------------------------------------------------------------------------

  setupPreview() {
    this.previewer = new VehiclePreview($('#previewStage'), $('#previewCanvas'), $$('.thumb'), $('#previewDir'),
      $('#partLabel'), $('#openChip'));
    this.previewer.onPlayChange = (on) => {
      const i = $('#playToggle i');
      i.dataset.icon = on ? 'pause' : 'play';
      mountIcons($('#playToggle'));
    };
    this.previewer.onHoverLayer = (n) => {
      for (const row of $$('.layer')) row.classList.toggle('peek', +row.dataset.index === n);
      // Fetch a hinged part's swing while the cursor rests on it, so the click plays at once.
      clearTimeout(this.prefetch);
      if (n >= 0 && this.project?.layers[n]?.hinged) this.prefetch = setTimeout(() => this.swingFrames(n, this.previewer.dir), 200);
    };
    this.previewer.requestSwing = (layer, dir) => this.swingFrames(layer, dir);
    // Once the drawing rests, fetch it driving; whenever frames arrive for what's drawn now, use them.
    this.previewer.requestDrive = (dir) => {
      clearTimeout(this.driveTimer);
      this.driveTimer = setTimeout(() => {
        const key = this.driveKey(dir);
        this.driveFrames(dir)?.then((r) => {
          if (r?.frames?.length && this.driveKey(dir) === key) this.previewer.setDrive(dir, r, this.driveDelay);
        });
      }, 350);
    };
    $('#playToggle').addEventListener('click', () => this.previewer.setDriving(!this.previewer.driving));
    $('#frameToggle').addEventListener('click', () => {
      this.previewer.frame = !this.previewer.frame;
      $('#frameToggle').classList.toggle('on', this.previewer.frame);
      this.previewer.draw();
    });
    $('#damageToggle').addEventListener('click', () => this.toggleDamage());
    $('#paintToggle').addEventListener('click', () => this.togglePreviewPaint());
    this.previewer.onPaint = (layer, key) => this.paintVoxel(layer, key);
    for (const b of $$('#bgSwatches button')) {
      b.addEventListener('click', () => {
        for (const x of $$('#bgSwatches button')) x.classList.toggle('on', x === b);
        this.previewer.setGround(b.dataset.bg);
      });
    }
  }

  /** Painting on the preview: a click gives the voxel under it the current material. It parks the
   * vehicle, so what's under the cursor holds still. */
  togglePreviewPaint() {
    const on = !this.previewer.painting;
    this.previewer.painting = on;
    $('#paintToggle').classList.toggle('on', on);
    $('#previewStage').classList.toggle('painting', on);
    if (on) this.previewer.setDriving(false);
    this.toast(on ? `Paint on the preview: click a voxel to make it <b>${this.escape(this.materials[this.material].replace(/_/g, ' '))}</b>`
      : 'Paint on the preview <b>off</b>', false, 1800);
  }

  /** The voxel a click on the preview landed on (the layer showing it, its f, r, z) given the current
   * material, and its mirror image with mirror on; a layer showing a damage level paints that level. */
  paintVoxel(n, key) {
    const p = this.project;
    let target = p?.layers[n];
    if (!target) return;
    const shown = this.stageShow()[target.name];
    if (shown) target = p.layers.find((l) => l.name === shown) || target;
    if (!target.voxels) {
      this.toast(`<b>${this.escape(target.name)}</b> is carved from its views. Set its Model to <b>Voxels</b> to paint it here.`, false, 3000);
      return;
    }
    if (!key) return;
    if (target.locked) { this.toast(`<b>${this.escape(target.name)}</b> is locked.`, true, 1600); return; }
    const { length: L, width: W, height: H } = p.box;
    const at = (f, r, z) => {
      const i = f + Math.floor(L / 2);
      const j = r + Math.floor(W / 2);
      return i < 0 || j < 0 || z < 0 || i >= L || j >= W || z >= H ? -1 : (i * W + j) * H + z;
    };
    const [f, r, z] = key;
    const cells = [at(f, r, z), ...(this.mirror ? [at(f, -r - 1, z)] : [])]
      .filter((c) => c >= 0 && target.voxels[c] !== EMPTY && target.voxels[c] !== this.material);
    if (!cells.length) return;
    this.beginStroke();
    const vox = target.writable();
    for (const c of cells) vox[c] = this.material;
    this.changed(true);
  }

  toggleDamage() {
    this.showDamage = !this.showDamage;
    $('#damageToggle').classList.toggle('on', this.showDamage);
    this.swings.clear();
    this.drives.clear();
    this.schedulePreview(0);
    const any = this.project?.layers.some((l) => l.stage);
    this.toast(this.showDamage ? (any ? 'Showing each part\'s <b>last damage state</b> at its top level' : 'No damage states yet') : 'Showing the parts <b>intact</b>', false, 1400);
  }

  /** The damage level each layer shows in the preview: the one being drawn, and with damage shown,
   * every other layer's last state at its top level. */
  stageShow() {
    const p = this.project;
    const show = {};
    if (!p) return show;
    if (this.showDamage) {
      for (const l of p.layers) {
        const states = l.stage ? [] : p.statesOf(l);
        if (!states.length) continue;
        const last = states[states.length - 1].levels;
        show[l.name] = last[last.length - 1].layer.name;
      }
    }
    const parent = p.parentOf(p.layer);
    if (parent) show[parent.name] = p.layer.name;
    return show;
  }

  paintParams() {
    const p = this.paint;
    return p.body || p.interior || p.seats || p.dirt ? p : null;
  }

  schedulePreview(delay) {
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => this.preview(), delay);
  }

  async preview() {
    if (!this.project) return;
    const seq = ++this.previewSeq;
    $('#previewBusy').classList.add('on');
    this.setBackend('busy', 'Rendering…');
    try {
      const show = this.stageShow();
      const result = await api.call('preview', {
        project: this.project.toBackend(show), box: this.project.box, materials: this.materials, paint: this.paintParams(),
      });
      if (result?.stale || seq !== this.previewSeq) return;
      const layers = this.project.layers.map((l) => {
        const drawn = show[l.name] ? this.project.layers.find((x) => x.name === show[l.name]) || l : l;
        return { name: show[l.name] ? `${l.name} · ${show[l.name]}` : l.name, hinged: l.hinged,
          wheel: l.kind === 'wheel' && !l.stage, voxels: !!drawn.voxels };
      });
      await this.previewer.setRender(result, layers);
      const shown = Object.entries(show);
      $('#damageChip').textContent = shown.length === 1 ? `${shown[0][1]}` : `${shown.length} damaged parts`;
      $('#damageChip').classList.toggle('on', shown.length > 0);
      $('#voxelCount').textContent = `${result.voxels.toLocaleString()} voxels`;
      $('#renderMs').textContent = `${result.ms.carve + result.ms.render} ms`;
      this.setBackend('ready', 'Python ready');
      if (!this.readySent) { this.readySent = true; api.ready(); }
    } catch (err) {
      if (seq !== this.previewSeq) return;
      this.setBackend('error', 'Render failed');
      this.toast(`<b>Render</b> ${this.escape(this.short(err.message))}`, true, 5000);
    } finally {
      if (seq === this.previewSeq) $('#previewBusy').classList.remove('on');
    }
  }

  swingFrames(layer, dir) {
    const key = `${this.project.version}:${layer}:${dir}`;
    if (!this.swings.has(key)) {
      this.swings.set(key, api.call('swing', {
        project: this.project.toBackend(this.stageShow()), box: this.project.box, layer, view: dir, materials: this.materials,
        paint: this.paintParams(),
      }).then((r) => r.frames).catch((err) => {
        this.swings.delete(key);
        this.toast(`<b>Swing</b> ${this.escape(this.short(err.message))}`, true);
        return [];
      }));
    }
    return this.swings.get(key);
  }

  driveKey(dir) {
    return `${this.project?.version}:${dir}:${JSON.stringify(this.paintParams())}:${JSON.stringify(this.stageShow())}`;
  }

  /** The vehicle driving one way, its wheels turning on the game's frames; fetched once a version. */
  driveFrames(dir) {
    if (!this.project) return null;
    const key = this.driveKey(dir);
    if (!this.drives.has(key)) {
      this.drives.set(key, api.call('drive', {
        project: this.project.toBackend(this.stageShow()), box: this.project.box, view: dir, materials: this.materials,
        paint: this.paintParams(),
      }).then((r) => {
        // Overtaken by a newer request before it was drawn: fetch it again when asked.
        if (r?.stale) { this.drives.delete(key); return null; }
        this.driveDelay = r.delay;
        return r;
      }).catch(() => { this.drives.delete(key); return null; }));
    }
    return this.drives.get(key);
  }

  // -- files and actions ------------------------------------------------------------------------------

  async action(name) {
    switch (name) {
      case 'new': return this.newProject();
      case 'open': return this.openDialog();
      case 'save': return this.save(false);
      case 'export': return this.exportSprites();
      case 'undo': return this.undo();
      case 'redo': return this.redo();
      case 'resize': return this.resizeBox();
      case 'save-as': return this.save(true);
      case 'download': return this.downloadProject();
      default: return null;
    }
  }

  confirmDiscard(key) {
    if (!this.dirty) return true;
    const now = Date.now();
    if (this.pendingDiscard?.key === key && now - this.pendingDiscard.at < 2500) return true;
    this.pendingDiscard = { key, at: now };
    this.toast('Unsaved changes. Do it again to discard them.', true, 2400);
    return false;
  }

  setProject(project, file = null) {
    this.project = project;
    this.undoStack = [];
    this.redoStack = [];
    this.swings.clear();
    this.drives.clear();
    this.setFile(file);
    this.setDirty(false);
    this.fourDir = false;
    const b = project.box;
    $('#boxL').value = b.length;
    $('#boxW').value = b.width;
    $('#boxH').value = b.height;
    this.layoutBoard();
    this.renderLayers();
    this.renderGame();
    this.renderRefs();
    this.refreshRoom();
    this.renderDriving();
    this.renderCamera();
    this.focusView(this.spriteOnly() ? this.views.top : this.views.side);
    this.schedulePreview(0);
  }

  async buildNewMenu() {
    const menu = $('#newMenu');
    const add = (label, about, fn) => {
      const b = document.createElement('button');
      b.innerHTML = `<span>${label}</span>${about ? `<small>${this.escape(about)}</small>` : ''}`;
      b.addEventListener('click', () => { menu.classList.remove('on'); fn(); });
      menu.append(b);
    };
    add('Blank', 'A body to draw from scratch.', () => this.newProject());
    if (api.importDialog) add('Import a sprite…', 'An Aseprite file or PNGs, drawn from above: one picture, turned to each direction.', () => this.importSprite());
    menu.insertAdjacentHTML('beforeend', '<div class="menu-sep"></div><div class="menu-label">From a template</div>');
    const title = (n) => n[0].toUpperCase() + n.slice(1).replace(/-/g, ' ');
    try {
      const { templates } = await api.call('templates');
      for (const t of templates.filter((x) => !x.example)) {
        add(title(t.name), `${t.description} · ${t.box.length}×${t.box.width}×${t.box.height}`, () => this.newFromTemplate(t.name));
      }
      const examples = templates.filter((x) => x.example);
      if (examples.length) menu.insertAdjacentHTML('beforeend', '<div class="menu-sep"></div><div class="menu-label">Finished vehicles</div>');
      for (const t of examples) {
        add(title(t.name), `${t.description} · ${t.box.length}×${t.box.width}×${t.box.height}`, () => this.newFromTemplate(t.name));
      }
    } catch (err) {
      menu.insertAdjacentHTML('beforeend', `<div class="menu-label">${this.escape(this.short(err.message))}</div>`);
    }
    // The generator's own vehicles, its model kept with what's drawn applied over it.
    if (this.info?.variants?.length) {
      menu.insertAdjacentHTML('beforeend', '<div class="menu-sep"></div><div class="menu-label">The jeep, from its generator</div>');
      for (const v of this.info.variants) add(`${title(v)} jeep`, null, () => this.loadVehicle(v));
    }
  }

  /** A new vehicle from a template: every part laid out and named, seats placed, ready to export. */
  async newFromTemplate(name) {
    if (!this.confirmDiscard('new')) return;
    try {
      const data = await api.call('template', { name });
      if (data.customMaterials?.length) await this.addMaterials(data.customMaterials);
      const p = Project.fromBackend(data, this.materials);
      p.vehicle = data.vehicle || null;
      p.customMaterials = data.customMaterials || [];
      this.setProject(p);
      this.toast(`New <b>${this.escape(name)}</b> from its template: every part's a voxel layer to reshape · slice a view to work inside`, false, 3600);
    } catch (err) {
      this.toast(`<b>Template</b> ${this.escape(this.short(err.message))}`, true, 5000);
    }
  }

  /** A new project from a finished sprite (backend/sprite.py): its layers' pictures drawn as they are,
   * turned to point the way each direction faces. */
  async importSprite() {
    if (!this.confirmDiscard('new')) return;
    const picked = await api.importDialog();
    if (!picked) return;
    try {
      const data = await api.call('import_sprite', picked);
      const p = Project.fromBackend(data, this.materials);
      p.vehicle = data.vehicle || null;
      p.sprite = data.sprite;
      p.refs = data.refs || {};
      this.setProject(p);
      const parts = p.layers.map((l) => l.name).join(', ');
      this.toast(`Imported <b>${this.escape(data.vehicle.name)}</b>: ${this.escape(parts)}. Drag the seats onto the picture.`
        + (data.warnings?.length ? `<br>${data.warnings.map((w) => this.escape(w)).join('<br>')}` : ''), !!data.warnings?.length, 6000);
    } catch (err) {
      this.toast(`<b>Import</b> ${this.escape(this.short(err.message))}`, true, 6000);
    }
  }

  /** The Wheels layer built from the axles, radius, track and tyre width given. */
  async buildWheels() {
    const p = this.project;
    const axles = $('#wheelAxles').value.split(/[,\s]+/).filter(Boolean).map(Number).filter((n) => Number.isFinite(n));
    if (!axles.length) { this.toast('Say where the axles go: their f, comma separated (e.g. -20, 20).', true, 2600); return; }
    try {
      const r = await api.call('wheels', { box: p.box, axles, radius: +$('#wheelRadius').value, track: +$('#wheelTrack').value,
        width: +$('#wheelWidth').value, materials: this.materials });
      const fresh = Project.fromBackend({ box: p.box, layers: [r.layer] }, this.materials).layers[0];
      this.beginStroke();
      const at = p.layers.findIndex((l) => l.kind === 'wheel' && !l.stage);
      if (at >= 0) {
        fresh.name = p.layers[at].name;
        p.layers[at] = fresh;
        p.active = at;
      } else {
        p.layers.push(fresh);
        p.active = p.layers.length - 1;
      }
      this.structureChanged();
      this.toast(`Built <b>${axles.length * 2}</b> wheels · Ctrl+Z to undo`, false, 1800);
    } catch (err) {
      this.toast(`<b>Wheels</b> ${this.escape(this.short(err.message))}`, true, 5000);
    }
  }

  newProject() {
    if (!this.confirmDiscard('new')) return;
    this.setProject(Project.blank(this.project?.box || DEFAULT_BOX, this.materials));
    this.toast('New vehicle. Start with the <b>Body</b>: its side, front and top.', false, 2600);
  }

  async loadVehicle(variant, quiet = false) {
    if (!quiet && !this.confirmDiscard(`load-${variant}`)) return;
    this.setBackend('busy', `Loading the ${variant}…`);
    try {
      const data = await api.call('load_vehicle', { variant, box: this.project?.box || DEFAULT_BOX });
      this.setProject(Project.fromBackend(data, this.materials));
      this.setFile(null);
      $('#docName').textContent = `${variant} (from the generator)`;
      if (!quiet) this.toast(`Loaded the <b>${variant}</b> as ${data.layers.length} layers.`);
    } catch (err) {
      this.setBackend('error', 'Load failed');
      this.toast(`<b>Load</b> ${this.escape(this.short(err.message))}`, true, 5000);
    }
  }

  async resizeBox(size = null, keepMode = null, quiet = false) {
    const box = size || { length: +$('#boxL').value, width: +$('#boxW').value, height: +$('#boxH').value };
    if (Object.values(box).some((v) => !(v >= 8 && v <= 160))) { this.toast('Box sizes go from 8 to 160.', true); return false; }
    const mode = keepMode || $('#resizeSeg button.on')?.dataset.value || 'keep';
    try {
      const r = await api.call('reshape', {
        project: this.project.toBackend(), box: this.project.box, size: [box.length, box.width, box.height], mode,
        materials: this.materials,
      });
      this.beginStroke();
      const { active, vehicle } = this.project;
      this.project = Project.fromBackend(r, this.materials);
      this.project.active = Math.min(active, this.project.layers.length - 1);
      this.project.vehicle = vehicle;
      $('#boxL').value = box.length;
      $('#boxW').value = box.width;
      $('#boxH').value = box.height;
      this.layoutBoard();
      this.structureChanged();
      this.refreshRoom();
      if (!quiet) this.toast(`${mode === 'stretch' ? 'Stretched' : 'Box is now'} <b>${box.length}×${box.width}×${box.height}</b>.`, false, 1800);
      return true;
    } catch (err) {
      this.toast(`<b>Resize</b> ${this.escape(this.short(err.message))}`, true, 5000);
      return false;
    }
  }

  async openDialog() {
    if (!this.confirmDiscard('open')) return;
    const r = await api.dialog.open({ filters: [{ name: 'Open vehicle', extensions: ['cmuov'] }], properties: ['openFile'] });
    if (r.canceled || !r.filePaths[0]) return;
    try {
      await this.openFile(r.filePaths[0]);
    } catch (err) {
      this.toast(`<b>Open</b> ${this.escape(this.short(err.message))}`, true, 5000);
    }
  }

  /** A project's own materials on the palette, added after the generator's so no index moves. */
  async addMaterials(list) {
    if (!list?.length || list.every((m) => this.materials.includes(m.name))) return;
    const r = await api.call('add_materials', { materials: list });
    this.palette = r.materials;
    this.materials = this.palette.map((m) => m.name);
    this.toneCache = {};
    if (this.project) this.project.materials = this.materials;
    this.buildSwatches();
  }

  async newMaterial() {
    const name = $('#newMatName').value.trim().toLowerCase().replace(/\W+/g, '_');
    if (!name) { this.toast('Name the material first.', true, 1600); return; }
    if (this.materials.includes(name)) { this.toast(`<b>${this.escape(name)}</b> is taken.`, true, 1600); return; }
    const spec = { name, color: $('#newMatColor').value };
    this.project.customMaterials.push(spec);
    await this.addMaterials([spec]);
    this.setMaterial(this.materials.indexOf(name));
    $('#newMatName').value = '';
    this.setDirty(true);
    this.toast(`Added <b>${this.escape(name)}</b> to this vehicle's materials.`, false, 1800);
  }

  async openFile(path, keep = false) {
    const text = await api.fs.read(path);
    const own = JSON.parse(text).customMaterials;
    if (own?.length) await this.addMaterials(own);
    const { project, extra } = Project.fromFile(text, this.materials);
    await this.refreshNative(project, extra.version);
    if (keep && this.project) project.active = Math.min(this.project.active, project.layers.length - 1);
    // A file from the computer is kept in the browser from then on, under its own name.
    const stored = path.startsWith('store:') ? path : await api.untitledPath(api.displayName(path));
    this.setProject(project, stored);
    if (stored !== path) this.setDirty(true);
    api.settings.set({ lastFile: stored });
  }

  /** A project from before per direction drawings has its layers loaded from the generator brought
   * up to them, so each view shows what the game shows from that side (the left, the driver's);
   * what was drawn on them stays. */
  async refreshNative(project, version) {
    if ((version || 1) >= 2 || !project.layers.some((l) => l.native)) return;
    try {
      const r = await api.call('refresh_native', { project: project.toBackend(), box: project.box, materials: this.materials });
      for (const [n, views] of Object.entries(r?.views || {})) project.setViews(+n, views);
    } catch (err) {
      this.toast(`<b>Open</b> ${this.escape(this.short(err.message))}`, true, 4000);
    }
  }

  /** The open file changed on disk, as when a script or cmuov.py draws on it: reload it unless it
   * has changes of its own here. */
  async fileChanged(path) {
    if (path !== this.file || Date.now() - this.ownWrite < 1500) return;
    if (this.dirty) {
      this.toast(`<b>${this.escape(path.split(/[\\/]/).pop())}</b> changed on disk. Saving here will replace it.`, true, 4000);
      return;
    }
    try {
      await this.openFile(path, true);
      this.toast(`Reloaded <b>${this.escape(path.split(/[\\/]/).pop())}</b>: changed outside the app.`, false, 1800);
    } catch (err) {
      this.toast(`<b>Reload</b> ${this.escape(this.short(err.message))}`, true, 4000);
    }
  }

  async save(as) {
    let path = this.file;
    if (as || !path) {
      const r = await api.dialog.save({ defaultPath: 'vehicle.cmuov', filters: [{ name: 'Open vehicle', extensions: ['cmuov'] }] });
      if (r.canceled || !r.filePath) return;
      path = r.filePath;
    }
    this.ownWrite = Date.now();
    await api.fs.write(path, this.project.toFile());
    this.ownWrite = Date.now();
    this.setFile(path);
    this.setDirty(false);
    api.settings.set({ lastFile: path });
    api.fs.watch(path);
    this.toast(`Saved <b>${this.escape(path.split(/[\\/]/).pop())}</b>`, false, 1600);
  }

  async exportSprites() {
    const r = await api.dialog.save({ defaultPath: 'vehicle.png', filters: [{ name: 'PNG', extensions: ['png'] }] });
    if (r.canceled || !r.filePath) return;
    const base = r.filePath.replace(/\.png$/i, '');
    // The four directions as an RSI does them: a 2×2 sheet of 96 px frames, S N / E W.
    const sheet = document.createElement('canvas');
    sheet.width = sheet.height = 192;
    const ctx = sheet.getContext('2d');
    const order = ['S', 'N', 'E', 'W'];
    order.forEach((d, n) => {
      const img = this.previewer.images[d];
      if (img) ctx.drawImage(img, 16, 16, 96, 96, (n % 2) * 96, Math.floor(n / 2) * 96, 96, 96);
    });
    await api.fs.writeBase64(`${base}.png`, sheet.toDataURL('image/png').split(',')[1]);
    await api.call('export_voxels', { project: this.project.toBackend(), box: this.project.box, path: `${base}.voxels.json`, materials: this.materials });
    this.toast(`Exported <b>${this.escape(base.split(/[\\/]/).pop())}.png</b> and its voxels.`);
  }

  // -- the vehicle in the game -------------------------------------------------------------------

  // -- driving: stats and sounds ------------------------------------------------------------------

  renderDriving() {
    const p = this.project;
    if (!p) return;
    const v = p.vehicle || {};
    const stats = $('#stats');
    stats.innerHTML = '';
    for (const [key, label, def] of STATS) {
      const row = document.createElement('label');
      row.className = 'stat';
      row.innerHTML = `<span>${label}</span><input type="number" min="0" placeholder="${def}" value="${v.stats?.[key] ?? ''}">`;
      row.querySelector('input').addEventListener('input', (e) => {
        const veh = this.vehicle();
        veh.stats ||= {};
        if (e.target.value === '') delete veh.stats[key];
        else veh.stats[key] = +e.target.value;
        this.setDirty(true);
      });
      stats.append(row);
    }
    const sounds = $('#sounds');
    sounds.innerHTML = '';
    for (const [key, label, def] of SOUNDS) {
      const row = document.createElement('div');
      row.className = 'sound';
      row.innerHTML = `<span>${label}</span><input list="soundList" spellcheck="false" placeholder="${def.split('/').pop()}" value="${this.escape(v.sounds?.[key] || '')}">`
        + '<button class="play" data-tip="Play it"><i data-icon="play"></i></button>';
      const input = row.querySelector('input');
      input.addEventListener('change', () => {
        const veh = this.vehicle();
        veh.sounds ||= {};
        if (input.value.trim()) veh.sounds[key] = input.value.trim();
        else delete veh.sounds[key];
        this.setDirty(true);
      });
      row.querySelector('.play').addEventListener('click', (e) => this.playSound(input.value.trim() || def, e.currentTarget));
      sounds.append(row);
    }
    mountIcons(sounds);
    this.loadSoundList();
  }

  /** Every sound in the game repo, offered as you type one's path. */
  async loadSoundList() {
    if (this.soundFiles) return;
    try {
      const r = await api.call('list_sounds', {});
      this.soundFiles = r.files;
      const list = $('#soundList');
      list.innerHTML = '';
      for (const f of r.files) list.append(new Option(f.path));
    } catch { /* offered nothing, typed paths still work */ }
  }

  async playSound(resPath, btn) {
    const file = this.soundFiles?.find((f) => f.path === resPath)?.file;
    if (!file) { this.toast(`<b>Sound</b> ${this.escape(resPath)} isn't one the tool has`, true, 2400); return; }
    try {
      this.audio?.pause();
      $$('.sound .play.on').forEach((b) => b.classList.remove('on'));
      this.audio = new Audio(await api.fs.readDataUrl(file));
      btn.classList.add('on');
      this.audio.addEventListener('ended', () => btn.classList.remove('on'));
      await this.audio.play();
    } catch (err) {
      btn.classList.remove('on');
      this.toast(`<b>Sound</b> ${this.escape(this.short(err.message))}`, true, 3000);
    }
  }

  // -- pictures to trace ------------------------------------------------------------------------

  renderRefs() {
    const p = this.project;
    if (!p) return;
    const box = $('#refs');
    box.innerHTML = '';
    for (const view of VIEWS) {
      const ref = p.refs[view];
      const row = document.createElement('div');
      row.className = 'ref-row';
      row.innerHTML = `<span>${view[0].toUpperCase()}${view.slice(1)}</span>`
        + `<button class="pick" data-tip="Pick a picture">${ref ? this.escape(ref.path.split(/[\\/]/).pop()) : 'Pick a picture…'}</button>`
        + `<input type="range" min="5" max="100" value="${Math.round((ref?.opacity ?? 0.4) * 100)}"${ref ? '' : ' disabled'}>`
        + `<button class="del" aria-label="Remove"${ref ? '' : ' hidden'}><i data-icon="close"></i></button>`;
      row.querySelector('.pick').addEventListener('click', async () => {
        const r = await api.dialog.open({ filters: [{ name: 'Pictures', extensions: ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'] }], properties: ['openFile'] });
        if (r.canceled || !r.filePaths?.[0]) return;
        p.refs[view] = { path: api.displayName(r.filePaths[0]), data: await api.fs.readDataUrl(r.filePaths[0]),
          opacity: p.refs[view]?.opacity ?? 0.4 };
        this.setDirty(true);
        this.renderRefs();
      });
      row.querySelector('input').addEventListener('input', (e) => {
        p.refs[view].opacity = +e.target.value / 100;
        this.views[view].stage.style.setProperty('--ref-opacity', p.refs[view].opacity);
        this.setDirty(true);
      });
      row.querySelector('.del').addEventListener('click', () => {
        delete p.refs[view];
        this.setDirty(true);
        this.renderRefs();
      });
      box.append(row);
      this.showRef(view);
    }
    mountIcons(box);
  }

  /** A view's picture under its drawing, loaded when it changes. */
  async showRef(view) {
    const vc = this.views[view];
    const ref = this.project.refs[view];
    vc.stage.style.setProperty('--ref-opacity', ref?.opacity ?? 0.4);
    if (!ref) { vc.ref.classList.remove('on'); vc.refPath = null; return; }
    const key = ref.data ? ref.data.length + ref.path : ref.path;
    if (vc.refPath === key) { vc.ref.classList.add('on'); return; }
    vc.refPath = key;
    try {
      vc.ref.src = ref.data || await api.fs.readDataUrl(ref.path);
      vc.ref.classList.add('on');
    } catch {
      vc.ref.classList.remove('on');
      this.toast(`<b>Trace</b> can't read ${this.escape(ref.path)}`, true, 3000);
    }
  }

  // -- the camera: how each sprite direction sees the vehicle ---------------------------------------------

  setupCamera() {
    this.segmented($('#cameraMode'), (mode) => this.setCamera({ ...(this.vehicle().camera || {}), mode }));
    this.segmented($('#cameraView'), (view) => this.setCamera({ ...(this.vehicle().camera || {}), mode: 'single', view }));
    $('#cameraReset').addEventListener('click', () => this.setCamera(null));
    $('#cameraFourDir').addEventListener('click', () => {
      this.fourDir = !this.fourDir;
      this.layoutBoard();
      this.renderCamera();
    });
  }

  /** A new camera for the vehicle (null for the game's own views): every drawing is made again. */
  setCamera(camera) {
    const v = this.vehicle();
    if (!camera || camera.mode === 'generator') delete v.camera;
    else v.camera = camera;
    this.structureChanged();
    this.renderCamera();
  }

  async renderCamera() {
    if (!this.project) return;
    const cam = this.project.vehicle?.camera || { mode: 'generator' };
    const mode = cam.mode || 'generator';
    // An imported sprite keeps its own camera: straight down on its picture.
    const sprite = mode === 'sprite';
    $('#cameraMode').hidden = sprite;
    $('#cameraReset').hidden = sprite;
    $('#cameraSprite').hidden = !sprite;
    if (sprite) {
      const names = { N: 'up', E: 'right', S: 'down', W: 'left' };
      $('#cameraNose').textContent = `Seen from above, its front pointing ${names[cam.nose] || 'up'} in the picture.`;
      $('#cameraFourDir').classList.toggle('on', !!this.fourDir);
    }
    $('#cameraMode').setValue?.(mode);
    $('#cameraSingle').hidden = mode !== 'single';
    $('#cameraView').setValue?.(cam.view || 'S');
    const dirs = $('#cameraDirs');
    dirs.hidden = mode !== 'custom';
    $('#cameraNote').textContent = {
      generator: 'Each direction drawn as the jeep is: the generator\'s own views.',
      custom: 'Pitch is how far above the horizon the camera looks down; yaw turns the vehicle on top of facing that way; zoom scales it; stretch draws it wider than tall.',
      single: 'One picture, turned a quarter at a time to point the way each direction faces. Crayons go on what that view shows.',
      sprite: 'Its picture as drawn, turned to point the way each direction faces. The 4 dir workspace shows the blocks it is pressed into, to reshape them.',
    }[mode];
    if (mode !== 'custom') return;
    this.cameraDefaults ||= (await api.call('camera_defaults').catch(() => ({ dirs: {} }))).dirs;
    dirs.innerHTML = '<span></span><span>Pitch</span><span>Yaw</span><span>Zoom</span><span>Stretch</span>';
    const names = { S: 'Front', N: 'Back', E: 'Right', W: 'Left' };
    const fields = [['pitch', 0, 90, 1], ['yaw', -180, 180, 1], ['zoom', 0.25, 4, 0.05], ['stretch', 0.25, 3, 0.05]];
    for (const d of ['S', 'N', 'E', 'W']) {
      dirs.insertAdjacentHTML('beforeend', `<span class="cam-dir">${names[d]}</span>`);
      for (const [key, min, max, step] of fields) {
        const input = document.createElement('input');
        input.type = 'number';
        input.min = min;
        input.max = max;
        input.step = step;
        input.value = cam.dirs?.[d]?.[key] ?? this.cameraDefaults?.[d]?.[key] ?? '';
        input.addEventListener('change', () => {
          const next = { ...cam, mode: 'custom', dirs: { ...(cam.dirs || {}) } };
          next.dirs[d] = { ...(this.cameraDefaults?.[d] || {}), ...(next.dirs[d] || {}), [key]: Math.min(max, Math.max(min, +input.value || 0)) };
          this.setCamera(next);
        });
        dirs.append(input);
      }
    }
  }

  /** The vehicle's game block, made when first needed. */
  vehicle() {
    const p = this.project;
    if (!p.vehicle) p.vehicle = { id: '', name: '', description: '', seats: [] };
    p.vehicle.seats ||= [];
    return p.vehicle;
  }

  setupGame() {
    this.placingSeat = null;
    const field = (id, key) => $(id).addEventListener('input', (e) => {
      this.vehicle()[key] = e.target.value;
      this.setDirty(true);
    });
    field('#vehId', 'id');
    field('#vehName', 'name');
    field('#vehDesc', 'description');
    $('#addSeat').addEventListener('click', () => {
      const v = this.vehicle();
      const taken = new Set(v.seats.map((s) => s.id));
      const id = ['driver', 'passenger'].find((n) => !taken.has(n)) || `seat_${v.seats.length + 1}`;
      v.seats.push({ id, f: 0, r: 0, z: Math.round(this.project.box.height / 3), ...(id === 'driver' ? { driver: true } : {}) });
      this.setDirty(true);
      this.placeSeat(v.seats.length - 1);
    });
    $('#gameBtn').addEventListener('click', () => this.writeGame());
    $('#newMatAdd').addEventListener('click', () => this.newMaterial());
    const newMenu = $('#newMenu');
    $('#newBtn').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!newMenu.childElementCount) await this.buildNewMenu();
      newMenu.classList.toggle('on');
    });
    document.addEventListener('click', (e) => { if (!newMenu.contains(e.target)) newMenu.classList.remove('on'); });
    $('#wheelsBtn').addEventListener('click', () => this.buildWheels());
    $('#newMatName').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.newMaterial(); });
    $('#drivingReset').addEventListener('click', () => {
      const v = this.vehicle();
      delete v.stats;
      delete v.sounds;
      this.setDirty(true);
      this.renderDriving();
    });
  }

  /** The file's name, as the vehicle's id and name until they're given. */
  fileSlug() {
    return this.file ? api.displayName(this.file) : 'vehicle';
  }

  renderGame() {
    const p = this.project;
    if (!p) return;
    const v = p.vehicle || {};
    const slug = this.fileSlug();
    $('#vehId').value = v.id || '';
    $('#vehId').placeholder = slug.replace(/(^|[^a-z0-9])([a-z])/gi, (_, a, b) => b.toUpperCase()).replace(/\W/g, '');
    $('#vehName').value = v.name || '';
    $('#vehName').placeholder = slug.toLowerCase();
    $('#vehDesc').value = v.description || '';
    const doors = p.layers.filter((l) => KINDS[l.kind]?.hinge && l.kind !== 'windshield' && !l.stage).map((l) => l.name);
    const list = $('#seats');
    list.innerHTML = '';
    (v.seats || []).forEach((s, n) => {
      const row = document.createElement('div');
      row.className = `seat${this.placingSeat === n ? ' placing' : ''}`;
      const opts = doors.map((d) => `<option${d === s.door ? ' selected' : ''}>${this.escape(d)}</option>`).join('');
      row.innerHTML = `<input class="sid" spellcheck="false" value="${this.escape(s.id)}">`
        + `<span class="where" data-tip="Click, then click where the rider sits">f ${s.f} · r ${s.r} · z ${s.z}</span>`
        + `<select class="door"><option value="">no door</option>${opts}</select>`
        + `<button class="drv${s.driver ? ' on' : ''}" data-tip="Drives">D</button>`
        + '<button class="del" aria-label="Remove"><i data-icon="close"></i></button>';
      row.querySelector('.sid').addEventListener('change', (e) => {
        s.id = e.target.value.trim().replace(/\W+/g, '_') || s.id;
        this.setDirty(true);
        this.renderGame();
      });
      row.querySelector('.where').addEventListener('click', () => this.placeSeat(this.placingSeat === n ? null : n));
      row.querySelector('.door').addEventListener('change', (e) => {
        if (e.target.value) s.door = e.target.value;
        else delete s.door;
        this.setDirty(true);
      });
      row.querySelector('.drv').addEventListener('click', () => {
        for (const o of v.seats) delete o.driver;
        s.driver = true;
        this.setDirty(true);
        this.renderGame();
      });
      row.querySelector('.del').addEventListener('click', () => {
        v.seats.splice(n, 1);
        this.placingSeat = null;
        this.setDirty(true);
        this.renderGame();
      });
      list.append(row);
    });
    mountIcons(list);
    this.scheduleChecks();
    for (const view of Object.values(this.views)) view.paintOverlay();
  }

  /** The pre-export check, run when the vehicle changes while its card shows: what would stop it
   * going into the game in red, what's worth knowing in amber. */
  async runChecks() {
    const p = this.project;
    if (!p || this.tab !== 'vehicle') return;
    const v = { ...(p.vehicle || {}) };
    v.name ||= $('#vehName').placeholder;
    v.id ||= $('#vehId').placeholder;
    try {
      const r = await api.call('check', { project: { ...p.toBackend(), vehicle: v }, box: p.box, materials: this.materials });
      const box = $('#checks');
      box.innerHTML = r.errors.map((e) => `<div class="err">${this.escape(e)}</div>`).join('')
        + r.warnings.map((w) => `<div class="wrn">${this.escape(w)}</div>`).join('')
        || '<div class="ok">Ready for the game.</div>';
      $('#gameBtn').disabled = r.errors.length > 0;
    } catch { /* the check is a help, not a gate */ }
  }

  scheduleChecks() {
    clearTimeout(this.checkTimer);
    this.checkTimer = setTimeout(() => this.runChecks(), 600);
  }

  /** Arms a seat to be placed by the next click on a view (null disarms). */
  placeSeat(n) {
    this.placingSeat = n;
    if (n != null && this.spriteOnly()) this.toast('Click where the rider sits on the picture · Esc to stop', false, 3000);
    else if (n != null) this.toast('Click where the rider sits on the <b>Top</b> view, then the <b>Side</b> or <b>Left</b> view for their height · Esc to stop', false, 3000);
    this.renderGame();
  }

  /** Where a click on a view puts the seat being placed: the top sets f and r, a side view f and z,
   * the front and back r and z. Seats sit between voxels across and along, on one in height. */
  seatClicked(view, c) {
    const s = this.project.vehicle?.seats?.[this.placingSeat];
    if (!s) return false;
    const { length: L, width: W, height: H } = this.project.box;
    const f = (i) => i - Math.floor(L / 2) + 0.5;
    const r = (j) => j - Math.floor(W / 2) + 0.5;
    if (view === 'top') { s.f = f(c.x); s.r = r(c.y); }
    else if (view === 'side') { s.f = f(c.x); s.z = H - 1 - c.y; }
    else if (view === 'left') { s.f = f(L - 1 - c.x); s.z = H - 1 - c.y; }
    else if (view === 'front') { s.r = r(W - 1 - c.x); s.z = H - 1 - c.y; }
    else { s.r = r(c.x); s.z = H - 1 - c.y; }
    this.setDirty(true);
    // A sprite's riders sit on its picture: where they are on it is all there is to place.
    if (view !== 'top' || this.spriteOnly()) this.placingSeat = null;
    this.renderGame();
    return true;
  }

  async writeGame() {
    const btn = $('#gameBtn');
    if (btn.classList.contains('running')) return null;
    const p = this.project;
    const v = { ...(p.vehicle || {}) };
    v.name ||= $('#vehName').placeholder;
    v.id ||= $('#vehId').placeholder;
    if (this.file) v.source = `${api.displayName(this.file)}.cmuov`;
    if (!(v.seats || []).some((s) => s.driver)) { this.toast('Give it a seat that <b>drives</b> first.', true, 2600); return null; }
    btn.classList.add('running');
    const label = btn.querySelector('b');
    label.textContent = 'Rendering…';
    btn.querySelector('.fill').style.transform = 'scaleX(0.15)';
    $('#written').innerHTML = '';
    try {
      const r = await api.call('export_game', { project: { ...p.toBackend(), vehicle: v }, box: p.box, materials: this.materials });
      const out = $('#written');
      out.innerHTML = `<div class="ok">${this.escape(r.download)} · ${r.states} sprite states · ${r.seconds}s</div>`
        + '<p class="note">Unzip it at the root of the CMU repo; its INSTALL.md says the rest.</p>';
      for (const f of r.files) {
        const row = document.createElement('div');
        row.className = 'file';
        row.textContent = f;
        out.append(row);
      }
      this.lastWritten = { ...r, name: v.name };
      for (const w of r.warnings || []) out.insertAdjacentHTML('beforeend', `<div class="warn">${this.escape(w)}</div>`);
      this.toast(`Downloading <b>${this.escape(r.download)}</b>.`, false, 3000);
      return r;
    } catch (err) {
      $('#written').innerHTML = `<div class="bad">${this.escape(this.short(err.message))}</div>`;
      this.toast(`<b>For the game</b> ${this.escape(this.short(err.message))}`, true, 6000);
      return null;
    } finally {
      btn.classList.remove('running');
      btn.querySelector('.fill').style.transform = 'scaleX(0)';
      label.textContent = 'Download for the game';
    }
  }

  // -- backend events --------------------------------------------------------------------------------

  backendEvent({ event, data }) {
    if (event === 'game_progress') {
      const btn = $('#gameBtn');
      btn.querySelector('b').textContent = `${data.text}…`;
      btn.querySelector('.fill').style.transform = `scaleX(${data.text.startsWith('Writing') ? 0.85 : 0.4})`;
      return;
    }
    if (event === 'generator') {
      if (data.state === 'reloaded') {
        this.palette = data.materials;
        this.materials = this.palette.map((m) => m.name);
        this.toneCache = {};
        this.buildSwatches();
        // A reloaded generator has only its own materials: the project's go back on.
        this.addMaterials(this.project?.customMaterials).then(() => this.repaintViews());
        this.repaintViews();
        this.schedulePreview(0);
        this.toast(`<b>Generator reloaded</b> · ${data.seconds}s`, false, 1800);
      } else {
        this.toast(`<b>Generator error</b> ${this.escape(data.error)}`, true, 7000);
      }
    } else if (event === 'busy') {
      this.setBackend('error', 'Server full');
      this.toast(`<b>The server is full</b> (${data.max} people at once). Try again in a while.`, true, 15000);
    } else if (event === 'exit') {
      this.setBackend('error', 'Disconnected');
      this.toast('<b>Lost the connection to the server.</b> Your work is saved in this browser: reload the page.', true, 15000);
    }
  }

  // -- side tabs -------------------------------------------------------------------------------------

  setupTabs() {
    for (const b of $$('.tab')) b.addEventListener('click', () => this.setTab(b.dataset.tab));
    new ResizeObserver(() => this.placeTabIndicator()).observe($('#tabs'));
  }

  /** Shows a tab's panel, sliding it in from the side of the strip it comes from. */
  setTab(name, instant = false) {
    if (name === this.tab) return;
    if (name === 'vehicle') setTimeout(() => this.runChecks(), 0);
    const from = TABS.indexOf(this.tab);
    const to = TABS.indexOf(name);
    const down = from < 0 || to > from;
    for (const b of $$('.tab')) {
      b.classList.toggle('on', b.dataset.tab === name);
      b.setAttribute('aria-selected', String(b.dataset.tab === name));
    }
    for (const panel of $$('.tab-panel')) {
      const on = panel.dataset.panel === name;
      const was = panel.classList.contains('on');
      panel.style.setProperty('--enter', `${down ? 12 : -12}px`);
      panel.style.setProperty('--leave', `${down ? -10 : 10}px`);
      if (on && !was) {
        panel.classList.add('instant');
        panel.classList.remove('leaving');
        void panel.offsetWidth;
        panel.classList.toggle('instant', instant);
        panel.classList.add('on');
      } else if (!on && was) {
        panel.classList.remove('on');
        panel.classList.add('leaving');
      }
    }
    this.tab = name;
    this.placeTabIndicator();
    if (!instant) api.settings.set({ sideTab: name });
  }

  placeTabIndicator() {
    const on = $(`.tab[data-tab="${this.tab}"]`);
    const ind = $('#tabIndicator');
    if (!on) return;
    ind.style.height = `${on.offsetHeight}px`;
    ind.style.transform = `translateY(${on.offsetTop}px)`;
  }

  // -- damage states -----------------------------------------------------------------------------------

  setupStages() {
    const menu = $('#damageMenu');
    $('#damageAdd').addEventListener('click', (e) => {
      e.stopPropagation();
      this.buildStageMenu();
      menu.classList.toggle('on');
    });
    document.addEventListener('click', (e) => { if (!menu.contains(e.target)) menu.classList.remove('on'); });
    $('#damageDone').addEventListener('click', () => this.leaveStage());
    // A name committed by pressing elsewhere re-renders the card once that press has landed, so the
    // click it was for still reaches its button.
    document.addEventListener('pointerdown', () => { this.pressing = true; }, true);
    document.addEventListener('pointerup', () => {
      this.pressing = false;
      const queued = this.afterPress;
      this.afterPress = null;
      if (queued) setTimeout(() => queued.forEach((fn) => fn()));
    }, true);
  }

  afterPointer(fn) {
    if (!this.pressing) fn();
    else (this.afterPress ||= []).push(fn);
  }

  /** The layer whose stages the card shows: the active layer, or the one the active stage belongs to. */
  stageOwner() {
    const p = this.project;
    return p ? (p.parentOf(p.layer) || p.layer) : null;
  }

  renderStages() {
    const p = this.project;
    if (!p) return;
    const owner = this.stageOwner();
    const active = p.layer.stage ? p.layer : null;
    $('#damageTitle').textContent = `Damage states · ${owner.name}`;
    const list = $('#damageList');
    list.innerHTML = '';
    const states = p.statesOf(owner);
    if (!states.length) list.innerHTML = '<div class="empty">None yet. Add one to draw how this part looks damaged.</div>';
    for (const s of states) list.append(this.stateBlock(owner, s, active));
    mountIcons(list);
    $('#damageNote').style.display = states.length ? 'none' : '';
    // Drawing a level: say so over the views.
    $('#damageBanner').classList.toggle('on', !!active);
    $('#board').classList.toggle('staging', !!active);
    if (active) {
      $('#damageBannerText').innerHTML = `Drawing damage · <b>${this.escape(owner.name)}</b> › <b>${this.escape(Project.stateName(active))}</b>`
        + ` at <b>${active.stage.from}</b> · ${this.escape(this.typeLabel(active.stage.type))}`;
    }
  }

  /** One damage state in the card: its name and type to change, and a chip per level: a drawn
   * level to draw on, an empty one to add (as a copy of the level below). */
  stateBlock(owner, s, active) {
    const p = this.project;
    // The state's name as it is when clicked: a rename doesn't rebuild the card under the pointer.
    const key = s.levels[0].layer;
    const state = () => p.statesOf(owner).find((x) => x.levels.some((lv) => lv.layer === key));
    const mine = !!active && s.levels.some((lv) => lv.layer === active);
    const el = document.createElement('div');
    el.className = `dstate${mine ? ' on' : ''}`;
    el.innerHTML = `
      <div class="dstate-head">
        <i data-icon="crack"></i>
        <input class="dname" spellcheck="false" maxlength="40" data-tip="Its name">
        <div class="select-wrap dtype-wrap" data-tip="The damage type that brings it on"><select></select></div>
        <button class="lbtn dim" data-act="del" data-tip="Delete this damage state and every level of it"><i data-icon="trash"></i></button>
      </div>
      <div class="dlevels"></div>`;
    const name = el.querySelector('.dname');
    name.value = s.name;
    name.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Enter') name.blur();
      if (e.key === 'Escape') { name.value = state()?.name ?? s.name; name.blur(); }
    });
    name.addEventListener('change', () => {
      if (!this.renameState(owner, key, name.value.trim())) name.value = state()?.name ?? s.name;
    });
    const select = el.querySelector('select');
    this.fillDamageTypes(select, s.type);
    select.addEventListener('change', () => this.setStateType(owner, key, select.value));
    el.querySelector('[data-act=del]').addEventListener('click', (e) => this.deleteState(owner, key, e.currentTarget));
    const levels = el.querySelector('.dlevels');
    const froms = [...new Set([...LEVELS, ...s.levels.map((lv) => lv.from)])].sort((a, b) => a - b);
    for (const from of froms) {
      const lv = s.levels.find((x) => x.from === from);
      const b = document.createElement('button');
      b.className = `dlevel${lv ? ' has' : ''}${lv && lv.layer === active ? ' on' : ''}`;
      b.innerHTML = lv ? `${from}` : `<i>+</i>${from}`;
      const below = s.levels.filter((x) => x.from < from).pop();
      b.dataset.tip = lv ? `Draw it at ${from}` : `Add its drawing at ${from}, starting as ${below ? `level ${below.from}` : `${this.escape(owner.name)} intact`}`;
      b.addEventListener('click', () => {
        const now = state();
        const found = now?.levels.find((x) => x.from === from);
        if (found) this.selectLayer(p.layers.indexOf(found.layer));
        else if (now) this.addLevel(owner, now.name, from);
      });
      levels.append(b);
    }
    if (mine) {
      const row = document.createElement('div');
      row.className = 'dlevel-actions';
      row.innerHTML = `<span>Level <b>${active.stage.from}</b></span>
        <button class="btn subtle" data-act="unlink" data-tip="Make this level a layer of its own"><i data-icon="unlink"></i>Unlink</button>
        <button class="btn subtle" data-act="del-level" data-tip="Delete this level's drawing"><i data-icon="trash"></i>Delete</button>`;
      row.querySelector('[data-act=unlink]').addEventListener('click', () => this.unlinkStage());
      row.querySelector('[data-act=del-level]').addEventListener('click', (e) => this.deleteLevel(e.currentTarget));
      el.append(row);
    }
    return el;
  }

  fillDamageTypes(select, current) {
    const t = this.damageTypes;
    const types = t?.types?.length ? t.types : COMMON_DAMAGE;
    select.innerHTML = '';
    const add = (label, items) => {
      const group = document.createElement('optgroup');
      group.label = label;
      for (const id of items) group.append(new Option(id.replace(/([a-z])([A-Z])/g, '$1 $2'), id));
      select.append(group);
    };
    const common = COMMON_DAMAGE.filter((id) => types.includes(id));
    add('Usual for vehicles', common);
    const groups = Object.keys(t?.groups || {});
    if (groups.length) add('Any of a group', groups.map((g) => `group:${g}`));
    add('Other', types.filter((id) => !common.includes(id)));
    for (const o of select.querySelectorAll('option')) if (o.value.startsWith('group:')) o.textContent = `Any ${o.value.slice(6).toLowerCase()}`;
    if (![...select.options].some((o) => o.value === current)) select.append(new Option(current, current));
    select.value = current;
  }

  buildStageMenu() {
    const p = this.project;
    const owner = this.stageOwner();
    const menu = $('#damageMenu');
    menu.innerHTML = '';
    const item = (label, color, fn) => {
      const b = document.createElement('button');
      b.innerHTML = `<i style="background:${color}"></i>${label}`;
      b.addEventListener('click', () => { menu.classList.remove('on'); fn(); });
      menu.append(b);
    };
    item('New state, from this layer', '#ff9f6b', () => this.addState('copy'));
    item('New state, blank', '#a3a7ae', () => this.addState('blank'));
    const others = p.layers.filter((l) => l !== owner && !l.stage && !p.stagesOf(l).length);
    if (others.length) {
      const sep = document.createElement('div');
      sep.className = 'menu-sep';
      menu.append(sep);
      const label = document.createElement('div');
      label.className = 'menu-label';
      label.textContent = 'New state, linking a drawing';
      menu.append(label);
      for (const l of others) item(this.escape(l.name), KIND_COLOURS[l.kind] || KIND_COLOURS.part, () => this.addState('link', l));
    }
  }

  /** A layer name not taken by another layer. */
  uniqueName(name, self = null) {
    const taken = (n) => this.project.layers.some((l) => l !== self && l.name === n);
    let out = name;
    for (let n = 2; taken(out); n++) out = `${name} ${n}`;
    return out;
  }

  /** A level of a damage state as a layer of its own, put after its layer's other levels and made
   * the one drawn: a copy of the state's level below (or of the layer intact), a blank drawing, or a
   * linked layer's. */
  makeLevel(owner, state, type, from, how, linked = null) {
    const p = this.project;
    const s = p.statesOf(owner).find((x) => x.name === state);
    let st;
    if (how === 'link') {
      st = linked;
      p.layers.splice(p.layers.indexOf(st), 1);
    } else if (how === 'blank') {
      st = new Layer(p.box, owner.kind, state);
      Object.assign(st, { sides: owner.sides, thickness: owner.thickness });
      // A voxel layer's blank level is blank voxels, to draw as it is.
      if (owner.voxels) st.voxels = new Uint8Array(owner.voxels.length).fill(EMPTY);
    } else {
      const below = (s?.levels || []).filter((lv) => lv.from < from).pop();
      st = (below ? below.layer : owner).clone(p.box);
    }
    st.rides = null;
    st.stage = { of: owner.name, state, type: s ? s.type : type, from };
    st.name = this.uniqueName(`${state} ${from}`, st);
    const own = p.stagesOf(owner);
    const at = p.layers.indexOf(own[own.length - 1] || owner) + 1;
    p.layers.splice(at, 0, st);
    p.active = at;
    return st;
  }

  /** A new damage state of the layer, drawn at its first level: a copy of the layer to change, a
   * blank drawing, or another layer's drawing linked as it. Its name field takes the keys at once. */
  addState(how, linked = null) {
    const p = this.project;
    const owner = this.stageOwner();
    const used = new Set(p.statesOf(owner).map((s) => s.name));
    let [name, type] = STATE_GUESSES.find(([n]) => !used.has(n)) || [`Damage ${used.size + 1}`, 'Blunt'];
    if (how === 'link') name = linked.name;
    while (used.has(name)) name += ' 2';
    this.beginStroke();
    const st = this.makeLevel(owner, name, type, LEVELS[0], how, linked);
    this.structureChanged();
    const block = $$('#damageList .dstate').find((b) => b.classList.contains('on'));
    block?.scrollIntoView({ block: 'nearest' });
    const input = block?.querySelector('.dname');
    input?.focus();
    input?.select();
    this.toast(`Added <b>${this.escape(name)}</b> at ${st.stage.from}: name it, pick its damage type, and draw it in the views.`, false, 2600);
  }

  /** A damage state's next drawing, at a level it hasn't got: a copy of its level below. */
  addLevel(owner, state, from) {
    this.beginStroke();
    const st = this.makeLevel(owner, state, null, from, 'copy');
    this.structureChanged();
    this.toast(`Added <b>${this.escape(state)}</b> at <b>${from}</b>. Draw how it looks by then.`, false, 2000);
    return st;
  }

  /** Renames the damage state a level belongs to: every level's layer follows. False if it can't. */
  renameState(owner, key, name) {
    const p = this.project;
    const s = p.statesOf(owner).find((x) => x.levels.some((lv) => lv.layer === key));
    if (!s || !name || name === s.name) return !!s && !!name;
    if (p.statesOf(owner).some((x) => x !== s && x.name === name)) {
      this.toast(`<b>${this.escape(owner.name)}</b> already has a damage state called <b>${this.escape(name)}</b>.`, true, 2000);
      return false;
    }
    this.beginStroke();
    for (const lv of s.levels) {
      lv.layer.stage.state = name;
      lv.layer.name = this.uniqueName(`${name} ${lv.from}`, lv.layer);
    }
    this.afterPointer(() => this.structureChanged());
    return true;
  }

  setStateType(owner, key, type) {
    const s = this.project.statesOf(owner).find((x) => x.levels.some((lv) => lv.layer === key));
    if (!s || s.type === type) return;
    this.beginStroke();
    for (const lv of s.levels) lv.layer.stage.type = type;
    this.structureChanged();
    this.toast(`<b>${this.escape(s.name)}</b> now shows for <b>${this.escape(this.typeLabel(type))}</b> damage.`, false, 1800);
  }

  /** Two clicks delete: the first arms the button. */
  armed(btn, label) {
    if (btn.classList.contains('confirm')) return true;
    btn.classList.add('confirm', 'keep');
    btn.style.color = 'var(--danger)';
    const text = [...btn.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
    const was = text?.textContent;
    if (text) text.textContent = 'Sure?';
    const tip = btn.dataset.tip;
    btn.dataset.tip = `Click again to delete ${label}`;
    setTimeout(() => {
      btn.classList.remove('confirm', 'keep');
      btn.style.color = '';
      if (text) text.textContent = was;
      btn.dataset.tip = tip;
    }, 1800);
    return false;
  }

  deleteState(owner, key, btn) {
    const p = this.project;
    const s = p.statesOf(owner).find((x) => x.levels.some((lv) => lv.layer === key));
    if (!s || !this.armed(btn, s.name)) return;
    this.beginStroke();
    const gone = new Set(s.levels.map((lv) => lv.layer));
    const active = p.layer;
    p.layers = p.layers.filter((l) => !gone.has(l));
    p.active = Math.max(0, p.layers.indexOf(gone.has(active) ? owner : active));
    this.structureChanged();
    this.toast(`Deleted <b>${this.escape(s.name)}</b> and its ${gone.size} level${gone.size > 1 ? 's' : ''} · Ctrl+Z to undo`, false, 2200);
  }

  unlinkStage() {
    const p = this.project;
    const st = p.layer;
    if (!st.stage) return;
    this.beginStroke();
    st.stage = null;
    this.structureChanged();
    this.toast(`<b>${this.escape(st.name)}</b> is a layer of its own again.`, false, 2000);
  }

  deleteLevel(btn) {
    const p = this.project;
    const st = p.layer;
    if (!st.stage || !this.armed(btn, `level ${st.stage.from}`)) return;
    const owner = p.parentOf(st);
    this.beginStroke();
    p.layers.splice(p.active, 1);
    // The state's next level down (or up) takes over; with none left, the layer.
    const rest = owner ? p.statesOf(owner).find((s) => s.name === Project.stateName(st))?.levels : null;
    const next = rest?.filter((lv) => lv.from < st.stage.from).pop() || rest?.[0];
    p.active = Math.max(0, p.layers.indexOf(next ? next.layer : owner));
    this.structureChanged();
    this.toast(`Deleted <b>${this.escape(Project.stateName(st))}</b> at ${st.stage.from} · Ctrl+Z to undo`, false, 2000);
  }

  /** Back to the layer the stage being drawn belongs to. */
  leaveStage() {
    const owner = this.project?.parentOf(this.project.layer);
    if (owner) this.selectLayer(this.project.layers.indexOf(owner));
  }

  // -- keyboard ------------------------------------------------------------------------------------

  setupKeys() {
    document.addEventListener('keydown', (e) => {
      if (e.target.closest('input, select, textarea')) return;
      const k = e.key.toLowerCase();
      if (e.ctrlKey || e.metaKey) {
        if (k === 'z' && !e.shiftKey) { e.preventDefault(); this.undo(); }
        else if (k === 'y' || (k === 'z' && e.shiftKey)) { e.preventDefault(); this.redo(); }
        else if (k === 's') { e.preventDefault(); this.save(e.shiftKey); }
        else if (k === 'o') { e.preventDefault(); this.openDialog(); }
        else if (k === 'n') { e.preventDefault(); this.newProject(); }
        else if (k >= '1' && k <= String(TABS.length)) { e.preventDefault(); this.setTab(TABS[+k - 1]); }
        return;
      }
      if (TOOL_KEYS[k]) this.setTool(TOOL_KEYS[k]);
      else if (k === 'm') this.toggle('mirror');
      else if (k === 'v') this.toggle('depthView');
      else if (k === 'h') this.toggle('showGhosts');
      else if (k === ' ') { e.preventDefault(); this.previewer.setDriving(!this.previewer.driving); }
      else if (k === 'x') this.toggleDamage();
      else if (k === 'escape' && this.placingSeat != null) this.placeSeat(null);
      else if (k === 'escape' && this.project?.layer.stage) this.leaveStage();
      else if ((k === ',' || k === '.') && this.activeView) this.activeView.stepSlice(k === '.' ? 1 : -1);
      else if (k === '<' && this.activeView) this.activeView.copySlice(-1);
      else if (k === '>' && this.activeView) this.activeView.carrySlice(1);
      else if (k === 'k') this.toggle('onion');
      else if (k === '[' || k === ']') {
        const range = $('#depthRange');
        range.value = Math.max(+range.min, Math.min(+range.max, +range.value + (k === ']' ? 1 : -1)));
        this.brushDepth = +range.value;
        range.sync();
        this.setDepthMode('fixed');
      } else if (k === 'arrowup' || k === 'arrowdown') {
        e.preventDefault();
        const p = this.project;
        this.selectLayer(Math.max(0, Math.min(p.layers.length - 1, p.active + (k === 'arrowup' ? 1 : -1))));
      }
    });
  }
}

const app = new App();
window.addEventListener('DOMContentLoaded', () => app.start());
window.__app = app;
export default app;
