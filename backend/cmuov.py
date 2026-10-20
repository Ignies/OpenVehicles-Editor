"""CMU Open Vehicles from the command line: build and change a vehicle without the app, by editing
its orthographic views and parameters, and render previews to look at. Made for scripts and for
language models: every command reads and writes a .cmuov project file (the app's own format, so the
app shows the changes live when it has the file open) and prints JSON, except `show`, which prints a
view as text.

    python cmuov.py new jeep.cmuov --from cargo        # a project from a generator vehicle
    python cmuov.py new truck.cmuov --box 80 50 40     # a blank project with a body layer
    python cmuov.py info truck.cmuov                   # layers, box, stages
    python cmuov.py materials                          # the palette
    python cmuov.py show truck.cmuov Body side --depth # a view as text, with its depths
    python cmuov.py draw truck.cmuov Body side '[{"op": "rect", "from": [4, 10], "to": [70, 30], "mat": "od"}]'
    python cmuov.py layer truck.cmuov add door --name "Driver door"
    python cmuov.py layer truck.cmuov set "Driver door" sides=left thickness=1
    python cmuov.py stage truck.cmuov "Driver door" add "Bullet holes" --type Piercing --from 20
    python cmuov.py stage truck.cmuov "Driver door" add "Bullet holes" --from 40   # its next level
    python cmuov.py stage truck.cmuov "Driver door" set "Bullet holes" name=Shot type=Piercing
    python cmuov.py reshape truck.cmuov 90 50 40 --stretch
    python cmuov.py preview truck.cmuov --out shots --drive E --swing "Driver door"
    python cmuov.py layer truck.cmuov bake Body                     # make it a voxel layer
    python cmuov.py show truck.cmuov Body side --slice 6            # its cross-section 6 deep
    python cmuov.py vehicle truck.cmuov id=Truck name=truck "description=A light truck."
    python cmuov.py seat truck.cmuov add driver -6.5 -8.5 8 --driver --door "Driver door"
    python cmuov.py game truck.cmuov --repo C:/cmu/jeeps          # write it into the game

Draw operations (a JSON list, or @file.json): each has "op" and usually "mat" (a material name, or
"empty" to erase) and "depth" (how far in from that view's side the surface sits) or "add" (depth
relative to what's there). Shapes: pixels {"points": [[x, y], ...]}, line {"from", "to"} or
{"points"}, rect {"from", "to", "fill"}, ellipse {"center", "radius": r or [rx, ry], "fill"},
polygon {"points", "fill"}, fill {"at"} (flood, like the app's bucket), text {"at", "rows": ["..##",
...], "legend": {"#": "od"}, "depths": ["..33", ...] (base 36)}, depth {"shape": rect..., "depth"}
(depth only, keeps materials), clear, shift {"by": [dx, dy]}. Coordinates are view pixels (column,
row) from the top left, or with "space": "vehicle" the generator's own: side (f, z), front and back
(r, z), left (f, z), top (f, r). "mirror": true mirrors across the centre line like the app's mirror.

Voxel layers (made by `layer FILE bake LAYER`, from a drawn layer or one loaded from the generator)
keep their voxels themselves, so they hold what views can't: a cockpit behind a door, a dash under a
windshield, a door's inner skin. Their views show what's first along each pixel's line of sight, and
what's drawn on a view is applied along that line, as on a layer loaded from the generator: erasing
cuts through ("erase": "surface" takes off only the first voxel), a deeper depth dents in, a nearer
one builds out, the surface hit takes the material, and drawing where there was nothing builds as
deep as the voxels beside it. On them, ops also take "extrude": [d0, d1] (fill each pixel's line
between those depths instead), "extrude": n (n voxels in from the pixel's depth), "through": true
(from its depth to the far side), "slice": d (only the voxel d deep: set it, or "empty" clears it),
and "mirror" on side or left does the same on the other side. Clear empties the layer, shift moves
its voxels. Two solids, in vehicle space (the view is ignored): {"op": "box", "from": [f, r, z],
"to": [f, r, z], "mat"} and {"op": "cylinder", "axis": "f" | "r" | "z", "center": [the other two
coordinates, in that order], "radius": n, "from": a, "to": b, "mat"} (a voxel is in when its centre
is, so a centre on a whole number lies between voxels); "mat": "empty" cuts them out and "mirror":
true adds their reflection across the centre (r to -r-1).

Views: side (from the right, nose right), left (from the left, nose left), front (from ahead), top
(from above, nose right), back (from behind). A layer on both sides takes its right from side and
its left from left; with left blank, its left mirrors its right.
"""

