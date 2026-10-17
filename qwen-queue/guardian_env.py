"""Secrets for the Guardian process come from a gitignored env file, never from ecosystem.config.cjs.

Real environment variables win over the file, so a manual override still works. Pure parsing
plus a startup self-check that names what is missing for the flags that are on.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, MutableMapping

DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".guardian.env"
TRUE = {"true", "1", "yes"}


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load(env: MutableMapping[str, str] = os.environ, path: str | os.PathLike | None = None) -> list[str]:
    """Fills unset variables from the file; returns the names it set (never values)."""
    target = Path(path or env.get("GUARDIAN_ENV_FILE") or DEFAULT_ENV_FILE)
    try:
        values = parse(target.read_text(encoding="utf-8"))
    except OSError:
        return []
    applied = [k for k in values if not env.get(k)]
    for key in applied:
        env[key] = values[key]
    return applied


def turn_on_gaps(env: Mapping[str, str] = os.environ) -> list[str]:
    """Settings that are missing for the features that are switched on. Names only."""
    on = lambda name: env.get(name, "").strip().lower() in TRUE  # noqa: E731
    gaps: list[str] = []
    if on("GUARDIAN_QWEN_APPROVAL") and not env.get("GUARDIAN_QWEN_APPROVAL_TOKEN"):
        gaps.append("GUARDIAN_QWEN_APPROVAL_TOKEN unset: Qwen can never be approved")
    if on("GUARDIAN_QUEUE_ALLOW_REMOTE") and not env.get("GUARDIAN_QUEUE_TOKEN"):
        gaps.append("GUARDIAN_QUEUE_TOKEN unset: remote callers are refused")
    if on("GUARDIAN_CLOUD") and not on("GUARDIAN_TASK_API"):
        gaps.append("GUARDIAN_CLOUD on without GUARDIAN_TASK_API: only clearance-header calls can reach cloud")
    if on("GUARDIAN_CLOUD") and not on("FLEET_ROUTER"):
        gaps.append("GUARDIAN_CLOUD on with FLEET_ROUTER off: direct cloud calls are not routed (jobs are still gated)")
    return gaps
