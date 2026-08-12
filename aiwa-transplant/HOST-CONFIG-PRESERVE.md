# AIWA Host Config — Preserve-by-Hand List (Task 3)

Captured 2026-08-10 via Orca (`aiwa-host`), redacted at source (sed pipeline on the
host; env-file *contents* were never read or copied — only their paths). Raw
captures live in `host-config/`:

- [etc-configs.txt](host-config/etc-configs.txt) — network, .link rules, fstab, smb.conf, storage.cfg, jobs.cfg
- [systemd-units.txt](host-config/systemd-units.txt) — all 11 custom systemd units, verbatim
- [discovery.txt](host-config/discovery.txt) — compose files, env-file paths, timers, identity dirs, docker images, /root and /opt listings

Everything below is host-level state that **vzdump does not capture**. The
fresh-install cutover must recreate or restore each item.

## 1. Network (MUST be rewritten, not copied)

- `/etc/systemd/network/10-lan0.link` and `11-p2p0.link` pin interface names
  `lan0` / `p2p0` by **MAC address** (`a0:10:a3:a8:0e:e1` / `:e2` — the ProDesk
  NICs). On the Z690 these match nothing; copied verbatim, the bridges in
  `/etc/network/interfaces` reference interfaces that never appear and the host
  comes up with no network. **Cutover step: write new .link files with the Z690
  NIC MACs (get them from `ip -br link` on first boot), keeping the names
  lan0/p2p0 so `interfaces` can be restored unchanged.**
- `/etc/network/interfaces`: vmbr0 = 192.168.1.12/24 gw 192.168.1.254 over lan0;
  vmbr1 = 10.110.10.1/30 over p2p0. Restorable as-is once names are pinned.
- Helper script `/root/pin-nic-names.sh` and backup dir
  `/root/nic-pin-backup-20260808-012944` exist from the original pinning work.

## 2. Storage / mounts

- `/etc/fstab` line: `UUID=AAD817C0D8178A29 /mnt/samsung-sata ntfs3
  force,nofail,uid=1000,gid=1000,...` — the 840 PRO by filesystem UUID, so the
  line survives the physical move as long as the drive does. If the 840 PRO
  retires instead, every consumer of `/mnt/samsung-sata` breaks: the `[Proxmox]`
  Samba share, hcp-sync ingest dir `/mnt/samsung-sata/mav-rag/hcp-exports`, and
  LXC 100's mp0 bind mount. **Decided 2026-08-11: the 840 PRO retires; its
  266 GB copies to main PC D: on Monday-night backup (see
  [TASK4-MONDAY-BACKUP-PLAN.md](TASK4-MONDAY-BACKUP-PLAN.md)); cutover re-homes
  /mnt/samsung-sata consumers onto the new host's storage.**
- `/etc/pve/storage.cfg`: stock (local + local-lvm). Nothing custom.

## 3. Samba

- `/etc/samba/smb.conf` shares: `[Proxmox]` (/mnt/samsung-sata/mav-transfer,
  valid user `mavshare`) and `[proxmox-root]`.
- Samba user `mavshare` (uid 1000) lives in the passdb — **the SMB password is
  not in any file we can copy**; recreate with `smbpasswd -a mavshare` at
  cutover (Carter supplies the password).
- ⚠️ **Security flag:** `[proxmox-root]` exports `/root` **read-write with
  `guest ok = yes`, forced to root** — anyone on the LAN can write to the
  hypervisor's root home with no password. Recommend removing or locking this
  share down; at minimum do not recreate it as-is on the new host.

## 4. Custom systemd units (11) + timers

Full unit files in [systemd-units.txt](host-config/systemd-units.txt).

| Unit | Runs | Needs |
|---|---|---|
| hermes-gateway | hermes venv `/opt/hermes-venv`, cwd `/home/hermes/.hermes` | user `hermes`, env `/home/hermes/.hermes/.env` |
| hermes-triage | `/opt/hermes-triage/triage_daemon.py` | same env file |
| hermes-customer-sms | gateway, profile customer-sms | `/home/hermes/.hermes/profiles/customer-sms/.env` |
| hermes-pc-sms | gateway, profile pc-sms | `/home/hermes/.hermes/profiles/pc-sms/.env` |
| pacc-registry | uvicorn on 0.0.0.0:8001 | `/opt/pacc-registry` + its .venv |
| orca-aiwa | `orca-ide serve --port 6768 --pairing-address 192.168.1.12` | /usr/bin/orca-ide, ELECTRON_DISABLE_SANDBOX=1 |
| node_exporter | `/usr/local/bin/node_exporter` | user/group `node_exporter` |
| hcp-catalog-sync (+.timer) | node v22 `/opt/hcp-catalog-sync/sync-catalog.mjs` | env file in /opt, writes `/mnt/samsung-sata/mav-rag/hcp-exports` |
| hcp-estimates-sync (+.timer) | node v22 `/opt/hcp-estimates-sync/sync-estimates.mjs` | same ingest dir; must not overlap catalog-sync |
| mav-pve-storage-metrics (+.timer) | `/usr/local/bin/mav-pve-storage-metrics.sh` | the script itself |
| pm2-root | `pm2 resurrect`, PM2_HOME=/root/.pm2 | global pm2 install + `/root/.pm2` dump |

