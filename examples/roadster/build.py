"""A civilian roadster after the second generation MX-5, built with the CMU Open Vehicles command line.

The car is described here in 3D, part by part, in the generator's space (f forward, r to its right,
z up, one voxel about 5 cm; a little wider than life, as the jeep is, for the top down view). Each part becomes a layer: its five orthographic views are worked out
from its voxels and drawn onto the layer with `cmuov.py draw`, which carves them back into voxels the
way the app does. Run it to rebuild roadster.cmuov beside it; open that in the app to keep drawing.

    python build.py            # rebuild roadster.cmuov
    python build.py --check    # also report how closely the carved layers match the model
"""

import json
import math
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "..", "backend")
CLI = os.path.join(BACKEND, "cmuov.py")
OUT = os.path.join(HERE, "roadster.cmuov")
sys.path.insert(0, BACKEND)
import voxels as V  # noqa: E402

L, W, H = 72, 40, 26
BOX = V.Box(L, W, H)

FRONT_AXLE, REAR_AXLE = 20, -20
WHEEL_Z, WHEEL_R, RIM_R = 6, 6.0, 4.1
ARCH_R = 7.2
WINDOWS = 4         # the rims' windows: a multiple of four, so the four wheel frames loop

# The body's profile, plan and floor, as (f, value) points along its length. Its top is in flat
# planes with creases between them, as pixel art wants (every step in height draws a line): the hood,
# a step up to the cowl, the beltline rising to the rear deck, the tail rounding off.
TOP = [(35.5, 10.5), (34.5, 12.0), (33, 13.0), (31.5, 13.6), (8, 13.6), (7, 14.6), (3, 14.6), (-13, 16.5),
       (-31, 16.5), (-32.5, 16.0), (-33.5, 15.2), (-34.5, 14.0), (-35.5, 12.5)]
BOTTOM = [(35.5, 5.0), (34.5, 3.8), (33, 3.0), (-33, 3.0), (-34.5, 4.0), (-35.5, 5.2)]
HALF = [(35.5, 10.5), (35, 12.5), (34, 14.2), (33, 15.4), (31, 16.4), (28, 17.1), (24, 17.5), (16, 17.4),
        (10, 17.0), (0, 17.0), (-10, 17.1), (-16, 17.5), (-24, 17.7), (-29, 17.4), (-32, 16.7), (-34, 15.4),
        (-35.5, 13.0)]

COCKPIT = (-13, 2)          # f, from the seats' back wall to the dash
DOOR = (-11, 6, 3, 16)      # f0, f1, z0, z1
HOOD = (8, 31)
HOOD_HALF = 12              # the hood's shut lines run along the front fenders' ridges
BOOT = (-34, -20)
SEATS = {"driver": (-13, -4), "passenger": (3, 12)}


def along(points, x):
    """A value between the points, by its place along the car."""
    xs = [p[0] for p in points]
    if x >= xs[0]:
        return points[0][1]
    if x <= xs[-1]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x1 <= x <= x0:
            t = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return points[-1][1]


def smooth(a, b, x):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def top_at(fc, rc):
    """The body's upper surface: its shoulders rounded off, the front fenders a ridge either side of
    the hood."""
    half = along(HALF, fc)
    top = along(TOP, fc) - 1.0 * (abs(rc) / half) ** 4
    if 11 <= fc <= 30 and abs(rc) > HOOD_HALF:
        top += 1.0
    return top


def shell():
    """The outer shape, solid: every voxel inside the skin."""
    occ = {}
    for f in range(BOX.f0, BOX.f0 + L):
        fc = f + 0.5
        bottom = along(BOTTOM, fc)
        for r in range(BOX.r0, BOX.r0 + W):
            rc = r + 0.5
            top = top_at(fc, rc)
            for z in range(H):
                zc = z + 0.5
                if not bottom <= zc <= top:
                    continue
                half = along(HALF, fc)
                if zc > top - 2.5:
                    half -= 1.6 * ((zc - (top - 2.5)) / 2.5) ** 2
                if zc < bottom + 1.5:
                    half -= 0.8
                if abs(rc) <= half:
                    occ[(f, r, z)] = True
    return occ


def in_arch(f, z):
    return any(math.hypot(f + 0.5 - a, z + 0.5 - WHEEL_Z) < ARCH_R for a in (FRONT_AXLE, REAR_AXLE))


