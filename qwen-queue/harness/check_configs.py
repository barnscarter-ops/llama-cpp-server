"""Parse live Grok TOML and Hermes YAML; confirm guardian MCP rows without dumping secrets."""
from __future__ import annotations

import tomllib
from pathlib import Path

GROK = Path.home() / ".grok" / "config.toml"
HERMES = Path.home() / "AppData" / "Local" / "hermes" / "config.yaml"
ADAPTER = r"D:\Workspace\Infrastructure\llama-cpp-server\qwen-queue\local-worker-mcp.py"
PY = r"C:\Users\carte\AppData\Local\Programs\Python\Python312\python.exe"


def main() -> int:
    grok = tomllib.loads(GROK.read_text(encoding="utf-8"))
    grok_mcp = grok.get("mcp_servers", {}).get("guardian_local_worker") or {}
    if grok.get("models", {}).get("default") != "grok-4.6":
        print("FAIL grok default model changed")
        return 1
    if grok_mcp.get("command") != PY or grok_mcp.get("args") != [ADAPTER, "--source", "grok"]:
        print("FAIL grok guardian MCP fragment")
        return 1
    print("ok grok toml guardian_local_worker source=grok default=grok-4.6")

    hermes_text = HERMES.read_text(encoding="utf-8")
    if "default: glm-5.3" not in hermes_text.split("mcp_servers:", 1)[0]:
        print("FAIL hermes default model changed")
        return 1
    needed = (
        "guardian_local_worker:",
        PY,
        ADAPTER,
        "- --source",
        "- hermes",
        "enabled: true",
    )
    block_start = hermes_text.find("guardian_local_worker:")
    if block_start < 0:
        print("FAIL hermes missing guardian_local_worker")
        return 1
    block = hermes_text[block_start:block_start + 800]
    for snippet in needed:
        if snippet not in block and snippet not in hermes_text:
            print("FAIL hermes guardian MCP fragment missing", snippet)
            return 1
    print("ok hermes yaml guardian_local_worker source=hermes default=glm-5.3 enabled=true")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
