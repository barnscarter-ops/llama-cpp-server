"""Per-model concurrency slots plus an exclusive host gate for model swaps.

Two tasks on one local model crash it, and a swap replaces the process under
any in-flight request. Every generation takes its model's slot; a swap takes
the whole host exclusively, waits for in-flight work to drain, and blocks new
work until it finishes. All state changes are synchronous so cancellation can
never leave a slot held.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import interactive
from model_registry import ModelRegistry, load_registry


def slots_enabled() -> bool:
    return os.environ.get("GUARDIAN_MODEL_SLOTS", "false").strip().lower() in {"true", "1", "yes"}


def slots_active() -> bool:
    """Slots are on by their own flag, or implied by the Qwen approval flag (its swap needs the host lock)."""
    return slots_enabled() or os.environ.get("GUARDIAN_QWEN_APPROVAL", "false").strip().lower() in {"true", "1", "yes"}


class DrainTimeout(asyncio.TimeoutError):
    """In-flight work on a host did not finish before an exclusive hold was due."""


class _NoSlot:
    """Flag-off stand-in. contextlib.nullcontext only became async-capable in 3.10."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc) -> None:
        return None


def no_slot() -> _NoSlot:
    return _NoSlot()


class _Host:
    def __init__(self) -> None:
        self.active = 0
        self.exclusive = False
        self.waiting_exclusive = 0
        self.waiters: list[asyncio.Future] = []

    def wake(self) -> None:
        waiters, self.waiters = self.waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    async def wait_changed(self) -> None:
        fut = asyncio.get_running_loop().create_future()
        self.waiters.append(fut)
        try:
            await fut
        finally:
            if fut in self.waiters:
                self.waiters.remove(fut)


class Holder:
    """One piece of work holding a model slot, so an interactive request can ask it to give the slot up.

    `kind` is "request" (direct completion) or "job" (durable queue job). The holder's owner watches
    `preempted` (see run_preemptible), closes its upstream connection and returns; the slot is released
    when its `acquire` context exits, which hands it to the interactive waiter.
    """

    def __init__(self, kind: str, ident: str, seat: str, *, preemptible: bool = True) -> None:
        self.kind, self.ident, self.seat = kind, ident, seat
        self.preemptible = preemptible
        self.priority = interactive.PRIORITY_DEFAULT
        self.preempted = asyncio.Event()

    def can_preempt(self) -> bool:
        if not self.preemptible or self.preempted.is_set() or self.priority >= interactive.PRIORITY_INTERACTIVE:
            return False
        return self.kind != "job" or interactive.job_preemptible(self.ident)


class _Waiter:
    def __init__(self, priority: int, seq: int, fut: asyncio.Future) -> None:
        self.priority, self.seq, self.fut = priority, seq, fut


