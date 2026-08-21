# NEXT-SESSION-HANDOFF.md — 2026-08-19 (GLM-4.7-Flash cutover + PC service restore)

## PC service restore — 2026-08-19 (post X870E rebuild)

All PC-side services restored from the old Z690 drive (D:) and old dump.pm2:

- **pm2 (Carter, PM2_HOME=C:\ProgramData\pm2)** — 9 apps online + saved:
  llama-guardian, local-llm, pc-actions-daemon (:8901, v0.3.0, 33 actions,
  token from ecosystem.local.config.cjs), hermes-sandbox-reaper (ARMED),
  hermes-deadman-sink (:8903), downloads-watcher (DownloadsOrganizer copied
  from D:\Users\carte), homelab-agent-sensors (:7331), prometheus-sync,
  maverick-dashboard (:8792).
- **Boot**: new scheduled task `PM2 Resurrect` (SYSTEM, AtStartup, runs
  `node pm2 resurrect` via hermes-bundled node). Old box had no such task in
  the 39 exported XMLs — this is new, deliberate.
- **windows_exporter**: copied from D:\Program Files, installed as a Windows
  service, RUNNING on :9182 (triage monitors this).
- **Hermes profiles**: all 13 restored from D:\Users\carte\AppData\Local\hermes
  \profiles (claude, council, debugger, executor, grok, mav-room, omp, pi,
  qwen-worker, researcher, reviewer, scripter, worker). active_profile=omp.
  Top-level config.yaml model.default restored to glm-5.3/custom:zai-coding
  (backup: config.yaml.bak-pre-glm-restore-20260819).
- **qwen-worker profile**: context_length 65536 → 202752; its `qwen3-llama`
  alias still works (guardian maps legacy aliases → local-llm; verified live
  through :8080 completion).
- **Scheduled tasks**: 24/39 Z690 XMLs already re-registered; remaining 15
  are old-hardware OEM junk (ASUS/SANDISK/StartCN/DVR) — correctly skipped.
- **Python for daemons**: Python312 (old dump path) re-provisioned with
  fastapi/uvicorn/psutil/httpx/watchdog — pc-actions-daemon, reaper, sink all
  run under it via ecosystem `script: python`.

## Night 4 (AIWA cutover) — SCHEDULED Sat 2026-08-22 evening

Full runbook: `aiwa-transplant/NIGHT4-PLAN.md` (written 2026-08-19).
**PREP IS COMPLETE as of 2026-08-20** — nothing left before Saturday:

