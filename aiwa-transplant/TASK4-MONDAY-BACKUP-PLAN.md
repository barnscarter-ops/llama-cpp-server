# Task 4 — Monday Night Backup Sequence (2026-08-11 → 08-12)

Drafted 2026-08-11. **Not yet approved — nothing here runs until Carter says go
on the night.** All commands run via Orca on `aiwa-host` unless marked
`[main PC]`. Approval gates are marked ⛔.

## Decisions baked into this plan

- **Backups land on C:, not D:.** Revised 2026-08-11: the 1 TB SN7100 (D:) is
  being repurposed as the new AIWA's boot drive and will be wiped, so nothing
  durable can live there. C: has 1211 GB free and moves to the X870E intact.
- **840 PRO (500 GB SATA) retires with the old hardware.** Its 266 GB of live
  data gets copied to main PC `C:\aiwa-840pro\` Monday night. The cutover
  runbook (task 6) will re-home `/mnt/samsung-sata` consumers onto the new
  host's storage.
- **R9700 stays in the Z690** — no GPU moves on swap night.
- **Grizzly photos → Google Photos**, not a drive rescue. **Confirmed backed up
  by Carter 2026-08-11** — the Toshiba (`E:`) is now free to retire on swap
  night with no rescue copy. Not part of Monday.
- Monday is chosen because the Sunday 02:00 RAG refresh and the sun 03:00
  vzdump of CT 102 have both completed — backups capture a fresh, quiet state.

## Copy map (end state Monday night)

| Artifact | Size (est) | AIWA local | Main PC C: | SanDisk 256 GB |
|---|---|---|---|---|
| vzdump ×4 CTs (zstd) | ~10–15 GB | ✔ /var/lib/vz/dump | ✔ | ✔ |
| Host-state tarball | ~8–12 GB | ✔ /var/lib/vz/dump | ✔ | ✔ |
| SHA256SUMS | — | ✔ | ✔ | ✔ |
| 840 PRO data (266 GB) | 266 GB | (source drive itself) | ✔ | ✂ doesn't fit |

The 840 PRO data ends with two copies (the drive itself until cutover + C:).
The core set ends with three. SanDisk is disconnected at the end.

**Drive-letter warning:** on the main PC, `E:` is currently the **Toshiba
"Archive"** drive, not the SanDisk. Confirm the SanDisk's actual letter when it
is plugged in (Phase 5) and substitute it — do not run Phase 5 against E:
blind.

## Phase 0 — Pre-flight (read-only, can run before Monday)

```
# 0.1 Confirm Sunday jobs completed clean
journalctl -u pvescheduler --since "sun 02:30" | grep -i vzdump | tail -20
ls -lh /var/lib/vz/dump/ | tail -5
# RAG refresh: check hcp export timestamps
ls -l --time-style=long-iso /mnt/samsung-sata/mav-rag/hcp-exports | tail -5

# 0.2 Space checks
df -h / /var/lib/vz /mnt/samsung-sata
# need ≥ ~30 GB free under /var/lib/vz

# 0.3 Docker volume inventory (what the tarball's /var/lib/docker/volumes actually contains)
docker volume ls
docker system df -v | head -40

# 0.4 SMART quick re-check both drives
smartctl -H /dev/nvme0; smartctl -H /dev/sda

# 0.5 [main PC] free space on C: (need ~300 GB) and SanDisk present
Get-PSDrive C | Select-Object Free
```

Abort criteria: any SMART FAIL, <30 GB free on AIWA local, <300 GB free on C:.

## Phase 1 — vzdump all four CTs (~20 min)

Snapshot mode; nothing stops. Note: LXC 100's mp0 bind mount is **not**
included by vzdump (by design) — that content lives on the 840 PRO and is
covered by Phase 4.

```
vzdump 100 101 102 103 --compress zstd --mode snapshot --storage local \
  --notes-template "pre-transplant full set {{guestname}}"
