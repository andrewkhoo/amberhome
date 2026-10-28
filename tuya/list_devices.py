"""List every device on the Tuya Cloud account with its ID and local key."""

import json
import os
import sys
from pathlib import Path

import tinytuya

ENV_FILE = Path(os.environ.get("TUYA_ENV", Path(__file__).resolve().parent.parent / "environment" / "tuya.env"))
OUTPUT = Path(__file__).resolve().parent / "devices.json"
FIELDS = ("id", "name", "key", "category", "product_name")


def load_env(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def main():
    env = load_env(ENV_FILE)
    cloud = tinytuya.Cloud(
        apiRegion=env["APIREGION"],
        apiKey=env["APIKEY"],
        apiSecret=env["APISECRET"],
    )

    devices = cloud.getdevices()
    if not devices:
        sys.exit("No devices returned. Check the region, API subscriptions and app account link.")

    rows = []
    for dev in devices:
        row = {field: dev.get(field) for field in FIELDS}
        row["local_key"] = row.pop("key")
        rows.append(row)
    OUTPUT.write_text(json.dumps(rows, indent=2))

    for row in rows:
        print(f"{row['id']}  {row['name']}  key={row['local_key']}")
    print(f"\nWrote {len(rows)} devices to {OUTPUT}")


if __name__ == "__main__":
    main()
