"""A vehicle drawn in the tool, rendered into every sprite state the game's open vehicle systems draw,
the way the jeep's generator draws the jeep's: its body under and over the riders, each hinged panel
shut, open and swinging, each lamp whole, broken and lit, the windshield up, down and folding (whole
and shot through), the kit stowed on it (and swinging with the panel it hangs on), the wheels turning
and destroyed, the engine, hover outlines and click masks, frame damage, the front crumpled in
levels, each damage state drawn in the tool at its levels, the crayon map and the wreck. States are
named by part id, as the vehicle's prototype lists its parts.

A layer's damage states (layers with "stage") are drawn two ways. The game's own broken looks take
the worst level drawn: the body's for the frame damage that fades in as the hull wears, a lamp's,
the engine's, the windshield's and kit's for their broken states. And each level is drawn as what it
changes of its part at rest, {part}_dmg_{state}_{level} (and _overlay_ over the riders), for a game
that shows them by damage type (see export_game).

    states = export.render_states(gen, project)      # {state: [96 px tile per direction, frame...]}
    warnings = export.check_canvas(states)           # raises if anything runs off the frame
    export.write_rsi(path, states, copyright)
"""

import math
import os
import random
import re
import json

from PIL import Image

import camera as C
import voxels as V

SIZE = 96
DIRS = "SNEW"
SWING_DELAY = 0.07
SIGNAL_DELAY = 0.4
MASK_ALPHA = 30
CRUMPLE_LEVELS = 3
# Rim maps: per rim pixel its place on the rim design's 11 pixel face and the wheel frame, in steps
# the game's sRGB sampling gives back exactly (rim.swsl).
RIM_STEP = 24
TYRE_MATS = {"rubber", "groove", "char", "black"}
HINGED = {"door", "hood", "boot", "tailgate", "hatch", "fueldoor", "windshield"}
# The windshield folds forward over its bottom edge in these steps of its way down (the jeep's own
# fold, as shares of it), until it lies on what is ahead of it.
FOLD_STEPS = (0, 0.2, 0.45, 0.71, 1.0)
# A kit item the game draws with tyres of their own: a road tyre wheel in its slot takes these
# states, road_ before the kit's.
WHEEL_KIT_TAGS = {"CMUJeepWheel"}
TYRE_PREFIXES = ("road_",)
# Lamp materials as they light up: the lens of a headlight, a tail light's red, braking brighter, a
# signal's amber.
LIT = {
    "head": {"lens": "lit", "lamp": "lit", "lens_dk": "lit_dim", "white": "lit"},
    "tail": {"red": "lit_red", "red_line": "lit_red"},
    "brake": {"red": "lit_brake", "red_line": "lit_brake"},
    "signal": {"amber": "lit_amber"},
}


def part_id(layer):
    """A layer's part id: its own, else its name in snake case. The engine is always "engine": the
    game drives its sprite layer by that key."""
    if layer.get("role") == "engine":
        return "engine"
    return layer.get("part") or re.sub(r"[^a-z0-9]+", "_", layer["name"].lower()).strip("_")


def lamp_role(layer):
    """A lamp layer's role by its name: searchlight, headlight, turn signal or tail light."""
    name = layer["name"].lower()
    if "search" in name or "spot" in name:
        return "search"
    if "tail" in name or "brake" in name or "rear" in name:
        return "tail"
    if "signal" in name or "indicator" in name or "blinker" in name:
        return "signal"
    return "head"


def roles(project):
    """What each layer is in game, by index: base, panel, windshield, kit, lamp, wheels, engine or
    stage."""
    out = {}
    for n, lay in enumerate(project["layers"]):
        kind = lay.get("kind")
        if lay.get("stage"):
            out[n] = "stage"
        elif lay.get("kit"):
            out[n] = "kit"
        elif kind == "wheel":
            out[n] = "wheels"
        elif kind == "lamp":
            out[n] = "lamp"
        elif kind == "windshield":
            out[n] = "windshield"
        elif kind in HINGED:
            out[n] = "panel"
        elif lay.get("role") == "engine":
            out[n] = "engine"
        else:
            out[n] = "base"
    return out


def wheel_kit(layer):
    """Whether a kit layer holds a wheel, which the game draws on the tyres fitted to it."""
    kit = layer.get("kit") or {}
    return kit.get("tag") in WHEEL_KIT_TAGS or kit.get("item") in WHEEL_KIT_TAGS


def prepared(project):
    """The project as the exporter draws it: kit hung on a panel rides on it, so it swings with it.
    The project itself is left as it is."""
    layers = []
    for lay in project["layers"]:
        host = (lay.get("kit") or {}).get("host")
        layers.append({**lay, "rides": host} if host and not lay.get("stage") else lay)
    return {**project, "layers": layers}


def showing(project, shown):
    """The project with layers showing damage stages: {layer index: stage layer name}."""
    layers = list(project["layers"])
    for n, name in shown.items():
        layers[n] = {**layers[n], "show_stage": name}
    return {**project, "layers": layers}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_") or "damage"


def damage_states(project, n):
    """A layer's damage states: [{"state", "slug", "type", "levels": [(from, stage layer name), ...]}],
    each state's levels lowest first, in the order the states were drawn."""
    out = {}
    for st in V.stages(project["layers"], project["layers"][n]):
        info = st["stage"]
        name = info.get("state") or st["name"]
        s = out.setdefault(name, {"state": name, "slug": slug(name), "type": info.get("type"), "levels": []})
        s["levels"].append((int(info.get("from") or 0), st["name"]))
    for s in out.values():
        s["levels"].sort(key=lambda e: e[0])
    return list(out.values())


