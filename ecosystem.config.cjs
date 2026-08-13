// ecosystem.config.cjs — PM2 process definitions for llama-cpp-server
//
// Register with:  pm2 start ecosystem.config.cjs          (starts paused or running)
// Start:          pm2 start qwen3-llama                  (start if stopped)
// Stop:           pm2 stop qwen3-llama                   (stop without removing)
// Remove:         pm2 delete qwen3-llama                 (remove from PM2 entirely)
//
// TUNED SETTINGS:
//   - Model: Qwen3.6-35B-A3B-UD-IQ3_XXS (100% GPU offload; MoE, 3B active)
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
// NOTE: This config registers qwen3-llama as STOPPED by default.
//       llama-guardian starts it on demand via port 8080 proxy.
//       Parallel kept at 1 — single slot, matches bench config.

module.exports = {
  apps: [
    // qwen3-llama (CUDA/4060 Ti entry) removed 2026-08-11: its IQ3_XXS model
    // was deleted along with every other quant — Q4_K_M below is deliberately
    // the only model on disk. The CUDA b9550 build itself is still in this
    // directory if a rollback ever needs it (it would need a model re-download).

    // ─────────────────────────────────────────────────────────────────────
    //  qwen3-llama-vulkan — Vulkan backend on the R9700 (32GB).
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
      name: "qwen3-llama-vulkan",
      // Wrapper, not the exe — same Windows kill-reliability fix as qwen3-llama
      // above (this entry is the one that hit the 687-restart loop 2026-08-07).
      script: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\launch-llama.cjs",
      cwd: "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan-b10362",

      env: {
        // Only load the AMD Vulkan ICD so the R9700 is always index 0.
        VK_LOADER_DRIVERS_SELECT: "*amd*",
      },

      args: [
        // Nemotron 3.5 Lightning (2026-08-12): +14% tg, +53% code gen vs Qwen.
        // --reasoning off is CRITICAL: Nemotron auto-detects thinking mode and
        // burns 1500+ hidden tokens per request without it. Alias stays
        // "local-llm" so guardian (GUARDIAN_QUEUE_MODEL) and qwen-submit work
        // unchanged — the swap is transparent downstream.
        "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan-b10362\\llama-server.exe",
        "--model",      "C:\\Workspace\\Infrastructure\\llama-cpp-server\\models\\NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q4_K_M.gguf",
        "--host",       "127.0.0.1",
        "--port",       "8081",
        "--alias",      "local-llm",
        "--gpu-layers", "99",
        "--ctx-size",   "65536",
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
      error_file: "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan\\logs\\qwen3-llama-vulkan-error.log",
      out_file:   "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan\\logs\\qwen3-llama-vulkan-out.log",
    },

    // ─────────────────────────────────────────────────────────────────────
    //  llama-guardian — on-demand lifecycle proxy for llama-server.
    //
    //  Owns port 8080 (where MCC connects). Proxies to llama on
    //  internal port 8081. Pre-warms llama when MCC boots, and
    //  stops llama after 30 min idle. See llama-guardian.py for full docs.
    //
    //  This app ALWAYS auto-starts (~40MB RAM). It controls qwen3-llama's
    //  lifecycle — qwen3-llama itself stays stopped until the guardian or a
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
