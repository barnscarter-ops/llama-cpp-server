#!/usr/bin/env python3
"""Coding-quality benchmark: HumanEval subset + workflow prompts + tool calling.

Usage: python bench-coding.py --model ornith-9b --out bench-ornith-9b.json
"""
from __future__ import annotations
import argparse, json, re, subprocess, sys, tempfile, time, urllib.request, os
from pathlib import Path

URL_DEFAULT = "http://127.0.0.1:8081/v1/chat/completions"

# ---------------------------------------------------------------------------
# HumanEval subset (10 problems). Each: prompt to model + canonical tests.
# Tests are run against the model's function in an isolated subprocess with a
# 10-second wall-clock cap. Extraction picks the last ```python ... ``` block if
# present, otherwise the full stripped output.
# ---------------------------------------------------------------------------
HUMANEVAL = [
    dict(
        id="he-01-two-sum",
        prompt=(
            "Write a Python function `has_close_elements(numbers: list[float], threshold: float) -> bool`\n"
            "that returns True if any two distinct numbers in the list are closer than `threshold` to each other.\n"
            "Return only the function definition. No prose, no examples, no markdown fences."
        ),
        tests="""
assert has_close_elements([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3) == True
assert has_close_elements([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.05) == False
assert has_close_elements([1.0, 2.0, 5.9, 4.0, 5.0], 0.95) == True
assert has_close_elements([1.0, 2.0, 5.9, 4.0, 5.0], 0.8) == False
assert has_close_elements([1.0, 2.0, 3.0, 4.0, 5.0, 2.0], 0.1) == True
assert has_close_elements([1.1, 2.2, 3.1, 4.1, 5.1], 1.0) == True
assert has_close_elements([1.1, 2.2, 3.1, 4.1, 5.1], 0.5) == False
""",
    ),
    dict(
        id="he-02-parens",
        prompt=(
            "Write a Python function `separate_paren_groups(paren_string: str) -> list[str]`.\n"
            "The input is a string of multiple groups of balanced parentheses, possibly with spaces.\n"
            "Return a list of the balanced groups as strings, ignoring whitespace. Groups are not nested inside each other.\n"
            "Return only the function definition."
        ),
        tests="""
assert separate_paren_groups('(()()) ((())) () ((())()())') == ['(()())', '((()))', '()', '((())()())']
assert separate_paren_groups('() (()) ((())) (((())))') == ['()', '(())', '((()))', '(((())))']
assert separate_paren_groups('(()(())((())))') == ['(()(())((())))']
assert separate_paren_groups('( ) (( )) (( )( ))') == ['()', '(())', '(()())']
""",
    ),
    dict(
        id="he-03-truncate",
        prompt=(
            "Write a Python function `truncate_number(number: float) -> float` that returns\n"
            "the decimal part of the number (i.e., the number minus its integer floor for positive numbers).\n"
            "Return only the function definition."
        ),
        tests="""
assert truncate_number(3.5) == 0.5
assert abs(truncate_number(1.33) - 0.33) < 1e-6
assert abs(truncate_number(123.456) - 0.456) < 1e-6
""",
    ),
    dict(
        id="he-04-below-zero",
        prompt=(
            "Write a Python function `below_zero(operations: list[int]) -> bool` that returns True\n"
            "if a running balance starting at 0 ever goes below zero when applying operations in order.\n"
            "Return only the function definition."
        ),
        tests="""
assert below_zero([]) == False
assert below_zero([1, 2, 3]) == False
assert below_zero([1, 2, -4, 5, 6]) == True
assert below_zero([1, -1, 2, -3]) == True
assert below_zero([3, -2, -1]) == False
""",
    ),
    dict(
        id="he-05-mean-abs-dev",
        prompt=(
            "Write a Python function `mean_absolute_deviation(numbers: list[float]) -> float` that\n"
            "returns the mean absolute deviation from the arithmetic mean of the list.\n"
            "Return only the function definition."
        ),
        tests="""
assert abs(mean_absolute_deviation([1.0, 2.0, 3.0, 4.0]) - 1.0) < 1e-6
assert abs(mean_absolute_deviation([1.0, 2.0, 3.0, 4.0, 5.0]) - 1.2) < 1e-6
assert abs(mean_absolute_deviation([10.0, 10.0, 10.0]) - 0.0) < 1e-9
""",
    ),
    dict(
        id="he-06-intersperse",
        prompt=(
            "Write a Python function `intersperse(numbers: list[int], delimiter: int) -> list[int]`\n"
            "that inserts `delimiter` between every two consecutive elements of `numbers`. Empty input yields [].\n"
            "Return only the function definition."
        ),
        tests="""
assert intersperse([], 4) == []
assert intersperse([1, 2, 3], 4) == [1, 4, 2, 4, 3]
assert intersperse([5], 9) == [5]
assert intersperse([1, 1, 1], 0) == [1, 0, 1, 0, 1]
""",
    ),
    dict(
        id="he-07-nested-depth",
        prompt=(
            "Write a Python function `parse_nested_parens(paren_string: str) -> list[int]` that,\n"
            "given a space-separated list of balanced parenthesis groups, returns a list of the deepest\n"
            "nesting level for each group.\n"
            "Return only the function definition."
        ),
        tests="""
assert parse_nested_parens('(()()) ((())) () ((())()())') == [2, 3, 1, 3]
assert parse_nested_parens('() (()) ((())) (((())))') == [1, 2, 3, 4]
assert parse_nested_parens('(()(())((())))') == [4]
""",
    ),
    dict(
        id="he-08-largest-prime-factor",
        prompt=(
            "Write a Python function `largest_prime_factor(n: int) -> int` that returns the largest\n"
            "prime factor of n. Assume n > 1 and has no prime factor larger than n itself.\n"
            "Return only the function definition."
        ),
        tests="""
assert largest_prime_factor(15) == 5
assert largest_prime_factor(27) == 3
assert largest_prime_factor(63) == 7
assert largest_prime_factor(330) == 11
assert largest_prime_factor(13195) == 29
""",
    ),
    dict(
        id="he-09-fib-mod-30",
        prompt=(
            "Write a Python function `fib_mod(n: int, m: int) -> int` that returns the n-th Fibonacci\n"
            "number modulo m. F(0)=0, F(1)=1. Must handle n up to 10**9 efficiently (use fast doubling\n"
            "or matrix exponentiation).\n"
            "Return only the function definition."
        ),
        tests="""
assert fib_mod(0, 1000) == 0
assert fib_mod(1, 1000) == 1
assert fib_mod(10, 1000) == 55
assert fib_mod(50, 10**9) == 12586269025 % (10**9)
assert fib_mod(10**6, 10**9 + 7) == 918091266  # verified
""",
    ),
    dict(
        id="he-10-longest-common-prefix",
        prompt=(
            "Write a Python function `longest_common_prefix(strs: list[str]) -> str` that returns\n"
            "the longest string that is a prefix of every string in the list. Empty list returns ''.\n"
            "Return only the function definition."
        ),
        tests="""
assert longest_common_prefix([]) == ''
assert longest_common_prefix(['a']) == 'a'
assert longest_common_prefix(['flower', 'flow', 'flight']) == 'fl'
assert longest_common_prefix(['dog', 'racecar', 'car']) == ''
assert longest_common_prefix(['abc', 'abc', 'abc']) == 'abc'
assert longest_common_prefix(['prefix', 'preface', 'prevent']) == 'pre'
""",
    ),
]

