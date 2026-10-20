// The depth panel: the active voxel layer in 3D, cut away where the active view is sliced, and a ruler
// of that view's slices, each a bar as full as the slice is, to see the layer's depth at a glance and
// walk it layer by layer. Click or drag the ruler to slice there; hover it for that slice's picture.
// Drag the 3D view to turn and tilt it (it turns to face a view when that view is sliced); click a
// voxel in it to slice the active view through that voxel.

import { EMPTY, sliceVoxels, slicePlane, viewShape } from './editor.js';

// Where the camera looks from for each view's slice, as a yaw: 0 is ahead of the vehicle, a quarter
// turn its left side, a negative quarter its right; each a little off square, to see it as a solid.
const FACING = { front: -0.55, side: -Math.PI / 2 + 0.55, back: Math.PI - 0.55, left: Math.PI / 2 - 0.55, top: -0.7 };

/** How deep voxel (i, j, k) lies from a view's side. */
export function depthFrom(box, view, i, j, k) {
  const { length: L, width: W, height: H } = box;
  return { top: H - 1 - k, side: W - 1 - j, left: j, front: L - 1 - i, back: i }[view];
}

export class DepthPanel {
  constructor(section, app) {
    this.section = section;
    this.app = app;
    this.canvas = section.querySelector('canvas.cut');
    this.ctx = this.canvas.getContext('2d');
    this.ruler = section.querySelector('canvas.ruler');
    this.rctx = this.ruler.getContext('2d');
    this.peek = section.querySelector('.peek');
    this.peekCanvas = this.peek.querySelector('canvas');
    this.yaw = FACING.top;
    this.pitch = 0.62;
    this.quads = [];
    this.facedView = null;
    this.pending = false;
    this.counts = null;
    this.key = null;
    this.hoverT = null;
    new ResizeObserver(() => this.refresh()).observe(section.querySelector('.cut-stage'));
    new ResizeObserver(() => this.paintRuler()).observe(section.querySelector('.ruler-stage'));

    // Turning the 3D view.
    let turn = null;
    this.canvas.addEventListener('pointerdown', (e) => {
      turn = { x: e.clientX, y: e.clientY, yaw: this.yaw, pitch: this.pitch, moved: false };
      this.canvas.setPointerCapture(e.pointerId);
    });
    this.canvas.addEventListener('pointermove', (e) => {
      if (!turn) return;
      if (Math.abs(e.clientX - turn.x) + Math.abs(e.clientY - turn.y) > 3) turn.moved = true;
      if (!turn.moved) return;
      this.yaw = turn.yaw + (e.clientX - turn.x) * 0.012;
      this.pitch = Math.max(0.12, Math.min(1.45, turn.pitch + (e.clientY - turn.y) * 0.01));
      this.refresh();
    });
    this.canvas.addEventListener('pointerup', (e) => {
      if (turn && !turn.moved) this.pickAt(e);
      turn = null;
    });
    this.canvas.addEventListener('dblclick', () => { this.facedView = null; this.refresh(); });

    // The ruler: click or drag to slice there, hover to see the slice.
    let scrub = false;
    this.ruler.addEventListener('pointerdown', (e) => {
      scrub = true;
      this.ruler.setPointerCapture(e.pointerId);
      this.scrubTo(e);
    });
    this.ruler.addEventListener('pointermove', (e) => {
      if (scrub) this.scrubTo(e);
      this.hover(e);
    });
    this.ruler.addEventListener('pointerup', () => { scrub = false; });
    this.ruler.addEventListener('pointerleave', () => { this.hoverT = null; this.peek.classList.remove('on'); this.paintRuler(); });

    for (const b of section.querySelectorAll('[data-depth]')) b.addEventListener('click', () => this.command(b.dataset.depth));
  }

  get view() { return this.app.activeView; }

