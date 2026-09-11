"""Workflows: named, reusable tasks the user configures once through
conversation and re-runs later (on request for now -- a scheduler that
triggers this automatically is a future feature, not built here).

Two modes:
- "chain": a fixed, deterministic sequence of tool calls, replayed in the
  same order every run. The step list is derived from this *thread's own,
  already-executed* tool-call history at save time -- not authored by the
  model as freeform JSON -- because that's the only ground truth this
  system has for "what actually worked". "Zero model calls during replay"
  cuts both ways: a step whose call raises no exception but quietly didn't
  do what it looks like it did (a browser action against a stale page,
  say) sails through unnoticed. WorkflowStep.expect_contains is the fix,
  auto-populated by a one-off model call at save time so this never
  becomes something the user has to think about.
- "agent": only a natural-language summary is remembered; each run hands
  it to a fresh agent loop (the same underlying mechanism spawn_agent
  uses) that decides what to do fresh each time.

This module holds the shared data model (Workflow/WorkflowStep/
WorkflowRun/...), on-disk storage (WorkflowStore/WorkflowRunStore), the
two curator-prompt constants (WORKFLOW_ASSERTION_INSTRUCTIONS/
WORKFLOW_CURATOR_INSTRUCTIONS), and the read-only management tools
(list_workflows/get_workflow/delete_workflow) wired directly into
coordinator.py. *Recording* and *running* a workflow -- the
runtime-specific halves that need a live client/tool_policy/checkpointer
-- live in runtime_lg/workflows.py instead (record_chain_workflow_lg,
record_agent_workflow_lg, propose_workflow_save_lg, run_chain_lg, ...),
called from web/session.py's /startworkflow, /endworkflow, /saveworkflow,
and run_workflow handling. That split is why this module needs nothing
from the old hand-rolled runtime (no Runner, ToolPolicy, or
FileStateStore) despite `record_*`/`run_*` sounding like they belong here.

*Creating* a workflow is deliberately NOT one of the model's free-standing
tools: recording is only ever triggered by the explicit /startworkflow+
/endworkflow and /saveworkflow slash commands, never by the model on its
own initiative. Earlier, a model-callable save_workflow tool existed; it
let the Coordinator decide on its own, based only on prose guidance,
whether and when to persist a workflow, and defaulted to capturing a
long-running thread's *entire* tool-call history if not given explicit
indices. In practice this meant an unrelated later conversation could end
up mechanically replaying stale tool calls an earlier, different
conversation never asked to save -- workflows are global by name (see
WorkflowStore) and outlive /clear by design, same as memory/tasks. Gating
creation behind an explicit user command fixes both halves: the user, not
the model, decides when a workflow is worth keeping, and /startworkflow's
start marker means only steps recorded after it are ever captured, not
the whole thread. A scheduler (not built yet) runs a workflow with zero
new plumbing, via the existing one-shot CLI mode: `coscribe
--thread X --message "Run workflow 'name'." --accept-edits`.

Bare /saveworkflow (no prior /startworkflow) no longer blindly summarizes
the *entire* thread into an agent-mode workflow regardless of content --
runtime_lg's propose_workflow_save_lg runs a curator call (using
WORKFLOW_CURATOR_INSTRUCTIONS below) that decides what's actually
save-worthy (possibly deriving a chain-mode step range on its own, not
just agent-mode text), asks a clarifying question instead of guessing
when the thread covers multiple/unclear tasks, and always previews the
proposed save for the user to confirm before anything is persisted.
/startworkflow+/endworkflow's exact, user-marked range is untouched by
any of this -- it's still the precise-control path when you'd rather mark
the range yourself than have the model guess.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..runtime.types import tool_metadata

# record_chain_workflow_lg's one-off assertion-writer call (runtime_lg/
# workflows.py): a chain-mode replay never involves the model at all, so
# this is its only chance for a step's actual result -- not just "no
# exception" -- to be checked against what real success looked like the
# first time. Deliberately asks for restraint (most steps don't need this)
# since a wrong guess would make a genuinely-successful replay fail loud
# for no reason -- worse than the silent-no-op problem this exists to
# catch.
WORKFLOW_ASSERTION_INSTRUCTIONS = """\
Below is a numbered list of tool calls that were just executed successfully \
while recording a workflow, each with the actual result it produced. This \
exact sequence is about to be saved and replayed again later, unattended -- \
no model will be watching that replay to notice if a step silently didn't \
really do what it looks like it did (e.g. a page navigation whose result \
doesn't actually show the intended page).

