"""A vehicle drawn in the tool, written into the game: its sprites (export.render_states), its crayon
map, its prototypes (the vehicle on CMUOpenVehicleBase, the overlay drawn over its riders and its
wreck) and its strings, under the open vehicles' tree:

    Resources/Prototypes/CMU14/CMUOpenVehicles/Vehicles/<Id>/<slug>.yml
    Resources/Textures/CMU14/CMUOpenVehicles/Vehicles/<Id>/<slug>.rsi, crayon.png
    Resources/Locale/en-US/CMU14/CMUOpenVehicles/<slug>.ftl

What the prototypes need from the drawing is worked out from it: where each part's click mask goes,
which directions draw a part over the riders, where the riders sit, the headlights' lenses, the
click bounds, the hull and engine bay, the tub the painter tells from the outside and how far forward
the front starts. The engine's smoke and fire are the jeep's, moved onto this engine.

The project's "vehicle" block names it, lists its seats and may tune it (every key optional, the
jeep's values where left out; see DEFAULT_STATS and DEFAULT_SOUNDS):
    {"id": "Roadster", "name": "roadster", "description": "...",
     "seats": [{"id": "driver", "f": -6.5, "r": -8.5, "z": 8, "driver": true, "door": "Driver door"}, ...],
     "stats": {"maxSpeed": 9, "acceleration": 5, "integrity": 300, "fuel": 50, "fuelStart": 40, "crashDamage": 30},
     "sounds": {"horn": "/Audio/CMU14/Jeep/jeep_horn.wav", ...}}
A seat may say "windshield": true or false; by default the front row behind a windshield has it.

A layer of kind "windshield" is the vehicle's folding windshield (one at most). A layer with "kit" is
kit stowed in a slot of its own: {"item": entity id, "tag": whitelist tag, "host": the hinged layer
it hangs on (or null), "maskOnly": filled only by clicking its own mask, "name": what the slot is
called (else the layer's name)}; KIT_PRESETS has the jeep's.

    report = export_game.check(project)            # {"errors": [...], "warnings": [...]}, no rendering
    result = export_game.write(gen, project, repo) # raises ValueError on check() errors
"""

import json
import math
import os
import re

from PIL import Image

import export as X
import voxels as V

SEAT_NAMES = {"driver": "cmu-vehicle-seat-driver", "passenger": "cmu-vehicle-seat-passenger"}
LAMP_ITEMS = {"head": "CMUJeepHeadlight", "signal": "CMUJeepTurnSignal", "tail": "CMUJeepTailLight"}
DIRECTION = {"S": "South", "N": "North", "E": "East", "W": "West"}
INTERIOR_MATS = {"od_in", "od_in_flat"}
# Voxels to a tile, as the game places click masks and seats.
TILE = 32
# A grid vehicle's hull: the jeep's width while the body is no wider than the jeep's (43 voxels, which
# still goes down a one tile corridor), no longer than three tiles while it fits them.
MAX_HALF_WIDTH = 0.45
MAX_HALF_LENGTH = 1.45
JEEP_HALF_WIDTH = 22 / 32
JEEP_RSI = ("Content.CMU", "Resources", "Textures", "CMU14", "Structures", "vehicles", "jeep", "jeep.rsi")
EFFECTS = ("engine_smoke_0", "engine_smoke_1", "engine_fire")
# The game's damage states by type, when the repo has them (see the report on export).
DAMAGE_STATES_CS = "Content.CMU/Shared/Vehicle/Open/CMUVehicleDamageStatesComponent.cs"

# Kit the jeep stows, for the app to offer: the item a slot starts with and the tag it takes.
KIT_PRESETS = {
    "spare": {"label": "Spare wheel", "item": "CMUJeepWheel", "tag": "CMUJeepWheel"},
    "jerrycan": {"label": "Jerry can", "item": "RMCFuelCan", "tag": "CMUJerryCan"},
    "shovel": {"label": "Shovel", "item": "CMEntrenchingTool", "tag": "Shovel"},
    "axe": {"label": "Fire axe", "item": "RMCFireAxe", "tag": "FireAxe"},
    "jack": {"label": "Maintenance jack", "item": "AU14MaintenanceJack", "tag": "MaintenanceJack"},
    "extinguisher": {"label": "Fire extinguisher", "item": "CMFireExtinguisherPortable", "tag": "CMFireExtinguisherPortable"},
}
# The jerry can's slot: the game holes and drains the can in it (CMUOpenVehicleSystem.JerryCanSlot).
JERRYCAN_SLOT = "jeep-jerrycan"
WINDSHIELD_SLOT = "vehicle-windshield"
WINDSHIELD_ITEM = "CMUJeepWindshield"
HOOD_ITEM = "CMUJeepHood"

# The jeep's sounds, which every open vehicle starts with: resource paths.
DEFAULT_SOUNDS = {
    "start": "/Audio/CMU14/Jeep/jeep_engine_start.wav",
    "idle": "/Audio/CMU14/Jeep/jeep_engine_idle.wav",
    "drive": "/Audio/CMU14/Jeep/jeep_engine_drive.wav",
    "rough": "/Audio/CMU14/Jeep/jeep_engine_rough.wav",
    "roughDrive": "/Audio/CMU14/Jeep/jeep_engine_rough_drive.wav",
    "rev": "/Audio/CMU14/Jeep/jeep_engine_rev.wav",
    "stall": "/Audio/CMU14/Jeep/jeep_engine_sputter.wav",
    "horn": "/Audio/CMU14/Jeep/jeep_horn.wav",
    "hornLong": "/Audio/CMU14/Jeep/jeep_horn_long.wav",
    "blinker": "/Audio/CMU14/Jeep/jeep_blinker.wav",
    "door": "/Audio/CMU14/Jeep/jeep_hood_open.wav",
    "doorClose": "/Audio/CMU14/Jeep/jeep_hood_close.wav",
    "lid": "/Audio/CMU14/Jeep/jeep_hood_open.wav",
    "lidClose": "/Audio/CMU14/Jeep/jeep_hood_close.wav",
    "windshield": "/Audio/CMU14/Jeep/jeep_windshield.wav",
}
# What every open vehicle drives like (CMUOpenVehicleBase): top speed and acceleration (tiles a
# second), hull integrity, fuel tank and the fuel it starts with, and the hull damage of smashing
# through a wall at top speed. A vehicle overrides only what differs; the game merges a component
# field by field with its parent's.
DEFAULT_STATS = {"maxSpeed": 8, "acceleration": 5, "integrity": 400, "fuel": 60, "fuelStart": 45, "crashDamage": 30}


