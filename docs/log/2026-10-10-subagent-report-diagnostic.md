# Why a sub-agent's report was not sent to its conversation is now logged (shipped)

Why: The maintainer saw a sub-agent finish with no word from the conversation. Four runs against a real model (foreground, Manual mode with the approval answered in the panel, background, a question card before the delegation) and two with the page reloaded (the approval answered at once, and nine minutes later) all delivered the report. The cause was not found.

## Shipped
- `_report_subagent` logs, at info level, the reason it sends no report: the record is gone, the model already read the outcome, a workflow run, or the cap of report turns in a row without the user. Search the server log (`%APPDATA%\coscribe\logs\coscribe-server.log` on Windows) for `not reported to`.

## Not verified
- That any of these is what happened; the log is for the next time it does.

## Follow-ups
- After a page reload the chat shows only "Delegated to a sub-agent" and an idle composer, while the sub-agent waits for approval in the Background tasks panel (closed after the reload, a small badge shows 1). Easy to read as "nothing happened". Frontend: reopen the panel, or show the waiting approval in the chat, when a conversation opens with a task waiting for the user.
