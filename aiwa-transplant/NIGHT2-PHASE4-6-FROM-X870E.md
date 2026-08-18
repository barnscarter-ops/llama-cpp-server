# Night 2, Phases 4–6 — run from the X870E after the swap

Phases 1–3 ran on AIWA on 2026-08-17 (see
`NIGHT2-PHASE0-OBSERVED-20260817.md`). What remains is pulling the result off
the host and verifying it. **Nothing here has been run.** Two live changes on
AIWA need Carter's approval at the point marked ⛔.

Target is the X870E's **9100 PRO (`C:`, 1793 GB free)** — not the SN7100. The
core set is ~33 GB and the 840 PRO tree is **65 GB** (observed, not the 266 GB
the prep doc records), so the whole pull is ~100 GB and fits with enormous
room. This is what decoupled the backup from the hardware swap.

## Before anything

```powershell
Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystemLabel, Size, SizeRemaining
```

Substitute the letters you actually observe. After the swap the 9100 PRO
should still be `C:` and the SN7100 arrives as `D:` (or later). Do not run any
step against a letter this document asserts.

Confirm AIWA is reachable on both paths before starting:

```powershell
ping 192.168.1.12
ping 10.110.10.1
```

If the direct link is down because the Realtek card's static config hasn't been
re-applied, that is fine — everything below works over `192.168.1.12`. Do not
alter anything on AIWA to fix it.

## Verify phases 1–3 actually finished

Via Orca on `aiwa-host`:

```
tail -20 /var/log/transplant-backup-20260817.log
ls -lh /var/lib/vz/dump/*2026_08_17* /var/lib/vz/dump/aiwa-host-state-20260817.tar.gz
cat /var/lib/vz/dump/SHA256SUMS-20260817.txt
```

Expect `PHASE1_RC=0`, `TAR_RC=0`, `ALL CONTAINERS BACK`, and `=== DONE`.
If `ALL CONTAINERS BACK` is absent, a Docker container did not return after the
stop/start — compare `/root/docker-before.txt` against `/root/docker-after.txt`
and resolve that **before** trusting the tarball.

## Phase 4 — pull ⛔

⛔ **Approval: add a temporary read-only Samba share on AIWA**, removed in
Phase 6. Read-only and authenticated, strictly safer than the existing
`[proxmox-root]` share.

```
cat >> /etc/samba/smb.conf <<'SMB'
[transplant-ro]
   path = /
   browseable = no
   read only = yes
   valid users = mavshare
   follow symlinks = no
SMB
testparm -s >/dev/null && smbcontrol smbd reload-config
```

Then from the X870E:

```powershell
net use T: \192.168.1.12\transplant-ro /user:mavshare *
robocopy T:\var\lib\vz\dump C:\aiwa-backups\20260817 vzdump-lxc-*.tar.zst aiwa-host-state-20260817.tar.gz SHA256SUMS-20260817.txt /Z /J /R:2
robocopy T:\mnt\samsung-sata C:\aiwa-840pro /E /Z /J /R:2
```

At 65 GB the 840 PRO tree is roughly a ten-minute copy on gigabit, so the
`hcp-exports` exclusion the original plan agonized over is no longer worth the
decision — just take all of it.

Verify the core set against the host's own manifest:

```powershell
cd C:\aiwa-backups\20260817
Get-FileHash * -Algorithm SHA256 | ForEach-Object { "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf) }
Get-Content SHA256SUMS-20260817.txt
```

Every hash must match. **Checksums, not file counts** — that rule exists
because of the SanDisk's silent-corruption failure mode and applies here too.

## Phase 5 — SanDisk offline copy

The core set is ~33 GB. The 840 PRO tree at 65 GB **also fits** on a 256 GB
stick, which the original plan ruled out on the 266 GB figure — worth taking
both now that it is free.

```powershell
Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter, FileSystemLabel, Size   # find the SanDisk, <S:> below
robocopy C:\aiwa-backups\20260817 <S:>\aiwa-backups\20260817 /E /Z
cd <S:>\aiwa-backups\20260817; Get-FileHash * -Algorithm SHA256   # compare to SHA256SUMS
```

Expect 30–60 MB/s and degrading — it is a thumb drive, not an SSD. Then eject
and **physically disconnect** it. Its whole value is being offline.

## Phase 6 — revert ⛔

⛔ **Approval: remove the `[transplant-ro]` stanza** from
`/etc/samba/smb.conf`, then:

```
testparm -s >/dev/null && smbcontrol smbd reload-config
smbclient -L //192.168.1.12 -U mavshare   # confirm transplant-ro is gone
net use T: /delete                        # [X870E]
```

AIWA keeps its local copy in `/var/lib/vz/dump` until cutover succeeds — that
is the third copy and the restore source for task 5.

## Then, and only then

Night 2 is complete when the core set exists in three places with matching
checksums and the SanDisk is unplugged. **Task 5 — prove a vzdump actually
restores — still has not been done, and a backup that has never been
test-restored is not a backup.** Night 3 should not start before it.