def sound(path, params, indent="      "):
    """A sound specifier's fields, at the indent given."""
    out = f"{indent}path: {path}\n"
    if params:
        out += f"{indent}params:\n" + "".join(f"{indent}  {k}: {v}\n" for k, v in params.items())
    return out


def panel_sounds(sounds, kind):
    """A hinged panel's sounds: a door's, or a lid's (hood, boot, hatch, tailgate); a fuel door is
    silent."""
    if kind == "door":
        params, opened, shut = {"volume": -5, "pitch": 1.15}, sounds["door"], sounds["doorClose"]
    elif kind in ("hood", "boot", "hatch", "tailgate"):
        params, opened, shut = {"volume": -2}, sounds["lid"], sounds["lidClose"]
    else:
        return ""
    return ("      openSound:\n" + sound(opened, params, "        ")
            + "      closeSound:\n" + sound(shut, params, "        "))


def driving(sounds):
    """The jeep's key, engine, horn and crash components, with the vehicle's sounds."""
    def engine(key, name, params):
        return f"    {key}:\n" + sound(sounds[name], params)

    return ("""  - type: CMUVehicleIgnition
    keySlot:
      name: cmu-jeep-part-ignition
      startingItem: CMUJeepKey
      swap: false
      insertSound: /Audio/Machines/id_insert.ogg
      ejectSound: /Audio/Machines/id_swipe.ogg
      whitelist:
        tags:
        - CMUJeepKey
  - type: CMUVehicleEngineSound
""" + engine("startSound", "start", {"maxDistance": 12})
        + engine("idleSound", "idle", {"volume": -2, "maxDistance": 10})
        + engine("driveSound", "drive", {"volume": -1, "maxDistance": 12})
        + engine("roughSound", "rough", {"volume": -2, "maxDistance": 10})
        + engine("roughDriveSound", "roughDrive", {"volume": -1, "maxDistance": 12})
        + engine("revSound", "rev", {"maxDistance": 12})
        + engine("stallSound", "stall", {"maxDistance": 12})
        + "  - type: CMUVehicleCrashKnockout\n" + engine("hornSound", "hornLong", {"volume": 2})
        + f"""  - type: VehicleSound
    hornSound: {sounds['horn']}
    hornCooldown: 1
    collisionSound: /Audio/_RMC14/Effects/metal_crash.ogg
    collisionSoundCooldown: 1.5
    collisionSoundMinSpeed: 7
    mobCollisionSound: /Audio/_RMC14/Xeno/alien_claw_block.ogg
""")


def tuning(stats):
    """The components a vehicle's own stats override: only the fields that differ from the base's."""
    out = ""
    mover = {"maxSpeed": "maxSpeed", "acceleration": "acceleration", "crashDamage": "wallSmashHullDamage"}
    changed = [(field, stats[key]) for key, field in mover.items() if stats[key] != DEFAULT_STATS[key]]
    if changed:
        out += "  - type: GridVehicleMover\n" + "".join(f"    {field}: {fmt(v)}\n" for field, v in changed)
    if stats["integrity"] != DEFAULT_STATS["integrity"]:
        out += f"  - type: HardpointIntegrity\n    maxIntegrity: {fmt(stats['integrity'])}\n    integrity: {fmt(stats['integrity'])}\n"
    if stats["fuel"] != DEFAULT_STATS["fuel"] or stats["fuelStart"] != DEFAULT_STATS["fuelStart"]:
        out += (f"  - type: Solution\n    id: fuel\n    solution:\n      maxVol: {fmt(stats['fuel'])}\n"
                f"      reagents:\n      - ReagentId: WeldingFuel\n        Quantity: {fmt(stats['fuelStart'])}\n")
    return out


def settings(veh):
    """The vehicle's stats and sounds, the defaults filled in. A fuel tank given without what it
    starts with starts three quarters full, as the jeep's."""
    given = veh.get("stats") or {}
    stats = {**DEFAULT_STATS, **{k: v for k, v in given.items() if v is not None}}
    if given.get("fuel") is not None and given.get("fuelStart") is None:
        stats["fuelStart"] = round(stats["fuel"] * DEFAULT_STATS["fuelStart"] / DEFAULT_STATS["fuel"], 2)
    sounds = {**DEFAULT_SOUNDS, **{k: v for k, v in (veh.get("sounds") or {}).items() if v}}
    return stats, sounds


def one_line(value):
    """Text on one line, for a YAML scalar or a Fluent string."""
    return " ".join(str(value).split())


def fluent(value):
    """Text as a Fluent string's value: on one line, its braces literal."""
    return "".join('{"' + c + '"}' if c in "{}" else c for c in one_line(value))


