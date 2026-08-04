// ecosystem.config.cjs — PM2 process definitions for llama-cpp-server
//
// Register with:  pm2 start ecosystem.config.cjs          (starts paused or running)
// Start:          pm2 start qwen3-llama                  (start if stopped)
// Stop:           pm2 stop qwen3-llama                   (stop without removing)
// Remove:         pm2 delete qwen3-llama                 (remove from PM2 entirely)
//
// TUNED SETTINGS:
//   - Model: DeepSeek-R1-Distill-Qwen-14B-Q6_K (100% GPU offload)
//   - Context: 32k
//   - KV Cache: Q4_0
//   - Flash attention: auto (on)
//   - MTP: not supported by model
//   - Continuous batching: on
//   - Quality bench: benchmarks/bench-deepseek-r1-14b.json
//
// NOTE: This config registers qwen3-llama as STOPPED by default.
//       llama-guardian starts it on demand via port 8080 proxy.
//       Parallel > 1 NOT supported with MTP — single slot only.

module.exports = {
  apps: [
    {
      name: "qwen3-llama",

      // llama-server binary path
      script: "C:\\Workspace\\Infrastructure\\llama-cpp-server\\llama-server.exe",

      // Working directory for relative paths
      cwd: "C:\\Workspace\\Infrastructure\\llama-cpp-server",

      // Arguments passed to llama-server — tuned for 16GB VRAM RTX 4060 Ti
      // User-tuned config: flash-attn ON, ctx 32K, NO MTP
      // Port 8081 loopback only — llama-guardian owns 8080 and proxies to here.
      args: [
        "--model",      "C:\\Workspace\\Infrastructure\\llama-cpp-server\\models\\DeepSeek-R1-Distill-Qwen-14B-Q6_K.gguf",
        "--host",       "127.0.0.1",
        "--port",       "8081",
        "--alias",      "deepseek-r1-14b",
        "--gpu-layers", "99",
        "--ctx-size",   "32768",
        "--parallel",   "1",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
        "--jinja",
        "--batch-size",  "2048",
        "--ubatch-size", "512",
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
