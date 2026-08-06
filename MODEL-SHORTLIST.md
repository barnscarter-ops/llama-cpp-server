# Model shortlist for the 32GB R9700

Compiled 2026-08-04 from two independent research passes (coding/agentic, and
general reasoning + tool calling) plus blob-level HuggingFace API verification
of every repo and file size. Nothing here is a guessed repo name.

## The sizing rule that eliminates most of the field

**VRAM is driven by TOTAL parameters, not active parameters.** All experts must
be resident. A 120B MoE with 6B active is still ~60-65 GB at Q4.

`Q4_K_M ≈ 0.55 GB per billion total params.` The 32 GiB card is ~34.4 decimal
GB. Practical ceiling: **~35B total params**, leaving 7-11 GB for KV cache.

This rules out the entire current frontier — GLM-5.2, DeepSeek V4, Kimi K3,
MiniMax M3, Mistral Small 4, gpt-oss-120b, Nemotron 3 Super. Do not be fooled
by low active-parameter counts.

## Quantize higher than 16GB habits suggest

Multiple 2026 sources report **Q4 producing invalid JSON under agent load**, and
the quality gain Q4→Q6 is larger than Q6→Q8. With 32 GB there is no reason to
sit at Q4 for a model under 30B. Prefer Q5_K_M / Q6_K for anything that will do
tool calling in anger.

Consequence: **27B dense at Q6 (26.0 GB) beats 35B MoE at Q6 (29.3 GB)** — the
latter is too tight to hold useful context.

## Ranked candidates (both passes agreed on the top 4)

| # | Model | Arch | License | Why |
| --- | --- | --- | --- | --- |
| 1 | **Qwen3.6-27B** | 27B dense | Apache 2.0 | SWE-V 77.2, LCB v6 83.9, Terminal-Bench 2.0 59.3. **AA index 37 — #1 in the 4B-40B class**, beating Nemotron 3 Super 120B. Independent SWE-rebench 31.2%. |
| 2 | **Qwen3.6-35B-A3B** | 35B / 3B active | Apache 2.0 | SWE-V 73.4, TAU3 67.2. ~4 pts behind #1 but far faster decode. Current production model. |
| 3 | **Gemma 4 31B** | 31B dense | Apache 2.0 | **LMArena 1451 — highest-ranked fitting model.** Terminal-Bench Hard 36.0, Tau2 76.9. Most battle-tested llama.cpp support (295+ GGUF repos, official ggml-org builds). |
| 4 | **GLM-4.7-Flash** | 31B / 3B active | MIT | **τ²-Bench 79.5 — best tool calling that fits.** 95% on an independent 40-case local tool harness (2nd of 13). |
| 5 | Devstral Small 2 24B | 24B dense | Apache 2.0 | SWE-V 68.0 — best coding-per-GB. First-class Cline/OpenHands integration. |
| 6 | Seed-OSS-36B | 36B dense | Apache 2.0 | 512K ctx, RULER@128k 94.6. Tunable thinking budget. Aug 2025, no successor. |
| 7 | Nemotron 3 Nano 30B-A3B | Mamba2 hybrid | NVIDIA open | RULER@1M 86.3 — best long-context retention by far. But SWE-V 38.8, weak coder. |
| 8 | Gemma 4 26B-A4B | 25B / 3.8B act | Apache 2.0 | Fast, but Terminal-Bench Hard collapses 36→14 vs the 31B. Weak at multi-step agent work. |
| 9 | Granite 4.0-h-small | 32B hybrid | Apache 2.0 | BFCL v3 73.7, IFEval 89.7 — best schema compliance. But MMLU-Pro 64, shallow reasoning. |

### Downloaded but disqualified / deprioritized

- **EXAONE-4.5-33B** — LCB v6 81.4 (top-3 in class) but the license is
  **EXAONE 1.2-NC, non-commercial**. Fine to benchmark, cannot be adopted.
- **gpt-oss-20b** — vendor claims SWE-V 60.7; independent Terminal-Bench 2.0
  measures **3.1%**. That is the starkest vendor-vs-reality gap found in the
  whole survey. Kept only because it is 11 GB and costs nothing to measure.
  Note the weights are natively MXFP4 — this IS full precision, not a quant.

