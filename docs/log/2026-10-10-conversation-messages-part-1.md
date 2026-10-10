# Conversations can message each other, part 1: the backend, switched off (partly shipped)

Why: A user running an Excel analysis in one conversation and a deck in another has to relay between them by hand (record 0008).

## Shipped
- `tools/conversation_messages.py`: a conversation accepts messages only if `<thread>.messaging.json` says so; each has an inbox `<thread>.inbox.json` (at most 20 waiting, 4000 characters each). Two deferred tools, `list_conversations` (only other conversations that accept) and `send_to_conversation` (needs approval like any local write). The text is blanked of stored secret values first. They are registered in `conversation/session.py` like `http_request`; `coordinator.py` is untouched.
- `conversation/session.py`: a message sent to an open conversation becomes a turn of its own after the turn in progress, marked `[Message from another conversation]` and followed by a note that it is not from the user. One that arrived while the conversation was closed is taken when it is opened. Message turns count with sub-agent reports toward the cap of consecutive turns without the user, so two conversations cannot keep each other going.
- Tests: the store and tools (7), and two conversations through the websocket, open and closed (2). Mutation-checked: without the accept check or without the blanking, a test fails.

## Not verified
- In the app: nothing turns the switch on yet, so no user can reach this.
- A receiver that is open in two tabs; a message that arrives during a long turn (it waits for the turn lock, like a sub-agent report).

## Follow-ups
- Part 2: read and write the switch through the existing environment endpoint (`web/routes/settings.py`).
- Part 3 (frontend): the switch in Edit environment, and a card for a message turn like the one for a sub-agent report.
- Open in record 0008: a scheduled task sending a message (not allowed now), a broadcast (not built).
