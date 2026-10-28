#!/usr/bin/env bash
# Install or reinstall the S2000 MQTT monitor as a systemd service on mindy.
#
# Run this ON mindy, from the directory that holds the project files:
#
#     scp mqtt_s2000.py install_s2000.sh nginx-s2000.conf s2000_dashboard.html ubuntu@mindy.peer.net:~/s2000/
#     ssh ubuntu@mindy.peer.net 'cd ~/s2000 && bash install_s2000.sh'
#
# Prerequisites: /etc/anker.env holds ANKERUSER, ANKERPASSWORD and ANKERCOUNTRY
# as KEY=value lines and is readable by root and the ubuntu group (640).
# The script uses sudo for apt and systemd. It is safe to run again.

set -euo pipefail

APP_DIR="$HOME/s2000"
VENV="$APP_DIR/.venv"
UNIT="/etc/systemd/system/s2000-mqtt.service"
ENV_FILE="/etc/anker.env"
WEB_DIR="/var/www/s2000"
NGINX_SITE="/etc/nginx/sites-available/default"
NGINX_SNIPPET="$APP_DIR/deploy/nginx-s2000.conf"
LANDING="/var/www/html/index.html"
# Same upstream commit as the local dev/anker venv
SOLIX_REF="git+https://github.com/thomluther/anker-solix-api.git@7ab6dd3c1590588926b7272e60e060f4eac96738"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -r "$ENV_FILE" ]; then
    echo "Cannot read $ENV_FILE. Check its owner and mode (expected root:ubuntu 640)." >&2
    exit 1
fi
if [ ! -f "$SRC_DIR/mqtt_s2000.py" ]; then
    echo "mqtt_s2000.py not found next to this script in $SRC_DIR" >&2
    exit 1
fi

# 1. System packages: venv support and git (needed to install the library from GitHub)
sudo apt-get update -q
sudo apt-get install -y -q python3-venv git

# 2. Application directory, copy of the script, and the web folder nginx serves
mkdir -p "$APP_DIR/deploy"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
    cp "$SRC_DIR/mqtt_s2000.py" "$APP_DIR/mqtt_s2000.py"
fi
cp "$SRC_DIR/nginx-s2000.conf" "$NGINX_SNIPPET"
sudo mkdir -p "$WEB_DIR"
sudo chown ubuntu:ubuntu "$WEB_DIR"
cp "$SRC_DIR/s2000_dashboard.html" "$WEB_DIR/index.html"

# 3. Virtualenv with the Python dependencies
if [ ! -x "$VENV/bin/python" ]; then
    rm -rf "$VENV"
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q aiohttp aiofiles paho-mqtt cryptography
"$VENV/bin/pip" install -q "$SOLIX_REF"

# 4. systemd unit
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=SOLIX S2000 MQTT monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=S2000_SNAPSHOT=$WEB_DIR/s2000_mqtt_latest.json
ExecStart=$VENV/bin/python mqtt_s2000.py --show-every 60
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

# 5. Enable and (re)start the service
sudo systemctl daemon-reload
sudo systemctl enable s2000-mqtt.service
sudo systemctl restart s2000-mqtt.service

# 6. nginx: serve the dashboard at /s2000/ and point the landing page button at it.
#    Both steps are skipped if already done, so the script can be re-run.
if ! sudo grep -q "nginx-s2000.conf" "$NGINX_SITE"; then
    sudo mkdir -p /var/backups/s2000
    sudo cp "$NGINX_SITE" "/var/backups/s2000/nginx-default.bak-$(date +%Y%m%d%H%M%S)"
    sudo sed -i "/include \/home\/ubuntu\/amberhome\/usage\/deploy\/nginx-snippet.conf;/a\\	include $NGINX_SNIPPET;" "$NGINX_SITE"
fi
if sudo grep -q 'href="/anker8t/s2000_dashboard.html"' "$LANDING"; then
    sudo mkdir -p /var/backups/s2000
    sudo cp "$LANDING" "/var/backups/s2000/index.html.bak-$(date +%Y%m%d%H%M%S)"
    sudo sed -i 's|href="/anker8t/s2000_dashboard.html"|href="/s2000/"|' "$LANDING"
fi
sudo nginx -t
sudo systemctl reload nginx

sleep 5
systemctl --no-pager status s2000-mqtt.service || true
echo "Follow the output with: journalctl -u s2000-mqtt -f"
