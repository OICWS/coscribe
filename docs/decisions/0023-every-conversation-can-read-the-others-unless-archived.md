# 0023: Every conversation can read the others unless they are archived

Status: accepted (2026-10-23). Replaces the opt-in of [0008](0008-conversations-can-message-each-other.md).

## Context
0008 made a conversation reachable only after the user switched it on in Edit environment,
and gave the sender no way to read anything. In use that was a setting nobody wanted to
manage per conversation, and a model that cannot read another conversation cannot use it.

## Decision
- A third deferred tool, `read_conversation(id, last_messages)`, returns the latest user and
  assistant text of another conversation (no tool calls or results), through the secret
  redactor, bounded in count (100) and per message (2000 characters). `list_conversations`
  shows every other conversation with its title and last activity.
- **Archiving is the only switch.** An archived conversation cannot be listed, read or
  messaged; there is no per-conversation policy, route or file any more. A scheduled task's
  own conversations are not offered.
- The transcripts come from the checkpointer through a small `ConversationSource` the
  session passes in, because `tools` cannot import LangChain. The tools read `archived` from
  the sidebar's `<id>.meta.json`.
- Unchanged from 0008: messages go to an inbox and arrive as a turn marked as coming from
  another conversation; approvals and secrets do not travel; `send_to_conversation` asks
  like any durable side effect. Reading is READ risk: it shows the model the user's other
  conversations, which the user already chose to keep un-archived.

- **Open by default is the maintainer's call (2026-10-23), after review raised the folder and
  network settings.** Reading only shows the model the user's own conversations. A message
  from A is not the user: the receiver gets it marked "from another conversation, not from the
  user" (`format_incoming`), the auto-approval reviewer does not count it as the user's words,
  and it cannot switch modes or pre-approve anything (`tests/web/test_prompt_injection.py`).
  So B's folder, network and approvals still decide what B does, and an outward action in B
  asks the user first. Archiving remains the way to take a conversation out of reach.

## Consequences
- Anything in an un-archived conversation can reach another conversation's model, so a
  conversation holding something to keep apart must be archived.
- A stale `<id>.messaging.json` from before is ignored and left on disk.
