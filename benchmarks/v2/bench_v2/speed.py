"""The frozen speed set and its measurement loop.

Six Guardian-shaped jobs (1k..32k tokens in) in three content classes. Built
deterministically from fixed seeds and hashed; never edited after the first run.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field

from .backend import Backend, ChatResult
from .stats import median, weighted_geomean

CHARS_PER_TOKEN = 3.5  # conservative; real n_in comes back from the server
SHAPES = [  # (prompt tokens, max output tokens, winner-rule weight)
    (1000, 200, 0.15), (2000, 500, 0.20), (4000, 800, 0.25),
    (8000, 1000, 0.20), (16000, 500, 0.10), (32000, 300, 0.10),
]
CLASSES = ("code", "json", "prose")
WORDS = ("vector buffer cache lane queue socket stream token window kernel thread batch signal budget "
         "ledger mirror anchor bridge cursor driver engine filter gather handle import jitter keeper "
         "lookup module number object parser quorum router sketch target update vendor wander yield").split()


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


def _fill(cls: str, tokens: int, seed: int) -> str:
    target = int(tokens * CHARS_PER_TOKEN)
    rng = _rng("fill", cls, tokens, seed)
    out, size, i = [], 0, 0
    while size < target:
        if cls == "code":
            a, b = rng.choice(WORDS), rng.choice(WORDS)
            piece = (f"def {a}_{b}_{i}(items, limit={rng.randint(2, 99)}):\n"
                     f"    result = [x for x in items if x.{b} > limit]\n"
                     f"    return sorted(result, key=lambda r: r.{a})[:{rng.randint(1, 9)}]\n\n")
        elif cls == "json":
            piece = json.dumps({"id": i, "name": f"{rng.choice(WORDS)}-{rng.randint(100, 999)}",
                                "status": rng.choice(["ok", "failed", "retry"]),
                                "latency_ms": rng.randint(1, 900), "tags": [rng.choice(WORDS), rng.choice(WORDS)]}) + ",\n"
        else:
            piece = " ".join(rng.choice(WORDS) for _ in range(rng.randint(8, 16))).capitalize() + ". "
        out.append(piece)
        size += len(piece)
        i += 1
    return "".join(out)


INSTRUCTIONS = {
    "code": "Above is Python. List the distinct function families you can see in one short list, then write one new helper in the same style.",
    "json": "Above are JSON records. Reply with the count of records whose status is 'failed' and the first three failed names.",
    "prose": "Above is filler text. Write a short summary of its vocabulary and say which three words appear most often.",
}


@dataclass(frozen=True)
class SpeedJob:
    id: str
    shape_tokens: int
    cls: str
    max_tokens: int
    messages: tuple
    weight: float


def build_speed_set() -> list[SpeedJob]:
    jobs = []
    for tokens, out, weight in SHAPES:
        for cls in CLASSES:
            body = _fill(cls, tokens, 1)
            msg = [{"role": "user", "content": body + "\n\n" + INSTRUCTIONS[cls]}]
            jobs.append(SpeedJob(f"{cls}-{tokens}", tokens, cls, out, tuple(map(lambda m: tuple(m.items()), msg)), weight))
    return jobs


def messages_of(job: SpeedJob) -> list[dict]:
    return [dict(m) for m in job.messages]


def speed_set_hash(jobs: list[SpeedJob] | None = None) -> str:
    jobs = jobs or build_speed_set()
    h = hashlib.sha256()
    for j in jobs:
        h.update(json.dumps([j.id, j.max_tokens, j.messages], sort_keys=True).encode())
    return h.hexdigest()


PROBE_PROMPTS = [  # 20 short prompts for the speculative byte-identity check at temperature 0
    f"Reply with exactly the next three items of this sequence and nothing else: {', '.join(str(i * (k + 2)) for i in range(1, 6))}"
    for k in range(7)
] + [
    f"Write one line of Python that returns the {w} of a list named xs." for w in
    ("sum", "maximum", "minimum", "length", "sorted copy", "reversed copy", "unique values")
] + [
    f"In one sentence, define the word '{w}' for a software engineer." for w in
    ("buffer", "cache", "socket", "ledger", "queue", "token")
]


@dataclass
class JobResult:
    job_id: str
    shape_tokens: int
    ok: bool
    n_in: int = 0
    n_out: int = 0
    ttft_s: float = 0.0
    total_s: float = 0.0
    pp_tps: float = 0.0
    tg_tps: float = 0.0
    finish_reason: str = ""
    error: str | None = None


def request_body(alias: str, messages: list[dict], max_tokens: int, sampler: dict | None = None,
                 extra: dict | None = None, seed: int = 1) -> dict:
    body = {"model": alias, "messages": messages, "max_tokens": max_tokens, "seed": seed,
            "cache_prompt": False, "temperature": 0}
    if sampler:
        body.update(sampler)
    if extra:
        body.update(extra)
    return body


def to_job_result(job_id: str, shape: int, r: ChatResult) -> JobResult:
    if not r.ok:
        return JobResult(job_id, shape, False, error=r.error)
    decode_s = max(r.total_s - r.ttft_s, 1e-9)
    tg = r.predicted_tps or ((r.completion_tokens - 1) / decode_s if r.completion_tokens > 1 else 0.0)
    pp = r.prompt_tps or (r.prompt_tokens / max(r.ttft_s, 1e-9))
    return JobResult(job_id, shape, True, r.prompt_tokens, r.completion_tokens, r.ttft_s, r.total_s, pp, tg,
                     r.finish_reason)


def run_pass(backend: Backend, url: str, alias: str, jobs: list[SpeedJob], rng: random.Random,
             on_request=None, mode_extra: dict | None = None) -> list[JobResult]:
    """One shuffled pass over the speed set at concurrency 1. `on_request` is the abort hook."""
    order = list(jobs)
    rng.shuffle(order)
    results = []
    for job in order:
        if on_request:
            on_request()
        body = request_body(alias, messages_of(job), job.max_tokens, extra=mode_extra)
        results.append(to_job_result(job.id, job.shape_tokens, backend.chat(url, body, timeout=900.0)))
    return results


def summarize(passes: list[list[JobResult]]) -> dict:
    """Median per shape over classes and passes; score is the weighted geomean of the
    nominal-output latency (prefill of the real prompt + decode of the shape's nominal
    output), so a config that merely answers shorter does not look faster."""
    flat = [r for p in passes for r in p]
    bad = [r for r in flat if not r.ok]
    if bad or not flat:
        return {"ok": False, "failures": [r.error for r in bad][:5]}
    nominal = {t: out for t, out, _ in SHAPES}
    weights = {t: w for t, _, w in SHAPES}
    shape_e2e, shape_raw = {}, {}
    for t in nominal:
        rs = [r for r in flat if r.shape_tokens == t]
        shape_e2e[t] = median([r.n_in / max(r.pp_tps, 1e-9) + nominal[t] / max(r.tg_tps, 1e-9) for r in rs])
        shape_raw[t] = median([r.total_s for r in rs])
    order = sorted(nominal)
    score = weighted_geomean([shape_e2e[t] for t in order], [weights[t] for t in order])
    big = [r.pp_tps for r in flat if r.shape_tokens >= 8000]
    return {
        "ok": True, "score": score, "shape_e2e_s": shape_e2e, "shape_raw_e2e_s": shape_raw,
        "pp_tps": median(big or [r.pp_tps for r in flat]), "tg_tps": median([r.tg_tps for r in flat]),
        "ttft_ms": median([r.ttft_s * 1000 for r in flat]), "n": len(flat),
        "tokens_out": sum(r.n_out for r in flat),
    }
