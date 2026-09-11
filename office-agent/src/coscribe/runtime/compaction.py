"""COMPACT_INSTRUCTIONS: the system prompt for /compact's summarize call.

This module used to also hold the compaction *mechanism* itself
(run_compaction/compact_state/maybe_auto_compact/render_transcript,
built around the old runtime's RunState/Runner) -- deleted as dead code
once web/session.py's own async _handle_compact (built directly on
runtime_lg's checkpointer/message-list model, which has no equivalent of
the old RunState to collapse) became the only real compaction path left
running. See runtime_lg/README.md's "audit + delete old runtime" section.
"""

from __future__ import annotations

COMPACT_INSTRUCTIONS = """\
Summarize the conversation transcript below concisely but completely: \
preserve every fact, decision, and piece of context that would be needed \
to keep working on this task, and drop routine back-and-forth. Write it as \
a short brief for someone picking up the work fresh, not a transcript.
"""