# ---------------------------------------------------------------------------
# Workflow prompts. Judged by the operator after the run.
# ---------------------------------------------------------------------------
WORKFLOW_CODE = [
    dict(
        id="wf-01-powershell-spaces",
        title="Fix PowerShell path-with-spaces bug",
        prompt=(
            "This PowerShell function fails when the file path contains spaces. Fix it. "
            "Return ONLY the corrected function, no explanation.\n\n"
            "```powershell\n"
            "function Get-FileHashSafe {\n"
            "    param([string]$Path)\n"
            "    $result = cmd.exe /c certutil -hashfile $Path SHA256\n"
            "    return $result[1].Trim()\n"
            "}\n"
            "```"
        ),
    ),
    dict(
        id="wf-02-refactor",
        title="Refactor a tangled Python function",
        prompt=(
            "Refactor this function to be clearer and shorter. Preserve behavior exactly. "
            "Return only the refactored function.\n\n"
            "```python\n"
            "def process(data):\n"
            "    result = []\n"
            "    for i in range(len(data)):\n"
            "        if data[i] is not None:\n"
            "            if isinstance(data[i], str):\n"
            "                if len(data[i]) > 0:\n"
            "                    result.append(data[i].strip().lower())\n"
            "            elif isinstance(data[i], int) or isinstance(data[i], float):\n"
            "                if data[i] > 0:\n"
            "                    result.append(str(data[i]))\n"
            "    return result\n"
            "```"
        ),
    ),
    dict(
        id="wf-03-pytest",
        title="Write pytest tests for a function",
        prompt=(
            "Write pytest tests for the following function. Cover happy paths, edge cases, and error cases. "
            "Return only the test code.\n\n"
            "```python\n"
            "def parse_duration(s: str) -> int:\n"
            "    '''Parse '1h30m' style strings into seconds. Supports h, m, s. Raises ValueError on invalid.'''\n"
            "    import re\n"
            "    matches = re.findall(r'(\\d+)([hms])', s)\n"
            "    if not matches:\n"
            "        raise ValueError(f'invalid duration: {s!r}')\n"
            "    mult = dict(h=3600, m=60, s=1)\n"
            "    return sum(int(n) * mult[u] for n, u in matches)\n"
            "```"
        ),
    ),
]

