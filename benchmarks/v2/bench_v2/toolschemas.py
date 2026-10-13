"""Real tool schemas -> frozen snapshot -> tool-calling items and a strict scorer.

Schemas come from Hermes (`tools/*.py` module-level dict literals, read with `ast`,
never imported) and Guardian (`qwen-queue/local-worker-mcp.py` TOOLS list). No
customer data is involved; Carter approves which tools are in the snapshot.
"""

from __future__ import annotations

import ast
import glob
import hashlib
import json
import os
import random
import re

DEFAULT_ALLOWLIST = {
    "hermes": ["read_file", "write_file", "patch", "search_files", "memory", "web_search", "session_search",
               "skill_view", "process", "send_message"],
    "guardian": ["local_worker_capabilities", "local_worker_submit", "local_worker_status", "local_worker_cancel"],
}


def _literal_dicts(path: str):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (OSError, SyntaxError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError, MemoryError):
                continue
            yield value


def extract_hermes(hermes_dir: str, allow: list[str]) -> dict:
    out = {}
    for f in sorted(glob.glob(os.path.join(hermes_dir, "tools", "*.py"))):
        for v in _literal_dicts(f):
            if isinstance(v, dict) and "name" in v and "parameters" in v and v["name"] in allow:
                out[v["name"]] = {"type": "function", "function": {
                    "name": v["name"], "description": v.get("description", ""), "parameters": v["parameters"]},
                    "source": "hermes:" + os.path.relpath(f, hermes_dir).replace("\\", "/")}
    return out


def extract_guardian(mcp_path: str, allow: list[str]) -> dict:
    out = {}
    for v in _literal_dicts(mcp_path):
        if isinstance(v, list):
            for t in v:
                if isinstance(t, dict) and t.get("name") in allow and "inputSchema" in t:
                    out[t["name"]] = {"type": "function", "function": {
                        "name": t["name"], "description": t.get("description", ""), "parameters": t["inputSchema"]},
                        "source": "guardian:qwen-queue/local-worker-mcp.py"}
    return out


def freeze_snapshot(hermes_dir: str | None, mcp_path: str | None, allowlist: dict | None = None) -> dict:
    allow = allowlist or DEFAULT_ALLOWLIST
    tools = {}
    if hermes_dir:
        tools.update(extract_hermes(hermes_dir, allow["hermes"]))
    if mcp_path:
        tools.update(extract_guardian(mcp_path, allow["guardian"]))
    return {"tools": tools, "sha256": hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()}


# -- minimal JSON-schema validation (the subset these schemas use) --
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}


def validate(value, schema: dict, path: str = "$") -> list[str]:
    errs = []
    t = schema.get("type")
    if t:
        py = _TYPES.get(t)
        if py is None or not isinstance(value, py) or (t in ("integer", "number") and isinstance(value, bool)):
            return [f"{path}: expected {t}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: above maximum")
    if t == "object" or isinstance(value, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}: missing required {req}")
        for k, v in value.items():
            if k in props:
                errs += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected property {k}")
    if t == "array" and "items" in schema and isinstance(value, list):
        for i, v in enumerate(value):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


# -- scenario builders: (rng) -> prompt, expected args --
W = ["alpha", "bravo", "cedar", "delta", "ember", "falcon", "garnet", "harbor"]
EXT = ["py", "ts", "md", "json"]


def _path(r):
    return f"src/{r.choice(W)}/{r.choice(W)}.{r.choice(EXT)}"


def sc_read_file(r):
    p = _path(r)
    if r.random() < 0.5:
        return f"Open {p} so I can read it.", {"path": p}
    n = r.randint(5, 80)
    return f"Read only the first {n} lines of {p}.", {"path": p, "limit": n}


def sc_write_file(r):
    p = f"notes/{r.choice(W)}.txt"
    text = f"{r.choice(W)} {r.choice(W)} {r.randint(10, 99)}"
    return f"Create the file {p} containing exactly this text and nothing else: {text}", {"path": p, "content": text}


def sc_patch(r):
    p, old, new = _path(r), f"{r.choice(W)}_old", f"{r.choice(W)}_new"
    if r.random() < 0.5:
        return f"In {p}, replace every occurrence of '{old}' with '{new}'.", {"path": p, "old_string": old, "new_string": new, "replace_all": True}
    return f"In {p}, change the single string '{old}' to '{new}'.", {"path": p, "old_string": old, "new_string": new}