## Per-model runtime gotchas

- **GLM-4.7-Flash** requires `--repeat-penalty 1.0 --min-p 0.01` or it loops.
- **Gemma 4** — use post-2026-07-16 weights; a silent refresh fixed tool calling.
- **Qwen3.6-27B** is *notably verbose* (AA measured 140M eval output tokens vs a
  37M median). Real per-turn latency cost in an agent loop. Thinking is on by
  default; disable with `--chat-template-kwargs '{"enable_thinking":false}'`.
- **Nemotron 3 Nano** has no dedicated tool parser; NVIDIA specifies `qwen3_coder`.
- **The `/no_think` soft switch is gone.** Qwen3.5/3.6 cards say so verbatim:
  "Qwen3.5 does not officially support the soft switch of Qwen3, i.e. `/think`
  and `/nothink`." The only mechanism is
  `chat_template_kwargs: {"enable_thinking": false}`. Thinking is ON by default.
  Qwen3.6 adds `preserve_thinking` (default keeps only the latest turn's trace).
- **GLM-4.7-Flash context is 202,752, not 131,072.** The 131072 on the card is
  `max_new_tokens`. Its thinking toggle is `enable_thinking` + `clear_thinking`;
  for agentic work the card recommends `enable_thinking: true,
  clear_thinking: false` ("Preserved Thinking").
- **All Qwen3.5/3.6 models are `image-text-to-text`** — causal LM *plus* a vision
  encoder. Text-only inference is unaffected, but vision needs a separate mmproj
  file, and vision GGUF support is the least-tested path.

## llama.cpp support maturity — checked against merged PRs

| Model | Arch in llama.cpp | Merged | Risk |
| --- | --- | --- | --- |
| GLM-4.7-Flash | `Glm4MoeLiteForCausalLM` → `DEEPSEEK2` | **2026-01-19, release day** | lowest |
| Qwen3.6-27B / 35B-A3B | Qwen3.5 path (`qwen3_5`/`qwen3_5_moe`) | 2026-02-09 (#19468, after a merge-and-revert) | open crash reports |

Two open llama.cpp issues on the Qwen3.6 path: CUDA illegal-memory-access on
35B-A3B vision requests (#25717) and a llama-server CUDA crash on 27B (#23210).
**Both are CUDA-specific — we are running Vulkan, so they may not apply here.**
Worth knowing if a model fails to load: it may be the backend, not the file.

**Do not substitute REAP-pruned or abliterated GGUF variants.** They circulate
under near-identical names and are modified models, not straight conversions.
The GLM-4.7-Flash file downloaded here is the standard `unsloth` conversion, not
the `-REAP-23B-A3B-` one.

**No vendor ships official GGUF for any of the top candidates.** Qwen's official
quants are FP8 and GPTQ-Int4 only; zai-org's are FP8 only. Qwen's own README
points users at community quants. The Unsloth/bartowski/ggml-org builds used
here are the intended path, not a workaround.

## Trust warnings — read before believing any number above

**Vendor agentic benchmarks are systematically inflated at this size.** Where an
independent measurement exists, the gap is large and consistently in one
direction:

| Model | Vendor claim | Independent |
| --- | --- | --- |
| Qwen3.6-35B-A3B | Terminal-Bench 2.0 51.5 | 24.6% (tbench.ai) |
| Qwen3.6-27B | SWE-V 77.2 | SWE-rebench 31.2% |
| gpt-oss-20b | SWE-V 60.7 | Terminal-Bench 2.0 3.1% |

Different harnesses, so not apples-to-apples — but treat every vendor agentic
figure as an **upper bound**, which is exactly why we bench locally.

**Which independent leaderboards are actually alive in Aug 2026** (verified by
pulling their underlying data files, not their rendered pages):

| Source | Status | Notes |
| --- | --- | --- |
| Artificial Analysis | **live** | Intelligence Index v4.1, AA runs every eval itself. 591 model records, `isOpenWeights` flag. |
| arena.ai (was lmarena.ai — 301 redirect) | **live** | Style-controlled human-vote Elo. 42 of 125 text models open-weight. |
| taubench.com / tau2-bench | **live** | Submissions dated as recently as today. Sierra-run entries carry trajectories; vendor entries are flagged `Unverified submission`. |
| BFCL v4 | live, roster 4 months stale | See below. |
| **RULER** | **dead** | Leaderboard table last committed **2025-10-09**. No 2026 models. |
| **LongBench v2** | **dead** | Last update **2025-05-06**. No 2026 models. |
| **HF Open LLM Leaderboard** | **archived** | Frozen at **2025-03-20**; top entries are Qwen2.5-era finetunes. |
| MMLU-Pro / IFEval | **no live board** | AA dropped MMLU-Pro from Index v4.1 and replaced IFEval with IFBench. |

Any page claiming current 2026 standings on RULER, LongBench v2, MMLU-Pro, or
the HF Open LLM Leaderboard is fabricating them.

**A note on provenance in τ-bench:** vendor submissions are Pass^1-only with no
trajectories. DeepSeek used *its own model* as the user simulator. NVIDIA's
Nemotron-Orchestrator entry sets `modified_prompts: true` and is a router
between strong/weak models, not a single open model. Scores from before
v1.0.1 (Jul 2026) are not comparable — banking grading was fixed.

**Independent coverage is otherwise thin and partly stale:**
- Aider polyglot leaderboard last updated **2025-11-20** — no 2026 model has an
  independent Aider score.
- BigCodeBench board is stale; only IBM publishes it. Any BigCodeBench number
  attributed to Qwen/Gemma/Nemotron/Mistral/gpt-oss is fabricated.
- **BFCL v4 is live but the roster is stale.** Correcting an earlier note here:
  the board's JS-rendered HTML hides real data files that *are* reachable —
  <https://gorilla.cs.berkeley.edu/data_overall.csv> (109 rows), plus
  `data_agentic.csv`, `data_multi_turn.csv`. Last updated **2026-04-12**, and
  every entry is Berkeley-run, not vendor-submitted. The catch: the roster
  contains **no Qwen3.5/3.6, GLM-5.x, Kimi K3, DeepSeek V4, MiniMax-M3, or
  gpt-oss**. Its "best open model" is GLM-4.6 at 72.38% overall. Read that as
  "best among models BFCL has gotten around to running."
  Also note v4 shipped **Jul 2025**, not Apr 2026 as one SEO blog claimed.
  Watch for tool-format overfitting: xLAM / Arch-Agent / BitAgent post high
  multi-turn AST scores but near-zero agentic (BitAgent-Bounty-8B: 0.00% web
  search, 1.51% memory).
- Gemma 4 publishes **no SWE-bench Verified** for any size.

**Models that do not exist** (ignore any blog claiming otherwise): Llama 5 —
Meta shipped no open-weight LLM in 2026 and went closed with Muse Spark in
April; gpt-oss-2; DeepSeek R2; Phi-5; Granite 5; Codestral 2026 (went
proprietary); Gemma 4 27B (real sizes are E2B/E4B/12B/26B-A4B/31B).

**Domains that produced verifiable fabrications during this research** — do not
cite: spheron.network, contracollective.com, codersera.com, serenitiesai.com,
aimadetools.com, gemma4.wiki, and the chroniclejournal.com / ragyfied.com
syndication network.

## Unresolved

- Gemma 4's model card claims a Jan 2025 pretraining cutoff while reporting AIME
  2026 scores. Not reconciled — flagged rather than guessed.
- **Cohere North Mini Code** (30B / ~3B active, AA index 27.6) is the only
  Western coding-specific model in the class, but the research hit its search
  budget before confirming license, context, or benchmarks. Worth a follow-up.
- **Intern-S2-Mobius 35B** (Apache 2.0) released ~2026-08-04 with a novel
  non-Transformer architecture and zero published coding benchmarks. Expect
  llama.cpp quant support to be broken for weeks. Revisit later.

## Next

1. `--list-devices` → find the R9700 index (`GGML_VK_VISIBLE_DEVICES` is still
   `0`, which is the 4060 Ti).
2. `bench-models.ps1 -Backend vulkan -Device <idx>` → speed across the field.
3. Cut to 3-4 finalists, then `tune-vulkan.ps1` on the winner.
4. Quality-test the finalists on real repo tasks. Speed is not the decision —
   the vendor-vs-independent gaps above are the whole reason to measure.
