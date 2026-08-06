# Session transcript — 2026-08-06 Vulkan tuning sweep (Task 2)

Agent: Hermes (claude-fable-5). Saved mid-session at Carter's request, next to
NEXT-SESSION-HANDOFF.md. This is the working record of what was said and done,
in order, with verified outcomes.

## Turn 1 — Carter: "read the handoff and tell me what your first task is"

- Read NEXT-SESSION-HANDOFF.md. Task 1 (Q5_K_M requant) done; first open task
  is Task 2 — Vulkan tuning sweep against Q5_K_M on the R9700.
- Also flagged: main was 4 commits ahead of origin, unpushed.

## Turn 2 — Carter: "push main and then start the sweep! ... make this local
## model the smartest rocket it can on this brand card."

Actions taken, all verified by tool output:

1. Ran brain vault session-brief.ps1 (L0/L1 loaded; no mapped project brief).
2. **Pushed main**: 91c2082..ce63aac -> origin/main. `git status -sb` confirms
   `## main...origin/main` level.
3. Read tune-vulkan.ps1, benchmarks/tune-sweep.py, ecosystem.config.cjs,
   grepped qwen-queue/llama-guardian.py (generation_lock is a strict one-slot
   asyncio.Lock — raising --parallel >1 needs guardian changes; parked).
4. Preflight state: llama-server.exe pid 11784 serving on 8081 (28.7 GB WS),
   guardian on 0.0.0.0:8080, slot idle (is_processing:false). Confirmed
   loopback:8080 is the Hermes SMS adapter, guardian only reachable via
   Tailscale 100.124.216.11:8080 (its /guardian/status 404s — path differs,
   noted but not chased).
5. **Hardened tune-vulkan.ps1** (3 patches):
   - New `-CooldownS 60` param + enforced 60s sleep between every config row
     (hard rule: R9700 VRAM churn TDR'd amdkmdag.sys 0x116 on 2026-08-06).
   - Sets `VK_LOADER_DRIVERS_SELECT=*amd*` (ICD pin, never device index —
     iGPU enumeration-order incident 2026-08-06).
   - Preflight guard: refuses to run if guardian (probed via
     100.124.216.11:8080), llama-server (127.0.0.1:8081), or any
     llama-server.exe process is alive.
   - Config matrix now sweeps batch too: ub 512/1024/2048 x b 2048/4096,
     fa on/off, KV f16/q8_0/q4_0 — 9 rows full, baseline-to-beat is prod
     ub1024/b2048/fa-on/f16 (pp512 3258.5 / tg128 134.0).
6. Wrote C:\Users\carte\scratch\pm2-stop-for-sweep.ps1 (PM2_HOME=
   C:\ProgramData\pm2; stops llama-guardian, qwen3-llama-vulkan, qwen3-llama;
   kills orphan llama-server if any).
7. **Ran it via gsudo** (approved). Result: stops issued (only error:
   "qwen3-llama not found" — CUDA twin not registered, harmless). Post-check
   from user shell: NO llama-server.exe process, NO listener on 8080/8081.
   Prod is DOWN, GPU idle — cleared for the sweep.
   Note: the `*>` log redirect inside the gsudo -d command didn't produce
   pm2-stop.log (elevated ctx redirect quirk); port/process checks were the
   real verification.

## State at save time

- Prod OFFLINE (intentional, for sweep). Restore path: gsudo pm2 start
  llama-guardian + qwen3-llama-vulkan, then pm2 save; verify via Tailscale
  8080 and a chat completion.
- Sweep NOT yet started. Next command (background, ~9 loads x (load+3 reps+60s)):
  pwsh -NoProfile -File C:/Workspace/Infrastructure/llama-cpp-server/tune-vulkan.ps1
    -Model models/Qwen3.6-35B-A3B-UD-Q5_K_M.gguf -Ctx 32768
  Results append to benchmarks/vulkan-tuning-sweep.md.
- After sweep: apply winner to ecosystem.config.cjs (qwen3-llama-vulkan
  block), restart prod, pm2 save, verify, commit (tune-vulkan.ps1 changes +
  results + config), update NEXT-SESSION-HANDOFF.md.

## Todo list at save time

1. [done] Push main to origin
2. [done] Harden tune-vulkan.ps1 (cooldown, ICD pin, preflight, batch rows)
3. [done] Stop guardian + server via gsudo PM2; verified ports free, no orphans
4. [next] Run sweep (background, notify on complete)
5. [ ] Apply winner to ecosystem.config.cjs, restart prod, pm2 save, verify
6. [ ] Commit results + config, update handoff
