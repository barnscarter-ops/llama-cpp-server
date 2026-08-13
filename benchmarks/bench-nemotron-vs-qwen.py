#!/usr/bin/env python3
"""
Nemotron 3.5 Lightning vs Qwen3.6-35B A/B benchmark.
Sequential, R9700-safe (one model at a time, 60s cooldown between).

Uses llama-bench for raw throughput (pp512, tg128) and the OpenAI-compatible
API for real agent-style completion tests (tool calling, code gen).

Run from an elevated context after stopping prod via PM2.
"""

import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────

VULKAN_DIR = r"C:\Workspace\Infrastructure\llama-cpp-server-vulkan-b10362"
MODELS_DIR = r"C:\Workspace\Infrastructure\llama-cpp-server\models"
RESULTS_DIR = r"C:\Workspace\Infrastructure\llama-cpp-server\benchmarks"

MODELS = {
    "qwen3.6-35b": {
        "path": str(Path(MODELS_DIR) / "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"),
        "alias": "qwen3.6-35b",
    },
    "nemotron-3.5-lightning": {
        "path": str(Path(MODELS_DIR) / "NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q4_K_M.gguf"),
        "alias": "nemotron-3.5-lightning",
    },
}

# llama-server settings (matching prod config for fair comparison)
CTX_SIZE = 65536
BATCH = 2048
UBATCH = 512
GPU_LAYERS = 99
PORT = 8081
HOST = "127.0.0.1"

# Cooldown between models (R9700 TDR guard — must be >=60s)
COOLDOWN_S = 70

# ── Helpers ─────────────────────────────────────────────────────────────

def run(cmd, timeout=120, env_extra=None):
    """Run a command, return stdout."""
    env = None
    if env_extra:
        env = dict(os.environ)
        env.update(env_extra)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

def start_server(model_path, alias):
    """Start llama-server with the given model. Returns Popen handle."""
    import os
    env = dict(os.environ)
    env["VK_LOADER_DRIVERS_SELECT"] = "*amd*"

    exe = str(Path(VULKAN_DIR) / "llama-server.exe")
    cmd = [
        exe,
        "--model", model_path,
        "--host", HOST,
        "--port", str(PORT),
        "--alias", alias,
        "--gpu-layers", str(GPU_LAYERS),
        "--ctx-size", str(CTX_SIZE),
        "--parallel", "1",
        "--jinja",
        "--batch-size", str(BATCH),
        "--ubatch-size", str(UBATCH),
        "--cont-batching",
        "--flash-attn", "on",
    ]
    print(f"  Starting server: {alias}")
    print(f"  Binary: {exe}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        cwd=VULKAN_DIR,
    )
    return proc

def wait_for_ready(proc, max_wait=120):
    """Wait for /v1/health to return HTTP 200."""
    start = time.time()
    while time.time() - start < max_wait:
        # Check if process died
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            return False, f"Process exited early (rc={proc.returncode}):\n{out[-2000:]}"
        try:
            req = urllib.request.Request(f"http://{HOST}:{PORT}/v1/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True, f"Ready in {time.time()-start:.1f}s"
        except (urllib.error.URLError, ConnectionRefusedError):
            pass
        time.sleep(1)
    return False, f"Timeout after {max_wait}s"

def stop_server(proc):
    """Kill the server process tree."""
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    # Also force-kill by name (Windows kill reliability)
    subprocess.run(
        ["powershell.exe", "-Command",
         "Stop-Process -Name llama-server -Force -ErrorAction SilentlyContinue"],
        capture_output=True
    )
    time.sleep(2)

def run_llama_bench(model_path):
    """Run llama-bench pp512 and tg128, 3 reps each."""
    import os
    env = dict(os.environ)
    env["VK_LOADER_DRIVERS_SELECT"] = "*amd*"

    exe = str(Path(VULKAN_DIR) / "llama-bench.exe")
    cmd = [
        exe,
        "--model", model_path,
        "--n-gpu-layers", str(GPU_LAYERS),
        "--repeats", "3",
        "--prompt", "512",
        "--gen", "128",
        "--flash-attn", "on",
    ]
    print(f"  Running llama-bench (pp512 x3, tg128 x3)...")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, cwd=VULKAN_DIR)
    return r.stdout.strip()

def run_completion(prompt, max_tokens=4096, enable_thinking=False):
    """Send a chat completion and return timing + content."""
    body = {
        "model": "bench",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    if not enable_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
    )

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:500]}"}
    elapsed = time.time() - start

    usage = result.get("usage", {})
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "tok_per_s": round(usage.get("completion_tokens", 0) / elapsed, 1) if elapsed > 0 else 0,
        "content_preview": content[:300],
        "content_len": len(content),
    }

# ── Benchmark prompts ───────────────────────────────────────────────────

