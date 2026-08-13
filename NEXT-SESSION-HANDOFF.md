# Next Session Handoff — post-Q5_K_M-cutover tasks

Written 2026-08-06 (~09:20) after the Q5_K_M requant + guardian hardening
session. Prior context: R9700-SWAP-HANDOFF.md (cutover) and git log
e93d5f1..a4cae79 (this session's three commits).

## Current state (verified 2026-08-06 ~19:09)

- Production **running**: `qwen3-llama-vulkan` + `llama-guardian` (pm2 save done).
  Q5_K_M, alias `local-llm`, 64k ctx, f16 KV, **ub512 / b2048 / flash-attn on**
  (Vulkan sweep winner). SI driver `oem56`/`u0200492` @ 32.0.22042.14002.
- Boot 18:33:42 after full TDR stack (TDR 60/60/120/10, MemoryCompression False,
  phantom NVIDIA + Adrenalin residuals purged). Canary + 9-config sweep: **zero**
  new WATCHDOG / no 1001/41 this boot. Leave soaking; watch idle-unload pattern.
- Depth-realistic bench (32k): **pp 2260.7 / tg 111.9** (ub512). Old empty-ctx
  3258/134 not comparable. Table: `benchmarks/vulkan-tuning-sweep.md`.
- Quality (earlier): HumanEval 9/10 + tools 3/3 @ 16k; ppl Q5 5.87 vs IQ3 6.20.
- Guardian: 45s stop→start cooldown, 60 min idle. Loopback :8080 still Hermes SMS.
- Repo: main ahead of origin; push when ready.

## 2026-08-06 TDR investigation — READ FIRST (updated ~18:25)

### Root cause (confirmed)

**Bugcheck `VIDEO_TDR_FAILURE (0x00000116)` with faulting driver `amdkmdag.sys`
(AMD WDDM kernel).** WER event 1019 names amdkmdag on every crash. Live
kernel WATCHDOG dumps under `C:\Windows\LiveKernelReports\WATCHDOG\` line
up with the BSODs. Latest dump `WATCHDOG-20260806-1809.dmp` contains the
string `llama-server`.

Mechanism: the AMD driver stalls past Windows TDR recovery; when recovery
fails the box hard-crashes (0x116). Default `TdrDelay=2s` / `TdrDdiDelay=5s`
is far too aggressive for 25GB VRAM map/unmap and long Vulkan compute on
this RDNA4 card.

### Crash timeline (all four 0x116 today)

| # | WATCHDOG / BSOD local | Trigger context |
|---|----------------------|-----------------|
| 1 | 08:33 / reboot 08:34 | Second llama-server during VRAM churn |
| 2 | 09:27 / reboot 09:28 | Idle after large unload (no llama process) |
| 3 | 11:52 / reboot 11:54 | ~29 min after clean idle stop at 11:23 — MMAgent fix was **not** sufficient |
| 4 | 18:09 / reboot 18:10 | Mid-inference (~116–120 t/s, length-retry storm); dump has `llama-server` |

Boot now: **2026-08-06 18:10:40**. Minidumps: `080626-15046/15281/15125/17343-01.dmp`.

### Fixes applied (full stack 2026-08-06 ~18:30)

1. **`Disable-MMAgent -mc`** (MemoryCompression = False) — keep it.
2. **TDR registry** (needs reboot to arm the timeouts):
   - `TdrDelay=60`, `TdrDdiDelay=60`, `TdrLimitTime=120`, `TdrLimitCount=10`
   - Backup: `%TEMP%\tdr-reg-backup-20260806-182107.txt`
3. **Phantom NVIDIA removed:**
   - Device `PCI\VEN_10DE&DEV_2805...` removed via `pnputil /remove-device`
   - Display package `oem52.inf` (`nv_dispsi.inf` 32.0.16.1062) deleted
   - NVIDIA Vulkan ICD unregistered (AMD ICD only)
   - `NvContainerLocalSystem`, `nvagent`, `NVDisplay.ContainerLocalSystem` → Disabled
   - CUDA `llama-server.exe` binary left in repo for rollback path (no NVIDIA GPU)
4. **Adrenalin residuals purged:** oem66–69 at **32.0.31035.1003**
   (amdocl/amdogl/amdvlk/amdwin) deleted from driver store.
5. **SI package installed:** official
   `260309a-200492c-aib.zip` (SI Driver for R9700). **Important:** its
   `u0200492.inf` DriverVer is **32.0.22042.14002** — same kernel bits as the
   WU driver already in use. SI Setup exit 0. No newer SI kernel than WU for
   this SKU. Do **not** install Adrenalin 26.7.1 (idle DPM bug).

### Still present risk factors (if TDR recurs after reboot)

- Same `amdkmdag` version as before the SI package (by design — AMD ships it).
  Stability bets are TDR timeouts + multi-GPU cleanup, not a newer binary.
- Intel UHD still present (fine). Display via RDP + AMD/Intel.
- If still 0x116: keep model loaded (disable idle unload), WinDbg minidumps,
  power-limit / ASPM experiments.

### Verification protocol (after reboot)

1. Confirm registry still 60/60/120/10.
2. Confirm MemoryCompression still False.
3. Note newest WATCHDOG name before any llama start.
4. `gsudo` pm2 start `qwen3-llama-vulkan` only (one instance). Wait for healthy 8081.
5. One short completion via guardian/Tailscale IP.
6. Controlled unload (`pm2 stop qwen3-llama-vulkan`), wait ≥60s, reload.
7. Pass = zero new files in `LiveKernelReports\WATCHDOG\` and no event 1001/41.
8. Only then re-enable long-running prod / sweeps.

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

## Task 2 — DONE (Vulkan tuning sweep Q5_K_M, 2026-08-06 ~19:00)

Applied to `qwen3-llama-vulkan`: ub512, b2048, flash-attn on, f16 KV.
Larger ubatch/batch slower; fa off tanks; q4/q8 KV not worth prefill hit.
`tune-vulkan.ps1` hardened: refuse if prod up, AMD ICD pin, 60s cool between
loads. `--parallel` >1 still open (guardian generation_lock assumes 1 slot).

## Task 3 — port 8080 overlap (guardian vs Hermes SMS)

Unchanged from last handoff. Hermes `qwen-worker` SMS adapter binds
127.0.0.1:8080; guardian binds 0.0.0.0:8080; Windows routes loopback to
Hermes. Works today only because MCC uses the Tailscale IP. Fix: move the
Hermes SMS adapter port (config under
`C:\Users\carte\AppData\Local\hermes\profiles\qwen-worker\`). Coordinate
with Carter first — that gateway is live (sms/buzz/api_server connected).

## Task 4 — NVIDIA cleanup + 4060 Ti plan (mostly done / backburner)

- Phantom NVIDIA device + oem52 display package + Vulkan ICD removed (2026-08-06).
  NvContainer services Disabled. **Keep** CUDA `llama-server.exe` for rollback.
- 4060 Ti rack plan: still ask Carter first; do not act.

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

- Push main to origin when ready.
- AIWA `aiwa-prod-102` (.14): Orca runtime port 6768 down (ping OK, SSH up).
  Investigate via Orca only, per AIWA runbook.
