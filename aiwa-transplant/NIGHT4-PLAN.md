# Night 4 — Cutover plan (AIWA: ProDesk → Z690)

**Scheduled: Saturday 2026-08-22, evening. Start ~18:30, services verified back by ~23:00.**
Written 2026-08-19 from the Night 3 PASS (`NIGHT3-OBSERVED-20260819.md`). Decisions
confirmed by Carter 2026-08-19: date = Sat 08-22 evening; 840 PRO media subset is
already on Google Drive (only service dirs restored); llama model already picked,
benched, tuned.

All AIWA-side commands run via Orca on the host, one approval gate at a time (⛔).
Nothing starts until Carter says go on the night.

## 2026-08-22 night-of overlay — read this first

Prep session ran **10:29 CDT** from `cmb-workbench`. Gate 0 still starts ~18:30
unless Carter says go early. This overlay supersedes Gate 4 as written on
2026-08-19 and the soak draft unit in `night4/llama-server.service`.

### Live facts (observed, not assumed)

| Check | Result |
|---|---|
| Clock | 2026-08-22 10:29 CDT — ~8 h before scheduled Gate 0 |
| ProDesk prod | `192.168.1.12:8006` TCP open (identity still here until Gate 3) |
| Direct link | `10.110.10.1:22` open from X870E `10.110.10.2` — **scp dumps here in Gate 0, before the card moves** |
| PoC | `192.168.1.230:8006` TCP open (`aiwa-poc`) |
| 690 llama | `http://192.168.1.240:8080` **healthy**, model `nemotron-3.5-lightning-30b-a3b` Q5_K_M, `n_ctx` 131072 |
| Box A GLM | guardian `llama_offline` (idle) — leave it; not this night |
| Cutover dumps | **not yet** — only fallback `C:\aiwa-backups\20260817\` (~8.9 GB). Dest staged: `C:\aiwa-backups\20260822\` (C: 1457 GB free) |
| 840 PRO tree on X870E | `C:\aiwa-840pro\` still has `mav-transfer/` + `mav-rag/` (PoC already has copies on `/mnt/samsung-stage`) |

### X870E NICs (identify by MAC — pull only the add-in card)

| Adapter | MAC | IP | Gate 1 |
|---|---|---|---|
| `HomeFiber` (X870E onboard Realtek) | `10-FF-E0-8A-A0-49` | `192.168.1.10/24` gw `.254` | **stays** |
| `AIWA Direct` (add-in Realtek) | `1C-86-0B-3A-48-FB` | `10.110.10.2/30` no gw/DNS | **moves to Z690** — Windows location **PCI bus 16, device 0** |
| Z690 onboard Intel I225-V | `58-11-22-30-68-48` | PoC `nic0` / future `lan0` | already in the Z690 |

After the card is out of the X870E: delete the stale `AIWA Direct` adapter
config (10.110.10.2/30). X870E stays on `HomeFiber` only. The point-to-point
link returns when the card is seated in the Z690 and Gate 3 brings up `p2p0`.

### Llama: CT 210 is already production — do not "bring up" the soak Q4

`aiwa-transplant/night4/llama-server.service` is a **dead draft** (host path
`/mnt/stage/llama`, Q4_K_M, 64k, port **8090**, alias `local-llm`, no MTP).
Starting it fights CT 210 for the R9700.

Live SoT is git `690-routing/` (origin through `3d2e39e`): systemd unit +
`swap-qwen-consult.sh` / `swap-nemo-clerk.sh`. Walk-away `:8080` = Nemotron
clerk, `--reasoning off`. If CT 210 dies in cutover, re-apply those files —
do not fall back to the Q4 host unit.

`pct destroy 200` in Gate 2 is the **rustdesk evidence** CT. **Never destroy
210.** `.240` stays the llama IP through the host identity swap to `.12`.

### Orca path tonight

This PC's Orca has no paired AIWA host terminal. Gate 0–3 commands run on
**ProDesk Orca (CT 101)** while it is up, then on the restored CT 101 / host
console after Gate 2. Named SSH exception to `aiwa-poc` (`id_ed25519_proxmox`
@ `192.168.1.230`) is only for CT 210 if llama must be repaired; announce on
WORKBOARD first. Do not steal `.12`. Do not dual llama-server.

### Locked 2026-08-22 (Carter)

1. SN770 **stays in the ProDesk** the whole night (rollback intact).
2. Voice / customer-SMS gateways are on hold — **safe to stop anytime**; no live customers.
3. CT 200 **stays stopped** (do not destroy). Still never touch 210.
4. Samba `mavshare`: **proven** on live `\\192.168.1.12\Proxmox` (`net use` succeeded). Same password at Gate 3. Restore `/var/lib/samba/private` from the host tarball first; `smbpasswd -a mavshare` only if that mount fails.
5. Say **go Gate 0** to start dumps.

## Doctrine reminders

- PC (X870E, `cmb-workbench` 192.168.1.10 / 100.124.41.115) = dev. AIWA = live.
- One identity on the wire at a time: the new host stays `192.168.1.230`
  (`aiwa-poc`) until the ProDesk is powered off and .12 is free.
- ProDesk stays **fully intact** as rollback — see "Open conflict" below.

## ⚠ Open conflict to settle before the night: the SN770

`NIGHT4-NEXT.md` says "move SN770"; the transplant-map parts table says SN770
2 TB "**Stays in ProDesk. Never moves**"; and the same NIGHT4-NEXT also says
"ProDesk stays intact as rollback" — which the SN770 leaving would break.

**Recommendation: do NOT move the SN770 on cutover night.** The new host's 1 TB
SN7100 has 794 GB thin free; CTs (~18 G) + 840-PRO service dirs (~small) + docker
(~9 G) + model(s) fit easily. After soak, moving the SN770 in as bulk storage is
a calm follow-up with its own window. Carter confirms on the night.

## What moves physically (only one part)

| Part | From → To | When |
|---|---|---|
| Realtek 2.5GbE card (`1C-86-0B-3A-48-FB`) | X870E → Z690 | during Gate 1 (X870E rebooted) |
| SN770 2 TB | **nowhere (recommended)** | post-soak, separate window |
| 840 PRO | nowhere — retires in the ProDesk | — |

X870E after the pull: keeps `HomeFiber` (onboard, 192.168.1.10). Delete the stale
`AIWA Direct` adapter config (10.110.10.2/30) after the card is out. The
point-to-point link dies with the card — it was a bulk-transfer optimization;
everything reachable via the switch survives.

## Pre-flight prep — Thu 08-20 / Fri 08-21 evenings (~1–2 h, read-only + staging)

1. **Soak check on aiwa-poc** (192.168.1.230): uptime since 08-19, `pveversion`,
   SMART on the SN7100, CT 200 still stopped (evidence), disk temps sane.
2. **Verify Night 2 artifacts on the X870E**: `C:\aiwa-backups\20260817\` (4
   vzdumps + host tarball) — spot-check SHA256 against `SHA256SUMS-20260817.txt`;
   `C:\aiwa-840pro\` present (source for the service-dir restore).
   **DONE 2026-08-20** — sha256sum -c: all 5 artifacts **OK** (4 CT dumps +
   host tarball). Note: the sums file carries 2 stale lines for old Aug 9/16
   CT-102 dumps that were deleted — harmless; the Aug 17 dump on disk verifies.
3. **Stage the 840 PRO service dirs** — the only /mnt/samsung-sata content that
   is *not* media and *not* covered by Google Drive:
   - `mav-transfer/` — **47 GB (measured)** — Samba share + LXC 100 mp0 bind mount
   - `mav-rag/` — **498 MB measured** (hcp-exports ingest tree, screenshots)
   The rest (Chris backup, WW, sync/, SOPDEV01) is skipped — media/archive per
   Carter. 47+ GB does NOT fit comfortably on pve-root (62 G free after llama
   staging; Saturday's dumps need ~10 G) → carve a thin LV. ⛔ **mkfs is
   agent-blocked — Carter pastes this one** (real usage = only what's written;
   300 G virtual from the 794 G pool):
   ```
   lvcreate --thin -V 300G -n samsung-stage pve/data
   mkfs.ext4 /dev/pve/samsung-stage
   mkdir -p /mnt/samsung-stage
   echo '/dev/pve/samsung-stage /mnt/samsung-stage ext4 defaults,nofail 0 2' >> /etc/fstab
   mount /mnt/samsung-stage
   ```
   ⚠️ Mount point is `/mnt/samsung-stage`, **NOT `/mnt/stage`** — `/mnt/stage`
   already holds the llama build + 25.5 GB model on pve-root; mounting the LV
   there would shadow (hide) them and break Gate 4.
   Then the agent scps `mav-transfer/` (47 GB) from `C:\aiwa-840pro\` to
   `/mnt/samsung-stage/` (skip `sync/`, `WW/`, `SOPDEV01-ChrisBackup/`) and
   moves the already-staged mav-rag onto the LV → one staging root.
   **DONE 2026-08-20 — ALL of step 3 complete:**
   - LV `samsung-stage` carved (300 G thin, 0% → 47 G used), Carter ran the
     one blocked line, fstab'd + mounted at `/mnt/samsung-stage` (295 G avail).
   - `mav-transfer/` — **47 G copied and verified** on the LV.
   - `mav-rag/` — **499 MB staged, incl. a `qdrant-data/` dir found living on
     the 840 PRO** (would have been orphaned at retirement).
   - `/mnt/stage/llama/` (build + model) stays on pve-root — 62 G still free.
4. **Write the Z690 .link files ahead of time** (MAC-identified, per
   `X870E-NETWORK-CUTOVER.md` — never by name):
   - `lan0` = Intel I225-V onboard, `58:11:22:30:68:48` (currently `nic0` on the PoC)
   - `p2p0` = Realtek card, `1C-86-0B-3A-48-FB` (arrives during Gate 1)
   **DONE 2026-08-20** — staged on the PoC at `/root/night4-staging/`
   (`10-lan0.link`, `11-p2p0.link`). Gate 3 = copy into
   `/etc/systemd/network/` + `networkctl reload` + `ifreload -a`.
   `/etc/network/interfaces` then restores unchanged (vmbr0 .12/24 on lan0,
   vmbr1 10.110.10.1/30 on p2p0).
5. **Stage llama for the R9700 on the PoC** — **DONE 2026-08-20**:
   - Vulkan userspace installed (mesa-vulkan-drivers); R9700 enumerates as
     **AMD Radeon Graphics (RADV GFX1201)** — confirmed via vulkaninfo.
   - llama.cpp **b10488 ubuntu-vulkan** build in `/mnt/stage/llama/llama-b10488/`.
   - Model staged: **NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q4_K_M.gguf**
     (25.5 GB; source sha256 `b520a899…a9158`, verify on the PoC after scp).
   - **The pick is documented** (git `1d97b3f` + ecosystem at `b0f23af`):
     Nemotron 3.5 Lightning 30B-A3B Q4_K_M, ctx 65536, f16 KV, ubatch **512**
     (TDR-sweep winner; the "ubatch 1024" header comment is stale — args are
     truth), batch 2048, flash-attn on, `--reasoning off` CRITICAL (burns
     1500+ hidden tokens/req without it), parallel 1, `--jinja`, no MTP
     (tested, net-slower on Vulkan). Build must be ≥ b10362 for
     `nemotron_h_moe` (b10488 ✓). Linux: no VK ICD pinning needed (single
     AMD ICD) — the `VK_LOADER_DRIVERS_SELECT=*amd*` trick was for the old
     Windows dual-ICD box. systemd unit: write in prep session, **not
     enabled** (keeps the soak clean).
   - Linux driver reality: this box runs RADV (mesa), the old Windows install
     ran AMDVLK/Adrenalin — the Adrenalin idle-VRAM-evict bug is a Windows
     driver issue and does not apply, but expect tg numbers to differ from
     the old Windows baseline (152 t/s). Night-of: bench proof below.
6. **X870E prep**: note which slot the Realtek card is in; plan the shutdown.
7. **Customer-facing check**: confirm no live voice call / active SMS session is
   expected Sat evening; the window takes voice-pipecat, hermes-customer-sms,
   mav-console (tailscale 443/3000), and HCP MCP down for ~2.5–3.5 h.

## Gate 0 — final backups on the ProDesk (services stay UP) ⛔

~18:30–19:50. The 08-17 set is the fallback; this is the cutover set.

```
# 0.1 space + Sunday-job check (last weekly vzdump 102 ran sun 03:00 on 08-16 set)
df -h / /var/lib/vz
# 0.2 final vzdump, all four CTs, snapshot mode, protected
vzdump 100 101 102 103 --compress zstd --mode snapshot --storage local \
  --protected 1 --notes-template "cutover final {{guestname}}"