WORKFLOW_DEBUG = [
    dict(
        id="wf-04-stack-trace",
        title="Identify the bug from stack trace",
        prompt=(
            "Given this code and stack trace, identify the bug in one sentence and provide the one-line fix.\n\n"
            "```python\n"
            "def merge_configs(default: dict, override: dict) -> dict:\n"
            "    result = default\n"
            "    for k, v in override.items():\n"
            "        if isinstance(v, dict) and k in result:\n"
            "            result[k] = merge_configs(result[k], v)\n"
            "        else:\n"
            "            result[k] = v\n"
            "    return result\n"
            "```\n\n"
            "Stack trace when called a second time:\n"
            "```\n"
            "Test: default was mutated by previous merge_configs call.\n"
            "  first call:  merge_configs({'a': 1}, {'b': 2}) -> {'a': 1, 'b': 2}   OK\n"
            "  second call: merge_configs({'a': 1}, {'c': 3}) -> {'a': 1, 'b': 2, 'c': 3}   FAIL\n"
            "  expected: {'a': 1, 'c': 3}\n"
            "```"
        ),
    ),
    dict(
        id="wf-05-concurrency",
        title="Concurrency: what's wrong",
        prompt=(
            "Explain what is wrong with this code in 2-3 sentences, then show the fix.\n\n"
            "```python\n"
            "import threading\n"
            "counter = 0\n"
            "def worker():\n"
            "    global counter\n"
            "    for _ in range(100_000):\n"
            "        counter += 1\n"
            "threads = [threading.Thread(target=worker) for _ in range(10)]\n"
            "for t in threads: t.start()\n"
            "for t in threads: t.join()\n"
            "assert counter == 1_000_000  # sometimes fails\n"
            "```"
        ),
    ),
]

# Tool-calling: models are given tool definitions, we check what they emit.
TOOLS_WEATHER = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local time in a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for a query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

TOOLS_STRUCT = [
    {
        "type": "function",
        "function": {
            "name": "create_ticket",
            "description": "Create a support ticket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                    "assignee": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "priority"],
            },
        },
    }
]

WORKFLOW_TOOLS = [
    dict(
        id="wf-06-single-call",
        title="Single correct tool call",
        prompt="What's the weather in Boston in fahrenheit?",
        tools=TOOLS_WEATHER,
        expected_name="get_weather",
        expected_args_contain={"city": "Boston", "unit": "fahrenheit"},
    ),
    dict(
        id="wf-07-choose-tool",
        title="Pick correct tool from three",
        prompt="I'm meeting a friend in Tokyo tomorrow. What time is it there right now?",
        tools=TOOLS_WEATHER,
        expected_name="get_time",
        expected_args_contain={"city": "Tokyo"},
    ),
    dict(
        id="wf-08-structured",
        title="Structured tool call with enums and arrays",
        prompt=(
            "Create an urgent ticket titled 'Prod DB replication stalled' assigned to 'oncall-sre' "
            "with tags 'database' and 'urgent-p1'."
        ),
        tools=TOOLS_STRUCT,
        expected_name="create_ticket",
        expected_args_contain={
            "title": "Prod DB replication stalled",
            "priority": "urgent",
            "assignee": "oncall-sre",
        },
        expected_tags={"database", "urgent-p1"},
    ),
]


