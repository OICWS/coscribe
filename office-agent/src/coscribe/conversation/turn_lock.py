"""The lock a conversation's turns take, which also reports when a turn
starts and ends -- the desktop app saves a download from the
conversation's browser tab without asking only while one is under way."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal


class TurnLock(asyncio.Lock):
    def __init__(self, on_change: Callable[[bool], None]) -> None:
        super().__init__()
        self._on_change = on_change

    async def acquire(self) -> Literal[True]:
        await super().acquire()
        self._on_change(True)
        return True

    def release(self) -> None:
        super().release()
        self._on_change(False)