class _Model:
    """Priority-ordered permits for one model. All changes are synchronous so cancellation cannot leak a permit."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.held = 0
        self.waiters: list[_Waiter] = []
        self.holders: set[Holder] = set()
        self.seq = itertools.count()

    def grant(self) -> None:
        """Hand free permits to the highest-priority, oldest waiters."""
        self.waiters = [w for w in self.waiters if not w.fut.done()]
        while self.held < self.capacity and self.waiters:
            best = min(self.waiters, key=lambda w: (-w.priority, w.seq))
            self.waiters.remove(best)
            self.held += 1
            best.fut.set_result(None)

    def release(self) -> None:
        self.held -= 1
        self.grant()


class ModelSlots:
    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry
        self._models = {s.id: _Model(s.max_concurrent) for s in registry}
        self._hosts = {s.host: _Host() for s in registry}
        self._in_flight = {s.id: 0 for s in registry}

    def model_id_for_seat(self, seat: str) -> str | None:
        spec = self._registry.for_seat(seat)
        return spec.id if spec else None

    def in_flight(self, model_id: str) -> int:
        return self._in_flight.get(model_id, 0)

    def exclusive_held(self, host: str) -> bool:
        return self._hosts[host].exclusive

    def _preempt(self, model_id: str) -> None:
        """Ask enough lower-priority holders to give up the model for the interactive waiters in line.

        Never touches a swap (exclusive holds are not holders) and never acts while a swap holds or awaits the host.
        """
        if not interactive.preempt_enabled():
            return
        model = self._models[model_id]
        host = self._hosts[self._registry.get(model_id).host]
        if host.exclusive or host.waiting_exclusive:
            return
        waiting = sum(
            1 for w in model.waiters if not w.fut.done() and w.priority >= interactive.PRIORITY_INTERACTIVE
        )
        already = sum(1 for h in model.holders if h.preempted.is_set())
        need = waiting - already
        for holder in [h for h in model.holders if h.can_preempt()][: max(0, need)]:
            holder.preempted.set()
            interactive.record_preemption(holder.seat, holder.kind, holder.ident)

    @asynccontextmanager
    async def acquire(
        self, model_id: str, *, priority: int = interactive.PRIORITY_DEFAULT, holder: Holder | None = None
    ) -> AsyncIterator[None]:
        """Take the model's slot. Higher priority first, FIFO within a priority. `holder` makes the work preemptible."""
        spec = self._registry.get(model_id)
        if spec is None:
            raise KeyError(f"unknown model {model_id!r}")
        model, host = self._models[model_id], self._hosts[spec.host]
        fut = asyncio.get_running_loop().create_future()
        model.waiters.append(_Waiter(priority, next(model.seq), fut))
        model.grant()
        if not fut.done() and priority >= interactive.PRIORITY_INTERACTIVE:
            self._preempt(model_id)
        try:
            await fut
        except BaseException:
            if fut.done() and not fut.cancelled():
                model.release()  # granted just as we were cancelled
            raise
        try:
            # A waiting swap also blocks new work, or a busy queue could starve it forever.
            while host.exclusive or host.waiting_exclusive:
                await host.wait_changed()
            host.active += 1
            self._in_flight[model_id] += 1
        except BaseException:
            model.release()
            raise
        if holder is not None:
            holder.priority = priority
            model.holders.add(holder)
            if priority < interactive.PRIORITY_INTERACTIVE:
                self._preempt(model_id)  # an interactive request may already be waiting on us
        try:
            yield
        finally:
            if holder is not None:
                model.holders.discard(holder)
            host.active -= 1
            self._in_flight[model_id] -= 1
            host.wake()
            model.release()

    async def wait_available(self, model_id: str) -> None:
        """Block while a swap holds or awaits the model's host; lets queue workers leave jobs queued meanwhile."""
        spec = self._registry.get(model_id)
        if spec is None:
            return
        host = self._hosts[spec.host]
        while host.exclusive or host.waiting_exclusive:
            await host.wait_changed()

    @asynccontextmanager
    async def exclusive(self, host_name: str, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Hold a whole host. Raises DrainTimeout if in-flight work does not drain in time."""
        host = self._hosts[host_name]
        host.waiting_exclusive += 1

        async def drain() -> None:
            while host.exclusive or host.active:
                await host.wait_changed()

        try:
            try:
                await asyncio.wait_for(drain(), timeout)
            except asyncio.TimeoutError as exc:
                raise DrainTimeout(f"{host_name} did not drain within {timeout}s") from exc
            host.exclusive = True
        finally:
            host.waiting_exclusive -= 1
            host.wake()
        try:
            yield
        finally:
            host.exclusive = False
            host.wake()


_slots: ModelSlots | None = None


def get_slots() -> ModelSlots:
    global _slots
    if _slots is None:
        _slots = ModelSlots(load_registry())
    return _slots


def reset_slots() -> None:
    """Tests and registry reloads drop the singleton."""
    global _slots
    _slots = None


async def run_preemptible(aw, holder: Holder | None):
    """Await `aw`; if the holder is preempted first, cancel it (closing its upstream) and say so.

    Returns (result, preempted). Exceptions from `aw` propagate. Without a holder this is a plain await.
    """
    if holder is None:
        return await aw, False
    task = asyncio.ensure_future(aw)
    signal = asyncio.ensure_future(holder.preempted.wait())
    try:
        await asyncio.wait({task, signal}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        task.cancel()
        signal.cancel()
        raise
    signal.cancel()
    if task.done():
        return task.result(), False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)  # an outer cancel still propagates from here
    return None, True
