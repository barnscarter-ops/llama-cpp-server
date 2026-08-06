# Next Session Handoff — post-Q5_K_M-cutover tasks

Written 2026-08-06 (~09:20) after the Q5_K_M requant + guardian hardening
session. Prior context: R9700-SWAP-HANDOFF.md (cutover) and git log
e93d5f1..a4cae79 (this session's three commits).

## Current state (verified)

- Production: `qwen3-llama-vulkan` serving **Qwen3.6-35B-A3B-UD-Q5_K_M**
  (25.2 GiB, alias `qwen3.6-35b` — quant-neutral now, consumers unchanged),
  64k ctx, f16 KV, 25.8/32 GB VRAM. `llama-guardian` proxying 8080.
- Measured (Vulkan b10275, WU driver): pp512 3258.5 / tg128 134.0;
  under guardian 112-127 t/s tg. Perplexity wikitext-2: Q5_K_M 5.87 vs
  IQ3_XXS 6.20. Quality: HumanEval 9/10 + tools 3/3 at 16k budget
  (benchmarks/bench-qwen36-q5km-16k.json).
- Guardian hardened (a4cae79): 45s stop→start GPU cooldown on every start
  path (`STOP_START_COOLDOWN_S`), idle reap 30→60 min. Deployed + verified.
- bench-coding.py grew `--max-tokens` — Qwen3.6 burns a 4096 budget in
  reasoning_content and returns empty content; use 16384 for quality runs.
- Repo HEAD: `a4cae79` on main, tree clean. NOT yet pushed.

## Hard rules (one violation = hard reboot, learned 2026-08-06)

- **NEVER run two llama-server instances on the R9700.** Never hand-launch
  outside PM2/guardian. A second server loading while VRAM churned TDR'd
  amdkmdag.sys (bugcheck 0x116) and hard-crashed the box mid-session.
- Leave ~1 min between big model unload/load. The guardian now enforces
  this; don't bypass it in manual/bench work — for A/B model benches, run
  models strictly sequentially with a sleep between (see Task 2 note).
- Stay on the WU base driver (32.0.22042.14002, oem56.inf). No Adrenalin.
- Community fallback if TDRs recur under normal ops: disable Windows memory
  compression (`Disable-MMAgent -mc`, elevated, reboot; reversible with
  Enable-MMAgent). Several AMD/Windows llama.cpp users report it fixed all
  stability issues. Not applied yet — prod has been stable without it.

## Environment gotchas (unchanged, still bite)

- PM2 needs elevation: `gsudo -d "pwsh -NoProfile -File <script>"` with
  PM2_HOME=C:\ProgramData\pm2. Write .ps1 to a scratch dir — inline quoting
  through gsudo mangles. env/args changes need pm2 delete + re-add, not
  restart. After stop/delete, check for orphaned llama-server.exe.
- Loopback 127.0.0.1:8080 = Hermes qwen-worker SMS adapter, NOT the
  guardian. Test guardian via Tailscale 100.124.216.11:8080 (see Task 3).
- Qwen3.6 with small max_tokens: finish_reason=length with all tokens in
  reasoning_content and content="". Use max_tokens≥4096 (16k for benches),
  or chat_template_kwargs {"enable_thinking": false} per request.

## Task 1 — DONE (Q5_K_M requant, this session)

## Task 2 — Vulkan tuning sweep (re-run against Q5_K_M)

ubatch/flash-attn/KV flags in `ecosystem.config.cjs` are still the CUDA/16GB
tune. Re-sweep on Vulkan/R9700 with Q5_K_M:

- `tune-vulkan.ps1`, `bench-models.ps1`, `benchmarks/tune-sweep.py`.
  Baseline to beat: pp512 3258.5 / tg128 134.0 (defaults, -fa 1).
- Stop guardian + server first (gsudo pm2 stop both; verify no orphan
  llama-server, port 8081 free). **Sequential runs only, sleep ≥60s between
  model loads — see hard rules.** Restart both + pm2 save after.
- Candidates: ubatch 512/1024/2048, batch 2048/4096, fa auto vs on,
  `--parallel` >1 now that VRAM allows (guardian assumes 1 slot — check
  generation_lock logic in llama-guardian.py before raising).

## Task 3 — port 8080 overlap (guardian vs Hermes SMS)

Unchanged from last handoff. Hermes `qwen-worker` SMS adapter binds
127.0.0.1:8080; guardian binds 0.0.0.0:8080; Windows routes loopback to
Hermes. Works today only because MCC uses the Tailscale IP. Fix: move the
Hermes SMS adapter port (config under
`C:\Users\carte\AppData\Local\hermes\profiles\qwen-worker\`). Coordinate
with Carter first — that gateway is live (sms/buzz/api_server connected).

## Task 4 — NVIDIA cleanup + 4060 Ti plan (backburner, ask Carter first)

- 4060 Ti out of the machine (CM_PROB_PHANTOM); nvcontainer.exe crash-loops
  (NvBackend64.dll 0xc0000409). Cleanup candidate: NVIDIA App/driver stack.
  **Do not** delete `llama-cpp-server\llama-server.exe` (CUDA build) — it's
  still the rollback path.
- Carter debating a rack server for the 4060 Ti — plan, don't act.

## Task 5 — reasoning-token budget hardening (new, from this session)

The guardian's seed-nudge retry on finish_reason=length is a bandaid.
Agreed direction, not yet built:
1. Guardian enforces a max_tokens floor (~4096) on proxied completions so
   naive callers can't starve the answer phase.
2. Document/adopt `chat_template_kwargs: {"enable_thinking": false}` for
   interactive consumers (MCC simple queries, qwen-submit fast paths).
Note: llama.cpp `--reasoning-budget` only supports 0/-1 — no token-capped
thinking, so per-request routing is the only middle ground.

## Also open (not this repo)

- Push main to origin (4 local commits).
- AIWA `aiwa-prod-102` (.14): Orca runtime port 6768 down (ping OK, SSH up).
  Investigate via Orca only, per AIWA runbook.
