"""Open vehicle projects (.cmuov), for scripts and the app: load and save them, find layers, draw on a
layer's views with simple operations, show a view as text, reshape the box, and keep damage states.

A project is the file's JSON: box, materials (names), active, layers. A layer: name, kind, sides,
thickness, visible, role, rides, native, stage, views {view: {"mat", "depth"}} (base64 bytes, one per
pixel, row by row: the material's index or 255 for nothing, and the depth). A damage state is a named
look of a layer for a damage type, drawn at levels (20, 40, 60, 80, 100: how much of that damage the
part has taken); each level is a layer of its own with stage = {"of": the layer's name, "state": the
state's name, "type": the damage type, "from": the level}. Version 2 projects draw a layer loaded from
the generator per direction (see voxels.native_drawing). A voxel layer has voxels {"enc": "rle",
"data"} instead of views: its grid over the box, run-length coded (see voxels.rle_encode); it's
shaped by them alone, and drawing on a view of it is applied along each pixel's line of sight.
"""

import base64
import json
import os

import numpy as np

import voxels as V

FORMAT = "cmu-open-vehicle-project"
VERSION = 2
# The levels a damage state is drawn at, as the app offers them.
LEVELS = (20, 40, 60, 80, 100)
KINDS = ("body", "wheel", "door", "hood", "tailgate", "hatch", "boot", "windshield", "fueldoor", "lamp", "part")
KIND_DEFAULTS = {
    "body": ("both", 0), "wheel": ("both", 0), "door": ("left", 1), "hood": ("both", 1),
    "tailgate": ("both", 1), "hatch": ("both", 1), "boot": ("both", 1), "windshield": ("both", 1), "fueldoor": ("right", 1),
    "lamp": ("both", 2), "part": ("both", 0),
}


# -- files ----------------------------------------------------------------------------------------

def new(box, materials):
    return {"format": FORMAT, "version": VERSION, "box": dict(box), "materials": list(materials), "active": 0,
            "layers": []}


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("format") != FORMAT:
        raise ValueError(f"{path} is not a CMU Open Vehicles project")
    return migrate(data)


def migrate(project):
    """A project from before the left view: a layer on the left had its drawing in the side view,
    seen from its left but nose right; it moves to the left view, nose left."""
    box = V.Box.from_dict(project["box"])
    for lay in project["layers"]:
        if lay.get("sides") != "left" or lay.get("voxels"):
            continue
        grids = V.layer_grids(lay, box)
        for view in ("side", "left"):
            m, d = grids[view]
            put(lay, view, m.astype(np.uint8), np.clip(d, 0, 255).astype(np.uint8))
    return project


