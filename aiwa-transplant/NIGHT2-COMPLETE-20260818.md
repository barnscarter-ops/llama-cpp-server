# Night 2 complete — observed results, 2026-08-18 ~00:45–01:35

Ran from the X870E (`CMB_WORKBENCH`) right after the SN7100 + Realtek card
swap. Supersedes the "nothing here has been run" state of
`NIGHT2-PHASE4-6-FROM-X870E.md`.

## Drive letters, observed

`C:` = 9100 PRO (1676 GB, Windows). `D:` = the moved 2 TB SN7100 (old Z690
`C:`, 1308 GB free). Exactly what the plan predicted.

## Network cutover — done, with one ambush the plan missed

Adapters identified by MAC per `X870E-NETWORK-CUTOVER.md`:

| Adapter | MAC | Role | Config |
|---|---|---|---|
| `HomeFiber` (onboard Realtek) | `10-FF-E0-8A-A0-49` | switch / Internet | static `192.168.1.10/24`, gw `.254`, DNS 8.8.8.8/8.8.4.4 |
| `AIWA Direct` (moved card) | `1C-86-0B-3A-48-FB` | direct link | static `10.110.10.2/30`, no gw, no DNS |

All four verification pings pass. But they did **not** pass at first:

**Tailscale had accepted AIWA's advertised `192.168.1.0/24` subnet route**
(metric 0, beating the on-link route's 256). Every connection the X870E
*initiated* toward the LAN — gateway, AIWA — went into the tunnel and died,
while inbound worked, which looked exactly like a one-way cable fault. The
fresh install had `accept-routes` on. Fix, permanent:

```
tailscale set --accept-routes=false
```

If any future machine sits physically on the home LAN while AIWA advertises
that subnet, it needs the same setting. The old Z690 evidently had it.

Cosmetic: the AT&T gateway `.254` answers ICMP only from on-subnet sources —
a failed gateway ping with working TCP is not a fault.

## Phase 4 — pulled over scp, not Samba. AIWA never modified.

SSH over the direct link worked (key recovered from `D:\Users\carte\.ssh` —
old-profile ACLs needed an elevated copy), so the ⛔ Samba share was never
created and **Phase 6 does not exist**. Phases 1–3 were verified on AIWA
first: `ALL CONTAINERS BACK`, `=== DONE 2026-08-17T23:39:26 ===`.

- **Core set → `C:\aiwa-backups\20260817`** (~9 GB): 4 vzdumps + 4.6 GB host
  tarball + manifest. **All 5 SHA256 match AIWA's own manifest.**
- **840 PRO tree → `C:\aiwa-840pro`** (64.3 GB, 124,526 files). scp exited 1:
  19 files (3 MB) hit Windows path limits — 17 are saved-webpage assets with
  260+ char names, one is a file literally named `nul` (reserved device name,
  can never exist on NTFS normally), one SQL file with an over-long path.
  All 19 were re-pulled as **`C:\aiwa-840pro\_longpath-missing-19.tar.gz`**
  (verified: 19 entries). Full-tree diff against a remote `find` manifest:
  **0 size mismatches, 0 missing** outside that tarball. 124,545 = 124,545.

## Phase 5 — SanDisk skipped, deliberately

Carter's call 2026-08-18: no SanDisk copy. The core set exists in **two**
places (AIWA `/var/lib/vz/dump` + the 9100 PRO), not three. AIWA keeps its
copy until cutover succeeds.

## First-boot items closed tonight

- **Junction live**: `C:\Workspace` → `D:\Workspace`. The pre-existing real
  `C:\Workspace` (interim install work) was **merged into `D:` first** —
  robocopy `/XO` moved 395 newer/unique files, notably the newer
  `brain\agent-memory` repo and `Active\.secrets\pve-token.txt` — then renamed
  to `C:\Workspace._old-20260818` (kept, not deleted).
- **SSH keys**: `id_ed25519_proxmox` + pub copied to `C:\Users\carte\.ssh`,
  ACLs tightened.
- **Scheduled task registered: `Grizzly FB Page Token Renewal`** — fires
  2026-09-07 09:12. Two fixes: node path re-pointed to the hermes-bundled
  node (`C:\Program Files\nodejs` doesn't exist here yet) and a second
  action added — a plain PowerShell message box — so the reminder cannot die
  silently if the SEO-Agents-App script can't run. The other 38 task XMLs
  remain unregistered pending tools (PM2/Python/system Node).

## Still open

1. **Task 5 — prove a vzdump restores.** Unchanged: "a backup that has never
   been test-restored is not a backup." Gates Night 3.
2. Remaining scheduled tasks (38), env vars (`consolidate-keys.ps1`), tool
   reinstalls — gradual.
3. Night 3: Proxmox proof-of-concept on the Z690. Before the installer runs,
   settle which disk is `pve-root` (prep doc names both the 1 TB SN7100 and
   the 256 GB Toshiba — unreconciled).
