"""The zip the web tool hands out for a vehicle written into the game: every file the game export
wrote, laid out as the repo is so it unzips at the repo's root, the project it came from, and an
INSTALL.md that says what to do with it."""

import json
import os
import zipfile

import project as P


def zip_export(out, result, project, zip_path):
    """Zips everything under out (export_game.write(..., out=out)) with the project and INSTALL.md;
    returns the zip's path."""
    veh = project.get("vehicle") or {}
    slug = os.path.splitext(os.path.basename(zip_path))[0]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(out):
            for f in sorted(files):
                full = os.path.join(root, f)
                zf.write(full, os.path.relpath(full, out).replace(os.sep, "/"))
        source = {"format": P.FORMAT, "version": P.VERSION, "active": 0, **project}
        zf.writestr(f"{slug}.cmuov", json.dumps(source))
        zf.writestr("INSTALL.md", install_text(result, veh, slug, out))
    return zip_path


def install_text(result, veh, slug, out):
    proto = result["id"]
    files = sorted(os.path.relpath(f, out).replace(os.sep, "/") for f in result["files"])
    warnings = "".join(f"- {w}\n" for w in result.get("warnings") or []) or "- None.\n"
    extra = "".join(f"| `{k}` | {v} |\n" for k, v in (result.get("spawnables") or {}).items())
    return f"""# {veh.get('name') or slug}, from CMU Open Vehicles

This zip is laid out like the CMU repo. Unzip it at the repo's root (the folder with
`Content.CMU` in it) and every file lands where the game looks for it.

## What you need first

The game must have the open vehicle systems (the jeeps): `CMUOpenVehicleBase` and the jeep's
parts, keys and crates, which this vehicle builds on.

## What's in it

{"".join(f"- `{f}`" + chr(10) for f in files)}- `{slug}.cmuov`: the project, to open in the tool again. Not for the repo.
- `INSTALL.md`: this file. Not for the repo.

## Try it

1. Unzip at the repo root and build the game.
2. Start a test server, then spawn it as an admin: `spawn {proto}` beside you, or from the
   entity spawn menu.

| Prototype | What it is |
| --- | --- |
| `{proto}` | The vehicle, ready to drive |
{extra}
## Before a pull request

- Run the YAML linter (`dotnet run --project Content.YAMLLinter`).
- Read `CONTRIBUTING.md`; new files are already in the CMU zones.
- Attach screenshots of it in game.

## Notes from the export

{warnings}"""