import argparse
import contextlib
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import project as P  # noqa: E402
import voxels as V  # noqa: E402

DEFAULT_GENERATOR = r"C:\cmu\jeeps\Content.CMU\Design\Vehicles\JeepPixelArt\jeep_pixel_art.py"


def generator_path(arg):
    if arg:
        return arg
    if os.environ.get("CMUOV_GENERATOR"):
        return os.environ["CMUOV_GENERATOR"]
    settings = os.path.join(os.environ.get("APPDATA", ""), "CMU Open Vehicles", "settings.json")
    try:
        with open(settings, encoding="utf-8") as fh:
            return json.load(fh).get("generator") or DEFAULT_GENERATOR
    except (OSError, ValueError):
        return DEFAULT_GENERATOR


_GEN = None


def gen(args):
    global _GEN
    if _GEN is None:
        _GEN = V.Generator(generator_path(args.generator))
    return _GEN


def out(obj):
    print(json.dumps(obj, indent=1))


def load(args):
    """A project, with its layers loaded from the generator brought up to per direction drawings
    when it's from before them (saved so by whichever command saves it)."""
    project = P.load(args.file)
    if project.get("customMaterials"):
        gen(args).add_materials(project["customMaterials"])
    if project.get("version", 1) < P.VERSION and any(l.get("native") for l in project["layers"]):
        V.refresh_native(gen(args), project["layers"], V.Box.from_dict(project["box"]), project["materials"])
    project["version"] = P.VERSION
    return project


def summary(project):
    box = V.Box.from_dict(project["box"])
    layers = []
    for n, lay in enumerate(project["layers"]):
        drawn = {}
        for view in V.VIEWS:
            m, _ = P.grid(lay, box, view)
            drawn[view] = int((m != V.EMPTY).sum())
        entry = {"index": n, "name": lay["name"], "kind": lay["kind"], "sides": lay["sides"],
                 "thickness": lay["thickness"], "visible": lay.get("visible", True), "pixels": drawn}
        for key in ("role", "rides", "stage"):
            if lay.get(key):
                entry[key] = lay[key]
        if lay.get("native"):
            entry["model"] = "generator, with what's drawn applied"
        g = V.layer_voxels(lay, box)
        if g is not None:
            entry["model"] = "voxels"
            entry["voxels"] = int((g != V.EMPTY).sum())
        layers.append(entry)
    return {"box": project["box"], "views": {v: dict(zip(("rows", "cols"), box.shape(v))) for v in V.VIEWS},
            "layers": layers}


# -- commands -------------------------------------------------------------------------------------

def cmd_new(args):
    if os.path.exists(args.file) and not args.force:
        raise SystemExit(f"{args.file} exists; --force to replace it")
    g = gen(args)
    if args.template:
        import templates as T
        project = T.make(args.template, tuple(args.box) if args.box else None)
        P.remap(project, g.materials)
        P.save(project, args.file)
        out(summary(project))
        return
    box = V.Box(*args.box) if args.box else V.Box(72, 48, 36)
    project = P.new(box.as_dict(), g.materials)
    if args.source:
        for spec in g.layers(args.source):
            lay = {k: spec[k] for k in ("name", "kind", "sides", "thickness", "role", "rides", "native")}
            lay.update(visible=True, stage=None, views=V.native_views(g, spec, box, g.materials))
            project["layers"].append(lay)
    else:
        project["layers"].append(P.blank_layer(box, "body", "Body"))
    P.save(project, args.file)
    out(summary(project))


