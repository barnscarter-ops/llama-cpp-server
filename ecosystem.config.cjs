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
    {
      name: "qwen3-llama",

      // llama-server binary path
      script: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\llama-server.exe",

      // Working directory for relative paths
      cwd: "C:\\Workspace\\Infrastructure\\llama-cpp-server",

      // Arguments passed to llama-server — tuned for 16GB VRAM RTX 4060 Ti
      // Flags from the 2026-08-04 tuning sweep (see BENCH-RESULTS.md addendum)
      // Port 8081 loopback only — llama-guardian owns 8080 and proxies to here.
      args: [
        "--model",      "C:\\Workspace\\Infrastructure\\llama-cpp-server\\models\\Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf",
        "--host",       "127.0.0.1",
        "--port",       "8081",
        "--alias",      "qwen3.6-35b-iq3",
        "--gpu-layers", "99",
        "--ctx-size",   "65536",
        "--parallel",   "1",
        // KV cache: f16 (default — no --cache-type flags). q4_0 KV measured
        // SLOWER at filled ctx (60.4 vs 64.6 t/s) and lower precision.
        "--jinja",
        "--batch-size",  "2048",
        "--ubatch-size", "1024",
        "--cont-batching",
        "--flash-attn",  "auto",
      ],

      // PM2 process management
      exec_mode: "fork",                 // Single instance (not cluster)
      autorestart: true,                 // Restart if it crashes (but starts STOPPED)
      watch: false,
      max_memory_restart: "45G",          // Restart if it leaks past 45GB RAM

      // Crash-loop protection. Without these, a stuck orphan on port 8081
      // (e.g. leftover from a hard kill mid-CUDA-init) would cause PM2 to
      // respawn indefinitely as the guardian's enforcer kept killing the
      // new-but-can't-bind instance.
      min_uptime: "30s",
      max_restarts: 5,
      restart_delay: 3000,
      kill_timeout: 20000,               // llama-server needs time to unwind CUDA on shutdown

      // Log output
      log_date_format: "YYYY-MM-DD HH:mm:ss",
      error_file: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\logs\\qwen3-llama-error.log",
      out_file:    "C:\\Workspace\\Infrastructure\\llama-cpp-server\\logs\\qwen3-llama-out.log",
    },

    // ─────────────────────────────────────────────────────────────────────
    //  qwen3-llama-vulkan — Vulkan-backend twin of qwen3-llama.
    //
    //  Staged 2026-08-04 for the Radeon AI PRO R9700 (32GB) install. Uses the
    //  llama.cpp b10275 Vulkan build in ..\llama-cpp-server-vulkan\ (production
    //  above is the CUDA b9550 build, left untouched for rollback).
    //
    //  Binds the SAME port 8081, so exactly one of the two may run at a time:
    //      pm2 stop qwen3-llama && pm2 start qwen3-llama-vulkan
    //  Rollback is the reverse. The guardian on 8080 is agnostic to which.
    //
    //  BEFORE FIRST START, see R9700-INSTALL.md. Two things must be set:
    //    1. VK_LOADER_DRIVERS_SELECT below — pin to the R9700 by only loading
    //       the AMD ICD. Do not pin by index (GGML_VK_VISIBLE_DEVICES):
    //       enumeration order is not stable across process contexts.
    //    2. --model / --ctx-size — the args below are copied from the CUDA
    //       config and are sized for a 16GB card (IQ3_XXS was forced by VRAM).
    //       On 32GB, requantize to Q4_K_M/Q5_K_M first; that is the real win.
    //
    //  The tuning flags below (ubatch 1024, flash-attn auto, f16 KV) were tuned
    //  for CUDA on a 4060 Ti. Treat them as UNVALIDATED on Vulkan — re-run the
    //  sweep in benchmarks/ before trusting them.
    // ─────────────────────────────────────────────────────────────────────
    {
      name: "qwen3-llama-vulkan",
      script: "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan\\llama-server.exe",
      cwd: "C:\\Workspace\\Infrastructure\\llama-cpp-server-vulkan",

      env: {
        // Only load the AMD Vulkan ICD so the R9700 is always index 0.
        // Do NOT use GGML_VK_VISIBLE_DEVICES with an index: enumeration order
        // (Intel UHD 770 iGPU vs R9700) is not stable across process contexts —
        // under the elevated PM2 daemon the child saw a different order and
        // silently ran on the iGPU/CPU at ~2.6 t/s (2026-08-06). The loader
        // filter is deterministic: the Intel ICD never loads at all.
        VK_LOADER_DRIVERS_SELECT: "*amd*",
      },

      args: [
        "--model",      "C:\\Workspace\\Infrastructure\\llama-cpp-server\\models\\Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf",
        "--host",       "127.0.0.1",
        "--port",       "8081",
        "--alias",      "qwen3.6-35b-iq3",
        "--gpu-layers", "99",
        "--ctx-size",   "65536",
        "--parallel",   "1",
        "--jinja",
        "--batch-size",  "2048",
        "--ubatch-size", "1024",
        "--cont-batching",
        "--flash-attn",  "auto",
      ],

      exec_mode: "fork",
      autorestart: true,
      watch: false,
      max_memory_restart: "45G",

      min_uptime: "30s",
      max_restarts: 5,
      restart_delay: 3000,
      kill_timeout: 20000,

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
