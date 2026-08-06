# R9700 Install Runbook

Prepared 2026-08-04, before the card arrived. Target config: **Radeon AI PRO
R9700 (32GB, gfx1201) in PCIe slot 1, RTX 4060 Ti (16GB) in slot 2, 850W PSU**,
ASUS TUF Z690-Plus WiFi D4 / i5-13600K / 64GB.

## Pre-staged (already done)

- `llama-cpp-server-vulkan\` — llama.cpp **b10275 Vulkan** build, extracted and
  verified to launch. Current production build is CUDA b9550 in
  `llama-cpp-server\`, left completely untouched.
- Models are referenced by absolute path in `ecosystem.config.cjs`, so both
  builds share `llama-cpp-server\models\` with no copying.

Rollback is: point PM2 back at `llama-cpp-server\llama-server.exe`.

## Open items requiring physical access

### 1. Verify Vulkan actually works — DONE, resolved 2026-08-04

**Vulkan now works on this machine.** Root cause of the initial `Found 0 Vulkan
devices` was neither RDP nor a broken driver: the NVIDIA Vulkan ICD had simply
never been registered. `nvoglv64.dll` (46MB) and `nv-vk64.json` were both
present in the DriverStore at
`...\nv_dispsi.inf_amd64_bd43d31db3bd09e9\`, but `HKLM:\SOFTWARE\Khronos` did
not exist, so the Vulkan loader had nothing to load.

Fixed by registering the ICD (the standard mechanism a driver installer uses):

```powershell
$icd = "C:\Windows\System32\DriverStore\FileRepository\nv_dispsi.inf_amd64_bd43d31db3bd09e9\nv-vk64.json"
New-Item -Path "HKLM:\SOFTWARE\Khronos\Vulkan\Drivers" -Force
New-ItemProperty -Path "HKLM:\SOFTWARE\Khronos\Vulkan\Drivers" -Name $icd -PropertyType DWord -Value 0 -Force
```

To undo: `Remove-Item HKLM:\SOFTWARE\Khronos -Recurse`. This has no effect on
CUDA, which does not use the Vulkan ICD.

Enumeration and inference were both confirmed working, over RDP. The AMD driver
will add its own ICD value alongside this one; both cards can be registered
simultaneously.

### 1b. Backend A/B baseline (4060 Ti, measured 2026-08-04)

Same card, same model (`Qwen3.6-35B-A3B-UD-IQ3_XXS`), `-ngl 99 -fa 1 -r 3`:

| Backend | Build | pp512 (t/s) | tg128 (t/s) |
| --- | --- | ---: | ---: |
| CUDA | b9550 | 2422.6 ± 3.6 | 81.5 ± 0.2 |
| Vulkan | b10275 | 1562.5 ± 19.7 | 35.2 ± 0.2 |

**Do not read this as a prediction for the R9700.** This measures Vulkan *on an
NVIDIA driver*, where Vulkan is the second-class path and CUDA is native — a
36% prefill and 57% generation penalty is the well-known cost of that pairing.
On AMD the polarity flips: Vulkan is the fast path and ROCm/HIP is the
problematic one. The purpose of this run was to prove the b10275 Vulkan build
loads a model and generates end-to-end, which it does.

**Practical consequence:** keep the 4060 Ti on CUDA. Do not try to serve both
cards from one Vulkan llama-server. If you ever want the 4060 Ti serving
alongside the R9700, run it as a *separate* PM2 instance using the CUDA build on
a different port.

### 1c. Post-install verification (still to do)

After the AMD driver is installed, confirm the R9700 enumerates:

```powershell
C:\Workspace\Infrastructure\llama-cpp-server-vulkan\llama-bench.exe --list-devices
```

You want **two** devices listed, with the R9700 showing ~32GB. Note its index —
`GGML_VK_VISIBLE_DEVICES` in `ecosystem.config.cjs` currently says `0`, which is
the 4060 Ti today. It will almost certainly need changing. Do not assume.

If the R9700 does not appear, check that the AMD driver added its ICD value:

```powershell
(Get-Item "HKLM:\SOFTWARE\Khronos\Vulkan\Drivers").Property
```

There should be an `amd-vulkan64.json` (or similar) entry alongside the NVIDIA
one registered in step 1. If AMD's installer failed to add it, register it the
same way step 1 did, pointing at AMD's manifest.

Then benchmark the R9700 before migrating anything, and compare against the
CUDA baseline in 1b:

```powershell
$env:GGML_VK_VISIBLE_DEVICES="<r9700-index>"; C:\Workspace\Infrastructure\llama-cpp-server-vulkan\llama-bench.exe -m C:\Workspace\Infrastructure\llama-cpp-server\models\Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf -ngl 99 -fa 1 -r 3 -p 512 -n 128
```

Only flip PM2 over once that looks right:

```powershell
pm2 stop qwen3-llama; pm2 start qwen3-llama-vulkan
```

### 2. Driver install

Get the driver from AMD's official page (requires accepting their EULA, so do
this yourself):

<https://www.amd.com/en/support/downloads/drivers.html/graphics/radeon-ai-pro/radeon-ai-pro-r9000-series/amd-radeon-ai-pro-r9700.html>

Since the 4060 Ti is **staying in the system**, do *not* DDU the NVIDIA driver.
Both vendor stacks coexist fine. Install the AMD driver with the card already
physically seated.

### 3. Physical install notes

- R9700 goes in the **top slot** (PCIe 5.0 x16, CPU-attached).
- The 4060 Ti in slot 2 runs at **PCIe 4.0 x4 off the chipset** on this board.
  Fine for inference — weights load once, then traffic is minimal.
- Check the R9700's power connector type before you start pulling cables;
  partner cards ship as either 2x8-pin or a single 16-pin 12V-2x6. Board power
  is 300W. Give it its own PSU cables — do not daisy-chain a single cable to
  feed both the R9700 and the 4060 Ti.
- Drive both cards' displays off whichever card you normally use; multi-vendor
  display output on Windows is fine but keep it simple.

## The actual payoff: requantize

The current config is squeezed onto 16GB — `Qwen3.6-35B-A3B-UD-IQ3_XXS` at
12.3GB, with 64k context landing at ~15.26GB peak. IQ3_XXS is an aggressive
quant chosen purely because of the VRAM ceiling.

With 32GB, that constraint is gone. Re-pull the same model at **Q4_K_M or
Q5_K_M** and you get a real quality jump at the same context length, with room
to spare. This is a bigger win than any backend tuning — do it before spending
time re-running the tuning sweep.

### Model candidates at 32GB

Worth knowing before shopping: by current roundups, **Qwen3.6-35B-A3B — the
model already running here — is still considered the best all-round pick at the
32GB tier.** The problem was never the model, it was that IQ3_XXS was forced by
a 16GB ceiling. Re-pulling the *same* model at Q4_K_M is the cheapest real
quality gain available and needs no config changes beyond the filename.

If you do want to switch, the two that come up for this tier:

- **Qwen3.6 27B** (dense) — reported 77.2% SWE-bench Verified, the strongest
  verified coding score among models that fit consumer hardware. Dense, so
  slower per token than the A3B MoE, but higher ceiling per token. Also the
  easier LoRA target if fine-tuning comes back on the table.
- **Qwen3-Coder-Next** — built for agentic coding and long context, which is
  what actually matters when a coding agent reloads files, test output, and its
  own reasoning every turn.

These are third-party claims, not measured here. Bench any candidate against
the 1b baseline before switching production over.

Re-run the sweep in `benchmarks/` afterward; the b9550 CUDA numbers in
`ecosystem.config.cjs` comments (ubatch 1024, flash-attn auto, f16 KV) were
tuned for CUDA on a 16GB card and should be treated as unvalidated on Vulkan.

## Pinning to the right GPU

With both cards present, the Vulkan backend will enumerate **both**. Splitting a
model across an AMD and an NVIDIA card via Vulkan generally performs badly.
Pin llama-server to the R9700 alone:

```powershell
$env:GGML_VK_VISIBLE_DEVICES="0"
```

Confirm the index from `--list-devices` first — do not assume the R9700 is 0.

## Backend choice: Vulkan, not ROCm

Vulkan is the recommendation for inference on this card:

- It benchmarks ahead of ROCm for llama.cpp on the R9700
  ([Phoronix](https://www.phoronix.com/review/rocm-71-llama-cpp-vulkan)).
- The HIP/ROCm backend has an open bug where R9700s never return to idle clocks
  until the process exits ([ROCm #5706](https://github.com/ROCm/ROCm/issues/5706),
  [ROCm #6453](https://github.com/ROCm/ROCm/issues/6453)). That matters a lot
  here, because llama-guardian holds the server up for 30 minutes of idle after
  each use.
- ROCm has no native Windows PyTorch/ML path anyway — it needs WSL2.
