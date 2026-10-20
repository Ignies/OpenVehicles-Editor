"""The vehicle tried in the game: a test server and a client from a built CMU repo, the way the jeep's
test harness runs them (its server.toml: Stable Garrison Redux, every player a colonist with the jeeps
beside them), and the vehicle spawned beside the player. Prototypes and textures written while it runs
are picked up by the debug builds' resource watching, so a vehicle written again shows without a
restart; spawn it again to see the new one.

    test = GameTest(repo); test.start(); test.status(); test.spawn("CMUVehicleRoadster"); test.stop()
"""

import os
import json
import re
import subprocess
import threading
import time
import urllib.request

TOML = ("Content.CMU", "Design", "Vehicles", "JeepPixelArt", "Test", "server.toml")
USER = "VehicleTester"
# The server log line the jeep dev kit writes once a player has spawned with their kit.
READY = re.compile(r"Jeep dev kit for (\S+)")
SPAWNED = re.compile(r"spawn", re.I)


class GameTest:
    def __init__(self, repo, run_dir):
        self.repo = repo
        self.run = run_dir
        self.server = None
        self.client = None
        self.server_up = False
        self.lines = []
        self.ready_user = None
        self.started = None
        self.lock = threading.Lock()

    # -- processes ------------------------------------------------------------------------------

    def exe(self, name):
        return os.path.join(self.repo, "bin", name, f"{name}.exe")

    def check(self):
        """Why it can't run, or None."""
        for name in ("Content.Server", "Content.Client"):
            if not os.path.isfile(self.exe(name)):
                return f"{self.exe(name)} isn't built: build the repo first (dotnet build {name})."
        if not os.path.isfile(os.path.join(self.repo, *TOML)):
            return f"No test config at {os.path.join(self.repo, *TOML)}."
        return None

    def running(self):
        return self.server is not None and self.server.poll() is None

    def start(self, width=1280, height=900):
        if self.running():
            return self.status()
        problem = self.check()
        if problem:
            raise ValueError(problem)
        os.makedirs(self.run, exist_ok=True)
        overlay = os.path.join(self.repo, "Content.CMU", "Resources")
        self.lines = []
        self.ready_user = None
        self.started = time.time()
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.server = subprocess.Popen(
            [self.exe("Content.Server"), "--mount-dir", overlay, "--config-file", os.path.join(self.repo, *TOML),
             "--cvar", "res.checkpathcasing=false", "--data-dir", os.path.join(self.run, "data")],
            cwd=self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
        threading.Thread(target=self.read, daemon=True).start()
        self.server_up = False
        threading.Thread(target=self.join, args=(overlay, width, height, flags), daemon=True).start()
        return self.status()

    def join(self, overlay, width, height, flags):
        """The client, once the server answers on its status port (a client that connects sooner
        gives up and waits at the menu)."""
        while self.running():
            try:
                with urllib.request.urlopen("http://127.0.0.1:1222/status", timeout=1) as r:
                    json.load(r)
                break
            except (OSError, ValueError):
                time.sleep(1)
        if not self.running():
            return
        self.server_up = True
        self.client_log = open(os.path.join(self.run, "client.log"), "w", encoding="utf-8")
        self.client = subprocess.Popen(
            [self.exe("Content.Client"), "--mount-dir", overlay, "--connect", "--connect-address", "127.0.0.1:1222",
             "--username", USER, "--cvar", "cmu.ui_configured=true", "--cvar", "res.checkpathcasing=false",
             "--cvar", f"display.width={width}", "--cvar", f"display.height={height}"],
            cwd=self.repo, stdout=self.client_log, stderr=subprocess.STDOUT, creationflags=flags)

    def read(self):
        with open(os.path.join(self.run, "server.log"), "w", encoding="utf-8") as log:
            for line in self.server.stdout:
                log.write(line)
                log.flush()
                with self.lock:
                    self.lines.append(line.rstrip())
                    del self.lines[:-400]
                m = READY.search(line)
                if m:
                    self.ready_user = m.group(1)

    def command(self, text):
        """A command on the server's console."""
        if not self.running():
            raise ValueError("The test server isn't running.")
        self.server.stdin.write(text + "\n")
        self.server.stdin.flush()
        return {"sent": text}

    def spawn(self, proto):
        """The vehicle beside the tester: on them, then a few tiles east so it isn't on top of them."""
        if not self.ready_user:
            raise ValueError("Nobody's in the game yet: wait for the client to join.")
        return self.command(f"entities with Actor spawn:on {proto}")

    def status(self):
        with self.lock:
            tail = self.lines[-12:]
        client = self.client is not None and self.client.poll() is None
        state = ("stopped" if not self.running() else "ready" if self.ready_user
                 else "joining" if getattr(self, "server_up", False) else "loading")
        return {"state": state, "client": client, "user": self.ready_user,
                "seconds": round(time.time() - self.started) if self.started else 0, "log": tail,
                "errors": [l for l in tail if " ERRO " in l or "[ERRO]" in l or "Exception" in l]}

    def stop(self):
        for p in (self.client, self.server):
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        self.client = self.server = None
        self.ready_user = None
        return {"state": "stopped"}
