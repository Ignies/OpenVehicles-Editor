"""A vehicle drawn in the tool, written as a solid model for the game's 3D view (CMU3D): a cmu3DModel
prototype for the vehicle and one for its overlay entity, under the 3D model library:

    Resources/ThreeD/Prototypes/World/cmu_open_vehicle_<slug>.yml

    result = export_3d.write_3d(gen, project, repo, ident, slug, tex)
    result = export_3d.after_game(gen, project, repo, export_game.write(gen, project, repo))

The 3D view swaps a vehicle for solid boxes only while every sprite layer it shows (its RSI and
state) has an entry in the model's vehicleLayers, and only while the boxes of the layers it shows at
once come to no more than 128 (CMU3DSceneCatalog.Vehicle.cs). So every state the vehicle's own sprite
can show gets an entry: its body, its engine whole and smoking, each panel shut, open, swinging and
(the hood) crumpled, each lamp whole and broken, its wheels turning and shot out, and the layers that
only draw over it (frame damage, the crumpled front, rims) with no boxes of their own. The states are
read from the RSI the game export wrote, so a state it adds or drops is seen here.

Units are the 3D view's: one tile is one unit, x and y across the ground and z up, the vehicle facing
south (its front at -y, its right side at -x), the ground at the bottom of its lowest wheel. A voxel
(f, r, z) is the box x in [-(r + 1), -r] / 32, y in [-(f + 1), -f] / 32, z in [z - ground, z + 1 -
ground] / 32.

Each part of the vehicle becomes boxes the way equipment_hull.cuboids() does it, greedily grown from
one voxel while what they take in is the same colour, except that voxels inside the part, which no one
sees, take any colour: exact boxes. While the boxes for what the vehicle shows by default come to more
than the budget, two touching boxes of a part become the one box around both, the pair that costs
least that way first: the empty voxels the new box takes in, another part's voxels it takes in (it
would hide them: the engine under the hood, the tub behind a door) and the voxels it shows off their
colour. The crumpled hood is merged down to no more boxes than the shut one. Open panels are
their shut boxes turned about the panel's hinge (yaw for a door, pitch for a lid), by the angle of the
last frame of the swing the sprites draw, so they take no more boxes than shut. Each wheel is a
tyre and a hub, two CylinderX parts.
"""

import heapq
import json
import math
import os
import re

import numpy as np
from PIL import Image, ImageDraw

import export as X
import voxels as V

TILE = 32
# A vehicle's visible layers may take no more boxes than this together (CMU3DSceneCatalog.Vehicle.cs).
PART_LIMIT = 128
# What its default look aims for, leaving room for a panel's dented pose.
BUDGET = 100
MODEL_DIR = ("Content.CMU", "Resources", "ThreeD", "Prototypes", "World")
TYRE_MATS = X.TYRE_MATS
# How many empty voxels a box may take in for one voxel shown this far off its colour (RGB distance).
COLOUR_WEIGHT = 1 / 20
# What a box taking in another part's voxel costs, in empty voxels: it would hide that part (an engine
# under its hood, the body behind an open door).
OTHER_WEIGHT = 6
# Ways to grow a box from its first voxel: round robin, or one axis after another.
GROWTH = ("rr", (0, 1, 2), (1, 2, 0), (2, 0, 1), (0, 2, 1), (1, 0, 2), (2, 1, 0))
DARKER = {"broken": 0.6, "smoking": 0.8}


def fmt(v):
    """A number as the prototypes take it: plain decimals, no exponent, no minus zero."""
    text = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def label_text(text):
    """A part label, quoted when YAML would read it as something else."""
    if re.search(r"[:#'\"\[\]{},&*!|>%@`]", text) or text != text.strip():
        return "'" + text.replace("'", "''") + "'"
    return text


def hex_colour(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c))):02X}" for c in rgb[:3])


def shade(rgb, k):
    return tuple(c * k for c in rgb[:3])


# -- voxels to boxes ------------------------------------------------------------------------------

