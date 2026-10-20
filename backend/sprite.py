"""A vehicle drawn as a finished sprite, in Aseprite or as PNGs: one top-down picture, turned to point
the way each direction faces, its layers named for what they are.

    base, overlay                 the body, under and over the riders (overlay is optional)
    damaged, damaged_overlay      the body worn (optional; without it the generator's scorches)
    wreck                         the burnt out wreck (optional; without it the body burnt dark)
    wheels                        what shows of the wheels (optional)
    door_driver, door_passenger,  panels that open: each shut, and with _open its look opened
    hood, tailgate, boot, hatch,  (optional; without one the panel just goes when it opens)
    fuel_door (+ _open)
    headlight_left/_right, taillight_left/_right, signal_left/_right, searchlight
                                  lamps

The project made from it (build) is the tool's own: a voxel layer per part, each picture pressed up
into a block (so the hull, seats, lamps, clicks and crayon map work as for a drawn vehicle), seen
from straight above by its camera ({"mode": "sprite", "nose": "S"}, camera.py), with the pictures
kept in project["sprite"]. When its states are drawn (export.render_states), recolour() puts the
pictures' own pixels back over what the blocks drew.
"""

import base64
import io
import re
import struct
import zlib

import numpy as np
from PIL import Image

import voxels as V

SIZE = 96
# How tall the pressed up body is, and the panels and lamps on it, in voxels.
BODY_TOP = 10
BOX_HEIGHT = 14

PANELS = {
    "door_driver": ("Driver door", "door", "left"),
    "door_passenger": ("Passenger door", "door", "right"),
    "hood": ("Hood", "hood", "both"),
    "tailgate": ("Tailgate", "tailgate", "both"),
    "boot": ("Boot lid", "boot", "both"),
    "hatch": ("Rear hatch", "hatch", "both"),
    "fuel_door": ("Fuel door", "fueldoor", "right"),
}
LAMPS = {
    "headlight_left": "Headlight left", "headlight_right": "Headlight right",
    "taillight_left": "Taillight left", "taillight_right": "Taillight right",
    "signal_left": "Turn signal left", "signal_right": "Turn signal right",
    "searchlight": "Searchlight",
}
PICTURES = {"base", "overlay", "damaged", "damaged_overlay", "wreck", "wheels"}
NOSES = "SNEW"


def key_of(name):
    """A layer's name as this module reads it: lower case, words joined by underscores."""
    k = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return {"fueldoor": "fuel_door", "fuel": "fuel_door", "driver_door": "door_driver",
            "passenger_door": "door_passenger", "spotlight": "searchlight"}.get(k, k)


# -- reading the pictures ------------------------------------------------------------------------