def build_parts():
    """The car's voxels, part by part: {part: {(f, r, z): material}}."""
    solid = shell()
    parts = {name: {} for name in ("body", "engine", "hood", "boot", "door_left", "door_right", "fuel_door",
                                   "seats", "wheel_ring", "soft_top", "windshield", "headlight_left",
                                   "headlight_right", "signal_left", "signal_right", "taillight_left",
                                   "taillight_right", "wheels")}
    body = parts["body"]

    def surface_top(f, r):
        zs = [z for z in range(H) if (f, r, z) in solid]
        return max(zs) if zs else None

    for (f, r, z) in solid:
        rc = r + 0.5
        # The wheel wells: the arches cut the sides up to under the fenders' skin; the wells' inner
        # walls are dark.
        if in_arch(f, z) and z < surface_top(f, r) - 1:
            if abs(rc) > 10:
                continue
            if abs(rc) > 9:
                body[(f, r, z)] = "black"
                continue
        # The doors, from the sill to the beltline, are their own layers.
        if DOOR[0] <= f <= DOOR[1] and DOOR[2] <= z <= DOOR[3] and abs(rc) > 14.5:
            parts["door_right" if rc > 0 else "door_left"][(f, r, z)] = "od"
            continue
        # The cockpit: down to the carpet.
        if COCKPIT[0] <= f <= COCKPIT[1] and abs(rc) <= 15 and z > 4:
            continue
        half = along(HALF, f + 0.5)
        top = surface_top(f, r)
        # The hood and the boot lid: the top two voxels of the skin between their shut lines, with the
        # engine bay and the boot under them.
        if HOOD[0] <= f <= HOOD[1] and abs(rc) <= HOOD_HALF:
            if z >= top - 1:
                parts["hood"][(f, r, z)] = "od"
                continue
            if f >= HOOD[0] + 1 and f <= HOOD[1] - 1 and abs(rc) <= HOOD_HALF - 1 and z >= 7:
                continue
        if BOOT[0] <= f <= BOOT[1] and abs(rc) <= half - 3:
            if z >= top - 1:
                parts["boot"][(f, r, z)] = "od"
                continue
            if BOOT[0] + 1 <= f <= BOOT[1] - 1 and abs(rc) <= half - 4 and z >= 6:
                continue
        body[(f, r, z)] = "od"

    # Carpet, the dash and its binnacle, the console and the gear lever.
    for (f, r, z), m in list(body.items()):
        if COCKPIT[0] <= f <= COCKPIT[1] and abs(r + 0.5) <= 15 and z == 4:
            body[(f, r, z)] = "black"
    for f in range(0, 3):
        for r in range(-15, 15):
            for z in range(5, 13 + (f > 0)):
                body[(f, r, z)] = "black"
    for f in (0, 1):
        for r in range(-12, -5):
            body[(f, r, 14)] = "black"
    for f in range(-8, 1):
        for r in range(-3, 3):
            for z in (5, 6):
                body[(f, r, z)] = "black"
    body[(-4, -1, 7)] = body[(-4, 0, 7)] = "steel"
    body[(-4, -1, 8)] = body[(-4, 0, 8)] = "black"
    # The engine bay's floor and walls, and the boot's.
    for (f, r, z), m in list(body.items()):
        if m != "od":
            continue
        rc = r + 0.5
        half = along(HALF, f + 0.5)
        bay = HOOD[0] <= f <= HOOD[1] and abs(rc) <= HOOD_HALF
        boot = BOOT[0] <= f <= BOOT[1] and abs(rc) <= half - 3
        if (bay or boot) and z < (surface_top(f, r) or 0) - 1:
            body[(f, r, z)] = "frame" if bay else "black"
    # The front: the mouth of the intake under the nose, and the plate and the tail pipe at the back.
    for f in (34, 35):
        for r in range(-8, 8):
            for z in range(5, 8):
                body.pop((f, r, z), None)
    for r in range(-8, 8):
        for z in range(5, 8):
            if (33, r, z) in body:
                body[(33, r, z)] = "black"
    for r in range(-4, 4):
        for z in range(7, 10):
            back = min((f for f in range(-36, 0) if (f, r, z) in body), default=None)
            if back is not None:
                body[(back, r, z)] = "white"
    for f in (-36, -35):
        for r in (-12, -11):
            for z in (3, 4):
                body[(f, r, z)] = "steel"
    # Red reflectors low on the rear bumper's corners.
    for r in list(range(-15, -12)) + list(range(12, 15)):
        back = min((f for f in range(-36, 0) if (f, r, 6) in body), default=None)
        if back is not None:
            body[(back, r, 6)] = "red_line"

    # The doors: a shut line round their outer face, a handle, and the mirrors on their front corners.
    for side, sign in (("door_right", 1), ("door_left", -1)):
        door = parts[side]
        outer = {}
        for (f, r, z) in door:
            key = (f, z)
            if key not in outer or sign * r > sign * outer[key]:
                outer[key] = r
        for (f, z), r in outer.items():
            edge = f in (DOOR[0], DOOR[1]) or z == DOOR[2] or (f, z + 1) not in outer
            if edge and z < DOOR[3]:
                door[(f, r, z)] = "od_seam"
        for f in (-9, -8):
            door[(f, outer[(f, 14)], 14)] = "black"
        # Mirrors sit on the door tops at the windshield's foot, their glass facing back.
        for f in (3, 4, 5):
            for z in (15, 16):
                for rr in (17, 18):
                    r = rr if sign > 0 else -rr - 1
                    door[(f, r, z)] = "gun" if f == 3 else "od"

    # The fuel door, on the right rear fender.
    for f in range(-18, -15):
        for z in range(12, 15):
            r = max((r for r in range(0, 20) if (f, r, z) in body), default=None)
            if r is not None:
                parts["fuel_door"][(f, r, z)] = "od" if f == -17 and z == 13 else "od_seam"
                del body[(f, r, z)]

    # The engine, under the hood.
    for f in range(10, 25):
        for r in range(-8, 8):
            for z in range(7, 12):
                parts["engine"][(f, r, z)] = "steel" if z == 11 and -6 <= r <= 5 and 12 <= f <= 22 else "gun"
    for f in range(13, 23, 3):
        parts["engine"][(f, -1, 11)] = parts["engine"][(f, 0, 11)] = "black"

    # Lamps. Headlights: teardrops on the front fenders' leading edge, set into the skin.
    for name, sign in (("headlight_right", 1), ("headlight_left", -1)):
        for (f, r, z) in list(body) + list(parts["hood"]):
            rc = (r + 0.5) * sign
            u, v = (f + 0.5 - 31.0) / 2.8, (rc - 12.6) / 3.3
            if u * u + v * v > 1 or z < 10:
                continue
            owner = body if (f, r, z) in body else parts["hood"]
            top = max(zz for zz in range(H) if (f, r, zz) in owner)
            if z < top - 1:
                continue
            # A clear lens over a chrome reflector, its bulb at the middle and a glint on its front.
            if u * u + v * v > 0.55:
                mat = "lens_dk"
            elif abs(u) < 0.3 and abs(v) < 0.4:
                mat = "lamp"
            else:
                mat = "lens" if u > 0.35 else "steel"
            parts[name][(f, r, z)] = mat
            del owner[(f, r, z)]
    # Turn signals: the bumper's front corners, and repeaters on the fenders' sides.
    for name, sign in (("signal_right", 1), ("signal_left", -1)):
        for r in range(12, 16):
            rr = r if sign > 0 else -r - 1
            front = max((f for f in range(0, 36) if (f, rr, 8) in body), default=None)
            if front is not None:
                parts[name][(front, rr, 8)] = "amber"
                del body[(front, rr, 8)]
        side = max((r for r in range(0, 20) if (27, r if sign > 0 else -r - 1, 11) in body), default=None)
        if side is not None:
            rr = side if sign > 0 else -side - 1
            parts[name][(27, rr, 11)] = "amber"
            del body[(27, rr, 11)]
    # Tail lights: ovals on the rear corners, wrapping a little round them.
    for name, sign in (("taillight_right", 1), ("taillight_left", -1)):
        for (f, r, z) in list(body) + list(parts["boot"]):
            rc = (r + 0.5) * sign
            u, v = (rc - 12.3) / 4.0, (z + 0.5 - 13.0) / 1.8
            if u * u + v * v > 1 or f > -32:
                continue
            owner = body if (f, r, z) in body else parts["boot"]
            back = min(ff for ff in range(-36, 0) if (ff, r, z) in owner)
            if f > back + 1:
                continue
            if u > 0.55:
                mat = "amber"
            elif -0.45 < u < -0.05 and abs(v) < 0.5:
                mat = "white"
            else:
                mat = "red" if u * u + v * v < 0.7 else "red_line"
            parts[name][(f, r, z)] = mat
            del owner[(f, r, z)]

    # Seats: two buckets, their backs leaning a little, with headrests.
    seats = parts["seats"]
    for r0, r1 in SEATS.values():
        for r in range(r0, r1 + 1):
            edge = r in (r0, r1)
            for f in range(-10, -2):
                for z in range(5, 9 if edge else 8):
                    seats[(f, r, z)] = "canvas_seam" if z == 7 and f == -6 else "canvas"
            for z in range(5, 18):
                lean = 1 if z >= 11 else 0
                for f in (-12 - lean, -11 - lean):
                    seats[(f, r, z)] = "canvas_seam" if z == 12 and not edge else "canvas"
            if r0 + 2 <= r <= r1 - 2:
                for z in (18, 19):
                    for f in (-13, -12):
                        seats[(f, r, z)] = "canvas"

    # The steering wheel, before the driver: a ring on its column.
    centre_r, centre_z = -8.0, 13.5
    for r in range(-13, -2):
        for z in range(9, 18):
            d = math.hypot(r + 0.5 - centre_r, z + 0.5 - centre_z)
            if 1.9 < d <= 3.0:
                parts["wheel_ring"][(-1, r, z)] = "black"
            elif d <= 0.8:
                parts["wheel_ring"][(-1, r, z)] = "gun"
            elif abs(z + 0.5 - centre_z) < 0.6 and d <= 2:
                parts["wheel_ring"][(-1, r, z)] = "black"
    parts["wheel_ring"][(0, -9, 13)] = "black"

    # The soft top, folded behind the seats under its black cover.
    for f in range(-17, -13):
        for r in range(-15, 15):
            hump = 17.1 + 0.9 * math.cos((f + 15) / 2.2) - 0.8 * (abs(r + 0.5) / 15) ** 4
            for z in range(14, 20):
                if z + 0.5 <= hump:
                    parts["soft_top"][(f, r, z)] = "gun" if f == -16 and z == 14 else "black"
    for key in parts["soft_top"]:
        body.pop(key, None)

    # The windshield, raked well back: a black frame round the glass, a mirror under its header.
    for z in range(15, 24):
        fz = 3.4 - (z - 15) * 0.72
        width = 15.0 - (z - 15) * 0.12
        for f in range(math.floor(fz - 0.9), math.floor(fz) + 1):
            for r in range(-16, 16):
                rc = abs(r + 0.5)
                if rc > width:
                    continue
                frame = rc > width - 1.2 or z >= 23 or z == 15
                parts["windshield"][(f, r, z)] = "black" if frame else "glass"
    parts["windshield"][(-3, -1, 22)] = parts["windshield"][(-3, 0, 22)] = "black"
    for key in parts["windshield"]:
        body.pop(key, None)

    # Wheels: low profile tyres on alloys, a bright face with four dark windows (at eight pixels across,
    # more spokes than that run together). The windows are in the face's colours, not cut into it: seen
    # from above at a slant, a recess hides under the edge over it.
    wheels = parts["wheels"]
    for axle in (FRONT_AXLE, REAR_AXLE):
        for sign in (1, -1):
            slices = range(12, 17) if sign > 0 else range(-17, -12)
            outer = 16 if sign > 0 else -17
            for f in range(axle - 7, axle + 7):
                for z in range(0, 14):
                    u, v = f + 0.5 - axle, z + 0.5 - WHEEL_Z
                    d = math.hypot(u, v)
                    if d > WHEEL_R:
                        continue
                    a = math.atan2(v, u)
                    for r in slices:
                        depth = abs(r - outer)
                        if d > WHEEL_R - 0.9:
                            mat = "groove" if (a / (2 * math.pi / 24)) % 1 < 0.35 and depth not in (0, 4) else "rubber"
                        elif d > RIM_R:
                            mat = "rubber"
                        elif depth == 0:
                            pitch = 2 * math.pi / WINDOWS
                            off = abs((a % pitch) - pitch / 2) * d
                            if d > RIM_R - 0.7:
                                mat = "steel"
                            elif d < 1.1:
                                mat = "hub"
                            elif 1.5 < d < 3.2 and off < 0.9:
                                mat = "gun"
                            else:
                                mat = "steel"
                        else:
                            mat = "rubber" if d > RIM_R - 1 else "gun"
                        wheels[(f, r, z)] = mat
    return parts


