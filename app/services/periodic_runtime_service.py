from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod


class PeriodicRuntimeService(ABC):
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task = self._task
        self._task = None
        self._started = False
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_loop(self) -> None:
        try:
            initial_delay_seconds = self.initial_delay_seconds
            if initial_delay_seconds > 0:
                await asyncio.sleep(initial_delay_seconds)
            while True:
                await self.run_cycle()
                await asyncio.sleep(self.poll_interval_seconds)
        except asyncio.CancelledError:
            raise

    @property
    def initial_delay_seconds(self) -> int:
        return 0

    @property
    @abstractmethod
    def poll_interval_seconds(self) -> int:
        raise NotImplementedError

    @abstractmethod
    async def run_cycle(self) -> None:
        raise NotImplementedError
