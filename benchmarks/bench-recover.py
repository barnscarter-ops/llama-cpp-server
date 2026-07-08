#!/usr/bin/env python3
"""Turn-2 recovery bench: feed each turn-1 failure back with real feedback.

For each Ornith HumanEval failure in bench-ornith-9b.json:
  - Replay the original user prompt as turn 1
  - Include Ornith's original assistant reply
  - Send a turn-2 user message with concrete feedback:
      * empty-content failures: "you didn't emit a function; try again briefly"
      * wrong-algorithm failures: the actual failing test case

Extract code from turn-2, run the same canonical unit tests, report pass/fail.
"""
from __future__ import annotations
import json, re, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

URL = "http://127.0.0.1:8081/v1/chat/completions"
MODEL = "ornith-9b"
BENCH_JSON = Path(r"C:\Workspace\Infrastructure\llama-cpp-server\bench-ornith-9b.json")
BENCH_SCRIPT = Path(r"C:\Workspace\Infrastructure\llama-cpp-server\bench-coding.py")

# Import HUMANEVAL from bench-coding.py so tests stay in one place.
sys.path.insert(0, str(BENCH_SCRIPT.parent))
from importlib.util import spec_from_file_location, module_from_spec
_spec = spec_from_file_location("bench_coding", BENCH_SCRIPT)
_bc = module_from_spec(_spec); _spec.loader.exec_module(_bc)  # type: ignore
HUMANEVAL_BY_ID = {p["id"]: p for p in _bc.HUMANEVAL}
strip_think = _bc.strip_think
extract_code = _bc.extract_code
run_case = _bc.run_humaneval_case

# Feedback strategy per failure type. Populated after inspecting bench json.
FEEDBACKS = {
    "he-03-truncate": (
        "You didn't emit a function last turn — you spent your whole reasoning budget "
        "in <think> without producing content. Try again, keep <think> brief, and just "
        "give me the `truncate_number` function definition."
    ),
    "he-06-intersperse": (
        "Your function is wrong. Test failure:\n"
        "  intersperse([1, 2, 3], 4) returned [1, 4, 4, 2, 3]\n"
        "  expected: [1, 4, 2, 4, 3]\n"
        "The delimiter should go BETWEEN each pair of consecutive elements, not "
        "clustered at the start. Fix it. Return only the corrected function."
    ),
    "he-08-largest-prime-factor": (
        "You didn't emit a function last turn — you spent your whole reasoning budget "
        "in <think> without producing content. Try again, keep <think> brief, and just "
        "give me the `largest_prime_factor` function definition."
    ),
}


def call(messages: list, max_tokens=4096, timeout=300) -> dict:
    body = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "seed": 42,
        "stream": False,
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    resp["_wall"] = time.time() - t0
    return resp


def main():
    if not BENCH_JSON.exists():
        print(f"Missing {BENCH_JSON}"); sys.exit(2)
    bench = json.loads(BENCH_JSON.read_text(encoding="utf-8"))
    turn1_by_id = {r["id"]: r for r in bench["humaneval"]}

    print("=== Ping ===")
    ping = call([{"role": "user", "content": "ping"}], max_tokens=8, timeout=30)
    print(f"  ok, {ping['_wall']:.2f}s")

    results = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for pid, feedback in FEEDBACKS.items():
            print(f"\n[{pid}]")
            prob = HUMANEVAL_BY_ID[pid]
            t1 = turn1_by_id[pid]
            # If turn-1 content was empty (loop failure), send an empty assistant message.
            # This matches what the server saw as its own output on turn 1.
            assistant_reply = t1["raw"] or ""

            messages = [
                {"role": "system", "content": "You are a careful Python coder. Return only what the user asks for — no prose."},
                {"role": "user",      "content": prob["prompt"]},
                {"role": "assistant", "content": assistant_reply},
                {"role": "user",      "content": feedback},
            ]
            try:
                resp = call(messages)
                msg = resp["choices"][0]["message"]
                raw = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                finish = resp["choices"][0].get("finish_reason")
                code = extract_code(raw)
                ok, err = run_case(code, prob["tests"], tmp)
                results.append(dict(
                    id=pid, pass_=ok, error=err, raw=raw, reasoning=reasoning,
                    code=code, finish_reason=finish, wall=resp["_wall"],
                    usage=resp.get("usage", {}),
                ))
                print(f"  turn-2: {'PASS' if ok else 'FAIL'} "
                      f"({resp['_wall']:.1f}s, {resp.get('usage', {}).get('completion_tokens', '?')} tok, "
                      f"finish={finish})")
                if not ok:
                    print(f"  error: {err[:200]}")
                    print(f"  code (first 300): {code[:300]}")
            except Exception as e:
                results.append(dict(id=pid, pass_=False, error=f"http: {e}"))
                print(f"  ERROR {e}")

    passed = sum(1 for r in results if r.get("pass_"))
    total = len(results)
    print(f"\n=== SUMMARY ===")
    print(f"Ornith turn-2 recovery: {passed}/{total}")
    out = Path(r"C:\Workspace\Infrastructure\llama-cpp-server\bench-ornith-recover.json")
    out.write_text(json.dumps(dict(model=MODEL, results=results, passed=passed, total=total),
                              indent=2, default=str), encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