# The layers, in stack order: (part, name, kind, sides, role). Each is drawn in
# every view it shows in, so its views alone shape it: no thickness.
LAYERS = [
    ("body", "Body", "body", "both", None),
    ("engine", "Engine", "part", "both", "engine"),
    ("hood", "Hood", "hood", "both", None),
    ("boot", "Boot lid", "boot", "both", None),
    ("door_left", "Driver door", "door", "left", None),
    ("door_right", "Passenger door", "door", "right", None),
    ("fuel_door", "Fuel door", "fueldoor", "right", None),
    ("seats", "Seats", "part", "both", "seat"),
    ("wheel_ring", "Steering wheel", "part", "both", None),
    ("soft_top", "Soft top", "part", "both", None),
    ("windshield", "Windshield", "part", "both", None),
    ("headlight_left", "Headlight left", "lamp", "left", None),
    ("headlight_right", "Headlight right", "lamp", "right", None),
    ("signal_left", "Turn signal left", "lamp", "left", None),
    ("signal_right", "Turn signal right", "lamp", "right", None),
    ("taillight_left", "Taillight left", "lamp", "left", None),
    ("taillight_right", "Taillight right", "lamp", "right", None),
    ("wheels", "Wheels", "wheel", "both", None),
]


def cli(*args):
    out = subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"cmuov.py {' '.join(args[:3])}: {out.stderr.strip()[-600:]}")
    return out.stdout


