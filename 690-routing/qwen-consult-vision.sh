#!/bin/bash
# One-off (Carter-approved 2026-08-29): Qwen consult + vision via --mmproj.
# Identical to swap-qwen-consult.sh except --mmproj is added.
set -euo pipefail
LOG=/opt/llama/qwen-consult-vision.log
: >"$LOG"
/opt/llama/src/build/bin/llama-server \
  -m /opt/llama/models/Qwen3.8-27B-UD-Q6_K.gguf \
  -md /opt/llama/models/mtp-Qwen3.8-27B-Q4_0.gguf \
  --spec-type draft-mtp \
  --mmproj /opt/llama/models/mmproj-Qwen3.8-27B-F16.gguf \
  --host 0.0.0.0 --port 8080 \
  -ngl 999 -c 262144 \
  -fa on -ctk q8_0 -ctv q8_0 \
  --jinja \
  --alias qwen3.8-27b \
  >>"$LOG" 2>&1
