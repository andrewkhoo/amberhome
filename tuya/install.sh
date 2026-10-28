#!/usr/bin/env bash
# Set up a virtual environment in tuya/ with tinytuya installed.
set -euo pipefail

cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install tinytuya

echo
echo "Done. Run the device listing with:"
echo "  .venv/bin/python list_devices.py"
