"""A declarative rule engine deciding whether a `run_python_script`/
`run_node_script` call can skip the normal live-approval step -- ROADMAP.md's
Phase 7, item 2, modeled on openai/codex's `execpolicy` crate but scoped
down for coscribe's actual shape.

**Rules are human-authored configuration, never inferred from content by
coscribe itself.** This is the whole reason this doesn't contradict
`tools/scripts.py`'s own documented stance that no import/library
allowlist exists because "any such restriction is trivially bypassable
... and would only offer a false sense of security" -- that critique is
about coscribe (or the model) deciding for itself that some code pattern
is safe. Here, the *user* pre-authorizes a specific pattern for their own
convenience, in advance, the same way accept-edits mode is a standing
pre-authorization -- if a rule is written too loosely and the model routes
something risky through it, that's a config mistake by the person who
wrote the rule, not a hole in coscribe's own design. Nothing here is a
security boundary; it only changes whether a human is asked before a
call that a human already blessed a pattern for.

Rule file shape (JSON, `{"rules": [...]}`), each rule:
    {"pattern": "<regex>", "decision": "allow"|"forbidden"|"prompt",
     "justification": "<optional human-readable reason>"}
Rules are evaluated in file order against the tool call's `script`
argument (the one thing `run_python_script`/`run_node_script` both take);
the first pattern that matches (via `re.search`, not full-match) decides.
No match, no configured file, or a file that fails to parse all mean the
same thing: "prompt" -- unconfigured/broken behaves exactly like today,
never silently starts auto-approving or auto-forbidding. `decision:
"prompt"` as an explicit rule value exists so a broad "allow" rule can be
followed by a narrower "prompt" rule carving out an exception (first
match wins, so the narrower rule needs to come first in the file to have
that effect)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Decision = Literal["allow", "forbidden", "prompt"]

_VALID_DECISIONS: frozenset[str] = frozenset({"allow", "forbidden", "prompt"})

# The only two tools this applies to -- both take their source under a
# `script` argument (see tools/scripts.py's/tools/node_scripts.py's own
# `run_python_script`/`run_node_script`).
EXEC_POLICY_TOOL_NAMES: frozenset[str] = frozenset({"run_python_script", "run_node_script"})


@dataclass(frozen=True)
class ExecPolicyRule:
    pattern: re.Pattern[str]
    decision: Decision
    justification: str = ""


class ExecPolicy:
    """An ordered, immutable list of rules, plus the `decide` method
    _decide_action_request actually calls. Construct via load_exec_policy,
    not directly -- that's what handles a missing/malformed file."""

    def __init__(self, rules: list[ExecPolicyRule]) -> None:
        self._rules = rules

    def decide(self, script: str) -> tuple[Decision, str | None]:
        """First matching rule wins; ("prompt", None) if none match or
        this policy has no rules at all -- the same "ask a human" outcome
        as today, unconfigured."""
        for rule in self._rules:
            if rule.pattern.search(script):
                return rule.decision, (rule.justification or None)
        return "prompt", None


_EMPTY_POLICY = ExecPolicy([])


def load_exec_policy(path: str | Path | None) -> ExecPolicy:
    """Loads an ExecPolicy from `path` (a JSON file, see this module's own
    docstring for shape). Missing path, missing file, invalid JSON, or a
    JSON shape that isn't `{"rules": [...]}` all fall back to an empty
    policy (every call "prompt"s, i.e. today's unconfigured behavior) with
    a logged warning rather than raising -- a broken exec-policy file
    should never itself become a reason a script tool call fails, and it
    must never silently start allowing/forbidding calls it can't actually
    parse rules for. An individual rule entry that's malformed (missing
    `pattern`, an invalid `decision`, or an unparseable regex) is skipped
    the same way tools/skills.py's loader skips one bad SKILL.md -- the
    rest of the file's rules still load."""
    if path is None:
        return _EMPTY_POLICY
    file_path = Path(path)
    if not file_path.is_file():
        return _EMPTY_POLICY
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read exec policy file %r", str(file_path), exc_info=True)
        return _EMPTY_POLICY
    if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
        logger.warning(
            "Exec policy file %r must be a JSON object with a 'rules' array -- ignoring it",
            str(file_path),
        )
        return _EMPTY_POLICY

    rules: list[ExecPolicyRule] = []
    for index, entry in enumerate(raw["rules"]):
        if not isinstance(entry, dict):
            logger.warning(
                "Exec policy rule %d in %r is not an object -- skipping", index, str(file_path)
            )
            continue
        pattern_str = entry.get("pattern")
        decision = entry.get("decision", "prompt")
        justification = entry.get("justification", "")
        if not isinstance(pattern_str, str) or not pattern_str:
            logger.warning(
                "Exec policy rule %d in %r has no string 'pattern' -- skipping",
                index,
                str(file_path),
            )
            continue
        if decision not in _VALID_DECISIONS:
            logger.warning(
                "Exec policy rule %d in %r has invalid decision %r -- skipping",
                index,
                str(file_path),
                decision,
            )
            continue
        try:
            compiled = re.compile(pattern_str)
        except re.error:
            logger.warning(
                "Exec policy rule %d in %r has an invalid regex %r -- skipping",
                index,
                str(file_path),
                pattern_str,
                exc_info=True,
            )
            continue
        rules.append(
            ExecPolicyRule(
                pattern=compiled,
                decision=decision,
                justification=justification if isinstance(justification, str) else "",
            )
        )
    return ExecPolicy(rules)
