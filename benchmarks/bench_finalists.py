#!/usr/bin/env python3
"""4060 Ti finalist benchmark suite — speed + quality, 2026-08-19.

Phases per model:
  A. llama-bench flag matrix (ubatch x KV type x flash-attn) at -p 2048 -n 128
  B. context ladder via llama-server load test (finds max stable ctx)
  C. quality gate via bench-coding.py (HumanEval subset + tool calls) on the
     tuned config

Usage: python bench_finalists.py --model glm|cohere|gemma|qwen-control
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, os, urllib.request
from pathlib import Path

BUILD = r"C:\Workspace\Infrastructure\llama-cpp-server-cuda-b10488"
ROOT = r"C:\Workspace\Infrastructure\llama-cpp-server"
MODELS = os.path.join(ROOT, "models")
OUTDIR = os.path.join(ROOT, "benchmarks")
PORT = 8099

SPECS = {
    # name: (file, sampler-overrides-for-quality, notes)
    "glm": dict(
        file="GLM-4.7-Flash-UD-IQ3_XXS.gguf",
        extra=["--repeat-penalty", "1.0", "--min-p", "0.01"],
        tool_parser="glm45",
        notes="DEEPSEEK2 arch quirks; repeat-penalty 1.0 mandatory",
    ),
    "cohere": dict(
        file="North-Mini-Code-1.0-UD-IQ3_XXS.gguf",
        extra=[],
        tool_parser="cohere_command4",
        notes="cohere2moe arch; 256k native ctx",
    ),
    "gemma": dict(
        file="gemma-4-26B-A4B-it-UD-IQ4_XS.gguf",
        extra=[],
        tool_parser=None,
        notes="dense attention; IQ4 class quant",
    ),
    "qwen-control": dict(
        file="Qwen3.6-35B-A3B-UD-IQ3_XXS.gguf",
        extra=[],
        tool_parser=None,
        notes="incumbent control; current production config",
    ),
}


def bench_matrix(tag: str, model: str, ctx: int = 16384):
    """Phase A: llama-bench sweep. Returns parsed rows."""
    out = os.path.join(OUTDIR, f"finalist-{tag}-matrix.json")
    cmd = [
        os.path.join(BUILD, "llama-bench.exe"),
        "-m", model,
        "-ngl", "99",
        "-fa", "1",
        "-ctk", "f16,q8_0",
        "-ctv", "f16,q8_0",
        "-ub", "512,1024,2048",
        "-b", "2048",
        "-c", str(ctx),
        "-p", "2048",
        "-n", "128",
        "-r", "2",
        "-o", "json",
    ]
    print("PHASE A:", " ".join(cmd[1:]), flush=True)
    with open(out, "w") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True, timeout=1200)
    if p.returncode != 0:
        print("bench stderr:", p.stderr[-500:])
    rows = json.load(open(out))
    for r in rows:
        print(f"  ctv={r['type_v']:>5} ub={r['n_ubatch']:>5} fa={r['flash_attn']} "
              f"pp{r['n_prompt']}={r['avg_ts']:.0f}" if r["n_prompt"] else
              f"  ctv={r['type_v']:>5} ub={r['n_ubatch']:>5} fa={r['flash_attn']} "
              f"tg{r['n_gen']}={r['avg_ts']:.1f}")
    return rows


def ctx_ladder(tag: str, spec: dict, kv: str = "q8_0", ub: int = 1024):
    """Phase B: find max stable ctx via llama-server health checks."""
    model = os.path.join(MODELS, spec["file"])
    results = {}
    for ctx in (32768, 65536, 98304, 131072):
        cmd = [
            os.path.join(BUILD, "llama-server.exe"),
            "--model", model, "--host", "127.0.0.1", "--port", str(PORT),
            "--gpu-layers", "99", "--ctx-size", str(ctx),
            "--flash-attn", "on", "--cache-type-k", kv, "--cache-type-v", kv,
            "--parallel", "1", "--ubatch-size", str(ub), "--batch-size", "2048",
            "--jinja", "--no-warmup",
        ] + spec["extra"]
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        up = False
        for _ in range(60):
            time.sleep(2)
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2).status == 200:
                    up = True
                    break
            except Exception:
                if p.poll() is not None:
                    break
        vram = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader"],
                              capture_output=True, text=True).stdout.strip()
        results[ctx] = dict(up=up, vram=vram)
        print(f"  ctx {ctx:>7}: {'LOADED' if up else 'FAILED'}  VRAM={vram}", flush=True)
        p.terminate()
        try:
            p.wait(10)
        except Exception:
            p.kill()
        time.sleep(8)
    json.dump(results, open(os.path.join(OUTDIR, f"finalist-{tag}-ctx.json"), "w"), indent=1)
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(SPECS))
    ap.add_argument("--phase", default="A", choices=["A", "B"])
    args = ap.parse_args()
    spec = SPECS[args.model]
    model = os.path.join(MODELS, spec["file"])
    assert os.path.exists(model), f"missing {model}"
    if args.phase == "A":
        bench_matrix(args.model, model)
    else:
        ctx_ladder(args.model, spec)
