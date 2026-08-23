#!/bin/bash
PID=$(systemctl show -p MainPID --value hermes-triage)
echo "PID=$PID"
python3 - << PY
import os, pathlib
pid = """$PID""".strip()
env = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
for raw in env:
    if not raw:
        continue
    k, _, v = raw.decode("utf-8", "replace").partition("=")
    if k in ("HERMES_PC_URL", "PC_HOST"):
        print(f"{k}={v}")
    elif k in ("PC_ACTIONS_TOKEN", "HERMES_PC_SENSOR_TOKEN"):
        print(f"{k}=SET len={len(v)}")
PY
