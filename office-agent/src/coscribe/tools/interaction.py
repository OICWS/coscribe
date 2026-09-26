"""ask_user_question -- lets the Coordinator ask a clarifying question with
clickable choices instead of only asking in prose, for exactly the same
reason Claude Code's own AskUserQuestion tool exists: a fixed option set
steers the user toward an answer that's actually actionable, and a click is
faster than typing a full sentence.

Unlike every other tool in this package, this one never actually runs its
own Python body. It's routed through LangGraph's HumanInTheLoopMiddleware
with `allowed_decisions=["respond"]` (see web/session.py's
_decide_action_request and runtime_lg/agent.py's `question_tool_names`) --
the interrupt always fires, and the human's answer (typed via
web/app.py's `question_response` message, or a stop/disconnect's own
placeholder) is substituted directly as this call's result, the tool
function itself never executing. The body below is a defensive
RuntimeError, not a real implementation: reaching it means something
failed to wire this tool into `question_tool_names` for whichever graph
called it, and that should fail loudly, not hang forever waiting on a
Future nobody will ever resolve.

Not given to sub-agents (runtime_lg/subagents.py's _NOT_FOR_SUBAGENTS):
a question belongs to the person talking to the parent, not to a
delegated run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..runtime.types import tool_metadata

QUESTION_TOOL_NAMES: frozenset[str] = frozenset({"ask_user_question", "exit_plan_mode"})

# exit_plan_mode's answers, as the frontend sends them.
PLAN_CHOICE_AUTO = "auto"
PLAN_CHOICE_MANUAL = "manual"
PLAN_CHOICE_REVISE = "revise:"


def ask_user_question(
    question: str,
    options: str,
    header: str = "",
    multi_select: bool = False,
) -> str:
    """Ask the user a clarifying question with clickable choices, instead
    of only asking in a normal reply. Use this when you're about to guess
    at a genuinely ambiguous requirement, when there's a real fork in how
    to proceed you'd otherwise describe in prose, or whenever picking
    from a short list would clearly be faster for the user than typing a
    full sentence. Don't reach for this for a plain yes/no you can just
    ask in your normal reply, or when the user's own request already
    fully specifies what to do -- this is for decisions only the user can
    make, not a substitute for actually reading what they already said.

    The user can always ignore every listed option and type a custom
    answer instead ("Something else") -- `options` narrows the likely
    answers to make deciding fast, it doesn't have to be exhaustive.

    This call always pauses for a real person to answer; there's no
    approval step and no auto-answer.

    Args:
        question: the question to ask, one sentence
        header: a short (a few words) label shown above the question,
            e.g. "Priority" -- pass "" for no header
        options: the clickable choices, one per line (2-6 short labels,
            each a few words -- these render as clickable rows, not
            paragraphs)
        multi_select: true when the options aren't mutually exclusive and
            the user may want several ("which sheets should I include?",
            "which sections need changes?"); false when exactly one
            answer makes sense ("which format?"). With true, the answer
            comes back as the chosen labels joined by ", ".
    """
    raise RuntimeError(
        "ask_user_question must be resolved via HumanInTheLoopMiddleware's "
        "'respond' decision -- reaching this body means the graph that "
        "called it never registered it in question_tool_names."
    )


def exit_plan_mode(plan: str) -> str:
    """In plan mode, present your finished plan for the user's approval.
    They choose: approve and carry it out in auto mode, approve and carry it
    out approving each change themselves, or keep planning with feedback.
    The result says which; plan mode ends when they approve.

    Args:
        plan: the plan in markdown -- what you found, the steps you'll take
            in order, the files or places each touches, and what you'll
            check at the end. Complete enough to approve without asking.
    """
    raise RuntimeError(
        "exit_plan_mode must be resolved via HumanInTheLoopMiddleware's 'respond' "
        "decision -- reaching this body means the graph never registered it in "
        "question_tool_names."
    )


def build_interaction_tools() -> list[Callable[..., Any]]:
    """Return the interaction tool callables. No state to bind -- unlike
    every other build_*_tools factory in this package, this one takes no
    arguments, since ask_user_question needs no workspace/thread/state_dir
    access (its whole behavior lives in the interrupt machinery, not this
    function body)."""
    return [
        tool_metadata(ask_user_question, risk_category="READ", category="interaction"),
        tool_metadata(exit_plan_mode, risk_category="READ", category="interaction"),
    ]