def cmd_info(args):
    out(summary(load(args)))


def cmd_materials(args):
    g = gen(args)
    out([{"name": m["name"], "top": m["tones"]["top"], "front": m["tones"]["front"]} for m in g.palette()])


def cmd_show(args):
    project = load(args)
    print(P.show(project, P.layer(project, args.layer), args.view, args.depth, args.slice))


def cmd_draw(args):
    project = load(args)
    text = args.ops
    if text.startswith("@"):
        with open(text[1:], encoding="utf-8") as fh:
            text = fh.read()
    ops = json.loads(text)
    if isinstance(ops, dict):
        ops = [ops]
    lay = P.layer(project, args.layer)
    changed = P.draw(project, lay, args.view, ops, args.mirror)
    P.save(project, args.file)
    out({"layer": lay["name"], "view": args.view, "changed": changed})


def cmd_layer(args):
    project = load(args)
    box = V.Box.from_dict(project["box"])
    layers = project["layers"]
    if args.action == "add":
        kind = args.target
        if kind not in P.KINDS:
            raise SystemExit(f"Kind is one of {', '.join(P.KINDS)}")
        lay = P.blank_layer(box, kind, args.name)
        layers.append(lay)
    elif args.action == "remove":
        lay = P.layer(project, args.target)
        gone = [lay] + P.stages(project, lay)
        project["layers"] = [l for l in layers if l not in gone]
    elif args.action == "copy":
        lay = json.loads(json.dumps(P.layer(project, args.target)))
        lay["name"] = args.name or f"{lay['name']} copy"
        lay["stage"] = None
        layers.append(lay)
    elif args.action == "move":
        lay = P.layer(project, args.target)
        layers.remove(lay)
        layers.insert(max(0, min(len(layers), int(args.values[0]))), lay)
    elif args.action == "detach":
        lay = P.layer(project, args.target)
        lay["native"] = None
    elif args.action == "bake":
        lay = P.layer(project, args.target)
        if lay.get("voxels"):
            raise SystemExit(f"{lay['name']} is a voxel layer already")
        P.bake_layer(project, lay, gen(args) if lay.get("native") else None)
    elif args.action == "set":
        lay = P.layer(project, args.target)
        for pair in args.values:
            key, _, value = pair.partition("=")
            if key == "name":
                P.rename(project, lay, value)
            elif key in ("kind", "sides", "rides", "role"):
                lay[key] = value or None
                if key == "sides":
                    lay["native"] = None
            elif key == "thickness":
                lay[key] = int(value)
                lay["native"] = None
            elif key == "visible":
                lay[key] = value.lower() in ("1", "true", "yes", "on")
            else:
                raise SystemExit(f"Can't set {key}: name, kind, sides, thickness, rides, role or visible")
    else:
        raise SystemExit("layer add | remove | copy | move | detach | bake | set")
    P.save(project, args.file)
    out(summary(project))


def cmd_stage(args):
    project = load(args)
    lay = P.layer(project, args.layer)
    def listing():
        return [{"state": s["name"], "type": s["type"], "levels": {f: st["name"] for f, st in s["levels"]}}
                for s in P.states(project, lay)]

    if args.action == "add":
        if not args.name:
            raise SystemExit("stage FILE LAYER add STATE [--type T] [--from N]")
        st = P.add_stage(project, lay, args.name, args.type, args.amount, blank=args.blank, link=args.link)
        result = {"added": st["name"], "of": lay["name"], **st["stage"]}
    elif args.action == "list":
        result = listing()
    elif args.action == "set" and lay.get("stage"):
        # A level itself: its level, or (for its whole state) type and state.
        for pair in ([args.name] if args.name else []) + args.values:
            key, _, value = pair.partition("=")
            if key == "from":
                lay["stage"]["from"] = int(value)
                P.name_level(project, lay)
            else:
                P.set_state(project, P.layer(project, lay["stage"]["of"]), P.state_name(lay),
                            name=value if key in ("state", "name") else None, damage_type=value if key == "type" else None)
        result = lay["stage"]
    elif args.action == "set":
        values = dict(pair.partition("=")[::2] for pair in args.values)
        P.set_state(project, lay, args.name, name=values.get("name"), damage_type=values.get("type"))
        result = listing()
    elif args.action == "remove":
        own = next((s for s in P.states(project, lay) if s["name"] == args.name), None)
        if own is None:
            raise SystemExit(f"{lay['name']} has no damage state {args.name}")
        gone = [st for f, st in own["levels"] if args.levels is None or f in args.levels]
        project["layers"] = [l for l in project["layers"] if all(l is not g for g in gone)]
        result = listing()
    elif args.action == "unlink":
        lay["stage"] = None
        result = {"unlinked": lay["name"]}
    else:
        raise SystemExit("stage add | list | set | remove | unlink")
    P.save(project, args.file)
    out(result)


