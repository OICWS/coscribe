"""The conversations another conversation's tools may read: the checkpointer's
threads, as plain user and assistant text (tools.bookkeeping.conversations)."""

from __future__ import annotations

from typing import Any

from ..tools import SCHEDULED_THREAD_PREFIX, ConversationTranscript
from .messages import extract_text, strip_mode_note


class CheckpointConversations:
    def __init__(self, checkpointer: Any) -> None:
        self._checkpointer = checkpointer

    async def thread_ids(self) -> list[str]:
        # Same setup the sidebar listing does: a fresh database has no tables yet.
        await self._checkpointer.setup()
        cursor = await self._checkpointer.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        )
        ids = [row[0] for row in await cursor.fetchall()]
        return [i for i in ids if not i.startswith(SCHEDULED_THREAD_PREFIX)]

    async def transcript(self, thread_id: str) -> ConversationTranscript | None:
        checkpoint = await self._checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
        if checkpoint is None:
            return None
        messages = []
        for message in checkpoint.checkpoint["channel_values"].get("messages", []):
            if message.type not in ("human", "ai"):
                continue
            text = extract_text(message.content)
            if message.type == "human":
                text = strip_mode_note(text)
            if text.strip():
                messages.append(("user" if message.type == "human" else "assistant", text))
        return ConversationTranscript(updated_at=checkpoint.checkpoint["ts"], messages=messages)
