#!/usr/bin/env python3
"""Write data/tool_schemas.frozen.json from the real Hermes and Guardian tool definitions.

Usage: python extract_tool_schemas.py --hermes ../../hermes-agent --guardian ../../qwen-queue/local-worker-mcp.py
Reads source files with ast only; imports nothing and contacts nothing. Carter approves the snapshot.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_v2 import toolschemas  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes", help="path to a hermes-agent checkout")
    ap.add_argument("--guardian", help="path to qwen-queue/local-worker-mcp.py")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "data" / "tool_schemas.frozen.json"))
    a = ap.parse_args()
    snap = toolschemas.freeze_snapshot(a.hermes, a.guardian)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(snap, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(snap['tools'])} tools -> {a.out} (sha256 {snap['sha256'][:12]})")
    for name, t in sorted(snap["tools"].items()):
        print(f"  {name}  [{t['source']}]")
    return 0 if snap["tools"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
