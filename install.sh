#!/usr/bin/env bash
# Sets up (or updates) the CMU Open Vehicles web tool on a Debian or Ubuntu server, served by Caddy:
#
#   curl -fsSL https://raw.githubusercontent.com/Ignies/OpenVehicles-Editor/main/install.sh | sudo bash
#
# Run it again to update. Settings, as environment variables (sudo env DOMAIN=... bash):
#   DOMAIN      the site's name                      (openvehicles.ignies.net)
#   HTTPS       1 for HTTPS on 443 with a certificate Caddy gets itself, else plain HTTP on 80
#   PORT        the port the tool listens on, on 127.0.0.1 only (8080)
#   TOOL_REPO   this tool's repo, and TOOL_REF its branch
#   CMU_REPO    the CMU repo the game data comes from, and CMU_REF its branch (the jeeps or later)
#   MAX_SESSIONS  browser tabs open at once, each its own Python backend (8)
set -euo pipefail

DOMAIN="${DOMAIN:-openvehicles.ignies.net}"
HTTPS="${HTTPS:-0}"
PORT="${PORT:-8080}"
TOOL_REPO="${TOOL_REPO:-https://github.com/Ignies/OpenVehicles-Editor.git}"
TOOL_REF="${TOOL_REF:-main}"
CMU_REPO="${CMU_REPO:-https://github.com/Ignies/ColonialMarinesUniverse.git}"
CMU_REF="${CMU_REF:-jeeps}"
MAX_SESSIONS="${MAX_SESSIONS:-8}"

ROOT=/opt/openvehicles
TOOL="$ROOT/tool"
CMU="$ROOT/cmu"
USER_NAME=ovtool

say() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
    echo "Run it as root: curl -fsSL .../install.sh | sudo bash" >&2
    exit 1
fi

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv curl ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https >/dev/null
if ! command -v caddy >/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy >/dev/null
fi

# Something else on port 80 (nginx, Apache) would keep Caddy from starting.
for other in nginx apache2; do
    if systemctl is-active --quiet "$other"; then
        echo "$other is running and holds port 80: stop it (systemctl disable --now $other) or proxy $DOMAIN to 127.0.0.1:$PORT from it, then run this again." >&2
        exit 1
    fi
done

say "User and folders"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$ROOT" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$ROOT" "$ROOT/work"

say "The tool ($TOOL_REPO, $TOOL_REF)"
if [ -d "$TOOL/.git" ]; then
    git -C "$TOOL" fetch -q --depth 1 origin "$TOOL_REF"
    git -C "$TOOL" reset -q --hard FETCH_HEAD
else
    rm -rf "$TOOL"
    git clone -q --depth 1 --branch "$TOOL_REF" "$TOOL_REPO" "$TOOL"
fi

say "The game data ($CMU_REPO, $CMU_REF): only what gamedata.txt lists"
if [ ! -d "$CMU/.git" ]; then
    rm -rf "$CMU"
    git clone -q --depth 1 --filter=blob:none --no-checkout --branch "$CMU_REF" "$CMU_REPO" "$CMU"
fi
grep -v '^\s*#' "$TOOL/gamedata.txt" | sed '/^\s*$/d' | sed 's#^#/#' > "$ROOT/sparse.txt"
git -C "$CMU" sparse-checkout set --no-cone --stdin < "$ROOT/sparse.txt"
git -C "$CMU" fetch -q --depth 1 --filter=blob:none origin "$CMU_REF"
git -C "$CMU" checkout -q -f FETCH_HEAD

say "Python"
[ -x "$ROOT/venv/bin/python" ] || python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q --upgrade pip
"$ROOT/venv/bin/pip" install -q numpy Pillow aiohttp
"$ROOT/venv/bin/python" "$TOOL/tools/snapshot.py" "$CMU" --out "$TOOL/gamedata" >/dev/null
chown -R "$USER_NAME:$USER_NAME" "$ROOT"

say "Service"
cat > /etc/systemd/system/openvehicles.service <<EOF
[Unit]
Description=CMU Open Vehicles web tool
After=network.target

[Service]
User=$USER_NAME
WorkingDirectory=$TOOL
Environment=CMUOV_HOST=127.0.0.1
Environment=CMUOV_PORT=$PORT
Environment=CMUOV_WORK=$ROOT/work
Environment=CMUOV_MAX_SESSIONS=$MAX_SESSIONS
ExecStart=$ROOT/venv/bin/python -u backend/web.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q openvehicles
systemctl restart openvehicles

say "Caddy"
if [ "$HTTPS" = "1" ]; then SITE="$DOMAIN"; else SITE="http://$DOMAIN"; fi
cat > /etc/caddy/Caddyfile <<EOF
$SITE {
    encode gzip
    reverse_proxy 127.0.0.1:$PORT
}
EOF
systemctl enable -q caddy
systemctl restart caddy

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null
    [ "$HTTPS" = "1" ] && ufw allow 443/tcp >/dev/null
fi

say "Checking"
for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
        if [ "$HTTPS" = "1" ]; then echo "Up: https://$DOMAIN"; else echo "Up: http://$DOMAIN"; fi
        echo "Logs: journalctl -u openvehicles -f    Update: run this again."
        exit 0
    fi
    sleep 1
done
echo "The tool didn't answer on port $PORT. Its log:" >&2
journalctl -u openvehicles -n 40 --no-pager >&2
exit 1
