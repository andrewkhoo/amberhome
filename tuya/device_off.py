"""Turn off the switch of a device listed in the devicelist file."""

import configparser
import sys
from pathlib import Path

import tinytuya

from list_devices import ENV_FILE, load_env

DEVICE_LIST = Path(__file__).resolve().parent / "devicelist"
SWITCH_CODE = "switch_1"


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
    code = devices[name].get("code", SWITCH_CODE)

    result = cloud.sendcommand(device_id, {"commands": [{"code": code, "value": False}]})
    if not result or not result.get("success"):
        sys.exit(f"Command failed for {name}: {result}")

    print(f"{name}: {code} -> False")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: device_off.py <device name>")
    main(sys.argv[1])
