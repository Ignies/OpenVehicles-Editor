"""The web tool: serves the app's pages and, for each browser tab, a WebSocket to a backend of its own
(server.py, started as a subprocess with CMUOV_WEB=1). The page speaks the backend's JSON lines
protocol over the socket, as the desktop app did over stdio: requests {"id", "method", "params"},
replies {"id", "result" | "error"}, events {"event", "data"}; the backend's log lines come as
{"log"}. Files the backend writes for the page (a zip for the game, an exported sheet's voxels) are
in the session's own folder, fetched from /download/<session>/<name>; sounds from /audio/...

    python backend/web.py                      # http://localhost:8080
    CMUOV_GAMEDATA=... CMUOV_PORT=8080 python backend/web.py

Settings, all environment variables: CMUOV_GAMEDATA (the repo slice, gamedata.txt; default
gamedata/ beside the tool), CMUOV_GENERATOR (default the jeep generator in it), CMUOV_HOST
(0.0.0.0), CMUOV_PORT (8080), CMUOV_WORK (session folders; a temp folder), CMUOV_MAX_SESSIONS (8),
CMUOV_MAX_EXPORTS (2 at once, server wide), CMUOV_IDLE_MINUTES (120), CMUOV_PYTHON (this Python).
"""

import asyncio
import json
import os
import secrets
import shutil
import sys
import tempfile
import time

from aiohttp import WSMsgType, web

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
RENDERER = os.path.join(TOOL, "renderer")
GAMEDATA = os.path.abspath(os.environ.get("CMUOV_GAMEDATA") or os.path.join(TOOL, "gamedata"))
GENERATOR = os.path.abspath(os.environ.get("CMUOV_GENERATOR") or os.path.join(
    GAMEDATA, "Content.CMU", "Design", "Vehicles", "JeepPixelArt", "jeep_pixel_art.py"))
WORK = os.path.abspath(os.environ.get("CMUOV_WORK") or os.path.join(tempfile.gettempdir(), "cmuov-web"))
PYTHON = os.environ.get("CMUOV_PYTHON") or sys.executable
MAX_SESSIONS = int(os.environ.get("CMUOV_MAX_SESSIONS") or 8)
MAX_EXPORTS = int(os.environ.get("CMUOV_MAX_EXPORTS") or 2)
IDLE = float(os.environ.get("CMUOV_IDLE_MINUTES") or 120) * 60
# A project with voxel layers and pictures to trace can run to tens of megabytes as JSON.
MAX_MESSAGE = 96 * 1024 * 1024
# The game export renders every sprite state: heavy, so only a few run at once across the server.
HEAVY = {"export_game"}
AUDIO_ROOTS = [os.path.join(GAMEDATA, "Resources"), os.path.join(GAMEDATA, "Content.CMU", "Resources")]

sessions = {}
exports = asyncio.Semaphore(MAX_EXPORTS)