For each step, decide: does this step's OWN result already contain a short, \
distinctive piece of text that -- if missing when this exact same call is \
repeated later -- would mean this step almost certainly didn't work? Only \
flag a step when you're confident. Most steps (filling a field, clicking a \
button) don't need this at all; it's mainly useful right after a step whose \
result naturally reports back the state it landed in (a navigation, a \
snapshot, a page/file read). When in doubt, leave it unflagged -- a wrong \
guess breaks a working replay, which is worse than not checking at all.

Respond with ONLY a JSON array, exactly one entry per step in the same \
order, each either null or a short string (a few words, copied or closely \
paraphrased from that step's OWN result -- never from its arguments, never \
from general knowledge). Example for 4 steps: [null, "Enter transaction \
code", null, null]
"""

# propose_workflow_save_lg's curator call (runtime_lg/workflows.py): bare
# /saveworkflow used to unconditionally summarize the *entire* thread into
# an agent-mode workflow, no matter how unrelated or ambiguous its
# contents -- this replaces that blind behavior with a judgment call, so a
# messy or multi-task thread gets a clarifying question instead of a
# silently wrong save. /startworkflow+/endworkflow's exact, user-marked
# range is untouched by this -- this instructs the *same* underlying
# judgment a chain-mode save's start_index already expects, just decided
# by the model instead of a manually-typed command.
WORKFLOW_CURATOR_INSTRUCTIONS = """\
You are deciding what to save when the user asks to save the conversation \
below as a named, reusable workflow. You are NOT executing anything -- \
only deciding what should be captured and how.

You'll see: the proposed workflow name, the full conversation transcript, \
and a numbered list (0-based) of every tool call made in this thread so \
far, in order. You may also see a previous clarification exchange, if the \
user already answered an earlier question from you about this same save.

Decide one of two things:

1. If a clear, save-worthy task is identifiable, decide HOW to capture it,
and always include a short one-line "summary" describing what the saved
workflow does (used as its human-readable description either way):
   - "chain" mode: the task is a specific, deterministic sequence of tool \
calls (the kind that should replay identically every time, no judgment \
needed on replay) -- pick the 0-based index of the FIRST tool call that \
belongs to it; every tool call from that index to the end of the list is \
captured verbatim, exactly as recorded (this mirrors what /startworkflow \
would have marked, had the user typed it before starting). Only pick this \
when the calls from that point on are ALL genuinely part of the same task \
-- if there's unrelated exploration mixed in after the real task's last \
relevant call, don't pick "chain".
   - "agent" mode: the task is better captured as a natural-language \
description (exploratory, judgment-dependent, or doesn't reduce to a \
clean fixed sequence) -- make "summary" itself the full instructions: \
what the goal is, what information/context is needed, and what to do, in \
order, as instructions for an agent picking this up cold in a future \
conversation with no memory of this one.

2. If you genuinely can't tell what should be saved -- the conversation \
covers multiple unrelated tasks with no clear single one to save under \
this name, or it's too vague to identify anything worth capturing -- ask \
ONE short, specific clarifying question instead of guessing. Only do this \
when actually unclear; most conversations have an obvious answer and \
should not be questioned needlessly.

Respond with ONLY a JSON object, one of exactly these three shapes, \
nothing else:
{"decision": "clarify", "question": "..."}
{"decision": "propose", "mode": "chain", "start_index": 0, "summary": "..."}
{"decision": "propose", "mode": "agent", "summary": "..."}
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class WorkflowStep:
    tool_name: str
    arguments: dict[str, Any]
    # A short substring this step's result must contain on replay, or None
    # to skip the check -- the deterministic-replay counterpart to a human
    # glancing at the screen after a risky action. Auto-populated by
    # record_chain_workflow_lg's one-off assertion pass at save time (see
    # WORKFLOW_ASSERTION_INSTRUCTIONS above); never authored by hand, so a
    # user recording a workflow never sees or configures this field at all.
    expect_contains: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "expect_contains": self.expect_contains,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        return cls(
            tool_name=data["tool_name"],
            arguments=dict(data.get("arguments", {})),
            expect_contains=data.get("expect_contains"),
        )


@dataclass
class Workflow:
    name: str
    mode: str  # "chain" | "agent" -- plain str, not typing.Literal,
    # deliberately: no tool function in this codebase uses Literal for a
    # parameter today, and aisuite's schema inference has already broken
    # silently on one exotic annotation before (X | None, see
    # test_gemini_provider.py's history) -- the plain-str-plus-runtime-
    # check pattern task_update's `status` param already uses is proven
    # safe, no reason to risk Literal being another one.
    summary: str
    steps: list[WorkflowStep] = field(default_factory=list)
    source_thread_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str | None = None
    last_run_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode,
            "summary": self.summary,
            "steps": [step.to_dict() for step in self.steps],
            "source_thread_id": self.source_thread_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workflow:
        return cls(
            name=data["name"],
            mode=data["mode"],
            summary=data["summary"],
            steps=[WorkflowStep.from_dict(s) for s in data.get("steps", [])],
            source_thread_id=data.get("source_thread_id", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            last_run_at=data.get("last_run_at"),
            last_run_status=data.get("last_run_status"),
        )


class WorkflowStore:
    """One JSON file per workflow *name* (not per thread -- a workflow must
    outlive the thread it was configured in), same atomic tmp-file +
    os.replace() write TaskToolkit already uses."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "workflows"

    def save(self, workflow: Workflow) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(workflow.name)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(workflow.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, name: str) -> Workflow | None:
        path = self._path_for(name)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            return Workflow.from_dict(json.load(handle))

    def delete(self, name: str) -> bool:
        path = self._path_for(name)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    def list_all(self) -> list[Workflow]:
        if not self.root.is_dir():
            return []
        workflows = []
        for path in sorted(self.root.glob("*.json")):
            with path.open(encoding="utf-8") as handle:
                workflows.append(Workflow.from_dict(json.load(handle)))
        return workflows

    def _path_for(self, name: str) -> Path:
        return self.root / f"{quote(name, safe='')}.json"


@dataclass
class WorkflowStepStatus:
    """One chain step's live status within a WorkflowRun -- distinct from
    WorkflowStep (the saved, replayable definition): this tracks what's
    happening *during a specific run*, not what to run."""

    index: int
    tool_name: str
    status: str  # "pending" | "running" | "done" | "failed" | "stopped"
    detail: str | None = None  # error/reason, set for "failed" and "stopped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "tool_name": self.tool_name,
            "status": self.status,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStepStatus:
        return cls(
            index=data["index"],
            tool_name=data["tool_name"],
            status=data["status"],
            detail=data.get("detail"),
        )


@dataclass
class WorkflowRun:
    """One execution of a saved workflow -- separate from Workflow itself
    (the reusable, named definition) so a "recent runs" view has more than
    just Workflow.last_run_at/last_run_status (a single most-recent
    summary) to show: real run history, and for chain mode, live
    per-step progress while a run is still in flight."""

    run_id: str
    workflow_name: str
    mode: str
    status: str  # "running" | "completed" | "failed" | "stopped"
    started_at: str
    finished_at: str | None = None
    steps: list[WorkflowStepStatus] = field(default_factory=list)  # chain mode only
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_name": self.workflow_name,
            "mode": self.mode,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": [step.to_dict() for step in self.steps],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowRun:
        return cls(
            run_id=data["run_id"],
            workflow_name=data["workflow_name"],
            mode=data["mode"],
            status=data["status"],
            started_at=data["started_at"],
            finished_at=data.get("finished_at"),
            steps=[WorkflowStepStatus.from_dict(s) for s in data.get("steps", [])],
            error=data.get("error"),
        )


class WorkflowRunStore:
    """One JSON file per run *id* (unlike WorkflowStore, which is one file
    per workflow *name* -- a run is a single execution, not a reusable
    definition, so there can be many for the same workflow name)."""

    def __init__(self, state_dir: str | Path) -> None:
        self.root = Path(state_dir) / "workflow_runs"

    def save(self, run: WorkflowRun) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path_for(run.run_id)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(run.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    def load(self, run_id: str) -> WorkflowRun | None:
        path = self._path_for(run_id)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            return WorkflowRun.from_dict(json.load(handle))

    def list_recent(self, limit: int = 20) -> list[WorkflowRun]:
        if not self.root.is_dir():
            return []
        runs = []
        for path in self.root.glob("*.json"):
            with path.open(encoding="utf-8") as handle:
                runs.append(WorkflowRun.from_dict(json.load(handle)))
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    def delete(self, run_id: str) -> bool:
        path = self._path_for(run_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    def _path_for(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json"


def reconcile_interrupted_runs(state_dir: str | Path) -> int:
    """Mark every persisted WorkflowRun still stuck at status="running" as
    failed. Meant to be called once, synchronously, at process startup
    (web/app.py's create_app, cli.py's chat()) -- a run can genuinely never
    still be "running" by the time a *fresh* process starts, since no
    checkpointer-backed run survives a restart; a "running" record found
    here can only mean the previous process died (crashed, was killed,
    lost power) while that run was still in flight, before run_workflow's
    own finalization code (which sets a terminal status) ever got to run.
    Left alone, a run like this stays stuck at "running" forever -- nothing
    will ever revisit it again, and /stop can't help either, since there's
    no live task anywhere actually backing it anymore.

    Returns the number of runs reconciled, for a one-line startup log.
    """
    run_store = WorkflowRunStore(state_dir)
    fixed = 0
    for run in run_store.list_recent(limit=10_000):
        if run.status != "running":
            continue
        run.status = "failed"
        run.error = "Interrupted -- the server restarted while this run was still in progress."
        run.finished_at = _now_iso()
        run_store.save(run)
        fixed += 1
    return fixed


@dataclass
class WorkflowSaveProposal:
    """One curator round's outcome (see runtime_lg/workflows.py's
    propose_workflow_save_lg) -- never persisted; the caller (web/
    session.py's /saveworkflow handler) holds this in memory while
    awaiting the user's answer/confirmation, and only calls
    record_chain_workflow_lg/record_agent_workflow_lg once they confirm.
    `decision` is "clarify" or "propose"; the mode/start_index/summary
    fields are only meaningful when decision == "propose"."""

    decision: str
    question: str | None = None
    mode: str | None = None
    start_index: int | None = None
    summary: str | None = None


def build_workflow_tools(state_dir: str | Path) -> list[Callable[..., Any]]:
    """Return the read-only/management workflow tool callables (list/get/
    delete) -- doesn't need a live client/tool_policy, so this is wired
    directly into coordinator.py. Deliberately does NOT include a "create a
    workflow" tool -- see this module's docstring for why that's gated
    behind /startworkflow+/endworkflow/saveworkflow instead, and does NOT
    include list_recorded_steps -- web/session.py builds its own version of
    that tool, backed by the LangGraph checkpointer's real message history
    (see ChatSessionLG._build_list_recorded_steps_tool), since this
    runtime has no equivalent of the old FileStateStore this function used
    to read from.
    """
    store = WorkflowStore(state_dir)

    def list_workflows() -> list[dict[str, Any]]:
        """List all saved workflows with their name, mode, and summary."""
        return [w.to_dict() for w in store.list_all()]

    def get_workflow(name: str) -> dict[str, Any]:
        """Get full detail on one saved workflow, including its chain steps if
        any. Read-only, for inspection -- e.g. checking what's saved, or
        picking a resume_from_step index. Do NOT treat the returned
        `summary` as instructions to carry out yourself: for "agent" mode
        workflows in particular, it reads like a task description because
        it's meant to be handed to run_workflow's own fresh sub-agent, not
        acted on directly in this turn. If the user wants the workflow
        actually run, call run_workflow(name) -- never manually replay the
        steps/summary yourself as a substitute for that call, even if you
        can see exactly what they'd involve. Doing both (manually acting on
        get_workflow's summary, then also calling run_workflow) executes
        the workflow twice, with real duplicate side effects if it does
        anything beyond reading (e.g. a browser-automation query actually
        running against a live system twice).

        Args:
            name: the workflow's name
        """
        workflow = store.load(name)
        if workflow is None:
            raise KeyError(f"No workflow named {name!r}")
        return workflow.to_dict()

    def delete_workflow(name: str) -> dict[str, Any]:
        """Permanently delete a saved workflow.

        Args:
            name: the workflow's name
        """
        if not store.delete(name):
            raise KeyError(f"No workflow named {name!r}")
        return {"deleted": name}

    return [
        tool_metadata(list_workflows, risk_category="READ", category="workflows"),
        tool_metadata(get_workflow, risk_category="READ", category="workflows"),
        tool_metadata(delete_workflow, risk_category="WRITE_LOCAL", category="workflows"),
    ]
