"""Per-model concurrency slots plus an exclusive host gate for model swaps.

Two tasks on one local model crash it, and a swap replaces the process under
any in-flight request. Every generation takes its model's slot; a swap takes
the whole host exclusively, waits for in-flight work to drain, and blocks new
work until it finishes. All state changes are synchronous so cancellation can
never leave a slot held.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from model_registry import ModelRegistry, load_registry


def slots_enabled() -> bool:
    return os.environ.get("GUARDIAN_MODEL_SLOTS", "false").strip().lower() in {"true", "1", "yes"}


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


class ModelSlots:
    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry
        self._sems = {s.id: asyncio.Semaphore(s.max_concurrent) for s in registry}
        self._hosts = {s.host: _Host() for s in registry}
        self._in_flight = {s.id: 0 for s in registry}

    def model_id_for_seat(self, seat: str) -> str | None:
        spec = self._registry.for_seat(seat)
        return spec.id if spec else None

    def in_flight(self, model_id: str) -> int:
        return self._in_flight.get(model_id, 0)

    def exclusive_held(self, host: str) -> bool:
        return self._hosts[host].exclusive

    @asynccontextmanager
    async def acquire(self, model_id: str) -> AsyncIterator[None]:
        spec = self._registry.get(model_id)
        if spec is None:
            raise KeyError(f"unknown model {model_id!r}")
        sem, host = self._sems[model_id], self._hosts[spec.host]
        await sem.acquire()
        try:
            # A waiting swap also blocks new work, or a busy queue could starve it forever.
            while host.exclusive or host.waiting_exclusive:
                await host.wait_changed()
            host.active += 1
            self._in_flight[model_id] += 1
        except BaseException:
            sem.release()
            raise
        try:
            yield
        finally:
            host.active -= 1
            self._in_flight[model_id] -= 1
            host.wake()
            sem.release()

    @asynccontextmanager
    async def exclusive(self, host_name: str, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Hold a whole host. Raises asyncio.TimeoutError if in-flight work does not drain in time."""
        host = self._hosts[host_name]
        host.waiting_exclusive += 1

        async def drain() -> None:
            while host.exclusive or host.active:
                await host.wait_changed()

        try:
            await asyncio.wait_for(drain(), timeout)
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
