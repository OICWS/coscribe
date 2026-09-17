"""spawn_agent for runtime_lg -- closes Phase 1's open question: does an
interrupt() raised inside a sub-agent's own tool node correctly bridge up
through a parent tool node that synchronously invokes it?

**Answer, live-verified against Gemini (see scripts/verify_nested_interrupt.py):
not automatically -- a sub-agent built via build_langgraph_agent is a
separately-compiled graph with its own checkpointer/thread_id.** When one of
its tools triggers HumanInTheLoopMiddleware's own interrupt(), the
sub-agent's own Pregel executor catches that GraphInterrupt internally and
returns *normally* from `.invoke()`, with `sub_agent.get_state(...).next`
populated -- nothing propagates to the parent automatically. A naive
spawn_agent that just does `sub_agent.invoke(...)` and returns the last
message would silently swallow the pending approval: the parent tool node
would report back whatever partial/stale text is in the last message (often
none at all), and the sub-agent would be left permanently stuck waiting for
an approval nobody will ever see.

The fix below manually bridges *one* pending child approval per spawn_agent
call: after invoking the child, it checks the child's own
`get_state().next`, and if paused, calls `interrupt()` **itself** (the
parent's own primitive, from inside the parent's own tool-node execution)
using the same payload shape `HumanInTheLoopMiddleware` already uses -- so
the existing approval UI/wire protocol needs zero changes to render a
nested approval identically to a top-level one. On resume, the decision is
fed back into the child via `Command(resume=...)` against the *same*,
deterministic child thread_id (derived from the parent tool call's own
injected `tool_call_id`, which is stable across the parent's own
interrupt/resume replay -- see LangGraph's documented re-run-from-the-top
node semantics; anything derived from *random* state, like a freshly
generated uuid, would silently lose track of the child's checkpoint the
moment the parent itself gets resumed).

**Multi-round nested approval (a sub-agent needing two separate approvals in
one spawn_agent call) was suspected to be broken by this same "code after
interrupt() re-executes on replay" reasoning -- checked live, and the
suspicion turned out to be right, just not in the way first assumed.**
scripts/verify_nested_interrupt.py's two-round scenario (two *sequential*
approval-gated write_file calls, decided APPROVE then REJECT to rule out
"round 2 silently reuses round 1's cached decision") passed -- both times
this docstring originally took that as proof the replay model was wrong.
**It wasn't: later testing with *concurrent* spawn_agent calls (two
proposed in one AIMessage) surfaced the real bug the two-round scenario
had been masking by luck.** `interrupt()`'s re-execution really does apply
to spawn_agent's *entire* function body on every resume, including
`sub_agent = build_langgraph_agent(...)` -- and that line used to build a
*fresh* `InMemorySaver()` on every single call (checkpointer=None
defaulted to one). So on resume, `state.values` read back empty, `if not
state.values: sub_agent.invoke(...)` silently re-asked the child *from
scratch*, and the resume decision meant for the *original* pending
question got applied to whatever *new* question that fresh ask produced
instead. Debug instrumentation confirmed it directly: `id(sub_agent)`
differs between the pre-interrupt and post-resume executions of the same
spawn_agent call, and a second, redundant child invocation really does
happen on every resume. This was invisible in every prior test (both
single- and two-round) purely because the redundant re-ask, by chance,
proposed the *same* tool call the second time -- re-approving/re-rejecting
a duplicate that looked identical to the original produces a correct-
looking result even though the mechanism underneath is wrong. The
concurrent-call test broke that luck: after both concurrent calls
successfully completed, a *third*, unexpected `approval_required` for one
of them appeared, tracing back to exactly this. **Fixed** by giving each
`build_spawn_agent_tool` closure one `InMemorySaver` shared across every
call it ever makes (`child_checkpointer`, module-scoped inside the
closure, not per-call) -- `sub_agent`'s *compiled graph* object still gets
rebuilt on every replay (cheap, harmless), but `get_state`/`invoke` against
the *same* checkpointer now correctly finds the real, already-persisted
checkpoint for a given child thread_id, so a replay resumes the actual
pending question instead of silently asking a new one. Live-verified
against real Gemini after the fix: two concurrent spawn_agent calls, one
approved and one rejected, resolved correctly and independently with no
further spurious approvals -- see runtime_lg/README.md.

`max_rounds` is kept as a parameter (not hardcoded to 1) so a sub-agent
needing multiple real approval rounds can still be verified without a
second copy of this function.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from ..tools import QUESTION_TOOL_NAMES
from .agent import build_langgraph_agent
from .agent import tool_name as _tool_name
from .messages import extract_text

# build_review_work_tool's fixed Reviewer persona. Used to live in
# tools/subagents.py alongside the old runtime's own spawn_agent/
# review_work tool factory -- moved here when that old factory was deleted
# as dead code (see runtime_lg/README.md's "audit + delete old runtime"
# section), since this was its only real consumer left.
REVIEWER_INSTRUCTIONS = """\
You are a reviewer. You did not do the work being reviewed -- look at it \
with fresh eyes. Given the user's original request and a summary of what \
was done, check: does it actually satisfy the request? Is anything missing, \
wrong, or riskier than necessary? If you were given a file path, read it \
yourself with your own tools rather than trusting the summary alone -- the \
summary is what the acting agent believes it did, not independent \
confirmation. If an image is attached, it's a rendered preview of the \
actual output -- look at it critically, the way a human design reviewer \
would, not just for structural correctness: is text legible against \
whatever is behind it, does the layout look generic or like every \
slide/page uses the same template, are numbers/tables/formulas that are \
visible actually complete and not obviously wrong, do tracked changes or \
comments look like they landed on the right content. Reply with a short, \
direct assessment: either confirm it looks correct, or list specific, \
concrete problems to fix (not vague "could be better" -- name what's \
actually wrong and where). Do not redo the work yourself.
"""


def build_spawn_agent_tool(
    model: Any,
    available_tools: Sequence[Callable[..., Any] | BaseTool],
    *,
    max_rounds: int = 4,
) -> BaseTool:
    """Return a spawn_agent tool bound to `model`/`available_tools`, mirroring
    tools/subagents.py's spawn_agent signature (instructions/prompt/
    tool_names) as closely as the injected-id requirement allows.

    `available_tools` may mix plain functions, aisuite's MCPToolWrapper
    (`__name__`, no `.name`), and LangChain `BaseTool`/`StructuredTool`
    instances -- e.g. runtime_lg/mcp.py's MCP tools, which have `.name` but
    no `__name__` -- agent.py's `tool_name` helper handles all three.

    `max_rounds` bounds how many separate approval round-trips one
    spawn_agent call will bridge before giving up and reporting the
    sub-agent as still stuck (a safety cap against a sub-agent that never
    stops asking for approvals, not evidence of a correctness limit --
    verified live for 1 and 2 rounds, see module docstring).
    """
    # Excludes itself defensively -- callers are expected to pass their own
    # tool list *before* adding spawn_agent to it (cli.py/session.py
    # both do), but this guards against unbounded self-recursion even if a
    # caller ever includes it by mistake, same reasoning as tools/
    # subagents.py's _DISALLOWED_SUBAGENT_TOOLS. Also excludes
    # QUESTION_TOOL_NAMES (ask_user_question) -- this sub-agent's own
    # graph never registers it in question_tool_names (see
    # build_langgraph_agent below, called with no question_tool_names
    # argument), so calling it here would just run its defensive
    # RuntimeError body instead of pausing for a real person the way it
    # does for the top-level Coordinator.
    excluded_names = {"spawn_agent", *QUESTION_TOOL_NAMES}
    tools_by_name = {
        _tool_name(t): t for t in available_tools if _tool_name(t) not in excluded_names
    }
    # Shared across every spawn_agent call this tool instance ever makes
    # (one per session, since build_spawn_agent_tool itself is called once
    # per ChatSessionLG/cli.py chat()) -- not per-call. A real bug, found
    # by testing concurrent spawn_agent calls and confirmed with debug
    # instrumentation, not assumed: `interrupt()`'s own documented
    # re-execution semantics ("resumes from the start of the node,
    # re-executing all logic") apply to spawn_agent's *entire* function body,
    # not just the code after interrupt() -- so `sub_agent = build_
    # langgraph_agent(...)` below reruns on every resume too. If it built a
    # *fresh* checkpointer each time (the previous behavior, checkpointer=
    # None defaulting to a new InMemorySaver()), the child's progress from
    # before the interrupt would vanish: `state.values` would read back
    # empty, `if not state.values: sub_agent.invoke(...)` would silently
    # re-ask the child from scratch, and the *new* pending interrupt that
    # produces would immediately (and incorrectly) receive the decision the
    # user gave for the *original* one -- invisible when the child's second
    # response happens to match its first (why this shipped without being
    # caught: every existing single-round/two-round live verification
    # produced a matching duplicate by chance), but a real risk of silently
    # executing something other than what the user actually approved
    # whenever the model's second attempt doesn't reproduce the first
    # exactly. One InMemorySaver shared across all of this closure's calls
    # fixes it: build_langgraph_agent still constructs a fresh *compiled
    # graph* object per call (cheap, harmless), but get_state/invoke against
    # the *same* checkpointer correctly finds the real, persisted checkpoint
    # for a given child thread_id regardless of how many times spawn_agent's
    # own function body has replayed.
    child_checkpointer = InMemorySaver()

    @tool
    def spawn_agent(
        instructions: str,
        prompt: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        tool_names: str = "",
    ) -> str:
        """Delegate a self-contained sub-task to an independent agent with
        its own context window -- only its final summary comes back, so its
        intermediate steps don't clutter your own conversation.

        Args:
            instructions: the sub-agent's system prompt / role.
            prompt: the specific task for the sub-agent to do.
            tool_names: comma-separated names of your own tools to grant the
                sub-agent, e.g. "read_file,write_file". Empty for a
                pure-reasoning sub-agent with no tools.
        """
        selected = []
        for name in (n.strip() for n in tool_names.split(",")):
            if not name:
                continue
            if name not in tools_by_name:
                raise ValueError(f"Unknown tool for a sub-agent: {name!r}")
            selected.append(tools_by_name[name])

        sub_agent = build_langgraph_agent(
            model, selected, instructions, checkpointer=child_checkpointer
        )
        # Deterministic across parent reruns -- tool_call_id is injected by
        # LangChain from the actual ToolCall and stays identical across the
        # parent tool node's own interrupt/resume replays (see docstring).
        child_config = {"configurable": {"thread_id": f"spawn-{tool_call_id}"}}

        state = sub_agent.get_state(child_config)
        if not state.values:
            sub_agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]}, config=child_config
            )

        rounds = 0
        while rounds < max_rounds:
            state = sub_agent.get_state(child_config)
            if not state.next:
                break
            pending = state.tasks[0].interrupts[0].value
            decision = interrupt(pending)
            sub_agent.invoke(Command(resume=decision), config=child_config)
            rounds += 1

        final_state = sub_agent.get_state(child_config)
        last = final_state.values["messages"][-1]
        # extract_text, not raw .content -- a Gemini "thinking" reply's
        # content is a list of blocks including a large opaque signature
        # blob, not a plain string; returning that raw would leak a huge,
        # useless payload into the parent conversation's tool result
        # (confirmed live: a real Gemini call produced exactly this shape).
        content = extract_text(getattr(last, "content", None))
        if final_state.next:
            return (
                f"(sub-agent still had a pending approval after the {max_rounds}-round cap -- "
                "raise max_rounds if this sub-task genuinely needs more approval round-trips)"
            )
        return content or "(sub-agent produced no text reply)"

    return spawn_agent


# LibreOffice's own PNG export (render_pptx_preview et al) has no
# explicit width/height set (see tools/_thumbnail.py), so a slide preview
# commonly lands well past this on its long edge -- and vision APIs
# charge per-pixel-patch (Anthropic: ceil(w/28) * ceil(h/28) "visual
# tokens", verified live against Anthropic's own docs 2026-09-17), not a
# flat per-image fee, so an oversized image is real, avoidable cost, not
# a one-time flat cost. 1568px matches Claude's own documented "standard
# tier" long-edge cap -- Claude itself silently downscales to at most
# this before scoring an image, so sending anything larger only spends
# extra upload bytes/latency with zero fidelity benefit on that
# provider; for a provider that instead prices roughly by raw pixel
# count (most other vision APIs use a similar patch-tiling scheme),
# this directly cuts the real token cost. A reviewer needs to judge
# layout/legibility/color, not read every pixel at native slide
# resolution, so this tradeoff costs nothing quality-wise that matters
# for that job.
_REVIEW_PREVIEW_MAX_DIMENSION = 1568


def _downscale_preview_for_review(image_bytes: bytes) -> bytes:
    """Resize a rendered slide/page preview PNG so neither dimension
    exceeds `_REVIEW_PREVIEW_MAX_DIMENSION`, preserving aspect ratio --
    a no-op (returns the original bytes untouched) if it's already
    smaller, so a small preview never gets needlessly re-encoded.
    Real, live-reported cost problem this addresses: a real multi-slide
    review pass (render every slide, call review_work with all of them)
    sent every preview at LibreOffice's own uncapped export resolution,
    a meaningful and entirely avoidable slice of a run that measured
    ~9.3M tokens for one deck.

    Never raises: same best-effort contract render_thumbnail's own
    docstring establishes for preview generation -- a corrupt/
    unreadable/unusual-format image falls back to the original bytes
    unchanged (the review still proceeds, just without the token
    saving) rather than failing the whole review_work call over a
    preview-resizing problem that isn't the point of calling it."""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    max_dim = _REVIEW_PREVIEW_MAX_DIMENSION
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            if image.width <= max_dim and image.height <= max_dim:
                return image_bytes
            resized = image.copy()
            resized.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buffer = BytesIO()
            resized.save(buffer, format="PNG")
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError):
        return image_bytes


