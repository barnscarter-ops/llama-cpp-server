# AIWA Proxmox — Transplant Prep (handoff prompt)

> Written 2026-08-10. Preparation phase only — the cutover is blocked until
> the AM5 build frees the Z690. Hand this file to a fresh session.

## The move, in one line

An X870E AORUS Elite WiFi 7 motherboard + Ryzen 9 9900X becomes the main PC.
The main PC's current Z690 board + 13600K + 64GB RAM become the new AIWA. The
HP ProDesk is retired and sold.

## Hardware disposition

### Main PC — X870E AORUS Elite WiFi 7 + Ryzen 9 9900X

| Component | Source | Notes |
|---|---|---|
| X870E AORUS Elite WiFi 7 motherboard + 9900X | purchased | DDR5, 2 DIMMs only |
| 32GB DDR5 | allocated | Do NOT run 4 mismatched DIMMs |
| RTX 4060 | allocated | Main-PC GPU |
| 2TB WD_BLACK SN7100 (`C:`, Windows) | stays | see VMD note below |
| 1TB WD_BLACK SN7100 (`D:`, storage) | stays | |

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

- HP ProDesk, i5-9500, 32GB DDR4, at **192.168.1.12**
- 2TB NVMe (WD_BLACK SN770): `pve-root` 96G (32% used), `pve-data` LVM-thin
  1.67TB (0.12% used), `pve-swap` 8G
- 500GB SATA (Samsung 840 PRO, ~2013): `/mnt/samsung-sata`, 6% used, hosts
  the `mav-transfer` Samba share

Services (verify — this list may be incomplete):
RAG on 8181, Prometheus on 9090, Samba on 445, Hermes gateway.

## Hard constraints

Read `C:\Workspace\Active\brain\agent-memory\runbooks\aiwa-deployment.md`
and any repository runbook **first**.

- Use **Orca** for every AIWA action. Never SSH, SCP, or an ad-hoc remote shell.
- Author and test locally. AIWA is a deployment target, not a workspace.
- Explicit approval required before **any** live state change: service
  restart, timer/unit action, firewall change, backup restore, credential
  action. Ask before the first read-only command too.
- Retain an exact rollback ref for anything committed.

## Scope

**Preparation only. Do not attempt the cutover.**

The Z690 is still in the daily-driver desktop and won't be free until the AM5
build lands — at least a week out. The goal is that when the hardware is
free, the migration is mechanical with a verified backup behind it.

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
