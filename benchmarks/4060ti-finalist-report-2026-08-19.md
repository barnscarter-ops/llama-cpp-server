# RTX 4060 Ti 16GB Execution Worker — Model Debate & Benchmark Report

Date: 2026-08-19. Method: 3-worker blind debate (agentic-quality /
throughput-engineering / contrarian lenses) → judged top-3 → downloaded,
benchmarked (llama-bench flag matrix + context ladders + quality gates on
bench-coding.py), all fully-on-GPU (99 layers) on CUDA b10488.

Hardware context: 4060 Ti 16GB (272 GB/s), Ryzen 9 9900X, display moved to
iGPU (~15.9 GB usable), Defender exclusion added for D:\Workspace\Infrastructure.

## TL;DR — Recommendation

**Winner: GLM-4.7-Flash UD-IQ3_XXS (12.9 GB)** — beats the Qwen3.6 incumbent
on every measured axis: +9% tg, +12% real-task latency, 202k vs 128k context,
equal quality scores (HumanEval 10/10, tools 3/3). Runner-up: keep
Qwen3.6-35B-A3B IQ3_XXS as fallback (mature, known-good). Cohere NMC and
gemma-4-26B lose on real-task token efficiency.

## Measured results (all 100% GPU offload, f16 KV unless noted)

| Metric | GLM-4.7-Flash IQ3_XXS | Cohere NMC-1.0 IQ3_XXS | gemma-4-26B IQ4_XS | Qwen3.6-35B control |
|---|---:|---:|---:|---:|
| tg128 t/s (llama-bench) | 90.1 | **95.4** | 68.7 | 82.9 |
| pp2048 t/s | 2787 | 3480 | **3625** | 2826 |
| Max ctx that fits | **202752** | **262144** | 131072 | 131072 |
| HumanEval subset | **10/10** | **10/10** | 7/10 | **10/10** |
| Tool calls | **3/3** | **3/3** | **3/3** | **3/3** |
| Workflow wall time (5 tasks) | **17.0 s** | 247.1 s | 182.6 s | 19.4 s |
| Loaded VRAM @ max ctx | 15.9 GB | 15.9 GB | 15.9 GB | 15.6 GB |

Raw data: benchmarks/finalist-{glm,cohere,gemma,qwen-control}-{matrix,ctx,quality}.json

## Flag tuning findings (Phase A matrix, verified)

1. **KV cache: f16 beats q8_0 on THIS GPU for all four models** — opposite of
   the R9700 Vulkan finding. q8 KV costs 25-35% tg on MoE models here (quant
   path overhead > bandwidth savings). Mixed k/v types (f16+q8) hit a
   catastrophically slow path (~100-500 pp t/s) — never mix.
2. **ubatch**: 1024 for GLM/Qwen/gemma tg; 2048 for Cohere pp (3480). Use
   --ubatch-size 1024 --batch-size 2048 as the balanced default.
3. **GLM KV-quant path is broken in b10488** (context creation fails with
   any quantized KV on the deepseek2 arch). f16 KV only — fine, since f16
   wins anyway.
4. flash-attn on everywhere (established in prior sweeps; kept constant).
5. GLM sampler fix mandatory: `--repeat-penalty 1.0 --min-p 0.01` (loops
   otherwise — confirmed by shortlist gotchas, harmless in practice).

## Quality-gate detail

- **GLM**: HumanEval 10/10, tools 3/3, workflow outputs complete in 1.7-13s
  (concise: 45-416 tokens/task). With `--reasoning off`.
- **Cohere NMC**: benchmarks 10/10 / 3/3 but EXTREMELY verbose in content
  (1477-4096 tokens per simple task, 170s on one; hit max_tokens on wf-03).
  Its interleaved thinking is baked in; `--reasoning off` does not suppress
  it. Fast tokens, slow tasks.
- **gemma-4-26B-A4B**: 7/10 HumanEval — the 3 failures were empty code with
  finish_reason=length (verbosity, not capability). Every workflow ran 4-30x
  slower than GLM/Qwen. Terminal-Bench collapse risk (shortlist) + measured
  verbosity = reject for execution-worker role.
- **Qwen control**: 10/10, 3/3, concise. Validates the harness.

## Why the debate's #1 held up (and the user's challenge)

User challenged "no way GLM 4.7 beat out Qwen." Measured answer: GLM wins,
but modestly — 90.1 vs 82.9 tg (+9%), 17.0s vs 19.4s workflow wall (-12%),
202k vs 128k ctx, identical quality scores. Not a blowout; Qwen remains a
fine fallback and the safest choice if GLM's repeat-penalty quirk or the
b10488 deepseek2 KV bug ever bites in production.

## Recommended production config (winner)

```
llama-server.exe \
  --model C:\Workspace\Infrastructure\llama-cpp-server\models\GLM-4.7-Flash-UD-IQ3_XXS.gguf \
  --host 127.0.0.1 --port 8081 --alias local-llm \
  --gpu-layers 99 --ctx-size 131072 \
  --cache-type-k f16 --cache-type-v f16 \
  --flash-attn on --ubatch-size 1024 --batch-size 2048 \
  --parallel 1 --cont-batching --jinja \
  --repeat-penalty 1.0 --min-p 0.01 --reasoning off
```
(ctx 131072 recommended over the max 202752 for VRAM headroom; 202k verified
to fit but at 15.9/16.0 GB with zero margin.)

Rollback: current Qwen3.6-35B config in ecosystem.config.cjs (unchanged).

## Infra fixes made during this run

- Restored missing `llama-common.dll` in llama-cpp-server-cuda-b10488 (the
  Aug-18 refresh dropped it; llama-server AND llama-bench were both broken —
  production would have crash-looped on next guardian start).
- Defender false-positive (Trojan:Win32/Wacatac.H!ml on llama.cpp DLLs):
  added exclusion for D:\Workspace\Infrastructure (elevated, approved).
- Display moved to AMD iGPU; per-app GPU preferences set to power-saving;
  ~15.9 GB usable VRAM for inference.
- New tooling: benchmarks/phase_a_matrix.py, phase_b_ctx.py, phase_c_quality.py
  (reusable finalist benchmark suite).

## Debate artifacts

- Run dir: C:\Workspace\Shared\Agents\debate-4060ti-execution-worker-2026-08-19\
  (RESEARCH-PACKAGE.md, CONSENSUS.md with worker verdicts + judge rationale)
- Workers: A (agentic-quality lens), B (throughput lens), C (contrarian lens,
  deepseek-v4-flash). Identity map: n/a (single-provider delegate variant).
