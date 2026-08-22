#!/bin/bash
# Stop systemd Nemotron clerk and serve Qwen 3.8 27B consult on :8080.
# Thinking ON — do not pass --reasoning off.
# Usage: /opt/llama/swap-qwen-consult.sh
set -euo pipefail

LOCK=/opt/llama/swap.lock
BIN=/opt/llama/src/build/bin/llama-server
MODEL=/opt/llama/models/Qwen3.8-27B-UD-Q6_K.gguf
MTP=/opt/llama/models/mtp-Qwen3.8-27B-Q4_0.gguf
LOG=/opt/llama/qwen-consult.log
PIDFILE=/opt/llama/qwen-consult.pid
HEALTH=http://127.0.0.1:8080/health
MODELS=http://127.0.0.1:8080/v1/models

mkdir -p /opt/llama
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "swap in progress (lock $LOCK); refusing" >&2
  exit 1
fi

echo "Stopping systemd llama-server (Nemotron clerk) before any pkill..."
systemctl stop llama-server || true

# Manual leftovers only — systemd is already stopped. [l] form avoids pkill self-match.
pkill -f '[l]lama-server' || true
for _ in $(seq 1 20); do
  pgrep -f '[l]lama-server' >/dev/null 2>&1 || break
  sleep 1
done
if pgrep -f '[l]lama-server' >/dev/null 2>&1; then
  echo "llama-server still alive after stop+pkill" >&2
  exit 1
fi

echo "Starting Qwen consult (thinking ON, alias qwen3.8-27b, 262k)..."
: >"$LOG"
nohup "$BIN" \
  -m "$MODEL" \
  -md "$MTP" \
  --spec-type draft-mtp \
  --host 0.0.0.0 --port 8080 \
  -ngl 999 -c 262144 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --jinja \
  --alias qwen3.8-27b \
  >>"$LOG" 2>&1 9>&- &
echo $! >"$PIDFILE"

ok=0
for _ in $(seq 1 180); do
  if curl -sf --max-time 2 "$HEALTH" >/dev/null 2>&1; then
    ok=1
    break
  fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Qwen llama-server died during load; tail of $LOG:" >&2
    tail -40 "$LOG" >&2 || true
    exit 1
  fi
  sleep 2
done
if [ "$ok" != 1 ]; then
  echo "Qwen failed to become healthy within 360s; tail of $LOG:" >&2
  tail -40 "$LOG" >&2 || true
  exit 1
fi

echo "GET /v1/models"
curl -sS --max-time 10 "$MODELS"
echo
