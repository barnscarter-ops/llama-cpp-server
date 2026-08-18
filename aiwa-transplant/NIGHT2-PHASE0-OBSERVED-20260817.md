# Night 2 — Phase 0 observed, and three doc corrections

Run 2026-08-17 ~23:25 CDT via Orca on `aiwa-host`. Read-only. These are
observed values; where they disagree with `AIWA-TRANSPLANT-PREP.md` (including
the `6dd16f9` correction pass from earlier the same day), **the values here are
the later observation.**

## Phase 0 results — all gates pass

| Check | Observed | Gate |
|---|---|---|
| `pve-root` | 96 G, 45 G used, **52 G avail** (47%) | need ≥30 G ✔ |
| `/mnt/samsung-sata` (`/dev/sda2`) | 477 G, **65 G used, 413 G avail (14%)** | see correction 1 |
| SMART `/dev/nvme0` (SN770) | **PASSED** | ✔ |
| SMART `/dev/sda` (840 PRO) | **PASSED** | ✔ |
| 840 PRO Power_On_Hours | 16,679 | see correction 2 |
| 840 PRO Reallocated_Sector_Ct | **0** | ✔ |
| 840 PRO Wear_Leveling_Count | raw 72, normalized 98 | ✔ |
| Guests | 100 `rustdesk`, 101 `orca`, 102 `hcp-mcp-prod`, 103 `mcc-prod` — all running | ✔ |
| Sunday vzdump CT 102 | completed 2026-08-16 03:00, 1.6 G | ✔ |

No abort criteria met.

## Correction 1 — the 840 PRO holds 65 GB, not 266 GB

`AIWA-TRANSPLANT-PREP.md` (line ~299, written in `6dd16f9` earlier today) says
`/mnt/samsung-sata` is "**56% used — 266 GB of live data, 211 GB free**". The
filesystem reports **14% used, 65 GB of data, 413 GB free**.

This matters more than a stray figure. The 266 GB number is what made Phase 4
a 60–100 minute unattended job and what pushed the 840 PRO copy out of the
"do it tonight" bucket. At 65 GB the whole thing is roughly a ten-minute pull
over gigabit, and the SanDisk-doesn't-fit reasoning needs revisiting too —
65 GB fits on a 256 GB stick with room to spare.

I have not reconciled *why* the figures differ. Possibilities: the earlier
reading was of a different mount or included the `hcp-exports` tree before a
prune, or it was `du` on a path rather than `df` on the filesystem. Worth one
command on the night rather than a guess.

## Correction 2 — the 840 PRO is in good health, not marginal

The prep doc frames the drive as a 13-year-old liability and the transplant
notes lean on its age ("a 16,511-hour 840 PRO") as the argument for urgency.
Observed: **zero reallocated sectors, wear-leveling raw 72, SMART PASSED.**
Wear-leveling 72 on a drive rated for far more means it has barely been
written. Age in hours is real; wear is not.

The backup is still worth doing tonight — an unbackuped host is the problem,
not the drive. But the drive is not on the edge of failure, and decisions
shouldn't be made as though it is.

## Correction 3 — hours, minor

16,679 observed vs 16,511 recorded. Consistent with a week of uptime since the
earlier reading. Not an issue; noted so the next reader doesn't treat it as a
discrepancy.

## What was run tonight

Phases 1–3 only, detached on AIWA as `/root/transplant-backup.sh`
(md5 `c745fe8c0e0876c7b43b415f446d52f1`), logging to
`/var/log/transplant-backup-20260817.log`:

1. `vzdump 100 101 102 103 --compress zstd --mode snapshot --storage local`
2. Host-state tarball → `/var/lib/vz/dump/aiwa-host-state-20260817.tar.gz`,
   **with Docker stopped** (Carter approved) and a before/after container diff
3. `sha256sum` → `SHA256SUMS-20260817.txt`

Deliberately **not** run: Phase 4 (pull), Phase 5 (SanDisk), Phase 6 (cleanup).
Those need the temporary `[transplant-ro]` Samba share, which is an unreverted
live config change and should not sit open unattended. They happen from the
X870E after the swap, targeting the 9100 PRO (1793 GB free).

Nothing on AIWA was wiped, restored, or reconfigured. The Docker stop/start is
the only state change, and it is self-reverting within the script.
