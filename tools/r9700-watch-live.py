#!/usr/bin/env python3
"""r9700-watch-live — same data as r9700-watch.py but single-line, timestamped,
for leaving running in a visible terminal during a consult session.

Usage: python r9700-watch-live.py [--interval 30]
"""
import os, re, subprocess, sys, time, argparse

SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/id_ed25519_proxmox"),
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "root@aiwa"]
CMD = ("cat /sys/kernel/debug/dri/0/amdgpu_pm_info; echo ===M===; "
       "od -A d -t u2 /sys/class/drm/card0/device/gpu_metrics 2>/dev/null | head -1")


def poll():
    out = subprocess.run(SSH + [CMD], capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return None
    t = out.stdout
    g = lambda p: float(re.search(p, t).group(1)) if re.search(p, t) else None
    edge = g(r"GPU Temperature:\s*([\d.]+)")
    load = g(r"GPU Load:\s*(\d+)")
    pw = g(r"([\d.]+) W \(average SoC\)")
    sclk = g(r"([\d.]+) MHz \(SCLK\)")
    mm = re.search(r"===M===\s*\d+\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)", t)
    jun, mem = (float(mm.group(2)), float(mm.group(3))) if mm else (None, None)
    return edge, jun, mem, pw, sclk, load


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=30)
    a = ap.parse_args()
    print("time      edge jun vram  power  sclk  load")
    while True:
        try:
            v = poll()
            if v:
                e, j, m, p, s, l = v
                print(f"{time.strftime('%H:%M:%S')}  {e:3.0f}C {j:3.0f}C {m:3.0f}C  {p:4.0f}W {s:4.0f}MHz {l:3.0f}%", flush=True)
            else:
                print(f"{time.strftime('%H:%M:%S')}  poll failed", flush=True)
        except KeyboardInterrupt:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
