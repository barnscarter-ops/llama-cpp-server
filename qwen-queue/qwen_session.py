"""Approved-Qwen session: the one place Qwen may load on the R9700.

One state machine under the R9700 exclusive lock: stop admitting Nemotron work,
drain what is running (never kill it), consume the approval, load Qwen, run the
approved task, then reload Nemotron on success, failure or cancel, and only then
let the Nemotron queue resume. Leaves CONSULT_IDLE_RESTORE_S alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from aiohttp import web

import aiwa_swap
import fleet_router
import model_slots
from qwen_approvals import ApprovalError, ApprovalStore, qwen_approval_enabled

log = logging.getLogger("guardian")
T = TypeVar("T")

RELOAD_ATTEMPTS = 3
RELOAD_RETRY_S = 5.0
RELOAD_RETRY_LOOP_S = 60.0


class SessionError(Exception):
    def __init__(self, code: str, message: str, status: int, response: web.Response | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.response = code, message, status, response


@dataclass
class QwenState:
    session_active: bool = False
    reload_failed: bool = False
    sessions_run: int = 0
    reload_failed_at: float | None = None
    reload_retries: int = 0


state = QwenState()


def reset_state() -> None:
    state.session_active = state.reload_failed = False
    state.sessions_run = 0
    state.reload_failed_at = None
    state.reload_retries = 0


async def restore_resting_model(client) -> bool:
    """Put Nemotron back. Caller holds the R9700 exclusively. Retries a few times, then flags the failure."""
    for attempt in range(RELOAD_ATTEMPTS):
        fleet_router.occupant_cache.invalidate()
        occupant, _model_id, reachable = await fleet_router.occupant_cache.get(client)
        if reachable and occupant == "clerk":
            _reload_ok()
            return True
        resp = await aiwa_swap.swap_while_exclusive("clerk", client)
        if resp.status == 200:
            _reload_ok()
            return True
        log.error("nemotron reload attempt %d failed (HTTP %s)", attempt + 1, resp.status)
        if attempt + 1 < RELOAD_ATTEMPTS:
            await asyncio.sleep(RELOAD_RETRY_S)
    state.reload_failed = True
    state.reload_failed_at = state.reload_failed_at or time.time()
    log.error("nemotron reload FAILED; the R9700 is not on its resting model")
    return False


def _reload_ok() -> None:
    state.reload_failed = False
    state.reload_failed_at = None


async def _finish_even_if_cancelled(awaitable: Awaitable[T]) -> T:
    """Wait for awaitable to finish however many times we are cancelled, then re-raise the cancel.

    The R9700 lock must outlive the reload: releasing it mid-reload would let a Nemotron request land on a half-loaded model.
    """
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                result = task.result()
                break
    if cancelled:
        raise asyncio.CancelledError
    return result


async def retry_failed_reload(client) -> bool:
    """One retry pass for a failed Nemotron reload; no-op unless one is outstanding and no session runs."""
    if not state.reload_failed or state.session_active or client is None:
        return False
    state.reload_retries += 1
    try:
        async with model_slots.get_slots().exclusive("r9700", timeout=aiwa_swap.SWAP_DRAIN_TIMEOUT_S):
            return await _finish_even_if_cancelled(restore_resting_model(client))
    except model_slots.DrainTimeout:
        log.error("nemotron reload retry skipped: R9700 work did not drain")
        return False


async def reload_retrier(get_client: Callable[[], object], interval_s: float | None = None) -> None:
    """Keep trying until Nemotron is back; a failed reload must not be permanent."""
    while True:
        await asyncio.sleep(RELOAD_RETRY_LOOP_S if interval_s is None else interval_s)
        try:
            await retry_failed_reload(get_client())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("nemotron reload retry crashed")


def health() -> dict:
    return {
        "qwen_session_active": state.session_active,
        "nemotron_reload_failed": state.reload_failed,
        "nemotron_reload_failed_since": state.reload_failed_at,
        "nemotron_reload_retries": state.reload_retries,
    }


async def run_approved_qwen(
    *, approvals: ApprovalStore, approval_id: str | None, task_id: str, client, run: Callable[[], Awaitable[T]]
) -> T:
    approvals.check_usable(approval_id, task_id)  # cheap early refusal before anything is drained
    slots = model_slots.get_slots()
    try:
        async with slots.exclusive("r9700", timeout=aiwa_swap.SWAP_DRAIN_TIMEOUT_S):
            state.session_active = True
            try:
                # Consumed only after the drain: a drain timeout leaves the approval usable for a retry.
                approvals.consume(approval_id, task_id)  # type: ignore[arg-type]
                fleet_router.occupant_cache.invalidate()
                occupant, _mid, reachable = await fleet_router.occupant_cache.get(client)
                if not (reachable and occupant == "consult"):
                    resp = await aiwa_swap.swap_while_exclusive("consult", client)
                    if resp.status != 200:
                        raise SessionError("qwen_load_failed", "Qwen did not load.", resp.status, resp)
                state.sessions_run += 1
                return await run()
            finally:
                # Success, failure and cancel all end here; repeated cancels cannot skip the reload or release the lock early.
                try:
                    await _finish_even_if_cancelled(restore_resting_model(client))
                finally:
                    state.session_active = False
    except model_slots.DrainTimeout as exc:
        raise SessionError(
            "aiwa_busy", "In-flight R9700 work did not finish in time; Qwen was not loaded.", 409
        ) from exc


async def recover_resting_model(client) -> bool:
    """Startup safety: Qwen is only legitimate inside a session, so a fresh process finding it reloads Nemotron."""
    if not qwen_approval_enabled() or client is None or state.session_active:
        return False
    occupant, _mid, reachable = await fleet_router.occupant_cache.get(client)
    if not (reachable and occupant == "consult"):
        return False
    log.warning("R9700 is on Qwen with no approved session running; reloading Nemotron")
    try:
        async with model_slots.get_slots().exclusive("r9700", timeout=aiwa_swap.SWAP_DRAIN_TIMEOUT_S):
            return await restore_resting_model(client)
    except model_slots.DrainTimeout:
        log.error("startup Nemotron reload skipped: R9700 work did not drain")
        return False


# ── HTTP handling for direct completions ────────────────────────────────────


def approval_error_response(exc: ApprovalError) -> web.Response:
    extra = {"approval_id": exc.approval["id"], "approval_status": exc.approval["status"]} if exc.approval else None
    return fleet_router.guardian_error(exc.message, exc.code, exc.status, extra=extra)


def needs_approval_response(approval: dict) -> web.Response:
    return fleet_router.guardian_error(
        "Qwen loads only after Carter approves. Decide the approval, then retry with its id.",
        "needs_approval",
        409,
        extra={"approval_id": approval["id"], "approval_status": approval["status"]},
    )


def session_error_response(exc: SessionError) -> web.Response:
    return exc.response or fleet_router.guardian_error(exc.message, exc.code, exc.status)


async def handle_completion(
    request: web.Request, *, body: bytes, seat: str, client, guardian
) -> web.StreamResponse:
    """Direct R9700 completion with GUARDIAN_QWEN_APPROVAL on. No string gate, no silent downgrade."""
    seat_hdrs, _rid = fleet_router.served_headers(seat, None)
    if seat == "consult":
        approval_id = request.headers.get("X-Guardian-Approval-Id") or request.query.get("approval_id")
        # Same body, same task: a retry without headers still finds its own approval and nobody else's.
        task_id = (
            request.headers.get("X-Guardian-Task-Id")
            or request.headers.get("Idempotency-Key")
            or f"direct-{hashlib.sha256(body).hexdigest()[:16]}"
        )
        if not approval_id:
            resp = needs_approval_response(guardian.approvals.create_pending(task_id))
            resp.headers.update(seat_hdrs)
            return resp
        fwd_body, fwd_headers = fleet_router.prepare_forward(request, body, seat)

        async def run() -> web.StreamResponse:
            return await fleet_router.forward_aiwa(
                request, body=fwd_body, fwd_headers=fwd_headers, client=client, guardian=guardian, seat=seat
            )

        try:
            return await run_approved_qwen(
                approvals=guardian.approvals, approval_id=approval_id, task_id=task_id, client=client, run=run
            )
        except ApprovalError as exc:
            resp = approval_error_response(exc)
        except SessionError as exc:
            resp = session_error_response(exc)
        resp.headers.update(seat_hdrs)
        return resp

    # Nemotron: take the slot first so a request arriving during a Qwen session waits, then check.
    async with fleet_router.seat_slot(seat):
        occupant, model_id, reachable = await fleet_router.occupant_cache.get(client)
        if not reachable or occupant in {None, "", "unknown"}:
            resp = fleet_router.guardian_error("AIWA is unreachable or occupant is unknown.", "aiwa_unreachable", 503)
        elif occupant != seat:
            resp = fleet_router.aiwa_wrong_occupant_response(occupant, model_id, seat)
        else:
            fwd_body, fwd_headers = fleet_router.prepare_forward(request, body, seat)
            return await fleet_router.forward_aiwa(
                request, body=fwd_body, fwd_headers=fwd_headers, client=client, guardian=guardian, seat=seat
            )
        resp.headers.update(seat_hdrs)
        return resp
