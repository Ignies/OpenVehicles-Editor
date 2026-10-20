// The project: a stack of layers (body, wheels, doors, hood, windshield, lamps...), each drawn in
// five orthographic views of one box (side from the right, left from the left, front, top, back), as
// grids of material indices (255 empty) and depths. A layer on both sides takes its right from the
// side view and its left from the left view; while one is blank it mirrors the other. A layer's
// damage states are drawn at levels, each a layer of its own linked to it (stage: { of, state, type,
// from }), shown in its place once the part has taken that much damage. A voxel layer keeps its
// voxels themselves instead: its views are what it looks like from each side, and what's drawn on
// them is applied along each pixel's line of sight. And the canvases that edit the active layer, with
// the other layers shown faintly behind it.

export const EMPTY = 255;
export const VIEWS = ['side', 'left', 'front', 'top', 'back'];
export const SIDE_VIEWS = { side: 'left', left: 'side' };

/** The kinds of layer: how a new one starts, and whether it opens when clicked in the preview. */
export const KINDS = {
  body: { label: 'Body', sides: 'both', thickness: 0, hinge: false },
  wheel: { label: 'Wheel', sides: 'both', thickness: 0, hinge: false },
  door: { label: 'Door', sides: 'left', thickness: 1, hinge: true },
  hood: { label: 'Hood', sides: 'both', thickness: 1, hinge: true },
  tailgate: { label: 'Tailgate', sides: 'both', thickness: 1, hinge: true },
  hatch: { label: 'Rear hatch', sides: 'both', thickness: 1, hinge: true },
  boot: { label: 'Boot lid', sides: 'both', thickness: 1, hinge: true },
  windshield: { label: 'Windshield', sides: 'both', thickness: 1, hinge: true },
  fueldoor: { label: 'Fuel door', sides: 'right', thickness: 1, hinge: true },
  lamp: { label: 'Lamp', sides: 'both', thickness: 2, hinge: false },
  part: { label: 'Part', sides: 'both', thickness: 0, hinge: false },
};

/** Rows, columns and the depth axis' length of a view, for a box. */
export function viewShape(box, view) {
  const { length: L, width: W, height: H } = box;
  return {
    top: { rows: W, cols: L, maxDepth: H },
    side: { rows: H, cols: L, maxDepth: W },
    left: { rows: H, cols: L, maxDepth: W },
    front: { rows: H, cols: W, maxDepth: L },
    back: { rows: H, cols: W, maxDepth: L },
  }[view];
}

const b64 = (arr) => {
  let s = '';
  for (let i = 0; i < arr.length; i += 0x8000) s += String.fromCharCode(...arr.subarray(i, i + 0x8000));
  return btoa(s);
};
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const blank = (arr) => arr.every((m) => m === EMPTY);

/** A view's grid turned left for right, column by column: the other side's view of the same. */
export function flipped(arr, cols) {
  const out = new Uint8Array(arr.length);
  for (let i = 0; i < arr.length; i++) {
    const x = i % cols;
    out[i - x + (cols - 1 - x)] = arr[i];
  }
  return out;
}

// -- voxel layers ----------------------------------------------------------------------------------
// A voxel layer's grid covers the box: ((i * W) + j) * H + k for i along it, j across it from its left
// and k up, each a material's index or EMPTY. It's saved run-length coded: each run its length as an
// unsigned LEB128 varint, then its value, the lot in base64 (as voxels.rle_encode).

export function rleEncode(arr) {
  const out = [];
  for (let i = 0; i < arr.length;) {
    const v = arr[i];
    let j = i + 1;
    while (j < arr.length && arr[j] === v) j++;
    let n = j - i;
    while (n >= 0x80) { out.push((n & 0x7f) | 0x80); n = Math.floor(n / 0x80); }
    out.push(n, v);
    i = j;
  }
  return b64(Uint8Array.from(out));
}

export function rleDecode(data, size) {
  const raw = unb64(data);
  const out = new Uint8Array(size);
  let p = 0;
  for (let i = 0; i < raw.length;) {
    let run = 0;
    let scale = 1;
    let b;
    do { b = raw[i++]; run += (b & 0x7f) * scale; scale *= 0x80; } while (b >= 0x80);
    if (p + run > size) throw new Error('The voxels don\'t fit the box.');
    out.fill(raw[i++], p, p + run);
    p += run;
  }
  if (p !== size) throw new Error('The voxels don\'t fill the box.');
  return out;
}

const voxelCount = (box) => box.length * box.width * box.height;

/** A view's lines of sight through a voxel grid: pixel (x, y)'s voxel t deep is base(x, y) + t * step. */
export function rays(box, view) {
  const { length: L, width: W, height: H } = box;
  const { rows, cols, maxDepth: n } = viewShape(box, view);
  switch (view) {
    case 'top': return { rows, cols, n, step: -1, base: (x, y) => (x * W + y) * H + H - 1 };
    case 'side': return { rows, cols, n, step: -H, base: (x, y) => (x * W + W - 1) * H + H - 1 - y };
    case 'left': return { rows, cols, n, step: H, base: (x, y) => (L - 1 - x) * W * H + H - 1 - y };
    case 'front': return { rows, cols, n, step: -W * H, base: (x, y) => ((L - 1) * W + W - 1 - x) * H + H - 1 - y };
    default: return { rows, cols, n, step: W * H, base: (x, y) => x * H + H - 1 - y };
  }
}

/** How deep a line of sight's first voxel lies, or -1. */
function surfaceOf(vox, at, step, n) {
  for (let t = 0; t < n; t++, at += step) if (vox[at] !== EMPTY) return t;
  return -1;
}

/** A voxel layer seen from a view: per pixel the first voxel's material and depth, as a view holds them. */
export function projectVoxels(vox, box, view) {
  const r = rays(box, view);
  const mat = new Uint8Array(r.rows * r.cols).fill(EMPTY);
  const depth = new Uint8Array(r.rows * r.cols);
  for (let y = 0; y < r.rows; y++) {
    for (let x = 0; x < r.cols; x++) {
      let at = r.base(x, y);
      for (let t = 0; t < r.n; t++, at += r.step) {
        if (vox[at] === EMPTY) continue;
        mat[y * r.cols + x] = vox[at];
        depth[y * r.cols + x] = Math.min(t, 255);
        break;
      }
    }
  }
  return { mat, depth };
}

/** A voxel layer's cross-section t0 deep from a view: per pixel the voxel there, and the first one
 * behind it and how deep that lies. */
export function sliceVoxels(vox, box, view, t0) {
  const r = rays(box, view);
  const size = r.rows * r.cols;
  const mat = new Uint8Array(size).fill(EMPTY);
  const behind = new Uint8Array(size).fill(EMPTY);
  const behindDepth = new Uint8Array(size);
  for (let y = 0; y < r.rows; y++) {
    for (let x = 0; x < r.cols; x++) {
      const p = y * r.cols + x;
      let at = r.base(x, y) + t0 * r.step;
      mat[p] = vox[at];
      for (let t = t0 + 1; t < r.n; t++) {
        at += r.step;
        if (vox[at] === EMPTY) continue;
        behind[p] = vox[at];
        behindDepth[p] = Math.min(t, 255);
        break;
      }
    }
  }
  return { mat, behind, behindDepth };
}

