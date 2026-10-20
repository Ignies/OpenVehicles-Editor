// The live preview: the vehicle driving the way it faces, as the game shows it moving: its wheels
// turning on their frames (the game's wheels_0 loop) and its body bobbing on the suspension over them
// (the game's CMUVehicleBob) while the ground scrolls under it. The thumbnails
// pick the direction; hovering one shows it still. Like the game, a part under the cursor gets a one
// pixel outline, and a door, hood, tailgate, hatch, boot lid or windshield clicked swings open (and shut again
// on a second click); the vehicle stops while one is open. Painting (or with Alt held), a click hands the
// voxel under it to onPaint instead.

const ORDER = ['S', 'E', 'N', 'W'];
const HEADING = { S: [0, 1], N: [0, -1], E: [1, 0], W: [-1, 0] };
const TILE = 32;
const GROUNDS = {
  dark: { base: '#0e1012', dots: ['#121518', '#101315'], seam: '#14171b' },
  checker: 'checker',
  grass: { base: '#3d5230', dots: ['#435935', '#39502d', '#4a6139'], seam: null },
  sand: { base: '#9d8a62', dots: ['#a59169', '#968359', '#ab986f'], seam: null },
  concrete: { base: '#6b6e70', dots: ['#727577', '#656869'], seam: '#5f6264' },
};
const FRAME = 96;
const OUT = 128;
const OUTLINE = 'rgba(160, 255, 150, 0.95)';
const SWING_STEP = 80;

const loadImage = (url) => new Promise((resolve) => {
  const img = new Image();
  img.onload = () => resolve(img);
  img.onerror = () => resolve(null);
  img.src = url;
});

export class VehiclePreview {
  constructor(stage, canvas, thumbs, dirLabel, partLabel, openChip) {
    this.stage = stage;
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.thumbs = thumbs;
    this.dirLabel = dirLabel;
    this.partLabel = partLabel;
    this.openChip = openChip;
    this.images = {};
    this.parts = {};
    this.keys = {};            // direction -> the voxel each pixel shows: f, r, z as int16s, -32768 for none
    this.painting = false;
    this.drive = {};           // direction -> { frames, wheels, body }: whole frames, and split for the suspension
    this.layers = [];
    this.facing = 'S';
    this.driving = true;
    this.speed = 2.6;          // tiles a second
    this.wheelDelay = 100;     // ms a wheel frame
    this.wheelFrame = 0;
    this.nextWheel = 0;
    this.travel = [0, 0];      // how far it has driven, in sprite pixels
    this.ground = 'dark';
    this.frame = false;
    this.held = null;
    this.hoverLayer = -1;
    this.open = null;          // { layer, dir, frames }: a part swung open, held on its last frame
    this.animating = null;     // { frames, i, next, reverse, done }
    this.place = { x: 0, y: 0, scale: 1 };
    this.last = performance.now();
    this.groundTile = null;
    this.setGround('dark');
    new ResizeObserver(() => this.resize()).observe(stage);
    for (const t of thumbs) {
      t.addEventListener('mouseenter', () => { if (!this.open) { this.held = t.dataset.dir; this.draw(); } });
      t.addEventListener('mouseleave', () => { this.held = null; this.draw(); });
      t.addEventListener('click', () => { if (!this.open) this.setFacing(t.dataset.dir); });
    }
    canvas.addEventListener('mousemove', (e) => this.hover(e));
    canvas.addEventListener('mouseleave', () => this.unhover());
    canvas.addEventListener('click', (e) => this.click(e));
    requestAnimationFrame((t) => this.tick(t));
  }

  get dir() { return this.open ? this.open.dir : (this.held || this.facing); }

  get moving() { return this.driving && !this.open && !this.animating && !this.held; }

  setFacing(dir) {
    this.facing = dir;
    this.requestDrive?.(dir);
    this.draw();
  }

  setDriving(on) {
    this.driving = on;
    this.onPlayChange?.(on);
    this.draw();
  }

  resize() {
    const r = this.stage.getBoundingClientRect();
    this.canvas.width = Math.round(r.width * devicePixelRatio);
    this.canvas.height = Math.round(r.height * devicePixelRatio);
    this.draw();
  }

