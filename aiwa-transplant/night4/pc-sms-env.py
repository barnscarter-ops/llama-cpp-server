import pathlib
import subprocess

p = subprocess.check_output(
    ["systemctl", "show", "-p", "MainPID", "--value", "hermes-pc-sms"], text=True
).strip()
print("pc-sms-pid", p)
env = pathlib.Path(f"/proc/{p}/environ").read_bytes().split(b"\0")
for raw in env:
    if not raw:
        continue
    k, _, v = raw.decode("utf-8", "replace").partition("=")
    if k in ("HERMES_PC_URL", "PC_HOST", "OPENAI_BASE_URL", "DEADMAN_URL"):
        print(f"{k}={v}")
