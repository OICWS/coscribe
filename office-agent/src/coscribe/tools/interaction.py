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


from collections.abc import Callable, Sequence
from typing import Annotated, Any, NotRequired

from pydantic import Field

# pydantic needs typing_extensions' TypedDict before Python 3.12.
from typing_extensions import TypedDict

from ..runtime.types import tool_metadata

QUESTION_TOOL_NAMES: frozenset[str] = frozenset({"ask_user_question", "exit_plan_mode"})

# exit_plan_mode's answers, as the frontend sends them.
PLAN_CHOICE_AUTO = "auto"
PLAN_CHOICE_MANUAL = "manual"
PLAN_CHOICE_REVISE = "revise:"


class QuestionOption(TypedDict):
    label: Annotated[str, Field(description="a few words -- what the user clicks")]
    description: NotRequired[
        Annotated[str, Field(description="one short line under the label: what picking it means")]
    ]


class UserQuestion(TypedDict):
    question: Annotated[str, Field(description="the question, one sentence")]
    options: Annotated[list[QuestionOption], Field(description="2-6 choices")]
    multi_select: NotRequired[
        Annotated[
            bool,
            Field(
                description='true when several can apply ("which sheets should I include?"); '
                'false when exactly one makes sense ("which format?")'
            ),
        ]
    ]
    header: NotRequired[
        Annotated[str, Field(description='a label of a few words for the question, e.g. "Style"')]
    ]


def ask_user_question(questions: list[UserQuestion]) -> str:
    """Ask the user clarifying questions with clickable choices, instead
    of only asking in a normal reply. Use this when you're about to guess
    at a genuinely ambiguous requirement, when there's a real fork in how
    to proceed you'd otherwise describe in prose, or whenever picking
    from a short list would clearly be faster for the user than typing a
    full sentence. Don't reach for this for a plain yes/no you can just
    ask in your normal reply, or when the user's own request already
    fully specifies what to do -- this is for decisions only the user can
    make, not a substitute for actually reading what they already said.

    Ask everything you need at once (1-4 questions): the user steps
    through them one by one and answers them together. Each option has a
    short label and, when the label alone doesn't say enough, a one-line
    description. The user can always type their own answer instead
    ("Something else") or skip a question, so the options narrow the
    likely answers without having to be exhaustive.

    The answer comes back as the chosen label(s) -- several joined by
    ", " for a multi-select question -- one line per question when you
    asked more than one, "(skipped)" for a question the user skipped.

    This call always pauses for a real person to answer; there's no
    approval step and no auto-answer.

    Args:
        questions: the questions, in the order to ask them
    """
    raise RuntimeError(
        "ask_user_question must be resolved via HumanInTheLoopMiddleware's "
        "'respond' decision -- reaching this body means the graph that "
        "called it never registered it in question_tool_names."
    )


def normalize_questions(args: dict[str, Any]) -> list[dict[str, Any]]:
    """The call's questions as the page shows them. A conversation saved
    before questions came in lists asked one question with its options as
    lines of text; it can still be resumed."""
    raw = args.get("questions")
    if not isinstance(raw, list):
        raw = [
            {
                "question": args.get("question", ""),
                "header": args.get("header", ""),
                "options": str(args.get("options", "")).split("\n"),
                "multi_select": args.get("multi_select", False),
            }
        ]
    questions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        options = []
        for option in item.get("options") or []:
            if isinstance(option, dict):
                label, description = (
                    str(option.get("label", "")),
                    str(option.get("description", "")),
                )
            else:
                label, description = str(option), ""
            if label.strip():
                options.append({"label": label.strip(), "description": description.strip()})
        questions.append(
            {
                "question": str(item.get("question", "")),
                "header": str(item.get("header", "")),
                "options": options,
                "multi_select": bool(item.get("multi_select", False)),
            }
        )
    return questions


QUESTIONS_DISMISSED = (
    "The user closed the questions without answering. Don't ask them again; go "
    "on with the most sensible choice and say which you made, or ask in your reply "
    "if you can't go on without an answer."
)


def format_question_answers(
    questions: Sequence[dict[str, Any]], reply: str | Sequence[str | None] | None
) -> str:
    """The tool result the model reads. `reply` is one answer per question
    (None for a skipped one), None when the user closed the questions, or
    a plain string -- a stop's placeholder, or an older page's single
    answer -- passed through as is."""
    if reply is None:
        return QUESTIONS_DISMISSED
    if isinstance(reply, str):
        return reply
    answers = [answer.strip() if answer and answer.strip() else "(skipped)" for answer in reply]
    answers += ["(skipped)"] * (len(questions) - len(answers))
    if len(questions) <= 1:
        return answers[0] if answers else "(skipped)"
    return "\n".join(
        f"{index}. {question['question']} -> {answer}"
        for index, (question, answer) in enumerate(zip(questions, answers, strict=False), 1)
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
