#!/usr/bin/env bash
# Sets up (or updates) the CMU Open Vehicles web tool on a Debian or Ubuntu server:
#
#   curl -fsSL https://raw.githubusercontent.com/Ignies/OpenVehicles-Editor/main/install.sh | sudo bash
#
# It only adds its own site, beside whatever the server already hosts: through the web server that
# already holds port 80 (nginx, Apache or Caddy: a server block, a vhost or an imported Caddy file
# of its own, the config tested before it's reloaded, never restarted), or through Caddy, installed,
# when nothing does. Anything else on port 80 and it stops before changing anything.
#
# Run it again to update. Settings, as environment variables (sudo env DOMAIN=... bash):
#   DOMAIN      the site's name                      (openvehicles.ignies.net)
#   HTTPS       1 for HTTPS with a certificate (Caddy only: it gets one itself), else plain HTTP on 80
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
STAMP="$(date +%Y%m%d%H%M%S)"

say() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run it as root: curl -fsSL .../install.sh | sudo bash"
command -v ss >/dev/null || apt-get install -y -qq iproute2 >/dev/null

# -- what is already there: decided before anything changes ----------------------------------------

# The program listening on a TCP port, or nothing.
# (A free port matches nothing, which mustn't stop the script.)
listener() { { ss -ltnp "sport = :$1" 2>/dev/null || true; } | { grep -o 'users:(("[^"]*' || true; } | head -1 | sed 's/users:(("//'; }

say "Looking at what the server already runs"
WEB="$(listener 80)"
case "$WEB" in
    "") FRONT=caddy-new ;;
    nginx*) FRONT=nginx ;;
    apache2*|httpd*) FRONT=apache ;;
    caddy*) FRONT=caddy ;;
    *) die "Port 80 is held by '$WEB', which this script doesn't know how to add a site to. Nothing was changed: proxy $DOMAIN to 127.0.0.1:$PORT from it yourself, then run this again." ;;
esac
echo "Port 80: ${WEB:-free} -> adding the site through ${FRONT/caddy-new/a new Caddy}"

TAKEN="$(listener "$PORT")"
if [ -n "$TAKEN" ] && ! systemctl is-active --quiet openvehicles; then
    die "Port $PORT is held by '$TAKEN'. Nothing was changed: pick a free one, e.g. sudo env PORT=8091 bash"
fi
if [ "$HTTPS" = "1" ] && [ "$FRONT" != caddy ] && [ "$FRONT" != caddy-new ]; then
    die "HTTPS=1 is for Caddy; with $FRONT, run this without it, then add a certificate with certbot (certbot --$FRONT -d $DOMAIN)."
fi

# -- the tool ---------------------------------------------------------------------------------------

say "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv curl ca-certificates >/dev/null

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

say "Waiting for the tool"
up=0
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then up=1; break; fi
    sleep 1
done
if [ "$up" != 1 ]; then
    journalctl -u openvehicles -n 40 --no-pager >&2
    die "The tool didn't answer on port $PORT (its log is above). The web server was left as it was."
fi

# -- its site, beside the others --------------------------------------------------------------------

if [ "$HTTPS" = "1" ]; then SITE="$DOMAIN"; else SITE="http://$DOMAIN"; fi

caddy_site() {
    cat > /etc/caddy/openvehicles.caddy <<EOF
# CMU Open Vehicles (install.sh): $DOMAIN to the tool on 127.0.0.1:$PORT
$SITE {
    encode gzip
    reverse_proxy 127.0.0.1:$PORT
}
EOF
}

case "$FRONT" in
nginx)
    say "nginx: a server block of its own for $DOMAIN"
    if [ -d /etc/nginx/sites-available ] && [ -d /etc/nginx/sites-enabled ]; then
        CONF=/etc/nginx/sites-available/openvehicles
        LINK=/etc/nginx/sites-enabled/openvehicles
    else
        CONF=/etc/nginx/conf.d/openvehicles.conf
        LINK=
    fi
    [ -f "$CONF" ] && cp "$CONF" "$CONF.bak-$STAMP"
    cat > "$CONF" <<EOF
