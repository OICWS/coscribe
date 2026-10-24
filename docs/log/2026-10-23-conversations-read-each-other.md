# Every conversation can list, read and message the others unless archived (shipped)

Why: the opt-in switch of 0008 was a per-conversation setting nobody wanted, and a model
could send but not read. See 0023.

## Shipped
- `tools/bookkeeping/conversations.py`: `list_conversations` shows all other conversations
  (id, title, last activity, count); new `read_conversation` (READ); `send_to_conversation`
  reaches any of them. Archived conversations are refused by all three. The messaging policy
  (`set_policy`, `allows`, `<id>.messaging.json`) is gone.
- `runtime_lg/conversation_source.py`: `CheckpointConversations`, the transcripts from the
  checkpointer, handed to the tools by `ChatSessionLG` through `build_coordinator_agent`.
- Removed `GET/PUT /api/threads/{id}/messaging`.
- Tests: `tests/test_conversation_source.py`, `tests/test_conversations_tool.py` (listing, archive, read bounds, redaction, send),
  web tests send through the tool on the server's loop.

## Not verified
- No real model has used `read_conversation`; `CheckpointConversations` is tested against a real
  sqlite checkpointer (`tests/test_conversation_source.py`), not against a live session.
- The Edit environment dialog still shows its old messages section (it will get a 404 on the
  removed route) until the dialog redesign removes it; `getMessagingPolicy`,
  `saveMessagingPolicy` and `MessagingPolicy` stay in the frontend until then.

## Follow-ups
- Remove the leftover frontend calls and type with the dialog redesign.
