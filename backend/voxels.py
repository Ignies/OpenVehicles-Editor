"""Layered orthographic drawings to voxels, and voxels to game sprites.

A vehicle is a stack of layers: its body, wheels, doors, hood, windshield, lamps and so on. Each
layer is drawn in orthographic views at one pixel per voxel, in the generator's space (f forward,
r towards the vehicle's right, z up):

- top:   seen from above, nose to the right. Columns are f, rows are r (its left side at the top).
- side:  seen from its right, nose to the right. Columns are f, rows are z (top row highest).
- left:  seen from its left, nose to the left. Columns are f backwards, rows are z.
- front: seen from ahead. Columns are r (its right side on the left), rows are z.
- back:  seen from behind. Columns are r (its right side on the right), rows are z.

A layer on both sides takes its right side from the side view and its left from the left view; with
the left view blank, its left mirrors its right. A layer on one side uses that side's view. (A layer
on the left drawn before there was a left view has its drawing in the side view, nose right, and
still works.)

Each pixel holds a material (or nothing) and a depth: how far into the box, from that view's side,
its surface lies. A layer starts as the whole box and every view drawn on carves it: nothing along
the line through an empty pixel, nothing in front of a surface, and, for a layer with a thickness,
nothing more than that deep behind it. A view left blank doesn't limit the layer. Layers later in the
stack win where they overlap. Each voxel takes its colour from the view that sees it, picked per
sprite direction, and the generator's own renderer and shading draw the sprites.

A voxel layer keeps its voxels themselves, each with its own material, so it can hold what views
can't: a cockpit behind a door, a dash under a windshield, wheel wells, a door's inner skin. Its views
are only what it looks like from each side; what's drawn on them is applied along each pixel's line
of sight, as on a layer loaded from the generator.

A layer's damage states are drawings of their own: layers linked to it, each a level of a named
state with a damage type (bullet holes from piercing at 20, 40, 60, 80 and 100), shown once the
part has taken that much of it. A stage left out of the stack shows in its layer's place, as that
layer, when it's the one shown.
"""

import base64
import contextlib
import importlib.util
import io
import math
import os
import sys
import time

import numpy as np
from PIL import Image

EMPTY = 255
OUT = 128
VIEWS = ("top", "side", "left", "front", "back")
GLASS = "glass"



