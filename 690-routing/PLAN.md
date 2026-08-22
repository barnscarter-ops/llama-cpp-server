# 690 Qwen consult / Nemotron clerk — finish the split

**Created:** 2026-08-22
**Target repo:** `D:\Workspace\Infrastructure\llama-cpp-server` (this tree) + live CT 210 `/opt/llama/`
**Branch:** `main`
**Canonical doctrine:** `C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`
**Previous diagnosis:** `C:\Workspace\Active\brain\inbox\2026-08-21-qwen-nemo-routing.md`

Diagnosis is done. This plan **installs** it: one-command swap, aliases that match pi, Nemotron as boot default, Qwen as a thinking-on consult, both seats proven, Nemotron left on `:8080`.

---

## Codebase Primer

*(Orchestrator-only. Fold anything a session needs into that session's text.)*

- **Executor for these sessions:** a capable cloud/desktop agent with SSH (Grok, Claude, Hermes). **Not** Nemotron. **Not** Qwen-as-pi-worker. This is infrastructure on the live inference box.
- **Box:** Proxmox `aiwa-poc` `192.168.1.230`, CT 210 `llama-vulkan`, endpoint `http://192.168.1.240:8080`. Access: `ssh -i ~/.ssh/id_ed25519_proxmox root@192.168.1.230` then `lxc-attach -n 210`.
- **Default model (systemd `llama-server.service`):** Nemotron 3.5 Lightning 30B-A3B Q5_K_M, **131k**, `--reasoning off`, MTP on. Clerk.
- **Consult model (manual today):** Qwen 3.8 27B UD-Q6_K + MTP head, **262k**, thinking **ON** (do not pass `--reasoning off`). Same GPU, same port after swap.
- **One model at a time** on the R9700. Mesa 26 + ReBAR: occupancy = `vram_used + gtt_used`.
- **pi:** provider `llamacpp-690`, `baseUrl` `http://192.168.1.240:8080/v1`. Model ids: `nemotron-3.5-lightning-30b-a3b` (maxTokens 32768, ctx 131072) and `qwen3.8-27b` (ctx 262144). Server `--alias` must match these ids after Session 2.
- **pkill trap (WORKBOARD 2026-08-21):** `pkill -f <pattern>` self-kills when the shell command line contains the pattern. Use `pkill -f '[P]attern'`.
- **Night 4:** AIWA cutover is scheduled **Saturday 2026-08-22 evening ~18:30** (`aiwa-transplant/NIGHT4-PLAN.md`). CT 210 may be rebuilt. **Every live change must also land in this git tree** (`690-routing/`). If Night 4 has started or is <2h away, do Sessions 1–3 as git-only and defer live swap/smoke to after soak. Check `C:\Workspace\Active\brain\WORKBOARD.md` before any llama restart.
- **Do not:** quality-bench Nemotron vs Qwen; raise Nemotron to 262k; add an API key; touch Night 4 gates; dispatch MacBridge-shaped coding to either local model; commit untracked `memory/` in this repo (not this work).

---

## Session 1 — Probe, capture flags, restore clerk default

**Goal:** we know what is on `:8080`, we have the exact live command lines for both models in git, and Nemotron is the default again unless Carter is mid-consult.
**Independent:** yes
**Stack / decisions:** read-only first. Restoring Nemotron is a service restart — announce on WORKBOARD before doing it.

**Tasks:**
1. `curl -sS --max-time 5 http://192.168.1.240:8080/v1/models` (and `/health` if present). Record model id(s). A timeout or connection refused is a finding, not a license to reboot the CT.
2. SSH CT 210. Capture: `systemctl cat llama-server.service`; `systemctl is-active llama-server`; `ps aux | grep -E '[l]lama-server'` (full argv); files under `/opt/llama/` (scripts, GGUF names). Copy the **exact** Qwen launch line from history, a leftover script, or Carter's last consult — do not invent flags. Known pieces: UD-Q6_K weights, `-md mtp-Qwen3.8-27B-Q4_0.gguf --spec-type draft-mtp`, `-c 262144`, q8_0 KV both, **no** `--reasoning off`.
3. Write the captured units/argv into `690-routing/captured-flags.md` in this repo (Nemotron unit + Qwen argv + GGUF paths + current `--alias` values).
4. If Qwen is loaded and Carter is not actively consulting: announce on WORKBOARD, then restore Nemotron (`systemctl start llama-server` after stopping the manual Qwen process with the `[P]` pkill form). If he is still consulting, stop after task 3 and say so in the session summary — do not yank it.
5. Confirm `GET /v1/models` after any restore.

**Verification:**
- Run: `curl -sS http://192.168.1.240:8080/v1/models` — expected: a model id (recorded). After restore: Nemotron alias, not Qwen.
- Run: `git -C D:\Workspace\Infrastructure\llama-cpp-server status --short 690-routing/` — expected: `captured-flags.md` present with both command lines.

**Commit:** `docs(690): capture live Nemotron unit and Qwen consult argv`

---

## Session 2 — Swap scripts + matching aliases

**Goal:** two scripts on CT 210 and mirrored in git. `swap-qwen-consult` stops systemd and serves Qwen on `:8080` with thinking on. `swap-nemo-clerk` kills Qwen and starts systemd. Aliases match pi model ids.
**Independent:** no (needs Session 1's captured-flags.md)
**Stack / decisions:** keep systemd as the **boot default** (Nemotron, enabled). Qwen is never a systemd default. Both binds stay `0.0.0.0:8080` so pi's baseUrl does not change. Nemotron `--alias nemotron-3.5-lightning-30b-a3b` (keep `local-llm` as a second `--alias` if the unit already has it). Qwen `--alias qwen3.8-27b`. If the live Nemotron unit's alias differs, update the unit **and** captured-flags.md.

**Tasks:**
1. Author `690-routing/swap-qwen-consult.sh` and `690-routing/swap-nemo-clerk.sh` from captured-flags.md. Scripts must: refuse if the other swap is mid-load; use `pkill -f '[l]lama-server'` only on the **manual** Qwen process (not a blind kill of systemd's pid without `systemctl stop` first); print `GET /v1/models` when ready; never pass `--reasoning off` on Qwen; always pass `--reasoning off` on Nemotron (via systemd).
2. If Nemotron systemd `--alias` does not match pi, patch `/etc/systemd/system/llama-server.service`, `daemon-reload`, and copy the unit into `690-routing/llama-server.service` (mirror). Args change on systemd = reload + restart, not a silent edit.
3. Install copies to CT 210 `/opt/llama/` (chmod +x). Same bytes as git.
4. Run **one** full cycle only if the 690 is idle and Night 4 is not in progress: Nemotron → Qwen → confirm `/v1/models` is `qwen3.8-27b` → Nemotron → confirm clerk alias. If Night 4 is close, skip the live cycle and say so.

**Verification:**
- Run: `curl -sS http://192.168.1.240:8080/v1/models` after the cycle — expected: Nemotron clerk alias.
- Run: scripts exist at `/opt/llama/swap-qwen-consult.sh` and `690-routing/` in git, no `--reasoning off` on the Qwen script.

**Commit:** `feat(690): Qwen consult / Nemotron clerk swap scripts`

---

## Session 3 — Client wiring so the wrong seat cannot happen by habit

**Goal:** pi and agent docs tell the truth; Nemotron is not advertised as a reasoner to pi; Qwen is labeled consult-only.
**Independent:** no (needs Session 2 aliases)
**Stack / decisions:** do not remove `llamacpp-690/qwen3.8-27b` from `enabledModels` — consult still needs it. Do not make it the default. Set Nemotron `"reasoning": false` in `C:\Users\carte\.pi\agent\models.json` (server already strips thinking; pi should not ask for it). Leave Qwen `"reasoning": true`.

**Tasks:**
1. Patch `C:\Users\carte\.pi\agent\models.json`: Nemotron `reasoning: false`; names already distinguish specialist vs clerk — keep that.
2. Patch `C:\Users\carte\.pi\agent\AGENTS.md` local-LLM paragraph: clerk vs consult, swap scripts, never pi-worker Qwen, `--reasoning off` is Nemotron-only.
3. Patch `C:\Workspace\Shared\Agents\AGENTS.md` and `C:\Workspace\Active\brain\knowledge\local-llm-architecture.md`: replace “launch manually (e.g. :8081)” with `/opt/llama/swap-qwen-consult.sh` / `swap-nemo-clerk.sh`. Same routing table, add the commands.
4. Write `690-routing/README.md` — 10-line operator card: when to swap, the two commands, how to tell which model is up, pkill trap, Night 4 git-mirror rule.
5. Commit brain architecture if that clone is the one in use (`C:\Workspace\Active\brain`). Brain currently has **no remote** — commit local, do not pretend a push.

**Verification:**
- Run: python/jq showing `llamacpp-690` Nemotron `reasoning` is false and Qwen is true.
- Run: grep of architecture + pi AGENTS.md for `swap-qwen-consult`.

**Commit (llama-cpp-server):** `docs(690): operator card and client routing pointers`
**Commit (brain, local):** `docs: 690 swap script paths in local-llm architecture`

---

## Session 4 — Prove both seats; leave Nemotron up

**Goal:** a clerk smoke and a consult smoke exist as logged evidence; `:8080` is Nemotron when you walk away.
**Independent:** no
**Stack / decisions:** smokes are tiny. Clerk: `max_tokens` 256, prompt that requires a short factual answer — `reasoning_content` must be empty/absent. Consult: one architecture question, thinking allowed, do not attach tools. Then swap back. Announce llama restarts on WORKBOARD.

**Tasks:**
1. With Nemotron up: POST `/v1/chat/completions` alias `nemotron-3.5-lightning-30b-a3b` (or current clerk alias), `max_tokens` 256. Save request+response under `690-routing/smokes/clerk.json`. Fail the session if `reasoning_content` is long or `content` is empty.
2. Swap to Qwen. POST alias `qwen3.8-27b`, one short hard question (`max_tokens` 2048 is fine). Save `690-routing/smokes/consult.json`. Expect thinking + an answer. Fail if it never answers (length/truncated think-only).
3. Swap back to Nemotron. `GET /v1/models` must be the clerk. `systemctl is-active llama-server` active. Manual Qwen process gone.
4. Append results (tg if timings present, occupancy if easy) to `690-routing/README.md` and a one-line update to architecture if numbers differ from the doc.
5. WORKBOARD: no leftover 690 workstream, or mark this one done.

**Verification:**
- Run: `curl -sS http://192.168.1.240:8080/v1/models` — expected: Nemotron clerk id.
- Run: both smoke JSON files in git; clerk has content and no think-bomb; consult has an answer.

**Commit:** `test(690): clerk and consult smokes; Nemotron restored as default`

---

## Out of scope (do not pull into these sessions)

- Nemotron-vs-Qwen HumanEval / tool-call quality bench
- Nemotron ctx 262k / 512k / 1M ladder (`inbox/2026-08-20-nemo-context-testing.md`)
- API key / CORS lockdown (do before any WAN/DNAT, not required to finish the split)
- Night 4 AIWA cutover
- Using either local model as a MacBridge / Swift / unknown-API executor

Those stay inbox threads. This plan is done when Session 4's verification passes.

## Revisions

- **2026-08-22** — initial plan after consult/clerk diagnosis. Four sessions, live box + git mirror, Night 4 constraint.
