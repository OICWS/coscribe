from __future__ import annotations

from typing import Any, Protocol


class EventSink(Protocol):
    """Where a conversation's events go: a browser's WebSocket, or the CLI's own
    terminal stand-in. Only this one method is used."""

    async def send_json(self, data: Any) -> None: ...
