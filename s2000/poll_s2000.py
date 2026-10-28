"""Poll the Anker cloud API for a SOLIX S2000 (model AS220) portable power station.

Authentication is handled by the anker_solix_api library (ECDH key exchange,
AES-encrypted password, token caching). Set credentials via environment:

    export ANKERUSER="you@example.com"
    export ANKERPASSWORD="..."
    export ANKERCOUNTRY="AU"          # ISO country code of the Anker account

Run:

    python poll_s2000.py                 # poll every 60 s until Ctrl+C
    python poll_s2000.py --once          # single poll and exit
    python poll_s2000.py --restart-auth  # drop cached login token and log in again
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from aiohttp import ClientSession
from anker_solix_api import api, errors

_LOGGER: logging.Logger = logging.getLogger("poll_s2000")

S2000_MODEL = "AS220"  # SOLIX S2000 portable power station
MIN_INTERVAL = 60  # cloud data refreshes about once per minute, polling faster gives no benefit
DETAILS_INTERVAL = 15 * 60  # device settings (TOU plan, attributes) change rarely
SNAPSHOT_FILE = Path(__file__).with_name("s2000_latest.json")


def is_s2000(device: dict) -> bool:
    """Return True if the cached device entry is the S2000 power station."""
    if device.get("type") != "pps":
        return False
    return S2000_MODEL in str(device.get("device_pn") or "") or "S2000" in str(
        device.get("alias_name") or ""
    )


def s2000_devices(myapi: api.AnkerSolixApi) -> dict:
    """Return all S2000 devices from the api cache keyed by serial number."""
    return {sn: dev for sn, dev in myapi.devices.items() if is_s2000(dev)}


def save_snapshot(devices: dict) -> None:
    """Write the latest S2000 data to a JSON file for other tools to read."""
    payload = {"updated": datetime.now().astimezone().isoformat(), "devices": devices}
    SNAPSHOT_FILE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def print_summary(devices: dict) -> None:
    """Print the full S2000 data as JSON for each device found."""
    if not devices:
        _LOGGER.warning("No S2000 device found in this account (check device list / serial)")
        return
    for sn, dev in devices.items():
        _LOGGER.info("S2000 %s (%s)\n%s", sn, dev.get("alias_name", "-"), json.dumps(dev, indent=2, default=str))


async def poll_once(myapi: api.AnkerSolixApi, refresh_details: bool) -> dict:
    """Run one poll cycle and return the S2000 device data."""
    await myapi.update_sites()
    if refresh_details:
        await myapi.update_device_details()
    devices = s2000_devices(myapi)
    save_snapshot(devices)
    return devices


async def run(args: argparse.Namespace) -> None:
    """Authenticate, then poll the S2000 in a loop."""
    email = os.getenv("ANKERUSER")
    password = os.getenv("ANKERPASSWORD")
    country = os.getenv("ANKERCOUNTRY")
    if not (email and password and country):
        raise SystemExit("Set ANKERUSER, ANKERPASSWORD and ANKERCOUNTRY environment variables first")

    interval = max(MIN_INTERVAL, args.interval)
    async with ClientSession() as websession:
        myapi = api.AnkerSolixApi(email, password, country, websession, _LOGGER)

        # Step 1: authenticate explicitly so bad credentials fail fast with a clear message
        if args.restart_auth:
            await myapi.apisession.async_authenticate(restart=True)
        if not await myapi.apisession.async_authenticate():
            raise SystemExit("Login to Anker cloud failed, check credentials and country code")
        _LOGGER.info("Logged in as %s", myapi.apisession.nickname or email)

        # Step 2: first full poll including device details
        last_details = 0.0
        devices = await poll_once(myapi, refresh_details=True)
        last_details = asyncio.get_running_loop().time()
        print_summary(devices)
        if args.once:
            return

        # Step 3: poll in a loop, handling token expiry and cloud rate limits
        while True:
            await asyncio.sleep(interval)
            try:
                now = asyncio.get_running_loop().time()
                refresh = now - last_details >= DETAILS_INTERVAL
                devices = await poll_once(myapi, refresh_details=refresh)
                if refresh:
                    last_details = now
                for sn, dev in devices.items():
                    _LOGGER.info(
                        "%s %s: %s", datetime.now().strftime("%H:%M:%S"), sn, dev.get("alias_name", "-")
                    )
            except (errors.AuthorizationError, errors.TokenKickedOutError):
                _LOGGER.warning("Token rejected, logging in again")
                await myapi.apisession.async_authenticate(restart=True)
            except errors.RequestLimitError:
                _LOGGER.warning("Endpoint request limit reached, backing off 5 minutes")
                await asyncio.sleep(300)
            except errors.AnkerSolixError as err:
                _LOGGER.error("Poll failed: %s (retrying next cycle)", err)


def parse_args() -> argparse.Namespace:
    """Parse command line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=int, default=MIN_INTERVAL, help="seconds between polls (min 60)")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    parser.add_argument("--restart-auth", action="store_true", help="discard cached token and log in again")
    parser.add_argument("--debug", action="store_true", help="enable library debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if cli_args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run(cli_args))
    except KeyboardInterrupt:
        _LOGGER.info("Stopped")