class Grid:
    """A part's voxels in an array: whether each is there, its colour (an index into colours), its
    material, and whether it shows (any side open)."""

    def __init__(self, vox, colour_of, others=()):
        keys = list(vox)
        self.lo = tuple(min(k[a] for k in keys) for a in range(3))
        hi = tuple(max(k[a] for k in keys) + 1 for a in range(3))
        shape = tuple(h - l for h, l in zip(hi, self.lo))
        self.colours = []
        index = {}
        self.occ = np.zeros(shape, bool)
        self.col = np.full(shape, -1, np.int32)
        self.mats = {}
        for k, mat in vox.items():
            rgb = colour_of(mat)
            if rgb not in index:
                index[rgb] = len(self.colours)
                self.colours.append(rgb)
            p = tuple(k[a] - self.lo[a] for a in range(3))
            self.occ[p] = True
            self.col[p] = index[rgb]
            self.mats[p] = mat
        self.vis = visible(self.occ)
        # Other parts' voxels inside this one's bounds.
        self.taken = np.zeros(shape, bool)
        for k in others:
            p = tuple(k[a] - self.lo[a] for a in range(3))
            if all(0 <= p[a] < shape[a] for a in range(3)) and not self.occ[p]:
                self.taken[p] = True
        c = np.array(self.colours, float)
        # Per colour a box might take, what each colour shown under it costs.
        self.cost = np.linalg.norm(c[:, None, :] - c[None, :, :], axis=2) * COLOUR_WEIGHT

    def boxes(self, found):
        """Boxes in array cells to boxes in the drawing's voxels, each named for the material most
        of what it shows is: [(lo, hi, rgb, material)]."""
        out = []
        for lo, hi, c in found:
            sl = tuple(slice(lo[a], hi[a]) for a in range(3))
            mats = {}
            for p in np.argwhere(self.vis[sl] & (self.col[sl] == c)):
                m = self.mats[tuple(int(p[a]) + lo[a] for a in range(3))]
                mats[m] = mats.get(m, 0) + 1
            mat = max(mats, key=mats.get) if mats else "fill"
            out.append((tuple(lo[a] + self.lo[a] for a in range(3)), tuple(hi[a] + self.lo[a] for a in range(3)),
                        self.colours[c], mat))
        return out


def visible(occ):
    """Voxels with a side open to the outside of the array's own voxels."""
    pad = np.pad(occ, 1)
    inner = np.ones_like(occ)
    for a in range(3):
        for d in (-1, 1):
            inner &= np.roll(pad, d, axis=a)[1:-1, 1:-1, 1:-1]
    return occ & ~inner


def grow(ok, start, order):
    """The box from start out, over the voxels ok allows, the way order grows it."""
    lo = list(start)
    hi = [s + 1 for s in start]
    shape = ok.shape

    def fits(a, at):
        sl = [slice(lo[i], hi[i]) for i in range(3)]
        sl[a] = at
        return ok[tuple(sl)].all()

    if order == "rr":
        moved = True
        while moved:
            moved = False
            for a in range(3):
                if lo[a] > 0 and fits(a, lo[a] - 1):
                    lo[a] -= 1
                    moved = True
                if hi[a] < shape[a] and fits(a, hi[a]):
                    hi[a] += 1
                    moved = True
    else:
        for a in order:
            while lo[a] > 0 and fits(a, lo[a] - 1):
                lo[a] -= 1
            while hi[a] < shape[a] and fits(a, hi[a]):
                hi[a] += 1
    return tuple(lo), tuple(hi)


def exact(grid):
    """Boxes standing exactly for a part: every showing voxel covered in its own colour, each box
    grown wherever its colour or a hidden voxel is, then those another box already covers dropped.
    [(lo, hi, colour index)] in array cells."""
    occ, col, vis = grid.occ, grid.col, grid.vis
    covered = np.zeros(occ.shape, bool)
    ok_of = {}
    boxes = []
    for start in map(tuple, np.argwhere(vis)):
        if covered[start]:
            continue
        c = int(col[start])
        if c not in ok_of:
            ok_of[c] = occ & (~vis | (col == c))
        best = None
        for order in GROWTH:
            lo, hi = grow(ok_of[c], start, order)
            sl = tuple(slice(lo[a], hi[a]) for a in range(3))
            score = (int((vis[sl] & (col[sl] == c) & ~covered[sl]).sum()),
                     int(np.prod([hi[a] - lo[a] for a in range(3)])))
            if best is None or score > best[0]:
                best = (score, lo, hi, sl)
        _, lo, hi, sl = best
        covered[sl] |= vis[sl] & (col[sl] == c)
        boxes.append((lo, hi, c))
    # A box all of whose showing voxels another of its colour also covers adds nothing.
    count = np.zeros(occ.shape, np.int32)
    masks = []
    for lo, hi, c in boxes:
        sl = tuple(slice(lo[a], hi[a]) for a in range(3))
        m = vis[sl] & (col[sl] == c)
        count[sl] += m
        masks.append((sl, m))
    keep = [True] * len(boxes)
    for i in sorted(range(len(boxes)), key=lambda i: int(masks[i][1].sum())):
        sl, m = masks[i]
        if (count[sl][m] >= 2).all():
            keep[i] = False
            count[sl] -= m
    return [b for b, k in zip(boxes, keep) if k]


def cost(grid, lo, hi):
    """A box's colour, and what it costs: the empty voxels it takes in, other parts' voxels it takes
    in (OTHER_WEIGHT apiece), and its showing voxels off that colour (COLOUR_WEIGHT per RGB step)."""
    sl = tuple(slice(lo[a], hi[a]) for a in range(3))
    o, v = grid.occ[sl], grid.vis[sl]
    shown = np.bincount(grid.col[sl][v], minlength=len(grid.colours))
    if shown.any():
        costs = grid.cost @ shown
        colour = int(np.argmin(costs))
        off = float(costs[colour])
    else:
        colour = int(np.bincount(grid.col[sl][o], minlength=1).argmax()) if o.any() else 0
        off = 0.0
    return colour, float(o.size - o.sum()) + (OTHER_WEIGHT - 1) * float(grid.taken[sl].sum()) + off


