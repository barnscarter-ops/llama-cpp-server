#!/usr/bin/env python3
"""r9700-watch — live R9700 (CT 210 / AIWA) GPU dashboard from this PC.

Polls amdgpu debugfs on the AIWA host over SSH every POLL seconds and renders
temps / power / clocks / load. No AIWA-side install; read-only.

Usage:  python r9700-watch.py [--interval 2] [--once]
Requires: ssh key root@aiwa (id_ed25519_proxmox) — already in this repo's ops.
"""
import argparse
import os
import re
import subprocess
import sys
import time

SSH = ["ssh", "-i", os.path.expanduser("~/.ssh/id_ed25519_proxmox"),
       "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "root@aiwa"]
CMD = "cat /sys/kernel/debug/dri/0/amdgpu_pm_info; echo ===M===; od -A d -t u2 /sys/class/drm/card0/device/gpu_metrics 2>/dev/null | head -2; echo ===V===; cat /sys/class/drm/card0/device/mem_info_vram_used"

FIELDS = [
    (r"GPU Temperature:\s*([\d.]+) C", "edge_C"),
    (r"GPU Load:\s*(\d+) %", "gfx_load_pct"),
    (r"MEM Load:\s*(\d+) %", "mem_load_pct"),
    (r"([\d.]+) W \(average SoC\)", "soc_W"),
    (r"([\d.]+) MHz \(SCLK\)", "sclk_MHz"),
    (r"([\d.]+) MHz \(MCLK\)", "mclk_MHz"),
    (r"([\d.]+) mV \(VDDGFX\)", "vdd_mV"),
]


def poll():
    out = subprocess.run(SSH + [CMD], capture_output=True, text=True, timeout=15)
    if out.returncode != 0:
        return None, out.stderr.strip()[:120]
    text = out.stdout
    vals = {}
    for pat, key in FIELDS:
        m = re.search(pat, text)
        if m:
            vals[key] = float(m.group(1))
    # gpu_metrics v2.0: u16 words after header(2 hdr + content rev...): offsets in words
    # word[2..7] after the 120/769 header are avg temps in 0.1C:
    # idx2=edge? observed: [120,769,58,67,64,64,59,61] -> edge=5.8? no: x10 => 58.0 edge,67.0 junction,64 mem
    mm = re.search(r"===M===\s*\d+\s+\d+\s+(\d+)\s+(\d+)\s+(\d+)", text)
    if mm:
        vals.setdefault("edge_C", float(mm.group(1)) / 1.0)  # already C*1 observed
        vals["junction_C"] = float(mm.group(2))
        vals["mem_C"] = float(mm.group(3))
    vram = re.search(r"===V===\s*\n?(\d+)", text)
    if vram:
        vals["vram_GB"] = float(vram.group(1)) / 1e9
    return vals, None


def render(v, ts):
    def g(k, fmt="{:.0f}", pad=6):
        return fmt.format(v[k]).rjust(pad) if k in v else "  n/a"
    print(f"\nR9700 @ {ts}   (Ctrl-C to stop)")
    print(f"  edge {g('edge_C')}C   junction {g('junction_C')}C   VRAM {g('mem_C')}C")
    print(f"  power {g('soc_W','{:.0f}')}W   vdd {g('vdd_mV','{:.0f}')}mV")
    print(f"  SCLK {g('sclk_MHz','{:.0f}')}MHz  MCLK {g('mclk_MHz','{:.0f}')}MHz")
    print(f"  GFX load {g('gfx_load_pct')}%   MEM load {g('mem_load_pct')}%   VRAM used {g('vram_GB','{:.1f}')}/34.4 GB")
    # simple headroom flags
    j = v.get("junction_C", 0)
    p = v.get("soc_W", 0)
    m = v.get("mem_C", 0)
    flags = []
    if j >= 100: flags.append("!! JUNCTION CRITICAL")
    elif j >= 95: flags.append("!! JUNCTION HIGH")
    if m >= 95: flags.append("!! VRAM HOT")
    elif m >= 90: flags.append("! VRAM warm")
    if p and p >= 310: flags.append("! at/above power cap")
    if not flags: flags.append("OK")
    print("  status: " + "  ".join(flags))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    try:
        while True:
            v, err = poll()
            if v is None:
                print(f"poll failed: {err}", file=sys.stderr)
            else:
                render(v, time.strftime("%H:%M:%S"))
            if args.once:
                return 0 if v else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