/** The voxels along the line of sight nearest to (x, y) that has any, from its surface in:
 * [how far behind the surface, material] each (voxels._profile). */
function profileAt(vox, r, x, y) {
  for (let ring = 1; ring <= 4; ring++) {
    let best = null;
    for (let dy = -ring; dy <= ring; dy++) {
      for (let dx = -ring; dx <= ring; dx++) {
        const X = x + dx;
        const Y = y + dy;
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring || X < 0 || Y < 0 || X >= r.cols || Y >= r.rows) continue;
        const dist = dx * dx + dy * dy;
        if (best && dist >= best[0]) continue;
        const at = r.base(X, Y);
        const s = surfaceOf(vox, at, r.step, r.n);
        if (s < 0) continue;
        const list = [];
        for (let t = s; t < r.n; t++) if (vox[at + t * r.step] !== EMPTY) list.push([t - s, vox[at + t * r.step]]);
        best = [dist, list];
      }
    }
    if (best) return best[1];
  }
  return null;
}

/** One drawn pixel along its line of sight (voxels._draw_ray): erased cuts through, a deeper surface
 * dents in to it, a nearer one builds out to it, and the surface it lands on takes the material.
 * Where there was nothing, it's built as deep as the profile (the voxels beside it) goes. */
function drawRay(vox, at, step, n, m, d, profile) {
  if (m === EMPTY) {
    for (let t = 0; t < n; t++) vox[at + t * step] = EMPTY;
    return;
  }
  if (d >= n) return;
  const s = surfaceOf(vox, at, step, n);
  if (s < 0) {
    for (const [dt, own] of profile || [[0, m]]) if (d + dt < n) vox[at + (d + dt) * step] = dt === 0 ? m : own;
    return;
  }
  if (d > s) {
    for (let t = s; t < d; t++) vox[at + t * step] = EMPTY;
    let t = d;
    while (t < n && vox[at + t * step] === EMPTY) t++;
    vox[at + (t < n ? t : d) * step] = m;
  } else if (d < s) {
    for (let t = d; t < s; t++) vox[at + t * step] = m;
  } else {
    vox[at + s * step] = m;
  }
}

/** One pixel of a voxel layer's view drawn into its voxels: in a slice, the voxel that deep set or
 * cleared; erased, every voxel along its line of sight, or only the first with surface; extruded, n
 * voxels in from its depth (Infinity: to the far side); else as drawRay. */
export function editRay(vox, box, view, x, y, m, d, { slice = null, extrude = 0, surface = false } = {}) {
  const r = rays(box, view);
  if (x < 0 || y < 0 || x >= r.cols || y >= r.rows) return;
  const at = r.base(x, y);
  if (slice != null) {
    if (slice >= 0 && slice < r.n) vox[at + slice * r.step] = m;
    return;
  }
  if (m === EMPTY && surface) {
    const s = surfaceOf(vox, at, r.step, r.n);
    if (s >= 0) vox[at + s * r.step] = EMPTY;
    return;
  }
  if (m !== EMPTY && extrude) {
    for (let t = Math.max(0, d); t < Math.min(r.n, d + extrude); t++) vox[at + t * r.step] = m;
    return;
  }
  const profile = m !== EMPTY && surfaceOf(vox, at, r.step, r.n) < 0 ? profileAt(vox, r, x, y) : null;
  drawRay(vox, at, r.step, r.n, m, d, profile);
}

/** The surface a pixel of a voxel layer's view is on, or the nearest one beside it within 8 (as
 * ViewCanvas.autoDepth does for a drawing); the fallback where there's none. */
export function voxelAutoDepth(vox, box, view, x, y, fallback) {
  const r = rays(box, view);
  const own = surfaceOf(vox, r.base(x, y), r.step, r.n);
  if (own >= 0) return own;
  for (let ring = 1; ring <= 8; ring++) {
    let best = null;
    for (let dy = -ring; dy <= ring; dy++) {
      for (let dx = -ring; dx <= ring; dx++) {
        const X = x + dx;
        const Y = y + dy;
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring || X < 0 || Y < 0 || X >= r.cols || Y >= r.rows) continue;
        const dist = dx * dx + dy * dy;
        if (best && dist >= best[0]) continue;
        const s = surfaceOf(vox, r.base(X, Y), r.step, r.n);
        if (s >= 0) best = [dist, s];
      }
    }
    if (best) return best[1];
  }
  return fallback;
}

/** Where a view's slice t deep lies in the box: { i } along it, { j } across or { k } up. */
export function slicePlane(box, view, t) {
  const { length: L, width: W, height: H } = box;
  return { top: { k: H - 1 - t }, side: { j: W - 1 - t }, left: { j: t }, front: { i: L - 1 - t }, back: { i: t } }[view];
}

let nextId = 1;

export class Layer {
  constructor(box, kind = 'part', name = null) {
    const k = KINDS[kind] || KINDS.part;
    this.id = nextId++;
    this.kind = kind;
    this.name = name || k.label;
    this.sides = k.sides;
    this.thickness = k.thickness;
    this.visible = true;
    this.locked = false;
    // A layer loaded from the generator keeps the generator's own model (native), with what's drawn
    // on it applied over it; its role keeps the renderer's handling of that part; it may ride on a
    // hinged layer, or be a damage stage of another.
    this.role = null;
    this.rides = null;
    this.native = null;
    this.stage = null;
    // Where a hinged layer hinges and how far it opens ({ edge, angle }; null: its kind's own), and
    // the kit a layer is in the game ({ item, tag, host, maskOnly }: a slot the item fits).
    this.hinge = null;
    this.kit = null;
    this.views = {};
    for (const v of VIEWS) {
      const { rows, cols } = viewShape(box, v);
      this.views[v] = { mat: new Uint8Array(rows * cols).fill(EMPTY), depth: new Uint8Array(rows * cols) };
    }
    // A voxel layer's grid (null for one carved from its views). An undo snapshot holds the same
    // array until the layer next changes, when it gets a copy of its own (shared); rev counts the
    // changes, for the projections kept of it.
    this.voxels = null;
    this.shared = false;
    this.rev = 0;
  }

  get hinged() { return (KINDS[this.kind] || KINDS.part).hinge; }

  clone(box) {
    const l = new Layer(box, this.kind, `${this.name} copy`);
    Object.assign(l, { sides: this.sides, thickness: this.thickness, visible: this.visible, role: this.role,
      rides: this.rides, native: this.native, stage: this.stage ? { ...this.stage } : null,
      hinge: this.hinge ? { ...this.hinge } : null, kit: this.kit ? { ...this.kit } : null,
      voxels: this.voxels ? this.voxels.slice() : null });
    for (const v of VIEWS) {
      l.views[v].mat.set(this.views[v].mat);
      l.views[v].depth.set(this.views[v].depth);
    }
    return l;
  }