def worst_stage(project, n):
    """The stage drawn for the most damage of any of a layer's states (the first state's, on a tie),
    or None for a layer without any."""
    best = None
    for s in damage_states(project, n):
        f, name = s["levels"][-1]
        if best is None or f > best[0]:
            best = (f, name)
    return best[1] if best else None


class Scene:
    """One direction's voxels drawn: per canvas pixel its colour (paint codes kept, glass blended), the
    layer it shows, its depth and its buffer entry."""

    def __init__(self, gen, vox, owner, view):
        g = gen.module
        self.g = g
        self.view = view
        self.buf, glass = C.render(gen, view, g.Model(vox))
        col = g.shade(self.buf)
        ox, oy = SIZE // 2, SIZE // 2 + C.dy(gen, view)
        self.px = {}
        # Per pixel, the solid voxel in front and the glass in front of that, apart: (colour, layer,
        # depth) each.
        self.solid = {}
        self.glass = {}
        for (x, y), c in col.items():
            e = self.buf[(x, y)]
            self.px[(x + ox, y + oy)] = [tuple(c) if len(c) == 4 else (*c, 255), owner.get(e[4], -1), e[0], e]
            self.solid[(x + ox, y + oy)] = tuple(self.px[(x + ox, y + oy)][:3])
        for (x, y), e in glass.items():
            under = self.buf.get((x, y))
            if under is not None and under[0] < e[0]:
                continue
            if hasattr(g, "dirty_glass") and e[2] == "windshield":
                tint = g.dirty_glass(x, y, e[4])
            elif hasattr(g, "glass_color"):
                tint = g.glass_color(x, y)
            else:
                tint = getattr(g, "GLASS_TINT", (176, 206, 204, 78))
            p = (x + ox, y + oy)
            current = self.px.get(p)
            colour = tuple(int(v) for v in g.blend(current[0] if current else None, tint))
            self.px[p] = [colour, owner.get(e[4], -1), e[0], e]
            self.glass[p] = (tuple(tint), owner.get(e[4], -1), e[0])

    def of(self, layers):
        return {p: v[0] for p, v in self.px.items() if v[1] in layers}

    def own(self, layers):
        """The layers' own pixels and their depths, as the game draws a part on its own layer: what of
        them is in front, solid, and their glass over that, or over nothing, never over another part
        behind it. A part behind someone else's glass still has those pixels."""
        out, depth = {}, {}
        for p, (c, n, d) in self.solid.items():
            if n in layers:
                out[p], depth[p] = c, d
        for p, (tint, n, d) in self.glass.items():
            if n in layers:
                out[p] = tuple(int(v) for v in self.g.blend(out.get(p), tint))
                depth[p] = min(d, depth.get(p, d))
        return out, depth

    def glass_of(self, layers):
        """The pixels where the layers' glass is in front."""
        return {p for p, (_, n, _) in self.glass.items() if n in layers}


def build(gen, project, box, view, skip=(), frame=0):
    models, owners, per_layer, _ = V.build(project, box, project["materials"], [view], skip=set(skip), gen=gen, frame=frame)
    return models[view], owners[view], per_layer


def fold_angle(gen, project, box, n):
    """How far a windshield folds forward over its bottom edge to lie on what is ahead of it (the
    hood, or the cowl): the furthest it turns, in steps of five degrees, before a tenth of it (above
    its hinge) would pass into the rest of the vehicle. Upright it folds through about a right angle,
    raked back further; with nothing ahead, until it lies flat."""
    models, owners, per_layer, _ = V.build(project, box, project["materials"], ["S"], skip={n}, gen=gen)
    own = (per_layer[n] or {}).get("S", {})
    if not own:
        return 0
    h = V.hinge("windshield", project["layers"][n].get("sides", "both"), own)
    _, (cf, cz), _ = h
    # What it covers shut isn't in its way: it is that, hidden.
    still = set(models["S"]) - set(own)
    top = {k: v for k, v in own.items() if k[2] >= cz + 2} or own
    # How far its top leans back of its bottom: it lies flat that much past a right angle.
    lo, hi = min(k[2] for k in own), max(k[2] for k in own)

    def mean_f(z):
        fs = [k[0] for k in own if k[2] == z]
        return sum(fs) / len(fs)

    rake = math.degrees(math.atan2(mean_f(lo) - mean_f(hi), max(1, hi - lo)))
    limit = max(60, min(170, round((90 + rake) / 5) * 5))
    best = 0
    for a in range(5, limit + 1, 5):
        moved = V.turn(top, h, a)
        if sum(1 for k in moved if k in still) * 10 > len(moved):
            break
        best = a
    return best


def fold_scenes(gen, project, box, n, view, degrees):
    """A windshield folding down, seen from one direction: per frame (FOLD_STEPS of the way to
    degrees), the voxels and who owns them. What rides on it folds with it."""
    layers = project["layers"]
    riders = [i for i, l in enumerate(layers) if i != n and l.get("rides") and l.get("rides") == layers[n].get("name")]
    models, owners, per_layer, _ = V.build(project, box, project["materials"], [view], skip={n, *riders}, gen=gen)
    own = (per_layer[n] or {}).get(view, {})
    if not own or not degrees:
        return []
    h = V.hinge("windshield", layers[n].get("sides", "both"), own)
    scenes = []
    for share in FOLD_STEPS:
        a = degrees * share
        flat = gen.flat if a % 90 else None
        vox = dict(models[view])
        owner = dict(owners[view])
        for i in [n, *riders]:
            moved = V.turn((per_layer[i] or {}).get(view, {}), h, a, flat, None if i == n or not a else f"L{i}")
            vox.update(moved)
            owner.update(dict.fromkeys(moved, i))
        scenes.append((vox, owner))
    return scenes


