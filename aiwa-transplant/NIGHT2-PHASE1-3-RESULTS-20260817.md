# Night 2, Phases 1-3 — Observed Results (2026-08-17)

Ran on AIWA via Orca terminal, detached, from `/root/transplant-backup.sh`
(md5 `c745fe8c0e0876c7b43b415f446d52f1`). Log:
`/var/log/transplant-backup-20260817.log`.

Started `2026-08-17T23:30:44-05:00`.

## Phase 1 — vzdump, all four CTs

`PHASE1_RC=0`. All four containers dumped in snapshot mode to `local`
(`/var/lib/vz/dump/`), zstd-compressed:

| CT | Artifact | Size |
|----|----------|------|
| 100 | `vzdump-lxc-100-2026_08_17-23_3*.tar.zst` | (see dir) |
| 101 | `vzdump-lxc-101-2026_08_17-23_3*.tar.zst` | (see dir) |
| 102 | `vzdump-lxc-102-2026_08_17-23_31_18.tar.zst` | 1569 MB |
| 103 | `vzdump-lxc-103-2026_08_17-23_31_48.tar.zst` | 1076 MB |

Snapshot mode — the CTs were never stopped. All four confirmed `running`
afterwards (`pct list`).

## Phase 2 — host state tarball

**`TAR_RC=1`.** This is tar's *warning* exit, not its fatal exit (fatal is 2).
The archive was written completely.

`aiwa-host-state-20260817.tar.gz` — **4.6 GB**.

65 warnings in `/tmp/tar-warnings.txt`, all benign, in four classes:

| Count | Warning | Meaning |
|-------|---------|---------|
| 54 | `Cannot flistxattr: Operation not supported` | `/etc/pve` is pmxcfs, a FUSE fs with no xattr support. `--xattrs` was asked for and politely refused. Nothing skipped. |
| 3 | `Cannot llistxattrat: Operation not supported` | Same cause (`lxc`, `local`, `openvz` under `/etc/pve`). |
| 4 | `socket ignored` | Unix sockets — `/root/.config/orca/daemon/daemon-v23.sock`, `/root/.config/orca/o-1210-b192.sock`, `/root/.pm2/pub.sock`, `/root/.pm2/rpc.sock`. Sockets are runtime objects; they are recreated by their daemons on start and are *correct* to omit. |
| 2 | `Removing leading '/' from member names` / `hard link targets` | Informational. Standard tar behaviour. |
| 1 | **`/home/hermes/.hermes: file changed as we read it`** | **This is what set RC=1.** |

### The one warning that is a real caveat

`/home/hermes/.hermes` was being written while tar read it. It is captured, but
that single path may be internally inconsistent in the archive. Everything else
is clean. It is a hermes agent state/config path, not one of the five `.env`
files and not a CT rootfs — nothing in the migration depends on it being
byte-exact. Noted rather than re-run, because a re-run would cost another
~45 min and another Docker stop for a file that does not gate the cutover.

### Docker stop/start (the only live state change made tonight)

Approved beforehand. `docker ps` recorded 7 containers before the stop;
`systemctl stop docker.socket docker` → tar → `systemctl start docker`.

Verified after: **7 running, `UP=7`, 0 unhealthy, 0 restarting, 0 exited**, and
the before/after name diff printed `ALL CONTAINERS BACK`. The stop fully
reverted.

## Phase 3 — checksums

`/var/lib/vz/dump/SHA256SUMS-20260817.txt` written, 7 lines. The glob
`vzdump-lxc-1*.tar.zst` also matched two older 102 dumps (Aug 09, Aug 16) —
harmless extras, not an error.

Confirmed present in the file: **all 4 of tonight's dumps** (100, 101, 102, 103)
and the host tarball.

`/var/lib/vz/dump/` totals **12 GB**.

## Integrity

`gzip -t` was run against the 4.6 GB host tarball as an independent check that
it decompresses end-to-end. Result recorded at the bottom of this file.

## What this does NOT cover

- **No restore has been proven.** These artifacts are untested until one vzdump
  is actually restored (task 5, not started). "The backup completed" and "the
  backup is good" are different claims; only the first is established.
- Phases 4-6 (pulling these off AIWA onto the 9100 PRO) have not run. See
  `NIGHT2-PHASE4-6-FROM-X870E.md`.
- Everything still lives only on AIWA. There is **one copy**. It is not yet on
  the SN7100 or the SanDisk.

---

**GZIP_TEST_RESULT: `GZIP_RC=0`** — clean. Zero bytes on stderr. The 4.6 GB
archive decompresses end to end, so the `TAR_RC=1` warning exit did not leave a
truncated or corrupt file.
