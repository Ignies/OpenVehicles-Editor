"""Vehicle templates: whole open vehicles to start a project from, built procedurally as voxel layers.

Each template is a car-like vehicle laid out in the generator's space (f forward, r to its right, z
up, one voxel a 32nd of a tile), every part on a layer of its own and named the way the export reads
them: Body, Engine (role engine), Hood, the doors (Driver door on the left, Passenger door on the
right), a boot lid, tailgate or rear hatch, Fuel door (right side), Windshield, Seats (role seat),
Steering wheel, six lamps (Headlight, Turn signal and Taillight, left and right) and Wheels. Every
layer is a voxel layer, so the app opens it ready to paint, slice and draw on, and it's symmetric
left to right. The vehicle block names it and seats a driver and a passenger over the seats, each by
their door.

    import templates
    project = templates.make("pickup")                  # a project dict, ready for project.save
    project = templates.make("car", box=(76, 40, 28))   # the same layout in another box
    templates.wheels(project, [22, -20], 7, 30, width=6)  # new wheels on any project

    python templates.py car car.cmuov       # one template to a file
    python templates.py --all outdir        # every template, as outdir/<name>.cmuov

Templates are drawn in their own box and scale with another: lengths, widths and heights move with
it, walls and panels keep their thickness and the wheels stay round.
"""

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import project as P  # noqa: E402
import voxels as V  # noqa: E402

EMPTY = V.EMPTY

# The jeep generator's palette, in its order. A project made here keeps these names; the app, the
# preview and the export remap materials by name, so another generator's palette works as well.
MATERIALS = [
    "od", "od_light", "od_in", "od_seam", "frame", "rubber", "groove", "rim", "rim_dk", "rim_hi", "hub",
    "steel", "gun", "canvas", "canvas_seam", "wood", "strap", "white", "lamp", "red", "red_line", "amber",
    "black", "char", "yellow", "fuel", "fuel_dk", "axe_red", "axe_wood", "etool", "jack_orange", "brass",
    "lit", "lit_red", "lit_brake", "lit_amber", "lens", "lens_dk", "lit_dim", "od_flat", "od_in_flat",
    "white_flat", "red_flat", "red_line_flat", "rust", "glass",
]

# Each template's own box: length, width, height, small enough that its sprites fit the game's 96
# pixel canvas in every direction.
BOXES = {"car": (70, 38, 26), "pickup": (72, 42, 32), "van": (74, 42, 34), "jeep-like": (68, 40, 32)}


# -- wheels ---------------------------------------------------------------------------------------

def wheel_grid(box, materials, axles, radius, track, width=5, rim=None, windows=4):
    """A set of wheels as a voxel grid over the box: a pair on each axle (f), their centres track
    apart across the vehicle and radius up, so they stand on z = 0. Each is a true cylinder: a rubber
    tyre with a grooved tread, and on its outer face a rim (radius rim) with a dark lip, windows
    between its spokes, bright lug nuts and a hub. The rim's pattern is in the face's own colours,
    not cut into it, so the slanted views still see it, and it has fourfold symmetry (windows a
    multiple of four) so the tool's four wheel frames, a quarter turn, loop."""
    idx = {m: i for i, m in enumerate(materials)}

    def mat(name):
        return idx.get(name, idx.get("rubber", 0))

    grid = V.blank_voxels(box)
    radius = float(radius)
    rim = float(rim) if rim is not None else radius * 0.64
    f = (np.arange(box.length) + box.f0 + 0.5)[:, None]
    z = (np.arange(box.height) + box.z0 + 0.5)[None, :]
    rc = (np.arange(box.width) + box.r0 + 0.5)
    # The right wheel's slices across, outer face last; the left one is its mirror (r to -r-1).
    right = [j for j in range(box.width) if abs(rc[j] - track / 2) <= width / 2]
    if not right:
        raise ValueError("The wheels fall outside the box: a smaller track or a wider box")
    outer = max(right)
    inner = min(right)
    for fc in axles:
        u, v = f - fc, z - radius
        d = np.hypot(u, v)
        a = np.arctan2(v, u)
        disc = d <= radius
        tread = d > radius - 0.9
        grooves = (a / (2 * math.pi / 24)) % 1 < 0.35
        # The rim face: lip, windows between the spokes, lug nuts, hub.
        pitch = 2 * math.pi / windows
        off = np.abs((a % pitch) - pitch / 2) * d          # distance from a window's middle line
        lugs = (np.abs(d - rim * 0.45) < 0.55) & (np.abs(((a + pitch / 2) % pitch) - pitch / 2) * d < 0.6)
        face = np.full(d.shape, mat("rim"), np.uint8)
        face[(d > rim * 0.45 + 0.4) & (d < rim - 0.8) & (off < rim * 0.24)] = mat("rim_dk")
        face[lugs] = mat("rim_hi")
        face[d < 1.0] = mat("hub")
        face[d > rim - 0.8] = mat("rim_dk")
        for j in right:
            depth = outer - j                              # 0 on the outer face
            m = np.where(d > rim, mat("rubber"), mat("rim_dk"))
            if depth == 0:
                m = np.where(d > rim, mat("rubber"), face)
            band = depth not in (0, outer - inner)
            m = np.where(tread & grooves & band, mat("groove"), m)
            m = np.where(disc, m, EMPTY).astype(np.uint8)
            for jj in (j, box.width - 1 - j):              # the right wheel and its mirror on the left
                sel = m != EMPTY
                grid[:, jj, :][sel] = m[sel]
    return grid


