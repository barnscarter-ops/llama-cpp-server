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
