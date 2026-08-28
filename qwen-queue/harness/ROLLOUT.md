# Managed local worker rollout (PLAN Sessions 0–6)

**Published:** 2026-08-28 (Session 6)
**Status:** source contract complete; live dispatch remains **off**
(`LOCAL_WORKER_ENABLED` absent from PM2 dump; `GET /__guardian/workers`
`enabled=false`). Watched smoke passed then rolled back. **Do not enable
the flag, mutate PM2, spawn Pi, or change firewall/network in this
workstream.**

Parent runtimes ask for a **work class**. Guardian owns admission, seat,
and the fixed runner. Callers never supply a model, profile, endpoint,
or executable.

| Work class | Guardian route | Rule |
|---|---|---|
| `mechanical_execution` | AIWA clerk / Nemotron | Bounded; no auto-swap |
| `tool_execution` | Workbench Pi executor | Write-capable; Workbench lifecycle stays guardian-owned |
| `planning` / `deep_analysis` | AIWA consult | Explicit `gate=operator` or `gate=frontier` only; never auto-swap |

Preference evidence: `CALIBRATION.md` and `LOCAL-WORKER-MANAGER.md`.
Prefer local only for bounded standard `tool_execution` (later
`mechanical_execution`). Frontier, unknown APIs, and architecture stay
cloud.

## Parent harness matrix

| Parent | Path | Live on this machine (2026-08-28) | Native subagents |
|---|---|---|---|
| Grok | MCP `--source grok` | Yes (`~/.grok/config.toml`) | n/a — use MCP tools |
| Hermes | MCP `--source hermes` | Yes (`%LOCALAPPDATA%\hermes\config.yaml`) | n/a — use MCP tools |
| DeepSeek Harness | Native `SubagentProvider` | In-repo adapter; not a live DSH register | Routed through guardian when registered (`deepseek-guardian-provider.mjs`) |
| Pi (orchestrator) | Native skipped | Paused 2026-08-28 | Do not spawn Pi. Guardian still *codes* a Pi runner for admitted jobs |
| Codex | MCP `--source codex` | **Not installed.** Fragment below only | Remain **cloud** |
| Claude Code | MCP `--source claude` | **Not installed.** Fragment below only | Remain **cloud** |

Adapter: `D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py`
Python: `C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe`
(the 3.12 install — not the Store stub). Tools: `local_worker_capabilities`,
`local_worker_submit`, `local_worker_status`, `local_worker_cancel`.
`--source` is launch-fixed (`grok`, `hermes`, `pi`, `deepseek-harness`,
`codex`, `claude`).

Parent contract (every MCP parent): executor classes for bounded
implementation; planning/deep-analysis only with an explicit gate; poll
or cancel by `job_id`; accepted ≠ done; escalate visibly on
`requires_cloud`, `consult_gate_required`, `consult_unavailable`, or
`local_workers_disabled`. Never pass model / profile / endpoint / runner.

Installing the Codex or Claude fragment is a **later watched harness-config
change**, not part of this session. Same rule as Session 2: source-owned
examples first; live files only when Carter asks.

## Codex and Claude — MCP fallback

### Why MCP, not a native provider

Session 5 verified one native seam: DeepSeek Harness
`packages/subagent/` `SubagentProvider` (`name`, `capabilities`,
`inheritsParentContext`, `start(request)`). That adapter is in this
directory and cannot select a raw local model.

No equivalent **supported** provider override is verified for Codex or
Claude Code:

- Codex live `~/.codex/config.toml`: default model `gpt-5.6-terra`;
  `mcp_servers` are `node_repl` and `gws` only; **no**
  `model_providers` block pointing at guardian, `:8081`, or `.240`.
  Codex child/subagent runs therefore stay on that cloud model.
- Claude Code live `~/.claude/settings.json` model `claude-fable-5`;
  user MCP in `~/.claude.json` is `gws` only. Agent/Task subagents stay
  on that cloud model. Claude Desktop config is **not** present.
- Pointing either runtime at `http://127.0.0.1:8080/v1` or `:8081` via a
  custom OpenAI-compatible provider would be a **completions bypass**,
  not the work-class contract. That override is **not** verified and
  must not be used as a local-worker path.

Until a DeepSeek-style provider is read from current Codex/Claude
interfaces and tested the same way, **native subagents remain cloud**.
Local worker work from those parents goes through this MCP adapter.

### Codex fragment (not live)

