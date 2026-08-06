# R9700 Swap — Live Handoff

Written 2026-08-05, immediately before powering down for the physical install.
Purpose: the Claude Code session that planned this swap runs **on the PC being
opened**, so it dies at shutdown. This file carries the state to whatever
session picks up next (laptop, or the PC after it comes back).

Read `R9700-INSTALL.md` in this repo for the full rationale. This file is the
operational checklist and the measured baseline.

## Rollback

- Repo rollback ref: **`4065d57`** ("chore: pre-R9700-swap baseline").
- Runtime rollback: point PM2 back at `llama-cpp-server\llama-server.exe`
  (`pm2 start qwen3-llama`). The CUDA b9550 build is untouched throughout.

## Pre-swap baseline (measured, 2026-08-05)

| Item | Value |
| --- | --- |
| PM2 | 12 processes, all `online` before the stop below |
| Vulkan ICD entries | **1** — NVIDIA `nv-vk64.json` only |
| GPU | RTX 4060 Ti, 16380 MiB, driver 610.62, index 0 |

ICD registry path: `HKLM\SOFTWARE\Khronos\Vulkan\Drivers`, single value:

```
C:\Windows\System32\DriverStore\FileRepository\nv_dispsi.inf_amd64_bd43d31db3bd09e9\nv-vk64.json
```

Backend A/B baseline to beat — 4060 Ti, `Qwen3.6-35B-A3B-UD-IQ3_XXS`,
`-ngl 99 -fa 1 -r 3`:

| Backend | Build | pp512 (t/s) | tg128 (t/s) |
| --- | --- | ---: | ---: |
| CUDA | b9550 | 2422.6 ± 3.6 | 81.5 ± 0.2 |
| Vulkan | b10275 | 1562.5 ± 19.7 | 35.2 ± 0.2 |

That Vulkan row is Vulkan *on an NVIDIA driver* — the slow pairing. Do not read
it as a prediction for the R9700, where Vulkan is the fast path.

## Already done before shutdown

- `4065d57` committed, working tree clean.
- `pm2 stop qwen3-llama llama-guardian` — both `stopped`, other ten online.
- **`pm2 save` deliberately NOT run.** The saved dump still lists them as
  `online`, so they resurrect on boot. That is intentional: `qwen3-llama`
  starting on the CUDA build is a free smoke test that the 4060 Ti survived the
  move to slot 2. Stop them again before benchmarking.
- Driver downloaded: **AMD Software: Adrenalin Edition 26.7.1 (WHQL
  Recommended)**, 849 MB, 2026-07-28. There is no PRO Edition offered for
  Windows 11 on the R9700 download page — Adrenalin is the only real option.

## Phase 2 — physical

1. Full shutdown, **PSU switch off**, hold case power button ~5s to discharge.
2. Ground on bare chassis.
3. 4060 Ti out of slot 1 → into **slot 2** (PCIe 4.0 x4, chipset-attached —
   fine, weights load once then traffic is negligible).
4. **R9700 into slot 1** (PCIe 5.0 x16, CPU-attached). Clip clicks. Screw both
   brackets down — a 300W card hanging on its power connector causes
   intermittent faults later.
5. **Separate PSU cables to each card.** Do not daisy-chain one cable's second
   connector to the other card. R9700 connector is either 2x8-pin or a single
   16-pin 12V-2x6 depending on the partner board.
6. Display cable onto the **4060 Ti**. Its driver is known-good, so a bad AMD
   install still leaves a picture on screen.
7. Check for loose screws, unplugged fans, cables in blades. Close up, PSU on.

## Phase 3 — first boot, BEFORE any driver

The R9700 will show as an unknown / basic display device. **That is expected.**

```bash
nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv
```

Expect the 4060 Ti reporting normally. If this fails, it is a seating or power
problem, not a driver one — fix it now, before an AMD install muddies the
picture.