def ops_for(mat, depth, materials, view):
    """A view's drawing as draw operations: its pixels grouped by material and depth, on a cleared view
    (a blank left or side view starts as the other side mirrored)."""
    groups = {}
    for y, x in zip(*np.nonzero(mat != V.EMPTY)):
        groups.setdefault((int(mat[y, x]), int(depth[y, x])), []).append([int(x), int(y)])
    return [{"op": "clear"}] + [{"op": "pixels", "points": pts, "mat": materials[m], "depth": d}
                                for (m, d), pts in sorted(groups.items())]


def main():
    parts = build_parts()
    cli("new", OUT, "--box", str(L), str(W), str(H), "--force")
    materials = json.load(open(OUT, encoding="utf-8"))["materials"]
    # The blank project's own Body layer is ours; the rest are added in order.
    for part, name, kind, sides, role in LAYERS:
        if name != "Body":
            cli("layer", OUT, "add", kind, "--name", name)
        values = [f"sides={sides}", "thickness=0"] + ([f"role={role}"] if role else [])
        cli("layer", OUT, "set", name, *values)
        vox = {k: (m, part) for k, m in parts[part].items()}
        arrays = V.drawing_arrays(vox, BOX, materials, sides)
        for view in V.VIEWS:
            mat, depth = arrays[view]
            if (mat == V.EMPTY).all():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
                json.dump(ops_for(mat, depth, materials, view), fh)
            try:
                cli("draw", OUT, name, view, "@" + fh.name)
            finally:
                os.remove(fh.name)
        print(f"{name}: {len(vox)} voxels")
    # What the game needs to know besides the drawing: its name and where its riders sit.
    project = json.load(open(OUT, encoding="utf-8"))
    project["vehicle"] = {
        "id": "Roadster", "name": "roadster",
        "description": "A small two-seat civilian roadster with its top folded down. Quick, nimble and no shelter at all.",
        "seats": [{"id": "driver", "f": -6.5, "r": -8.5, "z": 8, "driver": True, "door": "Driver door"},
                  {"id": "passenger", "f": -6.5, "r": 7.5, "z": 8, "door": "Passenger door"}],
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(project, fh)
    if "--check" in sys.argv:
        check(parts, materials)


def check(parts, materials):
    """How the carved layers compare with the model, voxel for voxel."""
    project = json.load(open(OUT, encoding="utf-8"))
    for (part, name, *_), lay in zip(LAYERS, project["layers"]):
        occ, _ = V.carve(lay, BOX)
        carved = {(int(i) + BOX.f0, int(j) + BOX.r0, int(k)) for i, j, k in zip(*np.nonzero(occ))}
        model = set(parts[part])
        print(f"{name:18} model {len(model):5}  carved {len(carved):5}  extra {len(carved - model):5}  missing {len(model - carved):4}")


if __name__ == "__main__":
    main()