def read_aseprite(data):
    """An .aseprite/.ase file's first frame: (width, height, [(layer name, RGBA array)]) in layer
    order, bottom first, the hidden layers and groups left out."""
    if len(data) < 128 or struct.unpack_from("<H", data, 4)[0] != 0xA5E0:
        raise ValueError("Not an Aseprite file")
    frames, width, height, depth = struct.unpack_from("<HHHH", data, 6)
    transparent = data[28]
    pos = 128
    layers, cels, palette = [], {}, {}
    if frames < 1:
        raise ValueError("The Aseprite file has no frames")
    frame_bytes, magic, old_chunks = struct.unpack_from("<IHH", data, pos)
    if magic != 0xF1FA:
        raise ValueError("The Aseprite file's first frame is damaged")
    new_chunks = struct.unpack_from("<I", data, pos + 12)[0]
    chunks = new_chunks or old_chunks
    end = pos + frame_bytes
    pos += 16
    for _ in range(chunks):
        if pos + 6 > end:
            break
        size, kind = struct.unpack_from("<IH", data, pos)
        body = data[pos + 6:pos + size]
        pos += size
        if kind == 0x2004:
            flags, ltype, _child = struct.unpack_from("<HHH", body, 0)
            opacity = body[12]
            n = struct.unpack_from("<H", body, 16)[0]
            name = body[18:18 + n].decode("utf-8", "replace")
            layers.append({"name": name, "visible": bool(flags & 1), "type": ltype, "opacity": opacity})
        elif kind == 0x2005:
            index, x, y, opacity, ctype = struct.unpack_from("<HhhBH", body, 0)
            if ctype in (0, 2):
                w, h = struct.unpack_from("<HH", body, 16)
                raw = body[20:] if ctype == 0 else zlib.decompress(body[20:])
                cels[index] = (x, y, w, h, raw, opacity)
        elif kind == 0x2019:
            first, last = struct.unpack_from("<II", body, 4)
            at = 20
            for i in range(first, last + 1):
                flags = struct.unpack_from("<H", body, at)[0]
                palette[i] = tuple(body[at + 2:at + 6])
                at += 6
                if flags & 1:
                    at += 2 + struct.unpack_from("<H", body, at)[0]
        elif kind == 0x0004 and not palette:
            packets = struct.unpack_from("<H", body, 0)[0]
            at, i = 2, 0
            for _ in range(packets):
                i += body[at]
                count = body[at + 1] or 256
                at += 2
                for _ in range(count):
                    palette[i] = (*body[at:at + 3], 255)
                    at += 3
                    i += 1
    out = []
    for index, lay in enumerate(layers):
        if lay["type"] != 0 or not lay["visible"] or index not in cels:
            continue
        x, y, w, h, raw, cel_opacity = cels[index]
        if depth == 32:
            px = np.frombuffer(raw, np.uint8, w * h * 4).reshape(h, w, 4).copy()
        elif depth == 16:
            ga = np.frombuffer(raw, np.uint8, w * h * 2).reshape(h, w, 2)
            px = np.dstack([ga[..., 0], ga[..., 0], ga[..., 0], ga[..., 1]]).astype(np.uint8)
        else:
            idx = np.frombuffer(raw, np.uint8, w * h).reshape(h, w)
            lut = np.zeros((256, 4), np.uint8)
            for i, c in palette.items():
                if 0 <= i < 256:
                    lut[i] = c
            lut[transparent] = (0, 0, 0, 0)
            px = lut[idx]
        alpha = lay["opacity"] * cel_opacity / 255
        if alpha < 255:
            px[..., 3] = (px[..., 3].astype(np.float32) * alpha / 255).astype(np.uint8)
        canvas = np.zeros((height, width, 4), np.uint8)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + w), min(height, y + h)
        if x1 > x0 and y1 > y0:
            canvas[y0:y1, x0:x1] = px[y0 - y:y1 - y, x0 - x:x1 - x]
        out.append((lay["name"], canvas))
    return width, height, out


def read_files(files):
    """Uploaded files, [{"name", "data" (base64)}], as (width, height, [(layer name, RGBA array)]):
    an Aseprite file's layers, or each PNG a layer named by its file. Same named layers stack."""
    pictures, size = [], None
    for f in files:
        data = base64.b64decode(f["data"].split(",", 1)[-1])
        name = f.get("name") or "base.png"
        if name.lower().endswith((".aseprite", ".ase")):
            w, h, found = read_aseprite(data)
        else:
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            w, h = img.size
            found = [(re.sub(r"\.[^.]*$", "", name), np.array(img))]
        if size and size != (w, h):
            raise ValueError(f"{name} is {w}x{h}, the others {size[0]}x{size[1]}: draw them on one canvas size")
        size = (w, h)
        pictures += found
    if not pictures:
        raise ValueError("No layers in the files")
    if size[0] > SIZE or size[1] > SIZE:
        raise ValueError(f"The canvas is {size[0]}x{size[1]}: the game draws vehicles in {SIZE}x{SIZE}")
    return size[0], size[1], pictures


# -- pictures as the project keeps them ----------------------------------------------------------

def to_frame(arr):
    """A canvas centred in the game's frame."""
    h, w = arr.shape[:2]
    out = np.zeros((SIZE, SIZE, 4), np.uint8)
    x0, y0 = (SIZE - w) // 2, (SIZE - h) // 2
    out[y0:y0 + h, x0:x0 + w] = arr
    return out


def encode(arr):
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


_decoded = {}


def decode(url):
    """A kept picture as {(x, y): rgba} of its opaque pixels."""
    if url not in _decoded:
        arr = np.array(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[-1]))).convert("RGBA"))
        ys, xs = np.nonzero(arr[..., 3] > 0)
        _decoded.clear() if len(_decoded) > 64 else None
        _decoded[url] = {(int(x), int(y)): tuple(int(c) for c in arr[y, x]) for x, y in zip(xs, ys)}
    return _decoded[url]