# 0.3 host-state tarball — ⛔ stop docker ~5 min for a clean Qdrant copy (recommended,
#     same call as Task 4 Phase 2; this is the set you'd actually restore from)
systemctl stop docker
tar --xattrs --acls --numeric-owner -czf /var/lib/vz/dump/aiwa-host-state-20260822.tar.gz \
  /etc /root /opt /home /usr/local/bin /var/lib/tailscale /var/lib/samba/private \
  /var/lib/docker/volumes /var/spool/cron 2>/tmp/tar-warnings.txt
systemctl start docker && docker ps   # all 7 back before anything shuts down
# 0.4 checksums + copy off (direct link while it still exists)
cd /var/lib/vz/dump && sha256sum vzdump-lxc-1*_2026_08_22*.tar.zst aiwa-host-state-20260822.tar.gz > SHA256SUMS-20260822.txt
# [X870E] scp -i ~/.ssh/id_ed25519_proxmox root@10.110.10.1:/var/lib/vz/dump/*2026_08_22* C:\aiwa-backups\20260822\
```

Abort: any dump fails, SMART error, or docker won't restart → no shutdown, fix
first, ProDesk stays production.

## Gate 1 — ProDesk down, card moves (downtime starts) ⛔

```
pct stop 100 101 102 103      # verify all four down
qm list                       # expect empty
tailscale down                # stop advertising 192.168.1.0/24 + node 100.87.155.47
shutdown -h now
```

- Verify from the X870E: `192.168.1.12` and `10.110.10.1` stop answering. One
  identity on the wire, proven.
- Pull the Realtek card from the X870E (brief X870E shutdown), install in the
  Z690. X870E reboots on `HomeFiber` only.
- ProDesk: powered off, **nothing removed** (per recommendation), = rollback.

## Gate 2 — restore the four CTs onto the new host ⛔

Still at .230/`aiwa-poc` — identity swap comes after, in Gate 3.

```
# 2.0 bind-mount target MUST exist before first CT start (Night 3 lesson):
mkdir -p /mnt/samsung-sata
cp -a /mnt/samsung-stage/mav-transfer /mnt/samsung-sata/
cp -a /mnt/samsung-stage/mav-rag /mnt/samsung-sata/     # hcp-exports ingest tree
# (if mav-rag was never moved onto the LV, it's at /mnt/stage/samsung-sata/mav-rag)
# ownership must match uid 1000 (mavshare) — the tarball /etc/passwd restores later;
# chown -R 1000:1000 /mnt/samsung-sata/mav-transfer for now, verify after user restore