def text(value):
    """Free text as a YAML scalar: double quoted, so a colon, quote or hash in it stays text."""
    value = one_line(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def fmt(v):
    v = round(v, 4)
    return f"{0.0 if v == 0 else v:g}"


def centroid(pixels):
    xs = [p[0] for p in pixels]
    ys = [p[1] for p in pixels]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def vehicle_space(vox):
    """A part's place as the game spawns its click mask: tiles from the vehicle's centre, facing south
    (its left side is +x, its front -y)."""
    f = sum(k[0] + 0.5 for k in vox) / len(vox)
    r = sum(k[1] + 0.5 for k in vox) / len(vox)
    return -r / TILE, -f / TILE


def bounds_of(vox):
    """Voxels' extent facing south, in tiles: (left, bottom, right, top)."""
    fs = [k[0] for k in vox]
    rs = [k[1] for k in vox]
    return -(max(rs) + 1) / TILE, -(max(fs) + 1) / TILE, -min(rs) / TILE, -min(fs) / TILE


def box_text(b):
    return ",".join(fmt(v) for v in b)


def frame_point(p):
    """A canvas pixel as pixels from the vehicle origin, x right and y up."""
    return p[0] + 0.5 - X.SIZE / 2, X.SIZE / 2 - (p[1] + 0.5)


def over_directions(states, name):
    st = states.get(f"{name}_overlay", states.get(f"{name}_overlay_0", {"frames": {}}))
    return [DIRECTION[v] for v in X.DIRS if any(st["frames"].get(v, [{}])[0])]


def flow(items):
    return f"[ {', '.join(items)} ]"


def names(veh):
    """The vehicle's name, prototype id suffix, file slug and string prefix."""
    name = (veh.get("name") or "vehicle").strip()
    ident = re.sub(r"\W", "", veh.get("id") or name.title().replace(" ", "")) or "Vehicle"
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "vehicle"
    return name, ident, slug, slug.replace("_", "-")


def paths(repo, ident, slug):
    """Where each file goes."""
    res = os.path.join(repo, "Content.CMU", "Resources")
    tex = f"CMU14/CMUOpenVehicles/Vehicles/{ident}"
    return {
        "tex": tex,
        "rsi": os.path.join(res, "Textures", *tex.split("/"), f"{slug}.rsi"),
        "crayon": os.path.join(res, "Textures", *tex.split("/"), "crayon.png"),
        "proto": os.path.join(res, "Prototypes", "CMU14", "CMUOpenVehicles", "Vehicles", ident, f"{slug}.yml"),
        "ftl": os.path.join(res, "Locale", "en-US", "CMU14", "CMUOpenVehicles", f"{slug}.ftl"),
    }


def repo_has(repo, rel, needle):
    try:
        with open(os.path.join(repo, *rel.split("/")), encoding="utf-8") as fh:
            return needle in fh.read()
    except OSError:
        return False


def kit_slot(layer, pid, taken):
    """A kit's item slot: the jerry can's own for the first jerry can (so the game can hole it),
    else vehicle-<part>."""
    kit = layer["kit"]
    if JERRYCAN_SLOT not in taken and (kit.get("tag") == "CMUJerryCan" or kit.get("item") == "RMCFuelCan"):
        return JERRYCAN_SLOT
    return f"vehicle-{pid.replace('_', '-')}"


def front_row(seats, shield_f):
    """The seats behind the windshield in its first row: they ride behind it."""
    behind = [s for s in seats if s["f"] < shield_f]
    if not behind:
        return set()
    first = max(s["f"] for s in behind)
    return {s["id"] for s in behind if first - s["f"] <= 4}


# -- the check before export ----------------------------------------------------------------------

LAMP_WORDS = ("head", "tail", "brake", "rear", "signal", "indicator", "blinker")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def layer_extent(layer, box):
    """A layer's voxels' extent (f0, f1, r0, r1) from its drawing, without rendering; None for one
    loaded from the generator or with nothing drawn."""
    if layer.get("native"):
        return None
    grid = V.layer_voxels(layer, box)
    occ = grid != V.EMPTY if grid is not None else V.carve(layer, box)[0]
    if not occ.any():
        return None
    i = occ.any((1, 2)).nonzero()[0]
    j = occ.any((0, 2)).nonzero()[0]
    return i.min() + box.f0, i.max() + box.f0, j.min() + box.r0, j.max() + box.r0


def check(project):
    """What would stop the vehicle working in game (errors), and what may not be meant (warnings),
    from the project alone: {"errors": [...], "warnings": [...]}."""
    errors, warnings = [], []
    layers = project.get("layers") or []
    veh = project.get("vehicle") or {}
    role = X.roles(project)
    by_name = {}
    for n, lay in enumerate(layers):
        if lay["name"] in by_name:
            errors.append(f"Two layers are called {lay['name']}.")
        by_name.setdefault(lay["name"], n)
    box = V.Box.from_dict(project["box"])

    # What it is.
    name, ident, _, _ = names(veh)
    if veh.get("id") and not IDENT.fullmatch(veh["id"]):
        errors.append(f"The vehicle id {veh['id']!r} isn't a valid identifier (letters, digits and _, not starting "
                      f"with a digit): it becomes the prototype id CMUVehicle<id>.")
    elif not IDENT.fullmatch(ident):
        errors.append(f"The vehicle's id, from its name {name!r}, would be {ident!r}: give it an id.")
    if not any(r == "base" for r in role.values()):
        errors.append("There's no body layer.")
    if not any(r == "wheels" for r in role.values()):
        errors.append("There's no wheel layer.")

    # Its parts: each a unique id, which names its sprite layer, states, slot and strings.
    owners = {}
    reserved = {"base", "overlay", "body", "wheels", "rims", "hover", "smoke", "fire", "front_dent", "damaged_frame"}
    for n, r in role.items():
        if r in ("panel", "windshield", "kit", "lamp", "engine"):
            pid = X.part_id(layers[n])
            if pid in reserved:
                errors.append(f"{layers[n]['name']}'s part id {pid!r} is one the vehicle uses itself: rename the layer.")
            elif not pid:
                errors.append(f"{layers[n]['name']} has no letters or digits to make a part id from.")
            owners.setdefault(pid, []).append(layers[n]["name"])
    for pid, who in owners.items():
        if len(who) > 1:
            errors.append(f"{', '.join(who)} all make the part id {pid!r}" + (" (a vehicle has one engine)" if pid == "engine" else "")
                          + ": rename them or give them part ids.")
    shields = [n for n, r in role.items() if r == "windshield"]
    if len(shields) > 1:
        errors.append(f"The game folds one windshield; there are {len(shields)}: {', '.join(layers[n]['name'] for n in shields)}.")
    hoods = [n for n, r in role.items() if r == "panel" and layers[n].get("kind") == "hood"]
    engines = [n for n, r in role.items() if r == "engine"]
    if hoods and not engines:
        warnings.append(f"{layers[hoods[0]]['name']} opens onto no engine: no layer has the engine role.")
    if engines and not hoods:
        warnings.append("The engine has no hood over it: it can be reached and repaired at any time.")
    if shields and not hoods:
        warnings.append("The windshield folds onto the cowl: there's no hood to keep shut under it.")
    for n, r in role.items():
        if r == "panel":
            for pid in layers[n].get("exposes") or []:
                if pid not in owners:
                    warnings.append(f"{layers[n]['name']} exposes {pid!r}, which isn't one of the vehicle's parts.")

    # Lamps, told apart by their names.
    lamps = [n for n, r in role.items() if r == "lamp"]
    for n in lamps:
        if not any(w in layers[n]["name"].lower() for w in LAMP_WORDS):
            warnings.append(f"{layers[n]['name']} is taken as a headlight: name a lamp with head, tail, brake, rear, "
                            f"signal, indicator or blinker to tell the game what it is.")
    if not any(X.lamp_role(layers[n]) == "head" for n in lamps):
        warnings.append("There are no headlights: the vehicle drives dark.")

    # Kit.
    for n, r in role.items():
        if r != "kit":
            continue
        kit = layers[n]["kit"]
        for key in ("item", "tag"):
            if not kit.get(key) or not IDENT.fullmatch(str(kit.get(key))):
                errors.append(f"{layers[n]['name']}'s kit needs an {key} (an id): see KIT_PRESETS.")
        host = kit.get("host")
        if host and role.get(by_name.get(host)) != "panel":
            errors.append(f"{layers[n]['name']} hangs on {host!r}, which " +
                          ("isn't a layer." if host not in by_name else "isn't a hinged panel."))

    # Seats: a driver's, each inside the body, each door a hinged panel.
    seats = veh.get("seats") or []
    if not seats:
        errors.append("The vehicle has no seats.")
    elif not any(s.get("driver") for s in seats):
        errors.append("No seat is the driver's.")
    ids = [s.get("id") for s in seats]
    for sid in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"Two seats are {sid!r}.")
    extents = [e for e in (layer_extent(layers[n], box) for n, r in role.items() if r == "base") if e is not None]
    for s in seats:
        door = s.get("door")
        if door and door not in by_name:
            errors.append(f"Seat {s.get('id')}'s door {door!r} isn't a layer.")
        elif door and role.get(by_name[door]) != "panel":
            errors.append(f"Seat {s.get('id')}'s door {door!r} isn't a hinged panel (a door, hood, boot, tailgate or hatch).")
        if extents:
            f0, f1 = min(e[0] for e in extents), max(e[1] for e in extents)
            r0, r1 = min(e[2] for e in extents), max(e[3] for e in extents)
            if not (f0 - 1 <= s.get("f", 0) <= f1 + 1 and r0 - 1 <= s.get("r", 0) <= r1 + 1):
                errors.append(f"Seat {s.get('id')} at f {s.get('f')}, r {s.get('r')} is outside the body "
                              f"(f {f0} to {f1}, r {r0} to {r1}).")

    # Damage states the game has no look for.
    for n, lay in enumerate(layers):
        st = lay.get("stage")
        if not st:
            continue
        of = by_name.get(st.get("of"))
        if of is None:
            warnings.append(f"{lay['name']} is a damage state of {st.get('of')!r}, which isn't a layer: it's left out.")
        elif role.get(of) == "wheels":
            warnings.append(f"{lay['name']}: the wheels have no damage states in game (only shot out); it's left out.")

    # Its tuning.
    stats = veh.get("stats") or {}
    for key, v in stats.items():
        if key not in DEFAULT_STATS:
            warnings.append(f"Unknown stat {key!r}: the stats are {', '.join(DEFAULT_STATS)}.")
        elif v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0 or (v == 0 and key != "fuelStart")):
            errors.append(f"The stat {key} must be a positive number, not {v!r}.")
    if isinstance(stats.get("fuel"), (int, float)) and isinstance(stats.get("fuelStart"), (int, float)) and stats["fuelStart"] > stats["fuel"]:
        errors.append("The fuel the tank starts with is more than it holds.")
    for key, v in (veh.get("sounds") or {}).items():
        if key not in DEFAULT_SOUNDS:
            warnings.append(f"Unknown sound {key!r}: the sounds are {', '.join(DEFAULT_SOUNDS)}.")
        elif v and not (isinstance(v, str) and v.startswith("/Audio/")):
            errors.append(f"The sound {key} must be a resource path like /Audio/CMU14/Jeep/jeep_horn.wav, not {v!r}.")
    return {"errors": errors, "warnings": warnings}