  /** The voxel drawn nearest under the pointer in the 3D view: the active view slices through it. */
  pickAt(e) {
    const v = this.view;
    const p = this.app.project;
    if (!v || !p?.layer.voxels) return;
    const r = this.canvas.getBoundingClientRect();
    const dpr = this.canvas.width / Math.max(1, r.width);
    const x = (e.clientX - r.left) * dpr;
    const y = (e.clientY - r.top) * dpr;
    for (let n = this.quads.length - 1; n >= 0; n--) {
      const q = this.quads[n];
      if (!inside(q.sc, x, y)) continue;
      const [i, j, k] = q.at;
      v.setSlice(depthFrom(p.box, v.view, i, j, k));
      return;
    }
  }

  command(name) {
    const v = this.view;
    if (name === 'bake') { const l = this.app.project?.layer; if (l && !l.voxels) this.app.bakeLayer(l); return; }
    if (name === 'onion') { this.app.toggle('onion'); return; }
    if (!v) return;
    if (name === 'whole') v.setSlice(null);
    else if (name === 'nearer') v.stepSlice(-1);
    else if (name === 'deeper') v.stepSlice(1);
    else if (name === 'pull') v.copySlice(-1);
    else if (name === 'carry') v.carrySlice(1);
  }

  /** The depth under the pointer on the ruler. */
  rulerT(e) {
    const v = this.view;
    if (!v) return null;
    const n = v.shape.maxDepth;
    const r = this.ruler.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - r.left) / r.width) * n)));
  }

  scrubTo(e) {
    const v = this.view;
    const l = this.app.project?.layer;
    if (!v || !l?.voxels) return;
    const t = this.rulerT(e);
    if (t !== v.slice) v.setSlice(t);
  }

  hover(e) {
    const v = this.view;
    const l = this.app.project?.layer;
    if (!v || !l?.voxels) return;
    const t = this.rulerT(e);
    if (t === this.hoverT) return;
    this.hoverT = t;
    this.paintRuler();
    // The slice's picture, above the ruler where the pointer is.
    const box = this.app.project.box;
    const { rows, cols } = viewShape(box, v.view);
    const { mat } = sliceVoxels(l.voxels, box, v.view, t);
    const tones = this.app.tones(v.view === 'top' ? 'top' : 'front');
    const scale = Math.max(1, Math.floor(Math.min(180 / cols, 90 / rows)));
    const c = this.peekCanvas;
    c.width = cols;
    c.height = rows;
    c.style.width = `${cols * scale}px`;
    c.style.height = `${rows * scale}px`;
    const g = c.getContext('2d');
    const img = g.createImageData(cols, rows);
    let n = 0;
    for (let p = 0; p < rows * cols; p++) {
      if (mat[p] === EMPTY) continue;
      n++;
      const [r, gr, b] = tones[mat[p]];
      img.data.set([r, gr, b, 255], p * 4);
    }
    g.putImageData(img, 0, 0);
    this.peek.querySelector('span').textContent = `${t} deep · ${this.planeText(v.view, t)} · ${n} voxels`;
    const rr = this.ruler.getBoundingClientRect();
    const sr = this.section.getBoundingClientRect();
    const x = rr.left - sr.left + ((t + 0.5) / v.shape.maxDepth) * rr.width;
    this.peek.style.left = `${Math.max(8, Math.min(sr.width - cols * scale - 20, x - (cols * scale) / 2 - 6))}px`;
    this.peek.classList.add('on');
  }

  planeText(view, t) {
    const b = this.app.project.box;
    const at = slicePlane(b, view, t);
    return at.i != null ? `f ${at.i - Math.floor(b.length / 2)}` : at.j != null ? `r ${at.j - Math.floor(b.width / 2)}` : `z ${at.k}`;
  }

  /** Repaints on the next frame (as often as the layer changes, at most once a frame). */
  refresh() {
    if (this.pending) return;
    this.pending = true;
    requestAnimationFrame(() => {
      this.pending = false;
      this.paint();
    });
  }

  paint() {
    const app = this.app;
    const p = app.project;
    if (!p) return;
    const l = p.layer;
    const v = this.view;
    const voxel = !!l.voxels;
    this.section.classList.toggle('carved', !voxel);
    this.section.classList.toggle('slicing', !!v?.slicing);
    this.section.classList.toggle('onion', !!app.onion);
    const title = this.section.querySelector('.depth-hint');
    if (!voxel) title.textContent = `${l.name} is carved from its views`;
    else if (v?.slicing) title.textContent = `${l.name} · ${v.view} slice ${v.slice} of ${v.shape.maxDepth} · ${this.planeText(v.view, v.slice)}`;
    else title.textContent = `${l.name} · whole · pick a slice below`;
    if (v?.slicing && this.facedView !== v.view) {
      this.facedView = v.view;
      this.yaw = FACING[v.view];
    }
    this.paintCut();
    this.paintRuler();
  }

  /** The voxel count of each of the active view's slices and its commonest material. */
  sliceCounts() {
    const p = this.app.project;
    const l = p.layer;
    const v = this.view;
    if (!l.voxels || !v) return null;
    const key = `${l.id}:${l.rev}:${v.view}:${p.box.length}:${p.box.width}:${p.box.height}`;
    if (this.key === key) return this.counts;
    const { length: L, width: W, height: H } = p.box;
    const n = v.shape.maxDepth;
    const count = new Uint32Array(n);
    const byMat = new Map();
    const vox = l.voxels;
    for (let i = 0; i < L; i++) {
      for (let j = 0; j < W; j++) {
        const base = (i * W + j) * H;
        for (let k = 0; k < H; k++) {
          const m = vox[base + k];
          if (m === EMPTY) continue;
          const t = depthFrom(p.box, v.view, i, j, k);
          count[t]++;
          const key2 = t * 256 + m;
          byMat.set(key2, (byMat.get(key2) || 0) + 1);
        }
      }
    }
    const top = new Uint8Array(n).fill(EMPTY);
    const best = new Uint32Array(n);
    for (const [k2, c] of byMat) {
      const t = Math.floor(k2 / 256);
      if (c > best[t]) { best[t] = c; top[t] = k2 % 256; }
    }
    this.key = key;
    this.counts = { count, top, max: Math.max(1, ...count) };
    return this.counts;
  }

  paintRuler() {
    const c = this.ruler;
    const r = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.max(1, Math.round(r.width * dpr));
    c.height = Math.max(1, Math.round(r.height * dpr));
    const g = this.rctx;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, r.width, r.height);
    const v = this.view;
    const info = this.app.project && this.sliceCounts();
    if (!v || !info) return;
    const n = v.shape.maxDepth;
    const w = r.width / n;
    const h = r.height - 14;
    const tones = this.app.tones('front');
    for (let t = 0; t < n; t++) {
      const x = t * w;
      const on = v.slicing && t === v.slice;
      if (on || t === this.hoverT) {
        g.fillStyle = on ? 'rgba(184,226,106,0.16)' : 'rgba(255,255,255,0.05)';
        g.fillRect(x, 0, w, h);
      }
      const c0 = info.count[t];
      if (!c0) {
        g.fillStyle = 'rgba(255,255,255,0.06)';
        g.fillRect(x + Math.min(1, w * 0.15), h - 1, Math.max(1, w - 2 * Math.min(1, w * 0.15)), 1);
        continue;
      }
      const bh = Math.max(2, Math.round((h - 4) * Math.sqrt(c0 / info.max)));
      const [cr, cg, cb] = tones[info.top[t]] || [180, 180, 180];
      g.fillStyle = `rgb(${cr},${cg},${cb})`;
      g.globalAlpha = on ? 1 : 0.72;
      const pad = Math.min(1, w * 0.15);
      g.fillRect(x + pad, h - bh, Math.max(1, w - 2 * pad), bh);
      g.globalAlpha = 1;
    }
    if (v.slicing) {
      g.fillStyle = '#b8e26a';
      g.fillRect(v.slice * w, h + 1, w, 2);
    }
    // Ticks every 4, labelled every 8; 0 is this view's own side.
    g.fillStyle = 'rgba(255,255,255,0.28)';
    g.font = '9px "Cascadia Mono", Consolas, monospace';
    g.textBaseline = 'top';
    for (let t = 0; t <= n; t += 4) {
      g.fillRect(Math.round(t * w), h + 4, 1, t % 8 ? 2 : 4);
      if (t % 8 === 0 && t < n) g.fillText(String(t), Math.round(t * w) + 3, h + 4);
    }
  }

  /** The active voxel layer in 3D, the voxels nearer than the slice cut away and the slice lit. */
  paintCut() {
    const c = this.canvas;
    const stage = c.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.max(1, Math.round(stage.width * dpr));
    c.height = Math.max(1, Math.round(stage.height * dpr));
    c.style.width = `${stage.width}px`;
    c.style.height = `${stage.height}px`;
    const g = this.ctx;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, c.width, c.height);
    this.quads = [];
    const p = this.app.project;
    const l = p?.layer;
    if (!l?.voxels) return;
    const { length: L, width: W, height: H } = p.box;
    const vox = l.voxels;
    const v = this.view;
    const sliced = v?.slicing ? { view: v.view, t: v.slice } : null;
    const kept = (i, j, k) => !sliced || depthFrom(p.box, sliced.view, i, j, k) >= sliced.t;
    const at = (i, j, k) => (i < 0 || j < 0 || k < 0 || i >= L || j >= W || k >= H ? EMPTY : vox[(i * W + j) * H + k]);
    const solid = (i, j, k) => at(i, j, k) !== EMPTY && kept(i, j, k);

    // The camera: yaw about the vertical, pitched down; it sits towards (cos yaw, -sin yaw). Screen x
    // right, y down, d towards the camera. (The box's j runs to the vehicle's right, so seen from
    // ahead its right side is on the left.)
    const cy = Math.cos(this.yaw);
    const sy = Math.sin(this.yaw);
    const cp = Math.cos(this.pitch);
    const sp = Math.sin(this.pitch);
    // World: x forward (i), y to the right (j), z up (k); centred on the box.
    const proj = (x, y, z) => {
      const X = x - L / 2;
      const Y = y - W / 2;
      const Z = z - H / 2;
      const u = -(X * sy + Y * cy);
      const w = X * cy - Y * sy;
      return [u, -Z * cp + w * sp, w * cp + Z * sp];
    };
    // Fit the box's corners.
    let minX = Infinity; let maxX = -Infinity; let minY = Infinity; let maxY = -Infinity;
    for (const x of [0, L]) for (const y of [0, W]) for (const z of [0, H]) {
      const [a, b] = proj(x, y, z);
      minX = Math.min(minX, a); maxX = Math.max(maxX, a); minY = Math.min(minY, b); maxY = Math.max(maxY, b);
    }
    const s = Math.min((c.width - 24 * dpr) / (maxX - minX), (c.height - 20 * dpr) / (maxY - minY));
    const ox = c.width / 2 - ((minX + maxX) / 2) * s;
    const oy = c.height / 2 - ((minY + maxY) / 2) * s;
    const screen = (x, y, z) => {
      const [a, b, d] = proj(x, y, z);
      return [ox + a * s, oy + b * s, d];
    };

    // The faces that look at the camera: up, and the two sides turned towards it. Camera direction in
    // world terms: towards +x when cos(yaw) > 0, towards -y when sin(yaw) > 0.
    const faces = [{ n: [0, 0, 1], shade: 1, tone: 'top' }];
    faces.push(cy >= 0 ? { n: [1, 0, 0], shade: 0.82, tone: 'front' } : { n: [-1, 0, 0], shade: 0.82, tone: 'front' });
    faces.push(sy >= 0 ? { n: [0, -1, 0], shade: 0.64, tone: 'front' } : { n: [0, 1, 0], shade: 0.64, tone: 'front' });
    const tones = { top: this.app.tones('top'), front: this.app.tones('front') };

    // The quads, painted far to near.
    const quads = [];
    for (let i = 0; i < L; i++) {
      for (let j = 0; j < W; j++) {
        for (let k = 0; k < H; k++) {
          const m = vox[(i * W + j) * H + k];
          if (m === EMPTY || !kept(i, j, k)) continue;
          const lit = sliced && depthFrom(p.box, sliced.view, i, j, k) === sliced.t;
          for (const f of faces) {
            const [a, b, d] = f.n;
            if (solid(i + a, j + b, k + d)) continue;
            // The face's corners.
            let pts;
            if (d) pts = [[i, j, k + 1], [i + 1, j, k + 1], [i + 1, j + 1, k + 1], [i, j + 1, k + 1]];
            else if (a) { const x = a > 0 ? i + 1 : i; pts = [[x, j, k], [x, j + 1, k], [x, j + 1, k + 1], [x, j, k + 1]]; }
            else { const y = b > 0 ? j + 1 : j; pts = [[i, y, k], [i + 1, y, k], [i + 1, y, k + 1], [i, y, k + 1]]; }
            const sc = pts.map(([x, y, z]) => screen(x, y, z));
            const depth = (sc[0][2] + sc[1][2] + sc[2][2] + sc[3][2]) / 4;
            quads.push({ sc, depth, m, f, lit, at: [i, j, k] });
          }
        }
      }
    }
    quads.sort((q1, q2) => q1.depth - q2.depth);
    this.quads = quads;
    for (const q of quads) {
      const [r, gr, b] = tones[q.f.tone][q.m] || [160, 160, 160];
      let k = q.f.shade;
      if (sliced && !q.lit) k *= 0.62;
      const lift = q.lit ? 1.12 : 1;
      g.fillStyle = `rgb(${Math.min(255, r * k * lift)},${Math.min(255, gr * k * lift)},${Math.min(255, b * k * lift)})`;
      g.beginPath();
      g.moveTo(q.sc[0][0], q.sc[0][1]);
      for (let n = 1; n < 4; n++) g.lineTo(q.sc[n][0], q.sc[n][1]);
      g.closePath();
      g.fill();
      // Seams, so the voxels read as voxels when they're big enough.
      if (s > 7 * dpr) { g.strokeStyle = 'rgba(0,0,0,0.18)'; g.lineWidth = 1; g.stroke(); }
    }

    // The slice's plane, outlined across the whole box.
    if (sliced) {
      const plane = slicePlane(p.box, sliced.view, sliced.t);
      let corners;
      if (plane.k != null) { const z = plane.k + 1; corners = [[0, 0, z], [L, 0, z], [L, W, z], [0, W, z]]; }
      else if (plane.j != null) { const y = sliced.view === 'side' ? plane.j + 1 : plane.j; corners = [[0, y, 0], [L, y, 0], [L, y, H], [0, y, H]]; }
      else { const x = sliced.view === 'front' ? plane.i + 1 : plane.i; corners = [[x, 0, 0], [x, W, 0], [x, W, H], [x, 0, H]]; }
      const sc = corners.map(([x, y, z]) => screen(x, y, z));
      g.beginPath();
      g.moveTo(sc[0][0], sc[0][1]);
      for (let n = 1; n < 4; n++) g.lineTo(sc[n][0], sc[n][1]);
      g.closePath();
      g.fillStyle = 'rgba(184,226,106,0.07)';
      g.fill();
      g.strokeStyle = 'rgba(184,226,106,0.8)';
      g.lineWidth = Math.max(1, dpr);
      g.setLineDash([4 * dpr, 3 * dpr]);
      g.stroke();
      g.setLineDash([]);
    }
  }
}

/** Whether (x, y) lies in a convex quad. */
function inside(pts, x, y) {
  let sign = 0;
  for (let n = 0; n < 4; n++) {
    const [ax, ay] = pts[n];
    const [bx, by] = pts[(n + 1) % 4];
    const cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax);
    if (cross === 0) continue;
    if (sign === 0) sign = Math.sign(cross);
    else if (Math.sign(cross) !== sign) return false;
  }
  return true;
}
