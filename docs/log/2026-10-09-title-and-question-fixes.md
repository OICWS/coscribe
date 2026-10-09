# A conversation is named from its first message at once; a pending question survives leaving twice (shipped)

Why: a long first message stayed the conversation's label until the first turn
ended (never, if the turn was waiting on the user), and a question left
unanswered vanished the second time the user came back to the conversation.

## Shipped
- The title is asked for as soon as the first message is sent, from that message
  alone; the end of the first turn asks again, with the reply, only if that got
  nothing (`web/session.py`: `_name_thread`).
- A question redelivered on reconnect now belongs to its connection, so closing it
  (switching to another conversation) releases the turn lock. Before, only the
  first connection's close did: the second return found the lock held by a turn
  waiting on a dead socket, and nothing was redelivered (`resume_after_reconnect`).
- Tests: `test_a_conversation_is_named_from_its_first_message_lg`,
  `test_a_pending_question_is_redelivered_every_time_the_conversation_is_reopened`
  (hangs without the fix: it waits for a question that never comes).

## Not verified
- Titles with a real model: checked with a scripted one and a stub endpoint.
- The same lock for an approval redelivered the same way is fixed by the same
  line; only the question was reproduced in the browser.
