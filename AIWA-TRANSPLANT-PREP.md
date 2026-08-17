# AIWA Proxmox — Transplant Prep (handoff prompt)

> Written 2026-08-10. Preparation phase only — the cutover is blocked until
> the AM5 build frees the Z690. Hand this file to a fresh session.

## The move, in one line

An X870E AORUS Elite WiFi 7 motherboard + Ryzen 9 9900X becomes the main PC.
The main PC's current Z690 board + 13600K + 64GB RAM become the new AIWA. The
HP ProDesk is retired and sold.

## Hardware disposition

### Main PC — X870E AORUS Elite WiFi 7 + Ryzen 9 9900X

> Parts table reconciled against the physical bench inventory on 2026-08-13,
> assembly state updated 2026-08-14.
> `aiwa-transplant/transplant-map.html` is the live version — update both.

| Component | Source | Notes |
|---|---|---|
| GIGABYTE X870E AORUS Elite WIFI7 motherboard + 9900X | purchased | DDR5, 2 DIMMs populated |
| KLEVV BOLT V 32GB DDR5-6000 (2x16) | purchased | Do NOT run 4 mismatched DIMMs. 64GB kit ordered. |
| ASUS ProArt GeForce RTX 4060 Ti 16GB | allocated | Confirmed from the card itself 2026-08-13 |
| Samsung 9100 PRO 2TB (bare SKU) | purchased | New `C:`, clean Windows install. **Runs on the board's stock M.2 heatsink — the HR-10 does not clear the AIO in slot 1.** Idles 35°C (observed 2026-08-17). A low-profile heatsink comes off the ProDesk's NVMe when that machine retires. |
| Thermalright HR-10 2280 Pro Black ×3 | purchased | **Active** M.2 heatsink (30mm fan, 6000 RPM, 12V/0.09A). **Three on hand as of 2026-08-14.** Corrected 2026-08-17: **only one goes in the X870E**, on the SN7100 in slot 3 (verified to clear). Slot 1 is blocked by the AIO, so the 9100 PRO runs stock. The SN7100 is the drive that needs it — it takes the sustained backup writes. |
| ASRock Challenger CL-850G | purchased | ATX 3.1, 80+ Gold, non-modular, native 12V-2x6 |
| ARCTIC Liquid Freezer III Pro 360 A-RGB | purchased | First unit arrived used — **Amazon approved return + free replacement, delivering Fri 2026-08-14 AM**. Still not delivered as of Friday morning. |
| NZXT AIO | already owned | **Unusable — mounting hardware lost.** Not a fallback. Needs a bracket, and NZXT retention varies by Kraken generation. |
| Thermalright Frost Commander 140 | on the Z690 | **Night 1 fallback if the AIO slips.** Currently cooling the 13600K — see the conflict note below. |
| Thermalright AM5 Secure Frame (black) | purchased | Contact frame, fits before the cooler |
| Okinos Cypress 7 case | **arrived 2026-08-13** | On the bench. **38mm radiator clearance verified** by test-fitting the used AIO before it ships back. |
| 2TB WD_BLACK SN7100 (`C:`, Windows) | stays | see VMD note below; becomes `D:` on the new board |
| 1TB WD_BLACK SN7100 (`D:`, storage) | stays | wiped, becomes the Proxmox boot drive |

### Build progress — 2026-08-14 morning

Observed and reported by Carter, not verified by me:

- 9900X seated on the X870E under the Thermalright Secure Frame
- Board mounted **in the Okinos** — there was no bench stage, see below
- RAM in **A2/B2** (correct for 2-DIMM AM5)
- Samsung 9100 PRO in **M.2 slot 1**, between the GPU and the CPU, on the
  board's **stock heatsink** — see the correction below
- RTX 4060 Ti installed with the anti-sag bracket. **It blocks M.2 slot 2.**
- PSU bolted into the case, no wiring run yet
- hub PWM-input jumper on `SYS_FAN1`, hub itself not mounted yet
- AIO had **not** arrived; still scheduled for the 14th

