# Task 4 — Monday Night Backup Sequence (2026-08-17 → 08-18)

Drafted 2026-08-11, **re-dated 2026-08-13** — the original 08-11 date passed
without the run. **Not yet approved — nothing here runs until Carter says go
on the night.** All commands run via Orca on `aiwa-host` unless marked
`[main PC]`. Approval gates are marked ⛔.

## Decisions baked into this plan

- **Backups land on the 2 TB SN7100, which is `D:` by the time this runs.**
  Revised 2026-08-11, corrected 2026-08-13 for the drive-letter shift. The
  reasoning is unchanged — the 1 TB SN7100 becomes the new AIWA's Proxmox boot
  drive and gets wiped, so nothing durable can live on it — but **Night 1
  (Fri 2026-08-14) renames the target**. The 2 TB SN7100 is `C:` today with
  1166 GB free; it moves to the X870E and comes back as **`D:`** with its data
  intact, while the fresh Samsung 9100 PRO becomes the new `C:`.

  So on Monday night, **write to `D:\`, not `C:\`**. `C:` will be a days-old
  Windows install on a different physical disk. Verify the letter with
  `Get-Volume` before the first robocopy rather than trusting this table —
  if Night 1 slipped and the swap hasn't happened, the target is still `C:`.
  The disk is the same either way: the 2 TB WD_BLACK SN7100.
- **840 PRO (500 GB SATA) retires with the old hardware.** Its 266 GB of live
  data gets copied to main PC `D:\aiwa-840pro\` Monday night. The cutover
  runbook (task 6) will re-home `/mnt/samsung-sata` consumers onto the new
  host's storage.
- **R9700 stays in the Z690** — no GPU moves on swap night.
- **Grizzly photos → Google Photos**, not a drive rescue. **Confirmed backed up
  by Carter 2026-08-11** — the Toshiba (`E:`) is now free to retire on swap
  night with no rescue copy. Not part of Monday.
- **Monday 2026-08-17** is chosen because the Sunday 02:00 RAG refresh and the
  Sunday 03:00 vzdump of CT 102 have both completed — backups capture a fresh,
  quiet state. It also lands after Night 1, so the target drive is settled.

## Copy map (end state Monday night)

| Artifact | Size (est) | AIWA local | Main PC `D:` (2 TB SN7100) | SanDisk 256 GB |
|---|---|---|---|---|
| vzdump ×4 CTs (zstd) | ~10–15 GB | ✔ /var/lib/vz/dump | ✔ | ✔ |
| Host-state tarball | ~8–12 GB | ✔ /var/lib/vz/dump | ✔ | ✔ |
| SHA256SUMS | — | ✔ | ✔ | ✔ |
| 840 PRO data (266 GB) | 266 GB | (source drive itself) | ✔ | ✂ doesn't fit |

The 840 PRO data ends with two copies (the drive itself until cutover + the
2 TB SN7100). The core set ends with three. SanDisk is disconnected at the end.

**Drive-letter warning:** every letter on the main PC moves on Friday 08-14.
The 2 TB SN7100 goes `C:` → `D:`, a fresh 9100 PRO becomes `C:`, and the
Toshiba "Archive" (`E:` today) is pulled and retired that night. So `E:` may
well be free by Monday and land on the SanDisk — which is convenient and also
exactly how the wrong drive gets written. **Run `Get-Volume` first and
substitute the letters you actually observe.** Do not run any phase against a
letter this document asserts.

## Phase 0 — Pre-flight (read-only, can run before Monday)

```
# 0.1 Confirm Sunday jobs completed clean
journalctl -u pvescheduler --since "sun 02:30" | grep -i vzdump | tail -20
ls -lh /var/lib/vz/dump/ | tail -5
# ^ Read the MODE column, not just the names. Phase 4 reads these files over SMB
#   as `mavshare` (uid 1000), not as root. If the .zst files are 0600 root:root the
#   [transplant-ro] share cannot read them and Phase 4 fails with the maintenance
#   window already open. Fix before the night: chmod 0644 on the dump files, or add
#   `force user = root` to the temp share stanza in Phase 4.
# RAG refresh: check hcp export timestamps
ls -l --time-style=long-iso /mnt/samsung-sata/mav-rag/hcp-exports | tail -5