def body_of(gen, vox, owner, view, base_layers, closed):
    """The body: what the closed vehicle shows of it, and what the panels, wheels and lamps hide, so
    it shows when they open or come off. Its pixels and their depths."""
    alone = Scene(gen, {k: v for k, v in vox.items() if owner.get(k) in base_layers}, owner, view)
    body, depth = {}, {}
    for p, (c, n, d, _) in alone.px.items():
        won = closed.px.get(p)
        if won is not None and won[1] in base_layers:
            body[p], depth[p] = won[0], won[2]
        else:
            body[p], depth[p] = c, d
    return body, depth


def changed(intact, damaged, crease):
    """What a damage drawing changes of a part, to draw over it: its own colour where it differs,
    the crease's dark where the part no longer reaches."""
    out = {p: c for p, c in damaged.items() if intact.get(p) != c}
    out.update({p: crease for p in intact if p not in damaged})
    return out


class Riders:
    """Where a direction draws the seated riders, as rectangles at their depths, to tell what is drawn
    over them."""

    def __init__(self, gen, seats, view):
        dy = C.dy(gen, view)
        hip = C.hip(gen, view)
        self.rects = []
        for seat in seats:
            sx, sy = C.project(gen, view, seat["f"], seat["r"], seat["z"])
            depth = C.depth(gen, view, seat["f"], seat["r"])
            cx, cy = sx + SIZE // 2, sy + SIZE // 2 + dy - (hip - 16)
            self.rects.append((depth, cx - 8, cx + 8, cy - 16, cy + 15))
        self.seats = {seat["id"]: rect for seat, rect in zip(seats, self.rects)}

    def rect_of(self, seat):
        """Where a seat's rider is drawn: (depth, x0, x1, y0, y1)."""
        return self.seats[seat["id"]]

    def over(self, p, depth):
        under = [d for d, x0, x1, y0, y1 in self.rects if x0 <= p[0] <= x1 and y0 <= p[1] <= y1]
        return bool(under) and depth < min(under)


def split(pixels, depths, riders):
    """A layer's pixels into those under the riders and those over them."""
    under, over = {}, {}
    for p, c in pixels.items():
        (over if riders.over(p, depths[p]) else under)[p] = c
    return under, over


def to_image(pixels):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    for (x, y), c in pixels.items():
        if 0 <= x < SIZE and 0 <= y < SIZE:
            img.putpixel((x, y), c)
    return img


def seed(*parts):
    return sum(ord(c) * (i + 1) for i, c in enumerate("/".join(map(str, parts))))


