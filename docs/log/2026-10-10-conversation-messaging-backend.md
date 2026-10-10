# Conversations can message each other: tools, inbox and policy API (partly shipped)

Why: decision 0008. Conversations shared files and nothing else; a user running an analysis in
one and a deck in another relayed by hand.

## Shipped
- `tools/conversations.py`: `list_conversations` (READ) and `send_to_conversation`
  (WRITE_LOCAL, so it asks like any durable side effect), both deferred. A conversation is
  reachable only if its policy (`<id>.messaging.json`: off by default, `any`, or `selected`
  ids) allows the sender; the text is passed through the secret redactor, capped at 8000
  characters, and put in the receiver's inbox (`<id>.inbox.json`, at most 50 unread).
- The receiving session turns the inbox into a turn of its own (`conversation/session.py`,
  `schedule_inbox_delivery` / `_deliver_inbox`), after the turn in progress, marked as coming
  from another conversation and as information, not the user's words. It reuses the cap and
  wind-down of sub-agent reports: past five message turns in a row they wait in the inbox until
  the user speaks. A message to a conversation not open yet waits and is delivered when its tab
  connects. A sync tool in a worker thread hands over to the server's event loop.
- `GET/PUT /api/threads/{id}/messaging` (policy); deleting a conversation removes its policy and
  inbox. Registered in `coordinator.py` next to the other tool groups.
- Tests: `tests/test_conversations_tool.py` (policy, listing, send, refusals, redaction, cap),
  and in `tests/web/` a delivery round trip, a message waiting for a conversation not opened
  yet, the cap, the policy endpoint and deletion.

## Not verified
- No model has used the tools: the tests drive them directly. Whether a real model finds them
  through `search_tools` and uses them sensibly is untried.
- An incoming message shows as a plain message in the chat until the card is built.

## Follow-ups
- Edit environment switch and a card for an incoming message (frontend), the next PR.