def touching(a, b):
    """Whether two boxes (lo, hi) meet or overlap."""
    return all(a[0][i] <= b[1][i] and b[0][i] <= a[1][i] for i in range(3))


def merge_down(grids, found, budget):
    """Parts' exact boxes merged down to no more than budget all told: again and again, the two
    touching boxes of a part whose box around both costs least over what they cost apart become
    that box. {key: [(lo, hi, colour index)]}."""
    boxes = {}
    by_key = {key: set() for key in found}
    heap = []
    serial = 0

    def add(key, lo, hi, colour=None, price=None):
        nonlocal serial
        if price is None:
            colour, price = cost(grids[key], lo, hi)
        serial += 1
        boxes[serial] = (key, lo, hi, colour, price)
        for other in by_key[key]:
            _, olo, ohi, _, oprice = boxes[other]
            if touching((lo, hi), (olo, ohi)):
                ulo = tuple(min(lo[a], olo[a]) for a in range(3))
                uhi = tuple(max(hi[a], ohi[a]) for a in range(3))
                ucolour, uprice = cost(grids[key], ulo, uhi)
                heapq.heappush(heap, (uprice - price - oprice, serial, other, ulo, uhi, ucolour, uprice))
        by_key[key].add(serial)

    for key, mine in found.items():
        for lo, hi, colour in mine:
            add(key, lo, hi)
    total = len(boxes)
    while total > budget and heap:
        _, one, two, lo, hi, colour, price = heapq.heappop(heap)
        if one not in boxes or two not in boxes:
            continue
        key = boxes[one][0]
        for gone in (one, two):
            del boxes[gone]
            by_key[key].discard(gone)
        add(key, lo, hi, colour, price)
        total -= 1
    out = {key: [] for key in found}
    for key, lo, hi, colour, _ in boxes.values():
        out[key].append((lo, hi, colour))
    return out


def fidelity(grid, boxes):
    """How well boxes stand for a part: of its showing voxels, how many they cover and how many in
    their exact colour, the mean colour error there (RGB distance), and how many empty voxels the
    boxes take in."""
    shape = grid.occ.shape
    got = np.full(shape, -1, np.int32)
    colours = []
    for lo, hi, rgb, _ in boxes:
        sl = tuple(slice(lo[a] - grid.lo[a], hi[a] - grid.lo[a]) for a in range(3))
        region = got[sl]
        region[region < 0] = len(colours)
        colours.append(np.array(rgb, float))
    vis = grid.vis
    hit = vis & (got >= 0)
    error = 0.0
    same = 0
    for p in map(tuple, np.argwhere(hit)):
        d = float(np.linalg.norm(np.array(grid.colours[grid.col[p]], float) - colours[got[p]]))
        error += d
        same += d < 1
    return {"voxels": int(grid.occ.sum()), "shown": int(vis.sum()), "covered": int(hit.sum()),
            "exact_colour": same, "colour_error": round(error / max(1, int(hit.sum())), 2),
            "added": int(((got >= 0) & ~grid.occ).sum())}


# -- boxes to parts -------------------------------------------------------------------------------

class Frame:
    """Voxel coordinates to the 3D view's, with the ground at the bottom of the lowest wheel."""

    def __init__(self, ground):
        self.ground = ground

    def box(self, lo, hi):
        """An axis-aligned box of voxels: (min, max) in tiles."""
        return ((-hi[1] / TILE, -hi[0] / TILE, (lo[2] - self.ground) / TILE),
                (-lo[1] / TILE, -lo[0] / TILE, (hi[2] - self.ground) / TILE))


def part(label, mn, mx, rgb, shape="Box", yaw=0.0, pitch=0.0):
    return {"label": label, "shape": shape, "min": tuple(mn), "max": tuple(mx), "color": tuple(rgb[:3]),
            "yaw": yaw, "pitch": pitch}


def swung(p, frame, h, degrees):
    """A part turned about a panel's hinge by degrees, as voxels.turn() turns the panel's voxels:
    about an upright line its yaw, about a line across the vehicle its pitch (yawed a quarter turn,
    so the pitch tips along the vehicle). Its own extents stay as they were."""
    if h is None or not degrees:
        return dict(p)
    plane, (ca, cb), sign = h
    a = math.radians(degrees) * sign
    mn, mx = np.array(p["min"]), np.array(p["max"])
    centre, half = (mn + mx) / 2, (mx - mn) / 2
    out = dict(p)
    if plane == "fr":
        # (f, r) turned by a is (x, y) turned by -a.
        hx, hy = -cb / TILE, -ca / TILE
        t = -a
        dx, dy = centre[0] - hx, centre[1] - hy
        centre = np.array([hx + dx * math.cos(t) - dy * math.sin(t), hy + dx * math.sin(t) + dy * math.cos(t), centre[2]])
        yaw = (math.degrees(t) + 180) % 360 - 180
        out.update(min=tuple(centre - half), max=tuple(centre + half), yaw=round(yaw, 4), pitch=0.0)
        return out
    # (f, z) turned by a is (y, z) turned by -a: +y towards +z.
    hy, hz = -ca / TILE, (cb - frame.ground) / TILE
    t = -a
    dy, dz = centre[1] - hy, centre[2] - hz
    centre = np.array([centre[0], hy + dy * math.cos(t) - dz * math.sin(t), hz + dy * math.sin(t) + dz * math.cos(t)])
    pitch = (math.degrees(t) + 180) % 360 - 180
    # A box is the same turned half a turn about its own axis: keep the pitch within a quarter turn.
    if pitch > 90:
        pitch -= 180
    elif pitch < -90:
        pitch += 180
    # Yawed a quarter turn, the part's own x runs along y and its y along -x.
    local = np.array([half[1], half[0], half[2]])
    out.update(min=tuple(centre - local), max=tuple(centre + local), yaw=90.0, pitch=round(pitch, 4))
    return out


