# llama-cpp-server

Local model serving stack (4060 Ti production). Canonical architecture doc:
`C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`.

## Standing rules

- Global config still applies alongside this file: `~/.claude/CLAUDE.md`
  (session-close procedure), `~/.agents/AGENTS.md` (Proxmox/AIWA via Orca
  sandboxes only), `~/.codex/AGENTS.md` (bootstrap). This file adds repo-specific
  structure; it does not replace them.
- Do not commit live ecosystem flags (e.g. `FLEET_ROUTER=true` in
  `ecosystem.config.cjs`) to origin unless Carter explicitly wants that default.
- Do not run PM2 from user space; read `C:\ProgramData\pm2\dump.pm2` instead.
- AIWA/Proxmox changes go through Orca sandboxes and the AIWA deployment runbook.
- **Pi agents paused (2026-08-28).** Do not spawn `pi` / Pi TUI / Orca `--agent pi`. Agent work goes through **Hermes**. Attempt 1 of the watched worker gate was Hermes GLM-5.3, which launched Pi on `llamacpp/local-llm` and circular-killed guardian. Pi also has a dual-install (independent `%APPDATA%\npm` vs Hermes-managed; `pi.cmd` truncates multi-line prompts). Guardian's coded Pi runner stays as-is in source; do not live-enable `LOCAL_WORKER_ENABLED` until Pi is unpaused or the runner is switched.
- **Local workers (Grok/Hermes MCP, live 2026-08-28).** Prefer `local_worker_submit` only for bounded `mechanical_execution` / `tool_execution` at `quality_floor=standard`. Do not pass model, profile, endpoint, or runner. Planning/deep_analysis need an explicit `gate`. Frontier, unknown APIs, and architecture stay cloud. Accepted ≠ done — poll `local_worker_status` until a terminal result. If guardian returns `requires_cloud`, `consult_gate_required`, `consult_unavailable`, or `local_workers_disabled`, escalate visibly; do not retry as a raw local model.

## Agent file conventions

This repo keeps agent docs in a fixed, small set of canonical files. Do not
invent alternative names (no `HANDOFF.md`, `NEXT.SESSION.md`, or per-agent
duplicates).

| File | Purpose | Update rule |
|---|---|---|
| `AGENTS.md` | repo rules + session-close contract (this file) | rarely, deliberately |
| `CLAUDE.md` | Claude-only overrides (optional) | rarely |
| `docs/NEXT-SESSION.md` | THE handoff: shipped / open / next repo + prompt | every session end |
| `memory/journal.md` | chronological log of what happened | every session end |

Durable facts, decisions, and cross-repo knowledge live in the Obsidian brain
vault (`C:\Workspace\Active\brain`), NOT in a repo file. Project-level durable
notes go in the vault's `projects/<repo>.md`; general knowledge in `knowledge/`.

Historical one-off records (`R9700-SWAP-HANDOFF.md`,
`SESSION-2026-08-06-vulkan-sweep.md`, `NEXT-SESSION-HANDOFF.md` for closed
workstreams) are kept but are not the live handoff.

## "prepare for session close" — mandatory ritual

When told "prepare for session close" (or when a session is clearly ending):

1. **Check the tree first** (`git status`).
   - Commit + push ONLY your own session files: `docs/NEXT-SESSION.md`,
     `memory/journal.md`, plus your actual code edits.
   - NEVER `git add -A` / `git add .` on a dirty tree.
   - **Unrelated uncommitted changes present → STOP and report them before
     proceeding.**

2. **Update info docs.** Any `docs/*.md`, README, or contract your work touched
   must match reality.

3. **Update memory.**
   - `memory/journal.md` — append a date-stamped entry: what you did, what
     changed, what's left.
   - Durable facts/decisions → brain vault `projects/llama-cpp-server.md` (or `knowledge/`).

4. **Write `docs/NEXT-SESSION.md`** with three sections:
   - **Shipped** — done + verified
   - **Open** — pending work, blockers
   - **Next session** — exact repo name + exact starting prompt

5. **If the next step is unclear → ask.** Never guess the next session's job.

6. **Report back:**
   - `Next session: <repo>`
   - `Prompt: <prompt>`
   - `Start it?`

7. **If Carter says start it:** start that job in a **new** Grok (or named)
   session with the exact prompt. Do **not** continue the next session's work
   in the closing conversation — that defeats the close. Confirm the new
   session received the prompt, then stop.