# CMU Open Vehicles (install.sh): $DOMAIN to the tool on 127.0.0.1:$PORT
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    client_max_body_size 100m;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
    [ -n "$LINK" ] && ln -sf "$CONF" "$LINK"
    if ! nginx -t 2>/tmp/openvehicles-nginx.log; then
        cat /tmp/openvehicles-nginx.log >&2
        rm -f "$LINK" "$CONF"
        [ -f "$CONF.bak-$STAMP" ] && mv "$CONF.bak-$STAMP" "$CONF" && [ -n "$LINK" ] && ln -sf "$CONF" "$LINK"
        die "nginx rejected the config (above), so it was taken back out and nginx left as it was."
    fi
    systemctl reload nginx
    ;;
apache)
    say "Apache: a vhost of its own for $DOMAIN"
    CONF=/etc/apache2/sites-available/openvehicles.conf
    [ -f "$CONF" ] && cp "$CONF" "$CONF.bak-$STAMP"
    cat > "$CONF" <<EOF
# CMU Open Vehicles (install.sh): $DOMAIN to the tool on 127.0.0.1:$PORT
<VirtualHost *:80>
    ServerName $DOMAIN
    ProxyPreserveHost On
    ProxyTimeout 3600
    LimitRequestBody 104857600
    RewriteEngine On
    RewriteCond %{HTTP:Upgrade} =websocket [NC]
    RewriteRule ^/(.*) ws://127.0.0.1:$PORT/\$1 [P,L]
    ProxyPass / http://127.0.0.1:$PORT/
    ProxyPassReverse / http://127.0.0.1:$PORT/
</VirtualHost>
EOF
    a2enmod -q proxy proxy_http proxy_wstunnel rewrite >/dev/null
    a2ensite -q openvehicles >/dev/null
    if ! apache2ctl configtest 2>/tmp/openvehicles-apache.log; then
        cat /tmp/openvehicles-apache.log >&2
        a2dissite -q openvehicles >/dev/null || true
        rm -f "$CONF"
        [ -f "$CONF.bak-$STAMP" ] && mv "$CONF.bak-$STAMP" "$CONF"
        die "Apache rejected the config (above), so it was taken back out and Apache left as it was."
    fi
    systemctl reload apache2
    ;;
caddy|caddy-new)
    if [ "$FRONT" = caddy-new ] && ! command -v caddy >/dev/null; then
        say "Caddy (nothing serves port 80 yet)"
        apt-get install -y -qq gnupg debian-keyring debian-archive-keyring apt-transport-https >/dev/null
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq
        apt-get install -y -qq caddy >/dev/null
    fi
    say "Caddy: a site file of its own for $DOMAIN, imported into the Caddyfile"
    MAIN=/etc/caddy/Caddyfile
    touch "$MAIN"
    cp "$MAIN" "$MAIN.bak-$STAMP"
    caddy_site
    grep -qxF 'import /etc/caddy/openvehicles.caddy' "$MAIN" || printf '\nimport /etc/caddy/openvehicles.caddy\n' >> "$MAIN"
    if ! caddy validate --config "$MAIN" --adapter caddyfile >/tmp/openvehicles-caddy.log 2>&1; then
        cat /tmp/openvehicles-caddy.log >&2
        cp "$MAIN.bak-$STAMP" "$MAIN"
        rm -f /etc/caddy/openvehicles.caddy
        die "Caddy rejected the config (above), so the Caddyfile was put back as it was."
    fi
    systemctl enable -q caddy
    if systemctl is-active --quiet caddy; then systemctl reload caddy; else systemctl start caddy; fi
    ;;
esac

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    ufw allow 80/tcp >/dev/null
    [ "$HTTPS" = "1" ] && ufw allow 443/tcp >/dev/null
fi

say "Done"
if [ "$HTTPS" = "1" ]; then echo "Up: https://$DOMAIN"; else echo "Up: http://$DOMAIN"; fi
echo "Logs: journalctl -u openvehicles -f    Update: run this again."