class Box:
    """The drawing's box in generator space: f in [-length/2, length/2), r likewise, z in [0, height)."""

    def __init__(self, length, width, height):
        self.length, self.width, self.height = int(length), int(width), int(height)
        self.f0, self.r0, self.z0 = -(self.length // 2), -(self.width // 2), 0

    @classmethod
    def from_dict(cls, d):
        return cls(d["length"], d["width"], d["height"])

    def shape(self, view):
        L, W, H = self.length, self.width, self.height
        return {"top": (W, L), "side": (H, L), "left": (H, L), "front": (H, W), "back": (H, W)}[view]

    def as_dict(self):
        return {"length": self.length, "width": self.width, "height": self.height}


# The game's sprite frame: every direction of an open vehicle is drawn in a square this size.
FRAME = 96


def room(gen, box, margin=1):
    """The biggest box the vehicle can grow to, an axis at a time, before the middle of a face of it
    would be drawn past the game's 96 px frame (less a margin, for the hover outline) in any
    direction: its length and width grow on both sides, its height upwards. A box's corners are
    mostly empty, so they don't count; what's drawn there is checked on export."""
    g = gen.module

    def inside(points):
        for view in "SNEW":
            for f, r, z in points:
                sx, sy = g.project(view, f, r, z)
                x, y = FRAME / 2 + sx, FRAME / 2 + gen.dy[view] + sy
                if not (margin <= x <= FRAME - margin and margin <= y <= FRAME - margin):
                    return False
        return True

    # Each axis on its own: the middles of the two faces it moves (the top, for the height).
    L, W, H = box.length, box.width, box.height
    ends = lambda n: (-(n // 2), n - n // 2)  # noqa: E731
    length = lambda n: [(f, 0, z) for f in ends(n) for z in (0, H)]  # noqa: E731
    width = lambda n: [(0, r, z) for r in ends(n) for z in (0, H)]  # noqa: E731
    height = lambda n: [(0, 0, n), (ends(L)[1] - 1, 0, n), (ends(L)[0], 0, n)]  # noqa: E731
    out = {"length": L, "width": W, "height": H}
    while inside(length(out["length"] + 2)):
        out["length"] += 2
    while inside(width(out["width"] + 2)):
        out["width"] += 2
    while inside(height(out["height"] + 1)):
        out["height"] += 1
    return out
    while fits(out["length"] + 2, W, H):
        out["length"] += 2
    while fits(L, out["width"] + 2, H):
        out["width"] += 2
    while fits(L, W, out["height"] + 1):
        out["height"] += 1
    return out


class Generator:
    """A generator script, imported fresh from its file, with what the tool needs from it."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.mtime = os.path.getmtime(self.path)
        name = f"cmuov_generator_{int(time.time() * 1000)}"
        spec = importlib.util.spec_from_file_location(name, self.path)
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(self.path))
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.pop(0)
        self.module = module
        self.cache = {}
        self.dy = module.view_dy()
        self.wheel_frames = int(getattr(module, "WHEEL_FRAMES", 4))
        self.wheel_delay = float(getattr(module, "WHEEL_DELAY", 0.1))
        self.materials = [n for n, ramp in module.PAL.items() if isinstance(ramp, dict) and "top" in ramp]
        self.materials.append(GLASS)

    def palette(self):
        """The materials in their order (a project's own last), each with its tones."""
        tint = list(getattr(self.module, "GLASS_TINT", (176, 206, 204, 78))[:3])
        return [{"name": n, "tones": ({k: tint for k in ("hi", "top", "front", "dark", "line")} if n == GLASS
                                      else {k: list(v[:3]) for k, v in self.module.PAL[n].items()})}
                for n in self.materials]

    def add_materials(self, specs):
        """A project's own materials ([{name, color: "#rrggbb"}]) added to the palette, each shaded
        from its colour the way the generator's are: a lit lip, the top, the faces, their shadow and
        an outline. Unpainted: the paint shader leaves them as they are."""
        for spec in specs or []:
            name = spec.get("name")
            hexa = (spec.get("color") or "#808080").lstrip("#")
            if not name or len(hexa) != 6:
                continue
            c = tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))

            def tone(k, lift=0.0):
                return tuple(int(min(255, max(0, v * k + (255 - v) * lift))) for v in c) + (255,)

            self.module.PAL[name] = {"hi": tone(1.0, 0.22), "top": tone(1.0), "front": tone(0.82),
                                     "dark": tone(0.64), "line": tone(0.42)}
            if name not in self.materials:
                self.materials.append(name)

    def layers(self, variant):
        """A generator vehicle split into its layers, shut, with its parts fitted. Each layer keeps
        its generator part as its role, so the renderer's own handling of it carries over (the
        wheels' round faces, the windshield's height, lamps over the grille), and the kit hung on a
        panel rides on that panel's layer."""
        g = self.module
        out = []

        def add(name, kind, vox, sides=None, role=None, source=None):
            if not vox:
                return
            if sides is None:
                rs = [k[1] for k in vox]
                sides = "left" if max(rs) < 0 else "right" if min(rs) >= 0 else "both"
            out.append({"name": name, "kind": kind, "sides": sides, "thickness": 0, "vox": vox,
                        "role": role, "rides": None, "native": dict(source or {}, variant=variant)})

        add("Body", "body", g.build(variant, "S").vox, "both", source={"source": "body"})
        if "engine" in g.HARDPOINTS:
            add("Engine", "part", g.HARDPOINTS["engine"](False).vox, "both", "engine",
                {"source": "hardpoint", "name": "engine"})
        add("Hood", "hood", g.hood_lid(variant).vox, "both", "hood_lid", {"source": "hood"})
        # A true 3D wheel set to draw from (the generator's side view disc, at the real axle places);
        # the game's wheels are drawn per direction (see native()).
        wheels = g.Model()
        for fc in (g.FRONT_AXLE, g.REAR_AXLE):
            for r0, r1 in g.WHEEL_SPAN:
                g.wheel(wheels, fc, g.WHEEL_Z, r0, r1, 0.0, g.inside_tub)
        add("Wheels", "wheel", wheels.vox, "both", "wheel", {"source": "wheels"})
        panels = g.panels_closed(variant).vox
        panel_names = {"door_driver": "Driver door", "door_passenger": "Passenger door", "tailgate": "Tailgate"}
        for part, name in panel_names.items():
            add(name, "tailgate" if part == "tailgate" else "door", {k: v for k, v in panels.items() if v[1] == part},
                role=part, source={"source": "panel", "part": part})
        if hasattr(g, "fuel_door"):
            add("Fuel door", "fueldoor", g.fuel_door(0).vox, role="fuel_door", source={"source": "fueldoor"})
        fitted = getattr(g, "FITTED", ())
        names = {"windshield_up": ("Windshield", "windshield"), "spare": ("Spare wheel", "part"),
                 "jerrycan": ("Jerry can", "part"), "shovel": ("Shovel", "part"), "axe": ("Axe", "part"),
                 "jack": ("Jack", "part"), "extinguisher": ("Extinguisher", "part")}
        rides = getattr(g, "RIDES", {}).get(variant, {})
        for hp in fitted:
            if hp == "engine" or hp not in g.HARDPOINTS:
                continue
            name, kind = names.get(hp, (hp.replace("_", " ").capitalize(), "lamp"))
            vox = g.HARDPOINTS[hp](False).vox
            add(name, kind, vox, role=next(iter(vox.values()))[1] if vox else hp,
                source={"source": "hardpoint", "name": hp})
            if hp in rides and out and out[-1]["name"] == name:
                out[-1]["rides"] = panel_names.get(rides[hp])
        return out

    def swing_angles(self, kind):
        """The angles a kind of layer swings through, frame by frame: the generator's own, so the
        tool animates like the game."""
        g = self.module
        table = {
            "door": getattr(g, "DOOR_SWING", (0, 22, 45, 62, 70)),
            "fueldoor": getattr(g, "FUEL_DOOR_SWING", (0, 35, 70, 100)),
            "hood": getattr(g, "HOOD_SWING", (0, 9, 24, 42, 58, 72, 80)),
            "tailgate": getattr(g, "TAILGATE_SWING", (0, 18, 45, 72, 90)),
            "hatch": (0, 18, 42, 66, 85),
            "boot": getattr(g, "HOOD_SWING", (0, 9, 24, 42, 58, 72, 80)),
            # The windshield folds from its upright lean down onto the hood.
            "windshield": tuple(max(getattr(g, "WINDSHIELD_FOLD", (105,))) - a
                                for a in getattr(g, "WINDSHIELD_FOLD", (105, 84, 58, 30, 0))),
        }
        return table.get(kind)

    def flat(self, mat):
        """A swung panel's material: resampled at a slant its voxels step in depth pixel to pixel and
        would each get an outline, so the generator shades it as one flat surface."""
        return getattr(self.module, "PANEL_FLAT", {}).get(mat, mat)


# -- voxels to views ------------------------------------------------------------------------------

def drawing_from_voxels(vox, box, materials, sides="both"):
    """A layer's views of its voxels, as the app sends them: for each pixel the first voxel seen and
    its depth. A layer on one side gets only that side's view."""
    return {view: {"mat": bytes_b64(m), "depth": bytes_b64(d)}
            for view, (m, d) in drawing_arrays(vox, box, materials, sides).items()}


def drawing_arrays(vox, box, materials, sides="both"):
    """A layer's views of its voxels: per view, the material and depth grids."""
    index = {m: i for i, m in enumerate(materials)}
    L, W, H = box.length, box.width, box.height
    best = {v: {} for v in VIEWS}
    right, left = sides != "left", sides != "right"
    for (f, r, z), (mat, _) in vox.items():
        i, j, k = f - box.f0, r - box.r0, z - box.z0
        if not (0 <= i < L and 0 <= j < W and 0 <= k < H) or mat not in index:
            continue
        m = index[mat]
        seen = [("top", (j, i), H - 1 - k), ("front", (H - 1 - k, W - 1 - j), L - 1 - i), ("back", (H - 1 - k, j), i)]
        if right:
            seen.append(("side", (H - 1 - k, i), W - 1 - j))
        if left:
            seen.append(("left", (H - 1 - k, L - 1 - i), j))
        for view, key, d in seen:
            cur = best[view].get(key)
            if cur is None or d < cur[0]:
                best[view][key] = (d, m)
    out = {}
    for view in VIEWS:
        rows, cols = box.shape(view)
        mat = np.full((rows, cols), EMPTY, np.uint8)
        depth = np.zeros((rows, cols), np.uint8)
        for (y, x), (d, m) in best[view].items():
            mat[y, x] = m
            depth[y, x] = min(d, 255)
        out[view] = (mat, depth)
    return out


# -- views to voxels ------------------------------------------------------------------------------

def _grid(views, box, view):
    rows, cols = box.shape(view)
    data = (views or {}).get(view)
    if not data:
        return np.full((rows, cols), EMPTY, np.uint8), np.zeros((rows, cols), np.int32)

    def decode(v, dtype):
        # The app sends base64 bytes; lists are accepted too.
        if isinstance(v, str):
            return np.frombuffer(base64.b64decode(v), np.uint8).astype(dtype)
        return np.array(v, dtype)

    return (decode(data["mat"], np.uint8).reshape(rows, cols),
            decode(data["depth"], np.int32).reshape(rows, cols))


def layer_grids(layer, box):
    """A layer's views as grids. A layer on the left drawn before there was a left view has its
    drawing in the side view, seen from its left but nose right: it comes back as its left view."""
    grids = {v: _grid(layer.get("views"), box, v) for v in VIEWS}
    m, d = grids["side"]
    if layer.get("sides") == "left" and (grids["left"][0] == EMPTY).all() and (m != EMPTY).any():
        grids["left"] = (m[:, ::-1], d[:, ::-1])
        grids["side"] = (np.full_like(m, EMPTY), np.zeros_like(d))
    return grids


def carve(layer, box):
    """The voxels a layer's views leave standing, and each view's materials in voxel space."""
    L, W, H = box.length, box.width, box.height
    sides = layer.get("sides", "both")
    thick = int(layer.get("thickness") or 0)
    views = layer.get("views")
    ii = np.arange(L)[:, None, None]
    jj = np.arange(W)[None, :, None]
    kk = np.arange(H)[None, None, :]
    occ = np.ones((L, W, H), bool)
    mats = {}

    top_m, top_d = _grid(views, box, "top")                      # [j, i]
    if (top_m != EMPTY).any():
        m, d = top_m.T[:, :, None], top_d.T[:, :, None]
        surface = H - 1 - d
        occ &= (m != EMPTY) & (kk <= surface)
        if thick:
            occ &= kk > surface - thick
        mats["top"] = top_m.T[:, :, None]

    side_m, side_d = _grid(views, box, "side")                   # [H-1-k, i], from the right
    left_m, left_d = _grid(views, box, "left")                   # [H-1-k, L-1-i], from the left
    has_side, has_left = (side_m != EMPTY).any(), (left_m != EMPTY).any()
    if sides == "left" and not has_left and has_side:
        # Drawn before there was a left view: seen from its left, but nose right, in the side view.
        left_m, left_d, has_left = side_m[:, ::-1], side_d[:, ::-1], True
    has_side &= sides != "left"
    has_left &= sides != "right"
    if has_side:
        m, d = side_m[::-1, :].T[:, None, :], side_d[::-1, :].T[:, None, :]
        occ &= m != EMPTY
        right = W - 1 - d
        mats["side"] = m
    if has_left:
        m, d = left_m[::-1, ::-1].T[:, None, :], left_d[::-1, ::-1].T[:, None, :]
        occ &= m != EMPTY
        left = d
        mats["left"] = m
    if has_side or has_left:
        if sides == "right":
            keep = (jj <= right) & ((jj > right - thick) if thick else True)
        elif sides == "left":
            keep = (jj >= left) & ((jj < left + thick) if thick else True)
        else:
            # A blank side mirrors the other.
            if not has_left:
                left, mats["left"] = W - 1 - right, mats["side"]
            if not has_side:
                right, mats["side"] = W - 1 - left, mats["left"]
            keep = (jj <= right) & (jj >= left)
            if thick:
                keep &= (jj > right - thick) | (jj < left + thick)
        occ &= keep

    front_m, front_d = _grid(views, box, "front")                # [H-1-k, W-1-j]
    if (front_m != EMPTY).any():
        m, d = front_m[::-1, ::-1].T[None, :, :], front_d[::-1, ::-1].T[None, :, :]
        surface = L - 1 - d
        occ &= (m != EMPTY) & (ii <= surface)
        if thick:
            occ &= ii > surface - thick
        mats["front"] = front_m[::-1, ::-1].T[None, :, :]

    back_m, back_d = _grid(views, box, "back")                   # [H-1-k, j]
    if (back_m != EMPTY).any():
        m, d = back_m[::-1, :].T[None, :, :], back_d[::-1, :].T[None, :, :]
        occ &= (m != EMPTY) & (ii >= d)
        if thick:
            occ &= ii < d + thick
        mats["back"] = back_m[::-1, :].T[None, :, :]

    if not mats:
        occ[:] = False
    return occ, mats


# -- voxel layers ---------------------------------------------------------------------------------
#
# A voxel layer's grid covers the box: ((i * W) + j) * H + k for i = f - f0, j = r - r0, k = z, each a
# material's index or EMPTY. It's saved run-length coded: each run its length as an unsigned LEB128
# varint, then its value, the lot in base64.

def rle_encode(grid):
    flat = np.ascontiguousarray(grid, np.uint8).ravel()
    out = bytearray()
    if flat.size:
        starts = np.concatenate(([0], np.flatnonzero(np.diff(flat)) + 1))
        lengths = np.diff(np.concatenate((starts, [flat.size])))
        for n, v in zip(lengths.tolist(), flat[starts].tolist()):
            while n >= 0x80:
                out.append((n & 0x7f) | 0x80)
                n >>= 7
            out.append(n)
            out.append(v)
    return base64.b64encode(bytes(out)).decode()


def rle_decode(data, size):
    raw = base64.b64decode(data)
    lengths, values = [], []
    i, end = 0, len(raw)
    while i < end:
        run = shift = 0
        while True:
            b = raw[i]
            i += 1
            run |= (b & 0x7f) << shift
            shift += 7
            if b < 0x80:
                break
        lengths.append(run)
        values.append(raw[i])
        i += 1
    out = np.repeat(np.array(values, np.uint8), np.array(lengths, np.int64))
    if out.size != size:
        raise ValueError(f"Voxels hold {out.size} cells, the box {size}")
    return out


def encode_voxels(grid):
    return {"enc": "rle", "data": rle_encode(grid)}


def blank_voxels(box):
    return np.full((box.length, box.width, box.height), EMPTY, np.uint8)


def layer_voxels(layer, box):
    """A voxel layer's grid, [i, j, k]; None for a layer carved from its views."""
    v = layer.get("voxels")
    if not v:
        return None
    if v.get("enc") != "rle":
        raise ValueError(f"Unknown voxel encoding {v.get('enc')!r}")
    return rle_decode(v["data"], box.length * box.width * box.height).reshape(box.length, box.width, box.height)


def _fallback(materials):
    return materials.index("frame") if "frame" in materials else 0


def voxel_dict(grid, box, materials, part):
    """A grid as a generator voxel dict: {(f, r, z): (material, part)}."""
    idx = np.nonzero(grid != EMPTY)
    names = list(materials) + ["frame"] * (256 - len(materials))
    coords = zip((idx[0] + box.f0).tolist(), (idx[1] + box.r0).tolist(), (idx[2] + box.z0).tolist())
    return {c: (names[m], part) for c, m in zip(coords, grid[idx].tolist())}


def grid_from_dict(vox, box, materials):
    """A generator voxel dict as a grid over the box; what lies outside it is left out, and a
    material not in the palette becomes frame."""
    grid = blank_voxels(box)
    index = {m: i for i, m in enumerate(materials)}
    other = _fallback(materials)
    for (f, r, z), (mat, _) in vox.items():
        i, j, k = f - box.f0, r - box.r0, z - box.z0
        if 0 <= i < box.length and 0 <= j < box.width and 0 <= k < box.height:
            grid[i, j, k] = index.get(mat, other)
    return grid


def view_rays(grid, view):
    """A grid seen from a view: [row, column, t], t counting voxels in along the pixel's line of sight
    from that view's side, as the depths do."""
    if view == "top":
        return grid.transpose(1, 0, 2)[:, :, ::-1]
    if view == "side":
        return grid.transpose(2, 0, 1)[::-1, :, ::-1]
    if view == "left":
        return grid.transpose(2, 0, 1)[::-1, ::-1, :]
    if view == "front":
        return grid.transpose(2, 1, 0)[::-1, ::-1, ::-1]
    return grid.transpose(2, 1, 0)[::-1, :, :]


def project_view(grid, view):
    """A voxel layer as one of its views: per pixel, the first voxel's material and its depth, as
    drawing_arrays gives them (every view, whatever its sides)."""
    rays = view_rays(grid, view)
    has = rays != EMPTY
    hit = has.any(2)
    first = np.argmax(has, 2)
    mat = np.where(hit, np.take_along_axis(rays, first[:, :, None], 2)[:, :, 0], EMPTY).astype(np.uint8)
    depth = np.where(hit, np.minimum(first, 255), 0).astype(np.uint8)
    return mat, depth


# A carved layer baked to voxels: each voxel the colour of the first of these faces that shows it.
BAKE_ORDER = ("top", "side", "left", "front", "back")


def bake(gen, layer, box, materials, part=None):
    """Any layer as a voxel grid. A carved layer is carved, each voxel coloured by the first face
    that shows it (top, side, left, front, back), then as colour_layer falls back. A layer loaded
    from the generator is its model as it faces east, with what's drawn on it applied."""
    grid = layer_voxels(layer, box)
    if grid is not None:
        return grid.copy()
    if layer.get("native") and gen is not None:
        model = native(gen, layer, "E") or {}
        model = apply_edits(model, native_edits(gen, layer, box, materials), box, layer, materials, part)
        return grid_from_dict(model, box, materials)
    occ, mats = carve(layer, box)
    grid = blank_voxels(box)
    if not occ.any():
        return grid
    seen = surfaces(occ)
    idx = np.nonzero(occ)
    full = {k: np.broadcast_to(v, occ.shape)[idx] for k, v in mats.items()}
    colour = _colours({k: v[idx] for k, v in seen.items()}, full, BAKE_ORDER, len(idx[0]))
    colour[colour == EMPTY] = _fallback(materials)
    grid[idx] = colour
    return grid


def surfaces(occ):
    """Per voxel: whether it's the first one seen from above, from the right, from the left, ahead
    and behind."""
    L, W, H = occ.shape
    kk = np.arange(H)[None, None, :]
    jj = np.arange(W)[None, :, None]
    ii = np.arange(L)[:, None, None]
    top_k = np.where(occ.any(2), H - 1 - np.argmax(occ[:, :, ::-1], 2), -1)[:, :, None]
    right_j = np.where(occ.any(1), W - 1 - np.argmax(occ[:, ::-1, :], 1), -1)[:, None, :]
    left_j = np.where(occ.any(1), np.argmax(occ, 1), W)[:, None, :]
    front_i = np.where(occ.any(0), L - 1 - np.argmax(occ[::-1, :, :], 0), -1)[None, :, :]
    back_i = np.where(occ.any(0), np.argmax(occ, 0), L)[None, :, :]
    return {"top": occ & (kk == top_k), "side": occ & (jj == right_j), "left": occ & (jj == left_j),
            "front": occ & (ii == front_i), "back": occ & (ii == back_i)}


# The faces each sprite direction takes a voxel's colour from first: east shows the right side, west
# the left.
ORDER = {"S": ("front", "top", "side", "left", "back"), "N": ("back", "top", "side", "left", "front"),
         "E": ("side", "left", "top", "front", "back"), "W": ("left", "side", "top", "front", "back")}


def colour_layer(occ, mats, materials, box, part, views):
    """A carved layer's voxels as generator voxel dicts, one per sprite direction: a voxel takes the
    colour of the face that direction shows most of, then of the top, then any view that has one."""
    if not occ.any():
        return {v: {} for v in views}
    seen = surfaces(occ)
    idx = np.nonzero(occ)
    full = {k: np.broadcast_to(v, occ.shape)[idx] for k, v in mats.items()}
    faces = {k: v[idx] for k, v in seen.items()}
    coords = list(zip((idx[0] + box.f0).tolist(), (idx[1] + box.r0).tolist(), (idx[2] + box.z0).tolist()))
    out = {}
    for view in views:
        colour = _colours(faces, full, ORDER[view], len(coords))
        out[view] = {c: ((materials[m] if m != EMPTY else "frame"), part) for c, m in zip(coords, colour.tolist())}
    return out


def _colours(faces, full, order, n):
    """A carved layer's voxels' materials: the first face in order that shows each, then any view
    that has one; EMPTY where none does."""
    colour = np.full(n, EMPTY, np.uint8)
    for face in order:
        if face in full:
            pick = faces[face] & (colour == EMPTY) & (full[face] != EMPTY)
            colour[pick] = full[face][pick]
    for face in ("top", "side", "left", "front", "back"):
        if face in full:
            pick = (colour == EMPTY) & (full[face] != EMPTY)
            colour[pick] = full[face][pick]
    return colour


def native(gen, layer, view, phase=0.0):
    """The generator's own model of a layer loaded from it, for one direction: exactly what the game
    shows. Some parts it draws per direction rather than as one solid: the body (its seat backs), the
    wheels (discs from the sides, tread faces from ahead and behind, turned to a phase while driving)
    and the spare from the sides. None for a layer drawn in the tool. Kept per generator, which draws
    the same model every time."""
    src = layer.get("native")
    if not src or gen is None:
        return None
    key = (tuple(sorted(src.items())), view, round(phase, 6) if src.get("source") == "wheels" else 0)
    if key not in gen.cache:
        gen.cache[key] = _native(gen.module, src, view, phase)
    return gen.cache[key]


def _native(g, src, view, phase):
    kind, variant = src.get("source"), src.get("variant")
    try:
        if kind == "body":
            return g.build(variant, view).vox
        if kind == "wheels":
            return g.wheels(view, phase, axles=g.near_axles(view)).vox
        if kind == "hood":
            return g.hood_lid(variant).vox
        if kind == "panel":
            return {k: v for k, v in g.panels_closed(variant).vox.items() if v[1] == src.get("part")}
        if kind == "fueldoor":
            return g.fuel_door(0).vox
        if kind == "hardpoint":
            if src.get("name") == "spare" and view in "EW" and hasattr(g, "spare_tread"):
                return g.spare_tread(False, view).vox
            return g.HARDPOINTS[src["name"]](False).vox
    except (AttributeError, KeyError, TypeError):
        return None
    return None


# -- drawing over the generator's model -----------------------------------------------------------
#
# A layer loaded from the generator keeps the generator's model. What is drawn on it is applied over
# that model, pixel by pixel, along each pixel's line of sight into the box: everything the artist
# didn't touch stays exactly as the game draws it (the seat backs each direction has, the wheels'
# faces), so the generator's own fixes carry over however much is drawn.

# The direction each view of a layer loaded from the generator is drawn from: the way the game shows
# that side, so the left view shows the driver's side and the side views leave out what the generator
# leaves out from the sides.
BASE_DIRECTION = {"top": "S", "side": "E", "left": "W", "front": "S", "back": "N"}


def _native_spec(gen, src):
    """The layer the generator split its vehicle into that a layer loaded from it came from."""
    variant = src.get("variant")
    loaded = gen.cache.get(("layers", variant))
    if loaded is None:
        loaded = gen.cache[("layers", variant)] = gen.layers(variant)
    return next((l for l in loaded if l["native"] == src), None)


def native_drawing(gen, layer, box, materials):
    """The views a layer loaded from the generator starts from, in this box: each its model as the
    game draws it from that view's direction (the body differs per direction: its seat backs, the
    bumper ends the sides leave out), so what differs is what has been drawn since."""
    src = layer.get("native")
    key = ("drawing", tuple(sorted(src.items())), box.length, box.width, box.height, tuple(materials))
    if key not in gen.cache:
        spec = _native_spec(gen, src)
        if spec is None:
            gen.cache[key] = None
        else:
            per_direction = {}
            out = {}
            for view in VIEWS:
                d = BASE_DIRECTION[view] if src.get("source") == "body" else None
                if d not in per_direction:
                    model = native(gen, layer, d) if d else spec["vox"]
                    per_direction[d] = drawing_arrays(model or {}, box, materials, spec["sides"])
                out[view] = per_direction[d][view]
            gen.cache[key] = out
    return gen.cache[key]


def legacy_native_drawing(gen, layer, box, materials):
    """The views a project from before per direction drawings has for a layer loaded from the
    generator: every view of the one model it was split into."""
    src = layer.get("native")
    spec = _native_spec(gen, src)
    return None if spec is None else drawing_arrays(spec["vox"], box, materials, spec["sides"])


def refresh_native(gen, layers, box, materials):
    """Brings a project's layers loaded from the generator up to per direction drawings: in each
    view, the pixels still as the old drawing had them take the new drawing's, and a view never
    drawn on (the left, in a project from before it) shows the model; what was drawn stays. Returns
    how many layers changed."""
    changed = 0
    for lay in layers:
        if not lay.get("native"):
            continue
        new = native_drawing(gen, lay, box, materials)
        old = legacy_native_drawing(gen, lay, box, materials)
        if new is None or old is None:
            continue
        grids = layer_grids(lay, box)
        views = {}
        for view in VIEWS:
            m, d = grids[view]
            nm, nd = new[view]
            if (m == EMPTY).all():
                m, d = nm, nd
            else:
                om, od = old[view]
                same = (m == om) & ((m == EMPTY) | (d == od))
                m, d = np.where(same, nm, m), np.where(same, nd, d)
            views[view] = {"mat": bytes_b64(m.astype(np.uint8)), "depth": bytes_b64(np.clip(d, 0, 255).astype(np.uint8))}
        if views != lay.get("views"):
            lay["views"] = views
            changed += 1
    return changed


def native_views(gen, layer, box, materials):
    """A layer loaded from the generator's views, as the app takes them."""
    return {view: {"mat": bytes_b64(m), "depth": bytes_b64(d)}
            for view, (m, d) in native_drawing(gen, layer, box, materials).items()}


def native_edits(gen, layer, box, materials):
    """What has been drawn on a layer loaded from the generator: (view, row, column, material,
    depth) for every pixel that differs from the drawing it started from. A view with nothing in it
    hasn't been drawn on (a project from before the left view has none)."""
    base = native_drawing(gen, layer, box, materials)
    if base is None:
        return []
    out = []
    grids = layer_grids(layer, box)
    for view in VIEWS:
        m, d = grids[view]
        if (m == EMPTY).all():
            continue
        bm, bd = base[view]
        changed = (m != bm) | ((m != EMPTY) & (d != bd))
        for y, x in zip(*(a.tolist() for a in np.nonzero(changed))):
            out.append((view, y, x, int(m[y, x]), int(d[y, x])))
    return out


def _rays(view, y, x, box, mirror=False):
    """The voxels a view's pixel looks through, nearest first. With mirror, a side view pixel looks
    in from both sides, for a layer whose left mirrors its right."""
    L, W, H = box.length, box.width, box.height
    f0, r0 = box.f0, box.r0
    if view == "side":
        i, k = x, H - 1 - y
        rays = [[(i + f0, W - 1 - t + r0, k) for t in range(W)]]
        if mirror:
            rays.append([(i + f0, t + r0, k) for t in range(W)])
        return rays
    if view == "left":
        i, k = L - 1 - x, H - 1 - y
        return [[(i + f0, t + r0, k) for t in range(W)]]
    if view == "front":
        j, k = W - 1 - x, H - 1 - y
        return [[(L - 1 - t + f0, j + r0, k) for t in range(L)]]
    if view == "back":
        j, k = x, H - 1 - y
        return [[(t + f0, j + r0, k) for t in range(L)]]
    return [[(x + f0, y + r0, H - 1 - t) for t in range(H)]]


def _draw_ray(vox, ray, m, d, materials, part, profile=None):
    """One drawn pixel along its line of sight: erased cuts through, a deeper surface dents in to it,
    a nearer one builds out to it, and the surface it lands on takes the pixel's material. Where
    there was nothing, it's built as deep as the profile (the drawn pixel nearest it) goes: a panel
    drawn bigger keeps both its skins, a body drawn taller is as wide as the rest."""
    present = [t for t, k in enumerate(ray) if k in vox]
    if m == EMPTY:
        for t in present:
            del vox[ray[t]]
        return
    if not 0 <= m < len(materials) or d >= len(ray):
        return
    mat = materials[m]
    if not present:
        for dt, (own_mat, own_part) in profile or [(0, (mat, part))]:
            if d + dt < len(ray):
                vox[ray[d + dt]] = (mat if dt == 0 else own_mat, own_part)
        return
    s = present[0]
    own = vox[ray[s]][1]
    if d > s:
        for t in present:
            if t < d:
                del vox[ray[t]]
        rest = [t for t in present if t >= d]
        t = rest[0] if rest else d
        vox[ray[t]] = (mat, vox[ray[t]][1] if ray[t] in vox else own)
    elif d < s:
        for t in range(d, s):
            vox[ray[t]] = (mat, own)
    else:
        vox[ray[s]] = (mat, own)


def apply_edits(vox, edits, box, layer, materials, part):
    """A model with what's been drawn over it applied. On a layer on both sides with nothing drawn
    in its left view, what's drawn on its right side goes on its left too."""
    if not edits:
        return vox
    left, _ = _grid(layer.get("views"), box, "left")
    mirror = layer.get("sides", "both") == "both" and (left == EMPTY).all()
    out = dict(vox)
    for view, y, x, m, d in edits:
        for n, ray in enumerate(_rays(view, y, x, box, mirror)):
            profile = None
            if m != EMPTY and not any(k in out for k in ray):
                profile = _profile(out, view, y, x, box, mirror, n)
            _draw_ray(out, ray, m, d, materials, part, profile)
    return out


def _profile(vox, view, y, x, box, mirror, n):
    """The voxels along the drawn pixel's line of sight nearest to (y, x), from its surface in:
    (how far behind the surface, (material, part)) each."""
    rows, cols = box.shape(view)
    for r in range(1, 5):
        best = None
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r or not (0 <= y + dy < rows and 0 <= x + dx < cols):
                    continue
                ray = _rays(view, y + dy, x + dx, box, mirror)[n]
                present = [t for t, k in enumerate(ray) if k in vox]
                if present and (best is None or dx * dx + dy * dy < best[0]):
                    best = (dx * dx + dy * dy, [(t - present[0], vox[ray[t]]) for t in present])
        if best:
            return best[1]
    return None


def build(project, box, materials, views, skip=(), gen=None, frame=0):
    """Every visible layer carved and coloured. Per direction: the stack's voxels (later layers
    win), each with the layer's role as its part (or 'L<n>' without one), and which layer owns each
    voxel; and each layer's own voxels, for swinging it. Layers in skip are left out of the stack.
    A layer whose role the generator draws per direction uses the generator's own model there; a
    voxel layer is its voxels from every direction. Damage stages stay out of the stack; a layer with
    one shown (show_stage) is drawn from that stage's drawing. Frame is the wheels' frame while
    driving."""
    models = {v: {} for v in views}
    owners = {v: {} for v in views}
    per_layer = []
    count = 0
    phase = wheel_phase(gen, frame)
    layers = project["layers"]
    for n, layer in enumerate(layers):
        if not layer.get("visible", True) or layer.get("stage"):
            per_layer.append(None)
            continue
        part = layer.get("role") or f"L{n}"
        source = stage_drawing(layers, layer) or layer
        grid = layer_voxels(source, box)
        if grid is not None:
            own = voxel_dict(grid, box, materials, part)
            count += len(own)
            if frame and layer.get("kind") == "wheel":
                own = spin_wheels(own, frame * SPIN_STEP)
            coloured = {v: own for v in views}
        elif source.get("native") and gen is not None:
            edits = native_edits(gen, source, box, materials)
            coloured = {}
            for v in views:
                own = native(gen, source, v, phase)
                coloured[v] = apply_edits(own or {}, edits, box, source, materials, part)
            count += len(coloured[views[0]])
        else:
            occ, mats = carve(source, box)
            count += int(occ.sum())
            coloured = colour_layer(occ, mats, materials, box, part, views)
            if frame and layer.get("kind") == "wheel":
                coloured = {v: spin_wheels(c, frame * SPIN_STEP) for v, c in coloured.items()}
        per_layer.append(coloured)
        if n in skip:
            continue
        for v in views:
            models[v].update(coloured[v])
            owners[v].update(dict.fromkeys(coloured[v], n))
    return models, owners, per_layer, count


def stages(layers, layer):
    """A layer's damage stages, in the order they were listed."""
    return [l for l in layers if (l.get("stage") or {}).get("of") == layer.get("name")]


def stage_drawing(layers, layer):
    """The stage a layer shows instead of itself, as a drawing in its place (keeping the layer's kind,
    role and what rides on it), or None when it shows intact."""
    shown = layer.get("show_stage")
    if not shown:
        return None
    st = next((l for l in stages(layers, layer) if l.get("name") == shown), None)
    if st is None:
        return None
    return {**layer, **{k: st.get(k) for k in ("views", "voxels", "native", "sides", "thickness")}}


# -- a drawn vehicle's chassis -------------------------------------------------------------------

def project_chassis(gen, project, box):
    """The chassis of a vehicle drawn in the tool, from its wheel layers: an axle for each group of
    wheels along it, their centre, radius and the tyres across it; no baked mud. None for a vehicle
    loaded from the generator, one without wheels, or a generator without chassis."""
    g = gen.module
    layers = project["layers"]
    if not hasattr(g, "Chassis") or any(l.get("native") for l in layers):
        return None
    vox = set()
    for n in wheel_layers(project):
        grid = layer_voxels(layers[n], box)
        occ = grid != EMPTY if grid is not None else carve(layers[n], box)[0]
        vox |= {(int(i) + box.f0, int(j) + box.r0, int(k) + box.z0) for i, j, k in zip(*np.nonzero(occ))}
    if not vox:
        return None

    def runs(values):
        values = sorted(set(values))
        out, start = [], values[0]
        for a, b in zip(values, values[1:]):
            if b - a > 1:
                out.append((start, a))
                start = b
        return out + [(start, values[-1])]

    axles = []
    for f0, f1 in runs(k[0] for k in vox):
        group = [k for k in vox if f0 <= k[0] <= f1]
        z0, z1 = min(k[2] for k in group), max(k[2] for k in group)
        axles.append(g.Axle((f0 + f1 + 1) / 2, (z0 + z1 + 1) / 2, (z1 - z0 + 1) / 2, runs(k[1] for k in group)))
    axles.sort(key=lambda a: -a.f)
    return g.Chassis(axles)


@contextlib.contextmanager
def styled(gen, project, box):
    """Renders with the project's own chassis while it's one drawn in the tool, so the dirt follows its
    wheels and its paint comes clean; a project loaded from the generator keeps the generator's."""
    c = project_chassis(gen, project, box)
    if c is None:
        yield
        return
    with gen.module.chassis(c):
        yield


# -- driving --------------------------------------------------------------------------------------

# A drawn wheel turns this far a frame: its four frames make a quarter turn, which loops for any rim
# with fourfold symmetry (four or eight lugs or spokes).
SPIN_STEP = 22.5


def wheel_phase(gen, frame):
    """The generator's wheel phase at a driving frame: one lug's pitch over its frames, as the game's
    wheels_0 state loops."""
    if not frame or gen is None:
        return 0.0
    g = gen.module
    lugs = getattr(g, "LUGS", 13)
    return frame * (2 * math.pi / lugs) / gen.wheel_frames


def spin_wheels(vox, degrees):
    """A drawn wheel layer turned for driving: each axle's wheels (voxels grouped by gaps along the
    length) turned about their own centre, the top going forward."""
    if not vox or not degrees:
        return vox
    fs = sorted({k[0] for k in vox})
    groups, start = [], fs[0]
    for a, b in zip(fs, fs[1:]):
        if b - a > 1:
            groups.append((start, a))
            start = b
    groups.append((start, fs[-1]))
    out = {}
    for f0, f1 in groups:
        axle = {k: v for k, v in vox.items() if f0 <= k[0] <= f1}
        zs = [k[2] for k in axle]
        centre = ((f0 + f1 + 1) / 2, (min(zs) + max(zs) + 1) / 2)
        out.update(turn(axle, ("fz", centre, 1), -degrees))
    return out


def wheel_layers(project):
    """The layers that stay on the ground while the body rides on its suspension: the wheels."""
    return {n for n, l in enumerate(project["layers"]) if l.get("kind") == "wheel" and not l.get("stage")}


def drive_sprites(gen, project, box, materials, view, colours=None):
    """The vehicle driving one way, split as the game draws it: per wheel frame, the whole sprite and
    its wheels alone, which stay on the ground; and once, the rest, which bobs on the suspension,
    with what the wheels hide drawn in (as the game's base keeps it) so it can rise off them."""
    wheels = wheel_layers(project)
    parts = set(getattr(gen.module, "WHEEL_PARTS", {"wheel"}))
    parts |= {l.get("role") or f"L{n}" for n, l in enumerate(project["layers"]) if n in wheels}
    frames, grounds, body = [], [], None
    for k, (vox, owner, phase) in enumerate(drive_scenes(gen, project, box, materials, view)):
        img, _, mask = render_sprite(gen, view, vox, owner, colours, phase, wheels=(wheels, parts))
        frames.append(img)
        ground = np.asarray(img).copy()
        ground[~mask] = 0
        grounds.append(Image.fromarray(ground, "RGBA"))
        if k == 0:
            models, owners, _, _ = build(project, box, materials, [view], skip=wheels, gen=gen)
            alone, _ = render_sprite(gen, view, models[view], owners[view], colours)
            rest = np.asarray(img).copy()
            rest[mask] = np.asarray(alone)[mask]
            body = Image.fromarray(rest, "RGBA")
    return frames, grounds, body


def drive_scenes(gen, project, box, materials, view):
    """The vehicle driving, seen from one direction: per wheel frame, the voxels and who owns them."""
    scenes = []
    for frame in range(gen.wheel_frames):
        models, owners, _, _ = build(project, box, materials, [view], gen=gen, frame=frame)
        scenes.append((models[view], owners[view], wheel_phase(gen, frame)))
    return scenes


# -- swinging a layer -----------------------------------------------------------------------------

def _skin(values, outer_high):
    """The outer skin of a panel along one axis: of its two most used positions, the outer one."""
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    top = sorted(counts, key=lambda v: -counts[v])[:2]
    return max(top) if outer_high else min(top)


def hinge(kind, sides, vox, edge=None):
    """A layer's hinge: (plane, centre, sign) for its kind, found from its voxels the way the
    generator places its own. 'fr' turns about an upright line, 'fz' about a line across. An edge
    (front, back, top or bottom) puts it there instead: a door on its front or back edge, anything
    else across its front, back, top or bottom."""
    fs = [k[0] for k in vox]
    rs = [k[1] for k in vox]
    if edge in ("front", "back") and kind in ("door", "fueldoor"):
        left = sides == "left" or (sides != "right" and max(rs) < 0)
        f = max(fs) + 1 if edge == "front" else min(fs)
        r = _skin(rs, False) if left else _skin(rs, True) + 1
        return "fr", (f, r), (1 if left else -1) * (1 if edge == "front" else -1)
    if edge in ("front", "back", "top", "bottom"):
        zs = [k[2] for k in vox]
        if edge == "front":
            return "fz", (max(fs) + 1, max(zs) + 1), -1     # The back lifts.
        if edge == "back":
            return "fz", (min(fs), max(zs) + 1), 1          # The front lifts.
        mid = (min(fs) + max(fs) + 1) / 2
        if edge == "top":
            return "fz", (mid, max(zs) + 1), -1             # The bottom lifts out.
        return "fz", (mid, min(zs)), 1                      # The top falls back.
    if kind in ("door", "fueldoor"):
        # On its front edge, at the outer face of its skin; the back swings out.
        f1 = max(fs) + 1
        left = sides == "left" or (sides != "right" and max(rs) < 0)
        if left:
            return "fr", (f1, _skin(rs, False)), 1
        return "fr", (f1, _skin(rs, True) + 1), -1
    # The panel itself: its skins are its two most used planes. What hangs off them (the tailgate's
    # spare carrier and can rack) doesn't move the hinge.
    if kind in ("hood", "boot"):
        top = _skins([k[2] for k in vox])
        skin = [k for k in vox if k[2] in top]
        if kind == "boot":
            return "fz", (max(k[0] for k in skin) + 1, max(top) + 1), -1   # Above its front edge: the back lifts.
        return "fz", (min(k[0] for k in skin), max(top) + 1), 1     # Above its back edge: the front lifts.
    skins = _skins(fs)
    skin = [k for k in vox if k[0] in skins]
    mid = (min(skins) + max(skins) + 1) / 2
    z0, z1 = min(k[2] for k in skin), max(k[2] for k in skin) + 1
    if kind == "tailgate":
        return "fz", (mid, z0), 1           # Along its bottom edge: the top falls back.
    if kind == "hatch":
        return "fz", (mid, z1), -1          # Along its top edge: the bottom lifts out behind.
    if kind == "windshield":
        return "fz", (mid, z0), -1          # Along its bottom edge: it folds forward.
    return None


def _skins(values):
    """A panel's skins along one axis: its two most used positions (or one, for a single skin)."""
    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    ranked = sorted(counts, key=lambda v: -counts[v])
    top = ranked[:2]
    # A second plane well apart from the first is not the panel's other skin.
    if len(top) == 2 and abs(top[0] - top[1]) > 2:
        top = top[:1]
    return top


def turn(vox, h, degrees, flat=None, part=None):
    """Voxels turned about a hinge by degrees. Like the generator, each is sampled at four points in
    the plane it turns in, so the panel stays solid; flat(mat) gives a slanted panel's material."""
    if not degrees or h is None:
        return dict(vox)
    plane, (ca, cb), sign = h
    a = math.radians(degrees) * sign
    cos, sin = math.cos(a), math.sin(a)
    out = {}
    for (f, r, z), (mat, own) in vox.items():
        val = (flat(mat) if flat else mat, part or own)
        for du in (0.25, 0.75):
            for dv in (0.25, 0.75):
                if plane == "fr":
                    u, v = f + du - ca, r + dv - cb
                    key = (math.floor(ca + u * cos - v * sin), math.floor(cb + u * sin + v * cos), z)
                else:
                    u, v = f + du - ca, z + dv - cb
                    key = (math.floor(ca + u * cos - v * sin), r, math.floor(cb + u * sin + v * cos))
                out[key] = val
    return out


def swing_scenes(gen, project, box, materials, n, view):
    """A layer swinging open, seen from one direction: per frame, the voxels and who owns them. The
    layers riding on it swing with it, cut where they would pass into the rest; slanted panels are
    shaded flat, as the generator does."""
    layers = project["layers"]
    spec = layers[n]
    angles = gen.swing_angles(spec.get("kind"))
    if not angles:
        return []
    # A hinge of its own: where it is and how far it opens, the kind's frames scaled to it.
    own_hinge = spec.get("hinge") or {}
    if own_hinge.get("angle"):
        top = max(abs(a) for a in angles) or 1
        angles = tuple(a * own_hinge["angle"] / top for a in angles)
    riders = [i for i, l in enumerate(layers) if i != n and l.get("rides") and l.get("rides") == spec.get("name")]
    models, owners, per_layer, _ = build(project, box, materials, [view], skip={n, *riders}, gen=gen)
    own = (per_layer[n] or {}).get(view, {})
    if not own:
        return []
    h = hinge(spec.get("kind"), spec.get("sides", "both"), own, own_hinge.get("edge"))
    still = models[view]
    # Kit on a panel at the back is cut off where it would fold forward of the panel's closed face,
    # into the body, as the generator cuts the tailgate's.
    face = min(k[0] for k in own) if spec.get("kind") in ("tailgate", "hatch") else None
    scenes = []
    for a in angles:
        flat = gen.flat if a % 90 else None
        vox = dict(still)
        owner = dict(owners[view])
        for i in [n, *riders]:
            body = (per_layer[i] or {}).get(view, {})
            if i == n and face is not None and a:
                # The panel's own mounts (off its skins) are cut at its face too.
                skins = set(_skins([k[0] for k in body]))
                panel = {k: v for k, v in body.items() if k[0] in skins}
                mounts = turn({k: v for k, v in body.items() if k[0] not in skins}, h, a, flat)
                moved = {**{k: v for k, v in mounts.items() if k[0] < face}, **turn(panel, h, a, flat)}
            else:
                # Moved off its place, kit is a plain voxel model: its own projection no longer applies.
                moved = turn(body, h, a, flat, None if i == n or not a else f"L{i}")
            if i != n:
                moved = {k: v for k, v in moved.items()
                         if k not in still and (face is None or not a or k[0] < face)}
            vox.update(moved)
            owner.update(dict.fromkeys(moved, i))
        scenes.append((vox, owner))
    return scenes


# -- paint ----------------------------------------------------------------------------------------

def _linear(c):
    c = np.asarray(c, np.float64) / 255.0
    return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)


def _srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c > 0.0031308, 1.055 * c ** (1 / 2.4) - 0.055, c * 12.92) * 255.0


