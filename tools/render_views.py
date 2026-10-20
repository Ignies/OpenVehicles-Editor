"""Renders a project's four directions as the preview does and saves them, one PNG per direction, so
two versions of the tool can be compared pixel for pixel, or a camera looked at.

    python tools/render_views.py examples/roadster/roadster.cmuov out/ [--camera '{"mode": "single", "view": "E"}']
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "backend"))

import project as P  # noqa: E402
import voxels as V  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project")
    parser.add_argument("out")
    parser.add_argument("--camera", help="a camera block (camera.py), as JSON")
    parser.add_argument("--gamedata", default=os.path.join(HERE, "gamedata"))
    args = parser.parse_args()
    gen = V.Generator(os.path.join(args.gamedata, "Content.CMU", "Design", "Vehicles", "JeepPixelArt", "jeep_pixel_art.py"))
    p = P.load(args.project)
    P.remap(p, gen.materials)
    if args.camera:
        p["camera"] = json.loads(args.camera)
    box = V.Box.from_dict(p["box"])
    os.makedirs(args.out, exist_ok=True)
    dirs = list(gen.module.DIRS)
    with V.styled(gen, p, box):
        models, owners, _, _ = V.build(p, box, gen.materials, dirs, gen=gen)
        for view in dirs:
            img, _ = V.render_sprite(gen, view, models[view], owners[view])
            img.save(os.path.join(args.out, f"{view}.png"))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