  isEmpty() {
    if (this.voxels) return this.voxels.every((m) => m === EMPTY);
    return VIEWS.every((v) => blank(this.views[v].mat));
  }

  /** The view a side of this layer is drawn in, or null when the layer isn't on that side. A voxel
   * layer can be drawn from every side: the inside of a door is what the other side sees. */
  uses(view) {
    if (this.voxels) return true;
    if (view === 'side') return this.sides !== 'left';
    if (view === 'left') return this.sides !== 'right';
    return true;
  }

  /** A blank side or left view of a layer on both sides shows the other side, mirrored. */
  mirrored(view, cols) {
    const other = SIDE_VIEWS[view];
    if (this.voxels || !other || this.sides !== 'both' || !blank(this.views[view].mat) || blank(this.views[other].mat)) return null;
    return { mat: flipped(this.views[other].mat, cols), depth: flipped(this.views[other].depth, cols) };
  }

  /** A voxel layer's grid, to change: a copy of its own first, while an undo snapshot holds it. */
  writable() {
    if (this.shared) {
      this.voxels = this.voxels.slice();
      this.shared = false;
    }
    this.rev++;
    return this.voxels;
  }

  /** A voxel layer as a view shows it (projectVoxels), kept until it changes. */
  projection(box, view) {
    const key = `${this.rev}:${box.length}:${box.width}:${box.height}`;
    if (!this.projected || this.projected.key !== key || this.projected.voxels !== this.voxels) {
      this.projected = { key, voxels: this.voxels, views: {} };
    }
    const views = this.projected.views;
    if (!views[view]) views[view] = projectVoxels(this.voxels, box, view);
    return views[view];
  }

  /** A voxel layer's grid as the file keeps it, encoded once a change. */
  encodedVoxels() {
    if (!this.encoded || this.encoded.rev !== this.rev || this.encoded.voxels !== this.voxels) {
      this.encoded = { rev: this.rev, voxels: this.voxels, data: rleEncode(this.voxels) };
    }
    return { enc: 'rle', data: this.encoded.data };
  }
}

export class Project {
  constructor(box, materials) {
    this.box = { ...box };
    this.materials = materials;
    this.layers = [];
    this.active = 0;
    this.version = 0;
    // What the game calls it and where its riders sit: { id, name, description, seats: [{ id, f, r,
    // z, driver, door }] }, for writing it into the game.
    this.vehicle = null;
    // Pictures traced under the views: { view: { path, opacity } }.
    this.refs = {};
    // Materials of its own, beyond the generator's: [{ name, color }].
    this.customMaterials = [];
  }

  get layer() { return this.layers[this.active]; }

  /** Every level of a layer's damage states, in stack order. */
  stagesOf(layer) { return this.layers.filter((l) => l.stage && l.stage.of === layer.name); }

  /** The damage state a level belongs to (a stage from before states is a state of its own). */
  static stateName(st) { return st.stage.state || st.name; }

  /** A layer's damage states in order: { name, type, levels: [{ from, layer }] lowest first }. */
  statesOf(layer) {
    const out = new Map();
    for (const st of this.stagesOf(layer)) {
      const name = Project.stateName(st);
      if (!out.has(name)) out.set(name, { name, type: st.stage.type, levels: [] });
      out.get(name).levels.push({ from: +st.stage.from || 0, layer: st });
    }
    for (const s of out.values()) s.levels.sort((a, b) => a.from - b.from);
    return [...out.values()];
  }

  /** A layer's views as the backend sends them, by its index. */
  setViews(n, views) {
    const l = this.layers[n];
    for (const v of VIEWS) {
      if (!views[v]) continue;
      l.views[v].mat.set(unb64(views[v].mat));
      l.views[v].depth.set(unb64(views[v].depth));
    }
  }

  /** The layer a damage level belongs to. */
  parentOf(layer) { return layer?.stage ? this.layers.find((l) => !l.stage && l.name === layer.stage.of) || null : null; }

  static fromBackend(data, materials) {
    const p = new Project(data.box, materials);
    for (const spec of data.layers) {
      const l = new Layer(p.box, spec.kind, spec.name);
      Object.assign(l, { sides: spec.sides, thickness: spec.thickness, visible: spec.visible !== false,
        role: spec.role || null, rides: spec.rides || null, native: spec.native || null,
        stage: spec.stage ? { ...spec.stage } : null, locked: !!spec.locked,
        hinge: spec.hinge ? { ...spec.hinge } : null, kit: spec.kit ? { ...spec.kit } : null });
      if (spec.voxels) {
        l.voxels = spec.voxels instanceof Uint8Array ? spec.voxels : Project.decodeVoxels(spec.voxels, p.box);
        l.native = null;
      }
      for (const v of VIEWS) {
        if (!spec.views?.[v]) continue;
        l.views[v].mat.set(typeof spec.views[v].mat === 'string' ? unb64(spec.views[v].mat) : spec.views[v].mat);
        l.views[v].depth.set(typeof spec.views[v].depth === 'string' ? unb64(spec.views[v].depth) : spec.views[v].depth);
      }
      p.layers.push(l);
    }
    return p;
  }

  static blank(box, materials) {
    const p = new Project(box, materials);
    p.layers.push(new Layer(p.box, 'body', 'Body'));
    return p;
  }

  static decodeVoxels(voxels, box) {
    if (voxels.enc !== 'rle') throw new Error(`Unknown voxel encoding ${voxels.enc}.`);
    return rleDecode(voxels.data, voxelCount(box));
  }

  /** An undo step. A voxel layer's grid isn't copied: the layer copies it before it next changes. */
  snapshot() {
    return {
      project: this,
      active: this.active,
      layers: this.layers.map((l) => {
        if (l.voxels) l.shared = true;
        return {
          layer: l,
          props: { kind: l.kind, name: l.name, sides: l.sides, thickness: l.thickness, visible: l.visible, locked: l.locked,
            role: l.role, rides: l.rides, native: l.native, stage: l.stage ? { ...l.stage } : null,
            hinge: l.hinge ? { ...l.hinge } : null, kit: l.kit ? { ...l.kit } : null },
          voxels: l.voxels,
          views: Object.fromEntries(VIEWS.map((v) => [v, { mat: l.views[v].mat.slice(), depth: l.views[v].depth.slice() }])),
        };
      }),
    };
  }

  restore(snap) {
    this.layers = snap.layers.map(({ layer, props, voxels, views }) => {
      Object.assign(layer, props);
      layer.voxels = voxels || null;
      layer.shared = !!voxels;
      layer.rev++;
      for (const v of VIEWS) {
        layer.views[v].mat.set(views[v].mat);
        layer.views[v].depth.set(views[v].depth);
      }
      return layer;
    });
    this.active = Math.min(snap.active, this.layers.length - 1);
    this.version++;
  }