# 0.2 Space checks
df -h / /var/lib/vz /mnt/samsung-sata
# need ≥ ~30 GB free under /var/lib/vz

# 0.3 Docker volume inventory (what the tarball's /var/lib/docker/volumes actually contains)
docker volume ls
docker system df -v | head -40

# 0.3b PM2 process list + environment — THE BLIND SPOT IN THE HOST INVENTORY
pm2 list
pm2 prettylist | head -120      # includes each process's cwd, script, and env
timedatectl
# HOST-CONFIG-PRESERVE.md captures `pm2-root` as a single systemd unit, but never
# recorded *what PM2 resurrects*. Per agent-memory/runbooks/aiwa-deployment.md the
# SEO-Agents-App services (seo-monitor, supabase-sync, mav-bridge, workers) MUST
# carry TZ=America/Chicago in their service environment — the scripts use
# getHours()/getDate() for no-show windows and week_of filing. On a UTC host
# without the pin, no-show detection shifts 5-6 hours and evening runs file posts
# under the wrong week. That failure is silent and surfaces days later.
#
# Capture this list now so the cutover runbook (task 6) can verify each process
# comes back with its env intact. Post-restore gate, from the runbook:
#   node -e "console.log(new Date().toString())"   # must print CST/CDT

# 0.4 SMART quick re-check both drives
smartctl -H /dev/nvme0; smartctl -H /dev/sda

# 0.5 [main PC] confirm drive letters post-Night-1, free space (need ~300 GB), SanDisk present
Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystemLabel, Size, SizeRemaining
# expect: 2 TB SN7100 as D: with ~1166 GB free. If it is still C:, Night 1 has not run.
# NOTE 2026-08-17 00:20: verified on the X870E — one disk present (9100 PRO as C:,
# 1793 GB free). The SN7100 is NOT installed yet. It must go in (M.2 slot 3 — the
# 4060 Ti blocks slot 2) before this phase means anything.

# 0.6 [main PC] is the 2.5 GbE direct link to AIWA up? Decides the Phase 4 path.
Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress, PrefixLength
ping 10.110.10.1
# Want: this PC on 10.110.10.2/30 (no gateway, no DNS) and AIWA answering on
# 10.110.10.1. That is the Realtek 2.5 GbE card out of the Z690. If it answers,
# Phase 4 uses it; if not, Phase 4 falls back to 192.168.1.12 over gigabit.
# Do not spend the maintenance window troubleshooting this link — settle it now.
```

Abort criteria: any SMART FAIL, <30 GB free on AIWA local, <300 GB free on the
2 TB SN7100, or the SN7100 not appearing at the letter the later phases use.

## Phase 1 — vzdump all four CTs (~20 min)

Snapshot mode; nothing stops. Note: LXC 100's mp0 bind mount is **not**
included by vzdump (by design) — that content lives on the 840 PRO and is
covered by Phase 4.

```
vzdump 100 101 102 103 --compress zstd --mode snapshot --storage local \
  --protected 1 \
  --notes-template "pre-transplant full set {{guestname}}"