def cmd_reshape(args):
    project = load(args)
    P.reshape(project, args.size, "stretch" if args.stretch else "keep", gen(args))
    P.save(project, args.file)
    out(summary(project))


def cmd_types(args):
    g = gen(args)
    repo = getattr(g.module, "REPO", None) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(g.path))))
    out(P.damage_types(repo))


GROUNDS = {"dark": (14, 16, 18), "grass": (61, 82, 48), "sand": (157, 138, 98), "concrete": (107, 110, 112)}


def cmd_vehicle(args):
    project = load(args)
    veh = project.setdefault("vehicle", {})
    for pair in args.values:
        key, _, value = pair.partition("=")
        if key not in ("id", "name", "description"):
            raise ValueError(f"{key}: a vehicle has an id, a name and a description")
        veh[key] = value
    P.save(project, args.file)
    out(veh)


def cmd_seat(args):
    project = load(args)
    seats = project.setdefault("vehicle", {}).setdefault("seats", [])
    seats[:] = [s for s in seats if s["id"] != args.id]
    if args.action == "add":
        if args.where is None or len(args.where) != 3:
            raise ValueError("a seat needs its f r z")
        seat = {"id": args.id, "f": args.where[0], "r": args.where[1], "z": args.where[2]}
        if args.driver:
            seat["driver"] = True
        if args.door:
            P.layer(project, args.door)
            seat["door"] = args.door
        seats.append(seat)
    P.save(project, args.file)
    out(seats)


def cmd_game(args):
    import export_game as G
    project = load(args)
    project.setdefault("vehicle", {}).setdefault("source", os.path.basename(args.file))
    repo = args.repo or os.path.abspath(os.path.join(os.path.dirname(gen(args).path), "..", "..", "..", ".."))
    if not os.path.isdir(os.path.join(repo, "Content.CMU", "Resources")):
        raise ValueError(f"{repo} isn't a CMU repo (no Content.CMU/Resources)")
    with contextlib.redirect_stdout(sys.stderr):
        result = G.write(gen(args), project, repo, lambda text: print(text, file=sys.stderr))
        if not args.no_3d:
            import export_3d as E3
            r3 = E3.after_game(gen(args), project, repo, result, progress=lambda text: print(text, file=sys.stderr))
            result["model3d"] = r3["path"]
            result["files"].append(r3["path"])
    out(result)