  /** The project as the backend takes it; show names the damage stage each layer shows, if any. A
   * voxel layer sends its voxels in place of its views. */
  toBackend(show = {}) {
    return {
      layers: this.layers.map((l) => ({
        name: l.name, kind: l.kind, sides: l.sides, thickness: l.thickness, visible: l.visible, locked: l.locked,
        role: l.role, rides: l.rides, native: l.native, stage: l.stage,
        ...(l.hinge ? { hinge: l.hinge } : {}), ...(l.kit ? { kit: l.kit } : {}),
        ...(show[l.name] ? { show_stage: show[l.name] } : {}),
        ...(l.voxels ? { voxels: l.encodedVoxels() }
          : { views: Object.fromEntries(VIEWS.map((v) => [v, { mat: b64(l.views[v].mat), depth: b64(l.views[v].depth) }])) }),
      })),
    };
  }

  toFile(extra = {}) {
    return JSON.stringify({
      format: 'cmu-open-vehicle-project', version: 2, box: this.box, materials: this.materials, active: this.active,
      ...this.toBackend(), ...(this.vehicle ? { vehicle: this.vehicle } : {}),
      ...(Object.keys(this.refs).length ? { refs: this.refs } : {}),
      ...(this.customMaterials.length ? { customMaterials: this.customMaterials } : {}), ...extra,
    });
  }

  static fromFile(text, materials) {
    const data = JSON.parse(text);
    if (data.format !== 'cmu-open-vehicle-project') throw new Error('Not a CMU Open Vehicles project.');
    // The file's material names onto the current palette; unknown ones fall back to the first.
    const remap = data.materials.map((name) => Math.max(0, materials.indexOf(name)));
    for (const spec of data.layers) {
      if (spec.voxels) {
        spec.voxels = Project.decodeVoxels(spec.voxels, data.box).map((m) => (m === EMPTY ? EMPTY : remap[m] ?? 0));
        continue;
      }
      for (const v of VIEWS) {
        if (!spec.views?.[v]) continue;
        spec.views[v].mat = unb64(spec.views[v].mat).map((m) => (m === EMPTY ? EMPTY : remap[m] ?? 0));
        spec.views[v].depth = unb64(spec.views[v].depth);
      }
      // From before the left view, a layer on the left was drawn in the side view, seen from its left
      // but nose right: it moves to the left view, nose left.
      const side = spec.views?.side;
      if (spec.sides === 'left' && side && !blank(side.mat) && (!spec.views.left || blank(spec.views.left.mat))) {
        const cols = data.box.length;
        spec.views.left = { mat: flipped(side.mat, cols), depth: flipped(side.depth, cols) };
        spec.views.side = { mat: new Uint8Array(side.mat.length).fill(EMPTY), depth: new Uint8Array(side.mat.length) };
      }
    }
    const p = Project.fromBackend(data, materials);
    p.active = Math.min(data.active || 0, p.layers.length - 1);
    p.vehicle = data.vehicle || null;
    p.refs = data.refs || {};
    p.customMaterials = data.customMaterials || [];
    return { project: p, extra: data };
  }
}

// The tools that draw a shape between where the drag starts and ends.
const SHAPES = new Set(['line', 'rect', 'ellipse']);

/** One view's canvas: paints the active layer over a faint ghost of the others, and takes the tools.
 * On a voxel layer it can show a slice instead: the voxels that deep from its side, those behind faintly. */
export class ViewCanvas {
  constructor(section, view, app) {
    this.section = section;
    this.view = view;
    this.app = app;
    this.stage = section.querySelector('.stage');
    // A reference picture under the drawing, to trace.
    this.ref = document.createElement('img');
    this.ref.className = 'ref';
    this.ref.alt = '';
    this.stage.prepend(this.ref);
    this.refPath = null;
    this.canvas = section.querySelector('canvas.paint');
    this.overlay = section.querySelector('canvas.overlay');
    this.ctx = this.canvas.getContext('2d');
    this.octx = this.overlay.getContext('2d');
    this.buffer = document.createElement('canvas');
    this.bctx = this.buffer.getContext('2d');
    this.scale = 1;
    this.hover = null;
    this.link = null;
    this.drag = null;
    // The depth a voxel layer is sliced at from this view's side; null shows it whole.
    this.slice = null;
    this.lastSlice = null;
    new ResizeObserver(() => this.fit()).observe(this.stage);
    this.stage.addEventListener('pointerdown', (e) => this.down(e));
    this.stage.addEventListener('pointermove', (e) => this.move(e));
    this.stage.addEventListener('pointerup', (e) => this.up(e));
    this.stage.addEventListener('pointerleave', () => this.leave());
    this.stage.addEventListener('contextmenu', (e) => e.preventDefault());
    const ctl = section.querySelector('.slice');
    ctl.querySelector('.slice-btn').addEventListener('click', () => this.toggleSlice());
    for (const b of ctl.querySelectorAll('[data-step]')) b.addEventListener('click', () => this.stepSlice(+b.dataset.step));
    ctl.addEventListener('wheel', (e) => {
      if (!this.slicing) return;
      e.preventDefault();
      this.stepSlice(e.deltaY > 0 ? 1 : -1);
    }, { passive: false });
  }

  get project() { return this.app.project; }
  get shape() { return viewShape(this.project.box, this.view); }
  /** The active layer's view (a voxel layer's projection). */
  get grid() {
    const l = this.project.layer;
    return l.voxels ? l.projection(this.project.box, this.view) : l.views[this.view];
  }
  /** Whether this view shows a slice of the active layer. */
  get slicing() { return this.slice != null && !!this.project?.layer.voxels; }

  fit() {
    if (!this.app.project) return;
    const { rows, cols, maxDepth } = this.shape;
    const r = this.stage.getBoundingClientRect();
    if (!r.width) return;
    this.scale = Math.max(1, Math.floor(Math.min((r.width - 24) / cols, (r.height - 20) / rows)));
    for (const c of [this.canvas, this.overlay]) {
      c.width = cols * this.scale;
      c.height = rows * this.scale;
      c.style.width = `${c.width}px`;
      c.style.height = `${c.height}px`;
    }
    this.buffer.width = cols;
    this.buffer.height = rows;
    this.ref.style.width = this.canvas.style.width;
    this.ref.style.height = this.canvas.style.height;
    this.section.querySelector('.dims').textContent = `${cols}×${rows} · depth ${maxDepth}`;
    if (this.slice != null && this.slice >= maxDepth) this.setSlice(maxDepth - 1);
    this.paint();
  }

  // -- slices --------------------------------------------------------------------------------------

  /** Slices the active voxel layer t deep from this view's side, or (null) shows it whole again. */
  setSlice(t) {
    this.slice = t == null ? null : Math.max(0, Math.min(this.shape.maxDepth - 1, t));
    if (this.slice != null) this.lastSlice = this.slice;
    this.section.querySelector('.slice-out').textContent = this.slice ?? '';
    // The other views draw where the slice cuts them.
    this.app.repaintViews();
  }

