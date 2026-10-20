"""How a vehicle is seen in each of the game's four sprite directions: its camera.

A project's camera (project["camera"], or its vehicle block's "camera") is one of:

    {"mode": "generator"}                 the generator's own views, as the jeep is drawn (the default)
    {"mode": "single", "view": "S"}       one direction's view drawn for all four: the vehicle looks
                                          the same whichever way it faces
    {"mode": "custom", "dirs": {"S": {"pitch": 64, "yaw": 0, "zoom": 1.16, "stretch": 1}, ...}}
                                          a camera of its own per direction: how far above the horizon
                                          it looks down (pitch, degrees), how far the vehicle is turned
                                          on top of facing that way (yaw, degrees, anticlockwise from
                                          above), its scale (zoom, pixels per voxel) and how much wider
                                          than tall it draws (stretch); a direction left out keeps the
                                          generator's look (default_dirs)
    {"mode": "sprite", "nose": "N"}       a picture drawn as a finished sprite (sprite.py): seen from
                                          straight above, a voxel a pixel, the nose pointing the way
                                          given, the same whichever way it faces

The generator draws with a hand tuned oblique projection per direction (generator.render); a custom
camera draws with an orthographic one of its own (raycast), into the same buffer the generator's
renderer fills, so the generator's shading, outlines, dirt and paint, and the tool's click masks and
crayon maps, work on it unchanged.

While a project's art is drawn, voxels.styled() puts its camera on the generator as gen.camera; the
renderer, the riders' placing and the seats read it from there through render(), project(), depth(),
dy() and hip().
"""

import math

import numpy as np

