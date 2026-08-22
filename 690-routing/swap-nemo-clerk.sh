#!/bin/bash
# Stop manual Qwen consult and start systemd Nemotron clerk on :8080.
# Reasoning off is in the unit — do not launch Nemotron by hand here.
# Usage: /opt/llama/swap-nemo-clerk.sh
set -euo pipefail

LOCK=/opt/llama/swap.lock
PIDFILE=/opt/llama/qwen-consult.pid
HEALTH=http://127.0.0.1:8080/health
MODELS=http://127.0.0.1:8080/v1/models

mkdir -p /opt/llama
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "swap in progress (lock $LOCK); refusing" >&2
  exit 1
fi

if systemctl is-active --quiet llama-server; then
  echo "systemd llama-server already active; not pkill'ing the clerk"
  echo "GET /v1/models"
  curl -sS --max-time 10 "$MODELS"
  echo
  exit 0
fi

echo "Stopping manual llama-server (Qwen consult)..."
# Only reached when systemd is inactive. [l] form avoids pkill self-match.
pkill -f '[l]lama-server' || true
for _ in $(seq 1 30); do
  pgrep -f '[l]lama-server' >/dev/null 2>&1 || break
  sleep 1
done
if pgrep -f '[l]lama-server' >/dev/null 2>&1; then
  echo "manual llama-server still alive after pkill" >&2
  exit 1
fi
rm -f "$PIDFILE"

echo "Starting systemd llama-server (Nemotron clerk, --reasoning off)..."
systemctl start llama-server

ok=0
for _ in $(seq 1 180); do
  if curl -sf --max-time 2 "$HEALTH" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done
if [ "$ok" != 1 ]; then
  echo "Nemotron failed to become healthy within 360s" >&2
  systemctl status llama-server --no-pager >&2 || true
  exit 1
fi

echo "GET /v1/models"
curl -sS --max-time 10 "$MODELS"
echo
