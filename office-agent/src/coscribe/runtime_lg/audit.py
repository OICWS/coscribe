"""Append-only audit log for approval-gated tool calls -- ROADMAP.md's
Phase 4 "Audit logging" item, deliberately parked at the time the rest of
that phase (risk tiering, selfwake, secrets hardening) shipped, picked up
here: a durable record of what a WRITE_LOCAL/EXEC/EXTERNAL action did,
when, and under what approval -- whether a human was actually watching in
real time or not. That last part is the whole point: an unattended
selfwake/scheduled-task run has no live approval UI to show anyone (see
web/session.py's _SilentSocket), so without this, an autonomous run's
approval-gated actions leave no trace anywhere for a human to review
after the fact.

Wired into web/session.py's _decide_action_request -- the single place
every gated tool call's decision actually gets made (a PreToolUse hook
veto, plan mode blocking it outright, accept-edits auto-approving it,
or a real human approving/denying it live), for attended and unattended
turns alike. Tool calls that were never gated at all (a READ-risk tool,
or a tool outside self._approval_required_names entirely) are not
logged here -- this is an audit trail for actions that needed someone's
sign-off, not a duplicate of the full conversation transcript.

record_decision runs `arguments`/`detail` through redact_secrets before
persisting -- see that function's own docstring for why and what it
does/doesn't catch (ROADMAP.md's Phase 7).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Decision = Literal["approve", "reject"]
Reason = Literal[
    "hook_veto", "plan_mode", "exec_policy", "accept_edits", "human", "stopped", "unattended"
]

_REDACTED = "[REDACTED]"

# A gated tool call's arguments can carry a real secret -- an MCP server's
# `env`/`headers` dict (add_mcp_server), a custom provider's `api_key`
# (add_provider), or one a user pasted into an ad-hoc script/message
# argument -- and this file is the one place that content gets written to
# disk verbatim, unlike runtime/secrets.py's own coverage (.env,
# providers.json, mcp.json), which only hardens *coscribe's own* stored
# copies. Found reading openai/codex's `secrets` crate (its own
# `redact_secrets` sanitizer runs over anything about to be persisted) --
# see ROADMAP.md's Phase 7. Two independent layers, same as that crate's
# approach: a key name that reads as secret-shaped redacts its whole value
# regardless of shape (cheap, catches anything named right, no false
# negatives from an unrecognized token format); a handful of well-known
# provider token *formats* catch a real secret that leaked into an
# innocuously-named field (e.g. a script's `code` argument). Deliberately
# not a generic high-entropy/long-random-string heuristic -- gated tool
# arguments routinely carry long legitimate strings (file contents, whole
# scripts, base64 image data) that would false-positive constantly and
# make the audit log less useful, not more secure.
_SECRET_KEY_NAME_RE = re.compile(
    r"api[-_]?key|access[-_]?key|secret|password|passwd|pwd|token|bearer|authorization|credential",
    re.IGNORECASE,
)

_SECRET_VALUE_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"sk-ant-[A-Za-z0-9_-]{20,}",  # Anthropic
        r"sk-proj-[A-Za-z0-9_-]{20,}",  # OpenAI (project key)
        r"sk-[A-Za-z0-9]{20,}",  # OpenAI (legacy) / other sk-prefixed keys
        r"gh[oprsu]_[A-Za-z0-9]{20,}",  # GitHub (ghp_/gho_/ghu_/ghs_/ghr_)
        r"github_pat_[A-Za-z0-9_]{20,}",  # GitHub fine-grained PAT
        r"xox[baprs]-[A-Za-z0-9-]{10,}",  # Slack
        r"AIza[A-Za-z0-9_-]{30,}",  # Google API key
        r"AKIA[A-Z0-9]{16}",  # AWS access key id
        r"glpat-[A-Za-z0-9_-]{15,}",  # GitLab
    )
]


def redact_secrets(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact secret-shaped content from a JSON-like value
    (the shape record_decision's `arguments`/`detail` are always built
    from -- dicts/lists/strings/numbers/None, never an arbitrary object).
    `key` is the dict key this value was found under, if any -- passed
    down so a value's own field name can trigger a blanket redaction even
    when the value's content doesn't match any known token format."""
    if isinstance(value, dict):
        return {k: redact_secrets(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v, key=key) for v in value]
    if isinstance(value, str):
        if key is not None and _SECRET_KEY_NAME_RE.search(key):
            return _REDACTED
        for pattern in _SECRET_VALUE_PATTERNS:
            value = pattern.sub(_REDACTED, value)
        return value
    return value


@dataclass
class AuditEntry:
    timestamp: str
    thread_id: str
    tool_name: str
    arguments: dict[str, Any]
    decision: Decision
    reason: Reason
    # The hook's veto message, plan mode's own explanation, or an
    # exec-policy rule's justification (if it had one) -- None for
    # accept_edits/human/stopped, which are self-explanatory from
    # decision+reason alone.
    detail: str | None = None


class AuditLog:
    """One append-only JSONL file, global across every thread (not
    per-thread) under state_dir -- mirrors this codebase's other global
    *Store classes' "one file/directory under state_dir" shape (WakeStore,
    ScheduledTriggerStore, WorkflowRunStore), but append-only rather than
    one-file-per-record: an audit trail is inherently a sequence, never
    edited or deleted after the fact the way a wake request or a
    scheduled trigger's own state is."""

    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / "audit.jsonl"

    def append(self, entry: AuditEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def read_all(self) -> list[AuditEntry]:
        """Every entry, oldest first. No live caller today (no UI surfaces
        this log yet, see this module's own docstring) -- exists so tests
        (and a future Settings panel, if one gets built) have a real
        read-back instead of only being able to assert "the file exists".
        A line that fails to parse (a hand-edited or truncated file) is
        skipped rather than raising -- reading history shouldn't break
        because of one bad line."""
        if not self.path.is_file():
            return []
        entries = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(AuditEntry(**json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    continue
        return entries


def record_decision(
    audit_log: AuditLog,
    *,
    thread_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    decision: Decision,
    reason: Reason,
    detail: str | None = None,
) -> None:
    """Thin convenience wrapper around AuditLog.append -- builds the
    timestamp and AuditEntry so the one real call site
    (web/session.py's _decide_action_request) stays a one-liner per
    decision branch rather than repeating this construction five times."""
    audit_log.append(
        AuditEntry(
            timestamp=datetime.now(UTC).isoformat(),
            thread_id=thread_id,
            tool_name=tool_name,
            arguments=redact_secrets(arguments),
            decision=decision,
            reason=reason,
            detail=redact_secrets(detail) if detail is not None else None,
        )
    )
