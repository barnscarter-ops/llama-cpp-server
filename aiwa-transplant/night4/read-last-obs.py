import json
from pathlib import Path

lines = Path("/home/hermes/.hermes/state/observations.jsonl").read_text().splitlines()
last = json.loads(lines[-1])
print("ts", last.get("ts"))
print("health", last["sensors"]["health"])
for k in ("pm2", "mcc", "workflows", "scheduled-tasks", "services", "local-models"):
    s = last["sensors"].get(k, {})
    print(k, {x: s.get(x) for x in ("ok", "error", "status_code")})
print("hcp_mcp", last["sensors"].get("hcp_mcp"))