def _hash(x, y):
    v = np.sin(x * 127.1 + y * 311.7) * 43758.5453
    return v - np.floor(v)


def _noise(x, y):
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = _hash(ix, iy), _hash(ix + 1, iy)
    c, d = _hash(ix, iy + 1), _hash(ix + 1, iy + 1)
    return (a + (b - a) * fx) + ((c + (d - c) * fx) - (a + (b - a) * fx)) * fy


def paint(img, body=None, interior=None, seats=None, dirt=0.0, base=(118, 126, 80), mud=(98, 84, 58)):
    """The game's paint shader (paint.swsl) on a sprite: paint pixels (alpha 207-254) recoloured by
    zone keeping their shading, and dirt; everything else as drawn. Colours are sRGB triples or None
    for the factory paint."""
    a = np.asarray(img.convert("RGBA"), np.float64)
    rgb, mark = a[..., :3], np.floor(a[..., 3] + 0.5)
    paint_px = (mark > 206.5) & (mark <= 254.5)
    zone = np.where(paint_px, np.floor((254 - mark) / 16), 0)
    code = np.where(paint_px, np.mod(254 - mark, 16), 0)
    muddy = np.mod(code, 2)
    soon = np.floor(code / 2) / 7
    tex = _linear(rgb)
    grey = lambda c: c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722  # noqa: E731
    shade = grey(tex) / max(float(grey(_linear(base)[None, :])[0]), 0.001)
    out = tex.copy()
    for z, colour in enumerate((body, interior, seats)):
        sel = paint_px & (zone == z)
        if colour is None or not sel.any():
            continue
        c = _linear(colour)
        s = shade[sel][:, None]
        luma = max(float(grey(c[None, :])[0]), 0.001)
        dim = np.maximum(s + (s - 1) * 0.12 / luma, s * s)
        lit = np.where(s >= 1, c[None, :] * s + (s - 1) * 0.12, c[None, :] * dim)
        over = np.clip(lit.max(1) - 1, 0, 1)[:, None]
        col = np.minimum(lit, 1) * (1 - over) + over
        m = (muddy[sel] / 3)[:, None]
        out[sel] = col * (1 - m) + _linear(mud)[None, :] * m
    if dirt > 0.001:
        h, w = mark.shape
        y, x = np.mgrid[0:h, 0:w].astype(np.float64)
        drift = (_noise(x / 7, y / 7) - 0.5) * 0.4
        tone = np.clip(soon + drift, 0, 1)[..., None]
        grime = (_linear((51, 41, 28)) * (1 - tone) + _linear((128, 112, 84)) * tone) * \
            np.clip(shade, 0.65, 1.1)[..., None]
        amount = (np.clip((dirt * 0.95 - soon) / 0.25, 0, 1) * paint_px)[..., None]
        k = amount * (0.8 + (0.3 - 0.8) * soon[..., None])
        out = out * (1 - k) + grime * k
    alpha = np.where(paint_px, 255, a[..., 3])
    return Image.fromarray(np.dstack([_srgb(out), alpha]).round().astype(np.uint8), "RGBA").copy()


