import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from coscribe.workflows.engine import (
    StepContext,
    StepRecord,
    WorkflowNotRunnable,
    WorkflowRun,
    fields_schema,
)
from coscribe.workflows.spec import parse_workflow

FIXTURE = Path(__file__).parent / "fixtures" / "pdf_error_audit_workflow.json"

MATCHES = [
    {"path": "big.pdf", "page": 97, "text": "Error code E-0097: account 4410-97"},
    {"path": "big.pdf", "page": 194, "text": "Error code E-0194: account 4410-194"},
]
GOOD_SUMMARY = {
    "error_count": 2,
    "accounts": ["4410-97", "4410-194"],
    "boilerplate": True,
    "summary": "Two generated lines.",
}


class FakeModel(BaseChatModel):
    """Answers each call with the next scripted step_result tool call."""

    results: list[Any]
    i: int = 0
    received: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        self.received.append(list(messages))
        result = self.results[self.i]
        self.i += 1
        call = {"name": "step_result", "args": result, "id": f"c{self.i}"}
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))]
        )

    @property
    def _llm_type(self) -> str:
        return "fake-structured"


class Harness:
    def __init__(self, tmp_path: Path, model_results: list[Any]) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.write_failures = 0
        self.scripts: list[str] = []
        self.records: list[StepRecord] = []
        self.model = FakeModel(results=model_results)
        self.ctx = StepContext(
            tools={
                "search_pdf": self.search_pdf,
                "write_file": self.write_file,
                "read_file": lambda path: "",
            },
            workspace_root=tmp_path,
            state_dir=tmp_path / "state",
            make_model=lambda name: self.model,
            run_script=self.run_script,
        )

    def search_pdf(self, query: str, path: str = ".", regex: bool = False) -> list[dict[str, Any]]:
        self.calls.append(("search_pdf", {"query": query, "path": path, "regex": regex}))
        return list(MATCHES)

    def write_file(self, path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
        self.calls.append(("write_file", {"path": path, "content": content}))
        if self.write_failures:
            self.write_failures -= 1
            raise PermissionError("disk is read-only")
        return {"path": path}

    def run_script(
        self, root: Path, state_dir: Path, script: str, timeout: float
    ) -> dict[str, object]:
        self.scripts.append(script)
        return {
            "exit_code": 0,
            "stdout": 'loading\n{"pages": 650}\n',
            "stderr": "",
            "timed_out": False,
        }

    def on_step(self, record: StepRecord) -> None:
        self.records.append(record)

    def final_status(self) -> dict[str, str]:
        latest: dict[str, str] = {}
        for record in self.records:
            latest[record.step_id] = record.status
        return latest

    def run(self, checkpointer: Any, workflow: Any = None, thread_id: str = "run-1") -> WorkflowRun:
        workflow = workflow or parse_workflow(json.loads(FIXTURE.read_text(encoding="utf-8")))
        return WorkflowRun(workflow, self.ctx, checkpointer, thread_id, self.on_step)


def _tool_names(h: Harness) -> list[str]:
    return [name for name, _ in h.calls]


async def test_the_audit_runs_end_to_end(tmp_path: Path) -> None:
    h = Harness(tmp_path, [GOOD_SUMMARY])

    outcome = await h.run(InMemorySaver()).start({"pdf_path": "big.pdf"})

    assert outcome.status == "completed"
    assert h.final_status() == {
        "search": "done",
        "search_variants": "done",
        "searches_agree": "done",
        "count_pages": "done",
        "summarize": "done",
        "reconcile": "done",
        "review": "skipped",  # boilerplate, so no review needed
        "write_report": "done",
    }
    assert h.calls[1] == (
        "search_pdf",
        {"query": "(?i)error\\s*code", "path": "big.pdf", "regex": True},
    )
    report = h.calls[-1][1]["content"]
    assert "File: big.pdf (650 pages)" in report and "Error lines: 2" in report
    assert outcome.values["page_count"] == {"pages": 650}
    reconcile = next(r for r in h.records if r.step_id == "reconcile" and r.status == "done")
    assert reconcile.checks == [
        {"held": True, "left": 2, "right": 2},
        {"held": True, "left": 2, "right": 2},
    ]


async def test_a_failed_check_stops_the_run_and_retrying_from_an_earlier_step_fixes_it(
    tmp_path: Path,
) -> None:
    short = {**GOOD_SUMMARY, "accounts": ["4410-97"]}
    h = Harness(tmp_path, [short, GOOD_SUMMARY])
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        run = h.run(checkpointer)
        failed = await run.start({})

        assert failed.status == "failed" and failed.step_id == "reconcile"
        assert failed.error == "1 of 2 checks failed"
        assert "write_report" not in h.final_status()
        failed_record = h.records[-1]
        assert failed_record.checks == [
            {"held": True, "left": 2, "right": 2},
            {"held": False, "left": 1, "right": 2},
        ]

        retried = await run.retry_from("summarize")

    assert retried.status == "completed"
    # Only the model step onward ran again; the searches and script did not.
    assert _tool_names(h) == ["search_pdf", "search_pdf", "write_file"]
    assert len(h.scripts) == 1
    assert h.model.i == 2


async def test_resume_reruns_only_the_step_that_failed(tmp_path: Path) -> None:
    h = Harness(tmp_path, [GOOD_SUMMARY])
    h.write_failures = 1
    run = h.run(InMemorySaver())

    failed = await run.start({})
    assert failed.status == "failed" and failed.step_id == "write_report"
    assert failed.error == "disk is read-only"

    resumed = await run.resume()

    assert resumed.status == "completed"
    assert _tool_names(h) == ["search_pdf", "search_pdf", "write_file", "write_file"]
    assert h.model.i == 1


async def test_an_approval_step_waits_and_its_answer_is_honoured(tmp_path: Path) -> None:
    real_incidents = {**GOOD_SUMMARY, "boilerplate": False}
    h = Harness(tmp_path, [real_incidents])
    run = h.run(InMemorySaver())

    waiting = await run.start({})
    assert waiting.status == "waiting" and waiting.step_id == "review"
    assert waiting.request is not None and "Two generated lines." in waiting.request["message"]
    assert "write_file" not in _tool_names(h)

    done = await run.answer(approved=True, note="checked the totals")

    assert done.status == "completed"
    assert next(r for r in h.records if r.step_id == "review" and r.status == "done").note == (
        "checked the totals"
    )
    assert _tool_names(h).count("write_file") == 1
    assert h.final_status()["review"] == "done"


async def test_declining_an_approval_fails_the_run_there(tmp_path: Path) -> None:
    h = Harness(tmp_path, [{**GOOD_SUMMARY, "boilerplate": False}])
    run = h.run(InMemorySaver())
    await run.start({})

    declined = await run.answer(approved=False, note="numbers look off")

    assert declined.status == "failed" and declined.step_id == "review"
    assert declined.error == "Not approved: numbers look off"
    assert "write_file" not in _tool_names(h)

    asked_again = await run.retry_from("review")
    assert asked_again.status == "waiting" and asked_again.step_id == "review"
    assert (await run.answer(approved=True)).status == "completed"
    assert _tool_names(h).count("write_file") == 1


async def test_a_model_step_gets_one_retry_with_what_was_wrong(tmp_path: Path) -> None:
    missing = {"error_count": 2, "accounts": ["4410-97", "4410-194"], "boilerplate": "yes"}
    h = Harness(tmp_path, [missing, GOOD_SUMMARY])

    outcome = await h.run(InMemorySaver()).start({})

    assert outcome.status == "completed"
    feedback = h.model.received[1][-1].content
    assert "summary is missing" in feedback and "boilerplate must be boolean" in feedback


async def test_a_model_step_fails_after_two_bad_answers(tmp_path: Path) -> None:
    h = Harness(tmp_path, [{"error_count": "two"}, {"error_count": "two"}])

    outcome = await h.run(InMemorySaver()).start({})

    assert outcome.status == "failed" and outcome.step_id == "summarize"
    assert outcome.error is not None and "error_count must be number" in outcome.error


async def test_script_inputs_can_never_become_code(tmp_path: Path) -> None:
    hostile = "x'); import os; os.system('echo pwned'); ('"
    workflow = parse_workflow(
        {
            "inputs": [{"name": "name"}],
            "steps": [
                {
                    "id": "echo",
                    "kind": "script",
                    "title": "Echo",
                    "code": "import json\nprint(json.dumps(inputs))",
                    "inputs": {"name": "{{name}}"},
                    "save_as": "echoed",
                }
            ],
        }
    )

    def real_python(root: Path, state_dir: Path, script: str, timeout: float) -> dict[str, object]:
        done = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
        )
        return {
            "exit_code": done.returncode,
            "stdout": done.stdout,
            "stderr": done.stderr,
            "timed_out": False,
        }

    h = Harness(tmp_path, [])
    h.ctx.run_script = real_python
    outcome = await h.run(InMemorySaver(), workflow).start({"name": hostile})

    assert outcome.status == "completed"
    assert outcome.values["echoed"] == {"name": hostile}


