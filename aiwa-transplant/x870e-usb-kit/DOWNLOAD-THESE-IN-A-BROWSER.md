# Four files I could not fetch — grab these in a browser

GIGABYTE returns HTTP 403 to scripted downloads. Everything here has to come
from a real browser session on this PC, before the kit goes to USB.

## Step 1 — read the board revision off the hardware

Printed on the PCB as `REV: 1.x`, usually along the lower edge near the PCIe
slot, and repeated on the retail box label. The board is already in the case,
so this may mean a flashlight and a look past the GPU.

**This is not optional.** GIGABYTE publishes different BIOS images per
revision. Q-Flash Plus will happily write a wrong-revision image and the
result is a board that doesn't post.

Write it down: `REV: ________`

## Step 2 — open the matching support page

| Your revision | Support page |
|---|---|
| 1.0 or 1.1 | https://www.gigabyte.com/Motherboard/X870E-AORUS-ELITE-WIFI7-rev-10-11/support |
| 1.2 | https://www.gigabyte.com/Motherboard/X870E-AORUS-ELITE-WIFI7-rev-12/support |
| 1.3 | https://www.gigabyte.com/Motherboard/X870E-AORUS-ELITE-WIFI7-rev-13/support |

If the 1.3 URL 404s, find it from
https://www.gigabyte.com/ → Support → search "X870E AORUS ELITE WIFI7" and
pick the revision-matched entry from the list.

## Step 3 — download these four

### a) BIOS → save into `01-BIOS\`

Support page → **BIOS** tab → newest version. Downloads as a `.zip`
containing the image plus Gigabyte's flash utilities.

**For Q-Flash Plus:** extract the BIOS image, rename it to exactly
`GIGABYTE.bin`, and put it in the **root** of a **FAT32**-formatted USB
stick. Not the kit stick — a small separate one. Q-Flash Plus reads only that
filename, only from FAT32, only from the one dedicated rear USB port.

Note the version you downloaded here: `F____`

### b) Wi-Fi 7 + Bluetooth → save into `04-network\`

The chip depends on revision, which is why step 1 comes first:

| Revision | Wi-Fi chip | Driver to grab |
|---|---|---|
| 1.0 | MediaTek MT7925 | MediaTek Wi-Fi 7 |
| 1.1 / 1.2 | Realtek RTL8922AE | Realtek Wi-Fi 7 |
| 1.3 | check the page | whatever it lists |

Grab **both** the Wi-Fi driver and the Bluetooth driver — they're separate
downloads on Gigabyte's page and Bluetooth is easy to forget.

If you can't determine the revision and don't want to risk it: download the
Wi-Fi drivers for **both** chips. They're small, they cost nothing to carry,
and the wrong one simply won't install.

### c) Realtek 2.5GbE LAN → save into `04-network\`

Listed under Drivers → LAN. **This is the one that matters most** — it's the
only item in the whole kit that, if missing, can leave the machine unable to
fetch anything else.

In practice Windows 11 24H2+ has an inbox RTL8125 driver and the NIC comes up
during Setup. This is the insurance policy. Take it anyway.

### d) Realtek audio → save into `06-tools\`

Drivers → Audio. Lowest priority — Windows Update supplies this. Grab it if
you're already on the page.

## Step 4 — record what you got

Append to `..\00-README\MANIFEST.md`, or just note here:

```
Board revision : REV ______
BIOS version   : F_____   file: ______________________________
Wi-Fi driver   : ______________________________
Bluetooth      : ______________________________
LAN driver     : ______________________________
Audio driver   : ______________________________
```

Then run `..\verify-kit.ps1` — it re-hashes the four staged files and reports
whether these four folders are still empty.
