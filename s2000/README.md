# s2000 poll

Monitors the two SOLIX S2000 power stations on the Anker account via the Anker MQTT server, runs as a systemd service on mindy, and serves a dashboard at http://mindy.peer.net/s2000/ (linked from the "8T dashboard" button on the landing page).

## Files

- `mqtt_s2000.py`: the monitor. Writes a JSON snapshot every 60 seconds.
- `s2000_dashboard.html`: the dashboard page. Reads the snapshot and refreshes every 60 seconds.
- `nginx-s2000.conf`: nginx location block that serves the dashboard at `/s2000/`.
- `install_s2000.sh`: installs or reinstalls everything on mindy. Safe to re-run.
- `poll_s2000.py`: older REST poller. Not deployed.

## to install or reinstall

Run from this folder on your Mac:

    scp mqtt_s2000.py grafana_push_s2000.py install_s2000.sh nginx-s2000.conf s2000_dashboard.html ubuntu@mindy.peer.net:~/s2000/
    ssh ubuntu@mindy.peer.net 'cd ~/s2000 && bash install_s2000.sh'

The script:
- installs `python3-venv` and `git` with apt
- creates the venv in `~/s2000/.venv` and installs `aiohttp`, `aiofiles`, `paho-mqtt`, `cryptography` and `anker-solix-api` (pinned to commit `7ab6dd3`, the same as the local `dev/anker` venv)
- creates `/var/www/s2000/` (owned by `ubuntu`) and copies the dashboard to `index.html`
- writes `/etc/systemd/system/s2000-mqtt.service` and restarts the service
- adds the nginx include to `/etc/nginx/sites-available/default`, if not already there, and reloads nginx
- changes the landing page button from `/anker8t/s2000_dashboard.html` to `/s2000/`, if not already changed

Backups of the nginx config and landing page go to `/var/backups/s2000/`.

It expects `/etc/anker.env` to exist and be readable by `ubuntu`. It doesn't create or change that file. The file holds `ANKERUSER`, `ANKERPASSWORD` and `ANKERCOUNTRY` as `KEY=value` lines.

## service

- Unit: `s2000-mqtt.service`, running `mqtt_s2000.py --show-every 60` as `ubuntu`.
- Restart: `on-failure`, after 60 seconds.
- Output: the journal. MQTT publish and subscribe lines are suppressed; only the per-device summaries are logged.
- Snapshot: `/var/www/s2000/s2000_mqtt_latest.json`, set by the `S2000_SNAPSHOT` environment variable in the unit file. Without that variable, the script writes next to itself.

## grafana cloud

- `grafana_push_s2000.py`: reads the snapshot and posts each new one to Grafana Cloud as Influx line protocol, measurement `s2000`, tagged by `device`. Only numeric values are sent.
- Unit: `s2000-grafana.service`, running `grafana_push_s2000.py --interval 60` as `ubuntu`. Credentials come from `/etc/grafana.env` (`GC_URL`, `GC_USER`, `GC_TOKEN`), which must be `root:ubuntu` 640.
- Last snapshot sent is recorded in `~/s2000/.grafana_last`, so a restart doesn't resend it.
- Logs: `journalctl -u s2000-grafana -f`. Test locally with `python3 grafana_push_s2000.py --dry-run`.

## dashboard and nginx

- `/s2000/` serves `/var/www/s2000/index.html`. `/s2000` redirects to `/s2000/`.
- The page loads `s2000_mqtt_latest.json` from the same folder.
- There is no authentication. The dashboard is public on port 80, like `/usage/`.

## to run manually

    ssh ubuntu@mindy.peer.net
    cd ~/s2000
    set -a; . /etc/anker.env; set +a
    .venv/bin/python mqtt_s2000.py                  # Ctrl+C to stop

Options: `--show-every N`, `--runtime MINUTES`, `--no-realtime`, `--debug`.

## debug

    sudo systemctl status s2000-mqtt
    journalctl -u s2000-mqtt -f          # follow the output
    sudo systemctl restart s2000-mqtt
    curl -I http://127.0.0.1/s2000/      # check the dashboard is served
