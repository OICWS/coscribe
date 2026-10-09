"""Internal routes."""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import AsyncIterator

from fastapi import (
    APIRouter,
    Request,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ...tools.browser import BROWSER_HOST
from ..schemas import (
    BrowserHostReply,
)
from ..state import AppState


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    settings = state.settings
    background_events = state.background_events


    @router.get("/internal/events")
    async def background_events_stream() -> StreamingResponse:
        """Server-Sent Events stream of background_events.BackgroundEvent
        payloads -- see that module's docstring for why this exists.
        Under `/internal/` rather than `/api/` since this isn't for the
        bundled frontend (which already gets live updates over its own
        per-thread WebSocket) -- the one real subscriber is the desktop
        shell (office-agent-desktop), connecting once at startup to show a
        native notification when a background/scheduled run finishes
        while its window is hidden. No auth beyond "reachable on
        127.0.0.1 at all," same as every other endpoint here -- this
        process already assumes a single local user.
        """

        async def event_source() -> AsyncIterator[str]:
            queue = background_events.subscribe()
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                background_events.unsubscribe(queue)

        return StreamingResponse(event_source(), media_type="text/event-stream")

    def _is_browser_host(request: Request) -> bool:
        expected = settings.browser_host_token
        given = request.headers.get("x-coscribe-browser-token", "")
        return bool(expected) and hmac.compare_digest(given.encode(), str(expected).encode())

    @router.get("/internal/browser-host", response_model=None)
    async def browser_host_stream(request: Request) -> Response:
        """The desktop app's browser takes its commands from here; see
        tools/browser.py."""
        if not _is_browser_host(request):
            return JSONResponse({"error": "Not the desktop app's browser."}, status_code=403)
        outbox = BROWSER_HOST.attach()

        async def command_source() -> AsyncIterator[str]:
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        command = await asyncio.wait_for(outbox.get(), 15)
                    except TimeoutError:
                        # A write is the only way a dropped connection gets
                        # noticed, and commands can be minutes apart.
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(command)}\n\n"
            finally:
                BROWSER_HOST.detach(outbox)

        return StreamingResponse(command_source(), media_type="text/event-stream")

    @router.post("/internal/browser-host/result")
    async def browser_host_result(request: Request, reply: BrowserHostReply) -> JSONResponse:
        if not _is_browser_host(request):
            return JSONResponse({"error": "Not the desktop app's browser."}, status_code=403)
        BROWSER_HOST.resolve(reply.id, reply.model_dump())
        return JSONResponse({"ok": True})

    return router