  /** Slices this view, or shows it whole again. A layer still carved from its views is made voxels
   * first (Ctrl+Z undoes it), as only voxels have an inside to slice. */
  async toggleSlice() {
    const l = this.project?.layer;
    if (!l) return;
    if (!l.voxels) {
      if (l.stage || !this.app.canEdit()) return;
      await this.app.bakeLayer(l);
      if (this.project?.layer !== l || !l.voxels) return;
    }
    this.app.focusView(this);
    this.setSlice(this.slicing ? null : (this.lastSlice ?? this.outerDepth()));
  }

  stepSlice(delta) {
    if (!this.slicing) this.toggleSlice();
    else this.setSlice(this.slice + delta);
  }

  /** The voxel indices of this view's slice t deep, pixel by pixel (null outside the box). */
  sliceCells(t) {
    const r = rays(this.project.box, this.view);
    if (t < 0 || t >= r.n) return null;
    const out = new Int32Array(r.rows * r.cols);
    for (let y = 0; y < r.rows; y++) for (let x = 0; x < r.cols; x++) out[y * r.cols + x] = r.base(x, y) + t * r.step;
    return out;
  }

  /** Copies the slice delta away (-1 the nearer one) into this one, in place of what's in it. */
  copySlice(delta) {
    const l = this.project?.layer;
    if (!this.slicing || !this.app.canEdit()) return;
    const from = this.sliceCells(this.slice + delta);
    const to = this.sliceCells(this.slice);
    if (!from) return;
    this.app.beginStroke();
    const vox = l.writable();
    for (let p = 0; p < to.length; p++) vox[to[p]] = vox[from[p]];
    this.app.changed(true);
    this.app.toast(`Slice ${this.slice} is a copy of slice ${this.slice + delta} · Ctrl+Z to undo`, false, 1600);
  }

  /** Carries this slice on into the next one (delta 1: deeper) and slices there: a shape built a
   * layer at a time, one key a layer. */
  carrySlice(delta) {
    const l = this.project?.layer;
    if (!this.slicing || !this.app.canEdit()) return;
    const from = this.sliceCells(this.slice);
    const to = this.sliceCells(this.slice + delta);
    if (!to) return;
    this.app.beginStroke();
    const vox = l.writable();
    for (let p = 0; p < to.length; p++) vox[to[p]] = vox[from[p]];
    this.setSlice(this.slice + delta);
    this.app.changed(true);
  }

  /** How deep the active voxel layer's nearest voxel lies from this side: where a first slice starts. */
  outerDepth() {
    const { mat, depth } = this.grid;
    let best = null;
    for (let p = 0; p < mat.length; p++) if (mat[p] !== EMPTY && (best == null || depth[p] < best)) best = depth[p];
    return best ?? 0;
  }

  // -- painting ------------------------------------------------------------------------------------

  paint() {
    if (!this.app.project) return;
    const { rows, cols, maxDepth } = this.shape;
    if (!this.buffer.width || this.buffer.width !== cols || this.buffer.height !== rows) { this.fit(); return; }
    const img = this.bctx.createImageData(cols, rows);
    const tones = this.app.tones(this.view === 'top' ? 'top' : 'front');
    const depthView = this.app.depthView;
    const project = this.project;
    const active = project.layer;
    const slicing = this.slicing;
    const colour = (m, d) => {
      const t = d / Math.max(1, maxDepth);
      if (depthView) {
        const v = 235 - 190 * Math.min(1, t * 1.6);
        return [v * 0.78, v * 0.92, v * 0.62];
      }
      const k = 1 - 0.55 * Math.min(1, t * 1.4);
      return tones[m].map((x) => x * k);
    };
    // The other visible layers: nearest surface first, faded. Damage stages aren't in the stack, and a
    // stage being drawn hides the layer it stands in for. A slice shows the layer alone.
    const ghostDepth = new Uint16Array(rows * cols).fill(65535);
    const ghostMat = new Uint8Array(rows * cols).fill(EMPTY);
    if (this.app.showGhosts && !slicing) {
      const parent = project.parentOf(active);
      for (const l of project.layers) {
        if (l === active || l === parent || l.stage || !l.visible) continue;
        const g = l.voxels ? l.projection(project.box, this.view) : l.views[this.view];
        for (let p = 0; p < rows * cols; p++) {
          if (g.mat[p] !== EMPTY && g.depth[p] < ghostDepth[p]) { ghostDepth[p] = g.depth[p]; ghostMat[p] = g.mat[p]; }
        }
      }
    }
    const implied = active.mirrored(this.view, cols);
    let mat;
    let depth;
    let behind = null;
    let behindDepth = null;
    // With onion skin, the slices either side of it in place of what's behind: the nearer one warm,
    // the deeper one cool.
    let nearer = null;
    let deeper = null;
    if (slicing) {
      ({ mat, behind, behindDepth } = sliceVoxels(active.voxels, project.box, this.view, this.slice));
      if (this.app.onion) {
        if (this.slice > 0) nearer = sliceVoxels(active.voxels, project.box, this.view, this.slice - 1).mat;
        if (this.slice < maxDepth - 1) deeper = sliceVoxels(active.voxels, project.box, this.view, this.slice + 1).mat;
        behind = null;
      }
    } else ({ mat, depth } = implied || this.grid);
    const dim = (active.visible ? 1 : 0.35) * (implied ? 0.55 : 1) * (active.uses(this.view) ? 1 : 0.3);
    this.showState(active, !!implied);
    for (let p = 0; p < rows * cols; p++) {
      let c;
      let a = 255;
      if (mat[p] !== EMPTY) {
        // A slice's own voxels in full colour.
        c = colour(mat[p], slicing ? (depthView ? this.slice : 0) : depth[p]);
        a = 255 * dim;
      } else if (nearer && nearer[p] !== EMPTY) {
        c = colour(nearer[p], 0).map((x, n) => x * 0.35 + [150, 70, 40][n]);
        a = 120;
      } else if (deeper && deeper[p] !== EMPTY) {
        c = colour(deeper[p], 0).map((x, n) => x * 0.35 + [40, 90, 160][n]);
        a = 105;
      } else if (behind && behind[p] !== EMPTY) {
        c = colour(behind[p], behindDepth[p]).map((x) => x * 0.5 + 16);
        a = 84;
      } else if (ghostMat[p] !== EMPTY) {
        c = colour(ghostMat[p], ghostDepth[p]).map((x) => x * 0.55 + 18);
        a = 70;
      } else continue;
      img.data[p * 4] = c[0];
      img.data[p * 4 + 1] = c[1];
      img.data[p * 4 + 2] = c[2];
      img.data[p * 4 + 3] = a;
    }
    this.bctx.putImageData(img, 0, 0);
    if (this.app.activeView === this) this.app.depthPanel?.refresh();
    this.ctx.imageSmoothingEnabled = false;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.drawImage(this.buffer, 0, 0, this.canvas.width, this.canvas.height);
    this.paintOverlay();
  }