# -- the jeep's engine effects --------------------------------------------------------------------

def borrow_effects(repo, states, engine_id):
    """The jeep's engine smoke and fire, each direction moved from the jeep's engine to this one's."""
    path = os.path.join(repo, *JEEP_RSI)
    try:
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            meta = {s["name"]: s for s in json.load(fh)["states"]}
    except OSError:
        return []
    mine = states.get(f"{engine_id}_0")
    if mine is None or "engine_0" not in meta:
        return []

    def frames(name):
        """A jeep state's frames per direction, as {pixel: rgba} each."""
        sheet = Image.open(os.path.join(path, f"{name}.png")).convert("RGBA")
        n = len(meta[name].get("delays", [[0]])[0])
        cols = sheet.width // X.SIZE
        out = {}
        for d, v in enumerate(X.DIRS):
            out[v] = []
            for k in range(n):
                i = d * n + k
                tile = sheet.crop(((i % cols) * X.SIZE, (i // cols) * X.SIZE, (i % cols + 1) * X.SIZE, (i // cols + 1) * X.SIZE))
                px = tile.load()
                out[v].append({(x, y): px[x, y] for y in range(X.SIZE) for x in range(X.SIZE) if px[x, y][3]})
        return out

    jeep_engine = frames("engine_0")
    done = []
    for name in EFFECTS:
        if name not in meta:
            continue
        src = frames(name)
        st = {"frames": {}, "delay": meta[name].get("delays", [[0.1]])[0][0]}
        for v in X.DIRS:
            here = mine["frames"][v][0]
            there = jeep_engine[v][0]
            if here and there:
                (hx, hy), (tx, ty) = centroid(here), centroid(there)
                dx, dy = round(hx - tx), round(hy - ty)
            else:
                dx = dy = 0
            st["frames"][v] = [{(x + dx, y + dy): c for (x, y), c in f.items()
                                if 0 <= x + dx < X.SIZE and 0 <= y + dy < X.SIZE} for f in src[v]]
        states[name] = st
        done.append(name)
    return done


# -- writing it -----------------------------------------------------------------------------------

def write(gen, project, repo, progress=None):
    """Writes everything into the repo and returns what it wrote. Raises ValueError with check()'s
    errors, or when the drawing runs off its sprite frame."""
    say = progress or (lambda text: None)
    report = check(project)
    if report["errors"]:
        raise ValueError("The vehicle can't be written into the game yet:\n- " + "\n- ".join(report["errors"]))
    warnings = list(report["warnings"])
    g = gen.module
    veh = project.get("vehicle") or {}
    name, ident, slug, loc = names(veh)
    stats, sounds = settings(veh)
    box = V.Box.from_dict(project["box"])
    project = X.prepared(project)
    layers = project["layers"]
    role = X.roles(project)
    ids = {n: X.part_id(lay) for n, lay in enumerate(layers)}
    by_name = {lay["name"]: n for n, lay in enumerate(layers)}
    seats = veh.get("seats", [])
    where = paths(repo, ident, slug)
    # The working tree's paint component maps each wheel layer to its slot; before rim designs it
    # listed them.
    paint_cs = "Content.CMU/Shared/Vehicle/Open/CMUVehiclePaintComponent.cs"
    wheel_maps = repo_has(repo, paint_cs, "Dictionary<string, string> WheelLayers")
    rims = repo_has(repo, paint_cs, "RimLayers")
    damage_by_type = repo_has(repo, DAMAGE_STATES_CS, "class CMUVehicleDamageStatesComponent")

    say("Rendering every sprite state")
    states = X.render_states(gen, project)
    warnings += X.check_canvas(states)
    engines = [n for n, r in role.items() if r == "engine"]
    effects = borrow_effects(repo, states, ids[engines[0]]) if engines else []
    say("Writing the sprites")
    X.write_rsi(where["rsi"], states, f"{name.title()} drawn in CMU Open Vehicles")
    X.write_crayon_map(where["crayon"], states)

    # Each layer's voxels, closed, for where its parts sit.
    models, owners, _, _ = V.build(project, box, project["materials"], ["S"], gen=gen)
    vox_of = {}
    for k, n in owners["S"].items():
        vox_of.setdefault(n, []).append(k)
    base_layers = [n for n, r in role.items() if r == "base"]
    body = [k for n in base_layers for k in vox_of.get(n, [])] or list(models["S"])

    panels = [n for n, r in role.items() if r == "panel"]
    lamps = [n for n, r in role.items() if r == "lamp"]
    shields = [n for n, r in role.items() if r == "windshield"]
    kits = [n for n, r in role.items() if r == "kit"]
    hosts = {k: by_name.get(layers[k]["kit"].get("host")) for k in kits}
    hosts = {k: h for k, h in hosts.items() if h in panels}

    def part_name(n):
        return f"cmu-{loc}-part-{ids[n].replace('_', '-')}"

    strings = {part_name(n): layers[n]["name"].lower() for n in panels + engines + shields}
    for k in kits:
        strings[part_name(k)] = (layers[k]["kit"].get("name") or layers[k]["name"]).lower()
    strings[f"cmu-{loc}-part-wheels"] = "wheels"

    def part(n, kind, over_of, extra=""):
        pid = ids[n]
        x, y = vehicle_space(vox_of.get(n, [(0, 0, 0)]))
        over = over_directions(states, over_of)
        out = f"    - id: {pid}\n      name: {part_name(n)}\n" + (f"      kind: {kind}\n" if kind else "")
        out += f"      offset: {fmt(x)},{fmt(y)}\n"
        if over:
            out += f"      overRiders: {flow(over)}\n"
        return out + extra

    # Item slots past the wheels': (slot id, name, item, tag, eject).
    slots = []
    parts = ""
    hood = next((ids[n] for n in panels if layers[n].get("kind") == "hood"), None)
    # With a windshield to fold onto it, the hood comes off as the jeep's does, so its raised lid can
    # draw over the windshield from a layer of its own.
    raised = f"{hood}_raised" if hood and shields else None
    for n in panels:
        kind = layers[n].get("kind")
        exposes = [ids[m] for m in engines] if kind == "hood" else list(layers[n].get("exposes") or [])
        extra = ""
        if raised and ids[n] == hood:
            slot = f"vehicle-{hood.replace('_', '-')}"
            slots.append((slot, part_name(n), HOOD_ITEM, HOOD_ITEM, False))
            extra += f"      slot: {slot}\n"
        extra += f"      exposes: {flow(exposes)}\n" if exposes else ""
        if raised and ids[n] == hood:
            extra += f"      raisedLayer: {raised}\n"
        extra += panel_sounds(sounds, kind)
        parts += part(n, "Panel", f"{ids[n]}_closed", extra)
    windshield_yaml = ""
    for n in shields:
        slots.append((WINDSHIELD_SLOT, part_name(n), WINDSHIELD_ITEM, WINDSHIELD_ITEM, False))
        extra = f"      slot: {WINDSHIELD_SLOT}\n" + (f"      interlock: [ {hood} ]\n" if hood else "")
        parts += part(n, "Windshield", f"{ids[n]}_up", extra)
        windshield_yaml = "    windshieldSound:\n" + sound(sounds["windshield"], {"volume": -2})
    for n in engines:
        parts += part(n, "Engine", ids[n])
    wheel_over = [DIRECTION[v] for v in X.DIRS if states["wheels_overlay_0"]["frames"][v][0]]
    parts += (f"    - id: wheels\n      name: cmu-{loc}-part-wheels\n      slot: wheel-1\n      kind: Wheels\n"
              + (f"      overRiders: {flow(wheel_over)}\n" if wheel_over else ""))
    kit_slots = {}
    for k in kits:
        kit = layers[k]["kit"]
        slot = kit_slot(layers[k], ids[k], kit_slots.values())
        kit_slots[k] = slot
        slots.append((slot, part_name(k), kit["item"], kit["tag"], True))
        extra = f"      slot: {slot}\n"
        if k in hosts:
            extra += f"      host: {ids[hosts[k]]}\n"
        if kit.get("maskOnly"):
            extra += "      maskOnly: true\n"
        parts += part(k, None, ids[k], extra)

    lamp_yaml = ""
    for n in lamps:
        pid = ids[n]
        kind = X.lamp_role(layers[n])
        item = LAMP_ITEMS[kind]
        lay = layers[n]
        x, y = vehicle_space(vox_of.get(n, [(0, 0, 0)]))
        side = ("Left" if lay.get("sides") == "left" or "left" in lay["name"].lower()
                else "Right" if lay.get("sides") == "right" or "right" in lay["name"].lower()
                else "Left" if x > 0 else "Right")
        over = over_directions(states, pid)
        role_name = {"head": "Headlight", "tail": "TailLight", "signal": "TurnSignal"}[kind]
        lit = {"head": f"      litLayer: {pid}_on\n",
               "tail": f"      litLayer: {pid}_on\n      brakeLayer: {pid}_brake\n      signalLayer: {pid}_signal\n",
               "signal": f"      signalLayer: {pid}_signal\n"}[kind]
        anchors = ""
        if kind == "head":
            anchors = "      beamAnchors:\n"
            for v in X.DIRS:
                lit_px = [p for p, c in states[f"{pid}_on"]["frames"][v][0].items() if c[3] == 255]
                ax, ay = frame_point(centroid(lit_px)) if lit_px else (0, 0)
                anchors += f"        {DIRECTION[v]}: {round(ax)},{round(ay)}\n"
        lamp_name = part_name(n)
        strings[lamp_name] = lay["name"].lower()
        lamp_yaml += (f"    - id: {pid}\n      name: {lamp_name}\n"
                      + (f"      overRiders: {flow(over)}\n" if over else "")
                      + f"      role: {role_name}\n      side: {side}\n{lit}{anchors}      offset: {fmt(x)},{fmt(y)}\n"
                      f"      slot:\n        name: {lamp_name}\n        startingItem: {item}\n        disableEject: true\n"
                      f"        swap: false\n        whitelist:\n          tags:\n          - {item}\n")

    # The hull: the body's footprint. A body no wider than the jeep's is held to the jeep's hull, so
    # it drives down a one tile corridor as the jeep does (its bodywork overhangs the hull, as the
    # jeep's does). A wider one gets a hull as much wider than the jeep's, and needs wider corridors.
    hl, hb, hr, ht = bounds_of(body)
    half_w, half_l = max(-hl, hr), max(-hb, ht)
    corridor = 1
    if half_w <= JEEP_HALF_WIDTH:
        hl, hr = max(hl, -MAX_HALF_WIDTH), min(hr, MAX_HALF_WIDTH)
    else:
        scale = MAX_HALF_WIDTH / JEEP_HALF_WIDTH
        hl, hr = hl * scale, hr * scale
        corridor = math.ceil(2 * max(-hl, hr))
        warnings.append(f"The body is {fmt(2 * half_w)} tiles wide, wider than the jeep's {fmt(2 * JEEP_HALF_WIDTH)}: "
                        f"its hull is {fmt(hr - hl)} tiles wide, so it needs {corridor} tile wide corridors.")
    if half_l <= 1.5:
        hb, ht = max(hb, -MAX_HALF_LENGTH), min(ht, MAX_HALF_LENGTH)
    else:
        warnings.append(f"The body is {fmt(2 * half_l)} tiles long: its hull is its own length, longer than the "
                        f"jeep's three tiles, so it needs more room to turn.")
    hull = (hl, hb, hr, ht)

    seat_yaml = ""
    hip = getattr(g, "RIDER_HIP_ROW", {"S": 19, "N": 20, "E": 18, "W": 18})
    half = max(-hull[0], hull[2]) + 0.5
    shield_f = min((k[0] for n in shields for k in vox_of.get(n, [])), default=None)
    sheltered = front_row(seats, shield_f) if shield_f is not None else set()
    for s in seats:
        x, y = -s["r"] / TILE, -s["f"] / TILE
        px = ""
        for v in X.DIRS:
            sx, sy = g.project(v, s["f"], s["r"], s["z"])
            cy = sy + gen.dy[v] - (hip[v] - 16)
            px += f"        {DIRECTION[v]}: {round(sx)},{round(-cy)}\n"
        clips = "".join(f"        {DIRECTION[v]}: {hip[v] - 16}\n" for v in X.DIRS)
        door = s.get("door")
        door_yaml = ""
        if door in by_name and role.get(by_name[door]) == "panel":
            # Facing south its left side is east.
            door_yaml = f"      door: {ids[by_name[door]]}\n      doorSide: {'East' if x > 0 else 'West'}\n"
        behind = s.get("windshield") if s.get("windshield") is not None else s["id"] in sheltered
        seat_yaml += (f"    - id: {s['id']}\n      name: {SEAT_NAMES.get(s['id'], 'cmu-vehicle-seat-passenger')}\n"
                      f"      offset: {fmt(x)},{fmt(y)}\n      exit: {fmt(half if x > 0 else -half)},{fmt(y)}\n"
                      + door_yaml + ("      windshield: true\n" if behind and shields else "")
                      + f"      pixelOffsets:\n{px}      clips:\n{clips}"
                      + ("      driver: true\n" if s.get("driver") else ""))

    # Click bounds: everything the vehicle draws, per direction, in tiles from its centre.
    bounds = ""
    for v, word in (("S", "south"), ("N", "north"), ("E", "east"), ("W", "west")):
        pts = set(states["base"]["frames"][v][0]) | set(states["overlay"]["frames"][v][0]) | set(states["wheels_0"]["frames"][v][0])
        xs = [frame_point(p)[0] for p in pts]
        ys = [frame_point(p)[1] for p in pts]
        bounds += f"      {word}: {fmt((min(xs) - 0.5) / 32)},{fmt((min(ys) - 0.5) / 32)},{fmt((max(xs) + 0.5) / 32)},{fmt((max(ys) + 0.5) / 32)}\n"

    # The engine bay where its engine sits; the front, the forward sixth of the body; the tub, where
    # the inside paint is.
    engine_vox = [k for n in engines for k in vox_of.get(n, [])]
    bay = bounds_of(engine_vox) if engine_vox else (hull[0], hull[1], hull[2], hull[1] + 0.5)
    bay_centre = ((bay[0] + bay[2]) / 2, (bay[1] + bay[3]) / 2)
    max_f = max(k[0] for k in body)
    min_f = min(k[0] for k in body)
    front_voxel = max_f - max(4, round((max_f - min_f) / 6))
    tub = [k for k, (m, _) in models["S"].items() if m in INTERIOR_MATS and owners["S"].get(k) in base_layers]
    interior = ""
    if tub:
        lo = [min(k[a] for k in tub) for a in range(3)]
        hi = [max(k[a] for k in tub) for a in range(3)]
        interior = f"    interiorMin: {lo[0]},{lo[1]},{lo[2]}\n    interiorMax: {hi[0]},{hi[1]},{hi[2]}\n"

    # Damage states by type: a layer per part and state, over the part, which the game shows at the
    # level the part has taken of that type (CMUVehicleDamageStates).
    damage_layers = {}      # part layer key -> [(layer key, first state, overlay state or None)]
    damage_yaml = ""
    plain_parts = set(ids[n] for n in engines + shields)
    plain_damage = []
    if damage_by_type:
        for n in base_layers + panels + engines + kits + lamps + shields:
            for s in X.damage_states(project, n):
                pid = ids[n]
                key = f"{pid}_dmg_{s['slug']}"
                levels = [f for f, _ in s["levels"]]
                over = f"{key}_overlay_{levels[0]}"
                has_over = any(f for lvl in levels for v in X.DIRS
                               for f in states[f"{key}_overlay_{lvl}"]["frames"][v])
                at = "base" if role[n] == "base" else pid
                damage_layers.setdefault(at, []).append((key, f"{key}_{levels[0]}", over if has_over else None))
                if pid in plain_parts:
                    plain_damage.append(key)
                if role[n] == "engine":
                    source = "      source: Engine\n"
                elif role[n] == "lamp":
                    source = f"      source: Slot\n      slot: vehicle-{pid.replace('_', '-')}\n"
                elif role[n] == "windshield":
                    source = f"      source: Slot\n      slot: {WINDSHIELD_SLOT}\n"
                else:
                    source = ""
                rest = {"panel": [f"{pid}_closed"], "windshield": [f"{pid}_up_"],
                        "kit": [f"{pid}_0", f"{pid}_1"] + ([f"{p}{pid}_0" for p in X.TYRE_PREFIXES] if X.wheel_kit(layers[n]) else [])
                        }.get(role[n])
                damage_yaml += (f"    - layer: {key}\n      part: {at}\n"
                                + ("      overlayPart: body\n" if at == "base" else "")
                                + (f"      restStates: {flow(rest)}\n" if rest else "")
                                + source + f"      damageType: {s['type'] or 'Blunt'}\n      levels: {flow(str(f) for f in levels)}\n")
        if damage_yaml:
            damage_yaml = "  - type: CMUVehicleDamageStates\n    stages:\n" + damage_yaml
    elif any(X.damage_states(project, n) for n in panels):
        warnings.append("The panels' damage states are drawn but not shown: this game has no damage states by type "
                        "(CMUVehicleDamageStates); the body's, lamps', engine's, windshield's and kit's worst ones are "
                        "their broken looks.")

    def damage_of(at, overlay=False):
        out = ""
        for key, first, over in damage_layers.get(at, []):
            if overlay and over is None:
                continue
            out += f"    - map: [ {key} ]\n      state: {over if overlay else first}\n      visible: false\n"
        return out

    panel_ids = [ids[n] for n in panels]
    lamp_ids = [ids[n] for n in lamps]
    kit_ids = [ids[k] for k in kits if not X.wheel_kit(layers[k])]
    wheel_kit_ids = {ids[k]: kit_slots[k] for k in kits if X.wheel_kit(layers[k])}
    shield_ids = [ids[n] for n in shields]

    def kits_on(host):
        return [k for k in kits if hosts.get(k) == host]

    sprite_layers = ("    - map: [ base ]\n      state: base\n"
                     "    - map: [ front_dent ]\n      state: front_dent_1\n      visible: false\n"
                     "    - map: [ damaged_frame ]\n      state: damaged_frame\n      visible: false\n") + damage_of("base")
    for n in engines:
        sprite_layers += f"    - map: [ {ids[n]} ]\n      state: {ids[n]}_0\n" + damage_of(ids[n])
    # Kit on no panel goes under the panels, which may swing open in front of it; kit on a panel
    # over it.
    for k in kits_on(None):
        sprite_layers += f"    - map: [ {ids[k]} ]\n      state: {ids[k]}_0\n" + damage_of(ids[k])
    for n in panels:
        sprite_layers += f"    - map: [ {ids[n]} ]\n      state: {ids[n]}_closed\n" + damage_of(ids[n])
        for k in kits_on(n):
            sprite_layers += f"    - map: [ {ids[k]} ]\n      state: {ids[k]}_0\n" + damage_of(ids[k])
    for pid in lamp_ids:
        sprite_layers += f"    - map: [ {pid} ]\n      state: {pid}_0\n" + damage_of(pid)
    for pid in shield_ids:
        sprite_layers += f"    - map: [ {pid} ]\n      state: {pid}_up_0\n" + damage_of(pid)
    sprite_layers += "    - map: [ rmc-wheels ]\n      state: wheels_1\n"
    if rims:
        sprite_layers += "    - map: [ rims ]\n      state: rims_wheels_0\n      visible: false\n"
    overlay_layers = ("    - map: [ body ]\n      state: overlay\n"
                      "    - map: [ front_dent ]\n      state: front_dent_overlay_1\n      visible: false\n"
                      "    - map: [ damaged_frame ]\n      state: damaged_overlay\n      visible: false\n") + damage_of("base", True)
    for k in kits_on(None):
        overlay_layers += f"    - map: [ {ids[k]} ]\n      state: {ids[k]}_overlay_0\n" + damage_of(ids[k], True)
    for n in panels:
        overlay_layers += f"    - map: [ {ids[n]} ]\n      state: {ids[n]}_closed_overlay\n" + damage_of(ids[n], True)
        for k in kits_on(n):
            overlay_layers += f"    - map: [ {ids[k]} ]\n      state: {ids[k]}_overlay_0\n" + damage_of(ids[k], True)
    for pid in lamp_ids:
        overlay_layers += f"    - map: [ {pid} ]\n      state: {pid}_overlay_0\n" + damage_of(pid, True)
    # Shut, the hood goes under the windshield, which folds down onto it; raised, the lid stands in
    # front of it, drawn from its raised layer.
    for pid in shield_ids:
        overlay_layers += f"    - map: [ {pid} ]\n      state: {pid}_up_overlay_0\n" + damage_of(pid, True)
    if raised:
        overlay_layers += f"    - map: [ {raised} ]\n      state: {hood}_open_overlay\n      visible: false\n"
    overlay_layers += "    - map: [ wheels ]\n      state: wheels_overlay_0\n"
    if rims:
        overlay_layers += "    - map: [ rims ]\n      state: rims_wheels_overlay_0\n      visible: false\n"
    for n in lamps:
        pid = ids[n]
        for suffix in {"head": ("on",), "tail": ("on", "brake", "signal"), "signal": ("signal",)}[X.lamp_role(layers[n])]:
            overlay_layers += f"    - map: [ {pid}_{suffix} ]\n      state: {pid}_{suffix}\n      shader: unshaded\n      visible: false\n"
    if "engine_smoke_0" in effects:
        overlay_layers += "    - map: [ smoke ]\n      state: engine_smoke_0\n      visible: false\n"
    if "engine_fire" in effects:
        overlay_layers += "    - map: [ fire ]\n      state: engine_fire\n      shader: unshaded\n      visible: false\n"
    overlay_layers += "    - map: [ hover ]\n      state: wheels_outline\n      visible: false\n"

    if wheel_maps:
        wheels_paint = ("    wheelLayers:\n      rmc-wheels: wheel-1\n" + "".join(f"      {p}: {s}\n" for p, s in wheel_kit_ids.items())
                        + "    wheelOverlayLayers:\n      wheels: wheel-1\n" + "".join(f"      {p}: {s}\n" for p, s in wheel_kit_ids.items()))
        if rims:
            wheels_paint += "    rimLayers:\n      rims: rmc-wheels\n    rimOverlayLayers:\n      rims: wheels\n"
    else:
        wheels_paint = (f"    wheelLayers: {flow(['rmc-wheels'] + list(wheel_kit_ids))}\n"
                        f"    wheelOverlayLayers: {flow(['wheels'] + list(wheel_kit_ids))}\n")
    front = f"  - type: CMUVehicleFrontDamage\n    frontVoxel: {front_voxel}\n"
    if hood:
        front += f"    closedHoodState: {hood}_closed_dent\n" + (f"    closedPanel: {hood}\n" if hood != "hood" else "")

    def fixture(mask, layer):
        return (f"        shape:\n          !type:PhysShapeAabb\n          bounds: {box_text(hull)}\n        density: 800\n"
                f"        mask:\n" + "".join(f"        - {m}\n" for m in mask)
                + "        layer:\n" + "".join(f"        - {m}\n" for m in layer))

    painted_damage = [key for at in damage_layers for key, _, _ in damage_layers[at] if key not in plain_damage]
    painted_damage_over = [key for at in damage_layers for key, _, over in damage_layers[at]
                           if over is not None and key not in plain_damage]
    plain_over_damage = [key for at in damage_layers for key, _, over in damage_layers[at]
                         if over is not None and key in plain_damage]
    paint = (f"  - type: CMUVehiclePaint\n"
             f"    layers: {flow(['base', 'front_dent', 'damaged_frame'] + panel_ids + lamp_ids + kit_ids + painted_damage)}\n"
             f"    overlayLayers: {flow(['body', 'front_dent', 'damaged_frame'] + panel_ids + ([raised] if raised else []) + lamp_ids + kit_ids + painted_damage_over)}\n"
             f"    plainLayers: {flow([ids[n] for n in engines] + shield_ids + plain_damage)}\n")
    if shield_ids or plain_over_damage:
        paint += f"    plainOverlayLayers: {flow(shield_ids + plain_over_damage)}\n"

    containers = "".join(f"      {slot}: !type:ContainerSlot\n" for slot, *_ in slots)
    item_slots = ""
    for slot, label, item, tag, eject in slots:
        item_slots += (f"      {slot}:\n        name: {label}\n        startingItem: {item}\n"
                       + ("" if eject else "        disableEject: true\n        swap: false\n")
                       + f"        whitelist:\n          tags:\n          - {tag}\n")

    name_text = veh.get("description") or f"A {name} drawn in CMU Open Vehicles."
    yml = f"""# Written by CMU Open Vehicles from {os.path.basename(veh.get('source', slug + '.cmuov'))}.

- type: entity
  parent: CMUOpenVehicleBase
  id: CMUVehicle{ident}
  name: {text(name)}
  description: {text(name_text)}
  components:
{tuning(stats)}{driving(sounds)}  - type: CMUVehicleSeats
    seats:
{seat_yaml}  - type: CMUOpenVehicle
    hullBounds: {box_text(hull)}
    engineBay: {box_text(bay)}
    parts:
{parts}{windshield_yaml}  - type: CMUVehicleLamps
    lamps:
{lamp_yaml}  - type: Clickable
    bounds:
{bounds}  - type: CMUVehicleCrayon
    map: /Textures/{where['tex']}/crayon.png
    panels: {flow(panel_ids)}
{paint}{interior}{wheels_paint}{front}{damage_yaml}  - type: CMUVehicleHeadlights
    switchSound:
      path: /Audio/Machines/lightswitch.ogg
      params:
        volume: -4
  - type: Fixtures
    fixtures:
      world:
{fixture(['Impassable', 'HighImpassable', 'LowImpassable', 'LargeMobLayer'], ['LargeMobLayer'])}      mob:
{fixture(['MidImpassable', 'BarricadeImpassable', 'LargeMobLayer'], ['LargeMobLayer'])}  - type: Sprite
    drawdepth: BelowMobs
    sprite: {where['tex']}/{slug}.rsi
    layers:
{sprite_layers}  - type: CMUVehicleOverlay
    prototype: CMUVehicle{ident}Overlay
  - type: ContainerContainer
    containers:
      wheel-1: !type:ContainerSlot
        ent: null
        showEnts: False
        occludes: True
{containers}      jeep-ignition: !type:ContainerSlot
  - type: ItemSlots
    slots:
      wheel-1:
        startingItem: CMUJeepWheel
        disableEject: true
        whitelist:
          components:
          - VehicleWheelItem
{item_slots}  - type: HardpointSlots
    vehicleFamily: CMUJeep
    slots:
    - id: wheel-1
      disableEject: true
      hardpointType: Wheel
      slotType: Wheel
      insertDelay: 1.5
  - type: VehicleWheelSlots
    slotCount: 1
    slots:
    - wheel-1
  - type: CMUVehicleWreck
    wreckPrototype: CMUVehicle{ident}Wreck
    engineBay: {fmt(bay_centre[0])},{fmt(bay_centre[1])}

- type: entity
  id: CMUVehicle{ident}Overlay
  categories: [ HideSpawnMenu ]
  components:
  - type: Sprite
    drawdepth: Mobs
    sprite: {where['tex']}/{slug}.rsi
    layers:
{overlay_layers}  - type: CMUVehicleOverlayVisuals
    signalSound:
      path: {sounds['blinker']}
      params:
        volume: -6
        maxDistance: 4
    directionOffsets:
      South: 0,0
      North: 0,0
      East: 0,0
      West: 0,0

- type: entity
  parent: CMUJeepWreckBase
  id: CMUVehicle{ident}Wreck
  categories: [ HideSpawnMenu ]
  name: {text('burnt-out ' + name)}
  description: {text(f'The blackened shell of a {name}. Nothing on it is worth saving.')}
  components:
  - type: Sprite
    sprite: {where['tex']}/{slug}.rsi
    state: wreck
"""
    os.makedirs(os.path.dirname(where["proto"]), exist_ok=True)
    with open(where["proto"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write(yml)
    os.makedirs(os.path.dirname(where["ftl"]), exist_ok=True)
    with open(where["ftl"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(f"{k} = {fluent(v)}\n" for k, v in strings.items()))
    return {"id": f"CMUVehicle{ident}", "rsi": where["rsi"], "crayon": where["crayon"], "prototypes": where["proto"],
            "strings": where["ftl"], "states": len(states), "effects": effects,
            "hull": box_text(hull), "corridor": corridor, "damageStates": bool(damage_yaml),
            "warnings": warnings,
            "files": [where["proto"], where["ftl"], where["rsi"], where["crayon"]]}
