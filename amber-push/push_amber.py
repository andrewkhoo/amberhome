"""Push Amber Electric prices to Grafana Cloud as Influx line protocol.

Fetches /v1/sites/{site}/prices/current, one point per interval and channel, and posts them to the
same Grafana Cloud endpoint as s2000/grafana_push_s2000.py. Credentials are read from
environment/grafana.env (GC_URL, GC_USER, GC_TOKEN) and environment/amber.env (one section per site,
with AMBER_TOKEN and AMBER_SITE). Set GRAFANA_ENV_FILE and AMBER_ENV_FILE to use other paths; the
server uses /etc/grafana.env and /etc/amber.env.

A batch whose body is identical to the last one sent is skipped. Forecast intervals change between
fetches, so most runs send something new.

Grafana Cloud rejects the whole request with HTTP 400 if any point is more than about 120 minutes old
or more than about 10 minutes in the future. Intervals outside that window are dropped before sending.

Run:

    python push_amber.py --section ryde              # push every 300 s until Ctrl+C
    python push_amber.py --section ryde --once       # push once and exit
    python push_amber.py --section ryde --dry-run    # print the line protocol, send nothing
"""

from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOGGER: logging.Logger = logging.getLogger("push_amber")

ENV_DIR = Path(__file__).resolve().parents[1] / "environment"
GRAFANA_ENV = Path(os.getenv("GRAFANA_ENV_FILE") or ENV_DIR / "grafana.env")
AMBER_ENV = Path(os.getenv("AMBER_ENV_FILE") or ENV_DIR / "amber.env")
AMBER_URL = "https://api.amber.com.au/v1/sites/{site}/prices/current?next={next}&previous={previous}&resolution={resolution}"
MEASUREMENT = "amber_price"
STATE_DIR = Path(__file__).resolve().parent  # one ".push_amber_last_<section>" file per site holds the sha256 of its last body
TIMEOUT = 20  # seconds for one HTTP request
MAX_AGE = timedelta(minutes=115)  # Grafana Cloud rejects older points (observed limit about 120 min)
MAX_AHEAD = timedelta(minutes=10)  # and points further in the future (observed limit about 10 min)


def load_grafana_env() -> dict[str, str]:
    """Read KEY=VALUE pairs from grafana.env, ignoring blank lines and comments."""
    values = {}
    for line in GRAFANA_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


def load_amber_section(section: str) -> tuple[str, str]:
    """Return (site, token) from the named section of amber.env."""
    cp = configparser.ConfigParser()
    cp.read(AMBER_ENV, encoding="utf-8")
    if section not in cp:
        raise SystemExit(f"No [{section}] section in {AMBER_ENV}; found {cp.sections()}")
    return cp[section]["AMBER_SITE"], cp[section]["AMBER_TOKEN"]


def fetch_prices(site: str, token: str, next_intervals: int, previous_intervals: int, resolution: int) -> list[dict]:
    """Fetch the current and forecast price intervals for a site, at the given resolution in minutes."""
    url = AMBER_URL.format(site=site, next=next_intervals, previous=previous_intervals, resolution=resolution)
    request = urllib.request.Request(
        url,
        headers={"accept": "*/*", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def start_time(interval: dict) -> datetime:
    """Parse an interval's startTime. fromisoformat rejects a bare Z before Python 3.11."""
    return datetime.fromisoformat(interval["startTime"].replace("Z", "+00:00"))


def in_window(interval: dict, now: datetime) -> bool:
    """True if the interval is recent enough for Grafana Cloud to accept."""
    return now - MAX_AGE <= start_time(interval) <= now + MAX_AHEAD


def to_line_protocol(site: str, intervals: list[dict]) -> str:
    """Build one line per interval and channel. Tags are site, channel and interval type."""
    now = datetime.now(timezone.utc)
    lines = []
    for interval in intervals:
        if not in_window(interval, now):
            continue
        ts_ns = int(start_time(interval).timestamp() * 1_000_000_000)
        fields = [f"perKwh={float(interval['perKwh'])!r}", f"spotPerKwh={float(interval['spotPerKwh'])!r}"]
        if "renewables" in interval:
            fields.append(f"renewables={float(interval['renewables'])!r}")
        tags = f"site={site},channel={interval['channelType']},type={interval['type']}"
        lines.append(f"{MEASUREMENT},{tags} {','.join(fields)} {ts_ns}")
    return "\n".join(lines)


def state_file(section: str) -> Path:
    """Return the file that holds the hash of the last body sent for this section."""
    return STATE_DIR / f".push_amber_last_{section}"


def last_sent(section: str) -> str | None:
    """Return the hash of the last body sent for this section, if any."""
    try:
        return state_file(section).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def post(body: str, env: dict[str, str]) -> None:
    """POST line protocol to Grafana Cloud. Raises on any response other than 204."""
    token = base64.b64encode(f"{env['GC_USER']}:{env['GC_TOKEN']}".encode()).decode()
    request = urllib.request.Request(
        env["GC_URL"],
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


def push_once(args: argparse.Namespace, grafana_env: dict[str, str]) -> bool:
    """Fetch and push one batch. Return True if it was sent or skipped as unchanged."""
    site, token = load_amber_section(args.section)
    intervals = fetch_prices(site, token, args.next, args.previous, args.resolution)
    body = to_line_protocol(site, intervals)
    if not body:
        _LOGGER.warning("No intervals returned for site %s", site)
        return False
    if args.dry_run:
        print(body)
        return True
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest == last_sent(args.section):
        _LOGGER.debug("Batch unchanged since last push")
        return True
    post(body, grafana_env)
    state_file(args.section).write_text(digest, encoding="utf-8")
    _LOGGER.info("Sent %d point(s) for site %s", body.count("\n") + 1, site)
    return True


def parse_args() -> argparse.Namespace:
    """Parse command line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--section", default="ryde", help="[section] of environment/amber.env (default ryde)")
    parser.add_argument("--next", type=int, default=432, help="forecast intervals to fetch (default 432)")
    parser.add_argument("--previous", type=int, default=60, help="past intervals to fetch (default 60)")
    parser.add_argument("--resolution", type=int, default=5, help="interval length in minutes (default 5)")
    parser.add_argument("--interval", type=int, default=300, help="seconds between pushes (default 300)")
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
    grafana_env = load_grafana_env()
    if not args.dry_run and not all(grafana_env.get(k) for k in ("GC_URL", "GC_USER", "GC_TOKEN")):
        _LOGGER.error("GC_URL, GC_USER and GC_TOKEN must be set in %s", GRAFANA_ENV)
        return 1

    if args.once or args.dry_run:
        try:
            return 0 if push_once(args, grafana_env) else 1
        except (RuntimeError, urllib.error.URLError) as err:
            _LOGGER.error("Failed: %s", err)
            return 1

    while True:
        try:
            push_once(args, grafana_env)
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