Same shape as the installed Grok server and the live Codex `gws` server
(`command` + `args`). Append to `%USERPROFILE%\.codex\config.toml` only
when Carter watches the change. Do not replace `model` or existing MCP
rows.

```toml
[mcp_servers.guardian_local_worker]
command = 'C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe'
args = ['D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py', '--source', 'codex']
```

### Claude Code fragment (not live)

Same shape as the installed Claude `gws` server. Append under
`mcpServers` in `%USERPROFILE%\.claude.json` only when Carter watches
the change. `~/.claude/settings.json` is not the MCP catalog on this
machine.

```json
{
  "mcpServers": {
    "guardian_local_worker": {
      "command": "C:\\Users\\carte\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
      "args": [
        "D:\\Workspace\\Infrastructure\\llama-cpp-server\\qwen-queue\\local-worker-mcp.py",
        "--source",
        "claude"
      ]
    }
  }
}
```

Do not merge this object blindly — `~/.claude.json` already has other
`mcpServers`. Add the one key. Main model stays cloud.

## Direct-access inventory (Workbench `:8081` and AIWA `.240`)

Live check 2026-08-28 this session (read-only): guardian `status=ok` on
`:8080`, `llama_up=false`, no `LISTEN` on `:8081`, no `llama-server.exe`,
AIWA seat `occupant=clerk` `reachable=true`
`endpoint=http://192.168.1.240:8080`, workers `enabled=false`.
`LOCAL_WORKER_ENABLED` is absent from `C:\ProgramData\pm2\dump.pm2`.

### Intended front door

| URL | Role | Bind |
|---|---|---|
| `http://127.0.0.1:8080` | llama-guardian OpenAI + `/__guardian/*` | `0.0.0.0:8080` (PM2 `llama-guardian`) |
| `http://127.0.0.1:8081` | Workbench llama-server (Qwen3.6 when up) | `127.0.0.1` only (`ecosystem.config.cjs` `--host 127.0.0.1 --port 8081`) |
| `http://192.168.1.240:8080` | AIWA CT 210 llama-server (clerk/consult) | `0.0.0.0:8080`, no API key (`690-routing/llama-server.service`) |

Clients must POST completions and worker jobs to guardian `:8080`.
`:8081` is guardian's upstream. `.240` is guardian's `AIWA_BASE`.

### Workbench `:8081` paths

| Path | Kind | Hits `:8081`? | Notes |
|---|---|---|---|
| `llama-guardian.py` proxy / idle / health | owner | Yes (loopback GET/POST as upstream) | `LLAMA_HOST=127.0.0.1` `LLAMA_PORT=8081`. Catalog/seats must not live-GET 8081; health may. |
| Slot-activity poller | owner | Yes (`/slots`) | Exists **because** direct `:8081` clients skip the proxy and would be idle-reaped |
| Single-llama enforcer | owner | Process/port | PM2 `local-llm` is source of truth; orphans holding `:8081` are killed |
| Pi `llamacpp` / `llamacpp-690` | client | **No** | `baseUrl` `http://127.0.0.1:8080/v1` in `~/.pi/agent/models.json` |
| Hermes `custom_providers` `qwen-llamacpp` | client | **No** | `base_url` `http://127.0.0.1:8080/v1` (legacy alias `qwen3-llama`) |
| Grok / Codex / Claude configs | client | **No** | No `:8081` / `.240` URLs in those live files |
| In-repo benches (`benchmarks/bench-coding.py` default, sweep JSON) | leftover | Would if run | Historical Vulkan/CUDA benches; not standing clients |
| `docs/ornith-vs-qwen3-2026-07-06.md` | leftover doc | Instructs `:8081` | Pre-guardian bench notes |

When `local-llm` is stopped (current live), `:8081` is closed. Direct
POST is possible only from **this box** while the model is up — LAN
cannot reach it. That is already a bind-level control.

### AIWA `.240` paths

| Path | Kind | Hits `.240`? | Notes |
|---|---|---|---|
| Guardian fleet router (`AIWA_BASE`) | owner | Yes | Reverse-proxy clerk/consult; occupancy + consult gate |
| Local LLM Board `SeatProbe` | GET status | Yes | Frozen Board; `DefaultSixNinetyBase = http://192.168.1.240:8080`. Status GET, not a token path |
| `690-routing/` swap + captured-flags curls | operator | Yes | Rollback / occupant check. Scripts live **on** the CT as well |
| Pi `llamacpp-690` | client | **No** (remapped) | Now guardian `:8080/v1` |
| DSH `llamacpp-690` | client | **No** (PR10) | Remapped to guardian; do not revert |
| Codex / Claude / Grok live configs | client | **No** | |
| Any LAN process | open bind | **Can POST** | `--host 0.0.0.0`, no API key, CORS `*`. This is the real completions bypass |