def save(project, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(project, fh)
    os.replace(tmp, path)


def remap(project, materials):
    """The project's material indices onto another palette by name; unknown names fall back to the
    first material."""
    old = project.get("materials") or []
    if old == list(materials):
        return project
    table = np.array([materials.index(n) if n in materials else 0 for n in old] + [0] * (256 - len(old)), np.uint8)
    table[V.EMPTY] = V.EMPTY
    box = V.Box.from_dict(project["box"])
    for layer in project["layers"]:
        g = V.layer_voxels(layer, box)
        if g is not None:
            layer["voxels"] = V.encode_voxels(table[g])
            continue
        for view in V.VIEWS:
            m, d = grid(layer, box, view)
            put(layer, view, table[m], d)
    project["materials"] = list(materials)
    return project


# -- layers and views -----------------------------------------------------------------------------

def layer(project, ref):
    """A layer by name (any case) or index."""
    layers = project["layers"]
    if isinstance(ref, int) or (isinstance(ref, str) and ref.lstrip("-").isdigit()):
        return layers[int(ref)]
    for l in layers:
        if l["name"].lower() == str(ref).lower():
            return l
    raise KeyError(f"No layer called {ref!r}. Layers: {', '.join(l['name'] for l in layers)}")


def blank_layer(box, kind="part", name=None):
    sides, thickness = KIND_DEFAULTS.get(kind, ("both", 0))
    out = {"name": name or kind.capitalize(), "kind": kind, "sides": sides, "thickness": thickness,
           "visible": True, "role": None, "rides": None, "native": None, "stage": None, "views": {}}
    for view in V.VIEWS:
        rows, cols = box.shape(view)
        put(out, view, np.full((rows, cols), V.EMPTY, np.uint8), np.zeros((rows, cols), np.uint8))
    return out


def grid(layer, box, view):
    """A view's material and depth grids, as arrays to change; a voxel layer's projection."""
    g = V.layer_voxels(layer, box)
    if g is not None:
        return V.project_view(g, view)
    m, d = V._grid(layer.get("views"), box, view)
    return m.astype(np.uint8).copy(), np.clip(d, 0, 255).astype(np.uint8).copy()


def put(layer, view, mat, depth):
    layer.setdefault("views", {})[view] = {"mat": V.bytes_b64(mat), "depth": V.bytes_b64(depth)}


def material_index(project, name):
    if name is None:
        return None
    if isinstance(name, int):
        return name
    if str(name).lower() in ("empty", "erase", "none"):
        return V.EMPTY
    mats = project["materials"]
    if name not in mats:
        raise KeyError(f"No material {name!r}. Materials: {', '.join(mats)}")
    return mats.index(name)


# -- drawing --------------------------------------------------------------------------------------

def to_cell(box, view, point, space):
    """A point to (column, row) in a view. In view space it already is one; in vehicle space it is
    the generator's coordinates: (f, z) on the side and left, (r, z) on the front and back, (f, r)
    on top."""
    a, b = point
    if space != "vehicle":
        return int(a), int(b)
    H = box.height
    if view == "side":
        return a - box.f0, H - 1 - b
    if view == "left":
        return box.length - 1 - (a - box.f0), H - 1 - b
    if view == "front":
        return box.width - 1 - (a - box.r0), H - 1 - b
    if view == "back":
        return a - box.r0, H - 1 - b
    return a - box.f0, b - box.r0


def _line(a, b):
    (x0, y0), (x1, y1) = a, b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        yield x0, y0
        if (x0, y0) == (x1, y1):
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _shape_cells(op, rows, cols, cell):
    kind = op["op"]
    fill = op.get("fill", True)
    if kind in ("pixel", "pixels"):
        pts = op.get("points") or [op.get("at")]
        return [cell(p) for p in pts]
    if kind == "line":
        pts = op.get("points") or [op["from"], op["to"]]
        out = []
        for a, b in zip(pts, pts[1:]):
            out.extend(_line(cell(a), cell(b)))
        return out
    if kind == "rect":
        (x0, y0), (x1, y1) = cell(op["from"]), cell(op["to"])
        xs, ys = sorted((x0, x1)), sorted((y0, y1))
        return [(x, y) for y in range(ys[0], ys[1] + 1) for x in range(xs[0], xs[1] + 1)
                if fill or x in xs or y in ys]
    if kind == "ellipse":
        cx, cy = cell(op["center"])
        rx, ry = op["radius"] if isinstance(op["radius"], (list, tuple)) else (op["radius"], op["radius"])
        out = []
        for y in range(int(cy - ry) - 1, int(cy + ry) + 2):
            for x in range(int(cx - rx) - 1, int(cx + rx) + 2):
                v = ((x - cx) / max(rx, 0.5)) ** 2 + ((y - cy) / max(ry, 0.5)) ** 2
                if v <= 1.0 and (fill or v >= (1 - 1.6 / max(rx, ry, 1))):
                    out.append((x, y))
        return out
    if kind == "polygon":
        pts = [cell(p) for p in op["points"]]
        out = []
        ys = [p[1] for p in pts]
        for y in range(min(ys), max(ys) + 1):
            xs = []
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                if (y0 <= y < y1) or (y1 <= y < y0):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for a, b in zip(xs[::2], xs[1::2]):
                out.extend((x, y) for x in range(int(round(a)), int(round(b)) + 1))
        if not fill:
            out = []
        for a, b in zip(pts, pts[1:] + pts[:1]):
            out.extend(_line(a, b))
        return out
    raise ValueError(f"Unknown op {kind!r}")


# Operations that only a voxel layer takes: solids in vehicle space, and keys that reach past the
# surface along each pixel's line of sight.
SOLID_OPS = ("box", "cylinder")
VOXEL_KEYS = ("extrude", "through", "slice", "erase")


def draw(project, lay, view, ops, mirror_default=False):
    """Draws a list of operations on one view of a layer. Each op: {"op": pixel(s) | line | rect |
    ellipse | polygon | fill | text | clear | shift | depth, "mat": a material name or "empty",
    "depth": n, "add": n (depth relative to what's there), "mirror": bool, "space": "view" or
    "vehicle", ...}. On a voxel layer they're applied along each pixel's line of sight (see
    draw_voxels), which also takes box and cylinder. Returns how many pixels (or voxels) changed."""
    box = V.Box.from_dict(project["box"])
    if view not in V.VIEWS:
        raise ValueError(f"View is one of {', '.join(V.VIEWS)}")
    if lay.get("voxels"):
        return draw_voxels(project, lay, view, ops, mirror_default)
    for op in ops:
        keys = [k for k in VOXEL_KEYS if k in op]
        if op["op"] in SOLID_OPS or keys:
            what = op["op"] if op["op"] in SOLID_OPS else f"{op['op']} with {' and '.join(keys)}"
            raise ValueError(f"{what} needs a voxel layer: bake {lay['name']} first (cmuov.py layer FILE bake LAYER)")
    m, d = grid(lay, box, view)
    seed_side(lay, box, view, m, d)
    before_m, before_d = m.copy(), d.copy()
    for op in ops:
        m, d = _draw_op(project, box, view, op, m, d, mirror_default)
    put(lay, view, m, d)
    return int(((m != before_m) | (d != before_d)).sum())


def _draw_op(project, box, view, op, m, d, mirror_default, touched=None):
    """One operation on a view's grids; returns them (a shift makes new ones). Touched, a list, takes
    every pixel the operation draws, in order."""
    rows, cols = m.shape
    max_depth = _depth_axis(box, view)
    touch = touched.append if touched is not None else (lambda p: None)

    def mirror_of(x, y):
        if view in ("side", "left"):
            return None
        return (x, rows - 1 - y) if view == "top" else (cols - 1 - x, y)

    kind = op["op"]
    space = op.get("space", "view")
    cell = lambda p: to_cell(box, view, p, space)  # noqa: E731
    mat = material_index(project, op.get("mat"))
    depth = op.get("depth")
    add = op.get("add")
    mirror = op.get("mirror", mirror_default)
    if kind == "clear":
        m[:] = V.EMPTY
        d[:] = 0
        return m, d
    if kind == "shift":
        dx, dy = op["by"]
        m = np.roll(np.roll(m, dy, 0), dx, 1)
        d = np.roll(np.roll(d, dy, 0), dx, 1)
        return m, d
    if kind == "fill":
        x, y = cell(op["at"])
        targets = [(x, y)] + ([mirror_of(x, y)] if mirror and mirror_of(x, y) else [])
        for sx, sy in targets:
            if not (0 <= sx < cols and 0 <= sy < rows):
                continue
            m0, d0 = m[sy, sx], d[sy, sx]
            seen = np.zeros_like(m, bool)
            stack = [(sx, sy)]
            while stack:
                cx, cy = stack.pop()
                if not (0 <= cx < cols and 0 <= cy < rows) or seen[cy, cx]:
                    continue
                if m[cy, cx] != m0 or (m0 != V.EMPTY and d[cy, cx] != d0):
                    continue
                seen[cy, cx] = True
                stack.extend(((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)))
            if depth is None and add is None and m0 == V.EMPTY and mat is not None:
                d[seen] = auto_depth(m, d, sx, sy, 0)
            if mat is not None:
                m[seen] = mat
            if depth is not None:
                d[seen] = depth
            elif add is not None:
                d[seen] = np.clip(d[seen].astype(int) + add, 0, max_depth - 1)
            if mat is not None or depth is not None or add is not None:
                for py, px in zip(*(a.tolist() for a in np.nonzero(seen))):
                    touch((px, py))
        return m, d
    if kind == "text":
        legend = op.get("legend") or {}
        x0, y0 = cell(op.get("at", [0, 0]))
        depths = op.get("depths")
        for ry, line in enumerate(op["rows"]):
            for rx, ch in enumerate(line):
                if ch not in legend:
                    continue
                x, y = x0 + rx, y0 + ry
                if not (0 <= x < cols and 0 <= y < rows):
                    continue
                if m[y, x] == V.EMPTY and depth is None and not depths:
                    d[y, x] = auto_depth(m, d, x, y, 0)
                m[y, x] = material_index(project, legend[ch])
                if depths:
                    d[y, x] = int(depths[ry][rx], 36) if ry < len(depths) and rx < len(depths[ry]) else (depth or 0)
                elif depth is not None:
                    d[y, x] = depth
                touch((x, y))
        return m, d
    cells = _shape_cells(op if kind != "depth" else {**op, "op": op.get("shape", "rect")}, rows, cols, cell)
    for x, y in cells:
        for px, py in [(x, y)] + ([mirror_of(x, y)] if mirror and mirror_of(x, y) else []):
            if not (0 <= px < cols and 0 <= py < rows):
                continue
            was_empty = m[py, px] == V.EMPTY
            if kind == "depth" or (mat is None and (depth is not None or add is not None)):
                if was_empty:
                    continue
            elif mat is not None:
                m[py, px] = mat
            if m[py, px] == V.EMPTY:
                d[py, px] = 0
            elif depth is not None:
                d[py, px] = min(depth, max_depth - 1)
            elif add is not None:
                d[py, px] = int(np.clip(int(d[py, px]) + add, 0, max_depth - 1))
            elif was_empty:
                # No depth given: a new pixel sits on the surface beside it, like the app's auto.
                m[py, px] = V.EMPTY
                d[py, px] = auto_depth(m, d, px, py, 0)
                m[py, px] = mat
            touch((px, py))
    return m, d


# -- drawing on a voxel layer -----------------------------------------------------------------------

def draw_voxels(project, lay, view, ops, mirror_default=False):
    """Operations on a voxel layer. The view's ops work on its projection as on a drawing, and each
    pixel they draw is applied along its line of sight as on a layer loaded from the generator
    (voxels._draw_ray): erasing cuts through, a deeper depth dents in, a nearer one builds out, the
    surface hit takes the material, and a pixel where there was nothing is built as deep as the
    voxels beside it go. "extrude": [d0, d1] fills each pixel's line between those depths instead,
    "extrude": n that many voxels in from its depth, "through": true from its depth to the far side
    (with "mat": "empty", they clear). "slice": d draws on the layer's cross-section that deep, a voxel
    a pixel. "erase": "surface" takes off only the first voxel along each line. "mirror" on the side or
    left view does the same on the other one. Clear empties the layer; shift moves its voxels.
    Solids, in vehicle space: box {"from": [f, r, z], "to": [f, r, z]}, cylinder {"axis": f | r | z,
    "center": [the other two], "radius", "from", "to"} (a voxel is in when its centre is), both
    with "mirror" across the centre (r to -r-1). Returns how many voxels changed."""
    box = V.Box.from_dict(project["box"])
    materials = project["materials"]
    grid = V.layer_voxels(lay, box).copy()
    before = grid.copy()
    rows, cols = box.shape(view)
    for op in ops:
        kind = op["op"]
        mirror = op.get("mirror", mirror_default)
        if kind in SOLID_OPS:
            mat = material_index(project, op.get("mat"))
            if mat is None:
                raise ValueError(f"{kind} needs a mat (or \"empty\")")
            _solid(grid, box, op, mat, mirror)
            continue
        if kind == "clear":
            grid[:] = V.EMPTY
            continue
        if kind == "shift":
            grid = _moved(grid, grid.shape, _view_step(view, *op["by"]))
            continue
        if "slice" in op:
            t = int(op["slice"])
            if not 0 <= t < _depth_axis(box, view):
                raise ValueError(f"slice is 0 to {_depth_axis(box, view) - 1} in the {view} view")
            m = np.ascontiguousarray(V.view_rays(grid, view)[:, :, t])
            d = np.full_like(m, t)
        else:
            m, d = V.project_view(grid, view)
        touched = []
        m, d = _draw_op(project, box, view, op, m, d, mirror_default, touched)
        edits = [(view, x, y, int(m[y, x]), int(d[y, x])) for x, y in dict.fromkeys(touched)]
        if mirror and view in ("side", "left"):
            other = "left" if view == "side" else "side"
            edits += [(other, cols - 1 - x, y, em, ed) for _, x, y, em, ed in edits]
        vox = V.voxel_dict(grid, box, materials, None)
        for v, x, y, em, ed in edits:
            _ray_edit(vox, v, x, y, em, ed, box, materials, op)
        grid = V.grid_from_dict(vox, box, materials)
    lay["voxels"] = V.encode_voxels(grid)
    lay["native"] = None
    lay.pop("views", None)
    return int((grid != before).sum())


def _ray_edit(vox, view, x, y, m, d, box, materials, op):
    """One drawn pixel of a voxel layer's view along its line of sight."""
    ray = V._rays(view, y, x, box)[0]
    n = len(ray)

    def put(t):
        if m == V.EMPTY:
            vox.pop(ray[t], None)
        else:
            vox[ray[t]] = (materials[m], None)

    if "slice" in op:
        put(int(op["slice"]))
        return
    span = None
    if op.get("through"):
        span = (d, n - 1)
    elif "extrude" in op:
        e = op["extrude"]
        span = (int(e[0]), int(e[1])) if isinstance(e, (list, tuple)) else (d, d + int(e) - 1)
    if span:
        a, b = sorted(span)
        for t in range(max(0, a), min(n - 1, b) + 1):
            put(t)
        return
    if m == V.EMPTY and op.get("erase") == "surface":
        first = next((k for k in ray if k in vox), None)
        if first is not None:
            del vox[first]
        return
    profile = None
    if m != V.EMPTY and not any(k in vox for k in ray):
        profile = V._profile(vox, view, y, x, box, False, 0)
    V._draw_ray(vox, ray, m, d, materials, None, profile)


def _solid(grid, box, op, mat, mirror):
    """A box or cylinder of voxels set to a material (or emptied), and its mirror image."""
    L, W, H = grid.shape
    F = (np.arange(L) + box.f0)[:, None, None]
    R = (np.arange(W) + box.r0)[None, :, None]
    Z = (np.arange(H) + box.z0)[None, None, :]

    def inside(r):
        if op["op"] == "box":
            (fa, ra, za), (fb, rb, zb) = op["from"], op["to"]
            return ((F >= min(fa, fb)) & (F <= max(fa, fb)) & (r >= min(ra, rb)) & (r <= max(ra, rb)) &
                    (Z >= min(za, zb)) & (Z <= max(za, zb)))
        axis = op.get("axis", "f")
        if axis not in ("f", "r", "z"):
            raise ValueError("A cylinder's axis is f, r or z")
        along, u, v = {"f": (F, r, Z), "r": (r, F, Z), "z": (Z, F, r)}[axis]
        cu, cv = op["center"]
        a, b = op.get("from", -10 ** 6), op.get("to", 10 ** 6)
        rad = float(op["radius"])
        return (along >= min(a, b)) & (along <= max(a, b)) & ((u + 0.5 - cu) ** 2 + (v + 0.5 - cv) ** 2 <= rad * rad)

    mask = inside(R)
    if mirror:
        mask = mask | inside(-R - 1)
    grid[np.broadcast_to(mask, grid.shape)] = mat


def _view_step(view, dx, dy):
    """How a view's columns and rows move the grid's i, j and k."""
    dx, dy = int(dx), int(dy)
    return {"top": (dx, dy, 0), "side": (dx, 0, -dy), "left": (-dx, 0, -dy),
            "front": (0, -dx, -dy), "back": (0, dx, -dy)}[view]


def _moved(grid, shape, by):
    """A grid moved by (di, dj, dk) into a grid of a shape; what falls outside is left out."""
    out = np.full(shape, V.EMPTY, np.uint8)
    src, dst = [], []
    for n, size, s in zip(grid.shape, shape, by):
        lo, hi = max(0, -s), min(n, size - s)
        if hi <= lo:
            return out
        src.append(slice(lo, hi))
        dst.append(slice(lo + s, hi + s))
    out[tuple(dst)] = grid[tuple(src)]
    return out


def bake_layer(project, lay, gen=None):
    """A layer made a voxel layer, shaped by its own voxels from now on (see voxels.bake). Returns
    how many voxels it has."""
    box = V.Box.from_dict(project["box"])
    n = project["layers"].index(lay)
    grid = V.bake(gen, lay, box, project["materials"], lay.get("role") or f"L{n}")
    lay["voxels"] = V.encode_voxels(grid)
    lay["native"] = None
    lay.pop("views", None)
    return int((grid != V.EMPTY).sum())


def auto_depth(m, d, x, y, default):
    """The depth of the drawn pixel nearest (x, y), or the default with none within 8."""
    rows, cols = m.shape
    for r in range(1, 9):
        best = None
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                X, Y = x + dx, y + dy
                if max(abs(dx), abs(dy)) != r or not (0 <= X < cols and 0 <= Y < rows) or m[Y, X] == V.EMPTY:
                    continue
                if best is None or dx * dx + dy * dy < best[0]:
                    best = (dx * dx + dy * dy, int(d[Y, X]))
        if best:
            return best[1]
    return default


def seed_side(lay, box, view, m, d):
    """Drawing on a blank side or left view of a layer on both sides starts from the other side,
    mirrored, which is what that side showed while blank: a view drawn carves the layer, so one
    holding only a detail would cut the rest away. Returns how many pixels it seeded."""
    if view not in ("side", "left") or lay.get("sides", "both") != "both" or (m != V.EMPTY).any():
        return 0
    om, od = grid(lay, box, "left" if view == "side" else "side")
    if (om == V.EMPTY).all():
        return 0
    m[:] = om[:, ::-1]
    d[:] = od[:, ::-1]
    return int((m != V.EMPTY).sum())


# -- text views -------------------------------------------------------------------------------------

CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*+=?"


def legend_for(project, used):
    """A character for each material used: its first letter where free."""
    out, taken = {}, {".", " "}
    for i in sorted(used):
        name = project["materials"][i]
        for ch in [name[0], name[0].upper()] + list(CHARS):
            if ch not in taken:
                out[i] = ch
                taken.add(ch)
                break
    return out


def slice_plane(box, view, t):
    """Where a view's slice t deep lies: the axis and its vehicle coordinate."""
    return {"top": ("z", box.z0 + box.height - 1 - t), "side": ("r", box.r0 + box.width - 1 - t),
            "left": ("r", box.r0 + t), "front": ("f", box.f0 + box.length - 1 - t), "back": ("f", box.f0 + t)}[view]


def show(project, lay, view, depth=False, slice_at=None):
    """A view as text: one character a pixel ('.' nothing), a legend, and a ruler every 10 columns;
    with depth, a second grid of depths in base 36. A voxel layer shows its projection, or with
    slice_at its cross-section that deep from the view's side."""
    box = V.Box.from_dict(project["box"])
    title = f"{lay['name']} / {view}"
    if slice_at is not None:
        g = V.layer_voxels(lay, box)
        if g is None:
            raise ValueError(f"{lay['name']} isn't a voxel layer: only those have slices (cmuov.py layer FILE bake LAYER)")
        t = int(slice_at)
        if not 0 <= t < _depth_axis(box, view):
            raise ValueError(f"slice is 0 to {_depth_axis(box, view) - 1} in the {view} view")
        m = np.ascontiguousarray(V.view_rays(g, view)[:, :, t])
        d = np.zeros_like(m)
        depth = False
        axis, at = slice_plane(box, view, t)
        title += f" slice {t} ({axis} = {at})"
    else:
        m, d = grid(lay, box, view)
    rows, cols = m.shape
    used = {int(i) for i in np.unique(m) if i != V.EMPTY}
    legend = legend_for(project, used)
    ruler = "".join(str((x // 10) % 10) if x % 10 == 0 else " " for x in range(cols))
    lines = [f"{title}: {cols} columns x {rows} rows, depth 0-{_depth_axis(box, view) - 1}",
             "     " + ruler, "     " + "".join(str(x % 10) for x in range(cols))]
    for y in range(rows):
        lines.append(f"{y:4d} " + "".join("." if m[y, x] == V.EMPTY else legend[int(m[y, x])] for x in range(cols)))
    lines.append("legend: " + ", ".join([f"{ch}={project['materials'][i]}" for i, ch in legend.items()] + [".=empty"]))
    if depth:
        lines.append("")
        lines.append("depth (base 36, '.' empty):")
        for y in range(rows):
            lines.append(f"{y:4d} " + "".join("." if m[y, x] == V.EMPTY else np.base_repr(int(d[y, x]), 36).lower()[-1]
                                              for x in range(cols)))
    return "\n".join(lines)


# -- reshaping ------------------------------------------------------------------------------------

def reshape(project, size, mode="keep", gen=None):
    """The project in a box of another size. Keep moves each drawing with the vehicle's centre and
    trims or pads around it, so the generator's model stays where it was: a layer loaded from it
    starts again from its model in the new box, with what was drawn on it moved across. Stretch
    scales every drawing (and its depths) with the box, for a longer, wider or taller vehicle, and so
    leaves the generator's model behind. A voxel layer's voxels move or scale the same way."""
    old = V.Box.from_dict(project["box"])
    new_box = V.Box(*size)
    materials = project["materials"]
    for lay in project["layers"]:
        g = V.layer_voxels(lay, old)
        if g is not None:
            # Voxels keep their place about the vehicle's centre, or are scaled with the box.
            shape = (new_box.length, new_box.width, new_box.height)
            if mode == "stretch":
                ii, jj, kk = (np.minimum(((np.arange(b) + 0.5) * a / b).astype(int), a - 1) for a, b in zip(g.shape, shape))
                g = g[np.ix_(ii, jj, kk)]
            else:
                g = _moved(g, shape, (old.f0 - new_box.f0, old.r0 - new_box.r0, old.z0 - new_box.z0))
            lay["voxels"] = V.encode_voxels(g)
            continue
        if mode != "stretch" and lay.get("native") and gen is not None and \
                V.native_drawing(gen, lay, new_box, materials) is not None:
            edits = V.native_edits(gen, lay, old, materials)
            for view in V.VIEWS:
                m, d = (a.copy() for a in V.native_drawing(gen, lay, new_box, materials)[view])
                dx, dy, dd = _keep_shift(old, new_box, view, lay.get("sides", "both"))
                for v, y, x, em, ed in edits:
                    X, Y = x + dx, y + dy
                    if v == view and 0 <= X < m.shape[1] and 0 <= Y < m.shape[0]:
                        m[Y, X] = em
                        d[Y, X] = 0 if em == V.EMPTY else int(np.clip(ed + dd, 0, _depth_axis(new_box, view) - 1))
                put(lay, view, m, d)
            continue
        for view in V.VIEWS:
            m, d = grid(lay, old, view)
            rows, cols = new_box.shape(view)
            if mode == "stretch":
                ys = np.minimum((np.arange(rows) + 0.5) * m.shape[0] / rows, m.shape[0] - 1).astype(int)
                xs = np.minimum((np.arange(cols) + 0.5) * m.shape[1] / cols, m.shape[1] - 1).astype(int)
                nm, nd = m[np.ix_(ys, xs)], d[np.ix_(ys, xs)].astype(float)
                scale = _depth_axis(new_box, view) / _depth_axis(old, view)
                nd = np.where(nm == V.EMPTY, 0, np.clip(np.round(nd * scale), 0, _depth_axis(new_box, view) - 1))
                put(lay, view, nm, nd.astype(np.uint8))
                continue
            dx, dy, dd = _keep_shift(old, new_box, view, lay.get("sides", "both"))
            nm = np.full((rows, cols), V.EMPTY, np.uint8)
            nd = np.zeros((rows, cols), np.uint8)
            for y, x in zip(*np.nonzero(m != V.EMPTY)):
                X, Y = x + dx, y + dy
                if 0 <= X < cols and 0 <= Y < rows:
                    nm[Y, X] = m[y, x]
                    nd[Y, X] = int(np.clip(int(d[y, x]) + dd, 0, _depth_axis(new_box, view) - 1))
            put(lay, view, nm, nd)
        if mode == "stretch":
            lay["native"] = None
    project["box"] = new_box.as_dict()
    return project


def _depth_axis(box, view):
    return {"top": box.height, "side": box.width, "left": box.width}.get(view, box.length)


def _keep_shift(a, b, view, sides):
    """How far a view's columns, rows and depths move when the box changes around the vehicle's
    centre (f and r from -size/2, z from the ground)."""
    half = lambda n: n // 2  # noqa: E731
    far = lambda n: n - 1 - n // 2  # noqa: E731
    dH = b.height - a.height
    if view == "top":
        return half(b.length) - half(a.length), half(b.width) - half(a.width), dH
    if view == "side":
        dd = (half(b.width) - half(a.width)) if sides == "left" else (far(b.width) - far(a.width))
        return half(b.length) - half(a.length), dH, dd
    if view == "left":
        return far(b.length) - far(a.length), dH, half(b.width) - half(a.width)
    if view == "front":
        return far(b.width) - far(a.width), dH, far(b.length) - far(a.length)
    return half(b.width) - half(a.width), dH, half(b.length) - half(a.length)


# -- damage stages --------------------------------------------------------------------------------

def stages(project, of):
    """Every level of a layer's damage states, as layers."""
    return [l for l in project["layers"] if (l.get("stage") or {}).get("of") == of["name"]]


def state_name(st):
    """The damage state a level belongs to (a stage from before states is a state of its own)."""
    return st["stage"].get("state") or st["name"]


def states(project, of):
    """A layer's damage states in order: {"name", "type", "levels": [(from, layer), ...] lowest first}."""
    out = {}
    for st in stages(project, of):
        s = out.setdefault(state_name(st), {"name": state_name(st), "type": st["stage"].get("type"), "levels": []})
        s["levels"].append((int(st["stage"].get("from", 0)), st))
    for s in out.values():
        s["levels"].sort(key=lambda e: e[0])
    return list(out.values())


def unique_name(project, name):
    taken = {l["name"] for l in project["layers"]}
    out, n = name, 2
    while out in taken:
        out, n = f"{name} {n}", n + 1
    return out


def add_stage(project, of, state, damage_type, amount, blank=False, link=None):
    """A level of one of a layer's damage states: its drawing at that much damage of the state's type,
    starting as a copy of the state's level below it (or the layer itself, for a new state), blank,
    or an existing layer linked as it. A new state takes the damage type given; a level of one it has
    takes the state's."""
    box = V.Box.from_dict(project["box"])
    own = next((s for s in states(project, of) if s["name"] == state), None)
    if own and any(f == amount for f, _ in own["levels"]):
        raise ValueError(f"{state} already has a level at {amount}")
    if link is not None:
        st = layer(project, link)
        if st is of:
            raise ValueError("A layer can't be its own damage state")
        project["layers"].remove(st)
    elif blank:
        st = blank_layer(box, of["kind"], state)
        st["sides"], st["thickness"] = of["sides"], of["thickness"]
        if of.get("voxels"):
            st["voxels"] = V.encode_voxels(V.blank_voxels(box))
            del st["views"]
    else:
        below = [l for f, l in (own["levels"] if own else []) if f < amount]
        st = json.loads(json.dumps(below[-1] if below else of))
    st["rides"] = None
    st["stage"] = {"of": of["name"], "state": state, "type": own["type"] if own else damage_type, "from": amount}
    st["name"] = unique_name(project, f"{state} {amount}")
    # Levels sit right after their layer and its other levels.
    at = project["layers"].index(of) + 1 + len(stages(project, of))
    project["layers"].insert(at, st)
    return st


def set_state(project, of, state, name=None, damage_type=None):
    """Renames one of a layer's damage states (its levels' layers follow) or changes its damage type."""
    own = next((s for s in states(project, of) if s["name"] == state), None)
    if own is None:
        raise ValueError(f"{of['name']} has no damage state {state}")
    if name and name != state and any(s["name"] == name for s in states(project, of)):
        raise ValueError(f"{of['name']} already has a damage state {name}")
    for _, st in own["levels"]:
        if damage_type:
            st["stage"]["type"] = damage_type
        if name:
            st["stage"]["state"] = name
            name_level(project, st)
    return own


def name_level(project, st):
    """A level's layer named after its state and level."""
    st["stage"]["state"] = state_name(st)
    want = f"{st['stage']['state']} {st['stage']['from']}"
    clash = any(l is not st and l["name"] == want for l in project["layers"])
    st["name"] = unique_name(project, want) if clash else want


def rename(project, lay, name):
    """Renames a layer, and what rides on it or is its damage stage follows."""
    old = lay["name"]
    lay["name"] = name
    for l in project["layers"]:
        if l.get("rides") == old:
            l["rides"] = name
        if (l.get("stage") or {}).get("of") == old:
            l["stage"]["of"] = name


def for_render(project, show_stages=None):
    """The project as the renderer takes it, showing the damage stages named: {layer: stage}, or
    "all" for each layer's last stage."""
    out = json.loads(json.dumps(project))
    for lay in out["layers"]:
        if lay.get("stage"):
            continue
        own = states(out, lay)
        if show_stages == "all" and own:
            lay["show_stage"] = own[-1]["levels"][-1][1]["name"]
        elif isinstance(show_stages, dict) and lay["name"] in show_stages:
            lay["show_stage"] = show_stages[lay["name"]]
    return out


def damage_types(repo):
    """The game's damage types and groups, read from its prototypes."""
    types, groups = [], {}
    folder = os.path.join(repo, "Resources", "Prototypes", "Damage")
    for name, kind in (("types.yml", "damageType"), ("groups.yml", "damageGroup")):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        current = None
        members = None
        for line in open(path, encoding="utf-8"):
            s = line.strip()
            if s.startswith("- type:"):
                current = {} if s.split(":", 1)[1].strip() == kind else None
                members = None
            elif current is not None and s.startswith("id:"):
                current["id"] = s.split(":", 1)[1].strip()
                if kind == "damageType":
                    types.append(current["id"])
                else:
                    groups[current["id"]] = []
            elif current is not None and kind == "damageGroup" and s.startswith("damageTypes:"):
                members = groups.get(current.get("id"), [])
            elif members is not None and s.startswith("- "):
                members.append(s[2:].strip())
    return {"types": types, "groups": groups}


def b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr, np.uint8).tobytes()).decode()
