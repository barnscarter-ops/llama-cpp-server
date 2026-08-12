# AIWA Inventory — 2026-08-10 (tasks 1–2 of transplant prep)

All data below observed live via Orca terminal on `aiwa-host` (192.168.1.12),
2026-08-10 ~22:45 CDT. Read-only commands only. Nothing on the host was changed
(three scratch files under `/tmp` were created and removed).

## Host

- Proxmox VE 9.2.0, pve-manager 9.2.2, kernel 7.0.2-6-pve
- Hostname `AIWA`, uptime 5 days at time of capture
- Tailscale active: 100.87.155.47 (tailscaled serving 443/3000 on that address)

## Network (as configured — critical for cutover)

`/etc/network/interfaces` references **custom NIC names `lan0` and `p2p0`** —
these are renamed interfaces (physical NICs show as `enp4s0`, `enp5s0`, `nic0`,
`wlp11s0`). The rename mechanism (systemd `.link` files or udev rules) has NOT
been captured yet — task 3. On the Z690 the interface will be different silicon
with a different MAC; the rename rules must be rewritten, not copied.

- `vmbr0`: 192.168.1.12/24, gw 192.168.1.254, bridge-ports `lan0`
- `vmbr1`: 10.110.10.1/30, bridge-ports `p2p0` (point-to-point link)
- Docker bridges: docker0, br-0cf922b58a17 (172.19/16), br-dfba11f87cbb (172.18/16)

## Guests — 4 LXC containers, zero VMs (`qm list` empty)

| VMID | Name | IP | Disk (thin LV) | Actual data | Notes |
|---|---|---|---|---|---|
| 100 | rustdesk | dhcp | 4G | 2.11G (52.84%) | ubuntu, unpriv, TUN device; **bind mount** `mp0: /mnt/samsung-sata/mav-transfer` |
| 101 | orca | 192.168.1.13 | 40G | 4.96G (12.40%) | debian, unpriv, onboot |
| 102 | hcp-mcp-prod | 192.168.1.14 | 40G | 6.18G (15.46%) | debian, unpriv, onboot |
| 103 | mcc-prod | 192.168.1.15 | 40G | 4.40G (10.99%) | debian, unpriv, onboot |

All four `onboot: 1`, `nesting=1`, static MACs recorded in configs (vzdump
preserves them). **vzdump does not back up bind-mount content** — LXC 100's
`mav-transfer` mount rides on the Samsung SATA drive.

## Storage

- `storage.cfg`: only `local` (dir, /var/lib/vz) and `local-lvm` (thinpool pve/data)
- NVMe (WD SN770 2TB): pve-root 96G XFS (45G used, 47%), pve-swap 8G,
  pve-data thin pool 1710G
- **Real `pve-data` usage: 1.03% ≈ 17.6 GB** (sum of the four thin LVs above).
  The 0.12%/~2GB figure in the prep note is wrong/stale.
- SATA (Samsung 840 PRO 512GB): **NTFS**, mounted `/mnt/samsung-sata`,
  **266G used / 211G free (56% used)** — not "6% used" as the prep note said.
  Contents: `mav-rag/` (hcp-exports + screenshots, fed weekly by cron),
  `mav-transfer/` (Samba share + LXC 100 bind mount), `SOPDEV01-ChrisBackup/`,
  `sync/` (owned by syncthing), `WW/`, `$RECYCLE.BIN`.

## SMART verdicts

**WD SN770 2TB (nvme0): HEALTHY — effectively new.** PASSED; 0% used;
195 power-on hours; 15 power cycles; 0 media/integrity errors; 0 error-log
entries; spare 100%; 26°C. 5 unsafe shutdowns (consistent with past TDR/power
events, no damage indicated). Safe to be the sole copy until dumps exist.