**Corrected 2026-08-17 — the HR-10 is not on the 9100 PRO.** Earlier revisions
of this document and the map recorded an HR-10 on the 9100 PRO in slot 1 with
its fan on `SYS_FAN2`. It does not fit: the AIO occupies that clearance. The
drive runs on the board's stock M.2 heatsink and idles at 35°C, so this is a
non-issue in practice. The HR-10 **does** clear in slot 3 and goes on the
SN7100 when that drive is installed. `SYS_FAN2` is therefore unused.

### X870E follow-ups — CLOSED 2026-08-17

All of the 08-16 follow-ups are resolved. **Device Manager is clean — zero
problem devices**, down from six.

| Was failing | Fixed by |
|---|---|
| `PCI\VEN_1022&DEV_1649` AMD PSP | Windows Update (SecurityDevices 5.17.0.0) |
| `ACPI\AMDI0204`, `ACPI\AMDI0052` | AMD Chipset Software 8.07.16.1035 |
| `PCI\VEN_10EC&DEV_8922` Realtek RTL8922AE Wi-Fi + Bluetooth | GIGABYTE Control Center |
| `ACPI\ITE8800\6` + `\7` USB-C UCSI | GIGABYTE Control Center |

Also done: NVIDIA 595.97 (`32.0.15.9597`, up from 591.86), Samsung Magician
9.0.1.950, Windows 11 Pro **activated** (RETAIL channel, permanently activated).

**The AMD download is scriptable after all** — the earlier HTML access-denied
result was a missing `Referer` header, not hard bot protection. This works:

```powershell
curl.exe -sL -A "<browser UA>" -e "https://www.amd.com/en/support/download/drivers.html" `
  -o amd_software_8.07.16.1035.exe "https://drivers.amd.com/drivers/amd_software_8.07.16.1035.exe"
