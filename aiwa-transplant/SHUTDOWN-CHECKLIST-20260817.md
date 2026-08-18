# Z690 teardown — shutdown checklist

Written 2026-08-17, revised after pulling `6dd16f9`. Observed live on
`CARTERSPC` (the Z690). Covers powering this machine down so the 2 TB SN7100
and the Realtek 2.5 GbE card can move into the X870E. **No AIWA action.**

## State, observed

| | |
|---|---|
| This box | ASUS TUF Z690-PLUS WIFI D4, i5-13600K, 64 GB — the future AIWA |
| `C:` | WD_BLACK SN7100 **2 TB**, 1311 GB free — Windows + `C:\Workspace` |
| `D:` | WD_BLACK SN7100 **1 TB**, 930/931 GB free — effectively empty |
| `E:` | Toshiba KXG50ZNV256G 256 GB "Archive", 237/238 GB free |
| LAN | `HomeFiber` static **192.168.1.10/24** |
| Direct | `AIWA Direct` static **10.110.10.2/30** (Realtek card — moves tonight) |
| Tailscale | 100.124.216.11 |
| X870E | `cmb-workbench`, Win11 Pro activated on the 9100 PRO, LAN 192.168.1.220 DHCP, Tailscale 100.124.41.115 |

## What tonight actually is

Night 1's teardown, deferred. Both parts move together: the 2 TB SN7100 into
**M.2 slot 3 with the HR-10** (fan → `SYS_FAN3`; slot 1 is blocked by the AIO
and the 9100 PRO stays on the board's stock heatsink at 35°C), and the Realtek
2.5 GbE card into the X870E for the AIWA direct link.

**The ESP hazard is cleared.** Windows is already installed and activated on
the 9100 PRO with that drive alone in the machine. Adding the SN7100 now is the
correct order, not a risk.

**This ends the Z690 as a working desktop.** Its Windows lives on the 2 TB. Once
that drive leaves, this box no longer boots — it stops being the rollback and
becomes the AIWA candidate. That is the one-way part of tonight; everything
else is reversible by putting the drive back.

## The AIWA backup is running — it no longer gates the swap

Started 2026-08-17 23:30 CDT, detached on AIWA as `/root/transplant-backup.sh`,
logging to `/var/log/transplant-backup-20260817.log`. It runs vzdump on all
four CTs, tars host state with Docker stopped, and writes checksums. **All of
it happens on AIWA** — this PC is not involved, so powering down mid-run costs
nothing.

Phases 4–6 (pull, SanDisk, revert) deliberately were not started: they need a
temporary Samba share on AIWA that should not sit open unattended. They run
from the X870E after the swap — see `NIGHT2-PHASE4-6-FROM-X870E.md`.

Phase 0 passed every gate. Both drives SMART PASSED; the 840 PRO has **zero
reallocated sectors** at 16,679 hours. Two prep-doc figures turned out wrong —
the 840 PRO holds **65 GB, not 266 GB**, and the drive is healthy rather than
marginal. Details and the unreconciled discrepancy in
`NIGHT2-PHASE0-OBSERVED-20260817.md`.

## Before pressing shut down

1. **Every `C:\Workspace\...` path breaks tomorrow.** The 2 TB becomes `D:` (or
   later) on the X870E while the 9100 PRO keeps `C:`. `CLAUDE.md`, the brain
   bootstrap script, `recall.ps1`, the qwen-submit path, the runbooks and the
   scheduled-task actions all hardcode `C:\Workspace`. Plan to either reassign
   the drive letter to `C:`-adjacent expectations or sweep the paths.
   **Decided: junction.** `mklink /J C:\Workspace D:\Workspace` on the X870E.
   Full first-boot sequence (boot order, junction, .claude, env vars, tasks):
   `X870E-FIRST-BOOT-20260817.md`.
2. **Scheduled tasks do not travel with the drive.** They live in the Windows
   task store on the old install. Re-create on the X870E: `agent-os-kernel`,
   `Grizzly SEO GBP Worker`, `Hermes_Gateway` / `HermesWebUI`,
   `ProxmoxBackup-Nightly`, `RigMonitor`, `Brain Stale Session Watcher`, the
   Grizzly SEO monitor/watchdog/photo-sync/weekly set, and the HCP
   cookie-check/relogin pair.
3. **Commit and push in-flight work.** `C:\Workspace\Active\brain` has a
   modified `tool-failures.md` and **no upstream set on `main`** — it will not
   push without `git branch --set-upstream-to`. Insurance either way, since the
   drive travels intact.
4. **Network order.** Power this PC off and unplug its switch-facing Ethernet
   before the X870E claims `192.168.1.10`. Then on the X870E: switch-facing
   adapter → static `192.168.1.10/24`, gateway `192.168.1.254`, DNS `8.8.8.8` /
   `8.8.4.4`; the moved Realtek card → static `10.110.10.2/30`, **no gateway,
   no DNS**. Verify in order: `192.168.1.254`, name resolution, `192.168.1.12`,
   then `10.110.10.1`. If the direct ping fails, stop at cabling/addressing —
   do not touch AIWA.
5. **Losing this Tailscale node** means RDP to `CARTERSPC` is gone.
   `cmb-workbench` (100.124.41.115) is already up, so remote access survives.
6. `D:` and `E:` are near-empty; nothing to rescue from either tonight.

## Two open doc issues (neither blocks tonight)

- **Two drives are still named as the new AIWA's Proxmox boot disk** — the
  parts table says the 1 TB SN7100 is "wiped, becomes the Proxmox boot drive"
  (line 34), the AIWA table says the 256 GB Toshiba → `pve-root` (line 269).
  The `6dd16f9` correction pass did not touch this. Settle it before the
  installer runs on Night 3.
- **`E:\Media\Grizzly\Curated` holds 93 files / 238 MB**, not the 601 files /
  1.19 GB the prep doc records. Photos are recorded as backed up to Google
  Photos (confirmed 2026-08-11), so not a stop condition — but confirm before
  the Toshiba is wiped.