def wheel_parts(vox, colour_of, frame, destroyed=False):
    """Each wheel (wheel voxels by side and by run along the vehicle): a tyre and a hub standing a
    little proud of it, both CylinderX."""
    out = []
    for right in (False, True):
        side = {k: m for k, m in vox.items() if (k[1] >= 0) == right}
        fs = sorted({k[0] for k in side})
        runs, start = [], None
        for i, f in enumerate(fs):
            start = f if start is None else start
            if i + 1 == len(fs) or fs[i + 1] - f > 1:
                runs.append((start, f))
                start = None
        for f0, f1 in runs:
            members = {k: m for k, m in side.items() if f0 <= k[0] <= f1}
            lo = tuple(min(k[a] for k in members) for a in range(3))
            hi = tuple(max(k[a] for k in members) + 1 for a in range(3))
            where = ("front" if (f0 + f1) / 2 > 0 else "rear") + (" right" if right else " left")
            tyre = [m for m in members.values() if m in TYRE_MATS] or list(members.values())
            hub = {k: m for k, m in members.items() if m not in TYRE_MATS}
            tyre_rgb = np.mean([colour_of(m) for m in tyre], axis=0)
            if destroyed:
                tyre_rgb = colour_of("char") if "char" in TYRE_MATS else shade(tyre_rgb, 0.5)
            mn, mx = frame.box(lo, hi)
            out.append(part(f"{where} tyre", mn, mx, tyre_rgb, "CylinderX"))
            if hub:
                hlo = tuple(min(k[a] for k in hub) for a in range(3))
                hhi = tuple(max(k[a] for k in hub) + 1 for a in range(3))
                hub_rgb = colour_of("gun") if destroyed else np.mean([colour_of(m) for m in hub.values()], axis=0)
                hmn, hmx = frame.box((hlo[0], lo[1], hlo[2]), (hhi[0], hi[1], hhi[2]))
                proud = 1 / 256
                out.append(part(f"{where} hub", (hmn[0] - proud, hmn[1], hmn[2]), (hmx[0] + proud, hmx[1], hmx[2]),
                                hub_rgb, "CylinderX"))
    return out


def as_parts(label, boxes, frame, k=1.0):
    return [part(f"{label} {mat}", *frame.box(lo, hi), shade(rgb, k)) for lo, hi, rgb, mat in boxes]


# -- the model ------------------------------------------------------------------------------------

def part_yaml(p, indent):
    pad = " " * indent
    out = (f"{pad}- label: {label_text(p['label'])}\n{pad}  shape: {p['shape']}\n"
           f"{pad}  min: {', '.join(fmt(v) for v in p['min'])}\n{pad}  max: {', '.join(fmt(v) for v in p['max'])}\n"
           f"{pad}  color: '{hex_colour(p['color'])}'\n")
    if p.get("yaw"):
        out += f"{pad}  yaw: {fmt(p['yaw'])}\n"
    if p.get("pitch"):
        out += f"{pad}  pitch: {fmt(p['pitch'])}\n"
    return out


def parts_yaml(parts, indent):
    if not parts:
        return " []\n"
    return "\n" + "".join(part_yaml(p, indent) for p in parts)


def rsi_states(repo, tex, slug):
    """The states of the vehicle's RSI, as the game export wrote it."""
    path = os.path.join(repo, "Content.CMU", "Resources", "Textures", *tex.split("/"), f"{slug}.rsi", "meta.json")
    with open(path, encoding="utf-8") as fh:
        return [s["name"] for s in json.load(fh)["states"]], path


