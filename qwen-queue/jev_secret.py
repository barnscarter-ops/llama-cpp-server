"""Jev credential loading (port of chief-jev-secret.ts + chief-gsm-gcloud.ts).

Order: GUARDIAN_JEV_API_KEY env (tests and break-glass) else one explicitly configured
Secret Manager version read with the operator's existing gcloud login. No login, no
cache, no file write. Failures raise without echoing provider output.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from typing import Awaitable, Callable

from jev_client import KeyLoader

_RESOURCE = re.compile(r"^projects/([a-zA-Z0-9][a-zA-Z0-9-]{0,62})/secrets/([a-zA-Z0-9_-]{1,255})/versions/([1-9][0-9]*|latest)$")
_PRINCIPAL = re.compile(r"^[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# (resource_name) -> (active principal, payload)
GsmAccessor = Callable[[str], Awaitable[tuple[str, str]]]


class JevCredentialUnavailable(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"Jev credential unavailable: {code}")
        self.code = code


async def _run(argv: list[str], timeout_s: float) -> str:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,  # stderr can echo the resource
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except BaseException:
        proc.kill()
        raise
    if proc.returncode != 0:
        raise RuntimeError("gcloud failed")
    return out.decode("utf-8")


def gcloud_accessor(gcloud: str | None = None, timeout_s: float = 15.0) -> GsmAccessor:
    # Why: on Windows gcloud is a .cmd shim that exec cannot start directly; args are regex-validated below.
    shim = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c"] if sys.platform == "win32" and gcloud is None else []
    exe = gcloud or shutil.which("gcloud") or "gcloud"

    async def access(resource_name: str) -> tuple[str, str]:
        m = _RESOURCE.match(resource_name)
        if not m:
            raise ValueError("invalid reference")
        project, secret, version = m.groups()
        account = (await _run([*shim, exe, "config", "get-value", "account", "--quiet"], timeout_s)).strip()
        payload = (await _run(
            [*shim, exe, "secrets", "versions", "access", version, f"--secret={secret}", f"--project={project}", "--quiet"],
            timeout_s,
        )).rstrip("\r\n")
        return account, payload

    return access


def gsm_key_loader(resource_name: str | None, expected_principal: str | None, accessor: GsmAccessor | None = None) -> KeyLoader:
    if resource_name is None or expected_principal is None:
        raise JevCredentialUnavailable("reference_missing")
    if not _RESOURCE.match(resource_name) or not _PRINCIPAL.match(expected_principal) or len(expected_principal) > 320:
        raise JevCredentialUnavailable("reference_invalid")
    read = accessor or gcloud_accessor()

    async def load() -> str:
        try:
            principal, payload = await read(resource_name)
        except Exception:  # noqa: BLE001
            raise JevCredentialUnavailable("access_unavailable") from None
        # Why: a key read under another gcloud identity is not the one Carter approved.
        if principal != expected_principal:
            raise JevCredentialUnavailable("binding_mismatch")
        if not payload or len(payload) > 8192 or _CONTROL.search(payload):
            raise JevCredentialUnavailable("payload_invalid")
        return payload

    return load


def env_key_loader(var: str = "GUARDIAN_JEV_API_KEY") -> KeyLoader | None:
    if not os.environ.get(var, "").strip():
        return None

    async def load() -> str:
        return os.environ.get(var, "").strip()

    return load


def build_key_loader(accessor: GsmAccessor | None = None) -> KeyLoader:
    """Env override first; otherwise the configured GSM version. Raises if neither is configured."""
    override = env_key_loader()
    if override:
        return override
    return gsm_key_loader(
        os.environ.get("GUARDIAN_JEV_SECRET_REF"), os.environ.get("GUARDIAN_JEV_SECRET_PRINCIPAL"), accessor,
    )
