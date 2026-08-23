import pathlib
import subprocess
import sys

unit = sys.argv[1]
pid = subprocess.check_output(
    ["systemctl", "show", "-p", "MainPID", "--value", unit], text=True
).strip()
print(f"{unit} pid={pid}")
if not pid or pid == "0":
    raise SystemExit(1)
env = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
keys = (
    "HERMES_PC_URL",
    "PC_HOST",
    "OPENAI_BASE_URL",
    "DEADMAN_URL",
    "PC_ACTIONS_URL",
)
for raw in env:
    if not raw:
        continue
    k, _, v = raw.decode("utf-8", "replace").partition("=")
    if k in keys:
        print(f"{k}={v}")