DIRS = "SNEW"
MODES = ("generator", "single", "custom", "sprite")
# The sprite camera: straight down, a voxel a pixel.
TOP_DOWN = {"pitch": 90.0, "yaw": 0.0, "zoom": 1.0, "stretch": 1.0}
# The way a vehicle facing each direction points, on the map (x east, y north).
HEADING = {"S": (0.0, -1.0), "N": (0.0, 1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}
# How finely a ray is stepped through the voxels, in voxels.
STEP = 0.2
# A rider's hips, in pixels down from the top of their 32 px sprite, for a custom camera's directions.
HIP = 18


class Camera:
    def __init__(self, spec, box=None):
        spec = spec or {}
        self.mode = spec.get("mode") if spec.get("mode") in MODES else "generator"
        self.view = spec.get("view") if spec.get("view") in tuple(DIRS) else "S"
        if self.mode == "sprite":
            self.view = spec.get("nose") if spec.get("nose") in tuple(DIRS) else "N"
        self.dirs = {d: dict(v) for d, v in (spec.get("dirs") or {}).items() if d in tuple(DIRS) and isinstance(v, dict)}
        self.box = box

    @property
    def plain(self):
        return self.mode == "generator"

    @property
    def one(self):
        """Whether every direction draws the same picture."""
        return self.mode in ("single", "sprite")

    def letter(self, direction):
        """The view a direction is drawn with: its own, or the single view's (a sprite's nose)."""
        return self.view if self.one else direction

    def custom(self, direction):
        return self.mode in ("custom", "sprite")

    def key(self):
        """What a cache of drawings must tell apart."""
        if self.mode == "generator":
            return "g"
        if self.mode in ("single", "sprite"):
            return f"{self.mode[:2]}{self.view}"
        return "c" + repr(sorted((d, sorted(v.items())) for d, v in self.dirs.items()))


def resolve(project, box=None):
    """A project's camera (Camera), from project["camera"] or its vehicle block's."""
    spec = project.get("camera") or (project.get("vehicle") or {}).get("camera")
    cam = Camera(spec, box)
    # A sprite's pictures, which its drawings are coloured with (sprite.py).
    cam.sprite = project.get("sprite") if cam.mode == "sprite" else None
    return cam


def default_dirs(g):
    """The custom camera that draws most like the generator: per direction, the pitch and zoom of its
    depth and height scales, and the stretch that keeps its width (its side views stretched by their
    length scale) as the generator draws it."""
    out = {}
    for d in DIRS:
        depth = g.DEPTH_SCALE.get(d, 1.0)
        height = g.HEIGHT_SCALE.get(d, 1.0)
        zoom = math.hypot(depth, height)
        out[d] = {"pitch": round(math.degrees(math.atan2(depth, height)), 1), "yaw": 0.0,
                  "zoom": round(zoom, 3), "stretch": round(g.LENGTH_SCALE.get(d, 1.0) / zoom, 3)}
    return out


def params(gen, cam, direction):
    if cam.mode == "sprite":
        return dict(TOP_DOWN)
    base = default_dirs(gen.module)[direction]
    own = cam.dirs.get(direction) or {}
    out = dict(base)
    for k in ("pitch", "yaw", "zoom", "stretch"):
        try:
            if own.get(k) is not None:
                out[k] = float(own[k])
        except (TypeError, ValueError):
            pass
    out["pitch"] = min(90.0, max(0.0, out["pitch"]))
    out["zoom"] = min(4.0, max(0.25, out["zoom"]))
    out["stretch"] = min(3.0, max(0.25, out["stretch"]))
    return out


class Axes:
    """A custom camera's directions in vehicle space (f forward, r right, z up): where a screen pixel
    is (right, up), the way it looks (view), and its scale."""

    def __init__(self, p, direction):
        hx, hy = HEADING[direction]
        yaw = math.radians(p["yaw"])
        c, s = math.cos(yaw), math.sin(yaw)
        hx, hy = hx * c - hy * s, hx * s + hy * c
        # The vehicle's forward and right on the map; the right of facing north is east.
        fwd = np.array([hx, hy, 0.0])
        right = np.array([hy, -hx, 0.0])
        pitch = math.radians(p["pitch"])
        # The camera looks north and down from the south, as the game's does.
        view_w = np.array([0.0, math.cos(pitch), -math.sin(pitch)])
        up_w = np.array([0.0, math.sin(pitch), math.cos(pitch)])
        side_w = np.array([1.0, 0.0, 0.0])
        # The same axes in vehicle space: a vector's (f, r, z) is its dot with fwd, right and z.
        basis = np.array([fwd, right, [0.0, 0.0, 1.0]])
        self.view = basis @ view_w
        self.up = basis @ up_w
        self.side = basis @ side_w
        self.zoom = p["zoom"]
        self.stretch = p["stretch"]
        # Depth per screen row up a flat top: the generator's shading allows that much step there.
        self.row_depth = 1 / max(1e-6, self.zoom * math.tan(max(pitch, 1e-6)))

    def screen(self, q):
        """A point in vehicle space on the screen: (x, y down), and its depth along the view."""
        q = np.asarray(q, float)
        return (q @ self.side) * self.zoom * self.stretch, -(q @ self.up) * self.zoom, q @ self.view


def raycast(gen, direction, p, model, parts=None):
    """The voxels drawn from a custom camera: the generator's render() buffers, {(x, y): (depth, mat,
    part, face, voxel)} for the solid surface each pixel shows and the same for the glass in front of
    it, with "top" for a face seen from above and "front" for one seen from the side."""
    g = gen.module
    buf = g.Buffer()
    glass = g.Buffer()
    view_key = f"cam{direction}"
    buf.view = glass.view = view_key
    items = [(k, v) for k, v in model.vox.items() if parts is None or v[1] in parts]
    if not items:
        return buf, glass
    ax = Axes(p, direction)
    # The generator's shading reads how far a flat top steps in depth per row from its view.
    g.DEPTH_SCALE[view_key] = 1 / ax.row_depth if ax.row_depth > 1 else 1.0
    keys = np.array([k for k, _ in items], np.int32)
    lo = keys.min(0)
    shape = keys.max(0) - lo + 1
    grid = np.full(shape, -1, np.int32)
    grid[tuple((keys - lo).T)] = np.arange(len(items))
    is_glass = np.array([v[0] == g.GLASS for _, v in items])

    # The screen the box of voxels covers, and every pixel's ray through it.
    corners = np.array([[x, y, z] for x in (lo[0], lo[0] + shape[0]) for y in (lo[1], lo[1] + shape[1])
                        for z in (lo[2], lo[2] + shape[2])], float)
    sx, sy, sd = ax.screen(corners)
    xs = np.arange(math.floor(sx.min()) - 1, math.ceil(sx.max()) + 1)
    ys = np.arange(math.floor(sy.min()) - 1, math.ceil(sy.max()) + 1)
    px, py = np.meshgrid(xs, ys)
    px, py = px.ravel(), py.ravel()
    near, far = sd.min() - 1, sd.max() + 1
    # A pixel's centre on the plane at depth `near`, in vehicle space.
    origin = (np.outer((px + 0.5) / (ax.zoom * ax.stretch), ax.side)
              + np.outer(-(py + 0.5) / ax.zoom, ax.up) + near * ax.view)
    n = len(px)
    hit = np.full(n, -1, np.int64)
    hit_t = np.zeros(n)
    glass_hit = np.full(n, -1, np.int64)
    glass_t = np.zeros(n)
    active = np.ones(n, bool)
    t = 0.0
    while t <= far - near and active.any():
        idx = np.nonzero(active)[0]
        q = origin[idx] + t * ax.view
        cell = np.floor(q).astype(np.int64) - lo
        inside = np.all((cell >= 0) & (cell < shape), axis=1)
        if inside.any():
            ii = idx[inside]
            c = cell[inside]
            v = grid[c[:, 0], c[:, 1], c[:, 2]]
            found = v >= 0
            if found.any():
                fi, fv = ii[found], v[found]
                g_mask = is_glass[fv]
                if g_mask.any():
                    gf, gv = fi[g_mask], fv[g_mask]
                    fresh = glass_hit[gf] < 0
                    glass_hit[gf[fresh]] = gv[fresh]
                    glass_t[gf[fresh]] = t
                solid = fi[~g_mask]
                if len(solid):
                    hit[solid] = fv[~g_mask]
                    hit_t[solid] = t
                    active[solid] = False
        t += STEP

    def entry(i, item, step_t):
        """Where the ray enters the voxel it hits, and through which face."""
        k = np.array(item[0], float)
        o = origin[i]
        best_t, axis = -math.inf, 2
        for a in range(3):
            d = ax.view[a]
            if abs(d) < 1e-9:
                continue
            bound = k[a] if d > 0 else k[a] + 1
            ta = (bound - o[a]) / d
            if ta > best_t:
                best_t, axis = ta, a
        if not math.isfinite(best_t):
            best_t = step_t
        return near + best_t, ("top" if axis == 2 else "front")

    for i in np.nonzero(hit >= 0)[0]:
        item = items[hit[i]]
        depth, face = entry(i, item, hit_t[i])
        buf[(int(px[i]), int(py[i]))] = (depth, item[1][0], item[1][1], face, tuple(int(c) for c in item[0]))
    for i in np.nonzero(glass_hit >= 0)[0]:
        item = items[glass_hit[i]]
        depth, face = entry(i, item, glass_t[i])
        solid = buf.get((int(px[i]), int(py[i])))
        if solid is None or depth < solid[0]:
            glass[(int(px[i]), int(py[i]))] = (depth, item[1][0], item[1][1], face, tuple(int(c) for c in item[0]))
    return buf, glass


# -- what the tool reads, whichever the camera ---------------------------------------------------------

def of(gen):
    return getattr(gen, "camera", None) or Camera(None)


def render(gen, direction, model, parts=None):
    """A direction's drawing of a model: the generator's render, or the custom camera's."""
    cam = of(gen)
    if cam.custom(direction):
        return raycast(gen, cam.letter(direction), params(gen, cam, direction), model, parts)
    return gen.module.render(model, cam.letter(direction), parts)


def project(gen, direction, f, r, z):
    """Where a point in vehicle space lands on a direction's drawing, before dy()."""
    cam = of(gen)
    if cam.custom(direction):
        x, y, _ = Axes(params(gen, cam, direction), cam.letter(direction)).screen([f, r, z])
        return float(x), float(y)
    return gen.module.project(cam.letter(direction), f, r, z)


def depth(gen, direction, f, r, z=0.0):
    """How far into a direction's drawing a point is, as its buffer measures depth."""
    cam = of(gen)
    if cam.custom(direction):
        return float(Axes(params(gen, cam, direction), cam.letter(direction)).screen([f, r, z])[2])
    g = gen.module
    view = cam.letter(direction)
    return g.to_world(view, f, r)[1] * g.DEPTH_SCALE[view]


def dy(gen, direction):
    """How far down a direction's drawing is moved to sit in its frame: the generator's for its own
    views; a custom camera's drawing is centred on the box's middle."""
    cam = of(gen)
    if cam.custom(direction):
        box = cam.box
        if box is None:
            return 0
        x, y, _ = Axes(params(gen, cam, direction), cam.letter(direction)).screen([0.0, 0.0, box.height / 2])
        return -round(float(y))
    return gen.dy[cam.letter(direction)]


def hip(gen, direction):
    """A rider's hip row in a direction (RIDER_HIP_ROW)."""
    cam = of(gen)
    if cam.custom(direction):
        return HIP
    rows = getattr(gen.module, "RIDER_HIP_ROW", {"S": 19, "N": 20, "E": 18, "W": 18})
    return rows[cam.letter(direction)]


def far_wheels(gen, direction):
    """Whether the generator's far wheels are added: only to its own views."""
    return not of(gen).custom(direction)