  /** The view's hint says what it's showing for this layer. */
  showState(layer, implied) {
    const hint = this.section.querySelector('.hint');
    if (!this.baseHint) this.baseHint = hint.textContent;
    let text = this.baseHint;
    if (!layer.uses(this.view)) text = `not used: ${layer.name} is on the ${layer.sides === 'left' ? 'left' : 'right'}`;
    else if (implied) text = `mirrors the ${this.view === 'left' ? 'right side' : 'left side'} · draw to change it`;
    else if (this.slicing) {
      const b = this.project.box;
      const at = slicePlane(b, this.view, this.slice);
      const plane = at.i != null ? `f ${at.i - Math.floor(b.length / 2)}` : at.j != null ? `r ${at.j - Math.floor(b.width / 2)}` : `z ${at.k}`;
      text = this.app.onion ? `slice at ${plane} · nearer warm, deeper cool` : `slice at ${plane} · behind it faint`;
    }
    hint.textContent = text;
    this.section.classList.toggle('implied', implied);
    this.section.classList.toggle('unused', !layer.uses(this.view));
    this.section.classList.toggle('voxel', !!layer.voxels);
    this.section.classList.toggle('slicing', this.slicing);
  }

  paintOverlay() {
    if (!this.app.project) return;
    const { rows, cols } = this.shape;
    const s = this.scale;
    const o = this.octx;
    o.clearRect(0, 0, this.overlay.width, this.overlay.height);
    if (s >= 7) {
      o.strokeStyle = 'rgba(255,255,255,0.035)';
      o.lineWidth = 1;
      o.beginPath();
      for (let x = 1; x < cols; x++) { o.moveTo(x * s + 0.5, 0); o.lineTo(x * s + 0.5, rows * s); }
      for (let y = 1; y < rows; y++) { o.moveTo(0, y * s + 0.5); o.lineTo(cols * s, y * s + 0.5); }
      o.stroke();
    }
    if (this.app.mirror && !SIDE_VIEWS[this.view]) {
      o.strokeStyle = 'rgba(184,226,106,0.22)';
      o.setLineDash([3, 4]);
      o.beginPath();
      if (this.view === 'top') { o.moveTo(0, (rows / 2) * s); o.lineTo(cols * s, (rows / 2) * s); }
      else { o.moveTo((cols / 2) * s, 0); o.lineTo((cols / 2) * s, rows * s); }
      o.stroke();
      o.setLineDash([]);
    }
    // Where the other views' slices cut this one.
    for (const v of Object.values(this.app.views)) {
      if (v === this || !v.slicing) continue;
      const at = this.linkFor({ i: null, j: null, k: null, ...slicePlane(this.project.box, v.view, v.slice) });
      for (const [col, row] of [[at.col, null], [null, at.row]]) {
        if (col == null && row == null) continue;
        o.fillStyle = 'rgba(184,226,106,0.07)';
        if (col != null) o.fillRect(col * s, 0, s, rows * s);
        else o.fillRect(0, row * s, cols * s, s);
        o.fillStyle = 'rgba(184,226,106,0.75)';
        if (col != null) o.fillRect(Math.floor((col + 0.5) * s), 0, 1, rows * s);
        else o.fillRect(0, Math.floor((row + 0.5) * s), cols * s, 1);
      }
    }
    if (this.link) {
      o.fillStyle = 'rgba(184,226,106,0.09)';
      if (this.link.col != null) o.fillRect(this.link.col * s, 0, s, rows * s);
      if (this.link.row != null) o.fillRect(0, this.link.row * s, cols * s, s);
    }
    this.paintSeats(o, s);
    if (this.drag && SHAPES.has(this.drag.tool) && this.hover) {
      o.fillStyle = 'rgba(184,226,106,0.55)';
      for (const [x, y] of this.shapeCells(this.drag.start, this.hover, this.drag.tool)) o.fillRect(x * s, y * s, s, s);
    }
    if (this.hover) {
      const size = this.app.brushSize;
      const h = Math.floor((size - 1) / 2);
      o.strokeStyle = 'rgba(255,255,255,0.85)';
      o.lineWidth = 1;
      o.strokeRect((this.hover.x - h) * s + 0.5, (this.hover.y - h) * s + 0.5, size * s - 1, size * s - 1);
    }
  }

  /** The vehicle's seats, where this view sees them: a ring for each, the one being placed lit. */
  paintSeats(o, s) {
    const seats = this.project.vehicle?.seats;
    if (!seats?.length) return;
    const { length: L, width: W, height: H } = this.project.box;
    const fx = (f) => f + Math.floor(L / 2);
    const ry = (r) => r + Math.floor(W / 2);
    const zy = (z) => H - 0.5 - z;
    seats.forEach((seat, n) => {
      const at = { top: [fx(seat.f), ry(seat.r)], side: [fx(seat.f), zy(seat.z)], left: [L - fx(seat.f), zy(seat.z)],
        front: [W - ry(seat.r), zy(seat.z)], back: [ry(seat.r), zy(seat.z)] }[this.view];
      const on = this.app.placingSeat === n;
      o.beginPath();
      o.arc(at[0] * s, at[1] * s, Math.max(4, s * 2.2), 0, Math.PI * 2);
      o.fillStyle = on ? 'rgba(184,226,106,0.28)' : 'rgba(120,190,255,0.16)';
      o.fill();
      o.lineWidth = 1.5;
      o.strokeStyle = on ? 'rgba(184,226,106,0.95)' : 'rgba(150,205,255,0.8)';
      o.stroke();
      if (s >= 6) {
        o.fillStyle = on ? '#dff5b6' : '#cfe6ff';
        o.font = '600 10px "Segoe UI", system-ui, sans-serif';
        o.textAlign = 'center';
        o.fillText(seat.driver ? 'D' : seat.id[0].toUpperCase(), at[0] * s, at[1] * s + 3.5);
        o.textAlign = 'start';
      }
    });
  }

  cell(e) {
    const r = this.canvas.getBoundingClientRect();
    const x = Math.floor((e.clientX - r.left) / this.scale);
    const y = Math.floor((e.clientY - r.top) / this.scale);
    const { rows, cols } = this.shape;
    return x >= 0 && y >= 0 && x < cols && y < rows ? { x, y } : null;
  }

  /** The box coordinates (i along, j across, k up) a cell lies on; null where this view doesn't fix
   * one (a slice fixes its depth too). */
  coords(c) {
    const { width: W, height: H } = this.project.box;
    let p;
    switch (this.view) {
      case 'top': p = { i: c.x, j: c.y, k: null }; break;
      case 'side': p = { i: c.x, j: null, k: H - 1 - c.y }; break;
      case 'left': p = { i: this.project.box.length - 1 - c.x, j: null, k: H - 1 - c.y }; break;
      case 'front': p = { i: null, j: W - 1 - c.x, k: H - 1 - c.y }; break;
      default: p = { i: null, j: c.x, k: H - 1 - c.y };
    }
    return this.slicing ? { ...p, ...slicePlane(this.project.box, this.view, this.slice) } : p;
  }

  /** How this view shows a point of the box: the column and row it falls on, where it has them. */
  linkFor(p) {
    const { width: W, height: H } = this.project.box;
    switch (this.view) {
      case 'top': return { col: p.i, row: p.j };
      case 'side': return { col: p.i, row: p.k != null ? H - 1 - p.k : null };
      case 'left': return { col: p.i != null ? this.project.box.length - 1 - p.i : null, row: p.k != null ? H - 1 - p.k : null };
      case 'front': return { col: p.j != null ? W - 1 - p.j : null, row: p.k != null ? H - 1 - p.k : null };
      default: return { col: p.j, row: p.k != null ? H - 1 - p.k : null };
    }
  }