def wheels(project, axles, radius, track, width=5, layer="Wheels", rim=None, windows=4):
    """Adds the wheels to a project, or replaces its layer of that name in place: a voxel layer of
    kind wheel with a pair on each axle (see wheel_grid). The preview and export find them as they
    find drawn ones: each axle a group of voxels along the length, its hub at the middle of its
    height, its outer faces the rims. Returns the layer."""
    box = V.Box.from_dict(project["box"])
    grid = wheel_grid(box, project["materials"], axles, radius, track, width, rim, windows)
    lay = _layer(layer, "wheel", grid)
    for n, old in enumerate(project["layers"]):
        if old["name"].lower() == layer.lower():
            lay["visible"] = old.get("visible", True)
            project["layers"][n] = lay
            return lay
    project["layers"].append(lay)
    return lay


def _layer(name, kind, grid, sides="both", role=None, rides=None):
    return {"name": name, "kind": kind, "sides": sides, "thickness": 0, "visible": True, "role": role,
            "rides": rides, "native": None, "stage": None, "voxels": V.encode_voxels(grid)}


# -- a vehicle built from solids ------------------------------------------------------------------

class Scale:
    """A template's numbers, drawn for its own box, in another: f positions scale with the length, r
    with the width, z with the height; wheel sizes with the smaller of length and height, so they
    stay round. Thicknesses aren't scaled."""

    def __init__(self, design, box):
        self.sf = box.length / design[0]
        self.sr = box.width / design[1]
        self.sz = box.height / design[2]
        self.sw = min(self.sf, self.sz)

    def f(self, x):
        return int(round(x * self.sf))

    def r(self, x):
        return int(round(x * self.sr))

    def z(self, x):
        return int(round(x * self.sz))

    def w(self, x):
        return max(3, int(round(x * self.sw)))