def composite(sprite):
    """The pictures stacked as the vehicle shows shut and whole: {(x, y): rgba} in the frame."""
    pics = sprite.get("pictures") or {}
    key = tuple(sorted((k, len(v)) for k, v in pics.items()))
    if _composites.get("key") != key:
        out = {}
        for k in ["wheels", "base", "overlay"] + list(PANELS) + list(LAMPS):
            if k in pics:
                out.update(decode(pics[k]))
        _composites.update(key=key, out=out)
    return _composites["out"]


_composites = {}


# -- the frame and the vehicle's voxels ----------------------------------------------------------

HEADING = {"S": (0, -1), "N": (0, 1), "E": (1, 0), "W": (-1, 0)}


def voxel_at(nose, x, y):
    """The (f, r) column a frame pixel shows from straight above, the nose pointing the way given (as
    camera.raycast draws it: zoom 1, the drawing centred)."""
    hx, hy = HEADING[nose]
    sx, sy = x - SIZE // 2 + 0.5, y - SIZE // 2 + 0.5
    return int(np.floor(sx * hx - sy * hy)), int(np.floor(sx * hy + sy * hx))


def pixel_at(nose, f, r):
    """The frame pixel a column shows at: voxel_at's inverse."""
    hx, hy = HEADING[nose]
    fc, rc = f + 0.5, r + 0.5
    return int(np.floor(fc * hx + rc * hy)) + SIZE // 2, int(np.floor(-fc * hy + rc * hx)) + SIZE // 2


def build(files, nose="N", name="vehicle", materials=None):
    """A project from the files: see the module's notes. Returns (project, warnings)."""
    if nose not in NOSES:
        raise ValueError(f"The nose points S, N, E or W, not {nose!r}")
    w, h, found = read_files(files)
    pictures, warnings = {}, []
    for lname, arr in found:
        key = key_of(lname)
        known = (key in PICTURES or key in PANELS or key in LAMPS
                 or (key.endswith("_open") and key[:-5] in PANELS))
        if not known:
            warnings.append(f"Layer {lname!r} isn't one the tool knows, so it's left out.")
            continue
        frame = to_frame(arr)
        if key in pictures:
            under = pictures[key]
            a = frame[..., 3:4].astype(np.float32) / 255
            frame = (frame * a + under * (1 - a)).astype(np.uint8)
            frame[..., 3] = np.maximum(under[..., 3], frame[..., 3])
        pictures[key] = frame
    if "base" not in pictures and "overlay" not in pictures:
        raise ValueError("No base layer: name the body's layer base (and what's over the riders overlay)")

    def opaque(key):
        arr = pictures.get(key)
        if arr is None:
            return set()
        ys, xs = np.nonzero(arr[..., 3] > 0)
        return set(zip(xs.tolist(), ys.tolist()))

    body = opaque("base") | opaque("overlay")
    every = set(body)
    for key in pictures:
        every |= opaque(key)
    cols = [voxel_at(nose, x, y) for x, y in every]
    fs, rs = [c[0] for c in cols], [c[1] for c in cols]
    length = max(2, 2 * max(-min(fs), max(fs) + 1))
    width = max(2, 2 * max(-min(rs), max(rs) + 1))
    box = V.Box(length, width, BOX_HEIGHT)
    materials = list(materials or [])
    mat = {m: i for i, m in enumerate(materials)}

    def grid_of(points, z0, z1, material):
        grid = V.blank_voxels(box)
        m = mat.get(material, 0)
        for x, y in points:
            f, r = voxel_at(nose, x, y)
            grid[f - box.f0, r - box.r0, z0:z1] = m
        return grid

    def layer(lname, kind, grid, sides="both"):
        return {"name": lname, "kind": kind, "sides": sides, "thickness": 0, "visible": True, "role": None,
                "rides": None, "native": None, "stage": None, "voxels": V.encode_voxels(grid)}

    layers = [layer("Body", "body", grid_of(body, 0, BODY_TOP, "od"))]
    layers[0]["picture"] = "base"
    for key, (lname, kind, sides) in PANELS.items():
        pts = opaque(key)
        if pts:
            lay = layer(lname, kind, grid_of(pts, BODY_TOP, BODY_TOP + 1, "od"), sides)
            lay["picture"] = key
            layers.append(lay)
    if opaque("wheels"):
        lay = layer("Wheels", "wheel", grid_of(opaque("wheels") - body, 0, 4, "rubber"))
    else:
        # Without a picture of them, four wheels under the body's corners: never seen from above, but
        # the game drives, holes and changes them as the jeep's.
        grid = V.blank_voxels(box)
        under = {voxel_at(nose, x, y) for x, y in body}
        f_lo, f_hi, r_lo, r_hi = min(fs), max(fs), min(rs), max(rs)
        for fc in (f_lo + (f_hi - f_lo) * 0.2, f_lo + (f_hi - f_lo) * 0.8):
            for rc in (r_lo + 2, r_hi - 2):
                for f in range(round(fc) - 3, round(fc) + 3):
                    for r in range(round(rc) - 1, round(rc) + 2):
                        if (f, r) in under:
                            grid[f - box.f0, r - box.r0, 0:4] = mat.get("rubber", 0)
        lay = layer("Wheels", "wheel", grid)
    lay["picture"] = "wheels"
    layers.append(lay)
    for key, lname in LAMPS.items():
        pts = opaque(key)
        if pts:
            sides = "left" if key.endswith("left") else "right" if key.endswith("right") else "both"
            lay = layer(lname, "lamp", grid_of(pts, BODY_TOP, BODY_TOP + 2, "lamp"), sides)
            lay["picture"] = key
            layers.append(lay)

    # The riders sit side by side in the body's middle, the driver on the left; moved in the tool.
    f_mid = (min(fs) + max(fs)) / 2
    r_side = max(3.0, (max(rs) - min(rs)) / 4)
    doors = {lay["name"] for lay in layers if lay["kind"] == "door"}
    seats = []
    for ident, r, door in (("driver", -r_side, "Driver door"), ("passenger", r_side, "Passenger door")):
        seat = {"id": ident, "f": round(f_mid, 1), "r": round(r, 1), "z": BODY_TOP}
        if ident == "driver":
            seat["driver"] = True
        if door in doors:
            seat["door"] = door
        seats.append(seat)

    ident = "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", name)) or "Vehicle"
    camera = {"mode": "sprite", "nose": nose}
    project = {
        "box": box.as_dict(), "materials": materials, "layers": layers,
        "vehicle": {"id": ident, "name": name, "description": f"A {name}.", "seats": seats, "camera": camera},
        "sprite": {"nose": nose, "size": [w, h], "pictures": {k: encode(v) for k, v in pictures.items()}},
        "refs": {"top": {"path": "sprite.png", "data": encode(top_view(pictures, box, nose)), "opacity": 1}},
    }
    return project, warnings


