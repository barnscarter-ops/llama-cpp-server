# NEXT-SESSION-HANDOFF.md — 2026-08-19 (GLM-4.7-Flash cutover + PC service restore)

## PC service restore — 2026-08-19 (post X870E rebuild)

All PC-side services restored from the old Z690 drive (D:) and old dump.pm2:

- **pm2 (SYSTEM, PM2_HOME=C:\ProgramData\pm2)** — 9 apps online + saved:
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

## Known follow-ups

- **AIWA triage + anything referencing CartersPC by Tailscale IP still points
  at 100.124.216.11 (old box). This box is 100.124.41.115.** Re-point at the
  AIWA cutover (Night 4+), NOT before — old AIWA is live production.
- pc-bridge plugin (native Hermes plugin from supervisor repo) not yet
  re-verified against the restored profiles — supervisor repo is checked out
  at C:\Workspace\Shared\Agents\Hermes-Supervisor with local mods (memory/
  HANDOFF.md dirty).
- gsudo NOT reinstalled on this box (old HANDOFF ops commands reference it);
  elevated ops currently use scratch .ps1 + Start-Process -Verb runAs.
- Mav-Room stack: healthy on :8920/:8642/:8921, tailscale serve :18920 live,
  `Mav-Room Stack` + `Mav-Room Desktop Presence` tasks registered.

## Current production state


- **Model: GLM-4.7-Flash UD-IQ3_XXS (12.9 GB)** — winner of the 2026-08-19
  3-worker debate + full benchmark suite. See
  `benchmarks/4060ti-finalist-report-2026-08-19.md` for the complete data.
- **Config**: ctx 202752 (full native), KV f16, ubatch 1024 / batch 2048,
  `--repeat-penalty 1.0 --min-p 0.01` (MANDATORY — loops without),
  `--reasoning off`, alias `local-llm`, port 8081 via guardian on 8080.
- **VRAM at 202k**: 15,856-15,900 MiB / 16,380 with display on iGPU and
  desktop overhead ~600 MiB. MLA KV: VRAM is flat from 65k→202k.
- Verified live 2026-08-19: health OK on 8081 + 8080, completion test passed.

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
