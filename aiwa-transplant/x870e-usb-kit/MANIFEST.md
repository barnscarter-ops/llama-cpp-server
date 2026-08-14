# Kit manifest

Staged 2026-08-14. Every file below was downloaded from the vendor's own
domain and had its Authenticode signature checked on arrival — all four came
back `Valid` with the expected signer. Re-verify any time with
`..\verify-kit.ps1`.

## Staged and verified (4 files, ~1.18 GB)

| # | Path | Size | Signer | Sig |
|---|---|---|---|---|
| 1 | `02-chipset\amd_software_8.07.16.1035.exe` | 81,490,200 B (77.7 MB) | Advanced Micro Devices | Valid |
| 2 | `03-gpu\595.97-desktop-win10-win11-64bit-international-dch-whql.exe` | 957,358,592 B (913.0 MB) | NVIDIA Corporation | Valid |
| 3 | `05-storage\Samsung_Magician_Installer_Official_9.0.1.950.exe` | 204,608,280 B (195.1 MB) | Samsung Electronics Co. | Valid |
| 4 | `06-tools\MediaCreationTool_Win11.exe` | 21,591,048 B (20.6 MB) | Microsoft Corporation | Valid |

### SHA256

```
1B55DD2DD661D19C5EA4D49BD53B673783E673DB9E427B709D404BB1BAE66BDB  02-chipset\amd_software_8.07.16.1035.exe
979ED00FEA181C786F608967377D6D83AC82E6368275994A4182EC79D97B3122  03-gpu\595.97-desktop-win10-win11-64bit-international-dch-whql.exe
D46CF61AA5B5073138E9D063E91F1861776315A14DFCA3FFD8724611C21588D8  05-storage\Samsung_Magician_Installer_Official_9.0.1.950.exe
E887DFFF70BAF09A8C1DEBFE8C304DD9F2D9652FAE8B7C83B3C24554A79BBD7F  06-tools\MediaCreationTool_Win11.exe
```

### Sources

| # | URL |
|---|---|
| 1 | `https://drivers.amd.com/drivers/amd_software_8.07.16.1035.exe` |
| 2 | `https://us.download.nvidia.com/Windows/595.97/595.97-desktop-win10-win11-64bit-international-dch-whql.exe` |
| 3 | `https://download.semiconductor.samsung.com/resources/software-resources/Samsung_Magician_Installer_Official_9.0.1.950.exe` |
| 4 | `https://go.microsoft.com/fwlink/?linkid=2156295` (Microsoft fwlink → Windows 11 Media Creation Tool) |

### Notes on each

1. **AMD chipset 8.07.16.1035** — released 2026-07-30, current as of today.
   Covers X870E. This is the single most important driver in the kit; install
   it before the GPU driver.
2. **NVIDIA 595.97** — Game Ready, WHQL, DCH, Win10/11 64-bit international.
   Covers the RTX 4060 Ti 16 GB.
3. **Samsung Magician 9.0.1.950** — 195 MB. Samsung publishes no standalone
   firmware image for the 9100 PRO; Magician is the only supported update
   path. Current 9100 PRO firmware in the wild is `1B2QNXH7`.
4. **Media Creation Tool** — builds the bootable Windows 11 stick. It
   downloads Windows itself at run time, so run it on this PC while online.
   It reformats its target drive; use a different stick than the kit.

## Empty by design

`04-network\` and `01-BIOS\` are empty. That is not an oversight — see below.

## NOT staged — 4 items, must be downloaded in a browser

GIGABYTE's site returns **HTTP 403 to every scripted request** (bot
protection). I tried `www.gigabyte.com`, `aorus.com`, and direct guesses at
the `download.gigabyte.com/FileList/BIOS/` CDN paths; all refused or 404'd. I
did not fall back to third-party driver mirrors — for a BIOS image in
particular, an unofficial mirror is not an acceptable source.

So four items need a browser, and three of them can't even be chosen until
the **board revision** is read off the silkscreen. Details and links are in
`..\01-BIOS\DOWNLOAD-THESE-IN-A-BROWSER.md`.

| Item | Goes in | Blocking on |
|---|---|---|
| BIOS (revision-specific) | `01-BIOS\` | board revision |
| Wi-Fi 7 + Bluetooth driver | `04-network\` | board revision (chip differs) |
| Realtek 2.5GbE LAN driver | `04-network\` | 403 only |
| Realtek audio driver | `06-tools\` | 403 only |

Of these, only the **LAN driver** is install-critical, and only if Windows 11
doesn't bring the NIC up on its own (it usually does on 24H2+). The audio
driver comes down through Windows Update. The BIOS update is optional for a
9900X on a board that already posts — the CPU is supported by shipping
firmware.
