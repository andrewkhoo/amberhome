#!/usr/bin/env bash
# Restore the mindy nginx configuration onto a new Ubuntu server.
#
# Run from a checkout of this directory on the new server:
#   sudo ./restore.sh
#
# What it does:
#   1. Installs nginx and ssl-cert (ssl-cert provides the snakeoil certificate the default site uses).
#   2. Backs up any existing /etc/nginx to /etc/nginx.bak-<timestamp>.
#   3. Copies the app snippets to the paths sites-available/default includes.
#   4. Copies this directory's contents into /etc/nginx.
#   5. Tests the configuration and reloads nginx. If the test fails, the backup is restored.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
	echo "Run as root: sudo $0" >&2
	exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETC_DIR=/etc/nginx
APP_USER=ubuntu
BACKUP_DIR="${ETC_DIR}.bak-$(date +%Y%m%d-%H%M%S)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx ssl-cert

if [[ -d "$ETC_DIR" ]]; then
	cp -a "$ETC_DIR" "$BACKUP_DIR"
	echo "Existing configuration backed up to $BACKUP_DIR"
fi

# Application snippets are included by path from sites-available/default,
# so they must exist at the same locations on the new server.
install_snippet() {
	local src="$1" dest="$2"
	install -d -o "$APP_USER" -g "$APP_USER" "$(dirname "$dest")"
	install -o "$APP_USER" -g "$APP_USER" -m 644 "$src" "$dest"
}
install_snippet "$SRC_DIR/app-snippets/usage/nginx-snippet.conf" /home/$APP_USER/amberhome/usage/deploy/nginx-snippet.conf
install_snippet "$SRC_DIR/app-snippets/s2000/nginx-s2000.conf" /home/$APP_USER/s2000/deploy/nginx-s2000.conf
install_snippet "$SRC_DIR/app-snippets/amber-push/nginx-amber-push.conf" /home/$APP_USER/amber-push/deploy/nginx-amber-push.conf
install_snippet "$SRC_DIR/app-snippets/tuya/nginx-tuya.conf" /home/$APP_USER/tuya/deploy/nginx-tuya.conf

# The S2000 dashboard is served from here; mqtt_s2000.py writes into it.
install -d -o "$APP_USER" -g "$APP_USER" /var/www/s2000
# The Amber Forecast page is written by build_page.py; the Tuya page is served as www-data.
install -d -o "$APP_USER" -g "$APP_USER" /var/www/amber-push
install -d -o www-data -g www-data /var/www/tuya

# Copy the nginx configuration, preserving symlinks and permissions.
# Excludes this script and the app snippets, which are not part of /etc/nginx.
tar -C "$SRC_DIR" \
	--exclude=./app-snippets \
	--exclude=./restore.sh \
	-cf - . | tar -C "$ETC_DIR" -xf -

if ! nginx -t; then
	echo "nginx configuration test failed." >&2
	if [[ -d "$BACKUP_DIR" ]]; then
		echo "Restoring previous configuration from $BACKUP_DIR" >&2
		rm -rf "$ETC_DIR"
		mv "$BACKUP_DIR" "$ETC_DIR"
	fi
	exit 1
fi

systemctl enable nginx
systemctl reload-or-restart nginx
systemctl --no-pager status nginx | head -n 5

echo
echo "nginx configuration restored."
echo "Reminder: ports 80 and 443 must be open in the host firewall (iptables/netfilter-persistent)"
echo "and in the cloud network security list. See ../README.md."
