# Q4_K_M vs Q5_K_M perf A/B — R9700 Vulkan (2026-08-11)

Same-night A/B, identical flags, exclusive GPU (guardian parked during the
window): `-ngl 99 -fa 1 -b 2048 -ub 512 -r 3 -p 512 -n 128 -d 0,4096`,
build 4308a4f03 (b10275), `VK_LOADER_DRIVERS_SELECT=*amd*`, R9700 sole device
(verified in stderr both runs).

| Quant | Size | pp512 | tg128 | pp512 @d4096 | tg128 @d4096 |
|---|---:|---:|---:|---:|---:|
| Q5_K_M | 25.22 GiB | 3247.7 ± 21.2 | 131.5 ± 0.5 | 3038.0 ± 12.4 | 128.2 ± 1.1 |
| Q4_K_M | 21.10 GiB | 3345.9 ± 18.0 | **140.5 ± 0.4** | 3114.6 ± 13.8 | **136.1 ± 0.8** |

Q4_K_M: +3% prefill, **+7% token gen**, −4.1 GB VRAM/commit/disk-read
(→ proportionally faster cold loads, less exposure to the guardian health
window). Unsloth ppl gap Q4 vs Q5 on this MoE is small (≈6.61 vs 6.58);
quality bench (bench-coding.py harness) still pending before a prod switch.

## Incident found during the run

First bench attempt silently landed on **CPU** (75 pp / 10 tg): the AMD
Vulkan ICD was wedged — `vkCreateInstance` hung or returned no AMD device —
after the evening's llama-server taskkill teardowns. Device Manager showed
the R9700 as healthy (CM_PROB_NONE) throughout. Recovery: runbook double
`pnputil /restart-device` (R9700-SWAP-HANDOFF.md), after which the AMD ICD
enumerated again.

Two operational lessons:

1. **"llama is healthy" ≠ "llama is on the GPU".** With the ICD wedged and
   the `*amd*` filter excluding Intel, llama-server falls back to CPU, loads
   in ~100s, and answers `/v1/models` — the guardian calls that healthy and
   serves ~10 t/s. Guardian has no GPU-placement check today.
2. Unfiltered enumeration orders the iGPU at index 0 and the R9700 at 1 —
   the `VK_LOADER_DRIVERS_SELECT=*amd*` filter is what keeps production
   deterministic (confirmed still working in-shell tonight).