# -- sprites ---------------------------------------------------------------------------------------

def render_sprite(gen, view, vox, owner, colours=None, phase=0.0, wheels=None, keys=None):
    """One sprite direction of voxels, placed like the generator places its vehicles in their 96 px
    frames (here a 128 px frame, centred the same way), and which layer every pixel shows (its index
    plus one, 0 where there's nothing), from owner: {voxel: layer index}. Phase turns the far wheels
    with the near ones while driving. With wheels, (layer indices, parts), also a mask of the pixels
    where a wheel shows: those layers' voxels of those parts. Keys, an int16 array [OUT, OUT, 3], takes
    the voxel each pixel shows (glass its glass voxel); the rest is left as it was."""
    g = gen.module
    buf, glass = g.render(g.Model(vox), view)
    col = g.shade(buf)
    img = Image.new("RGBA", (OUT, OUT), (0, 0, 0, 0))
    px = img.load()
    parts = np.zeros((OUT, OUT), np.uint8)
    mask = np.zeros((OUT, OUT), bool)
    ox, oy = OUT // 2, OUT // 2 + gen.dy[view]

    def layer_of(key):
        return owner.get(key, -1) + 1

    for (x, y), c in col.items():
        X, Y = x + ox, y + oy
        if 0 <= X < OUT and 0 <= Y < OUT:
            px[X, Y] = tuple(c) if len(c) == 4 else (*c, 255)
            e = buf[(x, y)]
            parts[Y, X] = layer_of(e[4])
            if keys is not None:
                keys[Y, X] = e[4]
            if wheels:
                mask[Y, X] = owner.get(e[4], -1) in wheels[0] and e[2] in wheels[1]
    # The far axle's wheels, which the generator adds where nothing else is drawn in front and back
    # views, when the wheels are the generator's own.
    wheel_owner = next((owner[k] for k, (_, part) in vox.items() if part == "wheel" and k in owner), None)
    if wheel_owner is not None and hasattr(g, "far_wheels"):
        for (x, y), c in g.far_wheels(view, set(col), phase).items():
            X, Y = x + ox, y + oy
            if 0 <= X < OUT and 0 <= Y < OUT and (x, y) not in col:
                px[X, Y] = tuple(c) if len(c) == 4 else (*c, 255)
                parts[Y, X] = wheel_owner + 1
                mask[Y, X] = True
    img = paint(img, **(colours or {}))
    # Glass over whatever it is in front of, coloured by the generator: the windshield's dusty
    # streaked glass, plain glass elsewhere.
    px = img.load()
    for (x, y), e in glass.items():
        X, Y = x + ox, y + oy
        if not (0 <= X < OUT and 0 <= Y < OUT):
            continue
        under = buf.get((x, y))
        if under is not None and under[0] < e[0]:
            continue
        if hasattr(g, "dirty_glass") and e[2] == "windshield":
            tint = g.dirty_glass(x, y, e[4])
        elif hasattr(g, "glass_color"):
            tint = g.glass_color(x, y)
        else:
            tint = getattr(g, "GLASS_TINT", (176, 206, 204, 78))
        current = px[X, Y]
        if hasattr(g, "blend"):
            px[X, Y] = tuple(int(v) for v in g.blend(current if current[3] else None, tint))
        else:
            px[X, Y] = tint
        parts[Y, X] = layer_of(e[4])
        mask[Y, X] = False
        if keys is not None:
            keys[Y, X] = e[4]
    return (img, parts, mask) if wheels is not None else (img, parts)


NO_KEY = -32768


def blank_keys():
    return np.full((OUT, OUT, 3), NO_KEY, np.int16)


def keys_b64(keys):
    return base64.b64encode(np.ascontiguousarray(keys, "<i2").tobytes()).decode()


def png_data_url(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def bytes_b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr, np.uint8).tobytes()).decode()
