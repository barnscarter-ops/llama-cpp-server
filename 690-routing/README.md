# 690 operator card — Qwen consult / Nemotron clerk

One model at a time on the R9700 (`http://192.168.1.240:8080`). Boot default is Nemotron (systemd `llama-server.service`, `--reasoning off`). Qwen is a thinking-on consult, never a pi worker.

- **Swap to consult:** `lxc-attach -n 210 -- /opt/llama/swap-qwen-consult.sh` (alias `qwen3.8-27b`, 262k, no `--reasoning off`, **vision ON via `--mmproj` mmproj-Qwen3.8-27B-F16.gguf** — permanent 2026-08-29).
- **Vision test:** send `image_url` content to `/v1/chat/completions`; the mmproj lives in `/opt/llama/models/`. If the mmproj file is missing (CT rebuild), the swap script fails — re-fetch `mmproj-F16.gguf` from `unsloth/Qwen3.8-27B-GGUF`.
- **Swap back to clerk:** `lxc-attach -n 210 -- /opt/llama/swap-nemo-clerk.sh` (starts systemd; alias `ornith-1.5-35b-a3b`; Nemotron unit kept as `llama-server.nemotron.service.rollback`).
- **Which is up:** `curl -sS --max-time 5 http://192.168.1.240:8080/v1/models` — walk away only when the id is the clerk.
- **pkill trap:** `pkill -f llama-server` self-kills. Use `pkill -f '[l]lama-server'` and only after `systemctl stop` if the clerk unit is running.
- **Announce** any llama restart on `C:\Workspace\Active\brain\WORKBOARD.md` first.
- **Night 4 / CT rebuild:** live copies on CT 210 can die; keep the same bytes in this `690-routing/` git tree (`barnscarter-ops/llama-cpp-server`). Gate 4 is **keep/repair CT 210**, not the stale host unit in `aiwa-transplant/night4/llama-server.service`.
- **Manager merge (PLAN READY):** do not rewrite these scripts. Plan: repo-root `PLAN.md` / `D:\Workspace\Active\WindowsApps\LocalLlmBoard\PLAN.md`. Pickup: repo-root `HANDOFF.md`.
- **R9700 telemetry (LIVE 2026-08-29):** permanent monitoring in `../r9700-monitor/` — debugfs collector -> node_exporter textfile -> homelab-prometheus -> Grafana. Dashboard `http://192.168.1.12:3000/d/r9700-aiwa`, Prometheus `http://192.168.1.12:9090` (query `r9700_temp_c`). Thermal/collector alerts post to Slack **#hermes-agent**. Terminal fallback unchanged: `tools/r9700-watch.py` / `r9700-watch-live.py`. Operator card: `../r9700-monitor/README.md`.

## Session 4 smokes (2026-08-22)

- Clerk (`nemotron-3.5-lightning-30b-a3b`, max_tokens 256): content `Paris`, `reasoning_content` empty. 2 predicted tokens @ 49 t/s — too short to compare to the ~150 t/s bench.
- Consult (`qwen3.8-27b`, max_tokens 2048): thinking 3894 chars then a 549-char answer, `finish_reason=stop`, ~43.9 t/s tg (matches 41–48). JSON under `smokes/`.
- Walk-away: `:8080` is the clerk; systemd `llama-server` active.

## Manual rollback to Nemotron (Ornith -> Nemotron)

Only with Carter's go. On CT 210 (`lxc-attach -n 210`):

1. `systemctl stop llama-server`
2. `cp /etc/systemd/system/llama-server.service /etc/systemd/system/llama-server.ornith.bak`
3. Install `llama-server.nemotron.service.rollback` from this directory as `/etc/systemd/system/llama-server.service`, then `systemctl daemon-reload && systemctl start llama-server`
4. Check `curl -s localhost:8080/health` and that `/v1/models` lists `nemotron-3.5-lightning-30b-a3b`.

In this repo, revert the Ornith PR. Guardian still maps the Nemotron id to the clerk seat, but its swap check polls for `SERVING_IDS["clerk"]`, so restore that constant too or a Qwen-session reload will time out.
