#!/usr/bin/env python3
"""Isolated tuning sweep for llama-server flags and fast benchmark results.

Tests MTP draft depth, KV cache type, flash attention, and context size.

This program owns port 8081 while it runs. It intentionally refuses to start
unless guardian (:8080) and Qwen (:8081) are already stopped, so it cannot
overlap PM2-managed serving or create an orphan llama-server process.

Usage: python tune-sweep.py --isolated
"""
from __future__ import annotations
import argparse
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(r"C:\Workspace\Infrastructure\llama-cpp-server")
SERVER = ROOT / "llama-server.exe"
MODEL = ROOT / "models" / "Qwen3.6-35B-A3B-UD-IQ2_XXS.gguf"
RESULTS = ROOT / "benchmarks" / "tune-sweep-results.json"
GUARDIAN_PORT = 8080
LLAMA_PORT = 8081

# Fast benchmark prompts — mix of short and long generation
PROMPTS = [
    ("short-code", "Write a Python function `is_prime(n: int) -> bool`. Return only the function."),
    ("medium-code", "Write a Python class `LinkedList` with append, prepend, delete, and __iter__ methods. Include type hints and docstrings."),
    ("long-gen",    "Explain how TCP congestion control works, covering slow start, congestion avoidance, fast retransmit, and fast recovery. Be thorough."),
]

def port_is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def require_isolated_services() -> None:
    active = [
        f"guardian :{GUARDIAN_PORT}" if port_is_listening(GUARDIAN_PORT) else "",
        f"llama-server :{LLAMA_PORT}" if port_is_listening(LLAMA_PORT) else "",
    ]
    active = [service for service in active if service]
    if active:
        raise SystemExit(
            "Refusing live-service benchmark: "
            + ", ".join(active)
            + " is listening. Stop llama-guardian and qwen3-llama through PM2, "
            "verify both ports are closed, then rerun with --isolated."
        )


def start_server(args: list[str]) -> subprocess.Popen:
    require_isolated_services()
    cmd = [
        str(SERVER),
        "--model", str(MODEL),
        "--host", "127.0.0.1", "--port", str(LLAMA_PORT),
        "--alias", "qwen3.6-35b",
        "--gpu-layers", "99",
        "--jinja",
        "--batch-size", "2048", "--ubatch-size", "512",
        "--cont-batching",
        "--reasoning", "off",
    ] + args
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT))
    # Wait for server to be ready
    for _ in range(60):
        time.sleep(2)
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{LLAMA_PORT}/v1/models")
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status == 200:
                    return proc
        except:
            pass
    print("  SERVER FAILED TO START")
    return proc

def stop_server(proc: subprocess.Popen):
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    time.sleep(3)  # Let GPU memory free

def run_bench(label: str, prompt: str, max_tokens=500) -> dict:
    body = json.dumps({
        "model": "qwen3.6-35b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "seed": 42,
        "stream": False,
    }).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{LLAMA_PORT}/v1/chat/completions",
                                  data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    wall = time.time() - t0
    u = data["usage"]
    td = data.get("timings", {})
    comp = u.get("completion_tokens", 0)
    tps = comp / wall if wall > 0 else 0
    return dict(
        label=label,
        prompt_tokens=u.get("prompt_tokens", 0),
        completion_tokens=comp,
        wall=round(wall, 2),
        tok_s=round(tps, 1),
        prompt_per_second=round(td.get("prompt_per_second", 0), 1),
        predicted_per_second=round(td.get("predicted_per_second", 0), 1),
    )

def sweep_config(name: str, args: list[str]) -> dict:
    print(f"\n{'='*60}")
    print(f"CONFIG: {name}")
    print(f"  args: {' '.join(args)}")
    print(f"{'='*60}")

    proc = start_server(args)
    results = []
    try:
        # Quick ping
        try:
            ping = run_bench("ping", "Say hello.", max_tokens=8)
            print(f"  ping: {ping['wall']}s, {ping['tok_s']} tok/s")
        except Exception as e:
            print(f"  PING FAILED: {e}")
            return dict(config=name, args=args, error=str(e), results=[])

        for label, prompt in PROMPTS:
            try:
                r = run_bench(label, prompt)
                results.append(r)
                print(f"  {label:15s}  {r['completion_tokens']:5d} tok  {r['wall']:6.2f}s  {r['tok_s']:6.1f} tok/s")
            except Exception as e:
                results.append(dict(label=label, error=str(e)))
                print(f"  {label:15s}  ERROR: {e}")
    finally:
        stop_server(proc)

    # Calculate averages
    valid = [r for r in results if "tok_s" in r and r["completion_tokens"] > 50]
    avg_tps = sum(r["tok_s"] for r in valid) / len(valid) if valid else 0
    total_tok = sum(r["completion_tokens"] for r in valid)
    total_wall = sum(r["wall"] for r in valid)
    overall_tps = total_tok / total_wall if total_wall > 0 else 0

    summary = dict(
        config=name,
        args=args,
        avg_tok_s=round(avg_tps, 1),
        overall_tok_s=round(overall_tps, 1),
        total_tokens=total_tok,
        total_wall=round(total_wall, 1),
        results=results,
    )
    print(f"  → avg: {avg_tps:.1f} tok/s, overall: {overall_tps:.1f} tok/s")
    return summary

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="confirm guardian and the PM2 Qwen server are stopped before this direct sweep",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS,
        help=f"result file (default: {RESULTS})",
    )
    args = parser.parse_args()
    if not args.isolated:
        parser.error("--isolated is required; this benchmark directly owns :8081")
    return args


def main():
    cli_args = parse_args()
    require_isolated_services()
    configs = [
        # MTP draft depth sweep
        ("mtp-n1",  ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "1"]),
        ("mtp-n2",  ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "2"]),
        ("mtp-n3",  ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]),
        ("mtp-n4",  ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "4"]),
        ("mtp-n5",  ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "5"]),

        # KV cache sweep (at best MTP depth)
        ("kv-q8-n3", ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]),
        ("kv-f16-n3",["--ctx-size", "65536",  "--parallel", "1", "--cache-type-k", "f16",  "--cache-type-v", "f16",  "--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]),

        # Context size sweep
        ("ctx-32k-n3",  ["--ctx-size", "32768",  "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]),
        ("ctx-64k-n3",  ["--ctx-size", "65536",  "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "3"]),

        # Flash attention (if supported)
        ("flash-n3", ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0", "--spec-type", "draft-mtp", "--spec-draft-n-max", "3", "--flash-attn", "auto"]),

        # No MTP (baseline for comparison)
        ("no-mtp",   ["--ctx-size", "131072", "--parallel", "1", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0"]),
    ]

    all_results = []
    for name, server_args in configs:
        result = sweep_config(name, server_args)
        all_results.append(result)
        # Save incrementally
        cli_args.output.write_text(json.dumps(all_results, indent=2, default=str), encoding="utf-8")

    # Final summary table
    print(f"\n{'='*70}")
    print(f"{'CONFIG':20s} {'avg tok/s':>10s} {'overall':>10s} {'total tok':>10s} {'wall':>8s}")
    print(f"{'='*70}")
    for r in sorted(all_results, key=lambda x: x.get("overall_tok_s", 0), reverse=True):
        if "error" in r:
            print(f"{r['config']:20s} ERROR: {r['error'][:40]}")
        else:
            print(f"{r['config']:20s} {r['avg_tok_s']:10.1f} {r['overall_tok_s']:10.1f} {r['total_tokens']:10d} {r['total_wall']:8.1f}s")
    print(f"\nResults saved to {cli_args.output}")

if __name__ == "__main__":
    main()