ls -lh /var/lib/vz/dump/*.zst | tail -8
```

CT 101 is our Orca access path — snapshot mode backs it up while we're using it;
that's fine and expected.

**`--protected 1` is not optional.** `/etc/pve/jobs.cfg` carries a weekly vzdump
of VMID 102 — `sun 03:00`, `keep-last=2`, storage `local`, the same storage this
phase writes to. When that job next runs (2026-08-23 03:00) it prunes CT 102's
backups on `local` down to two, and this manual dump is a candidate. Phase 6
designates the AIWA-local copy as the third copy *and* the task-5 restore source,
held until cutover succeeds — so if cutover slips past Sunday, that copy silently
degrades to two copies without anyone noticing. `--protected 1` exempts these
dumps from retention pruning. The `--notes-template` label does not.

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

tar --xattrs --acls --numeric-owner -czf /var/lib/vz/dump/aiwa-host-state-20260817.tar.gz \
  /etc /root /opt /home /usr/local/bin \
  /var/lib/tailscale /var/lib/samba/private /var/lib/docker/volumes \
  /var/spool/cron 2>/tmp/tar-warnings.txt

# (if stopped) systemctl start docker && docker ps   # verify all 7 back up
tail /tmp/tar-warnings.txt   # expect only "file changed as we read it" class noise if live
```

Secrets note: the five .env files ride inside this tarball. The tarball goes
only to the 2 TB SN7100 and the SanDisk (both physical media in Carter's
possession), never into this repo or any cloud.

## Phase 3 — Checksums on AIWA (~5 min)

```
cd /var/lib/vz/dump && sha256sum vzdump-lxc-1*.tar.zst aiwa-host-state-20260817.tar.gz > SHA256SUMS-20260817.txt
cat SHA256SUMS-20260817.txt
```

## Phase 4 — Stage + pull to main PC `D:` ⛔ one temp config change

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

**Use the direct link if Phase 0.6 said it was up.** The 266 GB copy is the long
pole of the night. `192.168.1.12` reaches AIWA over the switch at gigabit;
`10.110.10.1` is the point-to-point 2.5 GbE link (main PC `10.110.10.2/30` →
AIWA `vmbr1`/`p2p0`), which exists for exactly this kind of bulk transfer. Same
share, same credentials, same files — only the host part of the UNC changes, and
it should take a meaningful bite out of the 45–90 min estimate below. Fall back
to `192.168.1.12` without hesitation if the link didn't come up.

`[main PC]` pull + verify (robocopy restarts cleanly if interrupted):

```
net use T: \\10.110.10.1\transplant-ro /user:mavshare *      # or \\192.168.1.12\ if 0.6 failed
robocopy T:\var\lib\vz\dump D:\aiwa-backups\20260817 vzdump-lxc-*.tar.zst aiwa-host-state-20260817.tar.gz SHA256SUMS-20260817.txt /Z /J /R:2
robocopy T:\mnt\samsung-sata D:\aiwa-840pro /E /Z /J /R:2 /XD "hcp-exports"   # exclude regenerable weekly exports? — confirm on the night; ~45–90 min for 266 GB on gigabit
# verify core set
cd D:\aiwa-backups\20260817; Get-FileHash * -Algorithm SHA256   # compare against SHA256SUMS
```

For the 266 GB tree, robocopy's own per-file verification plus a final
`robocopy /L /E` (list-only diff) pass is the check — hashing 266 GB twice
would add ~an hour for little gain. If you want full hashes anyway, say so and
I'll add a two-sided hash manifest step.

## Phase 5 — SanDisk copy (~10 min) `[main PC]`

Core set only (266 GB doesn't fit on 256 GB):

```
Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystemLabel, Size   # identify the SanDisk's letter first — <S:> below
robocopy D:\aiwa-backups\20260817 <S:>\aiwa-backups\20260817 /E /Z
cd <S:>\aiwa-backups\20260817; Get-FileHash * -Algorithm SHA256   # compare against SHA256SUMS
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
| 4 pull to `D:` (core + 266 GB) | ~60–100 min, unattended |
| 5 SanDisk + verify | ~15 min |
| 6 cleanup | 5 min |
| **Total** | **~2–2.5 h, mostly unattended** |

## Explicit approval gates on the night

1. Start Phase 1 (first state-touching action: vzdump snapshots).
2. Phase 2: stop/restart Docker for a clean Qdrant copy (recommended) — or tar live.
3. Phase 4: add temporary read-only `[transplant-ro]` Samba share (reverted in Phase 6).
4. Phase 4: exclude `hcp-exports` from the 266 GB copy (regenerated weekly) — or include it.