- Soak: PoC clean (up since 08-19, SMART PASSED).
- Night-2 artifacts verified: all 5 files in `C:\aiwa-backups\20260817\`
  sha256 OK (2 stale lines in the sums file are harmless).
- Staging LV `samsung-stage` (300G thin) mounted at `/mnt/samsung-stage`;
  `mav-transfer/` **47 G copied + verified**, `mav-rag/` 499 MB incl.
  `qdrant-data/` (found orphaned-on-840-PRO; would have been lost).
- llama b10488 ubuntu-vulkan + Nemotron 3.5 Lightning 30B-A3B Q4_K_M
  (sha-verified) staged at `/mnt/stage/llama/`; **bench PROOF PASSED on the
  PoC's R9700: tg128 141.4 / pp512 2176** (prod baseline 140–152) — Gate 4 is
  a smoke test only.
- Z690 NIC `.link` files pre-written, staged at `/root/night4-staging/`.
- systemd unit drafted: `aiwa-transplant/night4/llama-server.service`.

Open on the night (all Carter): Samba mavshare password (Gate 3), SN770
confirm-leave (ProDesk = intact rollback), CT 200 destroy-or-keep call.
AIWA triage PC_HOST re-point (100.124.216.11 → this box) happens at Gate 3.
Timeline: Gate 0 18:30 backups w/ services up → downtime 19:50 → verified
by ~23:00. Rollback at any gate = power off Z690, power on ProDesk.

## Known follow-ups

- **AIWA triage + anything referencing CartersPC by Tailscale IP still points
  at 100.124.216.11 (old box). This box is 100.124.41.115.** Re-point at the
  AIWA cutover (Night 4 Gate 3), NOT before — old AIWA is live production.
- pc-bridge plugin (native Hermes plugin from supervisor repo) not yet
  re-verified against the restored profiles — supervisor repo is checked out
  at C:\Workspace\Shared\Agents\Hermes-Supervisor with local mods (memory/
  HANDOFF.md dirty).
- gsudo v2.6.1 is installed (`C:\Program Files\gsudo\Current`). Prefer
  forward slashes in `gsudo ... -File D:/path` — backslashes get stripped
  and the command exits 127.
- Mav-Room stack: healthy on :8920/:8642/:8921, tailscale serve :18920 live,
  `Mav-Room Stack` + `Mav-Room Desktop Presence` tasks registered.
- **presence-actions** (`D:\Workspace\Infrastructure\presence-actions\`):
  generalized home/away dispatcher. Task `Presence Watcher` (pwsh 7, ONLOGON).
  First action `swap-display` (SMS once on home) reminds Carter to move the
  display to iGPU then restore llama ctx 202752. SMS path proven 2026-08-21.
  Do not host this in pm2. Skill: `home-presence-actions`.

## Current production state


- **Model: GLM-4.7-Flash UD-IQ3_XXS (12.9 GB)** — winner of the 2026-08-19
  3-worker debate + full benchmark suite. See
  `benchmarks/4060ti-finalist-report-2026-08-19.md` for the complete data.
- **Config**: KV f16, ubatch 1024 / batch 2048, `--repeat-penalty 1.0 --min-p 0.01`
  (MANDATORY — loops without), `--reasoning off`, alias `local-llm`, port 8081
  via guardian on 8080. **ctx is 49152 (NOT 202752)** as of 2026-08-20 — see
  note below; restore to 202752 after the display swap.
- **Display is on the 4060 Ti** (~509 MiB desktop overhead), which is why ctx
  was cut 202752 → 49152 (the 202k slab no longer fits; driver sysmem-spills
  and tg cratered ~90 → ~24-35). **TODO (Carter, in progress): move display
  back to the iGPU, then restore `--ctx-size 202752` in ecosystem.config.cjs**
  (rollback note is in that file's local-llm args block).
- **Guardian GPU-probe false-positive FIXED 2026-08-21 (commit 178f422).**
  The probe threshold (40 t/s) was calibrated for the R9700/Vulkan and was
  false-flagging a healthy 4060 Ti + GLM as "CPU fallback", so guardian
  `pm2 stop`-ped llama on every cold start (agents saw "starts then stops").
  Fix: threshold 40 → 20, probe `max_tokens` 24 → 128 with a longer prompt so
  it measures steady-state (~84 t/s) instead of 2-token overhead. Verified:
  cold start now probes OK (83.8 t/s) and leaves llama up.
- Verified live 2026-08-21: cold start → probe OK (83.8 t/s) → completion
  returned "pong" through :8080. Guardian pid restarted to pick up the fix.

## What happened this session (2026-08-19)

1. 3-worker blind debate (delegate_task variant of multi-agent-debate skill):
   GLM-4.7-Flash / Cohere North-Mini-Code / gemma-4-26B-A4B finalists,
   Qwen3.6-35B IQ3_XXS as incumbent control. Run dir:
   `C:\Workspace\Shared\Agents\debate-4060ti-execution-worker-2026-08-19\`
2. Benchmarks (all 100% GPU, CUDA b10488):
   - GLM tg 90.1 / pp 2787 | Qwen tg 82.9 / pp 2826 | Cohere tg 95.4 but
     247s workflow wall (unsuppressible interleaved thinking = verbose) |
     gemma 68.7 tg, 7/10 HumanEval (verbosity failures)
   - Quality: GLM + Qwen 10/10 HumanEval, 3/3 tools; GLM workflow wall 17.0s
3. Infra repairs during the run:
   - **b10488 build was broken** — `llama-common.dll` missing since the
     Aug-18 refresh (prod llama-server would have crash-looped). Repaired
     from official b10488 release zip.
   - **Defender false-positive** on llama.cpp DLLs (Wacatac.H!ml):
     added exclusion `D:\Workspace\Infrastructure` (elevated). If DLLs go
     missing again after build refreshes, check Defender history first.
   - Display moved to AMD iGPU + per-app GPU prefs → ~600 MiB desktop
     overhead (was ~1100).
   - **pm2 global CLI was wiped** by an npm update (orphan daemon remained).
     Reinstalled via `npm install -g pm2`. CLI requires
     `PM2_HOME=C:\ProgramData\pm2` (elevated for control ops). Cutover
     script: `C:\Users\carte\scratch\pm2-glm-cutover.ps1`.
4. Models on disk: GLM (prod), Qwen3.6-35B IQ3_XXS (rollback only),
   Nemotron Q4_K_M + Qwen3.6 Q4_K_M + Qwen3.8-27B (AIWA earmarked / predate
   debate). Cohere + gemma deleted.
5. Rebooted all → 202k ctx verified at 524 MiB free.

## Gotchas for next session

- **KV quant on GLM**: q8_0 KV fails context creation (b10488 deepseek2 arch
  bug). f16 KV only. f16 also measured faster than q8_0 on this GPU for all
  bench models — do not "optimize" to q8_0.
- **Never mix --cache-type-k/--cache-type-v quant types** (e.g. f16+q8_0):
  hits a catastrophic slow path (~15x pp loss, 25-35% tg loss).
- **GLM sampler**: without `--repeat-penalty 1.0 --min-p 0.01` GLM loops.
  With it: 10/10 HumanEval. Do not remove.
- llama-bench in b10488 does not accept `-c` (auto-sizes ctx).
- pm2: args changes require `pm2 delete` + re-add (not restart). Check for
  orphaned llama-server.exe after delete/stop.
- npm updates can silently wipe the global pm2 CLI — daemon keeps running
  from a ghost path. Symptom: `pm2: command not found` but processes fine.

## Open items

- gemma-4-12B Q6_K + vision + MTP (Worker C's dark horse, unbenched).
- Granite 4.0-h-small (Worker C candidate, unbenched).
- "Qwen3.6-14B/12B" GGUFs on HF are heretic merges — do not use.
