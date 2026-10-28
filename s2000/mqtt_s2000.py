"""Monitor all SOLIX S2000 (model AS220) devices in near real time via the Anker MQTT server.

The REST API only gives minimal data for portable power stations. This script logs in
through the anker_solix_api library (same login as poll_s2000.py), connects to the
Anker MQTT server, subscribes to the data topic of every S2000 on the account and keeps
the decoded values per device.

Set credentials via environment:

    export ANKERUSER="you@example.com"
    export ANKERPASSWORD="..."
    export ANKERCOUNTRY="AU"

Run:

    python mqtt_s2000.py                  # stream values, Ctrl+C to stop
    python mqtt_s2000.py --show-every 10  # print a summary every 10 s
    python mqtt_s2000.py --runtime 30     # stop after 30 minutes
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import ClientSession
from anker_solix_api import api, errors
from anker_solix_api.mqtt import AnkerSolixMqttSession

_LOGGER: logging.Logger = logging.getLogger("mqtt_s2000")
_API_LOGGER: logging.Logger = logging.getLogger("anker_solix_api")  # library logs every MQTT publish here

S2000_MODEL = "AS220"  # SOLIX S2000 portable power station
STATUS_REQUEST_INTERVAL = 60  # seconds between explicit status requests
SNAPSHOT_FILE = Path(os.getenv("S2000_SNAPSHOT") or Path(__file__).with_name("s2000_mqtt_latest.json"))
INTERNAL_KEYS = {"last_message", "topics"}  # bookkeeping keys added by the library


def is_s2000(device: dict) -> bool:
    """Return True if the cached device entry is the S2000 power station."""
    if device.get("type") != "pps":
        return False
    return S2000_MODEL in str(device.get("device_pn") or "") or "S2000" in str(
        device.get("alias_name") or device.get("alias") or ""
    )


def find_s2000(myapi: api.AnkerSolixApi) -> dict:
    """Return all S2000 devices in the account keyed by serial number."""
    return {sn: dev for sn, dev in myapi.devices.items() if is_s2000(dev)}


def latest_values(session: AnkerSolixMqttSession, sn: str) -> dict:
    """Return the decoded MQTT values collected for the device."""
    data = dict(session.mqtt_data.get(sn) or {})
    return {k: v for k, v in data.items() if k not in INTERNAL_KEYS}


def save_snapshot(all_values: dict) -> None:
    """Write the latest decoded values of all devices to a JSON file for other tools to read."""
    payload = {
        "updated": datetime.now().astimezone().isoformat(),
        "devices": all_values,
    }
    SNAPSHOT_FILE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def print_values(sn: str, alias: str, values: dict, changed: set) -> None:
    """Print the values of one device, marking those that changed since the last print."""
    _LOGGER.info("---- S2000 %s (%s), %d values ----", sn, alias, len(values))
    for key in sorted(values):
        mark = "*" if key in changed else " "
        _LOGGER.info("%s %-40s %s", mark, key, values[key])


async def run(args: argparse.Namespace) -> None:  # noqa: C901
    """Connect to MQTT and stream values for all S2000 devices."""
    email = os.getenv("ANKERUSER")
    password = os.getenv("ANKERPASSWORD")
    country = os.getenv("ANKERCOUNTRY")
    if not (email and password and country):
        raise SystemExit("Set ANKERUSER, ANKERPASSWORD and ANKERCOUNTRY environment variables first")

    endtime = datetime.now() + timedelta(minutes=args.runtime) if args.runtime else None
    async with ClientSession() as websession:
        myapi = api.AnkerSolixApi(email, password, country, websession, _API_LOGGER)

        # Step 1: authenticate and load the device list (MQTT credentials come from the same login)
        if not await myapi.apisession.async_authenticate():
            _LOGGER.info("Login from cache, validated on first API call")
        await myapi.update_sites()
        await myapi.get_bind_devices()

        devices = find_s2000(myapi)
        if not devices:
            raise SystemExit(f"No S2000 (model {S2000_MODEL}) found for this account")
        for sn, dev in devices.items():
            _LOGGER.info("Found S2000 %s (%s)", sn, dev.get("alias_name") or dev.get("alias") or "no alias")

        # Step 2: connect to the Anker MQTT server
        session = await myapi.startMqttSession()
        if not session:
            raise SystemExit("Could not connect to the Anker MQTT server")
        _LOGGER.info("Connected to MQTT server %s:%s", session.host, session.port)

        # Step 3: subscribe to the data topic of every S2000 (topics need mqtt_info, so build them after connecting)
        topics: set = set()
        for dev in devices.values():
            if data_topic := session.get_topic_prefix(deviceDict=dev):
                topics.add(f"{data_topic}#")
        if not topics:
            raise SystemExit("Could not build MQTT topics for the S2000 devices")
        trigger = set(devices.keys()) if args.realtime else set()
        changed: dict = {sn: set() for sn in devices}
        last_values: dict = {sn: {} for sn in devices}
        msg_count = 0

        def on_message(_session, topic, message, data, model, *_args, **_kwargs) -> None:
            """Count messages and log which topic delivered them."""
            nonlocal msg_count
            msg_count += 1
            if args.debug:
                _LOGGER.debug("MQTT %s model=%s", topic, model)

        # The poller subscribes the topics, keeps the realtime trigger alive and dispatches messages
        poller = asyncio.create_task(
            session.message_poller(
                topics=topics,
                trigger_devices=trigger,
                msg_callback=on_message,
                timeout=args.timeout,
            )
        )
        _LOGGER.info(
            "Subscribed to %d S2000 topic(s) (realtime trigger %s)", len(topics), "on" if args.realtime else "off"
        )

        # Step 4: periodically print/save values and request status; reconnect if needed
        next_status = 0.0
        try:
            while True:
                await asyncio.sleep(args.show_every)
                if endtime and datetime.now() >= endtime:
                    _LOGGER.info("Runtime reached, stopping")
                    break
                if poller.done():
                    _LOGGER.warning("MQTT poller stopped, reconnecting")
                    poller = asyncio.create_task(
                        session.message_poller(
                            topics=topics, trigger_devices=trigger, msg_callback=on_message, timeout=args.timeout
                        )
                    )
                if not session.is_connected():
                    _LOGGER.warning("MQTT disconnected, restarting session")
                    if not (session := await myapi.startMqttSession()):
                        _LOGGER.error("Reconnect failed, retrying next cycle")
                        continue

                now = asyncio.get_running_loop().time()
                if now >= next_status:
                    for dev in devices.values():
                        session.status_request(deviceDict=dev)
                    next_status = now + STATUS_REQUEST_INTERVAL

                all_values: dict = {}
                if msg_count == 0:
                    _LOGGER.info("No MQTT messages yet")
                for sn, dev in devices.items():
                    values = latest_values(session, sn)
                    changed[sn] = {k for k, v in values.items() if last_values[sn].get(k) != v}
                    last_values[sn] = values
                    if values:
                        alias = dev.get("alias_name") or dev.get("alias") or "no alias"
                        print_values(sn, alias, values, changed[sn])
                        all_values[sn] = values
                    else:
                        _LOGGER.info("S2000 %s: no values yet", sn)
                if all_values:
                    save_snapshot(all_values)
        except errors.AnkerSolixError as err:
            _LOGGER.error("Anker error: %s", err)
        finally:
            poller.cancel()
            session.cleanup()
            myapi.mqttsession = None


def parse_args() -> argparse.Namespace:
    """Parse command line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show-every", type=int, default=10, help="seconds between printed summaries (default 10)")
    parser.add_argument("--timeout", type=int, default=60, help="seconds the realtime trigger stays active (default 60)")
    parser.add_argument("--no-realtime", dest="realtime", action="store_false", help="do not send realtime trigger")
    parser.add_argument("--runtime", type=int, default=0, help="stop after this many minutes (default: until Ctrl+C)")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    return parser.parse_args()


if __name__ == "__main__":
    cli_args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if cli_args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    _API_LOGGER.setLevel(logging.DEBUG if cli_args.debug else logging.WARNING)
    try:
        asyncio.run(run(cli_args))
    except KeyboardInterrupt:
        _LOGGER.info("Stopped")
