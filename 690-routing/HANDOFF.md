# Handoff — 690 Qwen consult / Nemotron clerk split (**DONE** 2026-08-22)

**Repo:** `D:\Workspace\Infrastructure\llama-cpp-server` `main` (origin in sync through `9220864`)
**Operator card:** `690-routing/README.md`
**Doctrine:** `C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`

## Closed

Sessions 1–4 of `690-routing/PLAN.md` ran live on CT 210. Walk-away:

- `:8080` = **Nemotron clerk** (`nemotron-3.5-lightning-30b-a3b`, 131k, systemd, `--reasoning off`)
- Qwen consult: `/opt/llama/swap-qwen-consult.sh` (thinking ON, 262k). Back: `/opt/llama/swap-nemo-clerk.sh`
- Same bytes in this git tree. pi: Nemotron `reasoning: false`, Qwen `true`
- Smokes: `690-routing/smokes/clerk.json` (Paris, no think dump); `consult.json` (~44 t/s, stop)

Do not re-diagnose MacBridge. Do not re-run the split. `pkill -f llama-server` still self-kills — use `[l]lama-server` after `systemctl stop`.

## Next (not this repo)

Carter took over **Local LLM Board** Research+Plan: Orca tab `LocalLLM-Board — orchestrator` (`term_92f96e6d`), repo `C:\Workspace\Active\WindowsApps\LocalLlmBoard`. Pipeline: parallel-build-handoff, **stop after PLAN.md**. Brief: `ORCHESTRATOR-BRIEF.md`.

Night 4 AIWA cutover still later today (~15h+ from this close). CT 210 scripts can die in cutover — git copy here is SoT.
