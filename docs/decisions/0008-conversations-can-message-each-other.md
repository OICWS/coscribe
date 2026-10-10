# 0008: Conversations can message each other

Status: accepted (2026-10-10). Built: the tools, the inbox, the policy API and, in Edit
environment, the switch.

## Context
Conversations are isolated: they share a workspace's files and nothing else
(`list_background_tasks` can look at another conversation's tasks, read only). A
user running an Excel analysis in one and a deck in another has to relay by hand.

## Decision (proposed)
- Two tools, deferred through `search_tools` (0001), so the fixed list is unchanged:
  `list_conversations` (titles and ids the user allowed) and
  `send_to_conversation(id, text)`. Per-conversation data goes in tool results only.
- **Opt in per conversation, in Edit environment**: "other conversations may
  message this one" (default off) and which ones. The sender can reach only
  conversations that allowed it; the model cannot browse the rest.
- **Delivery** reuses what a finished background sub-agent does: the text becomes a
  turn of its own after the turn in progress, marked as a message from conversation
  `<title>`; the receiver's model treats it as information, not a user instruction.
  An offline receiver gets it from a small per-thread inbox file on next open.
- **Approvals do not travel**: the receiver acts under its own mode and its own
  approvals; nothing in a message can pre-approve an action.
- **No secrets or environment cross**: only the text, passed through the same
  redaction as tool results (0003). Session variables are not forwarded.
- **Loops are bounded**: a cap on consecutive message turns per conversation (as
  for sub-agent reports) and a visible note when it is hit.
- The user sees every message in both transcripts.

## Consequences
- Touches `conversation/session.py` and `coordinator.py` (invariant path: needs an
  independent review) and the Edit environment dialog (frontend).
- Settled while building: a message crosses a restart (an inbox file per conversation, up
  to 50 unread); a scheduled task's conversation is not special-cased (it can send only
  where the receiver allowed it); no broadcast; `send_to_conversation` asks like any
  durable side effect (WRITE_LOCAL), `list_conversations` does not.