def sc_search_files(r):
    pat, d = f"{r.choice(W)}_{r.choice(W)}", f"src/{r.choice(W)}"
    if r.random() < 0.5:
        return f"Search the contents of files under {d} for the text '{pat}'.", {"pattern": pat, "path": d, "target": "content"}
    return f"List the files under {d} whose names match '*.{r.choice(EXT)}' (search by file name, not contents).", {"target": "files", "path": d}


def sc_memory(r):
    thing = f"{r.choice(W)} {r.choice(W)}"
    return f"Remember this about me for later: I prefer {thing}.", {"action": "add", "target": "user"}


def sc_web_search(r):
    q = f"{r.choice(W)} {r.choice(W)} wiring guide"
    n = r.randint(3, 8)
    return f"Search the web for \"{q}\" and return {n} results.", {"query": q, "limit": n}


def sc_session_search(r):
    q = r.choice(W)
    n = r.randint(2, 6)
    return f"Find earlier conversations that mention '{q}', newest first, at most {n}.", {"query": q, "sort": "newest", "limit": n}


def sc_skill_view(r):
    n = f"{r.choice(W)}-{r.choice(W)}"
    return f"Show me the skill called {n}.", {"name": n}


def sc_process(r):
    sid = f"proc_{r.randint(1000, 9999)}"
    return f"Kill the background process session {sid}.", {"action": "kill", "session_id": sid}


def sc_send_message(r):
    tgt, msg = f"#{r.choice(W)}", f"deploy window moved to {r.randint(1, 12)}pm"
    return f"Send the message '{msg}' to {tgt}.", {"action": "send", "target": tgt, "message": msg}


def sc_submit(r):
    ws = f"D:\\Workspace\\{r.choice(W)}"
    return (f"Submit a mechanical_execution job in the workspace {ws} to rename the helper functions to snake_case.",
            {"work_class": "mechanical_execution", "workspace": ws})


def _jobid(r):
    return "qj_" + "".join(r.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(r.randint(8, 14)))


def sc_status(r):
    j = _jobid(r)
    return f"What is the status of worker job {j}?", {"job_id": j}


def sc_cancel(r):
    j = _jobid(r)
    return f"Cancel the queued worker job {j}.", {"job_id": j}


def sc_caps(r):
    return "Which local worker capabilities does Guardian allow right now?", {}


SCENARIOS = {
    "read_file": sc_read_file, "write_file": sc_write_file, "patch": sc_patch, "search_files": sc_search_files,
    "memory": sc_memory, "web_search": sc_web_search, "session_search": sc_session_search, "skill_view": sc_skill_view,
    "process": sc_process, "send_message": sc_send_message, "local_worker_submit": sc_submit,
    "local_worker_status": sc_status, "local_worker_cancel": sc_cancel, "local_worker_capabilities": sc_caps,
}
NO_CALL_QUESTIONS = [("What is 12 times 12? Answer directly.", "144"), ("Which is longer, a kilometre or a mile? One word.", "kilometre"),
                     ("Spell the word 'bridge' backwards.", "egdirb"), ("How many days are in a leap year?", "366")]


def _offer(r, snap: dict, must: list[str], k: int = 5) -> list[dict]:
    names = list(snap["tools"])
    pool = [n for n in names if n not in must]
    r.shuffle(pool)
    chosen = must + pool[: max(0, k - len(must))]
    r.shuffle(chosen)
    return [{"type": "function", "function": snap["tools"][n]["function"]} for n in chosen]


