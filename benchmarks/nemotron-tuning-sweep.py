#!/usr/bin/env python3
"""
Nemotron 3.5 Lightning tuning sweep — find the optimal flag combination.
Tests: reasoning mode (on/off/auto), reasoning-budget, KV cache quantization,
and batch/ubatch sizing. All on Vulkan b10362, R9700-safe sequential.
"""

import json
import subprocess
import time
import urllib.request
import urllib.error
import os
from pathlib import Path

VULKAN_DIR = r"C:\Workspace\Infrastructure\llama-cpp-server-vulkan-b10362"
MODEL = r"C:\Workspace\Infrastructure\llama-cpp-server\models\NVIDIA-Nemotron-3.5-Lightning-30B-A3B-Q4_K_M.gguf"
RESULTS_DIR = r"C:\Workspace\Infrastructure\llama-cpp-server\benchmarks"
PORT = 8081
HOST = "127.0.0.1"
COOLDOWN = 65

# ── Flag configs to test ────────────────────────────────────────────────
# Naming: short_id -> full extra flags list
CONFIGS = {
    "baseline_no_reasoning": [
        "--reasoning", "off",
    ],
    "reasoning_auto": [
        # Server default — let template decide
    ],
    "reasoning_on_budget_512": [
        "--reasoning", "on",
        "--reasoning-budget", "512",
    ],
    "reasoning_on_budget_1024": [
        "--reasoning", "on",
        "--reasoning-budget", "1024",
    ],
    "kv_q8_0": [
        "--reasoning", "off",
        "--cache-type-k", "q8_0",
        "--cache-type-v", "q8_0",
    ],
    "ubatch_1024": [
        "--reasoning", "off",
        "--ubatch-size", "1024",
    ],
}

BASE_FLAGS = [
    "--model", MODEL,
    "--host", HOST,
    "--port", str(PORT),
    "--gpu-layers", "99",
    "--ctx-size", "65536",
    "--parallel", "1",
    "--jinja",
    "--batch-size", "2048",
    "--ubatch-size", "512",
    "--flash-attn", "on",
]

PROMPTS = [
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

def start_server(config_name, extra_flags):
    """Start llama-server with given flags."""
    env = dict(os.environ)
    env["VK_LOADER_DRIVERS_SELECT"] = "*amd*"
    exe = str(Path(VULKAN_DIR) / "llama-server.exe")
    cmd = [exe] + BASE_FLAGS + ["--alias", f"nemo-{config_name}"] + extra_flags
    print(f"  Starting: {config_name}")
    print(f"  Extra flags: {' '.join(extra_flags) if extra_flags else '(none)'}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=VULKAN_DIR)
    return proc

def wait_ready(proc, max_wait=120):
    start = time.time()
    while time.time() - start < max_wait:
        if proc.poll() is not None:
            out = proc.stdout.read()[-1500:] if proc.stdout else ""
            return False, f"Exited early:\n{out}"
        try:
            req = urllib.request.Request(f"http://{HOST}:{PORT}/v1/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True, f"Ready in {time.time()-start:.1f}s"
        except:
            pass
        time.sleep(1)
    return False, "Timeout"

def do_completion(prompt, max_tokens):
    body = json.dumps({
        "model": "bench",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:300]}"}
    elapsed = time.time() - start
    usage = result.get("usage", {})
    msg = result.get("choices", [{}])[0].get("message", {})
    content = msg.get("content", "")
    reasoning = msg.get("reasoning_content", "")
    return {
        "elapsed_s": round(elapsed, 2),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "tok_per_s": round(usage.get("completion_tokens", 0) / elapsed, 1) if elapsed > 0 else 0,
        "content_len": len(content),
        "reasoning_len": len(reasoning),
        "content_preview": content[:200],
    }

def kill_server(proc):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except:
        proc.kill()
        proc.wait(timeout=5)
    subprocess.run(["powershell.exe", "-Command", "Stop-Process -Name llama-server -Force -ErrorAction SilentlyContinue"], capture_output=True)
    time.sleep(2)

def main():
    results = {}

    for config_name, extra_flags in CONFIGS.items():
        print(f"\n{'='*60}")
        print(f"CONFIG: {config_name}")
        print(f"{'='*60}")

        proc = start_server(config_name, extra_flags)
        ready, msg = wait_ready(proc)
        if not ready:
            print(f"  FAILED: {msg}")
            results[config_name] = {"error": msg}
            kill_server(proc)
            print(f"  Cooldown {COOLDOWN}s...")
            time.sleep(COOLDOWN)
            continue

        print(f"  {msg}")

        config_results = {"extra_flags": extra_flags, "completions": {}}

        for bp in PROMPTS:
            name = bp["name"]
            print(f"  Testing: {name}...", end="", flush=True)
            r = do_completion(bp["prompt"], bp["max_tokens"])
            config_results["completions"][name] = r
            if "error" in r:
                print(f" ERROR: {r['error'][:60]}")
            else:
                print(f" {r['completion_tokens']}tok {r['elapsed_s']}s = {r['tok_per_s']}t/s (content={r['content_len']}ch reasoning={r['reasoning_len']}ch)")

        results[config_name] = config_results
        kill_server(proc)
        print(f"  Cooldown {COOLDOWN}s...")
        time.sleep(COOLDOWN)

    # ── Save ──
    ts = time.strftime("%Y%m%d-%H%M%S")
    outfile = Path(RESULTS_DIR) / f"nemotron-tuning-sweep-{ts}.json"
    outfile.parent.mkdir(parents=True, exist_ok=True)
    with open(outfile, "w") as f:
        json.dump(results, f, indent=2)

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"SUMMARY — Nemotron 3.5 Lightning Tuning Sweep")
    print(f"{'='*60}")
    print(f"{'Config':<28} {'code_gen':>10} {'tool_call':>10} {'reasoning':>10}")
    print(f"-"*62)
    for cn, cd in results.items():
        comps = cd.get("completions", {})
        vals = []
        for p in ["code_gen", "tool_call_json", "reasoning"]:
            c = comps.get(p, {})
            vals.append(f"{c.get('tok_per_s','ERR')}" if "error" not in c else "ERR")
        print(f"{cn:<28} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10}")

    print(f"\nFull results: {outfile}")

if __name__ == "__main__":
    main()
