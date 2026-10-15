# Benchmark harness v2 (protocol section 17, lean plan)

Builds the evidence that picks the three local seats: a Workbench worker bee, an R9700 fast executor and an R9700 slow reasoner. It tunes four levers per model, guards numerics, checks 128k context, times cold loads, scores a frozen ~300-item role suite, tests tool calling against the real Hermes and Guardian schemas, runs a separate Jev routing track, and writes an append-only ledger that can generate Guardian's `models.json`.

Python 3 stdlib only. Everything is testable on a fake machine; the tests never load a model, start a process or contact a live server.

    python3 -m unittest discover -s benchmarks/v2/tests          # 130 tests, ~35 s
    python3 benchmarks/v2/run_lane.py dry-run --lane r9700 --stages smoke,tune,confirm,ladder,quality

## Safety rules the code enforces
- Nothing real starts without a window file that Carter wrote (`window-template` only prints an example). It names the lane, start, end, approver and per-action approvals. Outside the window, or missing `stop_services` (and `guardian_quiet` on the R9700), the run refuses.
- Before a lane: snapshot which production services are active, write `runs/<lane>.RESTORE_REQUIRED`, stop them, verify stopped and GPU idle, check 20 GB disk. Service status fails closed: a status command that errors, prints something unparseable, or does not list the service is "unknown" and aborts the run; so does finding none of the configured services running (unless the window sets `allow_no_production_service`). After: start only what was active and verify before clearing the marker. Verification needs the service active and listening again since the stop (journal `--since`; pm2 uptime newer than the stop), and on the R9700 the process list must show Nemotron and not Qwen. If it cannot be verified the marker stays and `run_lane.py restore` finishes the job.
- Ctrl-C or SIGTERM once stops at the next check and restores; a second one interrupts a blocked request or perplexity run immediately (restore still runs). SIGKILL or a reboot leaves production stopped until `restore`.
- Abort and restore on: OOM or crash twice in a row, power alarm, GPU temperature over 90 C, STOP file (`runs/STOP` or `runs/<lane>.STOP`), window end, disk under 20 GB, any request leaving the lane's own bench port (8099 Workbench, 8090 R9700). Requests to 8080, 8081 or 192.168.1.240 raise before they are sent; the swap test sends no HTTP at all (it watches the service, its journal and its process list).
- One model per machine at a time (flock on the lane lock; the R9700 uses the same lock file as the swap scripts). Workbench and R9700 lanes can run in parallel in separate terminals.
- The production Nemotron on the R9700 is touched only by `swap` (the real swap scripts, logged) and by the lane stop/restore above. `swap` stops the loop if a script fails or times out, and always ends by putting Nemotron back and checking which model is resident; if that fails the marker stays and the command exits with an error.
- Every lane takes a lock (`safety.lock_file`; the R9700 shares the swap scripts' lock, the Workbench uses `runs/workbench.lock`), so two harness runs on one machine refuse.
- Cloud spend needs `cloud_spend_cap_usd > 0` and `cloud_endpoint_host` in the window and a key in a named environment variable; the key is never printed or written.

## Stages (all take `--lane workbench|r9700`)
| Command | What it does | Touches the machine? |
|---|---|---|
| `plan` | Fit table for every candidate: f16/q8_0 KV, draft, MoE offload. Disqualifies anything not 100% on GPU at 131072 context. | No |
| `freeze` | Builds the role suites and `frozen/FROZEN.json` hashes. Already committed; rerun only to change a suite. | No |
| `preflight` | Ledger chain, frozen hashes, binary paths, llama-perplexity has `--kl-divergence-base`, files on disk, disk space. | Reads only |
| `run --stages smoke` | S1: loads, tool round trips, 4k/32k prompts, decode at 8k, VRAM estimate error. Drops models that fail or are >10% slower than the incumbent with no new capability. | Yes |
| `run --stages tune` | S2: derive base, then levers A placement, B speculative decoding, C KV type, D batch/ubatch with the numeric guard and the 3% noise floor. Flat levers are skipped on later models. | Yes |
| `run --stages confirm` | S3: leave-one-out, final re-measure, drift check (>3% flags a rerun). Writes the `tuned` config. | Yes |
| `run --stages ladder` | S4: 8k/32k/(128k-3000) checks, max safe context, three cold loads, wake seconds. | Yes |
| `run --stages quality` | S5: frozen role mix in production reasoning mode, other mode and sampling spot-check on 60 items, role verdict. | Yes |
| `swap` | R9700: time Nemotron to Qwen and back through the real swap scripts, three round trips. Needs `qwen_load` and `swap_test` approvals. | Yes |
| `cloud` | DeepSeek reference arm on the reasoner mix, capped spend, window-approved host. | Network |
| `score` | Runs deferred model-generated code on Linux as `nobody`/in a sandbox and finishes the verdicts. | No (no GPU, no lane) |
| `jev` | Writes the 200 Jev cases (120 dev / 80 held-out by family), grades arm answers, built-in rules/majority/oracle arms. Never calls Jev. | No |
| `registry` | Ledger to a Guardian `models.json` plus `RESULTS.md`, validated by Guardian's own `model_registry.parse_registry`. | No |
| `status`, `restore`, `dry-run` | Ledger chain and markers; finish a restore; whole lane on the fake machine. | status/dry-run no |

Stages resume from the ledger. Global options (`--ledger-dir`, `--runs-dir`, `--frozen-dir`, `--lanes`, `--candidates`) go before the subcommand.

## What Carter does to start a lane
1. Review `data/tool_schemas.frozen.json` (14 tools extracted by AST from hermes-agent and `local-worker-mcp.py`) and the shortfall list in `frozen/FROZEN.json`; say go.
2. Make sure the binaries and models exist: `llama-server` and `llama-perplexity` of the same build per machine (the R9700 build update is the PC session's job), model files under the models dir. Missing files are skipped and logged, never downloaded.
3. Write a window file:

        python3 benchmarks/v2/run_lane.py window-template --lane workbench > window-workbench.json   # then edit times and approvals

   `qwen_load` allows the Qwen3.8 reasoner, `moe_exception` the Workbench expert-offload path, `alternates` the optional models, `swap_test` the swap timing.
4. Run `preflight`, then `run`:

        python3 benchmarks/v2/run_lane.py preflight --lane workbench --server-bin <llama-server> --perplexity-bin <llama-perplexity> --models-dir <dir>
        python3 benchmarks/v2/run_lane.py run --lane workbench --window window-workbench.json --stages smoke,tune,confirm,ladder,quality \
            --server-bin <llama-server> --perplexity-bin <llama-perplexity> --models-dir <dir>

   Paths can also come from `BENCH_SERVER_BIN`, `BENCH_PERPLEXITY_BIN`, `BENCH_MODELS_DIR` or `lanes.json`.
5. Optional: `--maverick tasks.jsonl` and `--humaneval he.jsonl` (rerun `freeze` first), a Hermes bench command in `lanes.json` `hermes_bench`, a UPS query in `power_alarm` (see below).
6. Stop at any time with `touch runs/STOP`. If anything dies, run `python3 benchmarks/v2/run_lane.py restore --lane <lane>`.

## UPS alarm
The R9700 lane queries `upsc cp1500@localhost ups.status` (CyberPower CP1500 under NUT on AIWA) every second and aborts and restores only when the status contains `OB` (on battery). `LB` and other flags are ignored: on line power they only mean the runtime is under the configured target, and a real low-battery state always comes with `OB`. A dead monitor is not silent: `preflight` fails if the query returns nothing, the ledger header records `power_monitor: true` only when the query worked, and three failed queries in a row during a run abort it. Note that restore reloads Nemotron even if the abort was caused by being on battery. The Workbench lane has no UPS query. Muting the beeper is not done by the harness.

## Workbench GPU spill (Windows)
`nvidia-smi` memory does not show WDDM spilling into system RAM, so the Workbench reads `\GPU Adapter Memory(*)\Shared Usage` with `typeperf` (every 5 s), takes a baseline with the production services stopped, and treats growth above 256 MiB as a spill. If that reading is unavailable the run aborts (`require_shared_reading`) and `preflight` says so. Also set the NVIDIA driver to "Prefer No Sysmem Fallback" before a run; that makes an overfull load fail instead of silently spilling.

## Model-generated code
Nothing runs model-written Python as the person who owns the machine. The Workbench (Windows PC) never executes it: the quality stage saves raw answers to `runs/<run>/raw/*.jsonl`, leaves code items unscored and writes a provisional verdict. Copy that folder and the lane ledger to Linux and run the score step, which runs the code as root-dropped-to-`nobody` (or inside `--sandbox-cmd`, for example `docker run --rm -i --network=none <image>`), with CPU, memory and file-size limits (with Docker, enforce the timeout inside the container, e.g. `timeout` in the image command or `--stop-timeout`, because killing the local `docker` client does not stop the container), a scrubbed environment and a killed process group on timeout. It writes `ledger/<lane>.scored.jsonl` (the source ledger is untouched) and the final `role_verdict`:

    sudo python3 benchmarks/v2/run_lane.py --ledger-dir benchmarks/v2/ledger score --lane workbench \
        --raw-dir <copied>/raw --source-ledger benchmarks/v2/ledger/workbench.jsonl

The R9700 lane scores inline only when it runs as root (drops to `nobody`) or has `--sandbox-cmd`; otherwise it defers the same way. Without an unprivileged runner `score_python` refuses to execute anything.

## Cloud arm rules
The window must name the endpoint host (`cloud_endpoint_host`); the URL must be https on exactly that host, not an internal name, not port 8080/8081, and every address it resolves to must be public (no private, loopback, link-local, CGNAT/Tailscale, ULA or multicast). Redirects are never followed, so the API key cannot travel to another host. Maverick, Hermes and tool-call items (built from Carter's real Hermes/Guardian schemas) are withheld unless the window sets `cloud_allow_internal_items`. Run the cloud arm with the same `--lane` as the reasoner lane (`slow_reasoner`'s machine, `r9700`) so the anchor lands in the ledger the score step reads. Code items from the anchor are deferred to the score step too. DNS is resolved at start; a rebinding attack between the check and the connection is not defended.

## Layout
`bench_v2/` modules (see each docstring), `candidates.json` (models, criteria, registry mapping), `lanes.json` (machines, ports, safety commands), `data/` (tool schema snapshot, guard text slice), `frozen/` (suites + hashes), `ledger/` (results, hash-chained JSONL, one file per lane), `tests/`.

## Reused, not duplicated
HumanEval-10 comes from `benchmarks/bench-coding.py` (loaded in place). The llama-bench/perplexity command shapes follow `bench_finalists.py`/`phase_c_quality.py`. The Hermes worker bench is only wrapped (`hermes_adapter.py`), and stays off until its `run.py` can be pointed at the bench port, because it defaults to the Guardian front door.

## Deviations and gaps
- Hard-code reference solutions live in `tests/hard_code_references.py` so the suites carry no answers; the frozen items hold tests only.
- Maverick (about 35 items) and Hermes (30) pools are empty until Carter supplies them; the manifest reports the shortfall instead of padding.
- Rubric items are never auto-scored; they need a judge supplied by the caller.
- `temp_pause_c` and `temp_resume_c` in `lanes.json` are not used: the only thermal rules are the 90 C abort and the cool-down to 65 C before each point.

## Not verified (no real machine was touched)
llama-perplexity flag spellings and KL output format; speculative-decoding flags (`--spec-type` spelling) and per-request vs server-flag reasoning control for each model; amdgpu sysfs and nvidia-smi sampling on the real cards; UPS query output; the pm2/gsudo and systemd command strings; the `typeperf` counter output on the real card; `upsc` output on AIWA; the swap scripts' behavior and whether the process list (`pgrep -ax llama-server`) shows `nemotron` vs `qwen3.8` in the command line (`swap.identity_cmd`, `nemotron_regex`, `qwen_regex`); the Hermes bench base-URL override; real llama-server log buffer names; the MTP flag (the repo's Nemotron unit still has `--spec-type draft-mtp` although notes say MTP was slower or absent). Run `preflight` and the `smoke` stage first and read the ledger before trusting later stages.

## PM2 home

The Workbench lane runs `pm2` through gsudo with a single `safety.pm2_home` setting in `lanes.json`: `C:\Users\carte\.pm2`. That is the home the live daemon uses on the Workbench (reported from the machine on 2026-10-14, with `PM2_HOME` unset there) and matches the AIWA-System instructions. `AGENTS.md`, `R9700-SWAP-HANDOFF.md` and the qwen-queue docs now say the same (`C:\ProgramData\pm2` is retired). Before the first Workbench window, confirm that `pm2 jlist` with this home lists both `llama-guardian` and `local-llm`: a wrong home fails closed (status reads `unknown`, the run aborts), and it would be the wrong daemon only if both homes held registrations. An unreadable `RESTORE_REQUIRED` marker also blocks the lane and is never deleted automatically.

## Guardian during a Workbench window

`llama-guardian` is a PM2 app that idle-stops `local-llm` after 30 minutes and starts it again on demand, so the Workbench lane stops Guardian first, then the seat, and restores them in the opposite order. A seat that was already idle-stopped stays stopped afterwards (Guardian wakes it on demand). If Guardian's prewarm starts the seat between the snapshot and Guardian's own stop, the lane stops it again (`safety.stop_late`). Every `recheck_s` (300 by default on the Workbench) the lane re-reads both services and aborts with `service_resurrected` if either came back, so two models never share the 16 GB card. PM2 calls run through gsudo with `PM2_HOME=C:\Users\carte\.pm2` (see PM2 home above) and are retried three times on a timeout or unparseable output; a status that stays unknown still aborts. Before every server launch the lane also re-reads both services (a `pm2 start` that was in flight when Guardian was stopped can land between points). Start the Workbench run from an elevated (admin) shell: the check runs `gsudo pm2 jlist` every few minutes and before each launch, and a non-elevated shell would raise a UAC prompt each time. Not verified on the real machine: that the elevated `pm2 jlist` reaches the session-0 daemon, which follows the AIWA-System gsudo pattern but has not been run from this harness.

Do not start Guardian or the seat by hand during a Workbench window. The service check runs between points and before each launch, not during one blocking request (up to 30 minutes); a second model on the card would be caught as spill or OOM and the point disqualified.