def cmd_preview(args):
    """Renders the vehicle's directions (and optionally it driving and a part swinging) to PNGs and
    GIFs, scaled up on a ground colour, and prints where they went."""
    g = gen(args)
    project = load(args)
    P.remap(project, g.materials)
    show = "all" if args.damage == "all" else None
    if args.stage:
        show = dict(pair.split("=", 1) for pair in args.stage)
    data = P.for_render(project, show)
    box = V.Box.from_dict(project["box"])
    # A vehicle drawn here renders with its own chassis: dirt by its wheels, no baked mud.
    styled = contextlib.ExitStack()
    styled.enter_context(V.styled(g, data, box))
    paint = None
    if args.paint or args.dirt:
        paint = {k: None for k in ("body", "interior", "seats")}
        for pair in args.paint or []:
            key, _, value = pair.partition("=")
            value = value.lstrip("#")
            paint[key] = tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        paint["dirt"] = args.dirt or 0.0
    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, args.name or os.path.splitext(os.path.basename(args.file))[0])
    ground = GROUNDS.get(args.ground, GROUNDS["dark"])
    scale = args.scale
    files = {}

    def framed(img):
        bg = Image.new("RGBA", img.size, ground + (255,))
        bg.alpha_composite(img)
        return bg.resize((img.width * scale, img.height * scale), Image.NEAREST)

    dirs = [d for d in args.dirs if d in "SNEW"]
    models, owners, _, count = V.build(data, box, g.materials, dirs, gen=g)
    tiles = []
    for view in dirs:
        img, _ = V.render_sprite(g, view, models[view], owners[view], paint)
        path = f"{base}_{view}.png"
        framed(img).save(path)
        files[view] = path
        tiles.append(framed(img))
    sheet = Image.new("RGBA", (sum(t.width for t in tiles), tiles[0].height), ground + (255,))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width
    sheet.save(f"{base}_sheet.png")
    files["sheet"] = f"{base}_sheet.png"

    def animate(frames, name, ms):
        imgs = [framed(f) for f in frames]
        strip = Image.new("RGBA", (sum(i.width for i in imgs), imgs[0].height), ground + (255,))
        x = 0
        for i in imgs:
            strip.paste(i, (x, 0))
            x += i.width
        strip.save(f"{base}_{name}.png")
        imgs[0].save(f"{base}_{name}.gif", save_all=True, append_images=imgs[1:], duration=ms, loop=0, disposal=2)
        files[name] = f"{base}_{name}.png"
        files[name + "_gif"] = f"{base}_{name}.gif"

    if args.drive:
        frames = [V.render_sprite(g, args.drive, vox, owner, paint, phase)[0]
                  for vox, owner, phase in V.drive_scenes(g, data, box, g.materials, args.drive)]
        animate(frames, f"drive_{args.drive}", round(g.wheel_delay * 1000))
    if args.swing:
        n = data["layers"].index(next(l for l in data["layers"] if l["name"].lower() == args.swing.lower()))
        view = args.swing_dir
        scenes = V.swing_scenes(g, data, box, g.materials, n, view)
        if not scenes:
            raise SystemExit(f"{args.swing} doesn't swing (its kind has no hinge)")
        frames = [V.render_sprite(g, view, vox, owner, paint)[0] for vox, owner in scenes]
        animate(frames + frames[::-1][1:-1], f"swing_{view}", 80)
    styled.close()
    out({"voxels": count, "files": files})


