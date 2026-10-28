"""Serve the switch_1 state of each device in devicelist as JSON, pulled from the Tuya Cloud on every request.

GET /status returns the states. POST /<name>/on and POST /<name>/off run device_on.py or device_off.py
for that device, then return the result.

Listens on 127.0.0.1 only. nginx proxies /tuya/api/ to it.
"""

import configparser
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import tinytuya

HERE = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("TUYA_ENV", "/etc/tuya.env"))
DEVICE_LIST = HERE / "devicelist"
HOST, PORT = "127.0.0.1", 8766
SWITCH_CODE = "switch_1"
ACTIONS = {"on": "device_on.py", "off": "device_off.py"}


def load_env(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def read_states():
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
            raise RuntimeError(f"Could not read status for {name}: {status}")

        state = next((s["value"] for s in status["result"] if s["code"] == code), None)
        rows.append({"name": name, "switch_1": state})

    return {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "devices": rows}


def known_devices():
    devices = configparser.ConfigParser()
    devices.read(DEVICE_LIST)
    return devices.sections()


def switch_device(name, action):
    result = subprocess.run(
        [sys.executable, str(HERE / ACTIONS[action]), name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


class Handler(BaseHTTPRequestHandler):
    def send_json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/status":
            self.send_error(404)
            return

        try:
            self.send_json(200, read_states())
        except Exception as exc:
            self.send_json(502, {"error": str(exc)})

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        if len(parts) != 2 or parts[1] not in ACTIONS:
            self.send_error(404)
            return

        name, action = parts
        if name not in known_devices():
            self.send_json(404, {"error": f"No device named '{name}'"})
            return

        try:
            ok, output = switch_device(name, action)
        except subprocess.TimeoutExpired:
            ok, output = False, "Timed out waiting for the Tuya Cloud"

        self.send_json(200 if ok else 502, {"ok": ok, "output": output})


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