```

It returns the genuine 81,490,200-byte installer, SHA256 `1B55DD2D…E66BDB`,
Authenticode `Valid`. **GIGABYTE is still hard-blocked** — a JS challenge, and
no header combination gets past it. Use GCC or a browser for their packages.

Still open, non-blocking: the ChatGPT Chrome computer-use extension needs
installing and connecting through **Settings → Computer use**. Note it pairs
with ChatGPT/Codex only and grants no browser control to other agents.

**No bench stage.** Earlier revisions of this document and the map said to
build on the motherboard box and drop the finished machine into the case
afterward. That was written when the case was late. The case arrived on the
13th, so Carter went straight into it on the 14th. Both documents have been
corrected; if you find another "bench-build first" reference, it is stale.

**The 4060 Ti blocks M.2 slot 2.** All four M.2 slots on this board are Gen 5,
so the SN7100 (a Gen 4 drive) loses nothing by going in slot 3 instead of
pulling the GPU back out. Use slot 3.

### Fan, AIO and M.2 cooler wiring — final, verified against the hardware

The board has **six** fan headers, not five as an earlier revision recorded.

```
CPU_FAN   <- AIO "FAN"  (3 radiator fans)   PWM, quiet curve
CPU_PUMP  <- AIO "PUMP"                     PWM, leave at 100%
CPU_OPT   <- AIO "VRM"                      PWM   <- not DC; ARCTIC specifies PWM
SYS_FAN1  <- fan hub PWM input
SYS_FAN2  <- (unused — the 9100 PRO has no HR-10, see correction above)
SYS_FAN3  <- M.2 fan, SN7100 (HR-10 in slot 3, 4-pin PWM)
D_LED1    <- AIO A-RGB (3-pin 5V only — never the 4-pin 12V header)
hub F-1   -> rear exhaust   (master port; the only one reporting RPM)
hub F-2   -> front trio     (case's own splitter)
hub POWER -> SATA or Molex direct from the PSU
```

Four things that were wrong at some point during the build session and are
worth not re-deriving:

1. **The AIO has three leads, not one.** The Liquid Freezer III **Pro** ships
   two alternative control cables: an *All-in-One* single-connector cable, and
   an *Individual control* cable ending in three connectors labelled PUMP, VRM
   and FAN. Use the **individual** cable — it lets the pump sit at 100% while
   the radiator fans follow a quiet curve. The all-in-one cable couples them.
2. **`CPU_OPT` is PWM.** The VRM fan is 3-pin, which normally implies DC, but
   ARCTIC's own documentation specifies PWM for all three headers.
3. **The hub is required.** Those three AIO leads consume every CPU-side
   header, so the four case fans have nowhere to go. Earlier notes calling the
   hub a spare are wrong. (This holds even though only one M.2 cooler is
   actually fitted — the AIO alone takes all three CPU-side headers.)
4. **The hub needs its own PSU power.** It has a power-input *socket*, not a
   captive pigtail — a SATA or Molex lead has to be run to it. A fan header
   alone cannot feed four fans.

Case fans are 4-pin, the front three are pre-split to one connector, and there
is no RGB on them. Radiator mounts top, exhaust, fans underneath pushing up,
tubes exiting at the rear. Per ARCTIC: **connect the PWM control cable before
mounting the cooler to the board.**

See `aiwa-transplant/transplant-map.html` (Night 1) for the diagram.

### USB kit for the clean Windows install

Staged 2026-08-14 at **`C:\Workspace\Active\x870e-usb-kit\`** — binaries stay
out of this repo; the docs are mirrored to `aiwa-transplant/x870e-usb-kit/`.

Four installers downloaded from vendor domains, SHA256-recorded and
Authenticode-verified (all `Valid`): AMD chipset 8.07.16.1035, NVIDIA 595.97,
Samsung Magician 9.0.1.950, and the Windows 11 Media Creation Tool.
`verify-kit.ps1` re-checks all four and passes 4/4.

**All four browser-only items are now staged — kit complete 2026-08-17.**
Carter downloaded the GIGABYTE files in a browser and published the whole kit
as **private GitHub release assets** on `barnscarter-ops/agent-memory`, tag
`x870e-kit-2026-08-17`. That removed the kit's dependency on the SN7100, which
was still physically in the Z690.

Re-downloaded to `C:\Workspace\Active\x870e-usb-kit\` on the X870E and verified
**9/9** against the release's own SHA256 digests; all four executables signed
`Valid` (AMD, NVIDIA, Samsung, Microsoft). The four originally-staged files
hash-match `MANIFEST.md` exactly, so the release is a byte-faithful copy.

```
01-BIOS\    mb_bios_x870e-aorus-elite-wifi7_8arpl323_f12.zip   15.9 MB
02-chipset\ amd_software_8.07.16.1035.exe                      77.7 MB
03-gpu\     595.97-...-dch-whql.exe                           913.0 MB
04-network\ mb_driver_654 (2.5GbE LAN) / 3701 (Wi-Fi) / 3702 (BT)
05-storage\ Samsung_Magician_Installer_Official_9.0.1.950.exe 195.1 MB
06-tools\   mb_driver_612 (audio) / MediaCreationTool_Win11.exe
```

Pull it on any authenticated machine with:

```powershell
gh release download --repo barnscarter-ops/agent-memory --pattern "*" --dir <path>
```

Note `gh release download <tag>` returns "release not found" for this repo, as
does `gh api .../releases`; omit the tag and pass `--pattern`. Plain `git`
credentials from Windows Credential Manager also 404 on the releases, tags and
branches endpoints for this private repo — `gh`'s own token is what works.

**Blocking on the board revision:** GIGABYTE publishes a different BIOS per
revision (1.0/1.1, 1.2, 1.3) and Q-Flash Plus will write the wrong one. The
revision also decides the Wi-Fi chip — MediaTek MT7925 on rev 1.0, Realtek
RTL8922AE on rev 1.1/1.2. Read `REV: 1.x` off the silkscreen first.

**Pull the SN7100 before running Windows Setup.** Setup places the EFI System
Partition on whichever disk it likes when two are present. If it lands on the
SN7100, the 9100 PRO stops booting the moment that drive moves — and it
surfaces weeks later. This reverses the "install it early while the slot is
reachable" instinct. That drive is also Night 2's backup target.

**Build the Windows stick with the Media Creation Tool**, not a file copy —
`install.wim` exceeds FAT32's 4 GB limit. Use a separate stick for the kit;
MCT reformats its target.

### Main-PC network cutover capture — observed 2026-08-15

Captured live from the current PC. These are **Windows adapter settings**, not
properties of the cable or NIC; recreate them on the new Windows install.

| Role | Current adapter | IPv4 configuration | Rule |
|---|---|---|---|
| Switch / Internet | `HomeFiber` (Intel I225-V; MAC `58-11-22-30-68-48`) | Static `192.168.1.10/24`; gateway `192.168.1.254`; DNS `8.8.8.8`, `8.8.4.4` | Exactly one wired adapter connects to this LAN. |
| Direct AIWA link | `AIWA Direct` (Realtek 2.5GbE; MAC `1C-86-0B-3A-48-FB`) | Static `10.110.10.2/30`; **no gateway**; **no DNS** | Direct cable only, peer is AIWA `10.110.10.1/30`. |

AIWA itself remains unchanged: LAN `192.168.1.12/24` via gateway
`192.168.1.254`; direct link `10.110.10.1/30` with no gateway.

**Network sequence after Windows is working:**

1. For the first Windows boot/install, use the X870E onboard LAN to the switch
   with DHCP. Do not claim `192.168.1.10` while the old PC is connected.
2. Once the new PC is stable, fully shut down the old PC and unplug its
   switch-facing Ethernet cable (or leave it powered off). Move/install the
   intended NIC hardware and reconnect the two existing cables to their same
   roles: switch-facing and direct-to-AIWA.
3. On the new PC, assign the two static configurations from the table above.
   The switch-facing adapter gets the gateway and DNS; the AIWA-direct adapter
   gets neither. Disconnect the temporary onboard-LAN cable from the switch.
4. Verify, in order: `ping 192.168.1.254`; Internet name resolution; `ping
   192.168.1.12`; then `ping 10.110.10.1`. If direct AIWA ping fails, stop at
   the cabling/address check; do not alter AIWA.

### AIO cooler — resolved 2026-08-13, replacement lands Friday AM

The first Liquid Freezer III Pro 360 was delivered as a previously-installed
customer return sold as new: bare scratched coldplate with no protective cap
and no factory pre-applied paste, thermal paste residue in the mounting
hardware, retail box torn open and repacked. Sold via ARCTIC's Amazon store.

**Resolved.** Carter reached Amazon 2026-08-13. The used unit is being
returned and a **brand-new replacement ships free, delivering the morning of
Friday 2026-08-14**. The earlier September 29 quote and the refund-and-re-buy
plan are both superseded. Micro Center Dallas drops to a distant third option
and is only in play if Friday's delivery is also bad.

**On arrival, check the coldplate before anything else.** A genuine unit has
grey paste pre-applied under a plastic cap. Bare copper means another used
unit — refuse it, fall back to air for the night, and buy in person rather
than trying a third time from the same commingled pool.

#### Fallback if the AIO slips: Frost Commander 140 — read the conflict

If the replacement doesn't show by build time, Night 1 runs on the
**Thermalright Frost Commander 140** air cooler instead of stalling. That is
the right call — a bench build needs a cooler, not specifically *that* cooler.
Two things to know before committing to it:

1. **It is currently mounted on the 13600K**, and per the Night 3 plan it
   stays with the Z690 to cool the new AIWA. Borrowing it for the AM5 build
   means swapping it back onto the Z690 once the AIO arrives — fine as a
   temporary measure, but it is a loan, not a reassignment. Do not let Night 3
   arrive with the Frost Commander bolted to the 9900X.
2. **Its AM5 mounting kit is confirmed on hand (2026-08-13).** That was the
   open risk — a lost bracket is what took the NZXT out of play — and it is
   closed. The fallback is real, not theoretical.

**The NZXT still is not a fallback** — its mounting hardware is lost, which is
why the Arctic was bought in the first place.

### New AIWA — Z690 + 13600K (retained from the main PC)

| Component | Source | Notes |
|---|---|---|
| ASUS TUF GAMING Z690-PLUS WIFI D4 (ATX) | from main PC | |
| Intel i5-13600K | from main PC | UHD 770 iGPU — keep enabled |
| 64GB RAM (4x16 TeamGroup UD4-3600 DDR4) | retained | trains at 3466 with 4 DIMMs; expected |
| Radeon AI PRO R9700 32GB | retained | The server retains twice the GPU memory available to the main PC, preserving inference headroom. |
| 256GB Toshiba KXG50ZNV256G NVMe | from main PC | → Proxmox boot / `pve-root` — **WIPED, see below** |
| 2TB WD SN770 | from old AIWA | → `pve-data` thin pool |
| 500GB Samsung 840 PRO SATA | from old AIWA | backup source, then retire |

### Retired

HP ProDesk (i5-9500, 32GB DDR4) — sold after the new host is verified.
Its 32GB DDR4 goes with it; the Z690 brings its own 64GB.

## READ THIS BEFORE TOUCHING THE 256GB DRIVE

`E:\Media\Grizzly\Curated` holds **601 files, 1.19 GB** of dated job photos
feeding the GBP posting workflow. Installing Proxmox on this drive destroys
them.

Move that directory somewhere durable and verify the copy before the drive
goes anywhere near an installer. It is the only thing on the disk worth
keeping (total usage is 2.29 GB, the rest is `$RECYCLE.BIN` and
`System Volume Information`).

## Current AIWA (source of truth to be migrated)

> **Corrected 2026-08-17 against the live host.** The figures originally in
> this section were estimates from a month-old local note and were wrong in
> every case. `aiwa-transplant/INVENTORY-2026-08-10.md` is the observed record
> and takes precedence over anything here.

- HP ProDesk, i5-9500, 32GB DDR4, at **192.168.1.12** — Proxmox VE 9.2.0,
  pve-manager 9.2.2, kernel 7.0.2-6-pve. Tailscale `100.87.155.47`.
- 2TB NVMe (WD_BLACK SN770): `pve-root` 96G (**47%** used), `pve-data` LVM-thin
  1710G (**1.03% ≈ 17.6 GB**, not 0.12%/~2 GB), `pve-swap` 8G
- 500GB SATA (Samsung 840 PRO, ~2013): `/mnt/samsung-sata`, **NTFS**,
  **56% used — 266 GB of live data, 211 GB free** (not "6% used"/~470 GB free).
  Hosts the `mav-transfer` Samba share, `mav-rag/` exports, a Chris backup and
  a syncthing folder.

**Guests:** 4 LXC containers, zero VMs — 100 `rustdesk`, 101 `orca`,
102 `hcp-mcp-prod`, 103 `mcc-prod`. All `onboot: 1`.

**Services — the original four-item list badly undersold the host.** It is
actually 7 Docker containers, 11 custom systemd units (4 Hermes, pacc-registry,
orca-aiwa, node_exporter, 3 hcp/metrics timers, pm2-root), syncthing, and a
weekly scraper cron. See `INVENTORY-2026-08-10.md` and
`HOST-CONFIG-PRESERVE.md` for the full picture.

**Also not captured anywhere until 2026-08-17:** `pm2-root` resurrects the
SEO-Agents-App services (seo-monitor, supabase-sync, mav-bridge, workers),
which per the deployment runbook **must** carry `TZ=America/Chicago`. What PM2
runs, and the env each process holds, has still never been captured — that is
Phase 0.3b of the backup plan.

## Hard constraints

Read `C:\Workspace\Active\brain\agent-memory\runbooks\aiwa-deployment.md`
and any repository runbook **first**.

- Use **Orca** for every AIWA action. Never SSH, SCP, or an ad-hoc remote shell
  **without Carter's explicit approval for that named exception** — that is the
  runbook's actual wording, and it is the mechanism, not a loophole. Carter
  granted one such exception on 2026-08-17: the **Proxmox web UI** at
  `https://192.168.1.12:8006`, for this session, in place of Orca. It is also
  reachable over Tailscale at `100.87.155.47:8006`.
- Author and test locally. AIWA is a deployment target, not a workspace.
- Explicit approval required before **any** live state change: service
  restart, timer/unit action, firewall change, backup restore, credential
  action. Ask before the first read-only command too.
- Retain an exact rollback ref for anything committed.

## Scope

**Preparation only. Do not attempt the cutover.**

### Where this actually stands — 2026-08-17

**Night 1 is done.** The X870E is built, running activated Windows 11 Pro off
the 9100 PRO, with a clean Device Manager, RDP and Tailscale
(`cmb-workbench` / `100.124.41.115`) reachable, and the driver kit restaged
locally. CPU 40°C, boot SSD 35°C.

**But the SN7100 did not move.** Night 1's plan had the 2 TB SN7100 and the
Realtek 2.5 GbE card coming out of the Z690 that night. Neither happened — as
of 2026-08-17 the X870E has exactly one disk (the 9100 PRO as `C:`) and one
NIC (onboard Realtek 2.5GbE at `192.168.1.220`, DHCP). Any part of this
document or the map that assumes `D:` exists, or that the `10.110.10.x` direct
link is up, is describing a future state.

This was a deliberate call, not a slip: the Z690 is still a complete working
machine and therefore the desktop's rollback. It stays intact until the X870E
has nothing left to prove — the same principle as assumption 5 below, applied
one layer up. Both parts move in a single teardown, with the HR-10 going on
the SN7100 in slot 3.

**Night 2 has therefore not happened either**, and nothing should be wiped on
AIWA until it has. The backup target `D:\aiwa-backups\` does not yet exist.
Phase 0.5 of the backup plan is the gate that catches this — run it first.

The clock that argues against unlimited prep: AIWA has **no verified off-host
backup** and runs on a 16,511-hour 840 PRO. If prep stretches, note the core
set is only ~33 GB and the 9100 PRO has 1793 GB free — Phases 1–3 plus a
core-set-only pull would give a real off-host copy with no SN7100 involved.
Only the 266 GB of 840 PRO data genuinely needs `D:`.

Out of scope: GPU passthrough (IOMMU/vfio). Separate session, after the host
is proven stable.

## Assumptions — challenge any of these

1. **Fresh Proxmox install + `vzdump` restore**, not a boot-disk transplant.
   Moving the boot disk to different hardware renames the NIC and breaks
   `vmbr0`, leaving the host unreachable.
2. **Backups go to three places, in priority order:**

   | # | Target | Role |
   |---|---|---|
   | 1 | `/mnt/samsung-sata` (~470GB free) | Primary dump — local and fast |
   | 2 | Main PC `D:` over SMB (930GB free on a WD_BLACK SN7100) | The copy to actually restore from |
   | 3 | SanDisk Ultra Dual Drive Luxe 256GB (`SDDDC4`) | Offline copy, disconnected, checksummed |

   The reason for copy 2: the 840 PRO lives inside the machine being torn
   apart. It gets unplugged, handled, remounted in a different chassis and
   powered by a different PSU. A backup that shares fate with the hardware
   being migrated is not a backup.

   The reason copy 3 is *not* copy 2: the SanDisk is a **thumb drive**, not
   an SSD. Sustained write is 30–60 MB/s and degrades as the pSLC cache
   fills; no DRAM, cheap controller, no power-loss protection. Its
   characteristic failure is *silent corruption*, so verify with checksums,
   never file counts. It earns its place only as a physically disconnected
   third copy.

   Sequencing: `D:` goes offline during the AM5 rebuild of the main PC. Do
   not schedule the AIWA restore for the same session as that rebuild.
3. The 2TB SN770 is wiped and rebuilt as a clean thin pool. Guest data comes
   back from the vzdumps.
4. Hostname and IP stay `192.168.1.12`.
5. The ProDesk is **not** decommissioned until the new host is verified
   serving every service. It is the rollback.

Assumptions 2 and 3 are coupled and load-bearing: wiping the 2TB is only
safe if the backups are genuinely verified **and** the offline copy exists.
See task 5.

## Tasks

1. **Inventory the current host.** Proxmox version, full guest list with
   VMIDs and disk sizes, `storage.cfg`, network config, what's listening.

2. **SMART health on both drives.** The SN770 is about to be the only copy of
   guest storage until the dumps exist; the 840 PRO is thirteen years old.
   Read-only, but ask first. The offline USB copy is what makes the 840 PRO's
   age tolerable — if that copy is skipped, the Samsung's health becomes a
   stop condition again.

   **Size the backup before choosing destinations.** `pve-data` reportedly
   reads 0.12% of 1.67TB (~2GB), which looks too small for a host running
   RAG, Prometheus, Samba and Hermes. That figure came from a month-old
   local note, not from the host. Get the real number. Everything downstream
   assumes the dumps fit in 256GB; if they don't, say so before anyone buys
   or wipes anything.

   Also confirm the SanDisk stick is empty of anything wanted before writing
   to it.

3. **Find the host-level config `vzdump` does not capture.** This is the
   most likely thing to get missed. At minimum: `/etc/network/interfaces`,
   `/etc/samba/smb.conf`, `/etc/fstab`, `/etc/pve/storage.cfg`, custom
   systemd units, crontabs, and any config the RAG service or Hermes gateway
   keep outside their containers.

4. **Write the backup plan, get approval, run it.** What gets dumped, in
   what mode, where, how long, how much space. Backups are a live operation.
   The plan must end with both secondary copies made and **checksum-verified**
   (not file-count-verified), and the SanDisk stick disconnected.

5. **Prove the backups restore.** A `vzdump` that has never been test-restored
   is not a backup. Propose how to demonstrate it.

6. **Write the cutover runbook** to a file in the repo. Ordered steps,
   verification gate at each stage, rollback point at each stage.

## Known traps

- **Intel RST VMD is enabled on that Z690 BIOS.** The Proxmox installer will
  not see any NVMe drive until VMD is disabled. This presents as dead
  hardware if you don't know about it.
- **The main PC's Windows install currently boots through the VMD driver.**
  AM5 has no VMD, so budget for a clean Windows install on the new board
  rather than expecting `C:` to boot after the swap.
- **NIC rename** is the reason for the fresh install. `/etc/network/interfaces`
  must be written for the *new* interface name, not copied verbatim.
- **Keep the UHD 770 iGPU enabled** and driving the console — that preserves a
  clean display path while the Radeon 9700 remains dedicated to AIWA inference.
- **Z690 M.2 topology is clean.** M.2_1 is CPU-fed PCIe 4.0 x4; M.2_2/3/4 are
  chipset-fed PCIe 4.0 x4. Verified against the manual: populating the second
  x16 slot or any M.2 disables nothing. No lane-sharing conflicts to plan around.
- **4 DIMMs of DDR4 train at 3466**, not the kit's rated 3600. Expected.

## Deliverables

- Written inventory of what is on AIWA today
- SMART verdict on both drives
- List of host-level config that must be preserved by hand
- Verified, test-restored backups on `/mnt/samsung-sata`
- Cutover runbook committed to the repo

Ask before the first live command. Report what you actually observed, not
what you expect to be true.