BENCH_PROMPTS = [
    {
        "name": "code_gen",
        "prompt": "Write a Python function that takes a list of dictionaries and returns a new dictionary keyed by a 'category' field, where each value is the list of items in that category sorted by 'priority' descending. Include type hints, docstring, and 3 assert-based test cases.",
        "max_tokens": 8192,
    },
    {
        "name": "tool_call_json",
        "prompt": "You are a helpful assistant with access to tools. Given the user request 'What's the weather in Dallas, TX?', respond with a JSON tool call. Use this format: {\"tool\": \"get_weather\", \"arguments\": {\"location\": \"...\", \"units\": \"...\"}}. Respond with ONLY the JSON, nothing else.",
        "max_tokens": 2048,
    },
    {
        "name": "reasoning",
        "prompt": "A system has 3 services: A, B, C. A depends on B and C. B depends on C. If C takes 200ms to respond, B takes 100ms + C's time, and A takes 50ms + B's time + C's time. What is the total latency for a request to A? Explain your reasoning step by step, then give the final answer.",
        "max_tokens": 4096,
    },
]

# ── Main ────────────────────────────────────────────────────────────────

def main():
    import os
    results = {}

    for model_name, model_cfg in MODELS.items():
        print(f"\n{'='*60}")
        print(f"BENCHMARKING: {model_name}")
        print(f"{'='*60}")

        model_results = {"config": model_cfg}

        # ── llama-bench (raw throughput) ──
        print(f"\n[1/2] llama-bench raw throughput...")
        bench_output = run_llama_bench(model_cfg["path"])
        model_results["llama_bench"] = bench_output
        print(f"  Done.")

        # ── Cooldown before starting server for completions ──
        print(f"\n  Cooling down {COOLDOWN_S}s before completion tests...")
        time.sleep(COOLDOWN_S)

        # ── Completion tests via API ──
        print(f"\n[2/2] Agent-style completion tests...")
        proc = start_server(model_cfg["path"], model_cfg["alias"])

        ready, msg = wait_for_ready(proc, max_wait=120)
        if not ready:
            print(f"  FAILED to start server: {msg}")
            model_results["completion_error"] = msg
            results[model_name] = model_results
            stop_server(proc)
            print(f"\n  Cooling down {COOLDOWN_S}s...")
            time.sleep(COOLDOWN_S)
            continue

        print(f"  {msg}")

        completion_results = {}
        for bp in BENCH_PROMPTS:
            print(f"  Running: {bp['name']}...")
            r = run_completion(bp["prompt"], max_tokens=bp["max_tokens"], enable_thinking=False)
            completion_results[bp["name"]] = r
            if "error" in r:
                print(f"    ERROR: {r['error'][:100]}")
            else:
                print(f"    {r['completion_tokens']} tokens in {r['elapsed_s']}s = {r['tok_per_s']} tok/s")

        model_results["completions"] = completion_results
        results[model_name] = model_results

        # ── Stop server, cooldown ──
        print(f"\n  Stopping server...")
        stop_server(proc)
        print(f"  Cooling down {COOLDOWN_S}s (R9700 TDR guard)...")
        time.sleep(COOLDOWN_S)

    # ── Save results ──
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    results_file = Path(RESULTS_DIR) / f"bench-nemotron-vs-qwen-{timestamp}.json"
    results_file.parent.mkdir(parents=True, exist_ok=True)

    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}")
    print(f"RESULTS SAVED: {results_file}")
    print(f"{'='*60}")

    # ── Summary table ──
    print(f"\nSUMMARY")
    print(f"{'Metric':<30} {'Qwen3.6-35B':>20} {'Nemotron-3.5':>20}")
    print(f"{'-'*70}")

    for model_name, model_data in results.items():
        # Parse bench output for last pp512 and tg128 numbers
        bench_lines = model_data.get("llama_bench", "").split("\n")
        for line in bench_lines:
            if "pp512" in line:
                parts = line.split()
                if parts:
                    val = parts[-1]
                    model_data.setdefault("_bench_pp", val)
            if "tg128" in line:
                parts = line.split()
                if parts:
                    val = parts[-1]
                    model_data.setdefault("_bench_tg", val)

    qwen = results.get("qwen3.6-35b", {})
    nemo = results.get("nemotron-3.5-lightning", {})

    print(f"{'pp512 (t/s)':<30} {qwen.get('_bench_pp','N/A'):>20} {nemo.get('_bench_pp','N/A'):>20}")
    print(f"{'tg128 (t/s)':<30} {qwen.get('_bench_tg','N/A'):>20} {nemo.get('_bench_tg','N/A'):>20}")

    for bp in BENCH_PROMPTS:
        name = bp["name"]
        qc = qwen.get("completions", {}).get(name, {})
        nc = nemo.get("completions", {}).get(name, {})
        q_tps = qc.get("tok_per_s", "N/A")
        n_tps = nc.get("tok_per_s", "N/A")
        print(f"{name + ' (tok/s)':<30} {str(q_tps):>20} {str(n_tps):>20}")

    print(f"\nFull details: {results_file}")

if __name__ == "__main__":
    import os
    main()
