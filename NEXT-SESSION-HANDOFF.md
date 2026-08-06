# Next Session Handoff — post-R9700-cutover tasks

Written 2026-08-06 (~01:50), immediately after the R9700 Vulkan production
cutover. Prior session's full findings are in `R9700-SWAP-HANDOFF.md` (read its
OUTCOME block first) — this file is only the open work.

## Current state (verified)

- Production: `qwen3-llama-vulkan` (PM2 id varies, port 8081 loopback) +
  `llama-guardian` (0.0.0.0:8080 proxy), both online, `pm2 save` run.
  12 PM2 apps total, all online. `qwen3-llama` (CUDA) is **deleted** from PM2;
  rollback is `pm2 start ecosystem.config.cjs --only qwen3-llama`.
- GPU: R9700 on **Windows Update base driver 32.0.22042.14002** (`oem56.inf`).
  Adrenalin has an idle-VRAM-eviction bug — **do not install AMD driver
  updates** (details + recovery command in R9700-SWAP-HANDOFF.md).
- GPU pinning is `VK_LOADER_DRIVERS_SELECT: "*amd*"` in `ecosystem.config.cjs`.
  Do NOT use `GGML_VK_VISIBLE_DEVICES` — enumeration order is unstable across
  process contexts (bit us: PM2 child silently ran on the Intel iGPU at 2.6 t/s).
- Measured under PM2: tg ~132–138 t/s on IQ3_XXS, healthy after idle windows.
- P2P link PC↔AIWA fixed: "Ethernet 2" (Realtek 2.5GbE) static
  `10.110.10.2/30`, AIWA at `10.110.10.1`, <1ms. LAN untouched
  (I225-V, 192.168.1.10/24).
- Repo HEAD: `948518e` on main, pushed, tree clean.

## Environment gotchas (will bite you immediately)

- PM2 needs elevation this environment: `gsudo -d "pm2 <cmd>"`. pm2_home is
  `C:\ProgramData\pm2` (the user-profile `.pm2` is stale).
- PM2 stop/delete on the .exe app can **orphan the llama-server.exe child**
  (it kept 8081 twice). After any stop: verify with
  `Get-Process llama-server`; `taskkill /PID <pid> /F` (elevated) strays before
  restarting, or you get two servers on 8081 and garbage behavior.
- PowerShell tool is EPERM-blocked; use Bash + `powershell.exe -NoProfile` or
  gsudo. Inline quoting through gsudo mangles — write .ps1 to a scratch dir and
  run by path.
- Guardian wouldn't restart from stale PM2 state ("stopped", no log). Fix:
  `pm2 delete llama-guardian` then re-add from ecosystem config.
- Loopback 127.0.0.1:8080 is answered by the **Hermes qwen-worker SMS
  adapter**, not the guardian (see Task 3). Test the guardian via the
  Tailscale IP (100.124.216.11:8080), which is what MCC uses.

## Task 1 — Q4_K_M requant swap (the payoff)

The 16GB VRAM ceiling is gone (R9700 = 32GB). Production still runs the
aggressive `IQ3_XXS` quant chosen for the 4060 Ti.

1. `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` (22.7GB) is already in `models/`
   (`Q5_K_M` 27.1GB is there too if headroom allows after KV).
2. Edit `--model` and `--alias` in the `qwen3-llama-vulkan` block of
   `ecosystem.config.cjs`. Check ctx-size VRAM math: 22.7GB weights + 64k f16
   KV + compute buffers must fit ~31.7GB usable.
3. `pm2 delete qwen3-llama-vulkan` + re-add from ecosystem (env/args changes
   need delete+add, not restart). Kill orphans (see gotchas).
4. Verify: warm request, then a **post-60s-idle request** (the driver-bug
   signature was idle-then-dead; WU driver is clean but re-verify on the new
   model), then quality spot-check via guardian.
5. Alias note: guardian queue uses `GUARDIAN_QUEUE_MODEL` default
   `qwen3.6-35b`; MCC calls the alias too. Keep the alias stable or update
   consumers.

## Task 2 — Vulkan tuning sweep

The ubatch/flash-attn/KV comments in `ecosystem.config.cjs` were tuned for
CUDA on 16GB. Re-run on Vulkan/R9700 with the new quant:

- `tune-vulkan.ps1` and `bench-models.ps1` in this repo; baselines in
  `BENCH-RESULTS.md`. Vulkan bench reference (IQ3_XXS, WU driver):
  pp512 3094.8 / tg128 140.4.
- Stop guardian + server first so nothing holds VRAM or answers 8080.
- Candidates: ubatch 512/1024/2048, batch 2048, fa auto vs on, maybe
  `--parallel` >1 now that VRAM allows (guardian assumes 1 slot — check
  `llama-guardian.py` slot logic before raising).

## Task 3 — port 8080 overlap (guardian vs Hermes SMS)

Hermes `qwen-worker` gateway's SMS platform adapter listens on
`127.0.0.1:8080` (its api_server is 8652). Guardian binds `0.0.0.0:8080`;
Windows routes loopback traffic to the more-specific Hermes socket, so
anything on this PC hitting `127.0.0.1:8080` gets Hermes, not the guardian.
Works today only because MCC uses the Tailscale IP.

Fix direction: move the Hermes SMS adapter port (config under
`C:\Users\carte\AppData\Local\hermes\profiles\qwen-worker\`) — the guardian's
8080 is the published contract. Also note its log warns
`SMS_INSECURE_NO_SIGNATURE=true`. Coordinate with Carter before touching
Hermes; it's live and connected (sms/buzz/api_server all "connected").

## Task 4 — NVIDIA cleanup + 4060 Ti plan (backburner, ask Carter first)

- 4060 Ti is out of the machine (shows CM_PROB_PHANTOM). `nvcontainer.exe`
  is crash-looping (NvBackend64.dll 0xc0000409) — NVIDIA stack installed with
  no NVIDIA card present. Cleanup candidate: remove NVIDIA App/driver stack.
  **Do not** delete `llama-cpp-server\llama-server.exe` (CUDA build) — it's
  the rollback path until the Vulkan stack has more runtime.
- Carter is debating a rack server for the 4060 Ti — plan, don't act.

## Also open (not this repo)

- AIWA `aiwa-prod-102` (.14): Orca runtime port 6768 down (ping OK, SSH up).
  Investigate via Orca only, per AIWA runbook.
