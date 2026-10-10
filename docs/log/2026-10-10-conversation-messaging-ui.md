# Conversations can message each other: the Edit environment switch and the message card (shipped)

Why: decision 0008, second half. The tools, inbox and policy API were in; nothing in the UI could
turn it on, and a message showed as a plain bubble.

## Shipped
- Edit environment (`EnvironmentDialog.tsx`) has a "Messages from other conversations" section:
  Off (default), Any other conversation, or Only the ones I pick (a checklist of the other
  conversations, scheduled runs left out). Saved with the rest through
  `PUT /api/threads/{id}/messaging`; a change counts as unsaved edits like the variables do.
- `ConversationMessageCard.tsx`: an incoming message is a collapsed card, "Message from another
  conversation: <title>", opened to read it as markdown (several messages are listed under their
  senders), drawn like the sub-agent report card. It is recognised by the prefix the server writes
  (`tools/conversations.py`'s `format_incoming`), so it also shows when the history is reloaded.
- Checked in the running app with Playwright: the section, a saved "selected" policy, and a
  message put in a conversation's inbox appearing as a card after its tab connects, still there
  after a reload.

## Not verified
- A real model sending with `send_to_conversation`: the message in the check above was written
  into the inbox directly.
- The dialog on a narrow window.

## Follow-ups
- None.
