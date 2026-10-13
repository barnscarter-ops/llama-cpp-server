"""Build the per-role 300-item mixes, hash them, freeze them, refuse drift.

Mixes are protocol 17.A. Pools that need Carter's material (Maverick tasks, the
Hermes worker bench) are reported as shortfalls, never padded with look-alikes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from .. import speed
from ..errors import FrozenSuiteMismatch
from . import generators as g

HERE = Path(__file__).resolve().parent.parent.parent  # benchmarks/v2
FROZEN_DIR = HERE / "frozen"
STRIP = ("reference", "buggy")

MIXES = {
    "worker_bee": [("ifeval", 45), ("clerk", 45), ("tool", 60), ("code_easy", 60), ("longctx", 40), ("hermes", 30), ("reasoning", 20)],
    "fast_executor": [("code_easy", 40), ("code_fix", 45), ("maverick", 15), ("tool", 60), ("clerk", 50), ("ifeval", 40),
                      ("hermes", 30), ("longctx", 20)],
    "slow_reasoner": [("analysis", 35), ("code_fix", 35), ("reasoning", 60), ("code_hard", 40), ("maverick", 20),
                      ("longctx", 40), ("tool", 40), ("ifeval", 30)],
}
EXTERNAL = {"maverick", "hermes"}
for _role, _mix in MIXES.items():
    assert sum(n for _, n in _mix) == 300, _role


def _bench_coding_items() -> list[dict]:
    path = HERE.parent / "bench-coding.py"
    spec = importlib.util.spec_from_file_location("bench_coding_existing", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # defines data and functions only; main() is guarded
    return [{"id": f"code_easy-bc{i:02d}", "category": "code_easy", "template": "bench-coding", "max_tokens": 900,
             "messages": [{"role": "user", "content": he["prompt"]}],
             "scorer": {"type": "python", "tests": he["tests"]}} for i, he in enumerate(mod.HUMANEVAL)]


def load_jsonl_items(path: str | None, category: str, scorer_type: str = "python") -> list[dict]:
    """HumanEval/MBPP+ style {task_id,prompt,test,entry_point} or already-shaped items."""
    if not path:
        return []
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        if "messages" in row and "scorer" in row:
            row.setdefault("category", category)
            row.setdefault("id", f"{category}-ext{n:04d}")
            out.append(row)
            continue
        tests = row["test"] + (f"\ncheck({row['entry_point']})" if row.get("entry_point") else "")
        out.append({"id": f"{category}-ext{n:04d}", "category": category, "max_tokens": 1500,
                    "messages": [{"role": "user", "content": row["prompt"] + "\n\nReturn the complete function in one code block."}],
                    "scorer": {"type": scorer_type, "tests": tests}})
    return out


def pools(snap: dict | None, external: dict | None = None) -> dict[str, list[dict]]:
    external = external or {}
    need = {c: max((n for mix in MIXES.values() for cat, n in mix if cat == c), default=0) for c in
            {c for mix in MIXES.values() for c, _ in mix}}
    ext_he = load_jsonl_items(external.get("humaneval"), "code_easy")
    easy = _bench_coding_items() + ext_he
    easy += g.gen_code_easy(max(0, need["code_easy"] - len(easy)))
    out = {
        "ifeval": g.gen_ifeval(need["ifeval"]), "clerk": g.gen_clerk(need["clerk"]),
        "reasoning": g.gen_reasoning(need["reasoning"]), "analysis": g.gen_logs(need["analysis"]),
        "code_easy": easy, "code_fix": g.gen_code_fix(need["code_fix"]), "code_hard": g.gen_code_hard(need["code_hard"]),
        "longctx": g.gen_longctx(need["longctx"]),
        "tool": __import__("bench_v2.toolschemas", fromlist=["x"]).gen_tool_items(need["tool"], snap) if snap else [],
        "maverick": load_jsonl_items(external.get("maverick"), "maverick"),
        "hermes": [],  # run through the Hermes worker-bench adapter, not generated here
    }
    return out


def _strip(it: dict) -> dict:
    return {k: v for k, v in it.items() if k not in STRIP}


def build_role(role: str, pool: dict[str, list[dict]]) -> tuple[list[dict], dict]:
    items, short = [], {}
    for cat, want in MIXES[role]:
        have = pool.get(cat, [])
        take = have[:want]
        items += [_strip(i) for i in take]
        if len(take) < want:
            short[cat] = {"wanted": want, "have": len(take),
                          "reason": "external pool not provided" if cat in EXTERNAL else "generator produced fewer items"}
    return items, short


def hash_items(items: list[dict]) -> str:
    h = hashlib.sha256()
    for it in items:
        h.update(json.dumps(it, sort_keys=True, separators=(",", ":")).encode())
    return h.hexdigest()


def sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def build_all(snap: dict | None, external: dict | None = None, data_dir: Path | None = None) -> dict:
    data_dir = data_dir or HERE / "data"
    pool = pools(snap, external)
    roles = {}
    for role in MIXES:
        items, short = build_role(role, pool)
        roles[role] = {"items": items, "hash": hash_items(items), "n": len(items), "shortfall": short,
                       "categories": {c: sum(1 for i in items if i["category"] == c) for c, _ in MIXES[role]}}
    return {"roles": roles, "speed_set_hash": speed.speed_set_hash(),
            "tool_schemas_sha256": (snap or {}).get("sha256"),
            "guard_slice_sha256": sha256_file(data_dir / "guard_slice.txt"),
            "external": {k: (sha256_file(Path(v)) if v else None) for k, v in (external or {}).items()}}


def freeze(snap: dict | None, external: dict | None = None, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or FROZEN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    built = build_all(snap, external)
    manifest = {k: v for k, v in built.items() if k != "roles"}
    manifest["roles"] = {}
    for role, r in built["roles"].items():
        (out_dir / f"{role}.jsonl").write_text("\n".join(json.dumps(i, sort_keys=True) for i in r["items"]) + "\n", encoding="utf-8")
        manifest["roles"][role] = {k: r[k] for k in ("hash", "n", "shortfall", "categories")}
    (out_dir / "FROZEN.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def load_frozen(role: str, out_dir: Path | None = None, snap: dict | None = None, external: dict | None = None) -> list[dict]:
    """Return the role's items after proving they still match FROZEN.json and what the code would rebuild."""
    out_dir = out_dir or FROZEN_DIR
    fp = out_dir / "FROZEN.json"
    if not fp.exists():
        raise FrozenSuiteMismatch("no FROZEN.json: run the freeze stage first")
    manifest = json.loads(fp.read_text(encoding="utf-8"))
    path = out_dir / f"{role}.jsonl"
    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if hash_items(items) != manifest["roles"][role]["hash"]:
        raise FrozenSuiteMismatch(f"{role}: items on disk differ from the frozen hash")
    if speed.speed_set_hash() != manifest["speed_set_hash"]:
        raise FrozenSuiteMismatch("speed set changed since the freeze")
    if snap is not None and snap.get("sha256") != manifest.get("tool_schemas_sha256"):
        raise FrozenSuiteMismatch("tool schema snapshot changed since the freeze")
    for k, v in (external or {}).items():
        if v and sha256_file(Path(v)) != manifest.get("external", {}).get(k):
            raise FrozenSuiteMismatch(f"external pool {k} changed since the freeze")
    return items


def verify_rebuild(snap: dict | None, external: dict | None = None, out_dir: Path | None = None) -> list[str]:
    """Rebuild from code and compare with FROZEN.json: catches generator edits after the freeze."""
    out_dir = out_dir or FROZEN_DIR
    manifest = json.loads((out_dir / "FROZEN.json").read_text(encoding="utf-8"))
    built = build_all(snap, external)
    problems = []
    for role, r in built["roles"].items():
        if r["hash"] != manifest["roles"][role]["hash"]:
            problems.append(f"{role}: generators no longer rebuild the frozen items")
    return problems
