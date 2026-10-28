"""Print every status code reported by a device listed in the devicelist file."""

import configparser
import sys
from pathlib import Path

import tinytuya

from list_devices import ENV_FILE, load_env

DEVICE_LIST = Path(__file__).resolve().parent / "devicelist"


def main(name):
    env = load_env(ENV_FILE)
    cloud = tinytuya.Cloud(
        apiRegion=env["APIREGION"],
        apiKey=env["APIKEY"],
        apiSecret=env["APISECRET"],
    )

    devices = configparser.ConfigParser()
    devices.read(DEVICE_LIST)
    if name not in devices:
        sys.exit(f"No device named '{name}' in {DEVICE_LIST.name}")
    device_id = devices[name]["id"]

    status = cloud.getstatus(device_id)
    if not status or not status.get("success"):
        sys.exit(f"Could not read status for {name}: {status}")

    for item in status["result"]:
        print(f"{item['code']} = {item['value']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: device_dump.py <device name>")
    main(sys.argv[1])