# ---------------------------------------------------------------------------
# HTTP + extraction helpers
# ---------------------------------------------------------------------------
MAX_TOKENS_DEFAULT = 4096  # overridable via --max-tokens (reasoning models need more)


def call(url: str, model: str, messages: list, tools=None, max_tokens=None, timeout=300) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens or MAX_TOKENS_DEFAULT,
        "temperature": 0.2,
        "seed": 42,
        "stream": False,
    }
    if tools is not None:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    resp["_wall"] = time.time() - t0
    return resp


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def strip_think(text: str) -> str:
    return THINK_RE.sub("", text).strip()


def extract_code(text: str) -> str:
    """Return the last python code block if present, else the stripped text."""
    text = strip_think(text)
    blocks = CODE_FENCE_RE.findall(text)
    if blocks:
        return blocks[-1].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# HumanEval scoring
# ---------------------------------------------------------------------------
def run_humaneval_case(code: str, tests: str, tmpdir: Path) -> tuple[bool, str]:
    script = tmpdir / "check.py"
    script.write_text(code + "\n\n" + tests, encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            return True, ""
        err = (proc.stderr or proc.stdout or "").strip()
        return False, err[-400:]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, f"runner error: {e}"


def bench_humaneval(url: str, model: str) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        for prob in HUMANEVAL:
            print(f"  [{prob['id']}] ", end="", flush=True)
            try:
                resp = call(url, model, [
                    {"role": "system", "content": "You are a careful Python coder. Return only what the user asks for — no prose."},
                    {"role": "user", "content": prob["prompt"]},
                ])
                msg = resp["choices"][0]["message"]
                raw = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                finish = resp["choices"][0].get("finish_reason")
                code = extract_code(raw)
                ok, err = run_humaneval_case(code, prob["tests"], tmpdir)
                results.append(dict(
                    id=prob["id"],
                    pass_=ok,
                    error=err,
                    raw=raw,
                    reasoning=reasoning,
                    code=code,
                    finish_reason=finish,
                    wall=resp["_wall"],
                    usage=resp.get("usage", {}),
                ))
                print("PASS" if ok else f"FAIL ({err[:60]})")
            except Exception as e:
                results.append(dict(id=prob["id"], pass_=False, error=f"http: {e}", raw="", code="", wall=None, usage={}))
                print(f"ERROR {e}")
    return results


# ---------------------------------------------------------------------------
# Workflow (subjective) + tool-calling (auto structural check)
# ---------------------------------------------------------------------------
def bench_workflow_subjective(url: str, model: str, items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        print(f"  [{it['id']}] ", end="", flush=True)
        try:
            resp = call(url, model, [{"role": "user", "content": it["prompt"]}])
            msg = resp["choices"][0]["message"]
            raw = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            finish = resp["choices"][0].get("finish_reason")
            out.append(dict(
                id=it["id"],
                title=it["title"],
                prompt=it["prompt"],
                raw=raw,
                reasoning=reasoning,
                stripped=strip_think(raw),
                finish_reason=finish,
                wall=resp["_wall"],
                usage=resp.get("usage", {}),
            ))
            print(f"OK ({resp['_wall']:.1f}s, {resp.get('usage', {}).get('completion_tokens', '?')} tok)")
        except Exception as e:
            out.append(dict(id=it["id"], title=it["title"], prompt=it["prompt"], raw="", stripped="", error=str(e)))
            print(f"ERROR {e}")
    return out


def _extract_tool_call(resp: dict) -> tuple[str | None, dict | None, str]:
    """Return (name, args_dict, note). Handles both native tool_calls and inline JSON."""
    msg = resp["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    if tcs:
        call0 = tcs[0]
        name = call0.get("function", {}).get("name")
        args_raw = call0.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except Exception:
            args = None
        return name, args, "native"
    content = msg.get("content") or ""
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
                return obj["name"], obj.get("arguments"), "inline-json"
        except Exception:
            pass
    return None, None, "no-tool-call"


def bench_tool_calls(url: str, model: str) -> list[dict]:
    out = []
    for it in WORKFLOW_TOOLS:
        print(f"  [{it['id']}] ", end="", flush=True)
        try:
            resp = call(url, model, [{"role": "user", "content": it["prompt"]}], tools=it["tools"])
            name, args, note = _extract_tool_call(resp)
            passed = True
            reasons = []
            if name != it["expected_name"]:
                passed = False; reasons.append(f"name={name!r} expected={it['expected_name']!r}")
            if not isinstance(args, dict):
                passed = False; reasons.append("args not dict")
            else:
                for k, v in it["expected_args_contain"].items():
                    got = args.get(k)
                    if isinstance(v, str) and isinstance(got, str):
                        if v.lower() not in got.lower():
                            passed = False; reasons.append(f"arg {k}={got!r} missing {v!r}")
                    elif got != v:
                        passed = False; reasons.append(f"arg {k}={got!r} != {v!r}")
                if "expected_tags" in it:
                    tags = args.get("tags") or []
                    if not isinstance(tags, list) or not it["expected_tags"].issubset(set(tags)):
                        passed = False; reasons.append(f"tags={tags} missing {it['expected_tags']}")
            out.append(dict(
                id=it["id"], title=it["title"], prompt=it["prompt"],
                pass_=passed, note=note, reasons=reasons,
                got_name=name, got_args=args,
                raw_content=resp["choices"][0]["message"].get("content"),
                raw_tool_calls=resp["choices"][0]["message"].get("tool_calls"),
                wall=resp["_wall"],
                usage=resp.get("usage", {}),
            ))
            print("PASS" if passed else f"FAIL ({'; '.join(reasons)})")
        except Exception as e:
            out.append(dict(id=it["id"], title=it["title"], pass_=False, error=str(e)))
            print(f"ERROR {e}")
    return out


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main():
    global MAX_TOKENS_DEFAULT
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=URL_DEFAULT)
    ap.add_argument("--model", required=True, help="model alias (e.g. ornith-9b, qwen3-14b)")
    ap.add_argument("--out",   required=True, help="output JSON path")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help=f"per-request completion budget (default {MAX_TOKENS_DEFAULT}; raise for reasoning models)")
    args = ap.parse_args()
    if args.max_tokens:
        MAX_TOKENS_DEFAULT = args.max_tokens

    print(f"\n=== Ping {args.url} model={args.model} ===")
    try:
        ping = call(args.url, args.model, [{"role": "user", "content": "ping"}], max_tokens=8, timeout=30)
        print(f"  ok, {ping['_wall']:.2f}s")
    except Exception as e:
        print(f"  FAILED to reach server: {e}")
        sys.exit(2)

    print("\n=== HumanEval (10) ===")
    he = bench_humaneval(args.url, args.model)

    print("\n=== Workflow: coding (3) ===")
    wf_code = bench_workflow_subjective(args.url, args.model, WORKFLOW_CODE)

    print("\n=== Workflow: debug (2) ===")
    wf_debug = bench_workflow_subjective(args.url, args.model, WORKFLOW_DEBUG)

    print("\n=== Tool calling (3) ===")
    tools = bench_tool_calls(args.url, args.model)

    result = dict(
        model=args.model,
        url=args.url,
        humaneval=he,
        workflow_code=wf_code,
        workflow_debug=wf_debug,
        tools=tools,
        summary=dict(
            humaneval_passed=sum(1 for r in he if r.get("pass_")),
            humaneval_total=len(he),
            tools_passed=sum(1 for r in tools if r.get("pass_")),
            tools_total=len(tools),
        ),
    )
    Path(args.out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    s = result["summary"]
    print(f"\n=== SUMMARY {args.model} ===")
    print(f"  HumanEval: {s['humaneval_passed']}/{s['humaneval_total']}")
    print(f"  Tools:     {s['tools_passed']}/{s['tools_total']}")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
