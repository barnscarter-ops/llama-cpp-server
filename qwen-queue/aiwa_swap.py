"""PR6: manager-owned AIWA swap via CT 210 scripts after WORKBOARD announce.

Single rule (PLAN.md "Board-unfreeze / swap ownership"): while
FLEET_SWAP_OWNER is false the Board console owns the SSH swap and
POST /__guardian/swap answers 409 swap_board_owns_ssh. When true, the
guardian announces on WORKBOARD.md (fail closed), SSHes the Proxmox host
(.12, NOT Board's freeze-bug .230), classifies the script output, then
polls AIWA /v1/models until the requested occupant is serving.

A live Board console port (17890/17891) is a warn-only hint; it must
never refuse a swap. In-process swap queue capacity is 1 (reject-second)
— the job SQLite queue is not involved.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from fleet_router import SERVING_IDS, guardian_error, occupant_cache

log = logging.getLogger("guardian")

WORKBOARD_DEFAULT_PATH = r"C:\Workspace\Active\brain\WORKBOARD.md"
WORKSTREAM = "690 GPU swap"          # Board WorkboardAnnounce.Workstream — do not rename
SSH_KEY = r"C:\Users\carte\.ssh\id_ed25519_proxmox"
SSH_HOST = "root@192.168.1.12"       # .12; Board's freeze bug was on .230
CT = "210"
SWAP_SCRIPTS = {"clerk": "swap-nemo-clerk.sh", "consult": "swap-qwen-consult.sh"}
CONSOLE_PORTS = (17890, 17891)
SWAP_POLL_S = 2.0
SWAP_POLL_TIMEOUT_S = 360.0
SWAP_ETA_TEXT = "~1 min"


def swap_owner_enabled() -> bool:
    return os.environ.get("FLEET_SWAP_OWNER", "false").strip().lower() in {
        "true",
        "1",
        "yes",
    }


def workboard_path() -> str:
    return os.environ.get("WORKBOARD_PATH", WORKBOARD_DEFAULT_PATH)


# ─────────────────────────────────────────────────────────────────────────────
#  In-process swap queue — capacity 1, reject-second.
# ─────────────────────────────────────────────────────────────────────────────

class SwapSlot:
    """Boolean try-lock: the second concurrent swap is rejected, never queued."""

    def __init__(self) -> None:
        self.busy = False

    def __enter__(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        return True

    def __exit__(self, *exc) -> None:
        self.busy = False


swap_slot = SwapSlot()


# ─────────────────────────────────────────────────────────────────────────────
#  Console-port warn (hint only — must never refuse)
# ─────────────────────────────────────────────────────────────────────────────

async def console_port_open(port: int) -> bool:
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
        await writer.wait_closed()
        return True
    except OSError:
        return False


async def warn_if_console_up() -> None:
    for port in CONSOLE_PORTS:
        if await console_port_open(port):
            log.warning(
                f"Board console port 127.0.0.1:{port} is up during a manager-owned "
                f"swap — hint only; confirm Board SSH (SwapInvoker) is gone."
            )


# ─────────────────────────────────────────────────────────────────────────────
#  WORKBOARD announce — port of Board WorkboardAnnounce (same row shape).
# ─────────────────────────────────────────────────────────────────────────────

def _format_pair(frm: str, to: str) -> str:
    return f"{frm}→{to}"


def build_row(frm: str, to: str, now: datetime) -> str:
    stamp = now.strftime("%Y-%m-%d %H:%M")
    return (
        f"| {WORKSTREAM} | Local LLM Board | CT 210 `/opt/llama` | "
        f"**ANNOUNCE** {_format_pair(frm, to)} (ETA {SWAP_ETA_TEXT}) {stamp} |"
    )


def _is_separator(line: str) -> bool:
    return line.startswith("|") and "---" in line


def _first_cell(line: str) -> str:
    parts = line.split("|")
    return parts[1].strip() if len(parts) > 1 else ""


def upsert_row(text: str, frm: str, to: str, now: datetime) -> str:
    """Upsert the WORKSTREAM row into the workboard table (Board-compatible)."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    row = build_row(frm, to, now)
    header = next(
        (i for i, l in enumerate(lines) if l.startswith("|") and "workstream" in l.lower()),
        -1,
    )
    if header < 0 or header + 1 >= len(lines) or not _is_separator(lines[header + 1]):
        if lines and lines[-1]:
            lines.append("")
        lines.append("| Workstream | Owner agent | Where | Status (updated) |")
        lines.append("|---|---|---|---|")
        lines.append(row)
        return newline.join(lines)
    first_data = header + 2
    for i in range(first_data, len(lines)):
        if not lines[i].startswith("|"):
            break
        if _first_cell(lines[i]).lower() == WORKSTREAM.lower():
            lines[i] = row
            return newline.join(lines)
    lines.insert(first_data, row)
    return newline.join(lines)