  async setRender(result, layers) {
    const loaded = await Promise.all(ORDER.map(async (d) => [d, await loadImage(result.images[d])]));
    for (const [d, img] of loaded) if (img) this.images[d] = img;
    for (const d of ORDER) {
      if (result.parts?.[d]) this.parts[d] = Uint8Array.from(atob(result.parts[d]), (c) => c.charCodeAt(0));
      if (result.keys?.[d]) this.keys[d] = new Int16Array(Uint8Array.from(atob(result.keys[d]), (c) => c.charCodeAt(0)).buffer);
    }
    this.layers = layers;
    // Driving frames and a part held open were drawn from the old render.
    this.drive = {};
    this.open = null;
    this.openChip.classList.remove('on');
    this.requestDrive?.(this.facing);
    this.draw();
    this.drawThumbs();
    this.stage.classList.remove('updated');
    void this.stage.offsetWidth;
    this.stage.classList.add('updated');
  }

  async setDrive(dir, r, delay) {
    const [frames, wheels, body] = await Promise.all([
      Promise.all(r.frames.map(loadImage)), Promise.all((r.wheels || []).map(loadImage)), r.body ? loadImage(r.body) : null,
    ]);
    if (frames.length && frames.every(Boolean)) {
      const split = body && wheels.length === frames.length && wheels.every(Boolean);
      this.drive[dir] = { frames, wheels: split ? wheels : null, body: split ? body : null };
    }
    if (delay) this.wheelDelay = delay;
  }

  /** How far the body rides up off its wheels now, in sprite pixels: as the game's CMUVehicleBob, a
   * few uneven waves summed and rounded, stronger the faster it goes. */
  bob(now) {
    const t = now / 1000;
    const wave = 0.55 * Math.sin(t * 10.7) + 0.3 * Math.sin(t * 17.3 + 1.3) + 0.25 * Math.sin(t * 26.9 + 0.7);
    const strength = Math.min(1, Math.max(0.4, this.speed / 6));
    return Math.round(wave * strength * 1.4);
  }

  setGround(name) {
    this.ground = name;
    const g = GROUNDS[name];
    if (g && g !== 'checker') {
      // A tile of the ground at sprite scale: speckles and, for paving, its seams, so it shows moving.
      const tile = document.createElement('canvas');
      tile.width = tile.height = TILE;
      const t = tile.getContext('2d');
      t.fillStyle = g.base;
      t.fillRect(0, 0, TILE, TILE);
      let seed = 7;
      const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
      for (let n = 0; n < 70; n++) {
        t.fillStyle = g.dots[n % g.dots.length];
        t.fillRect(Math.floor(rnd() * TILE), Math.floor(rnd() * TILE), 1, 1);
      }
      if (g.seam) {
        t.fillStyle = g.seam;
        t.fillRect(0, 0, TILE, 1);
        t.fillRect(0, 0, 1, TILE);
      }
      this.groundTile = tile;
    } else {
      this.groundTile = null;
    }
    this.draw();
    this.drawThumbs();
  }

  tick(now) {
    const dt = Math.min(0.1, (now - this.last) / 1000);
    this.last = now;
    let redraw = false;
    const a = this.animating;
    if (a && now >= a.next) {
      a.i++;
      a.next = now + SWING_STEP;
      if (a.i >= a.frames.length - 1) {
        this.animating = null;
        a.done();
      }
      redraw = true;
    }
    if (this.moving) {
      const [hx, hy] = HEADING[this.facing];
      const px = this.speed * TILE * dt;
      this.travel = [(this.travel[0] + hx * px) % (TILE * 64), (this.travel[1] + hy * px) % (TILE * 64)];
      if (now >= this.nextWheel) {
        this.wheelFrame = (this.wheelFrame + 1) % 4;
        this.nextWheel = now + this.wheelDelay;
      }
      redraw = true;
    }
    if (redraw) this.draw();
    requestAnimationFrame((t) => this.tick(t));
  }

