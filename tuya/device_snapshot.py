"""Write the switch_1 state of every device in the devicelist file to device_states.js."""

import configparser
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import tinytuya

from list_devices import ENV_FILE, load_env

DEVICE_LIST = Path(__file__).resolve().parent / "devicelist"
OUTPUT = Path(__file__).resolve().parent / "device_states.js"
SWITCH_CODE = "switch_1"


def main():
    env = load_env(ENV_FILE)
    cloud = tinytuya.Cloud(
        apiRegion=env["APIREGION"],
        apiKey=env["APIKEY"],
        apiSecret=env["APISECRET"],
    )

    devices = configparser.ConfigParser()
    devices.read(DEVICE_LIST)

    rows = []
    for name in devices.sections():
        device_id = devices[name]["id"]
        code = devices[name].get("code", SWITCH_CODE)
        status = cloud.getstatus(device_id)
        if not status or not status.get("success"):
            sys.exit(f"Could not read status for {name}: {status}")

        state = next((s["value"] for s in status["result"] if s["code"] == code), None)
        rows.append({"name": name, "id": device_id, "switch_1": state})

    snapshot = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "devices": rows}
    OUTPUT.write_text(f"window.DEVICE_STATES = {json.dumps(snapshot, indent=2)};\n")
    print(f"Wrote {len(rows)} devices to {OUTPUT}")


if __name__ == "__main__":
    main()