def announce_workboard(path: str, to: str) -> bool:
    """Fail closed: missing file or failed write → False, caller must not swap."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        text = p.read_text(encoding="utf-8")
        frm = "consult" if to == "clerk" else "clerk"
        p.write_text(upsert_row(text, frm, to, datetime.now()), encoding="utf-8")
        return True
    except OSError as exc:
        log.warning(f"WORKBOARD announce failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  SSH + classification (mocked in tests; never live-swapped from a session)
# ─────────────────────────────────────────────────────────────────────────────

async def run_swap_ssh(to: str) -> tuple[int, str]:
    """SSH to the Proxmox host and run the CT 210 swap script. Returns (rc, combined)."""
    remote = f"lxc-attach -n {CT} -- /opt/llama/{SWAP_SCRIPTS[to]}"
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-i", SSH_KEY, SSH_HOST, remote,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, (out or b"").decode(errors="replace")


def classify_ssh(exit_code: int, combined: str) -> str | None:
    """Map (rc, combined stdout/stderr) → error code, or None on success.

    Order matters: the CT 210 script's flock guard prints
    "swap in progress" + the lock path when it loses the mutex.
    """
    text = combined.lower()
    if exit_code != 0 and "swap in progress" in text and "lock" in text:
        return "aiwa_busy"
    if "no such file" in text or "not executable" in text:
        return "swap_scripts_missing"
    if exit_code != 0 and any(
        marker in text
        for marker in (
            "connection refused",
            "connection timed out",
            "permission denied",
            "auth",
            "host unreachable",
            "lxc-attach:",
            "could not resolve hostname",
        )
    ):
        return "swap_ssh_failed"
    if exit_code != 0:
        return "swap_ssh_failed"
    return None


async def wait_for_occupant(client, to: str, timeout: float = SWAP_POLL_TIMEOUT_S) -> bool:
    """Poll AIWA /v1/models (fleet_router occupant probe) until `to` is serving."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        occupant, _model_id, _reachable = await occupant_cache.get(client)
        if occupant == to:
            return True
        await asyncio.sleep(SWAP_POLL_S)
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  Swap handler body (auth is enforced by the guardian route wrapper)
# ─────────────────────────────────────────────────────────────────────────────

async def perform_swap(to: str, client) -> object:
    """Run one swap. Returns an aiohttp web.Response (guardian error shape)."""
    if to not in SWAP_SCRIPTS:
        return guardian_error(
            f"'to' must be one of {sorted(SWAP_SCRIPTS)}, got '{to}'.",
            "invalid_swap_target",
            400,
        )

    if not swap_owner_enabled():
        return guardian_error(
            "FLEET_SWAP_OWNER is false: the Board console owns the AIWA SSH swap.",
            "swap_board_owns_ssh",
            409,
        )

    await warn_if_console_up()

    if not swap_slot.__enter__():
        return guardian_error(
            "Another swap is already in flight.", "aiwa_busy", 409
        )
    try:
        path = workboard_path()
        if not announce_workboard(path, to):
            return guardian_error(
                f"WORKBOARD missing or unwritable at {path}; refusing to swap.",
                "workboard_missing",
                503,
            )

        exit_code, combined = await run_swap_ssh(to)
        code = classify_ssh(exit_code, combined)
        if code == "aiwa_busy":
            return guardian_error(
                f"CT 210 swap mutex is held: {combined.strip()[:200]}",
                "aiwa_busy",
                409,
            )
        if code == "swap_scripts_missing":
            return guardian_error(
                f"Swap script missing on CT 210: {combined.strip()[:200]}",
                "swap_scripts_missing",
                502,
            )
        if code == "swap_ssh_failed":
            return guardian_error(
                f"SSH swap to {SSH_HOST} failed: {combined.strip()[:200]}",
                "swap_ssh_failed",
                502,
            )

        if not await wait_for_occupant(client, to):
            return guardian_error(
                f"AIWA did not serve {SERVING_IDS[to]} within {SWAP_POLL_TIMEOUT_S:.0f}s.",
                "aiwa_unreachable",
                502,
            )
        from aiohttp import web
        return web.json_response(
            {"status": "ok", "occupant": to, "model_id": SERVING_IDS[to]}
        )
    finally:
        swap_slot.__exit__()