ls -lh /var/lib/vz/dump/*.zst | tail -8
```

CT 101 is our Orca access path — snapshot mode backs it up while we're using it;
that's fine and expected.

## Phase 2 — Host-state tarball (~15 min) ⛔ contains one approval item

Manifest is from [HOST-CONFIG-PRESERVE.md](HOST-CONFIG-PRESERVE.md). The one
live-state question: `/var/lib/docker/volumes` copied while containers run is
only crash-consistent (Qdrant is the concern).

⛔ **Approval on the night: stop Docker for ~5 minutes during the tar** (clean
Qdrant state), then restart. If declined, we tar live and accept
crash-consistent volumes.

```
crontab -l > /root/crontab-root-backup.txt

# (if approved) systemctl stop docker

tar --xattrs --acls --numeric-owner -czf /var/lib/vz/dump/aiwa-host-state-20260811.tar.gz \
  /etc /root /opt /home /usr/local/bin \
  /var/lib/tailscale /var/lib/samba/private /var/lib/docker/volumes \
  /var/spool/cron 2>/tmp/tar-warnings.txt

# (if stopped) systemctl start docker && docker ps   # verify all 7 back up
tail /tmp/tar-warnings.txt   # expect only "file changed as we read it" class noise if live
```

Secrets note: the five .env files ride inside this tarball. The tarball goes
only to C: and the SanDisk (both physical media in Carter's possession), never
into this repo or any cloud.

## Phase 3 — Checksums on AIWA (~5 min)

```
cd /var/lib/vz/dump && sha256sum vzdump-lxc-1*.tar.zst aiwa-host-state-20260811.tar.gz > SHA256SUMS-20260811.txt
cat SHA256SUMS-20260811.txt
```

## Phase 4 — Stage + pull to main PC C: ⛔ one temp config change

The existing `[Proxmox]` share only exposes `mav-transfer`, so the rest of the
840 PRO and the dump dir aren't reachable from the main PC.

⛔ **Approval on the night: add a temporary read-only Samba share** (removed in
Phase 6):

```
cat >> /etc/samba/smb.conf <<'EOF'
[transplant-ro]
   path = /
   browseable = no
   read only = yes
   valid users = mavshare
   follow symlinks = no
EOF
testparm -s >/dev/null && smbcontrol smbd reload-config
```

(Read-only + authenticated — strictly safer than the existing `[proxmox-root]`
share. Alternative if declined: stage everything through `mav-transfer`, which
double-writes 30 GB to the 840 PRO and can't reach the other 840 PRO dirs
without moving them first.)

`[main PC]` pull + verify (robocopy restarts cleanly if interrupted):

```
net use T: \\192.168.1.12\transplant-ro /user:mavshare *
robocopy T:\var\lib\vz\dump C:\aiwa-backups\20260811 vzdump-lxc-*.tar.zst aiwa-host-state-20260811.tar.gz SHA256SUMS-20260811.txt /Z /J /R:2
robocopy T:\mnt\samsung-sata C:\aiwa-840pro /E /Z /J /R:2 /XD "hcp-exports"   # exclude regenerable weekly exports? — confirm on the night; ~45–90 min for 266 GB on gigabit
# verify core set
cd C:\aiwa-backups\20260811; Get-FileHash * -Algorithm SHA256   # compare against SHA256SUMS
```

For the 266 GB tree, robocopy's own per-file verification plus a final
`robocopy /L /E` (list-only diff) pass is the check — hashing 266 GB twice
would add ~an hour for little gain. If you want full hashes anyway, say so and
I'll add a two-sided hash manifest step.

## Phase 5 — SanDisk copy (~10 min) `[main PC]`

Core set only (266 GB doesn't fit on 256 GB):

```
Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystemLabel, Size   # identify the SanDisk's letter first — <S:> below
robocopy C:\aiwa-backups\20260811 <S:>\aiwa-backups\20260811 /E /Z
cd <S:>\aiwa-backups\20260811; Get-FileHash * -Algorithm SHA256   # compare against SHA256SUMS
# then eject and physically disconnect the SanDisk
```

## Phase 6 — Cleanup + revert ⛔

```
# remove the temp share stanza added in Phase 4, then:
testparm -s >/dev/null && smbcontrol smbd reload-config
smbclient -L //192.168.1.12 -U mavshare   # confirm transplant-ro gone
```

AIWA keeps its local copy in /var/lib/vz/dump until cutover succeeds — that's
the third copy of the core set and the restore source for task 5.

## Timeline estimate

| Phase | Duration |
|---|---|
| 0 pre-flight | 10 min |
| 1 vzdump ×4 | ~20 min |
| 2 host tarball | ~15 min (+5 if Docker stopped) |
| 3 checksums | 5 min |
| 4 pull to C: (core + 266 GB) | ~60–100 min, unattended |
| 5 SanDisk + verify | ~15 min |
| 6 cleanup | 5 min |
| **Total** | **~2–2.5 h, mostly unattended** |

## Explicit approval gates on the night

1. Start Phase 1 (first state-touching action: vzdump snapshots).
2. Phase 2: stop/restart Docker for a clean Qdrant copy (recommended) — or tar live.
3. Phase 4: add temporary read-only `[transplant-ro]` Samba share (reverted in Phase 6).
4. Phase 4: exclude `hcp-exports` from the 266 GB copy (regenerated weekly) — or include it.
