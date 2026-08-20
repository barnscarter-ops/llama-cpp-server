# Night 4 — Cutover plan (AIWA: ProDesk → Z690)

**Scheduled: Saturday 2026-08-22, evening. Start ~18:30, services verified back by ~23:00.**
Written 2026-08-19 from the Night 3 PASS (`NIGHT3-OBSERVED-20260819.md`). Decisions
confirmed by Carter 2026-08-19: date = Sat 08-22 evening; 840 PRO media subset is
already on Google Drive (only service dirs restored); llama model already picked,
benched, tuned.

All AIWA-side commands run via Orca on the host, one approval gate at a time (⛔).
Nothing starts until Carter says go on the night.

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
3. **Stage the 840 PRO service dirs** — the only /mnt/samsung-sata content that
   is *not* media and *not* covered by Google Drive:
   - `mav-transfer/` — Samba share + LXC 100 mp0 bind mount
   - `mav-rag/hcp-exports/` — ingest dir for hcp-catalog-sync, hcp-estimates-sync,
     and the weekly hcp-scraper cron
   Confirm sizes under `C:\aiwa-840pro\`, decide the rest is skipped (Chris
   backup, WW, sync/, SOPDEV01 — media/archive per Carter). scp to the PoC host
   into `/root/stage-samsung-sata/` (do NOT mount anything yet).
4. **Write the Z690 .link files ahead of time** (MAC-identified, per
   `X870E-NETWORK-CUTOVER.md` — never by name):
   - `lan0` = Intel I225-V onboard, `58:11:22:30:68:48` (currently `nic0` on the PoC)
   - `p2p0` = Realtek card, `1C-86-0B-3A-48-FB` (arrives during Gate 1)
   `/etc/network/interfaces` then restores unchanged (vmbr0 .12/24 on lan0,
   vmbr1 10.110.10.1/30 on p2p0).
5. **Stage llama for the R9700 on the PoC** (build ≠ service; keep the soak
   clean): llama.cpp Vulkan build, the picked GGUF (confirm path + flags with
   Carter — models on disk on the X870E incl. AIWA-earmarked Nemotron Q4_K_M /
   Qwen3.6 Q4_K_M / Qwen3.8-27B), systemd unit written but **not enabled**.
   Sampler flags carry over from the tuning; **KV-cache setting does NOT** —
   the 4060 Ti law (f16 KV always) was measured on CUDA and the historical R9700
   Vulkan finding was the opposite (q8_0 KV won). Plan a 10-min `llama-bench`
   on the night, not a re-debate.
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
cp -a /root/stage-samsung-sata/mav-transfer /mnt/samsung-sata/
cp -a /root/stage-samsung-sata/mav-rag /mnt/samsung-sata/     # hcp-exports ingest tree
# ownership must match uid 1000 (mavshare) — the tarball /etc/passwd restores later;
# chown -R 1000:1000 /mnt/samsung-sata/mav-transfer for now, verify after user restore

# 2.1 drop the PoC evidence CT (its job is done) — or keep stopped until soak ends
pct destroy 200 --purge        # ⛔ Carter's call

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

## Gate 4 — R9700 llama + end-to-end verify ⛔

```
# 4.1 KV sanity (10 min, NOT a re-debate): llama-bench f16 vs q8_0 KV on this
#     Vulkan/RDNA build — pick the winner, note it in the observed doc
# 4.2 start the pre-staged unit with the picked model + tuned sampler flags
systemctl start llama-server (or compose, per prep step 5)
# 4.3 verify t/s + one completion through whatever fronts it
```

End-to-end checks (the map's "verify one cutover call succeeds"):

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
- Don't "optimize" llama flags on the night beyond the KV sanity check —
  the sampler set is tuned; leave it.
