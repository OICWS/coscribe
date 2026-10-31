"""A conversation's checkpointed history: replaying it to a connecting tab, paging back
through what /compact folded away, /compact and /clear, and the edit and rewind of the
last message. It lives apart from ChatSessionLG because all of it is reads and rewrites
of the checkpoint; the session keeps the agent, the turn lock and the turn itself.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Protocol

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langgraph.graph import END
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from ..runtime import COMPACT_INSTRUCTIONS
from ..runtime_lg import answer_every_tool_call, is_steer, serialize_history_for_ws_lg
from ..runtime_lg import extract_text as _extract_text
from ..runtime_lg import render_transcript_lg as _render_transcript_lg
from .events import EventSink
from .export_text import conversation_markdown
from .turn_lock import TurnLock

if TYPE_CHECKING:
    from .approvals import ApprovalDecider

# Shared between _handle_compact (which writes it as the first message of
# a fresh epoch) and send_history (which checks for it, below) -- lets
# send_history answer "might this thread have older history to page
# through" with an O(1) check on just the current checkpoint's own first
# message, instead of load_older_messages' own full backward walk (real,
# live-reported bug this avoids: the frontend used to offer "load older
# messages" unconditionally on *every* thread, including a brand new one
# with nothing to load).
_COMPACT_NOTE_PREFIX = "[Earlier conversation compacted to save context.]"


async def close_orphaned_tool_calls(agent: Any, config: dict[str, Any]) -> bool:
    """Give every tool call that never got a ToolMessage (a turn cancelled
    mid-tool: Stop, a dropped socket, a killed process) a placeholder one,
    and end the graph there. Every OpenAI-compatible provider rejects the
    whole history with a 400 otherwise, and a leftover pending "tools"
    node would re-run the cancelled call on the next resume.

    Skipped while an approval/question interrupt is pending: that tool
    call is legitimately unanswered and gets resumed, not patched.
    Rewrites the whole message list rather than appending, since a thread
    already corrupted by this bug has a newer HumanMessage after the
    orphan and the placeholder has to sit directly after its AIMessage.

    Also runs when leftover tasks exist but nothing needs a placeholder:
    aget_state folds in the pending writes of a tool that finished inside
    a superstep that never committed, but a fresh input restarts from the
    last *committed* checkpoint and drops those writes -- so the model
    would still see the orphan unless they're committed here."""
    state = await agent.aget_state(config)
    if not state.values or any(task.interrupts for task in state.tasks):
        return False
    messages = list(state.values.get("messages", []))
    rebuilt, patched = answer_every_tool_call(messages)
    if not patched and not state.tasks:
        return False
    await agent.aupdate_state(
        config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *rebuilt]}, as_node="tools"
    )
    await agent.aupdate_state(config, None, as_node=END)
    return True


class HistoryHost(Protocol):
    """What the history reads and calls on the session it belongs to."""

    thread_id: str
    config: dict[str, Any]
    lg_agent: Any
    model: Any
    active_run_prompt: str | None
    _turn_lock: TurnLock
    _current_turn_task: asyncio.Task[None] | None

    @property
    def _approvals(self) -> ApprovalDecider: ...

    async def _run_observational_hooks(self, event: str, payload: dict[str, Any]) -> None: ...

    async def _handle_user_message_locked(
        self,
        text: str,
        websocket: EventSink,
        images: list[str] | None = None,
        *,
        notice: bool = False,
        artifact: str | None = None,
        design_system: dict[str, Any] | None = None,
    ) -> None: ...