class Session:
    """One browser tab: its backend process and folder."""

    def __init__(self, ws):
        self.id = secrets.token_hex(16)
        self.ws = ws
        self.dir = os.path.join(WORK, self.id)
        self.proc = None
        self.heavy = {}
        self.last = time.monotonic()

    async def start(self):
        os.makedirs(self.dir, exist_ok=True)
        env = {**os.environ, "CMUOV_WEB": "1", "CMUOV_GENERATOR": GENERATOR, "CMUOV_WORKDIR": self.dir,
               "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        self.proc = await asyncio.create_subprocess_exec(
            PYTHON, "-u", os.path.join(HERE, "server.py"), cwd=HERE, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=MAX_MESSAGE)

    async def send(self, text):
        if not self.ws.closed:
            await self.ws.send_str(text)

    async def pump_out(self):
        """The backend's replies and events, to the page; an export's slot freed when it answers."""
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg_id = json.loads(text).get("id")
            except (ValueError, AttributeError):
                await self.send(json.dumps({"log": text}))
                continue
            if msg_id in self.heavy:
                self.heavy.pop(msg_id).release()
            await self.send(text)
        await self.send(json.dumps({"event": "exit", "data": {"code": self.proc.returncode}}))

    async def pump_err(self):
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            await self.send(json.dumps({"log": line.decode("utf-8", "replace").rstrip()}))

    async def request(self, text):
        """A request from the page, to the backend: heavy ones wait for a free slot first."""
        self.last = time.monotonic()
        try:
            req = json.loads(text)
        except ValueError:
            return
        if not isinstance(req, dict) or "id" not in req or not isinstance(req.get("method"), str):
            return
        if req["method"] in HEAVY:
            await exports.acquire()
            self.heavy[req["id"]] = exports
        self.proc.stdin.write((json.dumps(req, separators=(",", ":")) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def stop(self):
        for slot in self.heavy.values():
            slot.release()
        self.heavy.clear()
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


async def socket(request):
    ws = web.WebSocketResponse(max_msg_size=MAX_MESSAGE, heartbeat=30)
    await ws.prepare(request)
    if len(sessions) >= MAX_SESSIONS:
        await ws.send_str(json.dumps({"event": "busy", "data": {"max": MAX_SESSIONS}}))
        await ws.close()
        return ws
    s = Session(ws)
    sessions[s.id] = s
    try:
        await s.start()
        await ws.send_str(json.dumps({"session": s.id}))
        tasks = [asyncio.create_task(s.pump_out()), asyncio.create_task(s.pump_err())]
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await s.request(msg.data)
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
        for t in tasks:
            t.cancel()
    finally:
        sessions.pop(s.id, None)
        await s.stop()
    return ws


def inside(root, *parts):
    """root joined with parts, or None if that leaves root."""
    path = os.path.abspath(os.path.join(root, *parts))
    return path if path == root or path.startswith(root + os.sep) else None


async def download(request):
    s = sessions.get(request.match_info["session"])
    name = os.path.basename(request.match_info["name"])
    path = inside(s.dir, name) if s else None
    if not path or not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def audio(request):
    rel = request.match_info["path"]
    for root in AUDIO_ROOTS:
        path = inside(root, "Audio", *rel.split("/"))
        if path and os.path.isfile(path):
            return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
    raise web.HTTPNotFound()


async def index(_request):
    return web.FileResponse(os.path.join(RENDERER, "index.html"), headers={"Cache-Control": "no-cache"})


async def health(_request):
    return web.json_response({"ok": True, "sessions": len(sessions), "max": MAX_SESSIONS})


async def reap(_app):
    """Closes sessions left idle (a tab left open overnight), to free their backends."""
    async def loop():
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            for s in list(sessions.values()):
                if now - s.last > IDLE:
                    await s.ws.close(message=b"idle")
    task = asyncio.create_task(loop())
    yield
    task.cancel()


async def shutdown(_app):
    for s in list(sessions.values()):
        await s.stop()


@web.middleware
async def revalidate(request, handler):
    """The app's own files are checked with the server on every load (cheap: an unchanged file comes
    back 304), so a page never runs one script from before an update with another from after it."""
    response = await handler(request)
    if not request.path.startswith(("/audio/", "/download/")) and "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


def make_app():
    if not os.path.isfile(GENERATOR):
        sys.exit(f"No generator at {GENERATOR}: run tools/snapshot.py, or set CMUOV_GAMEDATA / CMUOV_GENERATOR.")
    os.makedirs(WORK, exist_ok=True)
    app = web.Application(client_max_size=MAX_MESSAGE, middlewares=[revalidate])
    app.router.add_get("/", index)
    app.router.add_get("/ws", socket)
    app.router.add_get("/healthz", health)
    app.router.add_get("/download/{session}/{name}", download)
    app.router.add_get("/audio/{path:.+}", audio)
    app.router.add_static("/", RENDERER, show_index=False)
    app.cleanup_ctx.append(reap)
    app.on_shutdown.append(shutdown)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host=os.environ.get("CMUOV_HOST") or "0.0.0.0",
                port=int(os.environ.get("CMUOV_PORT") or 8080))