Cutover implications: recreate users `hermes` and `node_exporter`; install node
v22, pm2, orca-ide; timers (not services) are what get enabled for the three
oneshot jobs.

## 5. Secrets / env files (paths only — contents never captured)

- `/home/hermes/.hermes/.env`
- `/home/hermes/.hermes/profiles/customer-sms/.env`
- `/home/hermes/.hermes/profiles/pc-sms/.env`
- `/opt/hcp-catalog-sync/hcp-catalog-sync.env`
- `/opt/hcp-estimates-sync/hcp-estimates-sync.env`

These ride inside the encrypted-at-rest host tarball (task 4), never in this repo.

## 6. Docker (7 containers, 4 compose stacks)

Compose files: `/root/homelab-dashboard-stack/`, `/opt/homelab-noc-dashboard/`,
`/opt/mav-rag/`, `/opt/pipecat/`. Images (~8.5 GB total: hcp-scraper 2.9G,
pipecat 1.91G, mav-rag-*, homelab-noc-*, qdrant v1.9.4, prom/*) are all locally
built or pullable — the backup captures compose files + volumes (`/var/lib/docker/volumes`),
and images get rebuilt/pulled at cutover. Qdrant pinned at **v1.9.4** — pin it
identically on the new host.

## 7. Identity state (copy the directories, or accept re-enrollment)

- **Tailscale**: `/var/lib/tailscale/tailscaled.state` (+ certs, profile-data).
  Copying preserves the node identity/IP (100.x); otherwise re-auth and update
  anything referencing the old Tailscale IP (fstab comment mentions
  100.124.216.11 for CartersPC).
- **Syncthing**: home `/home/syncthing` — device ID + folder config. Copy it or
  every peer must re-accept a new device.
- **SSH host keys** `/etc/ssh/ssh_host_*`: decision — copy (clients see same
  host) or regenerate (known_hosts churn). Recommend copying since the IP stays
  192.168.1.12.
- **Samba passdb**: `/var/lib/samba/private/` (or recreate `mavshare` manually).

## 8. Scheduled state

- `/etc/pve/jobs.cfg`: the **only** scheduled backup on the host today is
  weekly vzdump of VMID 102 (sun 03:00, zstd, snapshot, keep-last=2, storage
  local). LXCs 100/101/103 have **no scheduled backups at all** — task 4 fixes
  the one-time gap; consider adding a permanent all-CT job on the new host.
- Custom timers: hcp-catalog-sync, hcp-estimates-sync, mav-pve-storage-metrics.
- Root crontab: weekly hcp-scraper run (`/root/hcp-scraper`, `run_scheduled.py`).
- PVE firewall: **no** cluster.fw or host.fw exist — nothing to migrate.

## 9. /usr/local/bin and misc /root artifacts

- `/usr/local/bin/node_exporter`, `/usr/local/bin/mav-pve-storage-metrics.sh` —
  included in the host tarball.
- `/root` (4.6 GB) carries live projects (hcp-scraper, hermes-supervisor-deploy,
  homelab-dashboard-stack, agent-work) plus ~14 old homelab-noc-dashboard
  backup tarballs from 2026-06-08 that could be pruned before backup if size
  ever matters (it doesn't — total footprint is ~33 GB).

## Host tarball manifest (feeds task 4)

```
/etc                          # network, systemd, samba, pve (bind-mounted view), ssh
/root                         # projects, .pm2, crontab via `crontab -l` dump
/opt                          # hermes venvs, hcp-sync, pacc-registry, compose stacks
/home                         # hermes (.env secrets), syncthing identity
/usr/local/bin                # node_exporter, metrics script
/var/lib/tailscale            # node identity
/var/lib/samba/private        # passdb (mavshare)
/var/lib/docker/volumes       # qdrant + dashboard state
/var/spool/cron/crontabs      # root crontab
```