def main():
    ap = argparse.ArgumentParser(prog="cmuov", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generator", help="the generator script (default: the app's, or CMUOV_GENERATOR)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="a new project")
    p.add_argument("file")
    p.add_argument("--from", dest="source", help="a generator vehicle to start from (e.g. cargo)")
    p.add_argument("--template", help="a template to start from: car, pickup, van, jeep-like")
    p.add_argument("--box", nargs=3, type=int, metavar=("L", "W", "H"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("info", help="the project's box and layers")
    p.add_argument("file")
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser("materials", help="the generator's palette")
    p.set_defaults(fn=cmd_materials)

    p = sub.add_parser("types", help="the game's damage types and groups")
    p.set_defaults(fn=cmd_types)

    p = sub.add_parser("show", help="a layer's view as text")
    p.add_argument("file")
    p.add_argument("layer")
    p.add_argument("view", choices=V.VIEWS)
    p.add_argument("--depth", action="store_true")
    p.add_argument("--slice", type=int, metavar="D", help="a voxel layer's cross-section D deep from the view's side")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("draw", help="draw operations on a layer's view")
    p.add_argument("file")
    p.add_argument("layer")
    p.add_argument("view", choices=V.VIEWS)
    p.add_argument("ops", help="a JSON list of operations, or @file.json")
    p.add_argument("--mirror", action="store_true", help="mirror every op across the centre line")
    p.set_defaults(fn=cmd_draw)

    p = sub.add_parser("layer", help="add, remove, copy, move, detach, bake (to voxels) or set a layer")
    p.add_argument("file")
    p.add_argument("action", choices=("add", "remove", "copy", "move", "detach", "bake", "set"))
    p.add_argument("target", help="the kind for add, else the layer")
    p.add_argument("values", nargs="*", help="key=value pairs for set, the index for move")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_layer)

    p = sub.add_parser("stage", help="a layer's damage states, each drawn at levels")
    p.add_argument("file")
    p.add_argument("layer", help="the layer, or for set and unlink one of its levels")
    p.add_argument("action", choices=("add", "list", "set", "remove", "unlink"))
    p.add_argument("name", nargs="?", help="the damage state")
    p.add_argument("values", nargs="*", help="key=value pairs for set: name, type (or from, on a level)")
    p.add_argument("--type", default="Blunt", help="a new state's damage type")
    p.add_argument("--from", dest="amount", type=int, default=20, help="the level: how much damage it shows from")
    p.add_argument("--levels", type=int, nargs="*", help="for remove: only these levels")
    p.add_argument("--blank", action="store_true", help="start the level blank, not as a copy of the one below")
    p.add_argument("--link", help="use this existing layer as the level's drawing")
    p.set_defaults(fn=cmd_stage)

    p = sub.add_parser("reshape", help="a new box size")
    p.add_argument("file")
    p.add_argument("size", nargs=3, type=int, metavar=("L", "W", "H"))
    p.add_argument("--stretch", action="store_true", help="scale the drawings with the box")
    p.set_defaults(fn=cmd_reshape)

    p = sub.add_parser("vehicle", help="what the game calls it: id=, name=, description=")
    p.add_argument("file")
    p.add_argument("values", nargs="*", help="key=value pairs")
    p.set_defaults(fn=cmd_vehicle)

    p = sub.add_parser("seat", help="add (or replace) or remove a seat, at f r z in vehicle space")
    p.add_argument("file")
    p.add_argument("action", choices=("add", "remove"))
    p.add_argument("id", help="driver, passenger, or any other id")
    p.add_argument("where", nargs="*", type=float, help="f r z")
    p.add_argument("--driver", action="store_true")
    p.add_argument("--door", help="the door layer its rider gets in by")
    p.set_defaults(fn=cmd_seat)

    p = sub.add_parser("game", help="write it into the game: sprites, crayon map, prototypes, strings")
    p.add_argument("file")
    p.add_argument("--repo", help="the CMU repo (default: the generator's)")
    p.add_argument("--no-3d", action="store_true", help="leave out the CMU3D model")
    p.set_defaults(fn=cmd_game)

    p = sub.add_parser("preview", help="render the sprites, driving and swinging, to files")
    p.add_argument("file")
    p.add_argument("--out", default="preview")
    p.add_argument("--name")
    p.add_argument("--dirs", default="SENW")
    p.add_argument("--scale", type=int, default=3)
    p.add_argument("--ground", choices=tuple(GROUNDS), default="dark")
    p.add_argument("--paint", nargs="*", help="body=#hex interior=#hex seats=#hex")
    p.add_argument("--dirt", type=float, default=0.0)
    p.add_argument("--damage", choices=("none", "all"), default="none", help="show every layer's last stage")
    p.add_argument("--stage", nargs="*", help="layer=stage pairs to show")
    p.add_argument("--drive", choices=tuple("SNEW"), help="also render it driving that way")
    p.add_argument("--swing", help="also render this layer swinging open")
    p.add_argument("--swing-dir", default="E", choices=tuple("SNEW"))
    p.set_defaults(fn=cmd_preview)

    args = ap.parse_args()
    try:
        args.fn(args)
    except (KeyError, ValueError) as exc:
        print(json.dumps({"error": str(exc).strip("'\"")}))
        sys.exit(1)


if __name__ == "__main__":
    main()
