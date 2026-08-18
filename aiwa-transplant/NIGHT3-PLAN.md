# Night 3 — Proxmox proof-of-concept on the Z690, plus Task 5

Planned 2026-08-18, after Night 2 completed. Decisions locked by Carter
tonight: **`pve-root` goes on the Z690's 1 TB SN7100**, and **the 256 GB
Toshiba is removed from the machine entirely** — not wiped, not reused. That
resolves the prep doc's self-contradiction (it named both drives) and retires
the `E:\Media\Grizzly\Curated` wipe hazard: the photos leave the building on
the drive, intact, plus they're confirmed in Google Photos (2026-08-11).

Night 3 needs nothing from AIWA. The ProDesk keeps running production
untouched; everything restores from the copies already pulled to the X870E.
**AIWA hard gate does not come into play tonight — and if a step seems to
need AIWA, stop, because it's wrong.**

## What Night 3 proves (or refuses to)

1. The Z690 can run Proxmox at all (VMD is the known blocker).
2. **Task 5**: a Night-2 vzdump actually restores and boots. Until this
   passes, the backups are Schrödinger's backups and Night 4 must not start.

## Hardware state going in

| Part | State |
|---|---|
| Z690 + 13600K + 64 GB DDR4 | headless desktop shell; its Windows left with the 2 TB SN7100 |
| Frost Commander 140 | on the 13600K — stays (the AIO went to the X870E) |
| R9700 32 GB | in the board; ignore it tonight. iGPU (UHD 770) drives the monitor. GPU passthrough is explicitly out of scope |
| 1 TB SN7100 | blank → becomes `pve-root` + `pve-data` thin pool for the PoC |
| 256 GB Toshiba | **pull it physically before first power-on** |
| onboard Intel I225-V | the only NIC — switch-facing (the Realtek card lives in the X870E now) |

Pulling the Toshiba first means exactly one disk is present when the
installer runs — same discipline as Night 1's Windows install, same reason.

## Phase 0 — prep on the X870E (before touching the Z690)

1. Download the **Proxmox VE 9.2 ISO** (match the ProDesk's major version —
   a restore onto the same PVE generation removes one variable).
2. Write it to a USB stick. The SanDisk is free for this — it never became a
   backup drive. Verify the write.
3. Have `C:\aiwa-backups\20260817` reachable: the restore test will pull
   `vzdump-lxc-100-2026_08_17-23_30_44.tar.zst` (rustdesk, 580 MB — the
   smallest, least side-effectful CT) over the LAN via `scp`.

## Phase 1 — BIOS, before the installer

- **Disable Intel RST VMD.** The installer is blind to every NVMe until this
  is off. It presents as "no target disk", not as an error. Nothing boots
  through VMD anymore on this board, so nothing breaks.
- Boot order: USB first for the install; afterwards the SN7100.
- Leave the iGPU enabled (it already is — it was driving displays).

## Phase 2 — install

- Target: the 1 TB SN7100 (it will be the only disk — verify anyway).
- Default LVM layout is fine for a PoC (`pve-root` + `pve-data` thin).
- Hostname: **`aiwa-poc`**, NOT `aiwa`. Static IP **`192.168.1.13/24`**,
  gateway `192.168.1.254`. **Never `192.168.1.12` or `.10`** — the ProDesk
  and the X870E hold those. Two hosts with AIWA's identity on one switch is
  the exact failure the fresh-install strategy exists to avoid.
- `vmbr0` lands on the I225-V automatically (single NIC, nothing to
  misidentify).

## Phase 3 — Task 5, the restore test

From the X870E, push the dump + manifest to the PoC host, verify the hash
survived the hop, then restore into an **isolated bridge** so the CT cannot
touch the LAN:

```
# on aiwa-poc: an isolated bridge with no physical port
cat >> /etc/network/interfaces <<'EOF'

auto vmbr99
iface vmbr99 inet manual
	bridge-ports none
	bridge-stp off
	bridge-fd 0
EOF
ifreload -a
```

```
pct restore 200 /root/vzdump-lxc-100-2026_08_17-23_30_44.tar.zst --storage local-lvm
pct set 200 --net0 name=eth0,bridge=vmbr99
pct start 200
pct exec 200 -- ps aux
pct exec 200 -- systemctl --failed
```

New CT ID (200), isolated bridge — the restored rustdesk CT must never share
a wire with the production one at CT 100 on the ProDesk. **Do not restore
102/103 (hcp-mcp-prod / mcc-prod) on Night 3**: even isolated, prod-service
CTs are the wrong test subjects, and rustdesk proves the same thing — that
vzdump → pct restore → running processes works end to end.

**Pass criteria:** restore exits 0, CT starts, processes run, no failed
units (or only ones explained by the isolation). Record the output in
`NIGHT3-OBSERVED-<date>.md`. Then `pct stop 200` — leave it stopped as the
evidence.

## Phase 4 — poke the host, then leave it alone

- `pveversion -v`, `lscpu`, `free -h`, sensors sane, NVMe temps sane.
- Confirm web UI reachable at `https://192.168.1.13:8006`.
- Do NOT join it to anything, do NOT install Tailscale yet, do NOT start
  recreating services. Identity, Tailscale, and real data belong to Night 4.

## What Night 3 deliberately does not do

- No SN770 move (that drive leaves the ProDesk only at cutover).
- No 840 PRO anything — its data is already on the X870E.
- No GPU passthrough, no IOMMU flags.
- No touching AIWA. Not even reads.

## Aborts

- VMD off but installer still sees no disk → stop, reseat/BIOS-update
  territory, not a tonight problem.
- Restore fails or CT won't start → **Night 4 is blocked.** The failure mode
  decides whether the fix is on the backup side (re-dump on AIWA) or the
  host side (storage config). Either way, that's the finding — Night 3 will
  have done its job by failing loudly here instead of on cutover night.

## After Night 3 passes

Night 4 (cutover) gets its own plan, but the shape: shut ProDesk CTs down,
final incremental vzdump, restore all four onto the Z690, move the SN770,
swap identity to `192.168.1.12`, re-point Tailscale, then the ProDesk goes
quiet but stays intact as rollback until the new host has run clean for days.