def top_view(pictures, box, nose):
    """The pictures stacked as the tool's top view shows the box (nose right, the right side down), to
    trace seats over."""
    out = np.zeros((box.width, box.length, 4), np.uint8)
    order = ["wheels", "base", "overlay"] + list(PANELS) + list(LAMPS)
    for key in order:
        arr = pictures.get(key)
        if arr is None:
            continue
        for i in range(box.length):
            for j in range(box.width):
                x, y = pixel_at(nose, box.f0 + i, box.r0 + j)
                if 0 <= x < SIZE and 0 <= y < SIZE and arr[y, x, 3] > 0:
                    out[j, i] = arr[y, x]
    return out


# -- the states drawn with the pictures' own pixels ----------------------------------------------

def recolour(states, project, ids, role, g, mask_alpha):
    """Puts the pictures' pixels in the states export.render_states drew from the blocks: the body,
    its wear and wreck, each panel shut and open, the lamps, the wheels. What is drawn over the
    riders is what the overlay covers. ids and role are render_states' own, g its generator module
    and mask_alpha its click masks' alpha."""
    damage, outline = g.damage, g.outline

    def mask(pixels):
        return {p: c[:3] + (mask_alpha,) for p, c in pixels.items()}

    sprite = project.get("sprite") or {}
    pics = {k: decode(v) for k, v in (sprite.get("pictures") or {}).items()}
    over = set(pics.get("overlay", {}))

    def put(name, frames, delay=None):
        if name in states or delay is not None:
            states[name] = {"frames": {v: frames for v in "SNEW"}, "delay": delay or states.get(name, {}).get("delay")}

    def halves(px):
        return ({p: c for p, c in px.items() if p not in over}, {p: c for p, c in px.items() if p in over})

    base = {**pics.get("base", {}), **pics.get("overlay", {})}
    under, top = halves(base)
    put("base", [under])
    put("overlay", [top])
    if "damaged" in pics or "damaged_overlay" in pics:
        worn = {**pics.get("damaged", {}), **pics.get("damaged_overlay", {})}
        changed = {p: c for p, c in worn.items() if base.get(p) != c}
        du, do = halves(changed)
    else:
        du, do = damage(under, 11), damage(top, 13)
    put("damaged_frame", [du])
    put("damaged_overlay", [do])

    burnt = {}
    for p, (r_, g_, b_, a) in (pics.get("wreck") or base).items():
        if "wreck" in pics:
            burnt[p] = (r_, g_, b_, a)
            continue
        lum = (0.3 * r_ + 0.59 * g_ + 0.11 * b_) * 0.55
        burnt[p] = (round(min(255, lum + 8)), round(min(255, lum * 0.88 + 6)), round(min(255, lum * 0.75 + 4)), a)
    put("wreck", [burnt])

    for n, lay in enumerate(project["layers"]):
        key = lay.get("picture")
        if key is None or key in ("base",):
            continue
        pid = ids[n]
        mine = pics.get(key, {})
        if role.get(n) == "panel":
            opened = pics.get(f"{key}_open", {})
            cu, co = halves(mine)
            ou, oo = halves(opened)
            put(f"{pid}_closed", [cu])
            put(f"{pid}_closed_overlay", [co])
            put(f"{pid}_open", [ou])
            put(f"{pid}_open_overlay", [oo])
            put(f"{pid}_opening", [cu, ou], states.get(f"{pid}_opening", {}).get("delay") or 0.07)
            put(f"{pid}_opening_overlay", [co, oo], states.get(f"{pid}_opening_overlay", {}).get("delay") or 0.07)
            put(f"{pid}_closing", [ou, cu], states.get(f"{pid}_closing", {}).get("delay") or 0.07)
            put(f"{pid}_closing_overlay", [oo, co], states.get(f"{pid}_closing_overlay", {}).get("delay") or 0.07)
            put(f"{pid}_outline", [outline(set(mine))])
            put(f"{pid}_open_outline", [outline(set(opened))])
            put(f"click_{pid}_closed", [mask(mine)])
            put(f"click_{pid}_open", [mask(opened)])
            for level in range(1, 4):
                put(f"{pid}_closed_dent{level}", [cu])
                put(f"{pid}_closed_dent{level}_overlay", [co])
        elif role.get(n) == "lamp":
            lu, lo = halves(mine)
            put(f"{pid}_0", [lu])
            put(f"{pid}_overlay_0", [lo])
            spots = damage(mine, 17 + n, light=True)
            cracked = {p: g.blend(c, spots[p]) if p in spots else c for p, c in mine.items()}
            bu, bo = halves(cracked)
            put(f"{pid}_1", [bu])
            put(f"{pid}_overlay_1", [bo])
        elif role.get(n) == "wheels":
            wu, wo = halves(mine)
            count = len(next(iter(states.get("wheels_0", {"frames": {"S": [{}]}})["frames"].values())))
            put("wheels_0", [wu] * count, states.get("wheels_0", {}).get("delay"))
            put("wheels_overlay_0", [wo] * count, states.get("wheels_overlay_0", {}).get("delay"))
            put("rims_wheels_0", [{}] * count, states.get("rims_wheels_0", {}).get("delay"))
            put("rims_wheels_overlay_0", [{}] * count, states.get("rims_wheels_overlay_0", {}).get("delay"))
            dark = {p: (c[0] // 2, c[1] // 2, c[2] // 2, c[3]) for p, c in mine.items()}
            du_, do_ = halves(dark)
            put("wheels_1", [du_])
            put("wheels_overlay_1", [do_])

    # A top-down picture has no front to crumple: the dents change nothing.
    for name in list(states):
        if name.startswith("front_dent_"):
            put(name, [{}])

    # The crayon map's over-the-riders mark, by the overlay.
    if "crayon_map" in states:
        surface = {}
        for p, (r_, g_, b_, a) in states["crayon_map"]["frames"][sprite.get("nose", "N")][0].items():
            panel = (128 if a <= 128 else 255) - a
            surface[p] = (r_, g_, b_, (128 if p in over else 255) - panel)
        put("crayon_map", [surface])
