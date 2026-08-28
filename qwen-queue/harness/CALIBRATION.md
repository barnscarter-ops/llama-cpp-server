# Local-worker preference calibration (Session 4, 2026-08-28)

Corpus items have objective checks. No consult swap was attempted. Hidden
reasoning text is not recorded.

| ID | Class | Route tried | Check | Result | Wall clock | Escalation |
|---|---|---|---|---|---|---|
| C1 | `tool_execution` standard | Workbench via MCP `source=grok` | `GROK_MCP.txt` contains `S34_GROK_OK` | pass — `qj_3e8a4e29129a4a718d5550e7ca8f652a` | ~20s (pair with C2 ≈ 42s total) | none |
| C2 | `tool_execution` standard | Workbench via MCP `source=hermes` | `HERMES_MCP.txt` contains `S34_HERMES_OK` | pass — `qj_c926913dc0db46b98d7329a6aa42d62b` | (same window) | none |
| C3 | `tool_execution` standard | Workbench HTTP (attempt 2) | `SMOKE.txt` = `GATE_ATTEMPT_2_OK` | pass — `qj_d52cb887f9ae49db9ef440d322595a9f` | ~25s | none |
| C4 | `planning` no gate | guardian admission | HTTP 409 `consult_gate_required` | pass | <1s | stay cloud / wait for operator gate |
| C5 | `planning` `gate=operator` | guardian admission | HTTP 503 `consult_unavailable`; AIWA occupant still clerk | pass | <1s | no auto-swap |
| C6 | `tool_execution` `quality_floor=frontier` | guardian admission | HTTP 409 `requires_cloud` | pass | <1s | cloud |
| C7 | architecture / unknown API / Swift | cloud (not submitted local) | MacBridge S2–S10 shipped on GLM-5.3 after local executors failed | cloud-first | n/a | cloud |
| C8 | `mechanical_execution` standard | AIWA clerk (policy) | work class maps to clerk, no swap | not live-run this session (Pi orchestrators paused; clerk is Nemotron mechanical only) | n/a | prefer_local when flag on |

Workspace: `D:\Workspace\tmp\guardian-s34-2026-08-28`. AIWA occupant **clerk** before and after C4–C6.

## Preference table

See `LOCAL-WORKER-MANAGER.md`. Summary: prefer local only for bounded standard
`tool_execution` (and later `mechanical_execution`). Everything else escalates.

## Re-evaluation triggers

Repeated local failures on the same class; Workbench or AIWA model change;
context-limit change; guardian runner switched off Pi.

## Cloud comparison note

A parallel paid-cloud run of C1/C2 was not repeated here. Prior measured
evidence (MacBridge local vs GLM-5.3) already places unknown-API and
architecture work on cloud. C1–C3 show the local tool loop is correct for
tiny write-and-verify fixtures.
