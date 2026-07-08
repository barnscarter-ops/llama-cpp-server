#!/usr/bin/env python3
"""Benchmark llama-server: prompt processing + generation speed."""
import json, time, urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"

PROMPTS = [
    ("Short coding", "Write a Python function that checks if a string is a palindrome. Include a docstring."),
    ("Reasoning",    "Explain how a hash map works under the hood, including collision handling."),
    ("Longer gen",   "Write a Python class representing a bank account with deposit, withdraw, and transfer methods. Include type hints and a docstring."),
]

def bench(label, prompt, max_tokens=500):
    body = json.dumps({
        "model": "qwen3-14b",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    wall = time.time() - t0
    u = data["usage"]
    td = data.get("timings", {})
    out = data["choices"][0]["message"]["content"]
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"{'='*60}")
    print(f"Prompt tokens:     {u['prompt_tokens']}")
    print(f"Output tokens:     {u['completion_tokens']}")
    print(f"Prompt eval:       {td.get('prompt_per_second', 0):.1f} tok/s  ({td.get('prompt_ms',0):.0f} ms)")
    print(f"Generation:        {td.get('predicted_per_second', 0):.1f} tok/s  ({td.get('predicted_ms',0):.0f} ms)")
    print(f"Wall clock:        {wall:.2f}s")
    print(f"\n--- Output (first 400 chars) ---")
    print(out[:400])

for label, prompt in PROMPTS:
    try:
        bench(label, prompt)
    except Exception as e:
        print(f"\n{label} FAILED: {e}")
