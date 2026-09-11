"""In-process pub/sub for background-completion events -- the plumbing the
desktop shell's tray notification (office-agent-desktop) needs and that
didn't exist before: `_wake_poll_loop` (web/app.py) fires wakes/scheduled
tasks on its own timeline, with nobody watching, and a `_SilentSocket`
(runtime_lg/selfwake.py) throws away every message from those turns --
there was no channel a *process*, as opposed to a browser tab with a live
WebSocket, could subscribe to.

Deliberately NOT wired into runtime_lg (poll_due_wakes/poll_due_
scheduled_tasks themselves) -- both already return `list[WakeRequest]`/
`list[ScheduledTrigger]` describing exactly what fired, so the calling
loop in web/app.py can publish from that return value directly. Pushing
this into runtime_lg would mean either importing this module there (a new
coupling on top of the get_session-callback indirection selfwake.py's own
docstring already explains the lengths taken to avoid) or introducing yet
another callback parameter, for no benefit over reading a return value
the poll loop already has in hand.

A plain asyncio.Queue per subscriber, not asyncio's own broadcast
primitives (no built-in pub/sub in the stdlib) and not a heavier message
bus -- this is one process talking to at most a handful of local
subscribers (in practice: one desktop shell's SSE connection), not a
distributed system.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackgroundEvent:
    kind: str  # "wake" | "scheduled_task"
    status: str  # "completed" | "failed"
    title: str  # human-readable label: wake's reason, or trigger's name
    thread_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "background_run_completed",
            "kind": self.kind,
            "status": self.status,
            "title": self.title,
            "thread_id": self.thread_id,
        }


@dataclass
class BackgroundEventBus:
    """Fan-out broadcaster: every subscribed queue gets every published
    event. `publish` is sync and non-blocking (`put_nowait`) so the poll
    loop never stalls on a slow/stuck subscriber; queues are unbounded
    since events are small and rare (at most one per fired wake/trigger
    per poll interval), not a backpressure-worthy stream.
    """

    _subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: BackgroundEvent) -> None:
        payload = event.to_dict()
        for queue in self._subscribers:
            queue.put_nowait(payload)