  paintGround(ctx, w, h, scale, travel = [0, 0]) {
    if (this.ground === 'checker') {
      const s = 8 * scale;
      const ox = -Math.round(travel[0]) * scale;
      const oy = -Math.round(travel[1]) * scale;
      for (let y = (oy % (2 * s)) - 2 * s; y < h; y += s) {
        for (let x = (ox % (2 * s)) - 2 * s; x < w; x += s) {
          const cx = Math.round((x - ox) / s);
          const cy = Math.round((y - oy) / s);
          ctx.fillStyle = ((cx + cy) % 2 + 2) % 2 ? '#202327' : '#2a2d31';
          ctx.fillRect(x, y, s, s);
        }
      }
      return;
    }
    if (!this.groundTile) return;
    ctx.save();
    ctx.scale(scale, scale);
    // Whole sprite pixels, so the ground steps like the game's.
    const ox = -(((Math.round(travel[0]) % TILE) + TILE) % TILE);
    const oy = -(((Math.round(travel[1]) % TILE) + TILE) % TILE);
    ctx.translate(ox, oy);
    ctx.fillStyle = ctx.createPattern(this.groundTile, 'repeat');
    ctx.fillRect(0, 0, w / scale + TILE, h / scale + TILE);
    ctx.restore();
  }

  current() {
    if (this.animating) {
      const a = this.animating;
      const i = a.reverse ? a.frames.length - 1 - a.i : a.i;
      return a.frames[Math.max(0, Math.min(a.frames.length - 1, i))];
    }
    if (this.open) return this.open.frames[this.open.frames.length - 1];
    const d = !this.held && this.drive[this.facing];
    if (d && this.driving) return d.frames[this.wheelFrame % d.frames.length];
    return this.images[this.dir];
  }

  draw() {
    const ctx = this.ctx;
    const w = this.canvas.width;
    const h = this.canvas.height;
    ctx.imageSmoothingEnabled = false;
    // Fit the game's 96 px frame, not the larger render frame around it.
    const scale = Math.max(1, Math.floor(Math.min(w / (FRAME + 4), h / (FRAME + 4))));
    this.paintGround(ctx, w, h, scale, this.held ? [0, 0] : this.travel);
    const x = Math.floor((w - OUT * scale) / 2);
    const y = Math.floor((h - OUT * scale) / 2);
    this.place = { x, y, scale };
    // Driving: the body lifted on its suspension, the wheels on the ground.
    const d = this.moving && this.drive[this.facing];
    const lift = d?.body ? this.bob(performance.now()) : 0;
    if (d?.body) {
      ctx.drawImage(d.body, x, y - lift * scale, OUT * scale, OUT * scale);
      ctx.drawImage(d.wheels[this.wheelFrame % d.wheels.length], x, y, OUT * scale, OUT * scale);
    } else {
      const img = this.current();
      if (img) ctx.drawImage(img, x, y, OUT * scale, OUT * scale);
    }
    if (this.hoverLayer >= 0 && !this.animating && !this.open) {
      const up = this.layers[this.hoverLayer]?.wheel ? 0 : lift;
      this.drawOutline(this.hoverLayer, x, y - up * scale, scale);
    }
    if (this.frame) {
      ctx.strokeStyle = 'rgba(184,226,106,0.5)';
      ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
      ctx.lineWidth = devicePixelRatio;
      const f = (OUT - FRAME) / 2;
      ctx.strokeRect(x + f * scale + 0.5, y + f * scale + 0.5, FRAME * scale, FRAME * scale);
      ctx.setLineDash([]);
    }
    this.dirLabel.textContent = this.dir;
    for (const t of this.thumbs) t.classList.toggle('on', t.dataset.dir === this.dir);
  }

  /** The game's hover highlight: a one pixel rim just outside the part's pixels. */
  drawOutline(layer, x, y, scale) {
    const parts = this.parts[this.dir];
    if (!parts) return;
    const id = layer + 1;
    const ctx = this.ctx;
    ctx.fillStyle = OUTLINE;
    for (let py = 0; py < OUT; py++) {
      for (let px = 0; px < OUT; px++) {
        const n = py * OUT + px;
        if (parts[n] === id) continue;
        const near = (px > 0 && parts[n - 1] === id) || (px < OUT - 1 && parts[n + 1] === id) ||
          (py > 0 && parts[n - OUT] === id) || (py < OUT - 1 && parts[n + OUT] === id);
        if (near) ctx.fillRect(x + px * scale, y + py * scale, scale, scale);
      }
    }
  }

  /** The sprite pixel under the pointer, or null. */
  pixelAt(e) {
    const r = this.canvas.getBoundingClientRect();
    const cx = (e.clientX - r.left) * devicePixelRatio;
    const cy = (e.clientY - r.top) * devicePixelRatio;
    const { x, y, scale } = this.place;
    const px = Math.floor((cx - x) / scale);
    const py = Math.floor((cy - y) / scale);
    return px < 0 || py < 0 || px >= OUT || py >= OUT ? null : py * OUT + px;
  }

