"""Push the S2000 MQTT snapshot to Grafana Cloud as Influx line protocol.

Reads s2000_mqtt_latest.json, written every 60 seconds by mqtt_s2000.py, and posts
one point per device. Only numeric values are sent as fields. A snapshot whose
"updated" time was already sent is skipped.

Credentials come from the environment (on mindy, /etc/grafana.env):

    export GC_URL="https://influx-prod-41-prod-au-southeast-1.grafana.net/api/v1/push/influx/write"
    export GC_USER="1234567"
    export GC_TOKEN="..."

Run:

    python grafana_push_s2000.py                # push every 60 s until Ctrl+C
    python grafana_push_s2000.py --once         # push the current snapshot once and exit
    python grafana_push_s2000.py --dry-run      # print the line protocol, send nothing
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

_LOGGER: logging.Logger = logging.getLogger("grafana_push_s2000")

MEASUREMENT = "s2000"
SNAPSHOT_FILE = Path(os.getenv("S2000_SNAPSHOT") or Path(__file__).with_name("s2000_mqtt_latest.json"))
STATE_FILE = Path(__file__).with_name(".grafana_last")  # "updated" value of the last snapshot sent
TIMEOUT = 20  # seconds for one HTTP request


def escape_tag(value: str) -> str:
    """Escape a tag key or value for line protocol."""
    return str(value).replace(" ", r"\ ").replace(",", r"\,").replace("=", r"\=")


def numeric_fields(values: dict) -> dict:
    """Return only the numeric values, as floats. Booleans and text are dropped."""
    return {
        str(k): float(v)
        for k, v in values.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def to_line_protocol(snapshot: dict) -> str:
    """Build one line per device from the snapshot."""
    ts_ns = int(datetime.fromisoformat(snapshot["updated"]).timestamp() * 1_000_000_000)
    lines = []
    for sn, values in snapshot.get("devices", {}).items():
        fields = numeric_fields(values)
        if not fields:
            continue
        body = ",".join(f"{escape_tag(k)}={v!r}" for k, v in sorted(fields.items()))
        lines.append(f"{MEASUREMENT},device={escape_tag(sn)} {body} {ts_ns}")
    return "\n".join(lines)


def load_snapshot() -> dict | None:
    """Read the snapshot file, or return None if it is missing or not valid JSON yet."""
    try:
        return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        _LOGGER.warning("Cannot read %s: %s", SNAPSHOT_FILE, err)
        return None


def last_sent() -> str | None:
    """Return the "updated" value of the last snapshot sent, if any."""
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def remember_sent(updated: str) -> None:
    """Record the "updated" value of the snapshot just sent."""
    STATE_FILE.write_text(updated, encoding="utf-8")


def post(body: str) -> None:
    """POST line protocol to Grafana Cloud. Raises on any response other than 204."""
    url = os.environ["GC_URL"]
    token = base64.b64encode(f"{os.environ['GC_USER']}:{os.environ['GC_TOKEN']}".encode()).decode()
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Basic {token}", "Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 204:
                raise RuntimeError(f"unexpected HTTP {response.status}")
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code}: {err.read(200).decode('utf-8', 'replace')}") from err


def push_once(dry_run: bool) -> bool:
    """Push the snapshot if it is new. Return True if a push happened or was skipped as already sent."""
    snapshot = load_snapshot()
    if snapshot is None:
        return False
    updated = snapshot.get("updated", "")
    if not dry_run and updated == last_sent():
        _LOGGER.debug("Snapshot %s already sent", updated)
        return True
    body = to_line_protocol(snapshot)
    if not body:
        _LOGGER.warning("No numeric values in snapshot %s", updated)
        return False
    if dry_run:
        print(body)
        return True
    post(body)
    remember_sent(updated)
    _LOGGER.info("Sent %d device point(s) for %s", body.count("\n") + 1, updated)
    return True


def parse_args() -> argparse.Namespace:
    """Parse command line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=int, default=60, help="seconds between checks (default 60)")
    parser.add_argument("--once", action="store_true", help="push once and exit")
    parser.add_argument("--dry-run", action="store_true", help="print the line protocol instead of sending it")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    return parser.parse_args()


def main() -> int:
    """Run the pusher. Return the exit code."""
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.dry_run and not all(os.getenv(k) for k in ("GC_URL", "GC_USER", "GC_TOKEN")):
        _LOGGER.error("Set GC_URL, GC_USER and GC_TOKEN first")
        return 1

    if args.once or args.dry_run:
        try:
            return 0 if push_once(args.dry_run) else 1
        except RuntimeError as err:
            _LOGGER.error("Push failed: %s", err)
            return 1

    while True:
        try:
            push_once(dry_run=False)
        except RuntimeError as err:
            _LOGGER.error("Push failed: %s (retrying next cycle)", err)
        except urllib.error.URLError as err:
            _LOGGER.error("Network error: %s (retrying next cycle)", err.reason)
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _LOGGER.info("Stopped")