  mirrorOf(x, y) {
    const { rows, cols } = this.shape;
    if (!this.app.mirror || SIDE_VIEWS[this.view]) return null;
    return this.view === 'top' ? { x, y: rows - 1 - y } : { x: cols - 1 - x, y };
  }

  /** Paints a brush's worth of pixels. A null depth is automatic: each pixel keeps the depth it
   * has, or takes the depth of the surface nearest to it. */
  set(x, y, mat, depth, onlyDepth = false) {
    const { rows, cols } = this.shape;
    const size = this.app.brushSize;
    const h = Math.floor((size - 1) / 2);
    const g = this.grid;
    for (let dy = 0; dy < size; dy++) {
      for (let dx = 0; dx < size; dx++) {
        const cx = x - h + dx;
        const cy = y - h + dy;
        for (const p of [{ x: cx, y: cy }, this.mirrorOf(cx, cy)]) {
          if (!p || p.x < 0 || p.y < 0 || p.x >= cols || p.y >= rows) continue;
          const n = p.y * cols + p.x;
          const d = depth ?? this.autoDepth(g, p.x, p.y);
          for (const [grid, at] of [[g, n], ...this.twin(p.x, p.y)]) {
            if (onlyDepth) {
              if (grid.mat[at] !== EMPTY) grid.depth[at] = d;
            } else {
              grid.mat[at] = mat;
              grid.depth[at] = mat === EMPTY ? 0 : d;
            }
          }
        }
      }
    }
  }