def build_review_work_tool(
    model: Any,
    reviewer_tools: Sequence[Callable[..., Any] | BaseTool],
    state_dir: Path,
) -> Callable[..., Any]:
    """Return a review_work tool bound to `model` -- spawn_agent's fixed-
    Reviewer sibling (REVIEWER_INSTRUCTIONS above, reused
    unmodified rather than duplicated, same "reuse coscribe's own
    definitions" principle agent.py already follows for ToolMetadata).

    `reviewer_tools` should be read-only (the caller is expected to filter
    to exactly that, e.g. by ToolMetadata.requires_approval) -- none of
    spawn_agent's nested-interrupt machinery is needed here specifically
    *because* of that: a Reviewer with only read-only tools can never
    pause on HumanInTheLoopMiddleware, so a plain single-turn `.invoke()`
    (which itself may loop through several tool calls internally before
    returning) always runs straight to completion without ever raising
    interrupt(). No InjectedToolCallId/thread_id tracking either, for the
    same reason spawn_agent needs it (surviving the parent node's own
    resume-replay) never applies: there's nothing to resume.

    `state_dir` resolves a bare preview filename (exactly what a write
    tool's own `preview_path` field returns) to the real file under
    `state_dir/previews/`, the same lookup `GET /api/previews/{name}`
    already does -- no path-traversal surface, it's a single-directory
    filename match, not a caller-supplied path.
    """

    def review_work(
        original_request: str,
        summary_of_work: str,
        file_path: str = "",
        preview_name: str = "",
    ) -> str:
        """Ask a fresh reviewer (who didn't do the work) to check it against
        the original request before you finalize or act on it -- use this
        after a sub-agent finishes, after writing a multi-page/multi-slide
        or heavily formatted document, or before a high-risk/irreversible
        action, to catch what you might have missed. The reviewer has its
        own read-only tools and will independently read file_path itself
        rather than just trusting summary_of_work -- and if preview_name is
        given, it looks at the actual rendered image(s), not just a
        description of them.

        Args:
            original_request: what the user actually asked for
            summary_of_work: what was done, or is about to be done
            file_path: the file to independently check, relative to the
                workspace root -- the reviewer will read it with its own
                tools rather than trusting summary_of_work alone
            preview_name: one or more of a write tool's own `preview_path`
                value(s), comma-separated -- e.g. render_pptx_preview's
                `preview_paths_csv` for a full multi-slide check. Lets the
                reviewer see the actual rendered page(s)/slide(s), not just
                a text description of them; a single-slide check that only
                looks at slide 1 can miss real problems on the rest of a
                deck.
        """
        reviewer = build_langgraph_agent(model, reviewer_tools, REVIEWER_INSTRUCTIONS)
        prompt_text = (
            f"Original request:\n{original_request}\n\nWork summary:\n{summary_of_work}\n\n"
        )
        if file_path:
            prompt_text += (
                f"File to independently check: {file_path} -- read it yourself, "
                "don't just trust the summary above.\n\n"
            )

        content: str | list[dict[str, Any]] = prompt_text
        preview_names = [name.strip() for name in preview_name.split(",") if name.strip()]
        if preview_names:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            for name in preview_names:
                try:
                    image_bytes = (state_dir / "previews" / name).read_bytes()
                except OSError:
                    continue
                image_bytes = _downscale_preview_for_review(image_bytes)
                encoded = base64.b64encode(image_bytes).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    }
                )
            if len(blocks) > 1:
                content = blocks

        # build_langgraph_agent always attaches a checkpointer (defaults to
        # a fresh InMemorySaver()), which requires a thread_id in .invoke()'s
        # config regardless of whether anything actually needs to be
        # resumed later -- a real, previously-latent bug this call
        # surfaced (review_work had never actually been exercised against
        # a real .invoke() before). Each call is one-shot, so a fresh
        # thread_id per call is correct, not a workaround.
        thread_id = uuid.uuid4().hex
        result = reviewer.invoke(
            {"messages": [{"role": "user", "content": content}]},
            config={"configurable": {"thread_id": thread_id}},
        )
        last = result["messages"][-1]
        # extract_text, not raw .content -- see build_spawn_agent_tool's
        # identical comment; confirmed live against Gemini that a plain
        # attribute read on a "thinking" reply leaks a huge signature blob.
        reply = extract_text(getattr(last, "content", None))
        return reply or "(reviewer produced no text reply)"

    return review_work