**Samsung 840 PRO 512GB (sda): HEALTHY for its age, with one caveat.** PASSED;
16,511 power-on hours; 2,928 power cycles; wear-leveling 98% life remaining
(71 avg erase cycles); 0 reallocated sectors; 0 uncorrectable errors; 0 ECC
errors; ~8.3 TB lifetime writes. Caveat: **CRC_Error_Count raw = 22**
(historical SATA interface/cable errors, normalized 099). Not a media problem,
but recheck this counter after the drive is re-cabled in the new chassis — a
rising count there means cable/port, and it is the primary dump target.
No self-test has ever been run on either drive (deliberately not started —
live host, ask-first rule).

## Backup sizing — the real numbers

| Component | Size (observed) |
|---|---|
| LXC data, 4 guests (thin-LV actual) | ~17.7 GB raw → est. 8–12 GB as zstd vzdump |
| /var/lib/docker (excl. container rootfs overlays) | 5.2 GB |
| /root (incl. hcp-scraper etc.) | 4.6 GB |
| /opt (Orca) | 2.1 GB |
| /var/lib/vz (templates/ISOs) | 3.3 GB |
| /home | 247 MB |
| /etc | 5 MB |
| **Host-config + guest total (worst case, uncompressed)** | **~33 GB** |

**Verdict: everything fits everywhere.** 840 PRO free 211G ✔, main-PC `D:`
930G ✔, SanDisk 256G stick ✔ — with huge margin. The 256GB constraint is a
non-issue for the dumps themselves.

**Open question the prep note didn't anticipate:** the 840 PRO carries 266 GB
of live data (mav-rag exports, mav-transfer share, Chris backup, syncthing
folder). The prep note says the drive is "backup source, then retire" — if it
retires, that 266 GB needs a decided destination; it does NOT fit on the
SanDisk stick and is separate from the vzdump plan. Needs Carter's call.

## What runs on the HOST (vzdump captures none of this) — task 3 seed list

Docker containers (docker.service on the host):
`mav-rag-api` (8181), `mav-rag-qdrant` (qdrant v1.9.4, 6333), `mav-rag-ingest`,
`voice-pipecat` (7860), `mav-console-dashboard` (3010), `prometheus` (9090),
`bgw-exporter`. Docker volumes/config under /var/lib/docker (5.2G).

Custom/host systemd services (from /etc/systemd/system + running set):
`hermes-gateway`, `hermes-triage`, `hermes-customer-sms`, `hermes-pc-sms`
(ports 3013/3014/3015 + 8642 on the tailscale IP), `pacc-registry`,
`pm2-root`, `node_exporter` (9100), `orca-aiwa` (orca-ide on 6768),
`syncthing@syncthing` (22000/8384), `hcp-catalog-sync`, `hcp-estimates-sync`,
`mav-pve-storage-metrics`, plus smbd/nmbd/winbind (Samba), tailscaled,
smartmontools, chrony, postfix.

Other host listeners not yet attributed: `uvicorn` on 8001 (pid 1000),
`python` on 7860 (voice-pipecat), `app` on 9085.

Root crontab: weekly (Sun 02:00) `hcp-scraper` docker run writing into
`/mnt/samsung-sata/mav-rag/` (env file `/root/hcp-scraper/.env`), logging to
`/var/log/hcp-scraper.log`.

Identity/state that needs an explicit migration decision (task 3):
- Tailscale node identity (/var/lib/tailscale) — reuse vs re-auth as new node
- Samba config + `mavshare` user/passdb
- Syncthing device ID + folder config (device key = peer identity)
- NIC rename rules (lan0/p2p0), /etc/network/interfaces rewritten for Z690 NIC
- SSH host keys (clients will see changed keys on fresh install unless copied)
- /etc/fstab (NTFS mount for /mnt/samsung-sata), proxmox-firewall state

## Corrections to the prep note (observed vs assumed)

1. `pve-data` usage is **1.03% (~17.6 GB)**, not 0.12% (~2 GB).
2. 840 PRO is **56% used (211G free)**, not ~470G free — still ample for dumps.
3. Samsung drive is NTFS and holds 266 GB of live data whose post-retirement
   home is undecided.
4. "RAG, Prometheus, Samba, Hermes" undersells the host: 7 Docker containers,
   4 Hermes units, syncthing, pm2, pacc-registry, hcp sync units, and a weekly
   scraper cron all live on the host itself.
