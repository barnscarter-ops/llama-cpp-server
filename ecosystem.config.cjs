// ecosystem.config.cjs — PM2 process definitions for llama-cpp-server
//
// Register with:  pm2 start ecosystem.config.cjs          (starts paused or running)
// Start:          pm2 start local-llm                    (start if stopped)
// Stop:           pm2 stop local-llm                     (stop without removing)
// Remove:         pm2 delete local-llm                   (remove from PM2 entirely)
//
// TUNED SETTINGS:
//   - Model: NVIDIA Nemotron 3.5 Lightning 30B-A3B Q4_K_M (100% GPU offload)
//   - Context: 64k (15.26 GB peak VRAM at filled ctx — tuned sweep 2026-08-04)
//   - KV Cache: F16 (faster AND higher quality than q4_0 on this model; KV is
//     tiny thanks to GQA, so f16 fits even at 64k)
//   - Flash attention: auto (on) — REQUIRED: off costs ~20% tg and +1.3 GB VRAM
//   - ubatch 1024: +24% prefill vs 512 (2.3k t/s at 32k ctx)
//   - Speculative/MTP: none — model has no MTP tensors; ngram spec tested at
//     8% acceptance (net slower). Do not enable.
//   - Continuous batching: on
//   - Native tool calling: yes (3/3 on bench; prior DeepSeek model had none)
//   - Quality bench: benchmarks/bench-qwen36-iq3.json + bench-qwen36-iq3-tuned.json
//     (humaneval is seed-noisy ±2 on this model; fail mode is reasoning loops
//     that never terminate — the guardian auto-retries non-streaming
//     finish_reason=length responses with a nudged seed, see llama-guardian.py)
//
// NOTE: This config registers local-llm as STOPPED by default.
//       llama-guardian starts it on demand via port 8080 proxy.
//       Parallel kept at 1 — single slot, matches bench config.

module.exports = {
  apps: [
    // The former CUDA/4060 Ti entry was removed 2026-08-11: its IQ3_XXS model
    // was deleted along with every other quant — Q4_K_M below is deliberately
    // the only model on disk. The CUDA b9550 build itself is still in this
    // directory if a rollback ever needs it (it would need a model re-download).

    // ─────────────────────────────────────────────────────────────────────
    //  local-llm — Vulkan backend on the R9700 (32GB).
    //
    //  MODEL CUTOVER 2026-08-12: Qwen3.6-35B → Nemotron 3.5 Lightning 30B-A3B.
    //  Nemotron wins on every metric (R9700 Vulkan, b10362 build, tuned sweep):
    //    - tg128: 152 t/s vs 133 t/s (+14%)
    //    - code_gen: 138 t/s vs 90 t/s (+53% with --reasoning off)
    //    - reasoning: 136 t/s vs 121 t/s (+12%)
    //    - Same tool-call JSON correctness, more concise output
    //  MTP tested and REJECTED on Vulkan: 90 t/s with spec vs 152 t/s without
    //  (draft forward-pass overhead dominates; 66% acceptance can't overcome it).
    //  KV q8_0, ubatch 1024 also tested — no gain or slight regression.
    //  See: benchmarks/nemotron-tuning-sweep-*.json + bench-nemotron-vs-qwen-*.json
    //
    //  ROLLBACK: change --model back to Qwen3.6-35B-A3B-UD-Q4_K_M.gguf, remove
    //  --reasoning off, and point cwd/exe back to llama-cpp-server-vulkan (b10275).
    //  The Qwen GGUF is NOT deleted — it stays on disk for instant rollback.
    //
    //  Build: b10362 (4801e3c56) required for nemotron_h_moe architecture.
    //  Previous b10275 build in ..\llama-cpp-server-vulkan\ is the rollback.
    // ─────────────────────────────────────────────────────────────────────
    {
      name: "local-llm",
      // Wrapper, not the exe — Windows kill-reliability fix for llama-server
      // above (this entry is the one that hit the 687-restart loop 2026-08-07).
      script: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\launch-llama.cjs",
      cwd: "C:\\Workspace\\Infrastructure\\llama-cpp-server-cuda-b10488",

      // ── X870E PROFILE (2026-08-18): CUDA on the 4060 Ti 16GB ─────────
      // The R9700 (and its Vulkan build + Nemotron pairing) moved to the
      // AIWA candidate with the transplant. Carter's directive 2026-08-18:
      // NEVER run Nemotron on this machine — its Q4_K_M is 23.7 GB and can
      // only partial-offload here. This box runs Qwen3.8-27B UD-IQ3_XXS
      // (11.9 GB, released 2026-08-14, dense, vision-capable) fully
      // offloaded on the CUDA b10488 build. The Vulkan b10362 dir and the
      // Nemotron GGUF stay on disk untouched — they are the future AIWA's.
      env: {},

      args: [
        // Qwen3.8-27B UD-IQ3_XXS: newest Qwen dense, full 99-layer offload
        // on the 4060 Ti (~12 GB weights + KV in 16 GB). Alias stays
        // "local-llm" so guardian (GUARDIAN_QUEUE_MODEL) and qwen-submit
        // work unchanged — the swap is transparent downstream.
        "C:\\Workspace\\Infrastructure\\llama-cpp-server-cuda-b10488\\llama-server.exe",
        "--model",      "C:\\Workspace\\Infrastructure\\llama-cpp-server\\models\\Qwen3.8-27B-UD-IQ3_XXS.gguf",
        "--host",       "127.0.0.1",
        "--port",       "8081",
        "--alias",      "local-llm",
        "--gpu-layers", "99",
        "--ctx-size",   "32768",

        "--parallel",   "1",
        "--jinja",
        "--batch-size",  "2048",
        "--ubatch-size", "512",
        "--cont-batching",
        "--flash-attn",  "on",
        "--reasoning",   "off",
      ],

      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_memory_restart: "45G",

      min_uptime: "30s",
      max_restarts: 5,
      restart_delay: 3000,
      kill_timeout: 20000,
      shutdown_with_message: true,       // wrapper listens for PM2's IPC 'shutdown' and tree-kills the exe

      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan\\logs\\local-llm-error.log",
      out_file:   "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan\\logs\\local-llm-out.log",
    },

    // ─────────────────────────────────────────────────────────────────────
    //  llama-guardian — on-demand lifecycle proxy for llama-server.
    //
    //  Owns port 8080 (where MCC connects). Proxies to llama on
    //  internal port 8081. Pre-warms llama when MCC boots, and
    //  stops llama after 30 min idle. See llama-guardian.py for full docs.
    //
    //  This app ALWAYS auto-starts (~40MB RAM). It controls local-llm's
    //  lifecycle — local-llm itself stays stopped until the guardian or a
    //  user starts it.
    // ─────────────────────────────────────────────────────────────────────
    {
      name: "llama-guardian",

      // Run the guardian with the system Python (has aiohttp installed).
      script: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\qwen-queue\\llama-guardian.py",
      interpreter: "python",
      cwd: "C:\\Workspace\\Infrastructure\\llama-cpp-server",
      env: {
        HERMES_DECIDER_MODEL: "glm-5.2",
        HERMES_DECIDER_PROVIDER: "custom:zai-coding",
      },

      exec_mode: "fork",
      autorestart: true,                 // Guardian must always be up — it's the gateway
      watch: false,
      max_memory_restart: "500M",

      // Crash-loop protection
      min_uptime: "10s",
      max_restarts: 5,
      restart_delay: 5000,

      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\logs\\llama-guardian-error.log",
      out_file:    "C:\\Workspace\\Infrastructure\\llama-cpp-server\\logs\\llama-guardian-out.log",
    },
  ],
};