def build_parts(gen, project, budget=BUDGET, progress=None):
    """Everything the model needs from the drawing: per sprite state family its parts, how far each
    part was simplified and how well its boxes stand for it."""
    say = progress or (lambda text: None)
    g = gen.module
    box = V.Box.from_dict(project["box"])
    layers = project["layers"]
    role = X.roles(project)
    ids = {n: X.part_id(lay) for n, lay in enumerate(layers)}
    by_name = {lay["name"]: n for n, lay in enumerate(layers)}
    tint = tuple(getattr(g, "GLASS_TINT", (176, 206, 204, 78))[:3])

    def colour_of(mat):
        if mat == V.GLASS:
            return tint
        ramp = g.PAL.get(mat)
        return tuple(ramp["top"][:3]) if isinstance(ramp, dict) else (128, 128, 128)

    say("Building the voxels")
    with V.styled(gen, project, box):
        models, owners, per_layer, _ = V.build(project, box, project["materials"], ["S"], gen=gen)
        vox, owner = models["S"], owners["S"]
        # The hood crumpled with the front, as the sprites draw it.
        hoods = [n for n, r in role.items() if r == "panel" and layers[n].get("kind") == "hood"]
        dents = {}
        for level in range(1, X.CRUMPLE_LEVELS + 1):
            if hoods:
                dented = g.crumple(g.Model(vox), level, X.seed("crumple"))
                for n in hoods:
                    dents[(n, level)] = {k: v[0] for k, v in dented.items() if owner.get(k) == n}

    # Kit riding a panel goes with it.
    host = {}
    for n, lay in enumerate(layers):
        if lay.get("rides") in by_name and role.get(by_name[lay["rides"]]) == "panel":
            host[n] = by_name[lay["rides"]]
    groups = {}
    for k, (mat, _) in vox.items():
        n = owner[k]
        n = host.get(n, n)
        r = role.get(n)
        key = "base" if r == "base" else ("wheels" if r == "wheels" else n)
        groups.setdefault(key, {})[k] = mat
    wheel_vox = groups.pop("wheels", {})
    ground = min(k[2] for k in wheel_vox) if wheel_vox else min(k[2] for k in vox)
    frame = Frame(ground)

    grids = {key: Grid(v, colour_of, [k for other, w in groups.items() if other != key for k in w] + list(wheel_vox))
             for key, v in groups.items()}
    for (n, level), v in dents.items():
        if v:
            grids[("dent", n, level)] = Grid(v, colour_of, [k for other, w in groups.items() if other != n for k in w])
    wheels = wheel_parts(wheel_vox, colour_of, frame)
    panels = [n for n, r in role.items() if r == "panel"]

    say("Merging voxels into boxes")
    # Each part's exact boxes, merged down together until they fit the budget.
    shown = [k for k in grids if not isinstance(k, tuple)]
    exacts = {k: exact(grids[k]) for k in shown}
    found = merge_down({k: grids[k] for k in shown}, exacts, budget - len(wheels))
    method = {k: "exact" if len(found[k]) == len(exacts[k]) else "merged" for k in shown}
    # A crumpled hood stands in for the shut one: no more boxes than it.
    for key in [k for k in grids if isinstance(k, tuple)]:
        exacts[key] = exact(grids[key])
        found[key] = merge_down({key: grids[key]}, {key: exacts[key]}, max(len(found.get(key[1], [])), 1))[key]
        method[key] = "exact" if len(found[key]) == len(exacts[key]) else "merged"

    def name(key):
        return "body" if key == "base" else layers[key]["name"].lower()

    out = {"frame": frame, "wheels": wheels, "fidelity": {}, "groups": {}, "ids": ids,
           "role": role, "hinges": {}, "swing": {}, "vox": vox, "owner": owner, "host": host}
    out["wheels_destroyed"] = wheel_parts(wheel_vox, colour_of, frame, destroyed=True)
    for key in grids:
        b = grids[key].boxes(found[key])
        label = name(key) if not isinstance(key, tuple) else f"{name(key[1])} dented {key[2]}"
        out["groups"][key] = as_parts(label, b, frame)
        out["fidelity"][label] = dict(fidelity(grids[key], b), boxes=len(b), method=method[key],
                                      exact_boxes=len(exacts[key]))
        if isinstance(key, int):
            if role[key] == "engine":
                out["groups"][(key, "smoking")] = as_parts(label, b, frame, DARKER["smoking"])
            if role[key] == "lamp":
                out["groups"][(key, "broken")] = as_parts(label, b, frame, DARKER["broken"])
    # Each panel's swing: its hinge, found from its own voxels as the sprites find it, and the angle
    # of its last frame.
    for n in panels:
        own = (per_layer[n] or {}).get("S", {})
        kind = layers[n].get("kind")
        angles = gen.swing_angles(kind)
        h = V.hinge(kind, layers[n].get("sides", "both"), own) if own and angles else None
        out["hinges"][n] = h
        out["swing"][n] = angles[-1] if angles else 0
        if n in out["groups"]:
            out["groups"][(n, "open")] = [swung(p, frame, h, out["swing"][n]) for p in out["groups"][n]]
    return out