def _asst_call(call_id: str, name: str, args: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def gen_tool_items(n: int, snap: dict) -> list[dict]:
    names = [t for t in SCENARIOS if t in snap["tools"]]
    if not names:
        return []
    kinds = ["single"] * 8 + ["parallel"] * 3 + ["no_call"] * 3 + ["multi_turn"] * 4 + ["bad_args"] * 2
    out = []
    for i in range(n):
        r = random.Random(int(hashlib.sha256(f"tool|{i}".encode()).hexdigest()[:12], 16))
        kind = kinds[i % len(kinds)]
        t1 = names[(i * 3 + r.randint(0, 2)) % len(names)]
        item = {"id": f"tool-{i:04d}", "category": "tool", "kind": kind, "max_tokens": 600}
        if kind == "single":
            prompt, args = SCENARIOS[t1](r)
            item.update(messages=[{"role": "user", "content": prompt}], tools=_offer(r, snap, [t1]),
                        scorer={"type": "tool", "calls": [{"name": t1, "args": args}]})
        elif kind == "parallel":
            t2 = r.choice([t for t in names if t != t1])
            p1, a1 = SCENARIOS[t1](r)
            p2, a2 = SCENARIOS[t2](r)
            item.update(messages=[{"role": "user", "content": f"Do both of these now: (1) {p1} (2) {p2}"}],
                        tools=_offer(r, snap, [t1, t2]),
                        scorer={"type": "tool", "calls": [{"name": t1, "args": a1}, {"name": t2, "args": a2}], "any_order": True})
        elif kind == "no_call":
            q, ans = r.choice(NO_CALL_QUESTIONS)
            item.update(messages=[{"role": "user", "content": q}], tools=_offer(r, snap, [t1]),
                        scorer={"type": "tool", "calls": [], "answer_contains": ans})
        elif kind == "multi_turn":
            # earlier call + its result are in the history; the model must make the follow-up call
            p = _path(r)
            hist = [{"role": "user", "content": f"Find the file named {p.split('/')[-1]} and then open it."},
                    _asst_call("call_1", "search_files", {"pattern": p.split('/')[-1], "target": "files"}),
                    {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"matches": [p]})}]
            tools = _offer(r, snap, ["read_file", "search_files"]) if "read_file" in snap["tools"] and "search_files" in snap["tools"] else _offer(r, snap, [t1])
            if "read_file" in snap["tools"]:
                item.update(messages=hist, tools=tools, scorer={"type": "tool", "calls": [{"name": "read_file", "args": {"path": p}}]})
            else:
                continue
        else:  # bad_args: the previous call was rejected; the model must correct it
            if "read_file" not in snap["tools"]:
                continue
            p = _path(r)
            hist = [{"role": "user", "content": f"Read {p}."},
                    _asst_call("call_1", "read_file", {"file": p}),
                    {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"error": "Missing required argument: path"})}]
            item.update(messages=hist, tools=_offer(r, snap, ["read_file"]),
                        scorer={"type": "tool", "calls": [{"name": "read_file", "args": {"path": p}}]})
        out.append(item)
    for k, it in enumerate(out):  # stable ids even if some kinds were skipped
        it["id"] = f"tool-{k:04d}"
    return out


def _parse_args(raw) -> tuple[dict | None, str]:
    if isinstance(raw, dict):
        return raw, ""
    try:
        v = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"invalid JSON arguments: {exc}"
    return (v, "") if isinstance(v, dict) else (None, "arguments are not an object")


def score_tool(tool_calls: list[dict], text: str, spec: dict, offered: list[dict]) -> tuple[bool, str, dict]:
    """Returns (passed, detail, extra). extra['valid'] is strict schema validity of every call made."""
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in offered}
    valid, problems = True, []
    parsed = []
    for c in tool_calls:
        name = c.get("name") or (c.get("function") or {}).get("name", "")
        raw = c.get("arguments") if "arguments" in c else (c.get("function") or {}).get("arguments")
        args, err = _parse_args(raw)
        if args is None or name not in schemas:
            valid = False
            problems.append(err or f"unknown tool {name}")
            parsed.append((name, {}))
            continue
        errs = validate(args, schemas[name])
        if errs:
            valid = False
            problems += errs[:2]
        parsed.append((name, args))
    extra = {"valid": valid, "n_calls": len(parsed)}
    want = spec["calls"]
    if not want:
        ok = not parsed and (spec.get("answer_contains", "").lower() in (text or "").lower())
        return ok, "no call, answered directly" if ok else f"expected no tool call, got {[p[0] for p in parsed]}", extra
    if len(parsed) != len(want):
        return False, f"expected {len(want)} call(s), got {len(parsed)}", extra
    pool = list(parsed)
    for w in want:
        hit = None
        for cand in pool:
            if cand[0] == w["name"] and all(cand[1].get(k) == v for k, v in w["args"].items()):
                hit = cand
                break
            if not spec.get("any_order"):
                break
        if hit is None:
            return False, f"no match for {w['name']} {w['args']}; got {parsed}", extra
        pool.remove(hit)
    return valid, ("ok" if valid else "; ".join(problems[:3])), extra
