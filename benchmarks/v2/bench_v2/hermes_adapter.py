"""Wrapper for the Hermes worker bench (frozen v1.2 tasks; stays comparable to 2026-09-11).

Not configured by default: the bench's run.py calls the Guardian front door
(127.0.0.1:8080), which would wake the production seat. Enable it only after
confirming run.py can be pointed at the bench port; the command template below
must then print one JSON object {"tasks": {"<task>": true|false}} on stdout.
"""

from __future__ import annotations

import json

from .backend import Backend


class HermesBenchAdapter:
    def __init__(self, backend: Backend, cfg: dict | None) -> None:
        self.b = backend
        self.cfg = cfg or {}

    @property
    def configured(self) -> bool:
        return bool(self.cfg.get("cmd"))

    def run(self, alias: str, base_url: str, run_id: str) -> dict:
        if not self.configured:
            return {"configured": False, "tasks": {}}
        if "127.0.0.1:8080" in base_url or "127.0.0.1:8081" in base_url:
            raise ValueError("refusing: the Hermes bench must target the bench port, not Guardian")
        cmd = [c.replace("{alias}", alias).replace("{base_url}", base_url).replace("{run_id}", run_id) for c in self.cfg["cmd"]]
        r = self.b.run(cmd, timeout=float(self.cfg.get("timeout_s", 3600)))
        if r.rc != 0:
            return {"configured": True, "error": r.stderr[-200:], "tasks": {}}
        try:
            tasks = json.loads(r.stdout.strip().splitlines()[-1])["tasks"]
        except (ValueError, KeyError, IndexError):
            return {"configured": True, "error": "unparseable output", "tasks": {}}
        return {"configured": True, "tasks": {k: bool(v) for k, v in tasks.items()}}