# 2.1 drop the PoC evidence CT (its job is done) — or keep stopped until soak ends
pct destroy 200 --purge        # ⛔ Carter's call — this is rustdesk evidence, NOT llama
# NEVER pct destroy 210. CT 210 llama-vulkan is live production inference.
# After restores: pct list must still show 210 running; curl .240 /v1/models
# must still be nemotron-3.5-lightning-30b-a3b.

# 2.2 restore all four from the 2026_08_22 dumps
pct restore 100 /var/lib/vz/dump/vzdump-lxc-100-2026_08_22_*.tar.zst --storage local-lvm
pct restore 101 ... 102 ... 103 ...
# 2.3 CT 200's vmbr99 isolation does not apply here — verify each config net0 on vmbr0

# 2.4 start in dependency order, watch first boot of each
pct start 100 && pct exec 100 -- systemctl --failed   # rustdesk: hbbs/hbbr up, mp0 mounts
pct start 101 && pct exec 101 -- systemctl --failed   # orca (our access path — if it
                                                      # breaks, fix from host console)
pct start 102 && pct exec 102 -- systemctl --failed   # hcp-mcp-prod
pct start 103 && pct exec 103 -- systemctl --failed   # mcc-prod
```

Pass: all four up, zero failed units (or only isolation-explained ones). CT IPs
.13/.14/.15 now live on vmbr0 — safe, the old CTs are powered off.

## Gate 3 — host identity + config swap ⛔ (the long tail, ~60–90 min)

Order matters. Selective restore — never untar the old `/etc` wholesale.

**3.1 Identity first** (new host claims .12):

```
rm /etc/systemd/network/*nic0*   # drop the PoC-era pin if present
# install prepared .link files: lan0=I225-V (58:11:22:30:68:48), p2p0=Realtek (1C:86:0B:3A:48:FB)
# edit /etc/network/interfaces: vmbr0 192.168.1.230 → 192.168.1.12/24 gw .254 (lan0)
#                              vmbr1 10.110.10.1/30 (p2p0)
hostnamectl set-hostname aiwa && sed -i s/aiwa-poc/aiwa/ /etc/hosts
ifreload -a && ip -br a          # verify .12 AND 10.110.10.1
```

Do NOT restore old `/etc/pve` — CTs restored by dump already wrote their own
configs; storage.cfg on the new host is correct as-installed.

**3.2 Selective host-state restore** from `aiwa-host-state-20260822.tar.gz`:

- Safe wholesale: `/root`, `/opt`, `/home` (hermes .env's, syncthing identity),
  `/usr/local/bin`, `/var/lib/docker/volumes`, `/var/lib/samba/private`,
  `/var/spool/cron`, `/var/lib/tailscale` (node identity 100.87.155.47 — see 3.4).
- Selective from `/etc`: systemd units (the 11 custom), `/etc/samba/smb.conf`
  (**drop the insecure `[proxmox-root]` share** — flagged in
  HOST-CONFIG-PRESERVE §3), ssh host keys, users (`hermes`, `node_exporter`,
  uid-1000 `mavshare`/syncthing entries from passwd/shadow/group).
- Explicitly NOT restored: `/etc/pve`, `/etc/network`, `/etc/hostname`,
  `/etc/machine-id`, `/etc/fstab`'s 840 PRO NTFS line (drive not moving —
  `/mnt/samsung-sata` is now a plain dir on the SN7100).

**3.3 Services up, one at a time:**

```
systemctl daemon-reload
# enable timers (not services) for the three oneshots; enable the rest
docker compose up -d   # in /root/homelab-dashboard-stack, /opt/homelab-noc-dashboard,
                       # /opt/mav-rag, /opt/pipecat — images pull/build (~8.5 GB);
                       # qdrant PINNED v1.9.4
docker ps              # all 7
pm2 resurrect && pm2 list   # PM2_HOME=/root/.pm2 from tarball; mav-bridge etc.
node -e "console.log(new Date().toString())"   # must print CDT — TZ=America/Chicago
                                               # or no-show/week-filing shifts 5-6 h
systemctl start hermes-gateway hermes-triage hermes-customer-sms hermes-pc-sms \
                 pacc-registry orca-aiwa node_exporter syncthing@syncthing
smbpasswd -a mavshare   # ⛔ Carter supplies the password — passdb does not carry
```

**3.4 Tailscale** ⛔: `systemctl enable --now tailscaled` with the restored
state → same node 100.87.155.47, resumes subnet-route advertisement of
192.168.1.0/24. Verify `tailscale serve` 443/3000 answer on the tail IP from an
off-LAN client (phone).

**3.5 AIWA triage re-point** (the known-stale ref): `PC_HOST` in hermes-triage
config still targets old CartersPC `100.124.216.11` → change to
`cmb-workbench` / `100.124.41.115`. This is the moment it was deferred for.

## Gate 4 — R9700 llama (keep CT 210) + end-to-end verify ⛔

**Do not `systemctl start` a host `llama-server` and do not install
`aiwa-transplant/night4/llama-server.service`.** That file is the 08-20 soak
draft (Q4_K_M @ `/mnt/stage/llama`, port 8090, 64k, no MTP). Live llama is
already **CT 210** at `http://192.168.1.240:8080`.

SoT if the CT must be repaired: `690-routing/llama-server.service` +
`690-routing/swap-*.sh` (same bytes as `/opt/llama/` inside the CT).

```
# 4.1 prove the clerk survived Gates 0–3 (required)
curl -sS --max-time 5 http://192.168.1.240:8080/health
curl -sS --max-time 5 http://192.168.1.240:8080/v1/models
# expect: status ok, id nemotron-3.5-lightning-30b-a3b, n_ctx 131072, ftype Q5_K
# from PoC/host: pct status 210  → running
#                lxc-attach -n 210 -- systemctl is-active llama-server  → active

# 4.2 IF .240 is dead — restore from git, never from the Q4 host unit
#   scp 690-routing/{llama-server.service,swap-*.sh} onto CT 210
#   install unit to /etc/systemd/system/, scripts to /opt/llama/, chmod +x
#   systemctl daemon-reload && systemctl enable --now llama-server
#   pkill trap: pkill -f llama-server self-kills — use [l]lama-server after systemctl stop
# announce any llama restart on WORKBOARD first. one model at a time.

# 4.3 optional 2-min bench — SKIP unless llama looks wrong. 08-20 soak already
#     proved RADV on this card (Q4 tg128 141.4). Do not retune flags tonight.
```

End-to-end checks (the map's "verify one cutover call succeeds"):

- [ ] `http://192.168.1.240:8080` clerk alias `nemotron-3.5-lightning-30b-a3b` (CT 210; not a host :8090 Q4)
- [ ] voice-pipecat answers a test call
- [ ] hermes-customer-sms sends/receives a test message
- [ ] mav-console reachable via tailscale :3000 AND LAN :3010
- [ ] HCP MCP (CT 102) serves a request; hcp-exports dir writable
- [ ] rustdesk (CT 100): hbbs/hbbr up, a client reconnects
- [ ] orca (CT 101) reachable at 192.168.1.13
- [ ] Samba `[Proxmox]` share mounts from a client as mavshare
- [ ] syncthing peers reconnect (device ID preserved)
- [ ] triage "can't reach PC" alerts silent after PC_HOST re-point
- [ ] X870E cleanup: stale `AIWA Direct` adapter config removed
- [ ] Sun 03:00: first weekly vzdump 102 job fires clean on the new host (soak test #1)

## Timeline

| Block | Window | Notes |
|---|---|---|
| Gate 0 final backups | 18:30–19:50 | services UP; docker down only ~5 min |
| Gate 1 shutdown + card move | 19:50–20:15 | **customer downtime starts** |
| Gate 2 CT restores + first boots | 20:15–20:50 | ~1.4 GiB/s per Night 3 |
| Gate 3 identity + host config | 20:50–22:20 | long tail = docker image pulls |
| Gate 4 llama + verification | 22:20–23:00 | downtime ends (~3 h total) |
| Soak | Sun 08-23 → Wed 08-26 | ProDesk untouched the whole time |

## Rollback

- **Before identity swap fails badly** (Gate 2/early 3): new host back to .230,
  power ProDesk on, services resume exactly as they were. Losses: none.
- **After identity swap**: shut the Z690 down (frees .12 + tail identity), boot
  the ProDesk — it is intact because nothing left it. Decision point, don't
  ping-pong: if Gate 3 fails twice on the same thing, roll back and diagnose on
  the bench.
- The 08-17 dump set on `C:\aiwa-backups\20260817\` is the fallback behind the
  fallback (two nights of dumps exist from tonight onward).

## Post-soak (separate windows, not Night 4)

1. SN770 disposition (move in as bulk storage vs leave) — resolve the conflict.
2. ProDesk decommission/wipe decision.
3. Permanent all-CT backup job on the new host (only VMID 102 has one today) —
   or move ProxmoxBackup-Nightly off the PC onto AIWA cron/PBS.
4. The Grizzly/HCP Windows-bound migrations per `SEPARATION-AUDIT-20260818.md`,
   one at a time, each with rollback.

## Do-not list (carried forward)

- No second machine holding 192.168.1.12 or the tail identity, ever.
- No restoring CTs 102/103 on any host while prod CTs run (the PoC rule).
- No wholesale /etc restore — the selective list above is the whole point of
  the fresh-install strategy.
- Don't "optimize" llama flags on the night beyond proving CT 210 is still
  the clerk — the clerk/consult set is already tuned; leave it.
- Don't install or start `aiwa-transplant/night4/llama-server.service` on the host.