def destroyed_wheels(vox, wheel_layers, owner):
    """Wheels shot out: the tyres torn and flat at the bottom, scorched, the rims dulled."""
    rnd = random.Random(11)
    out = {}
    zs = {}
    for k, (mat, part) in vox.items():
        if owner.get(k) in wheel_layers:
            zs.setdefault((k[0] // 8, k[1] > 0), []).append(k[2])
    for k, v in vox.items():
        if owner.get(k) not in wheel_layers:
            out[k] = v
            continue
        mat, part = v
        if mat in ("rubber", "groove"):
            low = min(zs[(k[0] // 8, k[1] > 0)])
            if k[2] <= low + 1 or rnd.random() < 0.3:
                continue
            mat = "char" if rnd.random() < 0.5 else "black"
        elif mat in ("steel", "hub", "rim", "rim_hi"):
            mat = "gun"
        out[k] = (mat, part)
    return out


def wheel_hubs(vox, owner, wheel_layers):
    """Each wheel's hub (f, z), the r of its outer face and its rim's radius, by axle and side."""
    wheels = []
    for right in (False, True):
        side = [(k, mat) for k, (mat, _) in vox.items() if owner.get(k) in wheel_layers and (k[1] >= 0) == right]
        fs = sorted({k[0] for k, _ in side})
        start = None
        for i, f in enumerate(fs):
            start = f if start is None else start
            if i + 1 == len(fs) or fs[i + 1] - f > 1:
                wheels.append((right, [m for m in side if start <= m[0][0] <= f]))
                start = None
    hubs = []
    for right, members in wheels:
        fs = [k[0] for k, _ in members]
        zs = [k[2] for k, _ in members]
        fc, zc = (min(fs) + max(fs) + 1) / 2, (min(zs) + max(zs) + 1) / 2
        outer = max(k[1] for k, _ in members) if right else min(k[1] for k, _ in members)
        rim = [math.hypot(k[0] + 0.5 - fc, k[2] + 0.5 - zc) for k, mat in members if mat not in TYRE_MATS]
        hubs.append((min(fs), max(fs), right, fc, zc, outer, max(rim) if rim else 1.0))
    return hubs


def rim_map(scene, pixels, hubs, frame):
    """Of a wheel frame's pixels, those on a rim's outer face, coloured with their place on it."""
    out = {}
    for p in pixels:
        e = scene.px[p][3]
        if e[1] in TYRE_MATS or e[1] == V.GLASS:
            continue
        f, r, z = e[4]
        for f0, f1, right, fc, zc, outer, rim_r in hubs:
            if f0 - 1 <= f <= f1 + 1 and (r >= 0) == right:
                if r != outer:
                    break
                s = 5.6 / rim_r
                u = max(-5, min(5, round((f + 0.5 - fc) * s)))
                v = max(-5, min(5, round((z + 0.5 - zc) * s)))
                out[p] = (4 + (u + 5) * RIM_STEP, 4 + (v + 5) * RIM_STEP, 16 + 64 * frame, 255)
                break
    return out


def lit_layer(gen, vox, owner, lamp, kind, view):
    """A lamp lit: its glowing lens, the light it casts over the bodywork beside it and a soft halo,
    for an unshaded overlay layer."""
    g = gen.module
    table = LIT[kind]
    scene_vox = dict(vox)
    lit_keys = set()
    for k, (mat, part) in vox.items():
        if owner.get(k) == lamp and mat in table:
            scene_vox[k] = (table[mat], "lights")
            lit_keys.add(k)
    if not lit_keys:
        return {}
    buf, _ = C.render(gen, view, g.Model(scene_vox))
    col = g.shade(buf)
    ox, oy = SIZE // 2, SIZE // 2 + C.dy(gen, view)
    glow_of = g.LIGHT_GLOW
    spill_of = g.LIGHT_SPILL
    lit = {(x + ox, y + oy): (col[(x, y)], e[1]) for (x, y), e in buf.items() if e[2] == "lights" and e[1] in glow_of}
    out = {}
    for (x, y), e in buf.items():
        if e[2] == "lights":
            continue
        p = (x + ox, y + oy)
        best = None
        for (lx, ly), (_, mat) in lit.items():
            colour, reach, peak = spill_of[mat]
            d = math.hypot(p[0] - lx, p[1] - ly)
            a = round(peak * (1 - d / reach) ** 2) if d < reach else 0
            if a and (best is None or a > best[3]):
                best = colour + (a,)
        if best:
            out[p] = best
    for (x, y), (c, mat) in lit.items():
        glow = glow_of[mat]
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                d = math.hypot(dx, dy)
                if d == 0 or d > 3.2:
                    continue
                q = (x + dx, y + dy)
                if q in lit:
                    continue
                out[q] = g.over(out.get(q), glow + (round(110 * (1 - d / 3.4)),))
    for p, (c, _) in lit.items():
        out[p] = tuple(c[:3]) + (255,)
    return out


def render_states(gen, project, seats=None):
    """Every state, as {name: {"frames": {direction: [pixels per frame]}, "delay": ...}}."""
    g = gen.module
    project = prepared(project)
    box = V.Box.from_dict(project["box"])
    layers = project["layers"]
    role = roles(project)
    seats = seats if seats is not None else (project.get("vehicle") or {}).get("seats", [])
    ids = {n: part_id(lay) for n, lay in enumerate(layers)}
    by_name = {lay["name"]: n for n, lay in enumerate(layers)}
    base_layers = {n for n, r in role.items() if r == "base"}
    wheel_layers = {n for n, r in role.items() if r == "wheels"}
    panels = [n for n, r in role.items() if r == "panel"]
    lamps = [n for n, r in role.items() if r == "lamp"]
    engines = [n for n, r in role.items() if r == "engine"]
    shields = [n for n, r in role.items() if r == "windshield"]
    kits = [n for n, r in role.items() if r == "kit"]
    # The panel each kit hangs on, if it hangs on one.
    hosts = {k: by_name.get(layers[k]["kit"].get("host")) for k in kits}
    hosts = {k: h for k, h in hosts.items() if role.get(h) == "panel"}
    crease = getattr(g, "CREASE", (34, 38, 24, 255))
    states = {}

    def add(name, view, frames, delay=None):
        st = states.setdefault(name, {"frames": {}, "delay": delay})
        st["frames"][view] = frames

    def keep(n):
        """A moving part's layers: itself and what rides on it, but not kit, which is its own part."""
        return {n} | {i for i, l in enumerate(layers)
                      if l.get("rides") and l.get("rides") == layers[n]["name"] and role[i] not in ("kit", "stage")}

    def broken(pixels, at):
        """A part's pixels scorched and holed, for its broken look without a damage drawing."""
        spots = g.damage(pixels, at, light=True)
        return {p: g.blend(c, spots[p]) if p in spots else c for p, c in pixels.items()}

    # Each part's damage states, and the stage its broken look is drawn from: its worst.
    staged = {n: damage_states(project, n) for n, r in role.items()
              if r in ("base", "panel", "lamp", "engine", "windshield", "kit")}
    staged = {n: s for n, s in staged.items() if s}
    worst = {n: worst_stage(project, n) for n in staged}
    worst_project = showing(project, worst) if worst else project
    folds = {n: fold_angle(gen, project, box, n) for n in shields}

    wheel_parts = set(getattr(g, "WHEEL_PARTS", {"wheel"})) | {layers[n].get("role") or f"L{n}" for n in wheel_layers}

    cam = C.resolve(project, box)
    with V.styled(gen, project, box):
        # A vehicle drawn from one view is drawn once, in that view, then turned to each direction.
        for view in cam.view if cam.one else DIRS:
            riders = Riders(gen, seats, view)
            vox, owner, per_layer = build(gen, project, box, view)
            closed = Scene(gen, vox, owner, view)
            depth = {p: v[2] for p, v in closed.px.items()}
            # Kept by project (which each entry holds on to, so its id stays its own).
            scenes = {id(project): (project, vox, owner, closed)}
            swings = {}

            def scene_of(proj):
                """The vehicle shut, as the project given draws it: its voxels, owners and scene."""
                if id(proj) not in scenes:
                    pvox, powner, _ = build(gen, proj, box, view)
                    scenes[id(proj)] = (proj, pvox, powner, Scene(gen, pvox, powner, view))
                return scenes[id(proj)][1:]

            def swing(proj, n, layer_set):
                """A hinged layer swinging open on the project given: per frame, its scene and the
                pixels and depths of the layers given. One that doesn't swing stays shut."""
                if (id(proj), n) not in swings:
                    found = V.swing_scenes(gen, proj, box, proj["materials"], n, view)
                    swings[(id(proj), n)] = (proj, [Scene(gen, s, o, view) for s, o in found] or [scene_of(proj)[2]])
                return [(sc, *sc.own(layer_set)) for sc in swings[(id(proj), n)][1]]

            def engine_scene(proj, n):
                pid = ids[n]
                hidden = [m for m in panels if pid in (layers[m].get("exposes") or []) or layers[m].get("kind") == "hood"]
                evox, eowner, _ = build(gen, proj, box, view, skip=hidden)
                return Scene(gen, evox, eowner, view)

            def at_rest(proj, n):
                """A part's pixels and depths shut, as the project given draws it: the body for a body
                layer, the engine with its hood off."""
                if role[n] == "base":
                    pvox, powner, sc = scene_of(proj)
                    return body_of(gen, pvox, powner, view, base_layers, sc)
                if role[n] == "engine":
                    return engine_scene(proj, n).own({n})
                return scene_of(proj)[2].own(keep(n) if role[n] in ("panel", "windshield") else {n})

            # The body: what the closed vehicle shows of it, and what the panels, wheels and lamps
            # hide, so it shows when they open or come off.
            body, body_depth = body_of(gen, vox, owner, view, base_layers, closed)
            under, over = split(body, body_depth, riders)
            add("base", view, [under])
            add("overlay", view, [over])
            # The frame damage the game fades in as the hull wears: what the body's worst damage
            # drawings change of it, or without any, the generator's scorches, scrapes and holes.
            shown = {n: worst[n] for n in base_layers if n in worst}
            if shown:
                pvox, powner, sc = scene_of(showing(project, shown))
                worn, worn_depth = body_of(gen, pvox, powner, view, base_layers, sc)
                diff = changed(body, worn, crease)
                du, do = split(diff, {p: worn_depth.get(p, body_depth.get(p, 0)) for p in diff}, riders)
                add("damaged_frame", view, [du])
                add("damaged_overlay", view, [do])
            else:
                add("damaged_frame", view, [g.damage(under, seed(view, "base"))])
                add("damaged_overlay", view, [g.damage(over, seed(view, "overlay"))])

            # Hinged panels, each frame drawn with the panel at its angle.
            for n in panels:
                pid = ids[n]
                frames = swing(project, n, keep(n))
                frames_u, frames_o = [], []
                for _, mine, d in frames:
                    u, o = split(mine, d, riders)
                    frames_u.append(u)
                    frames_o.append(o)
                add(f"{pid}_closed", view, [frames_u[0]])
                add(f"{pid}_closed_overlay", view, [frames_o[0]])
                add(f"{pid}_open", view, [frames_u[-1]])
                add(f"{pid}_open_overlay", view, [frames_o[-1]])
                add(f"{pid}_opening", view, frames_u, SWING_DELAY)
                add(f"{pid}_opening_overlay", view, frames_o, SWING_DELAY)
                add(f"{pid}_closing", view, frames_u[::-1], SWING_DELAY)
                add(f"{pid}_closing_overlay", view, frames_o[::-1], SWING_DELAY)
                add(f"{pid}_outline", view, [g.outline(set(frames_u[0]) | set(frames_o[0]))])
                add(f"{pid}_open_outline", view, [g.outline(set(frames_u[-1]) | set(frames_o[-1]))])

            # The windshield up, down and folding between, whole and shot through: its worst damage
            # drawing, or bullet holes in its glass.
            for n in shields:
                pid = ids[n]
                for look in ("0", "1"):
                    proj = worst_project if look == "1" and n in worst else project
                    found = fold_scenes(gen, proj, box, n, view, folds[n])
                    frames = [Scene(gen, s, o, view) for s, o in found] or [scene_of(proj)[2]]
                    frames_u, frames_o, pts = [], [], []
                    for sc in frames:
                        mine, d = sc.own(keep(n))
                        if look == "1" and n not in worst:
                            if hasattr(g, "bullet_holes"):
                                mine = dict(mine)
                                g.bullet_holes(mine, sc.glass_of(keep(n)), seed(view, pid, "fold"), 4)
                            else:
                                mine = broken(mine, seed(view, pid))
                        u, o = split(mine, d, riders)
                        frames_u.append(u)
                        frames_o.append(o)
                        pts.append(set(mine))
                    add(f"{pid}_up_{look}", view, [frames_u[0]])
                    add(f"{pid}_up_overlay_{look}", view, [frames_o[0]])
                    add(f"{pid}_down_{look}", view, [frames_u[-1]])
                    add(f"{pid}_down_overlay_{look}", view, [frames_o[-1]])
                    add(f"{pid}_folding_{look}", view, frames_u, SWING_DELAY)
                    add(f"{pid}_folding_overlay_{look}", view, frames_o, SWING_DELAY)
                    add(f"{pid}_raising_{look}", view, frames_u[::-1], SWING_DELAY)
                    add(f"{pid}_raising_overlay_{look}", view, frames_o[::-1], SWING_DELAY)
                    if look == "0":
                        add(f"{pid}_up_outline", view, [g.outline(pts[0])])
                        add(f"{pid}_down_outline", view, [g.outline(pts[-1])])
                        add(f"click_{pid}_up", view, [mask(pts[0], frames[0])])
                        add(f"click_{pid}_down", view, [mask(pts[-1], frames[-1])])

            # Kit stowed on the vehicle, whole and broken (its worst damage drawing, or scorched);
            # kit hung on a panel also open, opening and closing with it.
            for k in kits:
                pid = ids[k]
                host = hosts.get(k)
                for look in ("0", "1"):
                    proj = worst_project if look == "1" and k in worst else project
                    mine, d = scene_of(proj)[2].own({k})
                    if look == "1" and k not in worst:
                        mine = broken(mine, seed(view, pid))
                    u, o = split(mine, d, riders)
                    add(f"{pid}_{look}", view, [u])
                    add(f"{pid}_overlay_{look}", view, [o])
                    if look == "0":
                        add(f"{pid}_outline", view, [g.outline(set(mine))])
                        add(f"click_{pid}", view, [mask(set(mine), closed)])
                    if host is None:
                        continue
                    frames = swing(proj, host, {k})
                    frames_u, frames_o = [], []
                    for _, fm, fd in frames:
                        if look == "1" and k not in worst:
                            fm = broken(fm, seed(view, pid))
                        u, o = split(fm, fd, riders)
                        frames_u.append(u)
                        frames_o.append(o)
                    add(f"{pid}_open_{look}", view, [frames_u[-1]])
                    add(f"{pid}_open_overlay_{look}", view, [frames_o[-1]])
                    add(f"{pid}_opening_{look}", view, frames_u, SWING_DELAY)
                    add(f"{pid}_opening_overlay_{look}", view, frames_o, SWING_DELAY)
                    add(f"{pid}_closing_{look}", view, frames_u[::-1], SWING_DELAY)
                    add(f"{pid}_closing_overlay_{look}", view, frames_o[::-1], SWING_DELAY)
                    if look == "0":
                        add(f"{pid}_open_outline", view, [g.outline(set(frames[-1][1]))])
                        add(f"click_{pid}_open", view, [mask(set(frames[-1][1]), frames[-1][0])])

            # Lamps: whole, broken (their worst damage drawing, or cracked), and lit.
            for n in lamps:
                pid = ids[n]
                mine = closed.of({n})
                u, o = split(mine, depth, riders)
                add(f"{pid}_0", view, [u])
                add(f"{pid}_overlay_0", view, [o])
                if n in worst:
                    sc = scene_of(worst_project)[2]
                    cracked = sc.of({n})
                    bu, bo = split(cracked, {p: sc.px[p][2] for p in cracked}, riders)
                else:
                    spots = g.damage(mine, seed(view, pid), light=True)
                    cracked = {p: g.blend(c, spots[p]) if p in spots else c for p, c in mine.items()}
                    bu, bo = split(cracked, depth, riders)
                add(f"{pid}_1", view, [bu])
                add(f"{pid}_overlay_1", view, [bo])
                add(f"{pid}_outline", view, [g.outline(set(mine))])
                kind = lamp_role(layers[n])
                if kind in ("head", "search"):
                    # A searchlight's lit lens isn't drawn in game (its beam is), but says where it is.
                    add(f"{pid}_on", view, [lit_layer(gen, vox, owner, n, "head", view)])
                elif kind == "tail":
                    add(f"{pid}_on", view, [lit_layer(gen, vox, owner, n, "tail", view)])
                    add(f"{pid}_brake", view, [lit_layer(gen, vox, owner, n, "brake", view)])
                    add(f"{pid}_signal", view, [lit_layer(gen, vox, owner, n, "signal", view), {}], SIGNAL_DELAY)
                else:
                    add(f"{pid}_signal", view, [lit_layer(gen, vox, owner, n, "signal", view), {}], SIGNAL_DELAY)

            # Wheels turning on their frames, and shot out; and where their rims show, for rim designs.
            hubs = wheel_hubs(vox, owner, wheel_layers)
            frames_u, frames_o, rims_u, rims_o = [], [], [], []
            for k in range(gen.wheel_frames):
                wvox, wowner, _ = build(gen, project, box, view, frame=k) if k else (vox, owner, None)
                sc = Scene(gen, wvox, wowner, view)
                mine = {p: v[0] for p, v in sc.px.items() if v[1] in wheel_layers and v[3][2] in wheel_parts}
                d = {p: sc.px[p][2] for p in mine}
                u, o = split(mine, d, riders)
                frames_u.append(u)
                frames_o.append(o)
                ru, ro = split(rim_map(sc, mine, hubs, k), d, riders)
                rims_u.append(ru)
                rims_o.append(ro)
            add("wheels_0", view, frames_u, gen.wheel_delay)
            add("wheels_overlay_0", view, frames_o, gen.wheel_delay)
            add("rims_wheels_0", view, rims_u, gen.wheel_delay)
            add("rims_wheels_overlay_0", view, rims_o, gen.wheel_delay)
            dvox = destroyed_wheels(vox, wheel_layers, owner)
            sc = Scene(gen, dvox, owner, view)
            mine = {p: v[0] for p, v in sc.px.items() if v[1] in wheel_layers}
            u, o = split(mine, {p: sc.px[p][2] for p in mine}, riders)
            add("wheels_1", view, [u])
            add("wheels_overlay_1", view, [o])
            add("wheels_outline", view, [g.outline(set(frames_u[0]) | set(frames_o[0]))])

            # The engine, drawn whole as it shows under a raised hood, and failing: its worst damage
            # drawing, or scorched.
            for n in engines:
                pid = ids[n]
                mine = engine_scene(project, n).of({n})
                add(f"{pid}_0", view, [mine])
                if n in worst:
                    add(f"{pid}_1", view, [engine_scene(worst_project, n).of({n})])
                else:
                    add(f"{pid}_1", view, [{**mine, **{p: g.blend(mine[p], c) for p, c in g.damage(mine, seed(view, pid), light=True).items()}}])
                add(f"{pid}_outline", view, [g.outline(set(mine))])

            # Each damage state at each of its levels: what it changes of its part at rest, to draw
            # over it.
            for n, own_states in staged.items():
                pid = ids[n]
                intact, intact_depth = at_rest(project, n)
                for s in own_states:
                    for level, name in s["levels"]:
                        worn, worn_depth = at_rest(showing(project, {n: name}), n)
                        diff = changed(intact, worn, crease)
                        u, o = split(diff, {p: worn_depth.get(p, intact_depth.get(p, 0)) for p in diff}, riders)
                        add(f"{pid}_dmg_{s['slug']}_{level}", view, [u])
                        add(f"{pid}_dmg_{s['slug']}_overlay_{level}", view, [o])

            # The front crumpled in levels: the pixels it changes, dark where it no longer reaches.
            for level in range(1, CRUMPLE_LEVELS + 1):
                dented_vox = g.crumple(g.Model(vox), level, seed("crumple"))
                dented = Scene(gen, dented_vox, owner, view)
                u, o = {}, {}
                for p in set(closed.px) | set(dented.px):
                    old, new = closed.px.get(p), dented.px.get(p)
                    if old is not None and new is not None and old[0] == new[0]:
                        continue
                    colour = new[0] if new is not None else g.CREASE
                    d = (new or old)[2]
                    (o if riders.over(p, d) else u)[p] = colour
                add(f"front_dent_{level}", view, [u])
                add(f"front_dent_overlay_{level}", view, [o])
                # The shut hood crumpled with the front, swapped in for its shut state.
                for n in panels:
                    if layers[n].get("kind") != "hood":
                        continue
                    lid = {p: v[0] for p, v in dented.px.items() if v[1] == n}
                    hu, ho = split(lid, {p: dented.px[p][2] for p in lid}, riders)
                    add(f"{ids[n]}_closed_dent{level}", view, [hu])
                    add(f"{ids[n]}_closed_dent{level}_overlay", view, [ho])

            # Clicks: each part's pixels as the vehicle shows them, faint.
            for n in panels:
                pid = ids[n]
                add(f"click_{pid}_closed", view, [mask(set(states[f"{pid}_closed"]["frames"][view][0]) |
                                                       set(states[f"{pid}_closed_overlay"]["frames"][view][0]), closed)])
                # Open, where it swung to; a panel that doesn't swing is clicked where it is.
                sc, mine, _ = swing(project, n, {n})[-1]
                add(f"click_{pid}_open", view, [mask(set(mine), sc)])
            for n in lamps:
                pid = ids[n]
                pts = set(closed.of({n}))
                add(f"click_{pid}", view, [mask(pts | set(g.outline(pts)), closed)])
            add("click_wheels", view, [mask({p for p, v in closed.px.items() if v[1] in wheel_layers}, closed)])
            for n in engines:
                pid = ids[n]
                add(f"click_{pid}", view, [{p: c[:3] + (MASK_ALPHA,) for p, c in states[f"{pid}_0"]["frames"][view][0].items()}])

            # Seats: the seat layers' pixels nearest each seat, or without any, what the vehicle shows
            # where its rider sits. The game asks for the same with the seat's door open (or off).
            seat_layers = {n for n, lay in enumerate(layers) if lay.get("role") == "seat" and role[n] == "base"}
            mine = {}
            for p, v in closed.px.items():
                if v[1] not in seat_layers or not seats:
                    continue
                f, r, z = v[3][4]
                best = min(seats, key=lambda s: (s["f"] - f) ** 2 + (s["r"] - r) ** 2)
                mine.setdefault(best["id"], set()).add(p)
            for s in seats:
                pts = mine.get(s["id"])
                if not pts:
                    _, x0, x1, y0, y1 = riders.rect_of(s)
                    pts = {p for p in closed.px if x0 + 3 <= p[0] <= x1 - 3 and y0 + 8 <= p[1] <= y1}
                add(f"seat_{s['id']}_outline", view, [g.outline(pts)])
                add(f"seat_{s['id']}_open_outline", view, [g.outline(pts)])
                add(f"click_seat_{s['id']}", view, [mask(pts, closed)])
                add(f"click_seat_{s['id']}_open", view, [mask(pts, closed)])

            # The crayon map: per pixel the body voxel and face it shows, drawn over the riders or not,
            # and the panel it is on (by its place in the panel list), with every panel shut.
            surface = {}
            for p, (c, n, d, e) in closed.px.items():
                if n < 0 or role.get(n) in ("wheels", "lamp", "windshield", "kit") or e[1] == V.GLASS:
                    continue
                f, r, z = e[4]
                panel = panels.index(n) + 1 if n in panels else 0
                surface[p] = (f + 128, r + 128, 2 * z + (1 if e[3] == "front" else 0),
                              (128 if riders.over(p, d) else 255) - panel)
            add("crayon_map", view, [surface])

            # The wreck: stripped of its glass, lamps and kit, on its shot out wheels, burnt dark.
            wreck_vox = {k: v for k, v in dvox.items() if role.get(owner.get(k)) not in ("lamp", "kit")}
            wreck = Scene(gen, wreck_vox, owner, view)
            rnd = random.Random(seed(view, "wreck"))
            burnt = {}
            for p, (c, n, d, e) in wreck.px.items():
                if e[1] == V.GLASS:
                    continue
                r_, g_, b_, a = c
                lum = (0.3 * r_ + 0.59 * g_ + 0.11 * b_) * 0.55
                roll = rnd.random()
                lum *= 0.55 if roll < 0.1 else 1.3 if roll > 0.95 else 1.0
                burnt[p] = (round(min(255, lum + 8)), round(min(255, lum * 0.88 + 6)), round(min(255, lum * 0.75 + 4)),
                            255 if a >= g.PAINT_MIN_ALPHA else a)
            add("wreck", view, [burnt])

    if cam.mode == "sprite":
        import sprite as S
        S.recolour(states, project, ids, role, g, MASK_ALPHA)
    if cam.one:
        for st in states.values():
            own = st["frames"][cam.view]
            st["frames"] = {v: [C.turn_pixels(f, cam.turns(v)) for f in own] for v in DIRS}

    # A wheel kit on road tyres looks the same here: the tool draws one tyre.
    for k in kits:
        if not wheel_kit(layers[k]):
            continue
        pid = ids[k]
        for prefix in TYRE_PREFIXES:
            for suffix in ("0", "overlay_0", "open_0", "open_overlay_0", "opening_0", "opening_overlay_0",
                           "closing_0", "closing_overlay_0"):
                if f"{pid}_{suffix}" in states:
                    states[f"{prefix}{pid}_{suffix}"] = states[f"{pid}_{suffix}"]

    # The game's vehicle parts start on the jeep's hood mask, looked up in this sprite when they move
    # over to it: a vehicle without a hood still has the state, empty.
    if "click_hood_closed" not in states:
        states["click_hood_closed"] = {"frames": {v: [{}] for v in DIRS}, "delay": None}

    for name in ("PRIMARY", "ATTACH", "WHEEL"):
        states[name] = {"frames": {"S": [{}]}, "delay": None, "single": True}
    return states


def mask(pixels, scene):
    """A click mask: the pixels as the scene shows them, faint."""
    return {p: scene.px[p][0][:3] + (MASK_ALPHA,) for p in pixels if p in scene.px}


def overflow(states, margin=0):
    """How far each direction's drawing runs off its frame, in pixels past the margin (one keeps a
    pixel clear for the hover outline): {direction: {"left", "right", "top", "bottom"}}, only those
    over. Outlines are left out: they are a pixel outside what they outline."""
    out = {}
    for name, st in states.items():
        if st.get("single") or name.endswith("outline"):
            continue
        for view, frames in st["frames"].items():
            for f in frames:
                if not f:
                    continue
                xs = [p[0] for p in f]
                ys = [p[1] for p in f]
                over = {"left": margin - min(xs), "right": max(xs) - (SIZE - 1 - margin),
                        "top": margin - min(ys), "bottom": max(ys) - (SIZE - 1 - margin)}
                mine = out.setdefault(view, {})
                for side, n in over.items():
                    if n > 0:
                        mine[side] = max(mine.get(side, 0), n)
    return {v: o for v, o in out.items() if o}


def overflow_text(over):
    words = {"S": "South", "N": "North", "E": "East", "W": "West"}
    return "; ".join(f"{words[v]}: " + ", ".join(f"{n} px past the {side} edge" for side, n in sides.items())
                     for v, sides in over.items())


# A panel's states while swung open or swinging (a tailgate dropped behind the vehicle): these may
# run off the frame, which only cuts the panel off at the edge.
SWUNG = re.compile(r"_(open|opening|closing)(_overlay)?(_\d+)?$")


def check_canvas(states):
    """Raises if any state of the vehicle as it stands runs off its frame, naming each direction and
    how far. A panel swung open past the edge only warns: the frame cuts it off there, and the vehicle
    itself still fits. Also warns for drawings that reach the edge, where their hover outline is cut
    off."""
    swung = {n for n in states if SWUNG.search(n)}
    standing = {n: st for n, st in states.items() if n not in swung}
    over = overflow(standing)
    if over:
        raise ValueError(f"The vehicle doesn't fit its {SIZE} px sprite frame ({overflow_text(over)}). The game "
                         f"draws open vehicles in {SIZE} px frames (its crayon map, paint shader and the riders' "
                         f"depth assume it): make it smaller in the directions named, or move it in its box.")
    warnings = []
    cut = overflow({n: states[n] for n in swung})
    if cut:
        parts = sorted({SWUNG.sub("", n).removeprefix("click_") for n in swung if overflow({n: states[n]})})
        warnings.append(f"Swung open, the {', '.join(parts)} runs off the {SIZE} px frame ({overflow_text(cut)}), "
                        "so the game cuts it off at the edge. Shorten it, or move the vehicle in its box, to keep it whole.")
    edge = overflow(standing, 1)
    if edge:
        words = {"S": "South", "N": "North", "E": "East", "W": "West"}
        where = "; ".join(f"{words[v]}: the {' and '.join(sides)} edge" for v, sides in edge.items())
        warnings.append(f"The vehicle touches the edge of its {SIZE} px frame, which cuts off its hover outline "
                        f"there ({where}).")
    return warnings


def write_rsi(path, states, copyright_text, skip=("crayon_map",)):
    """Writes the states as an RSI folder: one sheet per state, directions in RSI order."""
    os.makedirs(path, exist_ok=True)
    meta_states = []
    for name, st in states.items():
        if name in skip:
            continue
        views = ["S"] if st.get("single") else list(DIRS)
        tiles = [to_image(f) for v in views for f in st["frames"][v]]
        cols = math.ceil(math.sqrt(len(tiles)))
        rows = math.ceil(len(tiles) / cols)
        sheet = Image.new("RGBA", (SIZE * cols, SIZE * rows), (0, 0, 0, 0))
        for i, t in enumerate(tiles):
            sheet.paste(t, ((i % cols) * SIZE, (i // cols) * SIZE))
        sheet.save(os.path.join(path, name + ".png"))
        entry = {"name": name}
        if not st.get("single"):
            entry["directions"] = 4
        frames = len(st["frames"]["S"])
        if frames > 1 or st.get("delay"):
            entry["delays"] = [[st.get("delay") or 0.1] * frames for _ in views]
        meta_states.append(entry)
    meta = {"version": 1, "license": "CC-BY-SA-3.0", "copyright": copyright_text,
            "size": {"x": SIZE, "y": SIZE}, "states": meta_states}
    with open(os.path.join(path, "meta.json"), "w", newline="\n") as fh:
        json.dump(meta, fh, indent=4)
        fh.write("\n")


def write_crayon_map(path, states):
    """The crayon map as the game reads it: the four directions in a 2x2 grid."""
    tiles = [to_image(states["crayon_map"]["frames"][v][0]) for v in DIRS]
    sheet = Image.new("RGBA", (SIZE * 2, SIZE * 2), (0, 0, 0, 0))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 2) * SIZE, (i // 2) * SIZE))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path)
