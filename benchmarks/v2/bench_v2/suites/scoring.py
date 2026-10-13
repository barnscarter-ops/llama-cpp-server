"""Deterministic scorers. A scorer returns (passed, detail). Rubric items are never auto-scored."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

THINK = re.compile(r"<think>.*?</think>", re.S)
FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)
ANSWER = re.compile(r"ANSWER:\s*(.+)", re.I)
CODE_TIMEOUT_S = 10


def clean(text: str) -> str:
    """Drop inline reasoning blocks; the answer is whatever follows."""
    text = THINK.sub("", text)
    if "</think>" in text:  # opening tag was in the prompt template
        text = text.split("</think>", 1)[1]
    return text.strip()


def last_fence(text: str) -> str | None:
    blocks = FENCE.findall(text)
    return blocks[-1] if blocks else None


def extract_code(text: str) -> str:
    text = clean(text)
    return last_fence(text) or text


def extract_answer(text: str) -> str | None:
    found = ANSWER.findall(clean(text))
    return found[-1].strip().strip("`*. ") if found else None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def score_exact(text: str, spec: dict) -> tuple[bool, str]:
    ans = extract_answer(text)
    if ans is None:
        return False, "no ANSWER: line"
    want = [str(a) for a in spec["answers"]]
    if spec.get("numeric"):
        try:
            return (any(abs(float(ans.replace(",", "")) - float(w)) < 1e-6 for w in want), f"got {ans}")
        except ValueError:
            return False, f"not numeric: {ans}"
    return (any(_norm(ans) == _norm(w) for w in want), f"got {ans}")


def score_exact_text(text: str, spec: dict) -> tuple[bool, str]:
    got = last_fence(clean(text))
    got = (got if got is not None else clean(text)).strip("\n")
    want = spec["expected"].strip("\n")
    if spec.get("strip_trailing_ws", True):
        got = "\n".join(l.rstrip() for l in got.splitlines())
        want = "\n".join(l.rstrip() for l in want.splitlines())
    return got.strip() == want.strip(), "match" if got.strip() == want.strip() else "text differs"


def score_json(text: str, spec: dict) -> tuple[bool, str]:
    body = last_fence(clean(text)) or clean(text)
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=-1)
    if start < 0:
        return False, "no JSON"
    try:
        got, _ = json.JSONDecoder().raw_decode(body[start:])
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"
    return got == spec["expected"], "match" if got == spec["expected"] else "JSON differs"


def score_needle(text: str, spec: dict) -> tuple[bool, str]:
    body = clean(text)
    missing = [n for n in spec["needles"] if n not in body]
    return not missing, f"missing {missing}" if missing else "all found"


def score_python(text: str, spec: dict) -> tuple[bool, str]:
    """Run the model's code plus tests in an isolated interpreter with a hard timeout."""
    code = extract_code(text)
    program = code + "\n\n" + spec["tests"] + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "case.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(program)
        try:
            p = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True,
                               timeout=CODE_TIMEOUT_S, cwd=tmp)
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if p.returncode == 0:
        return True, "tests passed"
    return False, (p.stderr.strip().splitlines() or ["failed"])[-1][:160]


# -- IFEval-style programmatic constraints --
def _check(c: dict, body: str) -> bool:
    t = c["type"]
    words = re.findall(r"\b\w+\b", body)
    if t == "min_words":
        return len(words) >= c["n"]
    if t == "max_words":
        return len(words) <= c["n"]
    if t == "no_commas":
        return "," not in body
    if t == "all_lowercase":
        return body == body.lower()
    if t == "all_uppercase":
        return body == body.upper()
    if t == "keywords":
        return all(k.lower() in body.lower() for k in c["words"])
    if t == "forbidden":
        return not any(re.search(rf"\b{re.escape(w)}\b", body, re.I) for w in c["words"])
    if t == "bullets":
        return len(re.findall(r"^\s*[-*] ", body, re.M)) == c["n"]
    if t == "paragraphs":
        return len([p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]) == c["n"]
    if t == "ends_with":
        return body.strip().endswith(c["text"])
    if t == "starts_with":
        return body.strip().startswith(c["text"])
    if t == "json_only":
        try:
            json.loads(last_fence(body) or body)
            return True
        except json.JSONDecodeError:
            return False
    if t == "title_brackets":
        return bool(re.search(r"<<[^>\n]+>>", body))
    if t == "n_sentences":
        return len([s for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s]) == c["n"]
    if t == "contains_n_placeholders":
        return len(re.findall(r"\[[^\]\n]+\]", body)) >= c["n"]
    raise ValueError(f"unknown constraint {t}")


def score_ifeval(text: str, spec: dict) -> tuple[bool, str]:
    body = clean(text)
    failed = [c["type"] for c in spec["constraints"] if not _check(c, body)]
    return not failed, "all constraints met" if not failed else f"failed {failed}"


SCORERS = {"exact": score_exact, "exact_text": score_exact_text, "json": score_json,
           "needle": score_needle, "python": score_python, "ifeval": score_ifeval}
