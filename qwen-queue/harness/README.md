# Harness adapters for guardian-managed local workers

- `check_configs.py` — parse live Grok TOML / Hermes YAML (no secret dump).
- `check_mcp_stdio.py` — initialize, `tools/list`, reject raw `model`.
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
