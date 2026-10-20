"""The tool's Python side: a JSON-lines RPC server on stdin/stdout.

Requests are {"id", "method", "params"}; replies {"id", "result"} or {"id", "error"}; events
{"event", "data"}. Work runs on one worker thread, in order. A preview, or the vehicle driving, is
skipped when a newer one is already waiting, so the drawing never waits on renders it has drawn past. The generator script is
watched: when its file changes it is imported again and the app told, so edits to the generator show
in the preview at once.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import project as P  # noqa: E402
import voxels as V  # noqa: E402

# Run for the web tool (web.py starts one of these per browser tab): the generator is the server's,
# never a path the page sends; files are written only into the session's own folder, and handed out
# as downloads; nothing runs on, or writes into, a repo on the machine.
WEB = os.environ.get("CMUOV_WEB") == "1"
WEB_GENERATOR = os.environ.get("CMUOV_GENERATOR")
WORKDIR = os.environ.get("CMUOV_WORKDIR")
# What the web tool can't do: run the generator into a repo, lint or commit one, start the game.
DESKTOP_ONLY = {"generate", "cancel_generate", "lint", "git_state", "commit",
                "game_start", "game_status", "game_spawn", "game_command", "game_stop"}

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass


class Server:
    def __init__(self):
        self.gen = None
        self.jobs = queue.Queue()
        self.out = threading.Lock()
        self.latest_preview = -1
        self.latest_drive = -1
        self.process = None
        self.game = None

    # -- wire ---------------------------------------------------------------------------------

    def send(self, obj):
        line = json.dumps(obj, separators=(",", ":"))
        with self.out:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def event(self, name, data=None):
        self.send({"event": name, "data": data or {}})

    def run(self):
        threading.Thread(target=self.work, daemon=True).start()
        if not WEB:
            threading.Thread(target=self.watch, daemon=True).start()
        self.event("ready", {"python": sys.version.split()[0]})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            method = req.get("method")
            if WEB and method in DESKTOP_ONLY:
                self.reply(req, error=f"{method} isn't available in the web tool.")
                continue
            if method == "preview":
                self.latest_preview = req["id"]
            if method == "drive":
                self.latest_drive = req["id"]
            if method == "cancel_generate":
                self.reply(req, self.cancel_generate())
                continue
            # The test game answers at once, not after whatever the worker is rendering.
            if method in ("game_status", "game_command", "game_stop"):
                try:
                    self.reply(req, getattr(self, "rpc_" + method)(**(req.get("params") or {})))
                except Exception as exc:  # noqa: BLE001
                    self.reply(req, error=f"{type(exc).__name__}: {exc}")
                continue
            self.jobs.put(req)

    def reply(self, req, result=None, error=None):
        msg = {"id": req["id"]}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self.send(msg)

    def work(self):
        while True:
            req = self.jobs.get()
            if req.get("method") == "preview" and req["id"] != self.latest_preview or \
                    req.get("method") == "drive" and req["id"] != self.latest_drive:
                self.reply(req, {"stale": True})
                continue
            handler = getattr(self, "rpc_" + str(req.get("method")), None)
            if handler is None:
                self.reply(req, error=f"Unknown method {req.get('method')}")
                continue
            try:
                self.reply(req, handler(**(req.get("params") or {})))
            except Exception as exc:  # noqa: BLE001 - every failure goes back to the app
                self.reply(req, error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=6)}")

    def watch(self):
        while True:
            time.sleep(1.0)
            gen = self.gen
            if gen is None:
                continue
            try:
                mtime = os.path.getmtime(gen.path)
            except OSError:
                continue
            if mtime != gen.mtime:
                gen.mtime = mtime
                self.jobs.put({"id": -1, "method": "reload", "params": {}})

    # -- methods ------------------------------------------------------------------------------

    def rpc_open_generator(self, path):
        if WEB:
            path = WEB_GENERATOR
        started = time.time()
        self.gen = V.Generator(path)
        return self.info(time.time() - started)

    def rpc_reload(self):
        if self.gen is None:
            return None
        started = time.time()
        try:
            self.gen = V.Generator(self.gen.path)
        except Exception as exc:  # noqa: BLE001
            self.event("generator", {"state": "error", "error": f"{type(exc).__name__}: {exc}"})
            return None
        self.event("generator", {"state": "reloaded", **self.info(time.time() - started)})
        return None

    def info(self, seconds):
        g = self.gen.module
        return {
            "web": WEB,
            "path": os.path.basename(self.gen.path) if WEB else self.gen.path,
            "materials": self.gen.palette(),
            "variants": list(g.VARIANTS),
            "dirs": list(g.DIRS),
            "rsiDir": None if WEB else getattr(g, "RSI_DIR", None),
            "previewDir": None if WEB else getattr(g, "PREVIEW_DIR", None),
            "repo": None if WEB else self.repo(),
            "wheelFrames": self.gen.wheel_frames,
            "wheelDelay": self.gen.wheel_delay,
            "seconds": round(seconds, 2),
        }

    def repo(self):
        """The game repo the generator lives in: its REPO, or the first folder up with Resources."""
        repo = getattr(self.gen.module, "REPO", None)
        if repo and os.path.isdir(repo):
            return repo
        d = os.path.dirname(self.gen.path)
        while os.path.dirname(d) != d:
            if os.path.isdir(os.path.join(d, "Resources", "Prototypes")):
                return d
            d = os.path.dirname(d)
        return None

    def rpc_load_vehicle(self, variant, box):
        b = V.Box.from_dict(box)
        layers = []
        for layer in self.gen.layers(variant):
            views = V.native_views(self.gen, layer, b, self.gen.materials)
            layers.append({k: layer[k] for k in ("name", "kind", "sides", "thickness", "role", "rides", "native")}
                          | {"visible": True, "stage": None, "views": views})
        return {"box": b.as_dict(), "layers": layers, "materials": self.gen.materials}

    def rpc_refresh_native(self, project, box, materials=None):
        """A project's layers loaded from the generator brought up to per direction drawings (a
        project from before them): their views, by layer index, for those that changed."""
        b = V.Box.from_dict(box)
        layers = project["layers"]
        before = [l.get("views") for l in layers]
        V.refresh_native(self.gen, layers, b, materials or self.gen.materials)
        return {"views": {n: l["views"] for n, l in enumerate(layers) if l.get("views") is not before[n]}}

    def colours(self, paint):
        if not paint:
            return None
        out = {k: (tuple(paint[k]) if paint.get(k) else None) for k in ("body", "interior", "seats")}
        out["dirt"] = float(paint.get("dirt") or 0)
        return out

    def rpc_preview(self, project, box, materials=None, paint=None):
        """Each direction's sprite, which layer every pixel shows, and which voxel: (f, r, z) as int16
        triplets per pixel, -32768 where there's none, for painting on the preview."""
        started = time.time()
        b = V.Box.from_dict(box)
        dirs = list(self.gen.module.DIRS)
        images, parts, keys = {}, {}, {}
        with V.styled(self.gen, project, b):
            models, owners, _, count = V.build(project, b, materials or self.gen.materials, dirs, gen=self.gen)
            carved = time.time()
            for view in dirs:
                key = V.blank_keys()
                img, part = V.render_sprite(self.gen, view, models[view], owners[view], self.colours(paint), keys=key)
                images[view] = V.png_data_url(img)
                parts[view] = V.bytes_b64(part)
                keys[view] = V.keys_b64(key)
        return {"images": images, "parts": parts, "keys": keys, "size": V.OUT, "voxels": count,
                "ms": {"carve": round((carved - started) * 1000), "render": round((time.time() - carved) * 1000)}}

    def rpc_bake(self, project, box, layer, materials=None):
        """A layer as voxels (see voxels.bake), for the app to make it a voxel layer."""
        b = V.Box.from_dict(box)
        spec = project["layers"][layer]
        grid = V.bake(self.gen, spec, b, materials or self.gen.materials, spec.get("role") or f"L{layer}")
        return {"voxels": V.encode_voxels(grid), "count": int((grid != V.EMPTY).sum())}

    def rpc_swing(self, project, box, layer, view, materials=None, paint=None):
        """The frames of a layer swinging open, seen from one direction, at the generator's own angles
        for its kind; played backwards to shut."""
        b = V.Box.from_dict(box)
        frames = []
        with V.styled(self.gen, project, b):
            scenes = V.swing_scenes(self.gen, project, b, materials or self.gen.materials, layer, view)
            for vox, owner in scenes:
                img, _ = V.render_sprite(self.gen, view, vox, owner, self.colours(paint))
                frames.append(V.png_data_url(img))
        return {"frames": frames}

    def rpc_drive(self, project, box, view, materials=None, paint=None):
        """The vehicle driving, seen from one direction: a frame for each of the wheels' frames, as
        the game's wheels_0 state turns them while it moves, and split for the suspension: the
        wheels alone per frame, and the body that bobs over them."""
        b = V.Box.from_dict(box)
        with V.styled(self.gen, project, b):
            frames, wheels, body = V.drive_sprites(self.gen, project, b, materials or self.gen.materials, view,
                                                   self.colours(paint))
        return {"frames": [V.png_data_url(f) for f in frames], "wheels": [V.png_data_url(f) for f in wheels],
                "body": V.png_data_url(body) if body is not None else None,
                "delay": round(self.gen.wheel_delay * 1000)}

    def rpc_reshape(self, project, box, size, mode="keep", materials=None):
        """The drawing in a box of another size: kept around the vehicle's centre, or stretched."""
        data = {"box": box, "materials": materials or self.gen.materials, "layers": project["layers"]}
        P.reshape(data, size, mode, self.gen)
        return {"box": data["box"], "layers": data["layers"]}

    def rpc_camera_defaults(self):
        """The custom camera that draws most like the generator, per direction (camera.default_dirs)."""
        import camera
        return {"dirs": camera.default_dirs(self.gen.module)}

    def rpc_damage_types(self):
        repo = self.repo()
        return P.damage_types(repo) if repo else {"types": [], "groups": {}}

    def session_file(self, name):
        """A file in the web session's own folder, named after a path the page sent (its name only)."""
        name = os.path.basename(str(name).replace("\\", "/")).split(":")[-1] or "file"
        os.makedirs(WORKDIR, exist_ok=True)
        return os.path.join(WORKDIR, name)

    def rpc_export_voxels(self, project, box, path, materials=None):
        if WEB:
            path = self.session_file(path)
        b = V.Box.from_dict(box)
        _, _, per_layer, _ = V.build(project, b, materials or self.gen.materials, ["S"])
        layers = []
        for spec, coloured in zip(project["layers"], per_layer):
            if coloured is None:
                continue
            layers.append({"name": spec.get("name"), "kind": spec.get("kind"),
                           "voxels": [[f, r, z, m] for (f, r, z), (m, _) in sorted(coloured["S"].items())]})
        data = {"format": "cmu-open-vehicle-voxels", "version": 1, "box": b.as_dict(), "layers": layers}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        if WEB:
            return {"path": os.path.basename(path), "layers": len(layers), "download": os.path.basename(path)}
        return {"path": path, "layers": len(layers)}

    def rpc_game_info(self):
        """The game repo a vehicle is written into by default."""
        return {"repo": None if WEB else self.repo()}

    def rpc_export_game(self, project, box, materials=None, repo=None, model3d=True):
        """The vehicle written into the game (export_game.write): its sprites, crayon map, prototypes
        and strings. Tells the app how far along it is as it goes."""
        import export_game as G
        repo = self.repo() if WEB else repo or self.repo()
        if not repo or not os.path.isdir(os.path.join(repo, "Content.CMU", "Resources")):
            raise ValueError(f"{repo or 'No repo'} isn't a CMU repo (no Content.CMU/Resources).")
        data = {"box": box, "materials": materials or self.gen.materials, "layers": project["layers"],
                "vehicle": project.get("vehicle") or {}}
        started = time.time()
        say = lambda text: self.event("game_progress", {"text": text})  # noqa: E731
        # On the web, into a folder of its own laid out as the repo is, zipped up for download.
        out = None
        if WEB:
            import shutil
            out = os.path.join(WORKDIR, "export")
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out)
        result = G.write(self.gen, data, repo, say, out=out)
        # Its 3D model too, for the first person view: read from the sprites just written.
        if model3d:
            import export_3d as E3
            say("Building the 3D model")
            try:
                r3 = E3.after_game(self.gen, data, out or repo, result, progress=say)
                result["model3d"] = r3["path"]
                result["files"].append(r3["path"])
            except Exception as exc:  # noqa: BLE001 - the 2D vehicle is written whatever happens here
                result.setdefault("warnings", []).append(f"3D model: {type(exc).__name__}: {exc}")
        result["seconds"] = round(time.time() - started, 1)
        result["repo"] = repo
        if WEB:
            say("Packing the zip")
            import package
            zipped = package.zip_export(out, result, data, self.session_file(f"{G.names(data['vehicle'])[2]}.zip"))
            for key in ("rsi", "crayon", "prototypes", "strings", "model3d"):
                if result.get(key):
                    result[key] = os.path.relpath(result[key], out).replace(os.sep, "/")
            result["files"] = [os.path.relpath(f, out).replace(os.sep, "/") for f in result["files"]]
            result["repo"] = None
            result["download"] = os.path.basename(zipped)
        return result

    def rpc_check(self, project, box, materials=None):
        """What would stop the vehicle going into the game, and what's worth knowing, without
        rendering (export_game.check)."""
        import export_game as G
        return G.check({"box": box, "materials": materials or self.gen.materials, "layers": project["layers"],
                        "vehicle": project.get("vehicle") or {}})

    def rpc_room(self, box):
        """How far the box can grow before the vehicle runs off the game's 96 px frame (voxels.room)."""
        return V.room(self.gen, V.Box.from_dict(box))

    def rpc_templates(self):
        """The vehicles a new project can start from."""
        import templates as T
        return {"templates": T.listing()}

    def rpc_template(self, name, box=None):
        """A new project from a template, on this generator's palette."""
        import templates as T
        p = T.make(name, box)
        P.remap(p, self.gen.materials)
        return {"box": p["box"], "layers": p["layers"], "materials": self.gen.materials, "vehicle": p.get("vehicle"),
                "customMaterials": p.get("customMaterials") or []}

    def rpc_import_sprite(self, files, nose="N", name="vehicle"):
        """A new project from a finished sprite, an Aseprite file or PNGs (sprite.build): each layer
        named for the part it is, one top-down picture for all four directions."""
        import sprite as S
        p, warnings = S.build(files, nose, name, self.gen.materials)
        return {"box": p["box"], "layers": p["layers"], "materials": self.gen.materials, "vehicle": p["vehicle"],
                "sprite": p["sprite"], "refs": p["refs"], "customMaterials": [], "warnings": warnings}

    def rpc_wheels(self, box, axles, radius, track, width=5, materials=None):
        """A wheel layer built from axle places, radius, track and tyre width (templates.wheels)."""
        import templates as T
        p = {"box": box, "materials": list(materials or self.gen.materials), "layers": []}
        lay = T.wheels(p, axles, radius, track, width)
        P.remap(p, materials or self.gen.materials)
        return {"layer": p["layers"][0] if p["layers"] else lay}

    def rpc_lint(self, repo=None):
        """The repo's YAML linter, run on its own thread (minutes); its end comes as a lint event."""
        repo = repo or self.repo()
        exe = os.path.join(repo, "bin", "Content.YAMLLinter", "Content.YAMLLinter.exe")
        if not os.path.isfile(exe):
            raise ValueError(f"{exe} isn't built: dotnet build Content.YAMLLinter")

        def run():
            started = time.time()
            p = subprocess.run([exe], cwd=os.path.dirname(exe), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            out = (p.stdout or "") + (p.stderr or "")
            errors = [l.strip() for l in out.splitlines() if l.startswith("::error")]
            self.event("lint", {"code": p.returncode, "errors": errors, "seconds": round(time.time() - started)})

        threading.Thread(target=run, daemon=True).start()
        return {"started": True}

    def rpc_git_state(self, repo, files):
        """The repo's branch, and which of the files written have changes to commit."""
        def git(*args):
            return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        changed = git("status", "--porcelain", "--untracked-files=all", "--", *files).stdout.splitlines()
        return {"branch": branch, "changed": [l[3:] for l in changed]}

    def rpc_commit(self, repo, files, message):
        """Commits the files written, and only them, whatever else the repo has going."""
        def git(*args):
            p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if p.returncode:
                raise ValueError((p.stderr or p.stdout).strip())
            return p.stdout.strip()
        git("add", "--", *files)
        git("commit", "-m", message, "--", *files)
        return {"commit": git("rev-parse", "--short", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD")}

    def rpc_add_materials(self, materials):
        """A project's own materials added to the palette; the palette as it is now."""
        self.gen.add_materials(materials)
        return {"materials": self.gen.palette()}

    def rpc_list_sounds(self, repo=None):
        """Every sound in the repo's resources, as the game names them (/Audio/...), and its file."""
        repo = self.repo() if WEB else repo or self.repo()
        files = {}
        for root in (os.path.join(repo, "Resources"), os.path.join(repo, "Content.CMU", "Resources")):
            audio = os.path.join(root, "Audio")
            for d, _, names in os.walk(audio):
                for n in names:
                    if n.lower().endswith((".ogg", ".wav", ".mp3")):
                        full = os.path.join(d, n)
                        files["/" + os.path.relpath(full, root).replace(os.sep, "/")] = full
        # On the web the page plays them from the server, where web.py serves the same folders.
        if WEB:
            return {"files": [{"path": k, "file": "/audio" + k} for k in sorted(files)]}
        return {"files": [{"path": k, "file": v} for k, v in sorted(files.items())]}

    # -- the vehicle tried in the game ------------------------------------------------------------

    def rpc_game_start(self, repo=None):
        """A test server and client from the repo's build (gametest.GameTest)."""
        import gametest
        repo = repo or self.repo()
        if self.game is not None and self.game.repo != repo:
            self.game.stop()
            self.game = None
        if self.game is None:
            run = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "CMU Open Vehicles", "gametest")
            self.game = gametest.GameTest(repo, run)
        return self.game.start()

    def rpc_game_status(self):
        return self.game.status() if self.game else {"state": "stopped"}

    def rpc_game_spawn(self, proto):
        if not self.game:
            raise ValueError("The test game isn't running.")
        return self.game.spawn(proto)

    def rpc_game_command(self, text):
        if not self.game:
            raise ValueError("The test game isn't running.")
        return self.game.command(text)

    def rpc_game_stop(self):
        return self.game.stop() if self.game else {"state": "stopped"}

    # -- the full generator run -----------------------------------------------------------------

    def rpc_generate(self):
        if self.process is not None and self.process.poll() is None:
            return {"running": True}
        g = self.gen.module
        dirs = [d for d in (getattr(g, "RSI_DIR", None), getattr(g, "PREVIEW_DIR", None)) if d]
        total = sum(len(files) for d in dirs if os.path.isdir(d) for _, _, files in os.walk(d)) or 1
        started = time.time()
        self.process = subprocess.Popen([sys.executable, "-u", self.gen.path], cwd=os.path.dirname(self.gen.path),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        encoding="utf-8", errors="replace")
        threading.Thread(target=self.follow, args=(self.process, dirs, total, started), daemon=True).start()
        return {"running": True, "total": total}

    def follow(self, process, dirs, total, started):
        lines = []
        reader = threading.Thread(target=lambda: lines.extend(process.stdout), daemon=True)
        reader.start()
        while process.poll() is None:
            done = 0
            for d in dirs:
                for root, _, files in os.walk(d):
                    for f in files:
                        try:
                            if os.path.getmtime(os.path.join(root, f)) >= started:
                                done += 1
                        except OSError:
                            pass
            self.event("generate", {"state": "running", "files": done, "total": total,
                                    "seconds": round(time.time() - started, 1)})
            time.sleep(0.6)
        reader.join(timeout=2)
        code = process.returncode
        self.event("generate", {"state": "done" if code == 0 else "failed", "code": code,
                                "seconds": round(time.time() - started, 1), "output": "".join(lines[-40:])})

    def cancel_generate(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            return {"cancelled": True}
        return {"cancelled": False}


if __name__ == "__main__":
    Server().run()