class Model:
    """A vehicle being built: a voxel grid per part, and masks over the box to build them with. Masks
    test each voxel's centre, so a range (a, b) of whole numbers takes the voxels from a to b - 1, and
    anything given as a half width (|r| < half) is symmetric left to right."""

    def __init__(self, box, materials=MATERIALS):
        self.box = box
        self.materials = list(materials)
        self.idx = {m: i for i, m in enumerate(self.materials)}
        self.shape = (box.length, box.width, box.height)
        self.F = (np.arange(box.length) + box.f0 + 0.5)[:, None, None]
        self.R = (np.arange(box.width) + box.r0 + 0.5)[None, :, None]
        self.Z = (np.arange(box.height) + box.z0 + 0.5)[None, None, :]
        self.A = np.abs(self.R)
        self.parts = {}
        self.specs = []
        self.cavities = []

    # Masks.

    def full(self, mask):
        return np.broadcast_to(mask, self.shape)

    def block(self, f=None, z=None, half=None, r=None, out=None):
        """Voxels with f and z in their ranges, |r| < half (and >= out), or r in a range."""
        m = np.ones(self.shape, bool)
        if f is not None:
            m &= self.full((self.F > f[0]) & (self.F < f[1]))
        if z is not None:
            m &= self.full((self.Z > z[0]) & (self.Z < z[1]))
        if half is not None:
            m &= self.full(self.A < half)
        if out is not None:
            m &= self.full(self.A > out)
        if r is not None:
            m &= self.full((self.R > r[0]) & (self.R < r[1]))
        return m

    def across(self, fc, zc, radius):
        """A cylinder across the vehicle (axis r), in f and z."""
        return self.full((self.F - fc) ** 2 + (self.Z - zc) ** 2 <= radius * radius)

    # Parts.

    def part(self, name, kind="part", sides="both", role=None, rides=None):
        if name not in self.parts:
            self.parts[name] = np.full(self.shape, EMPTY, np.uint8)
            self.specs.append((name, kind, sides, role, rides))
        return self.parts[name]

    def m(self, name):
        return self.idx[name]

    def put(self, name, mask, mat):
        self.parts[name][mask] = self.m(mat)

    def cut(self, name, mask):
        self.parts[name][mask] = EMPTY

    def has(self, name):
        return self.parts[name] != EMPTY

    def move(self, src, dst, mask, mat=None):
        """Voxels from one part to another, keeping their material unless given one."""
        mask = mask & self.has(src)
        self.parts[dst][mask] = self.parts[src][mask] if mat is None else self.m(mat)
        self.parts[src][mask] = EMPTY
        return mask

    def cavity(self, mask, kind="in"):
        """Hollows the body out: a cockpit or a bed (its faces painted as the interior), an engine
        bay (dark frame) or a boot."""
        self.cut("Body", mask)
        self.cavities.append((mask, kind))

    def paint_cavities(self):
        """The body's faces on each cavity painted: interior colour in the cockpit, bed and boot,
        the frame's dark grey in the engine bay."""
        for kind in ("bay", "in"):
            near = np.zeros(self.shape, bool)
            for mask, k in self.cavities:
                if k == kind:
                    near |= _grow(mask)
            for name in ("Body", "Tailgate", "Rear hatch"):
                g = self.parts.get(name)
                if g is not None:
                    g[near & (g == self.m("od"))] = self.m("frame" if kind == "bay" else "od_in")

    # Building blocks.

    def envelope(self, tops, nose, tail, half, bottom, nose_corner=4, tail_corner=3, shoulder=0, lift=1):
        """The body's outer shape, solid od: from tail to nose (f edges), up from bottom (raised by
        lift at each end, like a bumper's underside) to the top given per stretch of length as
        [(f0, f1, top)], |r| < half, the corners rounded in plan, the top row inset by shoulder."""
        top = np.zeros(self.box.length)
        fs = np.arange(self.box.length) + self.box.f0 + 0.5
        for f0, f1, t in tops:
            top[(fs > f0) & (fs < f1)] = t
        top = top[:, None, None]
        hw = np.full(fs.shape, float(half))
        for edge, c, sign in ((nose, nose_corner, 1), (tail, tail_corner, -1)):
            if c:
                x = (fs - (edge - sign * c)) * sign
                hw = np.where(x > 0, half - c + np.sqrt(np.clip(c * c - x * x, 0, None)), hw)
        hw = hw[:, None, None]
        low = np.where((self.F > nose - 3) | (self.F < tail + 3), bottom + lift, bottom)
        inside = (self.F > tail) & (self.F < nose) & (self.Z > low) & (self.Z < top)
        width = np.where(self.Z > top - 1, hw - shoulder, hw)
        solid = self.full(inside & (self.A < width))
        self.part("Body", "body")
        self.put("Body", solid, "od")
        self.env = solid.copy()
        return solid

    def add_body(self, mask, mat="od"):
        """More of the body's outer shape (fenders, a bumper), kept by later arches and cavities."""
        self.put("Body", mask, mat)
        self.env |= mask

    def arches(self, axles, radius, inner, half, clearance=1.5):
        """Wheel arches: the body cut away round each wheel from just inside its inner face out, a
        dark wall inside the arch, and a flat-topped housing over the wheel where it reaches into a
        cockpit or a bed."""
        body = self.parts["Body"]
        env = self.env
        # Each column's top skin, two voxels deep, stays over the arch: the fender.
        top = np.where(env.any(2), env.shape[2] - np.argmax(env[:, :, ::-1], 2), 0)[:, :, None]
        under = self.full(self.Z < top - 2)
        ra = radius + clearance
        for fc in axles:
            arch = self.across(fc, radius, ra) & under
            wall = arch & self.block(out=inner - 2, half=inner - 1)
            # The housing is a box with a flat top: every step in height would draw a line.
            house = self.block(f=(fc - ra - 1, fc + ra + 1), z=(radius, radius + ra + 1), out=inner - 2, half=half)
            house &= ~arch & under
            body[house & env & (body == EMPTY)] = self.m("od")
            body[arch & self.block(out=inner - 1)] = EMPTY
            hollow = np.zeros(self.shape, bool)
            for mask, _ in self.cavities:
                hollow |= mask
            body[wall & env & ~hollow] = self.m("black")
            body[wall & env & hollow] = self.m("od")

    def seams(self, name, outer_sign, f_edges, z_edges):
        """A shut line round a panel's outer face: its outermost voxel in each (f, z) column, on the
        given f and z edges, in od_seam."""
        g = self.parts[name]
        occ = g != EMPTY
        if outer_sign > 0:
            j = self.box.width - 1 - np.argmax(occ[:, ::-1, :], axis=1)
        else:
            j = np.argmax(occ, axis=1)
        any_ = occ.any(axis=1)
        fi, zi = np.nonzero(any_)
        for i, k in zip(fi.tolist(), zi.tolist()):
            f, z = i + self.box.f0, k + self.box.z0
            if f in f_edges or z in z_edges:
                if g[i, j[i, k], k] in (self.m("od"),):
                    g[i, j[i, k], k] = self.m("od_seam")

    def surface(self, name, face, mask2d, deep=1, reach=3):
        """The first voxels of a part seen from one face ('front', 'back', 'right', 'left'), where a
        2D mask over that face is set: front and back masks are over (r, z), side masks over (f, z).
        Returns a mask of those voxels, deep voxels in. Only voxels within reach of the nearest one
        count, so a lamp at a corner doesn't reach past it into the cockpit or a bed."""
        occ = self.parts[name] != EMPTY
        # The part's voxels as rays from that face: [a, b, t], t counting in from the face.
        rays = {"front": occ.transpose(1, 2, 0)[:, :, ::-1], "back": occ.transpose(1, 2, 0),
                "right": occ.transpose(0, 2, 1)[:, :, ::-1], "left": occ.transpose(0, 2, 1)}[face]
        first = np.argmax(rays, axis=2)
        hit = rays.any(axis=2) & mask2d
        if not hit.any():
            return np.zeros(self.shape, bool)
        hit &= first <= first[hit].min() + reach
        sel = np.zeros(rays.shape, bool)
        t = np.arange(rays.shape[2])[None, None, :]
        sel[(t >= first[:, :, None]) & (t < first[:, :, None] + deep) & hit[:, :, None]] = True
        sel &= rays
        if face == "front":
            return sel[:, :, ::-1].transpose(2, 0, 1)
        if face == "back":
            return sel.transpose(2, 0, 1)
        if face == "right":
            return sel[:, :, ::-1].transpose(0, 2, 1)
        return sel.transpose(0, 2, 1)

    def face_grid(self, face):
        """Centre coordinates over a face: (r, z) for front and back, (f, z) for the sides."""
        if face in ("front", "back"):
            return self.R[0, :, :] + 0 * self.Z[0, :, :], self.Z[0, :, :] + 0 * self.R[0, :, :]
        return self.F[:, 0, :] + 0 * self.Z[:, 0, :], self.Z[:, 0, :] + 0 * self.F[:, 0, :]

    def lamp(self, name, face, centre, paint, src="Body", deep=1):
        """A lamp set into a part's face: the voxels round centre ((r, z) on the front or back, (f, z)
        on a side) that paint(du, dv) gives a material, moved onto the lamp's own layer. du runs
        outwards from the vehicle's middle (so a left and a right lamp are mirror images), dv up."""
        side = "left" if "left" in name.lower() else "right"
        g = self.part(name, "lamp", side)
        u, v = self.face_grid(face)
        cu, cv = centre
        du, dv = u - cu, v - cv
        if face in ("front", "back"):
            du = du * (1 if cu > 0 else -1)
        mats = np.vectorize(lambda a, b: paint(a, b) or "", otypes=[object])(du, dv)
        mask2d = mats != ""
        sel = self.surface(src, face, mask2d, deep)
        idx = np.nonzero(sel)
        for i, j, k in zip(*idx):
            a, b = (j, k) if face in ("front", "back") else (i, k)
            g[i, j, k] = self.m(mats[a, b])
        self.parts[src][sel] = EMPTY

    # Interior.

    def seats(self, fs, rs, floor, top, back, name="Seats"):
        """Bucket seats: a cushion over f range fs and each r range in rs from the floor to top, and
        a back two voxels thick behind it up to back, leaning a voxel back above halfway, with a
        headrest. Seams across the cushion and the back."""
        self.part(name, "part", "both", "seat")
        f0, f1 = fs
        for r0, r1 in rs:
            seat = self.block(f=(f0, f1), z=(floor, top), r=(r0, r1))
            self.put(name, seat, "canvas")
            self.put(name, seat & self.block(f=((f0 + f1) // 2 - 1, (f0 + f1) // 2), z=(top - 1, top)), "canvas_seam")
            mid = (floor + back) // 2 + 1
            low = self.block(f=(f0 - 2, f0), z=(floor, mid), r=(r0, r1))
            high = self.block(f=(f0 - 3, f0 - 1), z=(mid - 1, back), r=(r0, r1))
            self.put(name, low | high, "canvas")
            self.put(name, high & self.block(z=(mid + 1, mid + 2)), "canvas_seam")
            head = self.block(f=(f0 - 3, f0 - 1), z=(back - 1, back + 2), r=(r0 + 2, r1 - 2))
            self.put(name, head, "canvas")
            # Bolsters: the cushion's sides a voxel higher.
            side = self.block(f=(f0, f1), z=(top - 1, top + 1), r=(r0, r0 + 1)) | \
                self.block(f=(f0, f1), z=(top - 1, top + 1), r=(r1 - 1, r1))
            self.put(name, side, "canvas")

    def steering(self, f, r, z, column_to, radius=2.6, name="Steering wheel"):
        """A steering wheel: a ring across the vehicle (in r and z) at f before the driver, with a
        spoke and hub, on a column running forward to the dash at column_to."""
        self.part(name, "part", "both")
        ring = self.full(np.hypot(self.R - r, self.Z - z) <= radius) & self.block(f=(f, f + 1))
        inner = self.full(np.hypot(self.R - r, self.Z - z) <= radius - 1.0)
        spoke = self.full(np.abs(self.Z - z) < 0.6) & inner
        hub = self.full(np.hypot(self.R - r, self.Z - z) <= 0.8)
        self.put(name, ring & ~inner, "black")
        self.put(name, ring & (spoke | hub), "gun")
        col = self.block(f=(f + 1, column_to), r=(r - 0.6, r + 0.6), z=(z - 1.6, z - 0.4))
        self.put(name, col, "gun")

    def windshield(self, f, z0, z1, half, rake=0, frame="black", divider=False, name="Windshield"):
        """An open vehicle's windshield, on the cowl at f: a frame round glass, from z0 up to z1,
        |r| < half, two voxels thick (one with rake). Rake leans it back a voxel every that many
        rows. It folds forward on its bottom edge."""
        self.part(name, "windshield", "both")
        g = self.parts[name]
        for z in range(z0, z1):
            back = (z - z0) // rake if rake else 0
            thick = 1 if rake else 2
            sl = self.block(f=(f - back - thick, f - back), z=(z, z + 1), half=half)
            edge = self.block(out=half - 1) | self.full((self.Z > z1 - 1) | (self.Z < z0 + 1))
            if divider:
                edge = edge | self.block(half=0.6)
            g[sl] = self.m("glass")
            g[sl & edge] = self.m(frame)

    def engine(self, f, half, z, name="Engine"):
        """An engine block under the hood: a dark block with a bright rocker cover, an air cleaner and
        a fan at its front."""
        self.part(name, "part", "both", "engine")
        f0, f1 = f
        z0, z1 = z
        self.put(name, self.block(f=f, z=(z0, z1 - 1), half=half), "gun")
        self.put(name, self.block(f=(f0 + 2, f1 - 2), z=(z1 - 1, z1), half=half - 2), "steel")
        self.put(name, self.block(f=(f0 + 3, f1 - 3), z=(z1 - 1, z1), half=0.6), "black")
        cleaner = self.full(np.hypot(self.F - (f0 + f1) / 2, self.R) <= 2.2) & self.block(z=(z1, z1 + 1))
        self.put(name, cleaner, "black")
        fan = self.full(np.hypot(self.R, self.Z - (z0 + z1) / 2) <= min(half, (z1 - z0) / 2) - 0.5)
        self.put(name, fan & self.block(f=(f1, f1 + 1)), "frame")

    def fuel_door(self, f, z, size=3, name="Fuel door"):
        """A fuel door on the right side's outer face, size voxels square, its edge a shut line."""
        g = self.part(name, "fueldoor", "right")
        f0, z0 = f - size // 2, z - size // 2
        u, v = self.face_grid("right")
        patch = (u > f0) & (u < f0 + size) & (v > z0) & (v < z0 + size)
        sel = self.surface("Body", "right", patch)
        g[sel] = self.m("od_seam")
        mid = sel & self.block(f=(f0 + 1, f0 + size - 1), z=(z0 + 1, z0 + size - 1))
        g[mid] = self.m("od")
        self.parts["Body"][sel] = EMPTY

    # The project.

    def project(self, vehicle):
        project = P.new(self.box.as_dict(), self.materials)
        for name, kind, sides, role, rides in self.specs:
            grid = self.parts[name]
            if not (grid != EMPTY).any():
                continue
            project["layers"].append(_layer(name, kind, grid, sides, role, rides))
        project["vehicle"] = vehicle
        return project


def _grow(mask):
    """A mask grown by one voxel in each of the six directions."""
    out = mask.copy()
    for axis in range(3):
        for step in (1, -1):
            out |= np.roll(mask, step, axis) & ~_edge(mask.shape, axis, step)
    return out


def _edge(shape, axis, step):
    """The slab np.roll wraps into, to be left out."""
    e = np.zeros(shape, bool)
    sl = [slice(None)] * 3
    sl[axis] = 0 if step > 0 else -1
    e[tuple(sl)] = True
    return e


# -- lamp faces -----------------------------------------------------------------------------------

def round_headlight(radius=1.8):
    """A round headlight: a dark lens rim, a bright bulb, the lens between."""
    def paint(du, dv):
        d = math.hypot(du, dv)
        if d > radius:
            return None
        if d > radius - 0.7:
            return "lens_dk"
        if d < 0.8:
            return "lamp"
        return "lens"
    return paint


def rect_lamp(w, h, mat, edge=None, low=None):
    """A rectangular lamp w by h about its centre: mat, its rim edge, its bottom row low."""
    def paint(du, dv):
        if abs(du) > w / 2 or abs(dv) > h / 2:
            return None
        if low and dv < -h / 2 + 1:
            return low
        if edge and (abs(du) > w / 2 - 1 or abs(dv) > h / 2 - 1):
            return edge
        return mat
    return paint


# -- the templates --------------------------------------------------------------------------------

def _seat(f, r, z, ident, door, driver=False):
    seat = {"id": ident, "f": f, "r": r, "z": z}
    if driver:
        seat["driver"] = True
    seat["door"] = door
    return seat


def _common(m, d):
    """The cockpit and the hood over its engine bay, laid out by d (positions already scaled to the
    box), cut from the body every template starts with."""
    hw, wall = d["half"], 2
    # The cockpit, from the seats' back wall to the dash.
    c0, c1 = d["cockpit"]
    floor = d["floor"]
    m.cavity(m.block(f=(c0, c1), z=(floor, 99), half=hw - wall))
    # The engine bay under the hood, and the hood: the top voxel between its shut lines.
    h0, h1 = d["hood"]
    hh = d["hood_half"]
    top = d["hood_top"]
    m.part("Engine", "part", "both", "engine")
    m.part("Hood", "hood", "both")
    m.move("Body", "Hood", m.block(f=(h0, h1), z=(top - 1, top), half=hh))
    m.cavity(m.block(f=(h0 + 1, h1 - 1), z=(d["bay_floor"], top - 1), half=hh - 1), "bay")


def _finish(m, d, vehicle_id, name, description):
    """Paints the cavities, then cuts the doors and adds the dash, seats, steering wheel, engine,
    lamps, fuel door and wheels; names the vehicle and seats its riders. Returns the project."""
    hw, wall, floor = d["half"], 2, d["floor"]
    m.paint_cavities()
    # Dash: across the cockpit's front, its top a little under the cowl.
    c0, c1 = d["cockpit"]
    dash = m.block(f=(c1 - 2, c1), z=(floor, d["dash_top"]), half=hw - wall)
    m.put("Body", dash, "od_in")
    m.put("Body", dash & m.block(z=(d["dash_top"] - 1, d["dash_top"])), "black")
    # Doors: the cockpit's side walls between the sill and the beltline.
    d0, d1 = d["door"]
    z0, z1 = d["door_z"]
    for name_, sides, sign in (("Driver door", "left", -1), ("Passenger door", "right", 1)):
        m.part(name_, "door", sides)
        wall_ = m.block(f=(d0, d1), z=(z0, z1), out=hw - wall) & m.full(m.R * sign > 0)
        m.move("Body", name_, wall_)
        m.seams(name_, sign, (d0, d1 - 1), (z0,))
        face = "right" if sign > 0 else "left"
        u, v = m.face_grid(face)
        handle = (u > d0 + 1) & (u < d0 + 4) & (v > z1 - 3) & (v < z1 - 2)
        m.put(name_, m.surface(name_, face, handle), "black")
    # The rest of the cockpit: seats, the steering wheel before the driver, the engine.
    sf = d["seat_f"]
    rs = d["seat_r"]
    m.seats(sf, [rs, (-rs[1], -rs[0])], floor, d["seat_top"], d["seat_back"])
    driver_r = -(rs[0] + rs[1]) / 2
    m.steering(d["wheel_f"], driver_r, d["wheel_z"], c1 - 2)
    e0, e1 = d["engine_f"]
    m.engine((e0, e1), d["engine_half"], (d["bay_floor"], d["hood_top"] - 2))
    # Lamps.
    for side in (1, -1):
        word = "right" if side > 0 else "left"
        hr, hz = d["headlight"]
        m.lamp(f"Headlight {word}", "front", (side * hr, hz), d.get("headlight_paint", round_headlight()))
        sr_, sz_ = d["signal"]
        m.lamp(f"Turn signal {word}", "front", (side * sr_, sz_), rect_lamp(3, 2, "amber"))
        tr, tz = d["taillight"]
        m.lamp(f"Taillight {word}", "back", (side * tr, tz), d.get("taillight_paint", rect_lamp(3, 4, "red", "red_line", "amber")))
    ff, fz = d["fuel"]
    m.fuel_door(ff, fz)
    # The layers in stack order: lamps and wheels last, so they win where they touch.
    order = ["Body", "Engine", "Hood", "Boot lid", "Tailgate", "Rear hatch", "Driver door", "Passenger door",
             "Fuel door", "Seats", "Steering wheel", "Windshield"]
    lamps = [f"{kind} {side}" for kind in ("Headlight", "Turn signal", "Taillight") for side in ("left", "right")]
    rest = [n for n, *_ in m.specs if n not in order and n not in lamps]
    rank = {n: i for i, n in enumerate(order + rest + lamps)}
    m.specs.sort(key=lambda spec: rank[spec[0]])
    seat_f = (sf[0] + sf[1]) / 2
    vehicle = {"id": vehicle_id, "name": name, "description": description,
               "seats": [_seat(seat_f, driver_r, d["seat_top"], "driver", "Driver door", True),
                         _seat(seat_f, -driver_r, d["seat_top"], "passenger", "Passenger door")]}
    project = m.project(vehicle)
    wheels(project, d["axles"], d["wheel_r"], d["track"], d["tyre"])
    return project


def car(box=None):
    """A two door open car, roadster-like: a long low hood, a cockpit for two behind a raked
    windshield, a short rear deck with a boot lid."""
    design = BOXES["car"]
    box = box or V.Box(*design)
    s = Scale(design, box)
    m = Model(box)
    nose, tail = box.f0 + box.length - 1, box.f0 + 1
    hw = s.r(17)
    R = s.w(6)
    d = {
        "half": hw, "floor": s.z(5), "cockpit": (s.f(-14), s.f(6)), "hood": (s.f(7), nose - 2),
        "hood_half": hw - 4, "hood_top": s.z(14), "bay_floor": s.z(6), "dash_top": s.z(14),
        "door": (s.f(-12), s.f(4)), "door_z": (s.z(5), s.z(15)),
        "seat_f": (s.f(-10), s.f(-3)), "seat_r": (s.r(4), s.r(13)), "seat_top": s.z(8), "seat_back": s.z(16),
        "wheel_f": s.f(1), "wheel_z": s.z(14), "engine_f": (s.f(10), s.f(26)), "engine_half": s.r(8),
        "headlight": (s.r(12), s.z(11)), "signal": (s.r(14), s.z(7)), "taillight": (s.r(13), s.z(12)),
        "fuel": (s.f(-19), s.z(13)), "axles": [s.f(21), s.f(-21)], "wheel_r": R,
        "track": 2 * hw - 7, "tyre": 5,
    }
    tops = [(tail, tail + 2, s.z(14)), (tail + 2, s.f(-14), s.z(16)), (s.f(-14), s.f(6), s.z(15)),
            (s.f(6), nose - 2, s.z(14)), (nose - 2, nose, s.z(12))]
    m.envelope(tops, nose, tail, hw, s.z(3), nose_corner=6, tail_corner=4, shoulder=1)
    _common(m, d)
    # The boot under its lid on the rear deck.
    b0, b1 = tail + 3, s.f(-18)
    deck = s.z(16)
    m.part("Boot lid", "boot", "both")
    m.move("Body", "Boot lid", m.block(f=(b0, b1), z=(deck - 1, deck), half=hw - 3))
    m.cavity(m.block(f=(b0 + 1, b1 - 1), z=(s.z(6), deck - 1), half=hw - 4))
    # A grille mouth under the nose, and a plate at the back.
    u, v = m.face_grid("front")
    m.put("Body", m.surface("Body", "front", (np.abs(u) < s.r(8)) & (v > s.z(5)) & (v < s.z(8))), "black")
    u, v = m.face_grid("back")
    m.put("Body", m.surface("Body", "back", (np.abs(u) < s.r(4)) & (v > s.z(8)) & (v < s.z(11))), "white")
    # The soft top, folded behind the seats under its cover.
    m.part("Soft top", "part", "both")
    fold = m.full(np.hypot((m.F - s.f(-16)) / 2.2, (m.Z - deck) / 2.6) <= 1) & m.block(f=(s.f(-18), s.f(-14)), half=hw - 3)
    m.put("Soft top", fold & m.full(m.Z > deck), "black")
    m.put("Soft top", fold & m.full(m.Z > deck) & m.block(z=(deck, deck + 1), out=hw - 5), "gun")
    m.cut("Body", m.has("Soft top"))
    m.arches(d["axles"], R, d["track"] / 2 - d["tyre"] / 2, hw)
    m.windshield(s.f(6), s.z(15), s.z(23), hw - 2, rake=3, frame="black")
    return _finish(m, d, "OpenCar", "open car",
                   "A small two-door open car. Quick and nimble, with a boot for a little cargo and no roof at all.")


def pickup(box=None):
    """A pickup: an open cab behind a long hood, a roll bar over the cab's back wall, and an open
    bed with a tailgate."""
    design = BOXES["pickup"]
    box = box or V.Box(*design)
    s = Scale(design, box)
    m = Model(box)
    nose, tail = box.f0 + box.length - 1, box.f0 + 1
    hw = s.r(20)
    R = s.w(7)
    belt = s.z(19)
    d = {
        "half": hw, "floor": s.z(7), "cockpit": (s.f(-6), s.f(10)), "hood": (s.f(11), nose - 2),
        "hood_half": hw - 4, "hood_top": belt, "bay_floor": s.z(8), "dash_top": s.z(18),
        "door": (s.f(-5), s.f(8)), "door_z": (s.z(7), belt),
        "seat_f": (s.f(-3), s.f(4)), "seat_r": (s.r(4), s.r(14)), "seat_top": s.z(11), "seat_back": s.z(20),
        "wheel_f": s.f(6), "wheel_z": s.z(17), "engine_f": (s.f(14), s.f(30)), "engine_half": s.r(9),
        "headlight": (s.r(14), s.z(15)), "signal": (s.r(14), s.z(11)), "taillight": (s.r(18), s.z(15)),
        "fuel": (s.f(-27), s.z(15)), "axles": [s.f(24), s.f(-23)], "wheel_r": R,
        "track": 2 * hw - 8, "tyre": 6,
        "headlight_paint": rect_lamp(5, 3, "lens", "lens_dk"),
        "taillight_paint": rect_lamp(2, 5, "red", "red_line", "amber"),
    }
    cab_back = s.f(-6)
    tops = [(tail, cab_back - 2, belt), (cab_back - 2, cab_back, belt), (cab_back, nose - 1, belt),
            (nose - 1, nose, belt - 1)]
    m.envelope(tops, nose, tail, hw, s.z(5), nose_corner=3, tail_corner=2, lift=2)
    _common(m, d)
    # The bed, open, with the tailgate as its back wall; a roll bar over the cab's back wall.
    bed0, bed1 = tail, cab_back - 2
    m.cavity(m.block(f=(bed0 + 2, bed1), z=(s.z(9), 99), half=hw - 2))
    m.part("Tailgate", "tailgate", "both")
    m.move("Body", "Tailgate", m.block(f=(tail, tail + 2), z=(s.z(9), belt), half=hw - 2))
    bar = s.z(26)
    hoop = (m.block(f=(cab_back - 2, cab_back), z=(belt - 1, bar), out=hw - 3, half=hw - 1) |
            m.block(f=(cab_back - 2, cab_back), z=(bar - 2, bar), half=hw - 1))
    m.part("Roll bar", "part", "both")
    m.put("Roll bar", hoop, "gun")
    # A grille between the headlights, and the bumpers.
    u, v = m.face_grid("front")
    grille = (np.abs(u) < s.r(11)) & (v > s.z(12)) & (v < s.z(18))
    m.put("Body", m.surface("Body", "front", grille), "black")
    slats = grille & (np.floor(v) % 2 == 0)
    m.put("Body", m.surface("Body", "front", slats), "steel")
    m.put("Body", m.block(f=(nose - 1, nose + 1), z=(s.z(7), s.z(10)), half=hw), "steel")
    m.put("Body", m.block(f=(tail - 1, tail + 1), z=(s.z(6), s.z(9)), half=hw), "steel")
    m.arches(d["axles"], R, d["track"] / 2 - d["tyre"] / 2, hw)
    m.windshield(s.f(11), belt, s.z(29), hw - 2, rake=4, frame="od")
    return _finish(m, d, "OpenPickup", "open pickup",
                   "A light open-top pickup: two seats under the sky, a roll bar over the cab and a bed with a drop tailgate.")


def van(box=None):
    """A tall open-topped utility van: a short nose, a cab for two, and a high-walled cargo box
    behind with a rear hatch."""
    design = BOXES["van"]
    box = box or V.Box(*design)
    s = Scale(design, box)
    m = Model(box)
    nose, tail = box.f0 + box.length - 1, box.f0 + 1
    hw = s.r(20)
    R = s.w(6)
    belt = s.z(17)
    roof = s.z(25)
    d = {
        "half": hw, "floor": s.z(6), "cockpit": (s.f(4), s.f(22)), "hood": (s.f(23), nose - 2),
        "hood_half": hw - 4, "hood_top": s.z(15), "bay_floor": s.z(7), "dash_top": s.z(16),
        "door": (s.f(7), s.f(20)), "door_z": (s.z(6), belt),
        "seat_f": (s.f(9), s.f(16)), "seat_r": (s.r(4), s.r(14)), "seat_top": s.z(10), "seat_back": s.z(19),
        "wheel_f": s.f(18), "wheel_z": s.z(16), "engine_f": (s.f(25), s.f(33)), "engine_half": s.r(9),
        "headlight": (s.r(14), s.z(12)), "signal": (s.r(17), s.z(9)), "taillight": (s.r(18), s.z(14)),
        "fuel": (s.f(-24), s.z(13)), "axles": [s.f(24), s.f(-23)], "wheel_r": R,
        "track": 2 * hw - 8, "tyre": 6,
        "headlight_paint": rect_lamp(5, 3, "lens", "lens_dk"),
        "taillight_paint": rect_lamp(2, 6, "red", "red_line", "amber"),
    }
    box_end = s.f(4)
    tops = [(tail, box_end, roof), (box_end, s.f(22), belt), (s.f(22), nose - 1, s.z(15)),
            (nose - 1, nose, s.z(14))]
    m.envelope(tops, nose, tail, hw, s.z(4), nose_corner=4, tail_corner=2)
    _common(m, d)
    # The cargo box: tall walls, open to the sky, its back wall the rear hatch, ribs on its floor.
    m.cavity(m.block(f=(tail + 2, box_end), z=(s.z(6), 99), half=hw - 2))
    m.part("Rear hatch", "hatch", "both")
    m.move("Body", "Rear hatch", m.block(f=(tail, tail + 2), z=(s.z(8), roof), half=hw - 2))
    ribs = m.block(f=(tail + 3, box_end - 2), z=(s.z(6), s.z(6) + 1), half=hw - 4) & \
        m.full(np.floor(m.A) % 4 == 1)
    m.put("Body", ribs, "od_in")
    # A window over the hatch's top, and a step bumper.
    u, v = m.face_grid("back")
    m.put("Rear hatch", m.surface("Rear hatch", "back", (np.abs(u) < hw - 6) & (v > roof - 6) & (v < roof - 2)), "black")
    m.put("Body", m.block(f=(tail - 1, tail + 1), z=(s.z(4), s.z(7)), half=hw - 1), "steel")
    u, v = m.face_grid("front")
    grille = (np.abs(u) < s.r(10)) & (v > s.z(9)) & (v < s.z(14))
    m.put("Body", m.surface("Body", "front", grille), "black")
    m.put("Body", m.surface("Body", "front", grille & (np.floor(v) % 2 == 1)), "steel")
    m.put("Body", m.block(f=(nose - 1, nose + 1), z=(s.z(5), s.z(8)), half=hw), "steel")
    m.arches(d["axles"], R, d["track"] / 2 - d["tyre"] / 2, hw)
    m.windshield(s.f(23), s.z(15), s.z(27), hw - 2, rake=6, frame="od")
    return _finish(m, d, "OpenVan", "open van",
                   "A tall utility van with no roof: two seats up front, a high-walled cargo box and a rear hatch.")


def jeep_like(box=None):
    """An open 4x4 after the jeep: a flat hood between flat fenders, a slotted grille with round
    headlights, an open tub with a tailgate, big wheels outboard, and an upright windshield that
    folds down onto the hood."""
    design = BOXES["jeep-like"]
    box = box or V.Box(*design)
    s = Scale(design, box)
    m = Model(box)
    nose, tail = box.f0 + box.length - 1, box.f0 + 1
    hw = s.r(15)                      # the tub; the fenders reach out over the wheels
    fw = s.r(19)
    R = s.w(7)
    rim = s.z(19)
    d = {
        "half": hw, "floor": s.z(8), "cockpit": (s.f(-22), s.f(8)), "hood": (s.f(9), nose - 1),
        "hood_half": hw - 2, "hood_top": rim, "bay_floor": s.z(9), "dash_top": s.z(18),
        "door": (s.f(-5), s.f(6)), "door_z": (s.z(9), rim),
        "seat_f": (s.f(-3), s.f(3)), "seat_r": (s.r(2), s.r(11)), "seat_top": s.z(12), "seat_back": s.z(20),
        "wheel_f": s.f(4), "wheel_z": s.z(17), "engine_f": (s.f(12), s.f(28)), "engine_half": s.r(8),
        "headlight": (s.r(8), s.z(15)), "signal": (s.r(16.5), s.z(15)), "taillight": (s.r(13), s.z(15)),
        "fuel": (s.f(-8), s.z(15)), "axles": [s.f(21), s.f(-20)], "wheel_r": R,
        "track": 2 * fw - 8, "tyre": 6,
        "taillight_paint": rect_lamp(2, 4, "red", "red_line", "amber"),
        "headlight_paint": round_headlight(2.0),
    }
    tops = [(tail, nose, rim)]
    m.envelope(tops, nose, tail, hw, s.z(6), nose_corner=1, tail_corner=1, lift=0)
    # Flat fenders over the wheels: long ones from the cowl to the grille, short ones at the back.
    fz = s.z(17)
    front = m.block(f=(s.f(9), nose - 1), z=(fz - 2, fz), out=hw - 1, half=fw)
    front |= m.block(f=(s.f(9), s.f(12)), z=(s.z(10), fz), out=hw - 1, half=fw)   # its back edge, turned down
    rear = m.block(f=(s.f(-29), s.f(-11)), z=(fz - 2, fz), out=hw - 1, half=fw)
    m.add_body(front | rear)
    _common(m, d)
    # The tub behind the seats runs to the tailgate.
    m.cavity(m.block(f=(tail + 2, s.f(-22) + 1), z=(d["floor"], 99), half=hw - 2))
    m.part("Tailgate", "tailgate", "both")
    m.move("Body", "Tailgate", m.block(f=(tail, tail + 2), z=(d["floor"], rim), half=hw - 3))
    # The grille: vertical slots across the nose, and a bumper under it.
    u, v = m.face_grid("front")
    grille = (np.abs(u) < s.r(11)) & (v > s.z(10)) & (v < s.z(18))
    slots = grille & (np.abs(u) < s.r(5.5)) & (np.floor(np.abs(u)) % 2 == 1) & (v > s.z(11)) & (v < s.z(17))
    m.put("Body", m.surface("Body", "front", slots), "black")
    m.put("Body", m.block(f=(nose - 1, nose + 1), z=(s.z(7), s.z(10)), half=fw), "frame")
    m.put("Body", m.block(f=(tail - 1, tail + 1), z=(s.z(7), s.z(9)), half=hw + 1), "frame")
    m.arches(d["axles"], R, d["track"] / 2 - d["tyre"] / 2, fw)
    m.windshield(s.f(10), rim, s.z(30), hw, frame="od", divider=True)
    return _finish(m, d, "OpenFourByFour", "open 4x4",
                   "A rugged open four-by-four: two seats in an open tub, a cargo bed behind them and a windshield that folds flat.")


TEMPLATES = {
    "car": car,
    "pickup": pickup,
    "van": van,
    "jeep-like": jeep_like,
}

DESCRIPTIONS = {
    "car": "Two-door open car with a boot",
    "pickup": "Open cab and a bed with a tailgate",
    "van": "Tall open-top utility van with a rear hatch",
    "jeep-like": "Open 4x4 with a folding windshield",
}


# Finished vehicles kept as projects, offered as templates as they are (their own box).
EXAMPLES = {
    "roadster": (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "roadster", "roadster.cmuov"),
                 "A two-seat civilian roadster, top folded down, drawn in full"),
}


def make(name, box=None):
    """A new project from a template, as a dict (save it with project.save). Box, if given ((length,
    width, height), a dict or a voxels.Box), puts the same layout in a box of that size; whether it
    still fits the game's 96 px frame depends on its shape, which the game export checks
    (tools/check_templates.py checks every template as it comes). An example is its project as saved."""
    if name in EXAMPLES:
        import project as P
        return P.load(EXAMPLES[name][0])
    if name not in TEMPLATES:
        raise KeyError(f"No template {name!r}. Templates: {', '.join(TEMPLATES)}")
    if box is not None and not isinstance(box, V.Box):
        box = V.Box.from_dict(box) if isinstance(box, dict) else V.Box(*box)
    return TEMPLATES[name](box)


def listing():
    """The templates as the app lists them: name, what it is, and its own box; the examples after."""
    out = [{"name": name, "description": DESCRIPTIONS[name], "box": V.Box(*BOXES[name]).as_dict()}
           for name in TEMPLATES]
    for name, (path, about) in EXAMPLES.items():
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                box = json.load(fh)["box"]
            out.append({"name": name, "description": about, "box": box, "example": True})
    return out


def main():
    ap = argparse.ArgumentParser(description="Write a vehicle template as a .cmuov project.")
    ap.add_argument("name", nargs="?", help=f"one of {', '.join(TEMPLATES)}")
    ap.add_argument("out", help="the .cmuov to write, or with --all the folder")
    ap.add_argument("--all", action="store_true", help="every template into the folder out")
    ap.add_argument("--box", nargs=3, type=int, metavar=("L", "W", "H"))
    args = ap.parse_args()
    names = list(TEMPLATES) if args.all else [args.name]
    if not args.all and args.name is None:
        ap.error("a template name, or --all")
    if args.all:
        os.makedirs(args.out, exist_ok=True)
    written = []
    for name in names:
        project = make(name, args.box)
        path = os.path.join(args.out, f"{name}.cmuov") if args.all else args.out
        P.save(project, path)
        written.append({"template": name, "file": path, "box": project["box"],
                        "layers": [l["name"] for l in project["layers"]]})
    print(json.dumps(written if args.all else written[0], indent=1))


if __name__ == "__main__":
    main()