async def test_a_script_that_prints_no_json_fails_clearly(tmp_path: Path) -> None:
    h = Harness(tmp_path, [GOOD_SUMMARY])
    h.ctx.run_script = lambda *a: {
        "exit_code": 0,
        "stdout": "650 pages\n",
        "stderr": "",
        "timed_out": False,
    }

    outcome = await h.run(InMemorySaver()).start({})

    assert outcome.step_id == "count_pages"
    assert outcome.error == "The script's last line isn't JSON: '650 pages'"


@pytest.mark.parametrize(
    ("tool", "message"),
    [
        ("delete_everything", "no tool called 'delete_everything'"),
        ("ask_user_question", "can't be used"),
    ],
)
async def test_preflight_refuses_a_workflow_that_cant_run(
    tmp_path: Path, tool: str, message: str
) -> None:
    workflow = parse_workflow({"steps": [{"id": "a", "kind": "tool", "title": "A", "tool": tool}]})
    h = Harness(tmp_path, [])
    h.ctx.tools["ask_user_question"] = lambda **kw: None

    with pytest.raises(WorkflowNotRunnable, match=message):
        await h.run(InMemorySaver(), workflow).start({})
    assert h.records == []


async def test_an_input_without_a_value_or_default_is_refused(tmp_path: Path) -> None:
    workflow = parse_workflow(
        {
            "inputs": [{"name": "path"}],
            "steps": [
                {
                    "id": "a",
                    "kind": "tool",
                    "title": "A",
                    "tool": "read_file",
                    "args": {"path": "{{path}}"},
                }
            ],
        }
    )

    with pytest.raises(WorkflowNotRunnable, match="input 'path' needs a value"):
        await Harness(tmp_path, []).run(InMemorySaver(), workflow).start({})


