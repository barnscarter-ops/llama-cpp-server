> **Repo copy — documentation only.** The 1.2 GB of vendor installers this
> describes lives at `C:\Workspace\Active\x870e-usb-kit\` and is deliberately
> not committed. `MANIFEST.md` carries the SHA256 of every file so the staging
> folder can be verified against this record; `verify-kit.ps1` does it for you.
# X870E USB kit — clean Windows 11 install on the Samsung 9100 PRO

Staged 2026-08-14 for Night 1 of the AIWA transplant.
Target: Samsung 9100 PRO 2 TB in M.2 slot 1 (CPU-fed), becomes the new `C:`.
Board: GIGABYTE X870E AORUS ELITE WIFI7 · CPU: Ryzen 9 9900X · GPU: RTX 4060 Ti 16 GB.

Two files live next to this one:

- `MANIFEST.md` — every staged file with size, SHA256, signer, and source URL.
- `../01-BIOS/DOWNLOAD-THESE-IN-A-BROWSER.md` — the four files I could **not**
  fetch, with exact links and why.

Run `..\verify-kit.ps1` any time to re-check the kit against the manifest.

---

## READ THIS FIRST — three things that will bite

### 1. The USB stick must be made with the Media Creation Tool, not copied

`install.wim` inside Windows 11 is larger than 4 GB, and FAT32 cannot hold a
file that big. Dragging an ISO's contents onto a formatted stick produces a
USB that either won't boot or dies partway through Setup. Use
`06-tools\MediaCreationTool_Win11.exe` (staged, Microsoft-signed) and let it
build the stick. Rufus works too.

**Use a second stick for this kit.** MCT reformats its target drive.

### 2. Unplug the SN7100 before running Windows Setup

Windows Setup places the EFI System Partition on whichever disk it feels like
when more than one is present — frequently *not* the disk you selected for
Windows. If it lands the ESP on the SN7100, then pulling or reformatting that
drive later leaves the 9100 PRO unbootable, and the failure shows up weeks
after the install when you've forgotten why.

So: **install the SN7100 after Windows is up**, not before. One NVMe in the
machine during Setup — the 9100 PRO. This reverses the "install it now while
the slot is easy to reach" instinct, and it is worth the extra teardown.

The 2 TB SN7100 also carries the data that Monday's backup run writes to, so
it is not a drive to have exposed to a Windows installer's partitioning.

### 3. The BIOS file is revision-specific — do not guess

GIGABYTE ships **different BIOS images** for rev 1.0/1.1, rev 1.2, and rev 1.3
of this board, on three separate support pages. Flashing the wrong one via
Q-Flash Plus is a real brick risk, not a theoretical one.

The revision is silkscreened on the board (look near the PCIe slot / lower
edge, printed as `REV: 1.x`) and on the retail box label. Read it off the
hardware before you download anything. `DOWNLOAD-THESE-IN-A-BROWSER.md` has
the three URLs — pick one.

The Wi-Fi chip also changes with revision (MediaTek MT7925 on rev 1.0,
Realtek RTL8922AE on rev 1.1/1.2), so the same lookup settles the Wi-Fi
driver.

---

## Install order

**Phase A — before Windows**

1. Read the board revision off the silkscreen. Write it here: `REV: ______`
2. On this PC, download the BIOS + Gigabyte drivers for that revision
   (see `DOWNLOAD-THESE-IN-A-BROWSER.md`) into the matching kit folders.
3. Build the Windows 11 USB with the Media Creation Tool.
4. Copy this entire kit folder onto a **second** USB stick.
5. *(Optional but recommended)* Q-Flash Plus the BIOS with the CPU-less
   flashport: BIOS file renamed to **`GIGABYTE.bin`** in the root of a
   **FAT32** stick, stick in the dedicated Q-Flash Plus USB port, press the
   button, wait for the LED to stop blinking. Do not interrupt it.
6. In BIOS: enable **EXPO** for the DDR5, confirm the 9100 PRO is detected,
   confirm boot mode is UEFI with Secure Boot on (Win11 requirement), set the
   fan curves per the wiring below.

**Phase B — Windows Setup**

7. Only the 9100 PRO installed. SN7100 physically out.
8. Boot the MCT stick, install to the 9100 PRO, let it create its own
   partitions on that disk (delete any existing ones on it first).

**Phase C — drivers, in this order**

9. `02-chipset\amd_software_8.07.16.1035.exe` — **first, before anything
   else.** This is the AM5 platform package: PCIe, USB, the Ryzen power plan,
   and the chipset enumeration everything else depends on. Reboot after.
10. `03-gpu\595.97-...-dch-whql.exe` — NVIDIA. Choose a custom install and
    tick "perform a clean installation."
11. Networking: check Device Manager first. Windows 11 24H2+ usually brings up
    the Realtek 2.5GbE on its own. If it did, skip the Gigabyte LAN driver and
    let Windows Update supply Wi-Fi and audio. If it did **not**, install the
    Gigabyte LAN driver from the kit.
12. `05-storage\Samsung_Magician_Installer_Official_9.0.1.950.exe` — check the
    9100 PRO's firmware and update it if Magician offers one. There is no
    standalone firmware download for this drive; Magician is the only path.
13. Windows Update, twice, rebooting between passes.

**Phase D — after Windows is stable**

14. Power down, install the SN7100 in **slot 3** with the HR-10 cooler (fan to
    `SYS_FAN3`), boot, confirm it appears as **`D:`** with its data intact.

    > **Corrected 2026-08-17.** The HR-10 does **not** fit on the 9100 PRO in
    > slot 1 — the AIO takes that clearance. The 9100 PRO runs on the board's
    > stock M.2 heatsink and idles at 35°C. The HR-10 clears in slot 3, so the
    > SN7100 gets it, which is the right allocation anyway: that drive takes
    > the sustained backup writes. `SYS_FAN2` ends up unused.
    >
    > As of 2026-08-17 this step has **not** been done — the SN7100 and the
    > 2.5 GbE card are both still in the Z690.
15. Run `Get-Volume` and record the letters — Monday's backup plan writes to
    `D:\aiwa-backups\` and explicitly says to verify rather than trust.

---

## If the machine has no network after install

The kit is built so this isn't fatal, but have a plan:

- **USB tether a phone.** Windows has an inbox RNDIS driver; this works with
  no downloads and is the fastest fix.
- Or bring the Gigabyte LAN driver over on the kit stick (step 11).

Do not download Realtek or MediaTek drivers from third-party mirror sites to
solve this. Gigabyte's own support page is the trustworthy source.

---

## Fan and AIO wiring (as verified on the bench, for the BIOS step)

```
CPU_FAN   <- AIO "FAN"  (3 radiator fans)   PWM, quiet curve
CPU_PUMP  <- AIO "PUMP"                     PWM, leave at 100%
CPU_OPT   <- AIO "VRM"                      PWM  (ARCTIC specifies PWM here)
SYS_FAN1  <- fan hub PWM input
SYS_FAN2  <- (unused — HR-10 does not clear the AIO in slot 1, see note below)
SYS_FAN3  <- M.2 fan, SN7100 (HR-10 in slot 3)
D_LED1    <- AIO A-RGB (3-pin 5V only — never the 4-pin 12V header)
hub F-1   -> rear exhaust   (master port; the only one that reports RPM)
hub F-2   -> front trio     (case's own splitter)
hub POWER -> SATA or Molex direct from the PSU
```

Radiator mounts top, exhaust, fans underneath pushing up, tubes exiting at the
rear. RAM is in A2/B2. ARCTIC's instruction: connect the PWM control cable
**before** mounting the cooler to the board.
