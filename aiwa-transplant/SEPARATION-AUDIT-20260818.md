# Dev/prod separation audit — what stays on the PC, what moves to AIWA

2026-08-18. Doctrine set by Carter tonight: **PC = dev, AIWA = everything
live / customer-facing / 24-7.** Two permanent inference tiers — the 4060 Ti
serves dev/local/hermes-pc forever (Qwen3.6-35B MoE, 80 t/s, 128k ctx); the
R9700 in the new AIWA serves production exclusively, no longer shared with
dev work. The map's old "llama serving moves off the X870E" line is
superseded. Goal: no cross-machine hopping during live calls.

This classifies everything currently running on the X870E (24 scheduled
tasks + 2 pm2 apps). Moves happen AFTER cutover proves stable, one at a
time, each with its own rollback.

## Stays on the PC (dev / PC-local by nature)

| Item | Why it stays |
|---|---|
| llama-guardian + local-llm (pm2) | The dev inference tier — the point of the doctrine |
| Hermes_Gateway / HermesWebUI / Hermes_InteractiveScreenshot | hermes-pc drives THIS machine |
| Brain Stale Session Watcher, ClaudeIdleCleanup, KillOrphanedPi | PC session hygiene |
| RigMonitor | monitors this rig |
| MCC Dashboard on Login | login-time convenience for this desktop |
| Maverick Launch Log Morning | Carter-facing morning log |
| OMP_AuthBroker, cua-driver-serve | agent tooling for local dev |
| Grizzly Weekly Sync + Brain Vault Ingest | writes into the brain vault on this disk |

## Candidates to move to AIWA (live / customer-facing / 24-7)

| Item | Notes / blockers |
|---|---|
| Grizzly SEO Weekly Run + Monitor + Watchdog + Photo Sync + GBP Worker | The flagship 24-7 client automation. Blocker: anything using Playwright with the Windows Chrome profile needs a Linux-compatible auth path on AIWA, or a headless profile migration |
| Grizzly_HCPCookieCheck / Grizzly_HCPRelogin / HCP Session Relogin | Session keepalive for client CRM — belongs with prod, but rides on the PC's logged-in browser profile today |
| ProxmoxBackup-Nightly | Backs up AIWA — after cutover this should be a cron ON the new AIWA (or PBS), not a task on Carter's desktop |

**Resolved 2026-08-18:**

- **housecall-pro-mcp** — NOT a duplicate of CT 102. Deliberate split, per the
  task's own description: the PC daemon runs as `carte` because it decrypts
  the user's Chrome cookies for HCP sessions (moved out of PM2 2026-07-11 —
  SYSTEM can't decrypt them). CT 102 (`hcp-mcp-prod`) is the Linux prod MCP;
  the PC daemon is the Windows-bound cookie side. **Stays on the PC** unless
  HCP auth is reworked.
- **agent-os (kernel + AI-Tooling-Radar)** — RETIRED, not moved. Carter's
  call: project superseded by a simpler approach. Both tasks disabled (not
  deleted); project moved to `C:\Workspace\Archive\agent-os-2026-08\`.

## Sequencing

1. Night 3 (Proxmox PoC + restore test) and Night 4 (cutover) come first —
   nothing moves until the new AIWA has run clean for days.
2. Then migrate one service at a time, prod-window style: move, verify on
   AIWA, disable (don't delete) the PC task, keep the XML export as
   rollback.
3. The Windows-dependent items (Playwright profiles, anything needing a
   desktop session) move LAST or stay — reliability beats purity, per
   Carter: "some things are going to be faster, easier and more reliable
   here on pc."

Related: `NIGHT3-PLAN.md` · `transplant-map.html` (corrected end state)
