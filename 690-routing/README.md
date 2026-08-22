# 690 operator card — Qwen consult / Nemotron clerk

One model at a time on the R9700 (`http://192.168.1.240:8080`). Boot default is Nemotron (systemd `llama-server.service`, `--reasoning off`). Qwen is a thinking-on consult, never a pi worker.

- **Swap to consult:** `lxc-attach -n 210 -- /opt/llama/swap-qwen-consult.sh` (alias `qwen3.8-27b`, 262k, no `--reasoning off`).
- **Swap back to clerk:** `lxc-attach -n 210 -- /opt/llama/swap-nemo-clerk.sh` (starts systemd; alias `nemotron-3.5-lightning-30b-a3b`).
- **Which is up:** `curl -sS --max-time 5 http://192.168.1.240:8080/v1/models` — walk away only when the id is the clerk.
- **pkill trap:** `pkill -f llama-server` self-kills. Use `pkill -f '[l]lama-server'` and only after `systemctl stop` if the clerk unit is running.
- **Announce** any llama restart on `C:\Workspace\Active\brain\WORKBOARD.md` first.
- **Night 4 / CT rebuild:** live copies on CT 210 can die; keep the same bytes in this `690-routing/` git tree (`barnscarter-ops/llama-cpp-server`).

## Session 4 smokes (2026-08-22)

- Clerk (`nemotron-3.5-lightning-30b-a3b`, max_tokens 256): content `Paris`, `reasoning_content` empty. 2 predicted tokens @ 49 t/s — too short to compare to the ~150 t/s bench.
- Consult (`qwen3.8-27b`, max_tokens 2048): thinking 3894 chars then a 549-char answer, `finish_reason=stop`, ~43.9 t/s tg (matches 41–48). JSON under `smokes/`.
- Walk-away: `:8080` is the clerk; systemd `llama-server` active.