def test_fields_schema_requires_every_field_and_nothing_else() -> None:
    fields = parse_workflow(json.loads(FIXTURE.read_text(encoding="utf-8"))).steps[4].fields  # type: ignore[union-attr]
    schema = fields_schema(fields)

    assert schema["required"] == ["error_count", "accounts", "boilerplate", "summary"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["accounts"]["type"] == "array"


class NoForcedToolsModel(BaseChatModel):
    """Refuses forced tool calls, like DeepSeek's thinking mode; answers
    plain requests with the scripted text."""

    replies: list[str]
    i: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        raise ValueError("Thinking mode does not support this tool_choice")

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        reply = self.replies[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])

    @property
    def _llm_type(self) -> str:
        return "fake-no-forced-tools"


async def test_a_model_that_refuses_forced_tool_calls_is_asked_for_json(tmp_path: Path) -> None:
    h = Harness(tmp_path, [])
    reply = "Here you go:\n```json\n" + json.dumps(GOOD_SUMMARY) + "\n```"
    h.ctx.make_model = lambda name: NoForcedToolsModel(replies=[reply])

    outcome = await h.run(InMemorySaver()).start({})

    assert outcome.status == "completed"
    assert outcome.values["summary"] == GOOD_SUMMARY


async def test_a_workflow_with_no_steps_saves_but_wont_run(tmp_path: Path) -> None:
    workflow = parse_workflow({"inputs": [], "steps": []})

    with pytest.raises(WorkflowNotRunnable, match="no steps yet"):
        await Harness(tmp_path, []).run(InMemorySaver(), workflow).start({})


# -- Branches and loops -----------------------------------------------------------


