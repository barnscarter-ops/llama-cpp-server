"""Deterministic item generators. Same (category, index) always gives the same item.

Expected answers are computed by reference code in this file, never by a model.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import textwrap

from .. import speed

ANSWER_LINE = "\n\nFinish with one line of the form 'ANSWER: <value>' and nothing after it."


def rng_for(cat: str, i: int) -> random.Random:
    return random.Random(int(hashlib.sha256(f"suite|{cat}|{i}".encode()).hexdigest()[:12], 16))


def item(cat: str, i: int, prompt: str, scorer: dict, max_tokens: int, **extra) -> dict:
    return {"id": f"{cat}-{i:04d}", "category": cat, "messages": [{"role": "user", "content": prompt}],
            "scorer": scorer, "max_tokens": max_tokens, **extra}


# ── IFEval-style instruction following ───────────────────────────────────────

TOPICS = ["a database migration", "cleaning a workshop", "choosing a breaker panel", "a weekly team meeting",
          "backing up a laptop", "ordering parts for a job", "a customer reschedule request", "winter road safety",
          "writing a changelog", "onboarding a new hire", "reading a wiring diagram", "a late delivery",
          "testing a smoke detector", "planning a small budget", "labeling cables", "a power outage checklist",
          "estimating a rewire", "reviewing a pull request", "preparing a quote", "a quiet holiday week"]
FORMS = ["Write a short note about {t}.", "Explain {t} to a new colleague.", "Draft a message to a customer about {t}.",
         "Give practical advice on {t}.", "Describe the steps involved in {t}."]
KEYWORDS = ["schedule", "safety", "budget", "review", "backup", "label", "checklist", "estimate", "priority", "follow-up"]
FORBIDDEN = ["very", "really", "just", "obviously", "simply", "basically"]
IF_ENDINGS = ["Is there anything else I can help with?", "Thank you for reading.", "End of message."]


def _describe(c: dict) -> str:
    t = c["type"]
    return {
        "min_words": f"Use at least {c.get('n')} words.", "max_words": f"Use at most {c.get('n')} words.",
        "no_commas": "Do not use any commas.", "all_lowercase": "Write everything in lowercase letters.",
        "all_uppercase": "Write everything in capital letters.",
        "keywords": f"Include the words: {', '.join(c.get('words', []))}.",
        "forbidden": f"Do not use these words: {', '.join(c.get('words', []))}.",
        "bullets": f"Use exactly {c.get('n')} bullet points, each starting with '- '.",
        "paragraphs": f"Write exactly {c.get('n')} paragraphs separated by blank lines.",
        "ends_with": f"End your reply with exactly this sentence: {c.get('text')}",
        "starts_with": f"Start your reply with exactly: {c.get('text')}",
        "json_only": "Reply with a single valid JSON object and nothing else.",
        "title_brackets": "Include a title wrapped in double angle brackets, like <<title>>.",
        "n_sentences": f"Use exactly {c.get('n')} sentences.",
        "contains_n_placeholders": f"Include at least {c.get('n')} placeholders in square brackets, like [name].",
    }[t]


def _make_constraints(r: random.Random) -> list[dict]:
    pool = [
        lambda: {"type": "min_words", "n": r.choice([60, 90, 120])},
        lambda: {"type": "max_words", "n": r.choice([40, 60, 80])},
        lambda: {"type": "no_commas"}, lambda: {"type": "all_lowercase"},
        lambda: {"type": "keywords", "words": r.sample(KEYWORDS, 2)},
        lambda: {"type": "forbidden", "words": r.sample(FORBIDDEN, 2)},
        lambda: {"type": "bullets", "n": r.choice([3, 4, 5])},
        lambda: {"type": "paragraphs", "n": r.choice([2, 3])},
        lambda: {"type": "ends_with", "text": r.choice(IF_ENDINGS)},
        lambda: {"type": "title_brackets"}, lambda: {"type": "contains_n_placeholders", "n": r.choice([2, 3])},
        lambda: {"type": "n_sentences", "n": r.choice([3, 4, 5])},
    ]
    clash = [{"min_words", "max_words"}, {"bullets", "paragraphs"}, {"bullets", "n_sentences"},
             {"paragraphs", "n_sentences"}, {"all_lowercase", "ends_with"}, {"max_words", "paragraphs"},
             {"max_words", "bullets"}, {"min_words", "n_sentences"}, {"max_words", "keywords"}]
    for _ in range(50):
        cs = [f() for f in r.sample(pool, r.choice([2, 3]))]
        kinds = {c["type"] for c in cs}
        if not any(pair <= kinds for pair in clash):
            return cs
    return [{"type": "no_commas"}, {"type": "title_brackets"}]


def gen_ifeval(n: int) -> list[dict]:
    out = []
    for i in range(n):
        r = rng_for("ifeval", i)
        if i % 10 == 9:  # a strict-format item: JSON only
            fields = r.sample(["task", "owner", "due", "priority", "status", "notes"], 3)
            prompt = (f"Summarise {r.choice(TOPICS)} as a JSON object with exactly the keys {', '.join(fields)}. "
                      "Reply with a single valid JSON object and nothing else.")
            scorer = {"type": "ifeval", "constraints": [{"type": "json_only"}]}
        else:
            cs = _make_constraints(r)
            prompt = r.choice(FORMS).format(t=r.choice(TOPICS)) + " " + " ".join(_describe(c) for c in cs)
            scorer = {"type": "ifeval", "constraints": cs}
        out.append(item("ifeval", i, prompt, scorer, 700))
    return out


# ── Clerk-grade mechanical work ──────────────────────────────────────────────

NAMES = ["alpha", "bravo", "cedar", "delta", "ember", "falcon", "garnet", "harbor", "indigo", "juniper"]
SERVICES = ["api", "auth", "billing", "cache", "mailer", "queue", "search", "sync"]


def _clerk_rename(r):
    src_names = r.sample(["total", "count", "tmp", "val", "res", "buf", "idx", "acc"], 3)
    new_names = r.sample(["sum_amount", "item_count", "scratch", "value", "result", "buffer", "position", "running"], 3)
    code = (f"def run(items):\n    {src_names[0]} = 0\n    {src_names[1]} = []\n    for {src_names[2]}, x in enumerate(items):\n"
            f"        {src_names[0]} += x\n        {src_names[1]}.append({src_names[2]} * x)\n    return {src_names[0]}, {src_names[1]}\n")
    want = code
    for a, b in zip(src_names, new_names):
        want = re.sub(rf"\b{a}\b", b, want)
    mapping = ", ".join(f"{a} -> {b}" for a, b in zip(src_names, new_names))
    return (f"Rename these identifiers everywhere in the code (whole words only): {mapping}. "
            f"Return only the full updated code in one code block.\n\n```python\n{code}```"), {"type": "exact_text", "expected": want}


def _clerk_csv_md(r):
    cols = r.sample(["name", "qty", "price", "site", "status", "owner"], 3)
    rows = [[r.choice(NAMES), str(r.randint(1, 40)), f"{r.randint(1, 99)}.{r.randint(0, 99):02d}"] for _ in range(r.randint(3, 5))]
    csv = ",".join(cols) + "\n" + "\n".join(",".join(x) for x in rows)
    md = "| " + " | ".join(cols) + " |\n| " + " | ".join("---" for _ in cols) + " |\n" + "\n".join("| " + " | ".join(x) + " |" for x in rows)
    return ("Convert this CSV to a GitHub markdown table with a --- separator row. Return only the table.\n\n"
            f"```csv\n{csv}\n```"), {"type": "exact_text", "expected": md}


def _clerk_json_sort(r):
    keys = r.sample(NAMES, 5)
    obj = {k: r.randint(1, 99) for k in keys}
    want = json.dumps(dict(sorted(obj.items())), indent=2)
    return ("Sort the keys of this JSON object alphabetically and pretty-print with 2-space indentation. "
            f"Return only the JSON.\n\n```json\n{json.dumps(obj)}\n```"), {"type": "exact_text", "expected": want}


def _clerk_log_json(r):
    lines, exp = [], []
    for _ in range(r.randint(3, 4)):
        lvl, svc = r.choice(["INFO", "WARN", "ERROR"]), r.choice(SERVICES)
        ts = f"2026-03-{r.randint(1, 28):02d} {r.randint(0, 23):02d}:{r.randint(0, 59):02d}:{r.randint(0, 59):02d}"
        lat, user = r.randint(5, 900), f"u{r.randint(1, 99)}"
        lines.append(f"{ts} {lvl} {svc} latency={lat}ms user={user}")
        exp.append({"ts": ts, "level": lvl, "service": svc, "latency_ms": lat, "user": user})
    return ("Convert each log line to a JSON object with keys ts, level, service, latency_ms (integer), user. "
            "Return only a JSON array.\n\n```\n" + "\n".join(lines) + "\n```"), {"type": "json", "expected": exp}


def _clerk_dates(r):
    ds = [(r.randint(1, 12), r.randint(1, 28), r.randint(2024, 2027)) for _ in range(r.randint(3, 5))]
    src = "\n".join(f"{m:02d}/{d:02d}/{y}" for m, d, y in ds)
    want = "\n".join(f"{y}-{m:02d}-{d:02d}" for m, d, y in ds)
    return ("Convert each MM/DD/YYYY date to YYYY-MM-DD, one per line, same order. Return only the lines.\n\n"
            f"```\n{src}\n```"), {"type": "exact_text", "expected": want}


def _clerk_dedupe(r):
    pool = r.sample(NAMES, 5)
    lines = [r.choice(pool) for _ in range(r.randint(7, 10))]
    want = "\n".join(sorted(set(lines)))
    return ("Remove duplicate lines and sort the remaining lines alphabetically. Return only the lines.\n\n"
            "```\n" + "\n".join(lines) + "\n```"), {"type": "exact_text", "expected": want}


def _clerk_emails(r):
    emails = [f"{r.choice(NAMES)}{r.randint(1, 99)}@{r.choice(['example.com', 'mail.test', 'corp.example'])}" for _ in range(r.randint(2, 3))]
    text = " ".join(f"Contact {e} about {r.choice(NAMES)}." for e in emails)
    return ("List every email address in this text as a JSON array of strings in order of appearance. Return only the array.\n\n"
            f"{text}"), {"type": "json", "expected": emails}


def _clerk_units(r):
    vals = [round(r.uniform(1, 90), 1) for _ in range(r.randint(3, 4))]
    want = "\n".join(f"{v} ft = {v * 0.3048:.2f} m" for v in vals)
    return ("Convert each length from feet to metres (1 ft = 0.3048 m), formatted exactly like '12.5 ft = 3.81 m' "
            "with two decimals. Return only the lines.\n\n```\n" + "\n".join(f"{v} ft" for v in vals) + "\n```"), {"type": "exact_text", "expected": want}


def _clerk_camel(r):
    words = ["_".join(r.sample(["get", "set", "user", "order", "total", "item", "index", "page"], r.randint(2, 3))) for _ in range(4)]
    def camel(s):
        a = s.split("_")
        return a[0] + "".join(w.title() for w in a[1:])
    return ("Convert each snake_case name to camelCase, one per line, same order. Return only the lines.\n\n```\n"
            + "\n".join(words) + "\n```"), {"type": "exact_text", "expected": "\n".join(camel(w) for w in words)}


def _clerk_kv_json(r):
    keys = r.sample(["host", "port", "mode", "retries", "name", "region"], 4)
    d = {}
    for k in keys:
        d[k] = r.randint(1, 9999) if k in ("port", "retries") else r.choice(NAMES)
    text = "\n".join(f"{k}={v}" for k, v in d.items())
    return ("Convert this key=value config to a JSON object. Numbers must be JSON numbers, everything else strings. "
            f"Return only the JSON.\n\n```\n{text}\n```"), {"type": "json", "expected": d}


CLERK = [_clerk_rename, _clerk_csv_md, _clerk_json_sort, _clerk_log_json, _clerk_dates, _clerk_dedupe,
         _clerk_emails, _clerk_units, _clerk_camel, _clerk_kv_json]


def gen_clerk(n: int) -> list[dict]:
    out = []
    for i in range(n):
        r = rng_for("clerk", i)
        prompt, scorer = CLERK[i % len(CLERK)](r)
        out.append(item("clerk", i, prompt, scorer, 600, template=CLERK[i % len(CLERK)].__name__))
    return out


# ── Reasoning (exact answers) ────────────────────────────────────────────────

def _r_discount(r):
    price, n = r.randint(20, 90) * 5, r.randint(2, 6)
    pct, tax = r.choice([10, 15, 20, 25]), r.choice([5, 8, 10])
    total = price * n * (100 - pct) / 100 * (100 + tax) / 100
    return (f"A shop sells an item for ${price}. Someone buys {n}. A {pct}% discount applies to the whole purchase, "
            f"then {tax}% sales tax is added to the discounted amount. What is the final total in dollars, "
            "rounded to two decimals?"), {"type": "exact", "answers": [f"{total:.2f}"], "numeric": True}


def _r_modpow(r):
    a, b, m = r.randint(3, 19), r.randint(20, 200), r.choice([7, 11, 13, 17, 19, 23, 97])
    return f"What is {a}^{b} mod {m}?", {"type": "exact", "answers": [str(pow(a, b, m))], "numeric": True}


def _r_order(r):
    names = r.sample(NAMES, 5)
    order = names[:]  # tallest first
    clues = [f"{order[i].title()} is taller than {order[i + 1].title()}." for i in range(4)]
    r.shuffle(clues)
    which = r.choice(["tallest", "shortest", "middle"])
    ans = {"tallest": order[0], "shortest": order[-1], "middle": order[2]}[which]
    return (" ".join(clues) + f" Who is the {which} of the five?"), {"type": "exact", "answers": [ans, ans.title()]}


def _r_sequence(r):
    a, d, c = r.randint(1, 9), r.randint(2, 7), r.randint(1, 4)
    seq = [a + d * i + c * i * i for i in range(6)]
    nxt = a + d * 6 + c * 36
    return (f"The sequence {', '.join(map(str, seq))} follows a quadratic rule. What is the next term?"), {"type": "exact", "answers": [str(nxt)], "numeric": True}


def _r_base(r):
    n, base = r.randint(100, 5000), r.choice([2, 8, 16])
    digits = "0123456789abcdef"
    s, x = "", n
    while x:
        s, x = digits[x % base] + s, x // base
    return f"Write the decimal number {n} in base {base} (lowercase digits).", {"type": "exact", "answers": [s]}


def _r_workers(r):
    k, tasks = r.choice([2, 3]), [r.randint(2, 12) for _ in range(r.randint(6, 8))]
    free = [0] * k
    for t in tasks:
        i = free.index(min(free))
        free[i] += t
    return (f"{k} workers share these tasks (durations in minutes, in this order): {tasks}. Each task goes to whichever worker "
            f"becomes free first (lowest index on ties). When is the last worker finished, in minutes from the start?"), {"type": "exact", "answers": [str(max(free))], "numeric": True}


def _r_count(r):
    n, a, b = r.randint(100, 900), r.choice([3, 4, 6]), r.choice([5, 7, 9])
    cnt = sum(1 for x in range(1, n + 1) if x % a == 0 or x % b == 0)
    return f"How many integers from 1 to {n} inclusive are divisible by {a} or by {b}?", {"type": "exact", "answers": [str(cnt)], "numeric": True}


REASONING = [_r_discount, _r_modpow, _r_order, _r_sequence, _r_base, _r_workers, _r_count]


def gen_reasoning(n: int) -> list[dict]:
    out = []
    for i in range(n):
        r = rng_for("reasoning", i)
        q, scorer = REASONING[i % len(REASONING)](r)
        out.append(item("reasoning", i, q + ANSWER_LINE, scorer, 1500, template=REASONING[i % len(REASONING)].__name__))
    return out


# ── Log analysis (multi-step) ────────────────────────────────────────────────

def _make_logs(r, n=40):
    lines, rows = [], []
    t = r.randint(0, 3600)
    for _ in range(n):
        t += r.randint(1, 40)
        ts = f"{8 + t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"
        svc, lvl = r.choice(SERVICES[:5]), r.choices(["INFO", "WARN", "ERROR"], [6, 2, 2])[0]
        lat, user = r.randint(5, 900), f"u{r.randint(1, 12)}"
        rows.append((ts, svc, lvl, lat, user))
        lines.append(f"{ts} {lvl} {svc} latency={lat}ms user={user}")
    return lines, rows


def _l_top_error(r):
    lines, rows = _make_logs(r)
    counts = {}
    for _, svc, lvl, *_ in rows:
        if lvl == "ERROR":
            counts[svc] = counts.get(svc, 0) + 1
    if not counts:
        return _l_top_error(random.Random(r.random()))
    best = max(counts.values())
    winners = sorted(s for s, c in counts.items() if c == best)
    return ("Which service logged the most ERROR lines, and how many? If tied, give the alphabetically first. Format: name=count.\n\n"
            + "\n".join(lines)), {"type": "exact", "answers": [f"{winners[0]}={best}"]}


def _l_slow_users(r):
    lines, rows = _make_logs(r)
    lim = r.choice([400, 500, 600])
    users = {u for *_, lat, u in rows if lat > lim}
    return (f"How many distinct users had at least one request slower than {lim}ms?\n\n" + "\n".join(lines)), {"type": "exact", "answers": [str(len(users))], "numeric": True}


def _l_error_burst(r):
    lines, rows = _make_logs(r, 45)
    first = None
    for i in range(len(rows) - 2):
        a, b, c = rows[i:i + 3]
        if a[2] == b[2] == c[2] == "ERROR" or (a[1] == b[1] == c[1] and a[2] == b[2] == c[2] == "WARN"):
            first = a[0]
            break
    if first is None:  # plant a burst so every item has an answer
        k = r.randint(5, 30)
        for j in range(3):
            ts, svc, _, lat, user = rows[k + j]
            rows[k + j] = (ts, svc, "ERROR", lat, user)
            lines[k + j] = f"{ts} ERROR {svc} latency={lat}ms user={user}"
        first = rows[k][0]
        for i in range(len(rows) - 2):  # recompute so the answer is the true first
            a, b, c = rows[i:i + 3]
            if a[2] == b[2] == c[2] == "ERROR" or (a[1] == b[1] == c[1] and a[2] == b[2] == c[2] == "WARN"):
                first = a[0]
                break
    return ("Find the first moment where three consecutive log lines are all ERROR (any services), or three consecutive lines "
            "are all WARN from the same service. Give the timestamp of the first of those three lines as HH:MM:SS.\n\n"
            + "\n".join(lines)), {"type": "exact", "answers": [first]}


LOGS = [_l_top_error, _l_slow_users, _l_error_burst]


def gen_logs(n: int) -> list[dict]:
    out = []
    for i in range(n):
        r = rng_for("analysis", i)
        q, scorer = LOGS[i % len(LOGS)](r)
        out.append(item("analysis", i, q + ANSWER_LINE, scorer, 1800, template=LOGS[i % len(LOGS)].__name__))
    return out


# ── Code: generated functions with reference solutions ───────────────────────

def _exec_ref(src: str, name: str):
    ns: dict = {}
    exec(compile(src, "<ref>", "exec"), ns)  # reference code written in this file only
    return ns[name]


def _tests_from(ref_fn, name: str, inputs: list[tuple]) -> str:
    lines = []
    for args in inputs:
        lines.append(f"assert {name}({', '.join(repr(a) for a in args)}) == {ref_fn(*args)!r}")
    return "\n".join(lines)


def _rl(r, lo, hi, n=(0, 8)):
    return [r.randint(lo, hi) for _ in range(r.randint(*n))]


def _words(r, n=(0, 6)):
    return [r.choice(NAMES + ["Alpha", "bravo", "A", "b"]) for _ in range(r.randint(*n))]


def easy_templates():
    """Each: (func_name, spec(r)->(spec_text, ref_src, inputs), bug (old,new) or None)."""
    def window_sum(r):
        k = r.randint(2, 4)
        spec = f"`window_sums(xs)` returns the list of sums of every {k} consecutive elements of the list xs (empty list if xs has fewer than {k} elements)."
        ref = f"def window_sums(xs):\n    return [sum(xs[i:i + {k}]) for i in range(len(xs) - {k} + 1)]\n"
        return spec, ref, [(_rl(r, -9, 9, (0, 9)),) for _ in range(8)]

    def rle(r):
        spec = "`rle(s)` run-length encodes a string: 'aaabcc' -> 'a3b1c2'. The empty string returns ''."
        ref = ("def rle(s):\n    out = []\n    i = 0\n    while i < len(s):\n        j = i\n"
               "        while j < len(s) and s[j] == s[i]:\n            j += 1\n        out.append(s[i] + str(j - i))\n        i = j\n    return ''.join(out)\n")
        return spec, ref, [("".join(r.choice("abc") * r.randint(1, 4) for _ in range(r.randint(0, 5))),) for _ in range(8)]

    def rotate(r):
        spec = "`rotate(xs, k)` returns a new list with xs rotated right by k positions (k may be negative or larger than len(xs)); an empty list stays empty."
        ref = "def rotate(xs, k):\n    if not xs:\n        return []\n    k %= len(xs)\n    return xs[-k:] + xs[:-k] if k else list(xs)\n"
        return spec, ref, [(_rl(r, 0, 9, (0, 7)), r.randint(-12, 12)) for _ in range(8)]

    def dedupe(r):
        spec = "`dedupe(xs)` removes duplicates from the list xs keeping the first occurrence of each value and the original order."
        ref = "def dedupe(xs):\n    seen = set()\n    out = []\n    for x in xs:\n        if x not in seen:\n            seen.add(x)\n            out.append(x)\n    return out\n"
        return spec, ref, [(_rl(r, 0, 5, (0, 10)),) for _ in range(8)]

    def parse_kv(r):
        sep = r.choice([";", "|"])
        spec = f"`parse_kv(s)` parses a string like 'a=1{sep}b=2' into a dict of str->int; ignore empty segments and surrounding spaces."
        ref = (f"def parse_kv(s):\n    out = {{}}\n    for part in s.split({sep!r}):\n        part = part.strip()\n        if not part:\n            continue\n"
               "        k, v = part.split('=')\n        out[k.strip()] = int(v)\n    return out\n")
        return spec, ref, [(sep.join(f"{r.choice('abcde')}={r.randint(0, 99)}" + r.choice(["", " "]) for _ in range(r.randint(0, 4))),) for _ in range(8)]

    def moving_avg(r):
        k = r.randint(2, 4)
        spec = f"`moving_avg(xs)` returns the rounded (2 decimals) average of each {k} consecutive values of xs; empty list if fewer than {k} values."
        ref = f"def moving_avg(xs):\n    return [round(sum(xs[i:i + {k}]) / {k}, 2) for i in range(len(xs) - {k} + 1)]\n"
        return spec, ref, [(_rl(r, 0, 20, (0, 8)),) for _ in range(8)]

    def top_words(r):
        k = r.randint(1, 3)
        spec = (f"`top_words(text)` returns the {k} most frequent lowercase words of text (split on whitespace) as a list; "
                "ties are broken alphabetically.")
        ref = (f"def top_words(text):\n    counts = {{}}\n    for w in text.lower().split():\n        counts[w] = counts.get(w, 0) + 1\n"
               f"    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:{k}]]\n")
        return spec, ref, [(" ".join(_words(r, (0, 9))),) for _ in range(8)]

    def merge_intervals(r):
        spec = "`merge_intervals(iv)` takes a list of [start, end] pairs and returns them merged (overlapping or touching intervals combined), sorted by start, as a list of lists."
        ref = ("def merge_intervals(iv):\n    out = []\n    for s, e in sorted(iv):\n        if out and s <= out[-1][1]:\n"
               "            out[-1][1] = max(out[-1][1], e)\n        else:\n            out.append([s, e])\n    return out\n")
        def mk():
            return ([[a, a + r.randint(0, 5)] for a in _rl(r, 0, 20, (0, 6))],)
        return spec, ref, [mk() for _ in range(8)]

    def roman(r):
        spec = "`roman_to_int(s)` converts a valid Roman numeral (I V X L C D M, subtractive forms allowed) to an integer."
        ref = ("def roman_to_int(s):\n    v = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n    t = 0\n"
               "    for i, c in enumerate(s):\n        if i + 1 < len(s) and v[c] < v[s[i + 1]]:\n            t -= v[c]\n        else:\n            t += v[c]\n    return t\n")
        pool = ["III", "IV", "IX", "XL", "XC", "CD", "CM", "MCMXCIV", "MMXXVI", "XLII", "LVIII", "DCCC"]
        return spec, ref, [(r.choice(pool),) for _ in range(8)]

    def balanced(r):
        spec = "`balanced(s)` returns True if the brackets ()[]{} in s are balanced and properly nested, ignoring all other characters."
        ref = ("def balanced(s):\n    pairs = {')': '(', ']': '[', '}': '{'}\n    st = []\n    for c in s:\n        if c in '([{':\n            st.append(c)\n"
               "        elif c in pairs:\n            if not st or st.pop() != pairs[c]:\n                return False\n    return not st\n")
        pool = ["([]{})", "(]", "((", "a(b)c[d]{e}", "", "{[()]}", "([)]", "())"]
        return spec, ref, [(r.choice(pool),) for _ in range(8)]

    def transpose(r):
        spec = "`transpose(m)` returns the transpose of a rectangular matrix given as a list of lists; an empty matrix returns []."
        ref = "def transpose(m):\n    return [list(row) for row in zip(*m)] if m else []\n"
        def mk():
            rows, cols = r.randint(0, 3), r.randint(1, 4)
            return ([[r.randint(0, 9) for _ in range(cols)] for _ in range(rows)],)
        return spec, ref, [mk() for _ in range(8)]

    def cummax(r):
        spec = "`cummax(xs)` returns the running maximum of xs as a list of the same length."
        ref = "def cummax(xs):\n    out = []\n    m = None\n    for x in xs:\n        m = x if m is None or x > m else m\n        out.append(m)\n    return out\n"
        return spec, ref, [(_rl(r, -5, 20, (0, 8)),) for _ in range(8)]

    def count_chars(r):
        chars = "".join(r.sample("aeiouxyz", 3))
        spec = f"`count_chars(s)` counts how many characters of s are in the set '{chars}' (case-insensitive)."
        ref = f"def count_chars(s):\n    return sum(1 for c in s.lower() if c in {chars!r})\n"
        return spec, ref, [("".join(r.choice("abcxyzAEIOU ") for _ in range(r.randint(0, 12))),) for _ in range(8)]

    def clamp_list(r):
        lo, hi = r.randint(-3, 2), r.randint(5, 12)
        spec = f"`clamp_all(xs)` returns xs with every value limited to the inclusive range [{lo}, {hi}]."
        ref = f"def clamp_all(xs):\n    return [min(max(x, {lo}), {hi}) for x in xs]\n"
        return spec, ref, [(_rl(r, -10, 20, (0, 8)),) for _ in range(8)]

    def pair_sum(r):
        t = r.randint(5, 15)
        spec = f"`pairs_with_sum(xs)` returns the number of index pairs i<j with xs[i]+xs[j] == {t}."
        ref = f"def pairs_with_sum(xs):\n    return sum(1 for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[i] + xs[j] == {t})\n"
        return spec, ref, [(_rl(r, 0, 12, (0, 9)),) for _ in range(8)]

    # (name, builder, bug)  bug = (old, new) applied to ref to make the buggy version
    return [
        ("window_sums", window_sum, ("+ 1)]", ")]")), ("rle", rle, ("str(j - i)", "str(j - i + 1)")),
        ("rotate", rotate, ("xs[-k:] + xs[:-k]", "xs[k:] + xs[:k]")), ("dedupe", dedupe, ("if x not in seen", "if x in seen")),
        ("parse_kv", parse_kv, ("int(v)", "v")), ("moving_avg", moving_avg, ("round(sum", "(sum")),
        ("top_words", top_words, ("(-kv[1], kv[0])", "(kv[1], kv[0])")), ("merge_intervals", merge_intervals, ("s <= out[-1][1]", "s < out[-1][1]")),
        ("roman_to_int", roman, ("v[c] < v[s[i + 1]]", "v[c] > v[s[i + 1]]")), ("balanced", balanced, ("return not st", "return True")),
        ("transpose", transpose, ("zip(*m)", "zip(m)")), ("cummax", cummax, ("x > m", "x < m")),
        ("count_chars", count_chars, ("s.lower()", "s")), ("clamp_all", clamp_list, ("min(max(x,", "max(min(x,")),
        ("pairs_with_sum", pair_sum, ("range(i + 1,", "range(i,")),
    ]


def gen_code_easy(n: int) -> list[dict]:
    tmpl = easy_templates()
    out = []
    for i in range(n):
        r = rng_for("code_easy", i)
        name, builder, _bug = tmpl[i % len(tmpl)]
        spec, ref, inputs = builder(r)
        tests = _tests_from(_exec_ref(ref, name), name, inputs)
        prompt = f"Write a Python function {spec} Return only the function (and any imports it needs) in one code block."
        out.append(item("code_easy", i, prompt, {"type": "python", "tests": tests}, 900, template=name, reference=ref))
    return out


def _fails(src: str, name: str, tests: str) -> bool:
    """True if the (buggy) reference does not satisfy the tests. Simple generated code only."""
    try:
        ns: dict = {}
        exec(compile(src + "\n" + tests, "<buggy>", "exec"), ns)
    except Exception:
        return True
    return False


def gen_code_fix(n: int) -> list[dict]:
    tmpl = easy_templates()
    out = []
    for i in range(n):
        name, builder, bug = tmpl[i % len(tmpl)]
        for attempt in range(40):  # re-draw inputs until the planted bug is actually exposed
            r = rng_for("code_fix", i + 1000 * attempt)
            spec, ref, inputs = builder(r)
            if bug[0] not in ref:
                raise AssertionError(f"bug pattern {bug[0]!r} not in reference for {name}")
            buggy = ref.replace(bug[0], bug[1], 1)
            tests = _tests_from(_exec_ref(ref, name), name, inputs)
            if _fails(buggy, name, tests):
                break
        else:
            raise AssertionError(f"could not expose the planted bug for {name}")
        prompt = (f"This function is supposed to satisfy: {spec} It has a bug. Fix it and return the complete corrected function "
                  f"in one code block, with no explanation.\n\n```python\n{buggy}```")
        out.append(item("code_fix", i, prompt, {"type": "python", "tests": tests}, 1200, template=name, reference=ref, buggy=buggy))
    return out


# ── Code: harder multi-part problems ─────────────────────────────────────────

LRU_REF = '''class LRUCache:
    def __init__(self, capacity):
        self.cap = capacity
        self.d = {}
        self.order = []
    def get(self, key):
        if key not in self.d:
            return -1
        self.order.remove(key)
        self.order.append(key)
        return self.d[key]
    def put(self, key, value):
        if key in self.d:
            self.order.remove(key)
        elif len(self.d) >= self.cap:
            old = self.order.pop(0)
            del self.d[old]
        self.d[key] = value
        self.order.append(key)
'''


def _hard_lru(r):
    cap = r.randint(2, 3)
    ns: dict = {}
    exec(LRU_REF, ns)
    c = ns["LRUCache"](cap)
    ops, expected = [], []
    for _ in range(14):
        if r.random() < 0.5:
            k, v = r.randint(1, 5), r.randint(10, 99)
            ops.append(("put", k, v)); c.put(k, v); expected.append(None)
        else:
            k = r.randint(1, 5)
            ops.append(("get", k)); expected.append(c.get(k))
    spec = (f"Implement a class `LRUCache(capacity)` with `get(key)` (returns the value or -1; a hit makes the key most recently used) "
            f"and `put(key, value)` (inserts or updates and makes it most recently used; if the cache already holds `capacity` keys "
            f"and key is new, evict the least recently used key first).")
    tests = (f"c = LRUCache({cap})\nops = {ops!r}\nexpected = {expected!r}\ngot = []\nfor op in ops:\n    got.append(c.get(op[1]) if op[0] == 'get' else c.put(op[1], op[2]))\n"
             "assert got == expected, (got, expected)")
    return spec, tests


def _hard_topo(r):
    n = r.randint(5, 7)
    nodes = list(range(n))
    edges = set()
    perm = nodes[:]
    r.shuffle(perm)
    for _ in range(r.randint(n - 1, n + 2)):
        a, b = sorted(r.sample(range(n), 2))
        edges.add((perm[a], perm[b]))
    cyc = r.random() < 0.3
    if cyc:
        a, b = sorted(r.sample(range(n), 2))
        edges.add((perm[b], perm[a]))
        edges.add((perm[a], perm[b]))
    edges = sorted(edges)

    def ref(n_, e_):
        indeg = {i: 0 for i in range(n_)}
        adj = {i: [] for i in range(n_)}
        for a, b in e_:
            adj[a].append(b); indeg[b] += 1
        import heapq
        h = [i for i in indeg if indeg[i] == 0]
        heapq.heapify(h)
        out = []
        while h:
            x = heapq.heappop(h); out.append(x)
            for y in adj[x]:
                indeg[y] -= 1
                if indeg[y] == 0:
                    heapq.heappush(h, y)
        return out if len(out) == n_ else []

    cases = [(n, edges)] + [(m, [(a, b) for a, b in [(0, 1), (1, 2)] if a < m and b < m]) for m in (1, 3)]
    tests = "\n".join(f"assert topo_order({m}, {e!r}) == {ref(m, e)!r}" for m, e in cases)
    spec = ("Implement `topo_order(n, edges)` for a directed graph with nodes 0..n-1 and edges as (a, b) pairs meaning a must come before b. "
            "Return the lexicographically smallest topological order as a list, or an empty list if the graph has a cycle.")
    return spec, tests


def _hard_grid(r):
    h, w = r.randint(3, 5), r.randint(3, 5)
    grid = [[r.choice([0, 1, 2, 3, 4, 9]) for _ in range(w)] for _ in range(h)]
    grid[0][0] = grid[h - 1][w - 1] = 1
    walls = [(r.randrange(h), r.randrange(w)) for _ in range(r.randint(1, 4))]
    for y, x in walls:
        if (y, x) not in ((0, 0), (h - 1, w - 1)):
            grid[y][x] = -1

    def ref(g):
        INF = float("inf")
        hh, ww = len(g), len(g[0])
        dp = [[INF] * ww for _ in range(hh)]
        if g[0][0] >= 0:
            dp[0][0] = g[0][0]
        for y in range(hh):
            for x in range(ww):
                if g[y][x] < 0 or (y, x) == (0, 0):
                    continue
                best = min(dp[y - 1][x] if y else INF, dp[y][x - 1] if x else INF)
                dp[y][x] = best + g[y][x]
        return -1 if dp[-1][-1] == INF else dp[-1][-1]

    g2 = [[1, -1], [-1, 1]]
    tests = f"assert min_path({grid!r}) == {ref(grid)!r}\nassert min_path({g2!r}) == {ref(g2)!r}\nassert min_path([[5]]) == 5"
    spec = ("Implement `min_path(grid)`: grid is a list of lists of integers; a cell value of -1 is a wall, otherwise the value is the cost of entering the cell "
            "(the start cell's cost counts too). Moving only right or down from the top-left to the bottom-right, return the minimum total cost, or -1 if unreachable.")
    return spec, tests


def _hard_expr(r):
    def rnd_expr(depth=0):
        if depth > 2 or r.random() < 0.35:
            return str(r.randint(1, 20))
        op = r.choice("+-*")
        a, b = rnd_expr(depth + 1), rnd_expr(depth + 1)
        e = f"{a} {op} {b}"
        return f"({e})" if r.random() < 0.6 else e
    exprs = [rnd_expr() for _ in range(6)] + ["-(3 + 4) * 2", "2 * -3 + 10", "((7))", "10 - 2 - 3"]
    tests = "\n".join(f"assert evaluate({e!r}) == {eval(e)!r}" for e in exprs)
    spec = ("Implement `evaluate(s)` that evaluates an integer arithmetic expression with + - * (no division), parentheses, unary minus and spaces, "
            "using normal precedence and left associativity, and returns an int. Do not use eval, exec or ast.")
    return spec, tests


def _hard_edit(r):
    ins, sub = r.randint(1, 3), r.randint(1, 4)

    def ref(a, b):
        dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
        for i in range(len(a) + 1):
            dp[i][0] = i * ins
        for j in range(len(b) + 1):
            dp[0][j] = j * ins
        for i in range(1, len(a) + 1):
            for j in range(1, len(b) + 1):
                dp[i][j] = min(dp[i - 1][j] + ins, dp[i][j - 1] + ins, dp[i - 1][j - 1] + (0 if a[i - 1] == b[j - 1] else sub))
        return dp[-1][-1]

    pairs = [("kitten", "sitting"), ("", "abc"), ("flaw", "lawn"), ("same", "same")] + [("".join(r.choice("abc") for _ in range(r.randint(0, 6))), "".join(r.choice("abc") for _ in range(r.randint(0, 6)))) for _ in range(5)]
    tests = "\n".join(f"assert edit_cost({a!r}, {b!r}) == {ref(a, b)}" for a, b in pairs)
    spec = (f"Implement `edit_cost(a, b)`: the minimum total cost to turn string a into string b where inserting or deleting one character costs {ins} "
            f"and substituting one character costs {sub}.")
    return spec, tests


def _hard_rate(r):
    limit, window = r.randint(2, 4), r.choice([5, 10])
    ts = sorted(r.randint(0, 40) for _ in range(14))

    def ref():
        q, out = [], []
        for t in ts:
            q = [x for x in q if x > t - window]
            if len(q) < limit:
                q.append(t); out.append(True)
            else:
                out.append(False)
        return out

    tests = f"rl = RateLimiter({limit}, {window})\nts = {ts!r}\nassert [rl.allow(t) for t in ts] == {ref()!r}"
    spec = (f"Implement a class `RateLimiter(limit, window)` with `allow(t)`: t is a non-decreasing integer timestamp. A request is allowed "
            f"if fewer than `limit` allowed requests happened in the half-open window (t - window, t]; allowed requests are recorded, rejected ones are not. Return True or False.")
    return spec, tests


def _hard_inversions(r):
    def ref(xs):
        return sum(1 for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[i] > xs[j])
    cases = [[], [1], [2, 1]] + [[r.randint(0, 9) for _ in range(r.randint(3, 40))] for _ in range(6)]
    tests = "\n".join(f"assert count_inversions({c!r}) == {ref(c)}" for c in cases)
    spec = ("Implement `count_inversions(xs)`: the number of index pairs i<j with xs[i] > xs[j]. It must run in O(n log n) "
            "(a quadratic solution will time out on the large hidden input).")
    tests += "\nimport random\nrandom.seed(1)\nbig = [random.randint(0, 10**6) for _ in range(200000)]\nassert isinstance(count_inversions(big), int)"
    return spec, tests


HARD = [_hard_lru, _hard_topo, _hard_grid, _hard_expr, _hard_edit, _hard_rate, _hard_inversions]


def gen_code_hard(n: int) -> list[dict]:
    out = []
    for i in range(n):
        r = rng_for("code_hard", i)
        spec, tests = HARD[i % len(HARD)](r)
        prompt = spec + " Return only the code in one code block."
        out.append(item("code_hard", i, prompt, {"type": "python", "tests": tests}, 3500, template=HARD[i % len(HARD)].__name__))
    return out


# ── Long context (materialized at run time) ──────────────────────────────────

LC_TOKENS = [8000, 32000, 64000, 120000]  # the last stays under a 131072 window with room for the answer


def materialize_longctx(spec: dict) -> tuple[list[dict], dict]:
    """Rebuild the prompt and scorer from the small stored spec."""
    r = random.Random(spec["seed"])
    n = spec["needles"]
    codes = [f"{r.choice(NAMES)[:3].upper()}-{r.randint(1000, 9999)}" for _ in range(n)]
    labels = r.sample(["vault", "garage", "shed", "locker", "cabinet", "gate"], n)
    facts = [f"The access code for the {lb} is {cd}." for lb, cd in zip(labels, codes)]
    body = speed._fill("prose", spec["tokens"], spec["seed"] % 997)
    parts = body.split(". ")
    positions = sorted(r.sample(range(len(parts) // 10, len(parts) - len(parts) // 10), n))
    for k, pos in enumerate(positions):
        parts[pos] = parts[pos] + ". " + facts[k]
    text = ". ".join(parts)
    q = " ".join(f"What is the access code for the {lb}?" for lb in labels)
    prompt = (f"Read the text and answer the question at the end.\n\n{text}\n\n{q} Reply with only the codes, one per line, in the order asked.")
    return [{"role": "user", "content": prompt}], {"type": "needle", "needles": codes}


def gen_longctx(n: int) -> list[dict]:
    out = []
    for i in range(n):
        tokens = LC_TOKENS[i % len(LC_TOKENS)]
        needles = 1 if i % 2 == 0 else 3
        spec = {"tokens": tokens, "needles": needles, "seed": 1000 + i}
        out.append({"id": f"longctx-{i:04d}", "category": "longctx", "longctx": spec, "max_tokens": 120,
                    "scorer": {"type": "needle", "needles": []}, "messages": None})
    return out