  layerAt(e) {
    const n = this.pixelAt(e);
    const parts = this.parts[this.dir];
    return parts && n != null ? parts[n] - 1 : -1;
  }

  /** The voxel the pixel under the pointer shows, [f, r, z], or null. */
  keyAt(e) {
    const n = this.pixelAt(e);
    const keys = this.keys[this.dir];
    if (!keys || n == null || keys[n * 3] === -32768) return null;
    return [keys[n * 3], keys[n * 3 + 1], keys[n * 3 + 2]];
  }

  /** Whether a click paints: in painting mode, or with Alt held, while nothing swings. */
  paints(e) { return (this.painting || e.altKey) && !this.open && !this.animating; }

  hover(e) {
    if (this.animating) return;
    const layer = this.open ? this.open.layer : this.layerAt(e);
    if (layer !== this.hoverLayer) {
      this.hoverLayer = layer;
      this.onHoverLayer?.(layer);
      this.draw();
    }
    const info = this.layers[layer];
    const paint = this.paints(e);
    this.stage.classList.toggle('hovering', !!info?.hinged && !paint);
    if (info) {
      const r = this.stage.getBoundingClientRect();
      const key = paint && info.voxels ? this.keyAt(e) : null;
      const act = paint ? (info.voxels ? 'click to paint' : '') : info.hinged ? (this.open ? 'click to shut' : 'click to open') : '';
      this.partLabel.innerHTML = `${info.name}${key ? `<span class="key">${key.join(' ')}</span>` : ''}${act ? `<span class="act">${act}</span>` : ''}`;
      this.partLabel.style.left = `${Math.min(r.width - Math.max(150, this.partLabel.offsetWidth + 6), e.clientX - r.left + 14)}px`;
      this.partLabel.style.top = `${e.clientY - r.top + 14}px`;
      this.partLabel.classList.add('on');
    } else {
      this.partLabel.classList.remove('on');
    }
  }

  unhover() {
    if (this.hoverLayer !== -1 && !this.open) {
      this.hoverLayer = -1;
      this.onHoverLayer?.(-1);
      this.draw();
    }
    this.partLabel.classList.remove('on');
    this.stage.classList.remove('hovering');
  }

  async click(e) {
    if (this.animating) return;
    if (this.paints(e)) {
      const layer = this.layerAt(e);
      if (layer >= 0) this.onPaint?.(layer, this.keyAt(e));
      return;
    }
    if (this.open) {
      // Swing it shut, then drive on.
      const { frames } = this.open;
      this.animating = {
        frames, i: 0, next: performance.now(), reverse: true,
        done: () => { this.open = null; this.openChip.classList.remove('on'); this.draw(); },
      };
      return;
    }
    const layer = this.hoverLayer;
    const info = this.layers[layer];
    if (!info?.hinged || !this.requestSwing) return;
    const dir = this.dir;
    const urls = await this.requestSwing(layer, dir);
    if (!urls?.length) return;
    const frames = (await Promise.all(urls.map(loadImage))).filter(Boolean);
    this.held = null;
    this.open = { layer, dir, frames };
    this.animating = {
      frames, i: 0, next: performance.now(), reverse: false,
      done: () => { this.openChip.textContent = `${info.name} open · click to shut`; this.openChip.classList.add('on'); },
    };
    this.draw();
  }

  drawThumbs() {
    for (const t of this.thumbs) {
      const c = t.querySelector('canvas');
      const r = c.getBoundingClientRect();
      c.width = Math.round(r.width * devicePixelRatio);
      c.height = Math.round(r.height * devicePixelRatio);
      const ctx = c.getContext('2d');
      ctx.imageSmoothingEnabled = false;
      this.paintGround(ctx, c.width, c.height, 1);
      const img = this.images[t.dataset.dir];
      if (!img) continue;
      // Crop to the sprite's middle so it fills the thumbnail.
      const s = Math.max(1, Math.floor(Math.min(c.width / 80, c.height / 60)));
      const sw = c.width / s;
      const sh = c.height / s;
      ctx.drawImage(img, (OUT - sw) / 2, (OUT - sh) / 2, sw, sh, 0, 0, c.width, c.height);
    }
  }
}