  /** The depth an automatic pixel gets: its own if it's drawn, else the nearest drawn pixel's, so
   * what's drawn on a surface stays on it (a panel grows at the panel's depth); the brush's depth
   * where there's nothing near. */
  autoDepth(g, x, y) {
    const { rows, cols } = this.shape;
    if (g.mat[y * cols + x] !== EMPTY) return g.depth[y * cols + x];
    for (let r = 1; r <= 8; r++) {
      let best = null;
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
          const X = x + dx;
          const Y = y + dy;
          if (X < 0 || Y < 0 || X >= cols || Y >= rows || g.mat[Y * cols + X] === EMPTY) continue;
          const dist = dx * dx + dy * dy;
          if (!best || dist < best[0]) best = [dist, g.depth[Y * cols + X]];
        }
      }
      if (best) return best[1];
    }
    return this.app.brushDepth;
  }

  /** With mirror on, the other side's view of a layer on both sides takes the same stroke, left for
   * right, once it has been drawn (while blank it mirrors this one anyway). */
  twin(x, y) {
    const other = SIDE_VIEWS[this.view];
    const layer = this.project.layer;
    if (!other || !this.app.mirror || layer.sides !== 'both' || blank(layer.views[other].mat)) return [];
    const { cols } = this.shape;
    return [[layer.views[other], y * cols + (cols - 1 - x)]];
  }

  /** Drawing on a blank side of a layer on both sides starts it as the other side mirrored, which is
   * what it showed: a view drawn carves the layer, so one holding only a detail would cut the rest. */
  seed() {
    const layer = this.project.layer;
    const implied = layer.mirrored(this.view, this.shape.cols);
    if (!implied) return;
    layer.views[this.view].mat.set(implied.mat);
    layer.views[this.view].depth.set(implied.depth);
  }

  apply(tool, x, y, erase) {
    const app = this.app;
    if (this.project.layer.voxels) this.applyVoxels(tool, x, y, erase);
    else if (erase || tool === 'eraser') this.set(x, y, EMPTY, 0);
    else if (tool === 'depth') this.set(x, y, 0, app.brushDepth, true);
    else this.set(x, y, app.material, app.depthMode === 'auto' ? null : app.brushDepth);
  }

  // -- drawing on a voxel layer --------------------------------------------------------------------

  /** Where a voxel layer's pixel is drawn: here and, with mirror on a layer on both sides, its
   * reflection (r to -r-1): across the middle of the top, front and back views, the same pixel of the
   * other side's view. */
  voxelTargets(x, y) {
    const out = [[this.view, x, y]];
    if (!this.app.mirror || this.project.layer.sides !== 'both') return out;
    const { rows, cols } = this.shape;
    const other = SIDE_VIEWS[this.view];
    if (other) out.push([other, cols - 1 - x, y]);
    else out.push(this.view === 'top' ? [this.view, x, rows - 1 - y] : [this.view, cols - 1 - x, y]);
    return out;
  }

  /** One pixel of a voxel layer drawn along its line of sight (or in the slice), and its mirror: m
   * EMPTY erases (through, or only the surface), else from depth d, extruded when the brush says. */
  editVoxel(vox, x, y, m, d, plain = false) {
    const app = this.app;
    const opts = {
      slice: this.slicing ? this.slice : null,
      extrude: plain ? 0 : app.extrude === 'through' ? Infinity : app.extrude === 'n' ? app.extrudeN : 0,
      surface: app.eraseMode === 'surface',
    };
    for (const [view, tx, ty] of this.voxelTargets(x, y)) editRay(vox, this.project.box, view, tx, ty, m, d, opts);
  }

  /** A brush's worth of a voxel layer's pixels. The depth brush moves a surface, keeping its material. */
  applyVoxels(tool, x, y, erase) {
    const app = this.app;
    const box = this.project.box;
    const { rows, cols } = this.shape;
    const rubout = erase || tool === 'eraser';
    if (tool === 'depth' && !rubout && this.slicing) return;
    const vox = this.project.layer.writable();
    const r = rays(box, this.view);
    const size = app.brushSize;
    const h = Math.floor((size - 1) / 2);
    for (let dy = 0; dy < size; dy++) {
      for (let dx = 0; dx < size; dx++) {
        const px = x - h + dx;
        const py = y - h + dy;
        if (px < 0 || py < 0 || px >= cols || py >= rows) continue;
        if (rubout) {
          this.editVoxel(vox, px, py, EMPTY, 0);
        } else if (tool === 'depth') {
          const at = r.base(px, py);
          const s = surfaceOf(vox, at, r.step, r.n);
          if (s >= 0) this.editVoxel(vox, px, py, vox[at + s * r.step], app.brushDepth, true);
        } else {
          const d = app.depthMode === 'auto' ? voxelAutoDepth(vox, box, this.view, px, py, app.brushDepth) : app.brushDepth;
          this.editVoxel(vox, px, py, app.material, d);
        }
      }
    }
  }

  /** The fill on a voxel layer: the pixels around (x, y) of one material and depth (in a slice, of one
   * material), each drawn along its line of sight. False when there's nothing to change. */
  floodVoxels(x, y) {
    const app = this.app;
    const layer = this.project.layer;
    const { rows, cols } = this.shape;
    const slicing = this.slicing;
    const g = slicing ? sliceVoxels(layer.voxels, this.project.box, this.view, this.slice) : this.grid;
    const n0 = y * cols + x;
    const m0 = g.mat[n0];
    const d0 = slicing ? 0 : g.depth[n0];
    const m1 = app.material;
    let d1 = 0;
    if (!slicing) d1 = app.depthMode === 'auto' ? (m0 !== EMPTY ? d0 : this.autoDepth(g, x, y)) : app.brushDepth;
    if (m0 === m1 && (slicing || (d0 === d1 && app.extrude === 'off'))) return false;
    const seen = new Uint8Array(rows * cols);
    const cells = [];
    const stack = [[x, y]];
    while (stack.length) {
      const [cx, cy] = stack.pop();
      if (cx < 0 || cy < 0 || cx >= cols || cy >= rows) continue;
      const n = cy * cols + cx;
      if (seen[n] || g.mat[n] !== m0 || (!slicing && m0 !== EMPTY && g.depth[n] !== d0)) continue;
      seen[n] = 1;
      cells.push(n);
      stack.push([cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]);
    }
    const vox = layer.writable();
    for (const n of cells.sort((a, b) => a - b)) this.editVoxel(vox, n % cols, Math.floor(n / cols), m1, d1);
    return true;
  }

  flood(x, y) {
    const { rows, cols } = this.shape;
    const fill = (sx, sy, g = this.grid) => {
      const n0 = sy * cols + sx;
      const m0 = g.mat[n0];
      const d0 = g.depth[n0];
      const m1 = this.app.material;
      // Automatic: a drawn area keeps its depth, an empty one takes the surface next to it.
      const auto = this.app.depthMode === 'auto';
      const d1 = auto ? (m0 !== EMPTY ? d0 : this.autoDepth(g, sx, sy)) : this.app.brushDepth;
      if (m0 === m1 && d0 === d1) return;
      const stack = [[sx, sy]];
      const seen = new Uint8Array(rows * cols);
      while (stack.length) {
        const [cx, cy] = stack.pop();
        if (cx < 0 || cy < 0 || cx >= cols || cy >= rows) continue;
        const n = cy * cols + cx;
        if (seen[n] || g.mat[n] !== m0 || (m0 !== EMPTY && g.depth[n] !== d0)) continue;
        seen[n] = 1;
        g.mat[n] = m1;
        g.depth[n] = d1;
        stack.push([cx + 1, cy], [cx - 1, cy], [cx, cy + 1], [cx, cy - 1]);
      }
    };
    fill(x, y);
    const m = this.mirrorOf(x, y);
    if (m) fill(m.x, m.y);
    // The other side's view, filled from the same place mirrored.
    for (const [grid] of this.twin(x, y)) fill(cols - 1 - x, y, grid);
  }

  shapeCells(a, b, tool) {
    const out = [];
    if (tool === 'rect' || tool === 'ellipse') {
      const x0 = Math.min(a.x, b.x);
      const x1 = Math.max(a.x, b.x);
      const y0 = Math.min(a.y, b.y);
      const y1 = Math.max(a.y, b.y);
      // An ellipse fills the cells whose centres lie inside the one the drag spans.
      const cx = (x0 + x1 + 1) / 2;
      const cy = (y0 + y1 + 1) / 2;
      const rx = (x1 - x0 + 1) / 2;
      const ry = (y1 - y0 + 1) / 2;
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
          if (tool === 'ellipse' && ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 > 1) continue;
          out.push([x, y]);
        }
      }
      return out;
    }
    let x0 = a.x; let y0 = a.y;
    const dx = Math.abs(b.x - x0); const dy = -Math.abs(b.y - y0);
    const sx = x0 < b.x ? 1 : -1; const sy = y0 < b.y ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      out.push([x0, y0]);
      if (x0 === b.x && y0 === b.y) break;
      const e2 = 2 * err;
      if (e2 >= dy) { err += dy; x0 += sx; }
      if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return out;
  }

  down(e) {
    const c = this.cell(e);
    if (!c) return;
    if (this.app.placingSeat != null && this.app.seatClicked(this.view, c)) return;
    this.app.focusView(this);
    const tool = e.altKey ? 'picker' : this.app.tool;
    const erase = e.button === 2;
    const layer = this.project.layer;
    if (tool === 'picker') {
      if (this.slicing) {
        const r = rays(this.project.box, this.view);
        const m = layer.voxels[r.base(c.x, c.y) + this.slice * r.step];
        if (m !== EMPTY) this.app.pick(m, this.slice);
        return;
      }
      const n = c.y * this.shape.cols + c.x;
      const g = layer.mirrored(this.view, this.shape.cols) || this.grid;
      if (g.mat[n] !== EMPTY) this.app.pick(g.mat[n], g.depth[n]);
      return;
    }
    if (!this.app.canEdit()) return;
    if (!layer.uses(this.view)) {
      this.app.toast(`<b>${this.app.escape(layer.name)}</b> is on the ${layer.sides === 'left' ? 'left: draw it in the Left' : 'right: draw it in the Side'} view.`, true, 2200);
      return;
    }
    // Below 0, a surface stands out past the box: it grows that far first (up to the game's sprite
    // frame), and the stroke goes on at its new edge.
    const fixed = tool === 'depth' || (this.app.depthMode === 'fixed' && !SHAPES.has(tool) && tool !== 'fill');
    if (!erase && !this.slicing && fixed && this.app.brushDepth < 0) {
      this.app.growFor(this.view, -this.app.brushDepth).then((grown) => { if (grown) this.down(e); });
      return;
    }
    this.stage.setPointerCapture(e.pointerId);
    this.app.beginStroke();
    this.seed();
    if (tool === 'fill' && !erase) {
      if (layer.voxels) this.floodVoxels(c.x, c.y);
      else this.flood(c.x, c.y);
      this.app.changed(true);
      return;
    }
    this.drag = { tool: erase ? 'eraser' : tool, erase, start: c, last: c };
    if (SHAPES.has(tool)) { this.paintOverlay(); return; }
    this.apply(this.drag.tool, c.x, c.y, erase);
    this.app.changed(false);
  }

  move(e) {
    const c = this.cell(e);
    this.hover = c;
    this.app.hoverAt(this, c ? this.coords(c) : null);
    if (this.drag && c && !SHAPES.has(this.drag.tool)) {
      for (const [x, y] of this.shapeCells(this.drag.last, c, 'line')) this.apply(this.drag.tool, x, y, this.drag.erase);
      this.drag.last = c;
      this.app.changed(false);
    }
    this.paintOverlay();
  }

  up(e) {
    if (!this.drag) return;
    const c = this.cell(e) || this.drag.last;
    if (SHAPES.has(this.drag.tool)) {
      for (const [x, y] of this.shapeCells(this.drag.start, c, this.drag.tool)) this.apply('pencil', x, y, this.drag.erase);
    }
    this.drag = null;
    this.app.changed(true);
  }

  leave() {
    this.hover = null;
    this.app.hoverAt(this, null);
    this.paintOverlay();
  }
}
