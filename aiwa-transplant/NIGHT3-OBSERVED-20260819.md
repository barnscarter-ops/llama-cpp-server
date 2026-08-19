# Night 3 — observed results, 2026-08-19 (late night of 08-18)

**Both objectives met. Task 5 PASSED. Night 4 (cutover) is unlocked.**

## The boot saga — write this down for every future install on this board

The ASUS TUF Z690-PLUS refused to boot the SanDisk for ~2 hours. Every
layout was rejected the same way: stick visible as hardware, never offered
as a UEFI boot entry (or selected → dumped straight back into BIOS). What
finally worked, in combination:

1. **Secure Boot → OS Type → "Other OS"** (was "Windows UEFI mode" — the
   silent gatekeeper; nothing non-Microsoft-signed would boot, which also
   explains the historical pain with this same stick on this board)
2. **Ventoy 1.1.17 (GPT)** on the stick + the ISO copied on as a file.
   Straight Rufus was a dead end for this ISO: Proxmox ISOs cannot be
   FAT32-extracted (installer requires the raw layout), so Rufus writes
   them DD-style — the exact layout the board was rejecting.
3. USB-C stick via **USB-A adapter in a rear port** (the C ports hang off a
   controller the firmware doesn't boot-enumerate).
4. `diskpart clean` before the Ventoy install (per prior experience).

Also hit on the way: the 1 TB SN7100's leftover GPT/Windows remnants threw
"Automatic Repair" and a BIOS "corruption detected" gate — both ghosts,
both erased by the installer's wipe.

## Install

- VMD disabled → installer saw the NVMe immediately. Target: 1 TB SN7100.
- **pve-manager 9.2.2, kernel 7.0.2-6-pve — identical to the ProDesk.**
- Layout: pve-root 96G / pve-data thin 794G / swap 8G. 64 GB RAM seen.
- `aiwa-poc.lan` @ **192.168.1.230/24** (plan's original .13 was corrected
  live — it's production orca's static IP; .14/.15 are CTs 102/103).
- NIC pinned `nic0` = Intel I225-V `58:11:22:30:68:48` (igc).

## Task 5 — the restore test

- `vzdump-lxc-100-2026_08_17-23_30_44.tar.zst` + manifest scp'd over;
  **SHA256 verified on the PoC host** before restore.
- `pct restore 200 ... --storage local-lvm` → RC=0, 1.9 GiB at 1.4 GiB/s.
- First start FAILED — **expected class of failure, worth remembering**:
  the CT config carries `mp0: /mnt/samsung-sata/mav-transfer`, a
  ProDesk-only path. `mkdir -p` stub → started clean.
  **Cutover note: CTs with host bind mounts (at least CT 100) need their
  mp paths present (or remapped) on the new host before first start.**
- Running CT: full init, networkd, cron, dbus, rsyslog, **hbbr + hbbs
  (the actual RustDesk services) running**, `systemctl --failed` = 0 units.
- CT 200 stopped and retained as evidence, on vmbr99 (isolated, no
  physical port). Transferred dump deleted from the PoC host.

## State of the board

| Night | Status |
|---|---|
| 1 — X870E build | ✅ |
| 2 — backups, pulled + verified | ✅ |
| 3 — Proxmox PoC + Task 5 restore proof | ✅ **tonight** |
| 4 — cutover | **UNLOCKED** — awaiting Carter's go (taking a few days to soak) |

## Night 4 pre-flight (when ready)

1. Plan the CT bind-mount paths: restore 840 PRO data to the new host (or
   remap mp0) BEFORE first CT start.
2. Final incremental vzdump on AIWA (fresh dumps, not the 08-17 set).
3. Shut ProDesk CTs down → pull dumps → restore all four → move SN770 →
   identity swap to 192.168.1.12 → Tailscale re-point → ProDesk stays
   intact as rollback until the new host runs clean for days.
4. Move the Realtek 2.5GbE card from the ProDesk to the Z690 (direct link).
