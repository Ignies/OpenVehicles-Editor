"""Writes every template into a scratch folder through the game export, as the web tool does, and
reports what each wrote or why it couldn't: every template should go into the game as it comes.

    python tools/check_templates.py [--gamedata gamedata]
"""

import argparse
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "backend"))

import export_game as G  # noqa: E402
import project as P  # noqa: E402
import templates as T  # noqa: E402
import voxels as V  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gamedata", default=os.path.join(HERE, "gamedata"))
    parser.add_argument("names", nargs="*", help="templates to check (default: all)")
    args = parser.parse_args()
    gen = V.Generator(os.path.join(args.gamedata, "Content.CMU", "Design", "Vehicles", "JeepPixelArt", "jeep_pixel_art.py"))
    names = args.names or [t if isinstance(t, str) else t["name"] for t in T.listing()]
    failed = 0
    for name in names:
        p = T.make(name, None)
        P.remap(p, gen.materials)
        data = {"box": p["box"], "materials": gen.materials, "layers": p["layers"], "vehicle": p.get("vehicle") or {}}
        started = time.time()
        with tempfile.TemporaryDirectory() as out:
            try:
                r = G.write(gen, data, args.gamedata, out=out)
                print(f"ok   {name}: {r['id']}, {r['states']} states, {time.time() - started:.0f}s")
                for w in r["warnings"]:
                    print(f"     warning: {w}")
            except Exception as exc:  # noqa: BLE001 - report every template
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
