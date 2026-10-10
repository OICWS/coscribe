"""WebSocket routes: the chat protocol and the browser panel."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..browser_panel import BrowserPanelError, BrowserPanelSession
from ..state import AppState


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    meta_store = state.meta_store
    _get_session = state.get_session
    _track_background = state.track_background
    _write_workspace_sidecar = state.write_workspace_sidecar

    @router.websocket("/ws/browser")
    async def browser_panel_ws(websocket: WebSocket) -> None:
        """Separate socket from /ws/{thread_id} on purpose, not new
        message types bolted onto that one -- screencast frames are a
        fundamentally different traffic shape (many small JPEGs a
        second, independent of any chat turn) from the chat protocol's
        own carefully-paced agent_delta/tool_result stream, and mixing
        them risks one starving the other. Not thread_id-scoped either:
        one browser_panel.py session for the lifetime of this one
        connection, closed the moment it drops -- see BrowserPanelSession's
        own docstring for why this is a companion tool, not conversation
        state.

        Registered *before* /ws/{thread_id} below on purpose -- Starlette
        matches WebSocket routes in registration order, and /ws/{thread_id}
        is a path-param route that would otherwise swallow /ws/browser
        first (thread_id="browser"), never reaching this handler at all.
        Confirmed the hard way: a route-order test written against a fake
        BrowserPanelSession failed with resolve_chat_model raising on the
        literal string "browser" as a model id, coming from _get_session --
        proof the request was landing in ws_endpoint instead."""
        await websocket.accept()
        session = BrowserPanelSession()
        try:
            await session.launch()
        except BrowserPanelError as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
            return

        async def on_frame(data: str) -> None:
            await websocket.send_json({"type": "frame", "data": data})

        await session.start_screencast(on_frame)
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                try:
                    if message_type == "navigate":
                        await session.navigate(data["url"])
                    elif message_type == "reload":
                        await session.reload()
                    elif message_type == "back":
                        await session.go_back()
                    elif message_type == "forward":
                        await session.go_forward()
                    elif message_type == "mouse":
                        await session.dispatch_mouse(
                            data["kind"],
                            data["x"],
                            data["y"],
                            button=data.get("button", "left"),
                            delta_x=data.get("deltaX", 0),
                            delta_y=data.get("deltaY", 0),
                        )
                    elif message_type == "key":
                        await session.dispatch_key(data["kind"], data["key"])
                    elif message_type == "text":
                        await session.insert_text(data["text"])
                    elif message_type == "resize":
                        await session.resize(data["width"], data["height"], data.get("scale", 1.0))
                    elif message_type == "hover_element":
                        element = await session.hover_element(data["x"], data["y"])
                        await websocket.send_json({"type": "hover", "element": element})
                    elif message_type == "pick_element":
                        picked = await session.pick_element(data["x"], data["y"])
                        await websocket.send_json({"type": "picked", **picked})
                except BrowserPanelError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    @router.websocket("/ws/{thread_id}")
    async def ws_endpoint(
        websocket: WebSocket,
        thread_id: str,
        workspace: str | None = None,
    ) -> None:
        await websocket.accept()
        session = _get_session(thread_id, workspace)
        # See ChatSessionLG.notify_resync's own docstring -- this is the
        # one place that knows "a real browser tab is watching this
        # thread right now," so it's the one place that sets/clears it.
        # A *new* connection to an already-open thread (two tabs, or a
        # reload racing its own old socket's teardown) simply overwrites
        # this with the newest connection -- acceptable: resync is a
        # best-effort nudge, not a correctness-critical channel, and the
        # newest tab is the one actually worth nudging.
        session._live_websocket = websocket
        meta_store.mark_seen(thread_id)
        try:
            await session.send_state(websocket, on_connect=True)
            await session.send_history(websocket)
            # A pending approval from before a restart or dropped connection
            # doesn't wait for a new user_message to surface -- redeliver it
            # now. Backgrounded (not awaited) for the same reason
            # user_message handling is: it can block on a future that only
            # resolves via an approval_response arriving through the loop
            # below, so awaiting it inline here would deadlock.
            # Tracked, so shutdown stops it before the checkpointer it reads
            # closes.
            _track_background(asyncio.create_task(session.resume_after_reconnect(websocket)))
            session.schedule_inbox_delivery()
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                if message_type == "user_message":
                    asyncio.create_task(
                        session.handle_user_message(
                            data["text"], websocket, images=data.get("images")
                        )
                    )
                elif message_type == "edit_message":
                    # Same asyncio.create_task treatment as user_message
                    # above -- an edit runs a real turn afterward (may
                    # itself block on an approval), so it can't be awaited
                    # inline without blocking this loop from ever reaching
                    # the approval_response that would unblock it.
                    asyncio.create_task(
                        session.handle_edit_message(
                            data["index"], data["text"], websocket, images=data.get("images")
                        )
                    )
                elif message_type == "rewind_message":
                    # Never blocks on an approval future the way edit's
                    # rerun can, but create_task anyway -- consistent with
                    # every other mutating message type here, and safe
                    # regardless since it only ever awaits _turn_lock.
                    asyncio.create_task(session.handle_rewind_message(data["index"], websocket))
                elif message_type == "approval_response":
                    session.resolve_approval(
                        data["id"], bool(data.get("approved")), data.get("scope")
                    )
                elif message_type == "question_response":
                    answers = data.get("answers")
                    if data.get("dismissed"):
                        session.resolve_question(data["id"], None)
                    elif isinstance(answers, list):
                        session.resolve_question(
                            data["id"], [None if a is None else str(a) for a in answers]
                        )
                    else:
                        session.resolve_question(data["id"], str(data.get("answer", "")))
                elif message_type == "steer":
                    session.add_steer(str(data.get("id", "")), str(data.get("text", "")))
                elif message_type == "stop":
                    session.request_stop()
                    await session.run_interrupt_hooks()
                elif message_type == "switch_model":
                    # Directly awaited, not asyncio.create_task like
                    # user_message -- same reasoning as web/app.py's
                    # identical handler: a model switch has no unbounded
                    # wait on a human the way an approval-blocked turn
                    # does, and Starlette's WebSocket.send() has no
                    # internal locking against concurrent callers.
                    await session.switch_model(data["model"], websocket)
                elif message_type == "set_folders":
                    folders = [str(folder) for folder in data.get("folders", [])]
                    # Persisted only once the session took them, so the
                    # sidecar never names folders the agent isn't using.
                    if await session.set_folders(folders, websocket):
                        _write_workspace_sidecar(thread_id, [str(f) for f in session.folders])
                elif message_type == "add_folder":
                    if await session.add_folder(str(data.get("folder", "")), websocket):
                        _write_workspace_sidecar(thread_id, [str(f) for f in session.folders])
                elif message_type == "load_older_messages":
                    # Directly awaited, not asyncio.create_task like
                    # user_message -- same reasoning as switch_model above:
                    # this never blocks on a human, so there's nothing to
                    # keep this loop free to service concurrently.
                    await session.load_older_messages(websocket)
        except WebSocketDisconnect:
            # A turn still blocked on an approval for *this* connection at
            # the moment it drops would otherwise dangle forever: nothing
            # can ever resolve that pending Future once the socket that
            # would have carried its approval_response is gone, and
            # ChatSessionLG._turn_lock means an orphaned task like that
            # blocks every future turn on this thread_id too -- including
            # resume_after_reconnect's own attempt to redeliver the same
            # pending approval to a fresh connection. abandon_orphaned_turn
            # (not request_stop -- see its own docstring for why) hard-
            # cancels that task without ever resolving the approval or
            # resuming the graph, so the checkpointer's real pending state
            # is untouched and a genuine reconnect still redelivers it.
            session.abandon_orphaned_turn(websocket)
            await session.run_session_end_hooks()
        finally:
            # Only clear if this is still *this* connection's own socket --
            # a newer connection (see the comment above where this is set)
            # may have already overwritten it with itself, and this older,
            # now-dead connection's teardown must not clobber that.
            if session._live_websocket is websocket:
                session._live_websocket = None

    return router