def classify(states, built):
    """Every RSI state the vehicle's own sprite can show, with its parts: {state: parts}. The
    states of its overlay entity, its hover outlines, click masks, seats and wreck are elsewhere."""
    ids, role, groups = built["ids"], built["role"], built["groups"]
    out = {}
    for n, r in role.items():
        pid = ids[n]
        if r == "engine":
            out[f"{pid}_0"] = groups.get(n, [])
            out[f"{pid}_1"] = groups.get((n, "smoking"), [])
        elif r == "lamp":
            out[f"{pid}_0"] = groups.get(n, [])
            out[f"{pid}_1"] = groups.get((n, "broken"), [])
        elif r == "panel":
            shut, opened = groups.get(n, []), groups.get((n, "open"), [])
            # Mid-swing a panel is shown where it is going; the RSI's frames carry the swing itself.
            out.update({f"{pid}_closed": shut, f"{pid}_closing": shut, f"{pid}_open": opened, f"{pid}_opening": opened})
            for level in range(1, X.CRUMPLE_LEVELS + 1):
                if ("dent", n, level) in groups:
                    out[f"{pid}_closed_dent{level}"] = groups[("dent", n, level)]
    out["base"] = groups.get("base", [])
    # Drawn over the body, with no shape of their own: the frame's damage and the crumpled front.
    out["damaged_frame"] = []
    for level in range(1, X.CRUMPLE_LEVELS + 1):
        out[f"front_dent_{level}"] = []
    # The wheels, turning (and with any tyre's own art) or shot out; a rim design is painted on them.
    for s in states:
        if "overlay" in s or s.startswith(("click_", "rims_")):
            continue
        m = re.fullmatch(r"(?:\w+_)?wheels_([01])", s)
        if m:
            out[s] = built["wheels_destroyed"] if m.group(1) == "1" else built["wheels"]
    for s in states:
        if re.fullmatch(r"rims_(?:\w+_)?wheels_\d", s):
            out[s] = []
    present = set(states)
    missing = sorted(s for s in out if s not in present)
    return {s: p for s, p in out.items() if s in present}, missing


def default_set(built, states):
    """The states the vehicle shows when it is spawned: everything fitted, whole, shut and still."""
    ids, role = built["ids"], built["role"]
    want = ["base"]
    for n, r in role.items():
        if r == "engine" or r == "lamp":
            want.append(f"{ids[n]}_0")
        elif r == "panel":
            want.append(f"{ids[n]}_closed")
    want.append("wheels_0")
    return [s for s in want if s in states]


def worst_set(built, states):
    """The most parts any mix of the vehicle's states can show at once: each layer at its largest."""
    ids, role = built["ids"], built["role"]
    total = len(states.get("base", [])) + max(len(states.get(s, [])) for s in states if re.fullmatch(r"(?:\w+_)?wheels_[01]", s))
    for n, r in role.items():
        pid = ids[n]
        family = [s for s in states if s.startswith(pid + "_") and
                  re.fullmatch(r"closed|closing|open|opening|closed_dent\d+|[01]", s[len(pid) + 1:])]
        if r in ("engine", "lamp", "panel") and family:
            total += max(len(states[s]) for s in family)
    return total


def overlay_anchor(base):
    """The overlay entity's single part: a speck deep inside the body's largest box, so the 3D view
    takes the overlay as modelled (its sprite is all drawn over the body) and shows nothing for it."""
    if not base:
        return part("overlay anchor", (-1 / 512, -1 / 512, 1 / 512), (1 / 512, 1 / 512, 2 / 512), (0, 0, 0))
    big = max(base, key=lambda p: np.prod(np.array(p["max"]) - np.array(p["min"])))
    centre = (np.array(big["min"]) + np.array(big["max"])) / 2
    half = min(1 / 128, float(min(np.array(big["max"]) - np.array(big["min"]))) / 4)
    return part("overlay anchor (hidden inside the body)", centre - half, centre + half, big["color"])