`.240` is reachable without guardian. That is the remaining
policy-plus-network hole. It is also the Board probe and operator
rollback URL.

## Bypass-control proposal (not deployed)

**Decision: do not deploy network or new process enforcement.** Keep
the current bind + policy + guardian enforcer. Revisit AIWA POST
allowlisting only after Board is unfrozen or an ops allowlist is
written that preserves rollback.

### Option A — policy and current binds (recommended / current)

| | |
|---|---|
| What | Docs + `:8081` loopback + single-llama enforcer + slot poller + consult gate |
| Rollback | None (already live) |
| Compatibility | None |
| Gap | LAN can still POST `.240`; loopback can still POST `:8081` when GLM is up |

### Option B — API key on Workbench `:8081`

| | |
|---|---|
| What | `llama-server --api-key`; guardian injects it on upstream calls |
| Blocks | Loopback clients that skip `:8080` while the model is up |
| Breaks | In-repo benches that default to `:8081`; any forgotten local curl; RigMonitor-style probes still on 8081 |
| Rollback | Remove the flag from `ecosystem.config.cjs` args and watched delete-and-start `local-llm` |
| Impact | MCC/Pi/Hermes stay on `:8080` so they keep working. Requires a watched PM2 mutation — **out of scope now** |

### Option C — Windows Firewall on `:8081`

| | |
|---|---|
| What | Block inbound TCP 8081 |
| Blocks | Little: the port is already `127.0.0.1`. Windows Firewall does not reliably filter loopback the way operators expect |
| Breaks | Easy to misfire onto `:8080` (public bind) and take down every client |
| Rollback | Delete the rule |
| Impact | High operational risk, near-zero benefit. **Do not deploy** |

### Option D — AIWA nftables/iptables allowlist on `.240:8080`

| | |
|---|---|
| What | Accept TCP 8080 only from Workbench LAN + Tailscale (`100.124.41.115`) + maybe the Proxmox host |
| Blocks | Other LAN completions that skip guardian |
| Breaks | Board `SeatProbe` if Workbench IP is omitted; operator curl from any unlisted host; future agents on other boxes; CT-local traffic is fine (swap scripts run on 210) |
| HTTP GET vs POST | Packet filters cannot keep Board GET `/v1/models` while denying POST `/v1/chat/completions` without an HTTP proxy |
| Rollback | Flush the chain; service already binds `0.0.0.0` |
| Impact | Highest-value control **and** highest compatibility cost. Needs a written allowlist that includes Board + rollback curls. **Do not deploy in this session** |

### Option E — extra process policy (AppLocker / job objects)

| | |
|---|---|
| What | Only PM2 `local-llm` may launch `llama-server.exe` |
| Already have | `enforce_single_llama` — PM2 PID is source of truth |
| Breaks | Manual benches that start their own `llama-server` on 8081 |
| Rollback | Stop adding AppLocker; enforcer stays (it is load-bearing) |
| Impact | AppLocker is a watched OS change. The enforcer already covers the crash-loop that mattered. **Do not add AppLocker now** |

### Compatibility summary

| Consumer | `:8080` guardian | Direct `:8081` | Direct `.240` | If we locked `.240` |
|---|---|---|---|---|
| Pi local models | yes | no | no | unaffected |
| Hermes `qwen-llamacpp` | yes | no | no | unaffected |
| Grok/Hermes worker MCP | yes (`/__guardian/workers`) | no | no | unaffected |
| Codex/Claude native subagents | no (cloud) | no | no | unaffected |
| Board SeatProbe | no | no | GET | **must stay allowed** |
| Operator swap verify | health on both | no | GET `/v1/models` | **must stay allowed** |
| LAN stranger POST | can (guardian) | no (loopback) | **yes today** | would stop |

## What this session did not do

- No firewall or nftables change
- No PM2 start/stop/delete, no `ecosystem.config.cjs` live-flag edit
- No Pi spawn, no `LOCAL_WORKER_ENABLED` flip
- No live Codex/Claude/Grok/Hermes config edit
- No POST to `.240` or `:8081`

Offline check: `python qwen-queue/harness/check_mcp_stdio.py` (includes
`codex` and `claude` sources). Live policy check:
`python qwen-queue/harness/check_rollout.py`.
