"""The zip the web tool hands out for a vehicle written into the game: every file the game export
wrote, laid out as the repo is so it unzips at the repo's root, the project it came from, a script
that puts its crate on the cargo catalog, and an INSTALL.md that says what to do with it all."""

import json
import os
import zipfile

import project as P

# Edits the repo's catalogs to sell the vehicle: a script, so it edits the files as they are in the
# checkout it runs in rather than patching lines that may have moved.
CATALOG_SCRIPT = '''"""Puts {crate} on the cargo catalog, as the jeeps are: run from the CMU repo's root.

    python add_{slug}_to_catalog.py              # the platoons' ASRS, {cost} points
    python add_{slug}_to_catalog.py --colony     # the colony's ASRS too
    python add_{slug}_to_catalog.py --clf        # and its drop flare in the CLF's shop
"""

import argparse
import os
import sys

REQ = os.path.join("Content.CMU", "Resources", "Prototypes", "CMU14", "Vehicles", "Jeep", "jeep_requisitions.yml")
CLF = os.path.join("Content.CMU", "Resources", "Prototypes", "CMU14", "Economy", "Misc", "clfstuff.yml")


def edit(path, change):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\\n")
    if change(lines):
        with open(path, "w", encoding="utf-8", newline="\\n") as fh:
            fh.write("\\n".join(lines))


def add_crate(block_id, cost):
    """Adds the crate at the end of the catalog entries of the entity block_id."""
    def change(lines):
        start = next((i for i, l in enumerate(lines) if l.strip() == f"id: {{block_id}}"), None)
        if start is None:
            sys.exit(f"{{REQ}} has no {{block_id}}: is this the CMU repo with the jeeps?")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("- type:")), len(lines))
        if any(lines[i].strip() == "crate: {crate}" for i in range(start, end)):
            print(f"{{block_id}}: {crate} is already on it")
            return False
        last = max(i for i in range(start, end) if lines[i].startswith("        crate:"))
        lines[last + 1:last + 1] = [f"      - cost: {{cost}}", "        crate: {crate}"]
        print(f"{{block_id}}: added {crate} for {{cost}}")
        return True
    edit(REQ, change)


def add_flare():
    """Adds the drop flare to the CLF's shop, after the jeep's."""
    def change(lines):
        if any(l.strip() == "- id: {flare}" for l in lines):
            print("The CLF's shop already has {flare}")
            return False
        at = next((i for i, l in enumerate(lines) if l.strip() == "- id: CMUFlareJeepDropCLF"), None)
        if at is None:
            sys.exit(f"{{CLF}} has no CMUFlareJeepDropCLF: is this the CMU repo with the jeeps?")
        indent = lines[at][:len(lines[at]) - len(lines[at].lstrip())]
        end = at + 1
        while end < len(lines) and lines[end].startswith(indent + "  "):
            end += 1
        lines[end:end] = [f"{{indent}}- id: {flare}", f"{{indent}}  amount: 1", f"{{indent}}  points: 250"]
        print("The CLF's shop: added {flare}")
        return True
    edit(CLF, change)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--colony", action="store_true", help="the colony's ASRS too")
    parser.add_argument("--clf", action="store_true", help="its drop flare in the CLF's shop")
    parser.add_argument("--cost", type=int, default={cost})
    args = parser.parse_args()
    add_crate("CMUASRSJeepKits", args.cost)
    if args.colony:
        add_crate("CMUASRSColonyJeepKits", args.cost)
    if args.clf:
        add_flare()


if __name__ == "__main__":
    main()
'''


def catalog_script(result, slug):
    return CATALOG_SCRIPT.format(crate=result["crate"], flare=result["flare"], slug=slug, cost=result["cost"])


def zip_export(out, result, project, zip_path):
    """Zips everything under out (export_game.write(..., out=out)) with the project, the catalog
    script and INSTALL.md; returns the zip's path."""
    veh = project.get("vehicle") or {}
    slug = os.path.splitext(os.path.basename(zip_path))[0]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(out):
            for f in sorted(files):
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, out).replace(os.sep, "/"))
        source = {"format": P.FORMAT, "version": P.VERSION, "active": 0, **project}
        zf.writestr(f"{slug}.cmuov", json.dumps(source))
        zf.writestr(f"add_{slug}_to_catalog.py", catalog_script(result, slug))
        zf.writestr("INSTALL.md", install_text(result, veh, slug, out))
    return zip_path


def single_note(veh):
    """For a vehicle drawn the same at every heading: the crayon's fixedDirection it needs."""
    if (veh.get("camera") or {}).get("mode") not in ("single", "sprite"):
        return ""
    return ("\n\nIt is drawn the same whichever way it faces, so its crayon map has one view: the game's\n"
            "`CMUVehicleCrayon` must know `fixedDirection` (the single view crayon change). Without it\n"
            "the YAML linter rejects the field.")


def install_text(result, veh, slug, out):
    proto = result["id"]
    files = sorted(os.path.relpath(f, out).replace(os.sep, "/") for f in result["files"])
    warnings = "".join(f"- {w}\n" for w in result.get("warnings") or []) or "- None.\n"
    rows = "".join(f"| `{k}` | {v} |\n" for k, v in (result.get("spawnables") or {}).items())
    return f"""# {veh.get('name') or slug}, from CMU Open Vehicles

This zip is laid out like the CMU repo. Unzip it at the repo's root (the folder with
`Content.CMU` in it) and every file lands where the game looks for it.

## What you need first

The game must have the open vehicle systems (the jeeps): `CMUOpenVehicleBase` and the jeep's
parts, keys, crates and manual, which this vehicle builds on.{single_note(veh)}

## What's in it

{"".join(f"- `{f}`" + chr(10) for f in files)}- `add_{slug}_to_catalog.py`: puts its crate on the cargo catalog (see below). Not for the repo.
- `{slug}.cmuov`: the project, to open in the tool again. Not for the repo.
- `INSTALL.md`: this file. Not for the repo.

## Try it

1. Unzip at the repo root and build the game.
2. Start a test server, then spawn as an admin, beside you or from the entity spawn menu:

| Prototype | What it is |
| --- | --- |
| `{proto}` | The vehicle, ready to drive |
{rows}
## Sell it

`python add_{slug}_to_catalog.py` (from the repo root) adds `{result['crate']}` to the platoons'
cargo catalog beside the jeeps, for {result['cost']} points. `--colony` puts it on the colony's
too, `--clf` its drop flare in the CLF's shop, `--cost N` sets the price.

## Before a pull request

- Run the YAML linter (`dotnet run --project Content.YAMLLinter`).
- Read `CONTRIBUTING.md`; the new files are already in the CMU zones. The catalog script edits two
  existing CMU files, which is fine for a PR.
- Attach screenshots of it in game.

## Notes from the export

{warnings}"""