def write_3d(gen, project, repo, ident, slug, tex, budget=BUDGET, progress=None, preview_path=None):
    """Writes the vehicle's 3D model into the repo, after its sprites (export_game.write()), and
    returns what it wrote. tex is the folder of its RSI under Textures (export_game.paths()["tex"])."""
    say = progress or (lambda text: None)
    states, meta_path = rsi_states(repo, tex, slug)
    built = build_parts(gen, project, budget, say)
    entries, missing = classify(states, built)
    rsi = f"{tex}/{slug}.rsi"
    defaults = default_set(built, entries)
    default_parts = [p for s in defaults for p in entries[s]]
    worst = worst_set(built, entries)
    veh = project.get("vehicle") or {}
    name = (veh.get("name") or slug.replace("_", " ")).strip()
    model_id = f"CMU3DOpenVehicle{ident}"
    source = f"CMUVehicle{ident}"
    anchor = overlay_anchor(entries.get("base", []))

    layers_yaml = "".join(f"  - rsi: {rsi}\n    state: {s}\n    parts:{parts_yaml(p, 4)}" for s, p in entries.items())
    yml = f"""# Written by CMU Open Vehicles from {os.path.basename(veh.get('source', slug + '.cmuov'))}.
# The {name} as solid parts for the 3D view: one entry per sprite state its layers can show.

- type: cmu3DModel
  id: {model_id}
  label: {label_text(name)}
  status: draft
  description: Drawn in CMU Open Vehicles; voxels merged into boxes, panels turned on their hinges.
  sourcePrototypes:
  - {source}
  useEntityRotation: true
  vehicleSpriteScale: 1, 1
  vehicleSpriteOffset: 0, 0
  parts:{parts_yaml(default_parts, 2)}  vehicleLayers:
{layers_yaml}
- type: cmu3DModel
  id: {model_id}Overlay
  label: {label_text(name + ' overlay')}
  status: draft
  description: What the {name} draws over its riders. The vehicle's own model has all its shape.
  sourcePrototypes:
  - {source}Overlay
  useEntityRotation: true
  parts:{parts_yaml([anchor], 2)}"""
    path = os.path.join(repo, *MODEL_DIR, f"cmu_open_vehicle_{slug}.yml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(yml)

    result = {
        "path": path, "model": model_id, "overlay_model": f"{model_id}Overlay", "rsi_meta": meta_path,
        "states": len(entries), "default_states": defaults, "default_parts": len(default_parts),
        "worst_parts": worst, "limit": PART_LIMIT, "budget": budget,
        "parts_per_state": {s: len(p) for s, p in entries.items()},
        "not_in_rsi": missing, "fidelity": built["fidelity"],
        "ignored_states": sorted(set(states) - set(entries)),
        "warnings": [],
    }
    if len(default_parts) > budget:
        result["warnings"].append(f"{len(default_parts)} parts by default, over the budget of {budget}: "
                                  "each part keeps at least one box")
    if worst > PART_LIMIT:
        result["warnings"].append(f"up to {worst} parts at once, over the 3D view's {PART_LIMIT}: "
                                  "it draws the vehicle as a sprite then")
    if preview_path:
        say("Drawing the preview")
        preview(gen, project, built, entries, defaults, preview_path)
        result["preview"] = preview_path
    return result


def after_game(gen, project, repo, written, **options):
    """write_3d() for what export_game.write() just wrote (its result): the same vehicle id, RSI and
    folder."""
    ident = written["id"][len("CMUVehicle"):]
    slug = os.path.basename(written["rsi"])[:-len(".rsi")]
    tex = "/".join(os.path.relpath(os.path.dirname(written["rsi"]),
                                   os.path.join(repo, "Content.CMU", "Resources", "Textures")).split(os.sep))
    return write_3d(gen, project, repo, ident, slug, tex, **options)


# -- a quick look ---------------------------------------------------------------------------------

LIGHT = np.array([-0.35, -0.6, 1.0]) / np.linalg.norm([-0.35, -0.6, 1.0])
VIEWS_3D = {"front right": np.array([-1.0, -1.0, 1.1]), "rear left": np.array([1.0, 1.0, 1.1])}


class Camera:
    """An isometric camera looking at the vehicle from eye (a direction), in voxels."""

    def __init__(self, eye, scale, origin):
        self.eye = eye / np.linalg.norm(eye)
        forward = -self.eye
        right = np.cross(forward, [0, 0, 1.0])
        self.right = right / np.linalg.norm(right)
        self.up = np.cross(self.right, forward)
        self.scale, self.origin = scale, origin

    def point(self, p):
        return (self.origin[0] + float(np.dot(p, self.right)) * self.scale,
                self.origin[1] - float(np.dot(p, self.up)) * self.scale)

    def depth(self, p):
        return -float(np.dot(p, self.eye))


def lit(rgb, normal):
    k = 0.55 + 0.45 * max(0.0, float(np.dot(normal, LIGHT)))
    return tuple(int(max(0, min(255, c * k))) for c in rgb[:3])


def voxel_faces(vox, colour_of, cam):
    """Each voxel's open sides that face the camera, in voxels with the vehicle facing south."""
    faces = []
    sides = [((1, 0, 0), 0), ((-1, 0, 0), 0), ((0, 1, 0), 1), ((0, -1, 0), 1), ((0, 0, 1), 2), ((0, 0, -1), 2)]
    for (f, r, z), mat in vox.items():
        base = np.array([-r - 1, -f - 1, z], float)
        for d, a in sides:
            if (f + d[0], r + d[1], z + d[2]) in vox:
                continue
            # (f, r, z) steps to (x, y, z) steps: x = -r, y = -f.
            n = np.array([-d[1], -d[0], d[2]], float)
            if np.dot(n, cam.eye) <= 0:
                continue
            centre = base + 0.5 + n * 0.5
            u, w = [np.eye(3)[i] * 0.5 for i in range(3) if n[i] == 0]
            quad = [centre - u - w, centre + u - w, centre + u + w, centre - u + w]
            faces.append((cam.depth(centre), quad, lit(colour_of(mat), n)))
    return faces


def part_faces(p, cam, ground):
    """A part's sides facing the camera, cut into pieces a voxel or two across so they sort."""
    mn, mx = np.array(p["min"]) * TILE, np.array(p["max"]) * TILE
    centre, half = (mn + mx) / 2, (mx - mn) / 2
    yaw, pitch = math.radians(p.get("yaw", 0)), math.radians(p.get("pitch", 0))

    def world(v):
        x, y, z = v
        x, z = x * math.cos(pitch) - z * math.sin(pitch), x * math.sin(pitch) + z * math.cos(pitch)
        x, y = x * math.cos(yaw) - y * math.sin(yaw), x * math.sin(yaw) + y * math.cos(yaw)
        return np.array([x, y, z])

    faces = []
    if p["shape"] == "CylinderX":
        seg = 14
        ring = [(math.cos(2 * math.pi * i / seg), math.sin(2 * math.pi * i / seg)) for i in range(seg)]
        for i in range(seg):
            (c0, s0), (c1, s1) = ring[i], ring[(i + 1) % seg]
            quad = [centre + world((x, half[1] * c, half[2] * s)) for x, c, s in
                    ((-half[0], c0, s0), (half[0], c0, s0), (half[0], c1, s1), (-half[0], c1, s1))]
            n = world((0, (c0 + c1) / 2, (s0 + s1) / 2))
            if np.dot(n, cam.eye) > 0:
                faces.append((cam.depth(sum(quad) / 4), quad, lit(p["color"], n)))
        for sx in (-1, 1):
            n = world((sx, 0, 0))
            if np.dot(n, cam.eye) > 0:
                cap = [centre + world((sx * half[0], half[1] * c, half[2] * s)) for c, s in ring]
                faces.append((cam.depth(centre + n * half[0]) - 0.01, cap, lit(p["color"], n)))
        return faces
    for a in range(3):
        for sign in (-1, 1):
            n_local = np.zeros(3)
            n_local[a] = sign
            n = world(n_local)
            if np.dot(n, cam.eye) <= 0:
                continue
            b, c = [i for i in range(3) if i != a]
            nb, nc = max(1, math.ceil(2 * half[b] / 2)), max(1, math.ceil(2 * half[c] / 2))
            for i in range(nb):
                for j in range(nc):
                    corners = []
                    for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                        v = np.zeros(3)
                        v[a] = sign * half[a]
                        v[b] = -half[b] + 2 * half[b] * (i + di) / nb
                        v[c] = -half[c] + 2 * half[c] * (j + dj) / nc
                        corners.append(centre + world(v))
                    faces.append((cam.depth(sum(corners) / 4), corners, lit(p["color"], n)))
    return faces


def draw(faces, cam, size):
    img = Image.new("RGB", size, (236, 236, 230))
    d = ImageDraw.Draw(img)
    for _, quad, colour in sorted(faces, key=lambda f: -f[0]):
        d.polygon([cam.point(q) for q in quad], fill=colour)
    return img


def preview(gen, project, built, entries, defaults, path):
    """The voxels and the boxes side by side, shut and with every panel open, from the front right
    and the rear left, to check the boxes by eye."""
    g = gen.module
    tint = tuple(getattr(g, "GLASS_TINT", (176, 206, 204, 78))[:3])

    def colour_of(mat):
        if mat == V.GLASS:
            return tint
        ramp = g.PAL.get(mat)
        return tuple(ramp["top"][:3]) if isinstance(ramp, dict) else (128, 128, 128)

    box = V.Box.from_dict(project["box"])
    ground = built["frame"].ground
    shut = {k: v[0] for k, v in built["vox"].items()}
    shut = {(f, r, z - ground): m for (f, r, z), m in shut.items()}
    # Every panel open, each from the last frame of its own swing.
    opened = dict(shut)
    role, owner, host = built["role"], built["owner"], built["host"]
    panels = [n for n, r in role.items() if r == "panel"]
    with V.styled(gen, project, box):
        for n in panels:
            frames = V.swing_scenes(gen, project, box, project["materials"], n, "S")
            if not frames:
                continue
            last, last_owner = frames[-1]
            mine = {n} | {i for i, h in host.items() if h == n}
            for k in [k for k in opened if owner.get((k[0], k[1], k[2] + ground)) in mine]:
                del opened[k]
            for k, v in last.items():
                if last_owner.get(k) in mine:
                    opened[(k[0], k[1], k[2] - ground)] = v[0]
    open_states = []
    for s in defaults:
        alt = s[:-len("_closed")] + "_open" if s.endswith("_closed") else s
        open_states.append(alt if alt in entries else s)
    shut_parts = [p for s in defaults for p in entries[s]]
    open_parts = [p for s in open_states for p in entries[s]]
    scale = 4
    cell = (int(box.length * 1.25 * scale + 40), int(box.length * 0.95 * scale + 40))
    sheet = Image.new("RGB", (cell[0] * 4, cell[1] * 2 + 24), (236, 236, 230))
    d = ImageDraw.Draw(sheet)
    for row, eye in enumerate(VIEWS_3D.values()):
        for col, (title, kind, what) in enumerate((("voxels shut", "v", shut), (f"{len(shut_parts)} parts shut", "p", shut_parts),
                                                   ("voxels open", "v", opened), (f"{len(open_parts)} parts open", "p", open_parts))):
            cam = Camera(eye, scale, (cell[0] / 2, cell[1] * 0.6))
            if kind == "v":
                faces = voxel_faces(what, colour_of, cam)
            else:
                faces = [f for p in what for f in part_faces(p, cam, ground)]
            sheet.paste(draw(faces, cam, cell), (col * cell[0], 24 + row * cell[1]))
            if row == 0:
                d.text((col * cell[0] + 8, 6), title, fill=(30, 30, 30))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sheet.save(path)