Confirm the R9700 appears in Device Manager under Display adapters, even as
unknown. **If it does not appear at all, power down and reseat.** Do not install
a driver for a card Windows cannot see.

## Phase 4 — AMD driver

Run the Adrenalin 26.7.1 installer.

- **Do NOT check "Factory Reset" / "Clean install".** It wipes existing display
  driver state and you will be reinstalling the NVIDIA driver at midnight.
- **Do NOT DDU the NVIDIA driver.** The 4060 Ti is staying; both vendor stacks
  coexist fine.
- Decline optional extras (overlay, recording, bundleware). You want the driver
  and its Vulkan ICD, nothing that hooks the desktop.

Reboot when it asks.

## Phase 5 — verify enumeration

```bash
reg query "HKLM\SOFTWARE\Khronos\Vulkan\Drivers"
```

Expect an **AMD entry alongside** the NVIDIA `nv-vk64.json` one — 2 values now,
where the baseline had 1. If AMD's installer failed to add it, register it
manually the same way §1 of `R9700-INSTALL.md` registered NVIDIA's.

```bash
C:/Workspace/Infrastructure/llama-cpp-server-vulkan/llama-bench.exe --list-devices
```

Expect **two devices, R9700 showing ~32GB**. Note its index.
**Do not assume it is 0** — `GGML_VK_VISIBLE_DEVICES` in
`ecosystem.config.cjs:111` currently says `0`, which is the 4060 Ti today.

## Phase 6 — benchmark before changing anything

Stop the llama services again first so nothing holds VRAM:

```bash
pm2 stop qwen3-llama llama-guardian
```

Then, same model and flags as the baseline so the comparison is fair:

```bash
GGML_VK_VISIBLE_DEVICES=<r9700-index> "C:/Workspace/Infrastructure/llama-cpp-server-vulkan/llama-bench.exe" -m "C:/Workspace/Infrastructure/llama-cpp-server/models/Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf" -ngl 99 -fa 1 -r 3 -p 512 -n 128
```

Compare against pp512 2422.6 / tg128 81.5.

## Phase 7 — cut over

Update `GGML_VK_VISIBLE_DEVICES` at `ecosystem.config.cjs:111` to the R9700
index, then:

```bash
pm2 start qwen3-llama-vulkan
```

Once that is confirmed healthy: `pm2 delete qwen3-llama` and `pm2 save`.
Rollback the whole way is `pm2 start qwen3-llama`.

## The actual payoff — do not stop before this

Production is still on `Qwen3.6-35B-A3B-UD-IQ3_XXS` (13.2GB), an aggressive
quant chosen purely because of a 16GB ceiling **that no longer exists**.

`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` (22.7GB) is already downloaded and sitting in
`llama-cpp-server/models/`. Swapping the filename is a bigger quality gain than
any backend tuning. `Q5_K_M` (27.1GB) is there too.

Afterward, re-run the tuning sweep (`tune-vulkan.ps1`, `bench-models.ps1`) — the
ubatch/flash-attn/KV settings in `ecosystem.config.cjs` comments were tuned for
CUDA on a 16GB card and are **unvalidated on Vulkan**.

## Known environment gotchas for the next session

- The PowerShell tool was EPERM-blocked in the planning session. Use the Bash
  tool for PC-side commands.
- Bash tool `cwd` is `C:\Workspace\Infrastructure`, which is **not** a git repo.
  The repo is the `llama-cpp-server` subdirectory — use `git -C`.
- PM2's service reads `C:\ProgramData\pm2\`, **not** `C:\Users\carte\.pm2\`.
  The user-profile dump is stale leftover (backed up as
  `dump.pm2.bak-20260805-pre-gpu`).

## Next after the swap

Fix the PC↔AIWA p2p link: static `10.110.10.2/30` on the PC adapter, no
gateway, then `ping 10.110.10.1`. Link is up at 2.5G and an IPv6 neighbor is
present, but IPv4 ARP fails — the PC adapter is likely on DHCP/APIPA.