class Files:
    """A read tool per file, one of which can be made to fail a set number
    of times."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.failing: dict[str, int] = {}

    def read_file(self, path: str) -> str:
        self.reads.append(path)
        if self.failing.get(path, 0) > 0:
            self.failing[path] -= 1
            raise FileNotFoundError(f"{path} is locked")
        return f"text of {path}"

    def list_files(self, path: str = ".") -> list[dict[str, Any]]:
        return [{"path": "a.txt"}, {"path": "b.txt"}, {"path": "c.txt"}]


def _loop_workflow(body_extra: list[Any] | None = None, **loop: Any) -> Any:
    return parse_workflow(
        {
            "steps": [
                {"id": "ls", "kind": "tool", "title": "List", "tool": "list_files",
                 "args": {}, "save_as": "files"},
                {"id": "each", "kind": "loop", "title": "Each file", "over": "files",
                 "item": "file", "collect": "text", "save_as": "texts", **loop,
                 "steps": [
                     {"id": "read", "kind": "tool", "title": "Read", "tool": "read_file",
                      "args": {"path": "{{file.path}}"}, "save_as": "text"},
                     *(body_extra or []),
                 ]},
                {"id": "enough", "kind": "check", "title": "Enough",
                 "conditions": [{"left": {"count": "texts"}, "op": "eq", "right": {"value": 3}}]},
            ]
        }
    )  # fmt: skip


def _files_harness(tmp_path: Path) -> tuple[Harness, Files]:
    h, files = Harness(tmp_path, []), Files()
    h.ctx.tools.update(read_file=files.read_file, list_files=files.list_files)
    return h, files


def _records(h: Harness, step_id: str) -> list[tuple[str, list[int]]]:
    return [(r.status, r.iteration) for r in h.records if r.step_id == step_id]


async def test_a_loop_runs_its_steps_per_item_and_collects_a_list(tmp_path: Path) -> None:
    h, files = _files_harness(tmp_path)

    outcome = await h.run(InMemorySaver(), _loop_workflow()).start({})

    assert outcome.status == "completed"
    assert files.reads == ["a.txt", "b.txt", "c.txt"]
    assert outcome.values["texts"] == ["text of a.txt", "text of b.txt", "text of c.txt"]
    assert _records(h, "read") == [
        ("running", [0]), ("done", [0]), ("running", [1]), ("done", [1]),
        ("running", [2]), ("done", [2]),
    ]  # fmt: skip
    loop_records = [r for r in h.records if r.step_id == "each"]
    assert [r.output["done"] for r in loop_records] == [0, 1, 2, 3, 3]
    assert loop_records[0].output["items"] == ["a.txt", "b.txt", "c.txt"]
    assert loop_records[-1].status == "done"
    assert loop_records[-1].output["collected"][0] == "text of a.txt"


async def test_a_failure_inside_a_loop_resumes_at_that_item(tmp_path: Path) -> None:
    h, files = _files_harness(tmp_path)
    files.failing["b.txt"] = 1
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        run = h.run(checkpointer, _loop_workflow())
        failed = await run.start({})

        assert failed.status == "failed" and failed.step_id == "read"
        loop_record = [r for r in h.records if r.step_id == "each"][-1]
        assert loop_record.status == "failed"
        assert loop_record.error == "Stopped at item 2: b.txt is locked"

        resumed = await run.retry_from("read")

    assert resumed.status == "completed"
    assert files.reads == ["a.txt", "b.txt", "b.txt", "c.txt"]
    assert resumed.values["texts"][1] == "text of b.txt"
    assert ("running", []) in _records(h, "each")[-3:]


async def test_a_branch_takes_one_arm_and_a_value_both_set_is_read_after(tmp_path: Path) -> None:
    def workflow(threshold: int) -> Any:
        return parse_workflow(
            {
                "inputs": [{"name": "limit", "type": "number", "default": threshold}],
                "steps": [
                    {"id": "ls", "kind": "tool", "title": "List", "tool": "list_files",
                     "args": {}, "save_as": "files"},
                    {"id": "many", "kind": "branch", "title": "Many files?",
                     "condition": {"left": {"count": "files"}, "op": "gt",
                                   "right": {"ref": "limit"}},
                     "then": [{"id": "big", "kind": "tool", "title": "Big", "tool": "read_file",
                               "args": {"path": "big.txt"}, "save_as": "text"}],
                     "otherwise": [{"id": "small", "kind": "tool", "title": "Small",
                                    "tool": "read_file", "args": {"path": "small.txt"},
                                    "save_as": "text"}]},
                    {"id": "save", "kind": "tool", "title": "Save", "tool": "write_file",
                     "args": {"path": "out.txt", "content": "{{text}}"}},
                ],
            }
        )  # fmt: skip

    h, files = _files_harness(tmp_path)
    taken = await h.run(InMemorySaver(), workflow(2), "t1").start({})
    skipped = await h.run(InMemorySaver(), workflow(5), "t2").start({})

    assert taken.status == skipped.status == "completed"
    assert files.reads == ["big.txt", "small.txt"]
    assert [c[1]["content"] for c in h.calls] == ["text of big.txt", "text of small.txt"]
    branch_outputs = [r.output for r in h.records if r.step_id == "many" and r.status == "done"]
    assert branch_outputs == [{"arm": "then"}, {"arm": "otherwise"}]


async def test_an_approval_inside_a_loop_waits_on_each_item(tmp_path: Path) -> None:
    approve = {"id": "ok", "kind": "approval", "title": "OK?", "message": "Keep {{file.path}}?"}
    h, files = _files_harness(tmp_path)
    run = h.run(InMemorySaver(), _loop_workflow([approve]))

    outcome = await run.start({})
    messages = []
    while outcome.status == "waiting":
        messages.append(outcome.request["message"] if outcome.request else None)
        outcome = await run.answer(True)

    assert outcome.status == "completed"
    assert messages == ["Keep a.txt?", "Keep b.txt?", "Keep c.txt?"]
    assert ("waiting", []) in _records(h, "each")


@pytest.mark.parametrize(
    ("loop", "error"),
    [
        ({"max_items": 2}, "files has 3 items, more than this loop's limit of 2"),
        ({"over": "files.0"}, "files.0 is dict, not a list to go through"),
    ],
)
async def test_a_loop_refuses_what_it_cant_go_through(
    tmp_path: Path, loop: dict[str, Any], error: str
) -> None:
    h, files = _files_harness(tmp_path)

    outcome = await h.run(InMemorySaver(), _loop_workflow(**loop)).start({})

    assert outcome.status == "failed" and outcome.step_id == "each"
    assert outcome.error == error
    assert files.reads == []


async def test_nested_loops_record_both_positions(tmp_path: Path) -> None:
    inner = {
        "id": "twice", "kind": "loop", "title": "Twice", "over": "pair", "item": "n",
        "steps": [{"id": "touch", "kind": "tool", "title": "Touch", "tool": "read_file",
                   "args": {"path": "{{file.path}}-{{n}}"}}],
    }  # fmt: skip
    workflow = parse_workflow(
        {
            "inputs": [],
            "steps": [
                {"id": "ls", "kind": "tool", "title": "List", "tool": "list_files",
                 "args": {}, "save_as": "files"},
                {"id": "pairs", "kind": "script", "title": "Pair", "code": "print('[1, 2]')",
                 "save_as": "pair"},
                {"id": "each", "kind": "loop", "title": "Each", "over": "files", "item": "file",
                 "steps": [inner]},
            ],
        }
    )  # fmt: skip
    h, files = _files_harness(tmp_path)
    h.ctx.run_script = lambda *a: {"exit_code": 0, "stdout": "[1, 2]", "stderr": ""}

    outcome = await h.run(InMemorySaver(), workflow).start({})

    assert outcome.status == "completed"
    assert files.reads == ["a.txt-1", "a.txt-2", "b.txt-1", "b.txt-2", "c.txt-1", "c.txt-2"]
    done = [r.iteration for r in h.records if r.step_id == "touch" and r.status == "done"]
    assert done == [[0, 0], [0, 1], [1, 0], [1, 1], [2, 0], [2, 1]]
    assert [r.iteration for r in h.records if r.step_id == "twice" and r.status == "done"] == [
        [0], [1], [2],
    ]  # fmt: skip


async def test_an_empty_loop_or_branch_is_refused_before_running(tmp_path: Path) -> None:
    h, _files = _files_harness(tmp_path)
    workflow = parse_workflow(
        {"steps": [{"id": "ls", "kind": "tool", "title": "List", "tool": "list_files",
                    "args": {}, "save_as": "files"},
                   {"id": "each", "kind": "loop", "title": "Each", "over": "files", "steps": []}]}
    )  # fmt: skip
    with pytest.raises(WorkflowNotRunnable, match="step 2: the loop has no steps to repeat yet"):
        await h.run(InMemorySaver(), workflow).start({})


def test_item_labels_use_the_field_that_tells_items_apart() -> None:
    from coscribe.workflows.engine import _item_labels

    hits = [{"path": "big.pdf", "page": 97, "text": "Error code E-0097"},
            {"path": "big.pdf", "page": 194, "text": "Error code E-0194"}]  # fmt: skip
    assert _item_labels(hits) == ["Error code E-0097", "Error code E-0194"]
    assert _item_labels(["a", 3, {"x": 1}]) == ["a", "3", '{"x": 1}']
    assert _item_labels(["word " * 30]) == [("word " * 12).strip()[:59] + "…"]