class ConversationHistory:
    def __init__(self, host: HistoryHost) -> None:
        self._host = host
        # Pagination cursor for load_older_messages, seeded on its first call (see
        # its docstring for why not at construction).
        self._history_boundary_config: dict[str, Any] | None = None
        self._history_known_message_ids: set[str] | None = None

    async def send_history(self, websocket: EventSink) -> None:
        """One-shot replay of this thread's checkpointed messages, sent
        once right after send_state on every fresh WS connection (see
        app.py's ws_endpoint) -- fixes a real, live-reported bug: the
        chat log was otherwise only ever built up from live events during
        the *current* connection, so switching to (or reconnecting to) an
        existing thread showed a blank pane even though the model still
        remembered the whole conversation. See serialize_history_for_ws_lg
        for the entry shape and what's deliberately not perfectly handled
        (a /compact'd thread's synthetic summary message).

        A brand-new thread has no checkpointed messages yet, so this is a
        harmless no-op "history" event with an empty list -- no special
        casing needed for the new-thread path."""
        state = await self._host.lg_agent.aget_state(self._host.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        # O(1) proxy for "does load_older_messages have anything to find"
        # -- a real full-history walk (that method's own, correct way to
        # know for sure) is too expensive to do unconditionally on every
        # connect; see _COMPACT_NOTE_PREFIX's own comment for why this
        # check is safe and accurate in practice.
        first_message = messages[0] if messages else None
        has_older = isinstance(first_message, HumanMessage) and str(
            first_message.content
        ).startswith(_COMPACT_NOTE_PREFIX)
        entries = serialize_history_for_ws_lg(messages)
        prompt = self._host.active_run_prompt
        if prompt is not None and not any(
            e["kind"] == "user" and e["text"] == prompt for e in entries
        ):
            entries.append({"kind": "user", "text": prompt})
        await websocket.send_json({"type": "history", "entries": entries, "has_older": has_older})
        # The replay replaces what the tab shows; a turn still waiting on a
        # question or approval (a run's, an investigation's) asks this tab too.
        for event in list(self._host._approvals.open_prompts.values()):
            await websocket.send_json(event)

    async def load_older_messages(self, websocket: EventSink) -> None:
        """Scroll-up pagination for a thread that's been /compact'd: send_
        history above only ever returns the *current* checkpoint's message
        list, which right after a compaction is just the synthetic summary
        note -- real, live-reported complaint that the earlier conversation
        then looked permanently gone, with no way to scroll up and see it,
        unlike every other AI product the user had used. The underlying
        messages aren't actually gone: LangGraph's checkpointer is append-
        only/versioned (aupdate_state's RemoveMessage(id=REMOVE_ALL_
        MESSAGES) sentinel writes a *new* checkpoint, it doesn't delete the
        old ones) -- this was purely a read-side gap, nothing ever walked
        aget_state_history to read them back.

        Mechanism, verified empirically against a real LangGraph graph
        before wiring this in (a compacted thread's checkpoint sequence
        behaves exactly as follows): within one "epoch" (the stretch of
        checkpoints between two RemoveMessage(ALL) resets, or since the
        thread's start), the messages list only ever grows -- the
        add_messages reducer appends, so every older checkpoint's own id
        set is a *subset* of the current one's. The moment a walk backward
        crosses a RemoveMessage(ALL) boundary, that breaks: the checkpoint
        immediately on the other side holds the *previous* epoch's own
        final, fully-accumulated message list, entirely disjoint from
        anything already known. So: walk aget_state_history backward from
        `_history_boundary_config` and return the first checkpoint whose
        message ids aren't already a subset of `_history_known_message_
        ids` -- that's exactly the previous epoch's own full message
        list, in one shot, not a partial slice of it. Advances both the
        cursor and the known-ids set so a second "load older" click
        (multiple compactions in one thread) correctly skips back past
        the whole batch just revealed instead of re-finding it.

        The cursor is lazily initialized to *whatever is currently live*
        on this method's own first call, never to send_history's
        connect-time snapshot -- real bug caught by this fix's own tests:
        seeding it once in send_history left the cursor frozen at
        connect time (typically an empty thread), so every message sent
        *after* connecting -- the entire conversation a real user would
        ever want to page back through -- looked like it was "before"
        that stale cursor and never got walked at all. Recomputing here
        instead means the boundary always reflects everything the
        frontend has already rendered by the time the user actually
        clicks "load older" (send_history's own dump, plus every live
        turn since), which is the only version of "already known" that's
        actually correct.

        Real cost tradeoff, not free: for a thread that was *never*
        compacted, every earlier checkpoint is always a subset of the
        current one, so this walks the thread's *entire* checkpoint
        history before concluding there's nothing older -- acceptable
        because the frontend only calls this once per thread-view (on
        first scroll-to-top) and remembers has_more=False afterward, not
        because the walk itself is bounded."""
        if self._history_boundary_config is None or self._history_known_message_ids is None:
            current_state = await self._host.lg_agent.aget_state(self._host.config)
            self._history_boundary_config = current_state.config
            self._history_known_message_ids = (
                {m.id for m in current_state.values.get("messages", [])}
                if current_state.values
                else set()
            )

        older: list[Any] = []
        found_config: dict[str, Any] | None = None
        async for snapshot in self._host.lg_agent.aget_state_history(
            self._host.config, before=self._history_boundary_config
        ):
            snapshot_messages = list(snapshot.values.get("messages", [])) if snapshot.values else []
            snapshot_ids = {m.id for m in snapshot_messages}
            if not snapshot_ids <= self._history_known_message_ids:
                older = snapshot_messages
                found_config = snapshot.config
                break

        if found_config is not None:
            self._history_boundary_config = found_config
            self._history_known_message_ids |= {m.id for m in older}
        # has_more is a hint to keep offering "load older", not a real
        # lookahead -- true whenever this call itself found a batch, even
        # on the very last one (confirming there's truly nothing earlier
        # would mean walking one epoch further just to check, every time).
        # An empty result is the one unambiguous "nothing left" signal;
        # the frontend is expected to stop offering it only then, not on
        # has_more alone.
        await websocket.send_json(
            {
                "type": "older_messages",
                "entries": serialize_history_for_ws_lg(older),
                "has_more": bool(older),
            }
        )

    async def _handle_compact(self, websocket: EventSink) -> None:
        """Summarize this thread's checkpointed message history down to one
        note, freeing up context -- the runtime_lg counterpart to
        ChatSession's /compact. Reuses runtime/compaction.py's own
        COMPACT_INSTRUCTIONS (not a duplicate prompt) but can't reuse
        run_compaction/compact_state themselves: those work on RunState's
        plain dict messages, not LangGraph's checkpointed BaseMessage list,
        and collapse via FileStateStore.save_state rather than
        aupdate_state's RemoveMessage(id=REMOVE_ALL_MESSAGES) sentinel
        (verified live against real Gemini before wiring this in -- see
        runtime_lg/README.md).

        No system message to re-add after clearing: verified empirically
        that create_agent never stores one in checkpointed state at all
        (system_prompt is injected fresh at call time), unlike the old
        runtime's RunState.messages, which always keeps one at index 0.

        The pending-approval check below predates _turn_lock and is mostly
        unreachable through ordinary concurrent messaging now that the lock
        serializes every turn -- see _handle_clear's docstring for the one
        remaining window it still guards (a fresh process restart racing
        resume_after_reconnect for the lock)."""
        state = await self._host.lg_agent.aget_state(self._host.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        if len(messages) < 4:
            await websocket.send_json({"type": "error", "message": "Nothing much to compact yet."})
            return
        if state.next:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Resolve the pending approval before compacting.",
                }
            )
            return

        await self._host._run_observational_hooks(
            "PreCompact",
            {"event": "PreCompact", "thread_id": self._host.thread_id, "agent_name": "coordinator"},
        )
        start = time.perf_counter()
        summary_message = await self._host.model.ainvoke(
            [
                SystemMessage(content=COMPACT_INSTRUCTIONS),
                HumanMessage(content=_render_transcript_lg(messages)),
            ]
        )
        summary = _extract_text(summary_message.content) or "(no summary)"
        note = f"{_COMPACT_NOTE_PREFIX}\n\n{summary}"
        await self._host.lg_agent.aupdate_state(
            self._host.config,
            {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content=note)]},
        )
        after_state = await self._host.lg_agent.aget_state(self._host.config)
        after = len(after_state.values.get("messages", [])) if after_state.values else 0
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        await self._host._run_observational_hooks(
            "PostCompact",
            {
                "event": "PostCompact",
                "thread_id": self._host.thread_id,
                "agent_name": "coordinator",
                "before": len(messages),
                "after": after,
                "elapsed_ms": elapsed_ms,
            },
        )
        await websocket.send_json(
            {
                "type": "compacted",
                "before": len(messages),
                "after": after,
                "elapsed_ms": elapsed_ms,
            }
        )

    async def _handle_clear(self, websocket: EventSink) -> None:
        """Wipe this thread's conversation history and start fresh -- unlike
        /compact (collapses down to a summary note), this discards it
        outright. Doesn't touch plan_mode/accept_edits (session-level UI
        toggles, not conversation content) -- matches ChatSession's
        _handle_clear.
        Same pending-approval guard as _handle_compact -- see that
        docstring for why an aupdate_state call shouldn't race a paused
        interrupt task. With _turn_lock now serializing every turn (see
        __init__), this specific check is unreachable through ordinary
        concurrent messaging -- a /clear sent while another turn is
        pending on approval queues behind _turn_lock instead of racing it,
        so by the time this method actually runs, that turn has already
        been resolved one way or another (see test_clear_queues_behind_a_
        pending_turn_instead_of_racing_it). Kept anyway as a defensive
        backstop for the one window that still isn't covered by the lock:
        a fresh process restart, where the checkpointer still shows a
        genuinely pending interrupt from before the restart but this new
        ChatSessionLG's own _turn_lock is unheld, so a /clear racing
        resume_after_reconnect for the lock could in principle win it
        first."""
        state = await self._host.lg_agent.aget_state(self._host.config)
        if state.next:
            await websocket.send_json(
                {"type": "error", "message": "Resolve the pending approval before clearing."}
            )
            return
        await self._host.lg_agent.aupdate_state(
            self._host.config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]}
        )
        await websocket.send_json({"type": "cleared"})

    async def export_markdown(self) -> str:
        entries = serialize_history_for_ws_lg(await self._current_messages())
        return conversation_markdown(self._host.thread_id, entries)

    async def _current_messages(self) -> list[Any]:
        """This thread's checkpointed message history right now -- the
        runtime_lg counterpart to reading state.messages off a
        FileStateStore-persisted RunState."""
        state = await self._host.lg_agent.aget_state(self._host.config)
        return list(state.values.get("messages", [])) if state.values else []

    async def handle_edit_message(
        self,
        index: int,
        text: str,
        websocket: EventSink,
        images: list[str] | None = None,
    ) -> None:
        # Same _turn_lock serialization as handle_user_message -- an edit
        # is a new turn like any other, just one that first rewrites
        # history before running it.
        async with self._host._turn_lock:
            self._host._current_turn_task = asyncio.current_task()
            await self._handle_edit_message_locked(index, text, websocket, images=images)

    async def _handle_edit_message_locked(
        self,
        index: int,
        text: str,
        websocket: EventSink,
        images: list[str] | None = None,
    ) -> None:
        """Edit an earlier user turn and regenerate the conversation from
        there. `index` is 0-based, counting only HumanMessages -- the exact
        count the frontend's own `history` hydration already produces
        (every checkpointed HumanMessage maps 1:1 to one "user"-kind
        LogItem there), so no new id-plumbing between frontend and backend
        is needed to keep the two sides pointing at the same message.

        Truncates the checkpointed state back to (and including) the
        target HumanMessage via RemoveMessage/aupdate_state -- the same
        mechanism _handle_compact/_handle_clear already use, and safe to
        reuse here for the same reason: add_messages assigns every
        checkpointed message a real id on the way in (verified against
        langgraph.graph.message.add_messages's own source), so
        RemoveMessage(id=m.id) always targets a real, existing id. Once
        truncated, the edited text is re-run through the exact same turn
        path _handle_user_message_locked already uses for a freshly typed
        message -- an edit is not a special kind of turn, just one that
        starts from a rewound history."""
        await close_orphaned_tool_calls(self._host.lg_agent, self._host.config)
        state = await self._host.lg_agent.aget_state(self._host.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        if state.next:
            await websocket.send_json(
                {"type": "error", "message": "Resolve the pending approval before editing."}
            )
            return

        human_positions = [
            i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and not is_steer(m)
        ]
        if index < 0 or index >= len(human_positions):
            await websocket.send_json(
                {"type": "error", "message": f"No such message to edit (index {index})."}
            )
            return
        # Editing an older message would silently discard every turn after
        # it, including work the agent already did on files.
        if index != len(human_positions) - 1:
            await websocket.send_json(
                {"type": "error", "message": "Only your most recent message can be edited."}
            )
            return

        to_remove = messages[human_positions[index] :]
        if to_remove:
            await self._host.lg_agent.aupdate_state(
                self._host.config, {"messages": [RemoveMessage(id=m.id) for m in to_remove]}
            )

        await self._host._handle_user_message_locked(text, websocket, images=images)

    async def handle_rewind_message(self, index: int, websocket: EventSink) -> None:
        # Same _turn_lock serialization as handle_edit_message -- see that
        # method's own comment.
        async with self._host._turn_lock:
            self._host._current_turn_task = asyncio.current_task()
            await self._handle_rewind_message_locked(index, websocket)

    async def _handle_rewind_message_locked(self, index: int, websocket: EventSink) -> None:
        """Undo the last question and its reply -- real, live-reported
        bug this fixes: the UI's "Rewind" button used to call
        handle_edit_message with the turn's own *unedited* text, which
        truncates and immediately reruns it -- that's retry (same
        question, new answer), not rewind. This only truncates; the
        frontend hands the original question text back to the composer
        instead of resubmitting it, so the user decides what happens next
        (edit it, discard it, or resend it unchanged). Same truncation
        mechanism and guards as _handle_edit_message_locked (pending-
        approval check, index range check), just without the rerun step
        at the end."""
        await close_orphaned_tool_calls(self._host.lg_agent, self._host.config)
        state = await self._host.lg_agent.aget_state(self._host.config)
        messages = list(state.values.get("messages", [])) if state.values else []
        if state.next:
            await websocket.send_json(
                {"type": "error", "message": "Resolve the pending approval before rewinding."}
            )
            return

        human_positions = [
            i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and not is_steer(m)
        ]
        if index < 0 or index >= len(human_positions):
            await websocket.send_json(
                {"type": "error", "message": f"No such message to rewind (index {index})."}
            )
            return

        to_remove = messages[human_positions[index] :]
        if to_remove:
            await self._host.lg_agent.aupdate_state(
                self._host.config, {"messages": [RemoveMessage(id=m.id) for m in to_remove]}
            )
        await websocket.send_json({"type": "rewound", "index": index})
