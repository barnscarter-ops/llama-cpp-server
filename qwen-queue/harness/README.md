# Harness adapters for guardian-managed local workers

- `ROLLOUT.md` — Sessions 0–6 capstone: parent matrix, Codex/Claude MCP
  fallback (native subagents stay cloud), `:8081` / `.240` inventory,
  propose-only enforcement. Fragments are not live-installed. Adapter:
  `qwen-queue/local-worker-mcp.py`.
- `check_configs.py` — parse live Grok TOML / Hermes YAML (no secret dump).
- `check_mcp_stdio.py` — initialize, `tools/list`, reject raw `model`
  (`grok`, `hermes`, `codex`, `claude`).
- `check_rollout.py` — fragment paths + live guardian policy (workers off,
  `:8081` down). Read-only; no PM2/firewall.
- `live_session34.py` — watched MCP jobs + admission probes (flag must be on).
- `CALIBRATION.md` — Session 4 corpus and preference evidence.
- `deepseek-guardian-provider.mjs` — DeepSeek Harness `SubagentProvider`.
  Fixed `work_class=tool_execution`. Register with Cordis:

  ```js
  import { apply } from './deepseek-guardian-provider.mjs'
  apply(ctx, { name: 'guardian' })
  ```

  Or `ctx.subagents.registerProvider(new GuardianSubagentProvider())`.
  Pi native mapping is **not** shipped (Pi orchestrators paused 2026-08-28).
  Codex/Claude have no verified native provider; use MCP (`ROLLOUT.md`).
