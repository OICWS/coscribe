import json
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from coscribe.workflows.solidify import (
    DraftFailed,
    check_draft,
    draft_workflow,
    render_conversation,
)
from coscribe.workflows.spec import parse_workflow


class ScriptedModel(BaseChatModel):
    replies: list[str]
    i: int = 0
    received: list[list[BaseMessage]] = []

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        self.received.append(list(messages))
        reply = self.replies[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])

    @property
    def _llm_type(self) -> str:
        return "fake-curator"


def search_pdf(path: str, query: str, regex: bool = False) -> list[dict[str, Any]]:
    """Find lines in a PDF.

    Args:
        path: the PDF to search
        query: what to look for
        regex: treat query as a regular expression
    """
    return []


def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """Write a text file."""
    return "ok"


TOOLS = {"search_pdf": search_pdf, "write_file": write_file}


def _call(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


CONVERSATION = [
    HumanMessage("Find every 'Error code' line in big.pdf and write a report."),
    _call("c1", "list_directory", {"path": "."}),
    ToolMessage('["big.pdf"]', tool_call_id="c1"),
    _call("c2", "search_pdf", {"path": "big.pdf", "query": "Eror code"}),
    ToolMessage("no such page", tool_call_id="c2", status="error"),
    _call("c3", "search_pdf", {"path": "big.pdf", "query": "Error code"}),
    ToolMessage('[{"page": 97, "text": "Error code E-0097"}]', tool_call_id="c3"),
    _call("c4", "write_file", {"path": "report.md", "content": "1 error", "overwrite": True}),
    ToolMessage('"ok"', tool_call_id="c4"),
    AIMessage("Found 1 error line and wrote report.md."),
]

GOOD_WORKFLOW = {
    "inputs": [{"name": "pdf_path", "label": "PDF", "type": "file", "default": "big.pdf"}],
    "steps": [
        {
            "id": "search",
            "kind": "tool",
            "title": "Search",
            "tool": "search_pdf",
            "args": {"path": "{{pdf_path}}", "query": "Error code"},
            "save_as": "matches",
        },
        {
            "id": "found_some",
            "kind": "check",
            "title": "Found something",
            "conditions": [{"left": {"ref": "matches"}, "op": "not_empty"}],
        },
    ],
}


def _reply(workflow: dict[str, Any], **extra: Any) -> str:
    return json.dumps({"name": "Error audit", "workflow": workflow, "notes": ["check"], **extra})


def test_the_transcript_numbers_calls_and_marks_failures() -> None:
    transcript, used = render_conversation(CONVERSATION)
    assert '[tool call #3] search_pdf({"path": "big.pdf", "query": "Error code"})' in transcript
    assert "[tool call #2, FAILED] search_pdf" in transcript
    assert "error: no such page" in transcript
    assert transcript.startswith("[user] Find every 'Error code' line")
    assert used == ["list_directory", "search_pdf", "write_file"]


def test_a_long_transcript_keeps_its_beginning_and_end() -> None:
    messages: list[Any] = [HumanMessage("the task")]
    for n in range(400):
        messages += [_call(f"c{n}", "search_pdf", {"path": "a.pdf", "query": "x" * 200})]
        messages += [ToolMessage("[]", tool_call_id=f"c{n}")]
    messages.append(AIMessage("all done"))
    transcript, _ = render_conversation(messages)
    assert transcript.startswith("[user] the task")
    assert transcript.endswith("[assistant] all done")
    assert "middle of the conversation left out" in transcript


async def test_drafts_a_workflow_that_holds_up() -> None:
    model = ScriptedModel(replies=["Here it is:\n" + _reply(GOOD_WORKFLOW)])
    draft = await draft_workflow(model, CONVERSATION, TOOLS)
    assert draft.name == "Error audit"
    assert draft.notes == ["check"]
    assert draft.workflow == parse_workflow(GOOD_WORKFLOW).model_dump(mode="json")
    [system, human] = model.received[0]
    assert "search_pdf(path: text -- the PDF to search" in str(human.content)
    # Tools it never used successfully aren't offered.
    assert "list_directory(" not in str(human.content).split("The conversation:")[0]


async def test_a_given_name_wins_over_the_curators() -> None:
    model = ScriptedModel(replies=[_reply(GOOD_WORKFLOW)])
    draft = await draft_workflow(model, CONVERSATION, TOOLS, name_hint="Monthly audit")
    assert draft.name == "Monthly audit"
    assert 'Name the workflow "Monthly audit"' in str(model.received[0][1].content)


async def test_an_invalid_draft_goes_back_with_its_problems() -> None:
    bad = json.loads(json.dumps(GOOD_WORKFLOW))
    bad["steps"][0]["args"]["pages"] = "1-5"
    bad["steps"][1]["conditions"][0]["left"] = {"ref": "hits"}
    model = ScriptedModel(replies=[_reply(bad), "not json at all", _reply(GOOD_WORKFLOW)])
    draft = await draft_workflow(model, CONVERSATION, TOOLS)
    assert draft.workflow["steps"][0]["args"] == {"path": "{{pdf_path}}", "query": "Error code"}
    second_try = str(model.received[1][-1].content)
    assert "reads 'hits'" in second_try
    third_try = str(model.received[2][-1].content)
    assert "no JSON object" in third_try


async def test_gives_up_after_three_bad_drafts() -> None:
    unknown_tool = json.loads(json.dumps(GOOD_WORKFLOW))
    unknown_tool["steps"][0]["tool"] = "search_everything"
    model = ScriptedModel(replies=[_reply(unknown_tool)] * 3)
    with pytest.raises(DraftFailed, match="no tool called 'search_everything'"):
        await draft_workflow(model, CONVERSATION, TOOLS)
    assert model.i == 3


async def test_the_curator_can_say_theres_nothing_to_repeat() -> None:
    model = ScriptedModel(replies=['{"error": "This was a one-off question."}'])
    with pytest.raises(DraftFailed, match="one-off question"):
        await draft_workflow(model, CONVERSATION, TOOLS)


async def test_a_conversation_without_tool_calls_is_refused_without_asking() -> None:
    model = ScriptedModel(replies=[])
    with pytest.raises(DraftFailed, match="hasn't used any tools"):
        await draft_workflow(model, [HumanMessage("hi"), AIMessage("hello")], TOOLS)


def test_check_draft_catches_what_the_spec_cant() -> None:
    workflow = parse_workflow(
        {
            "steps": [
                {"id": "a", "kind": "tool", "title": "A", "tool": "search_pdf", "args": {}},
                {"id": "b", "kind": "tool", "title": "B", "tool": "run_python_script"},
                {"id": "c", "kind": "tool", "title": "C", "tool": "ask_user_question"},
            ]
        }
    )
    problems = check_draft(workflow, {**TOOLS, "run_python_script": print})
    assert problems == [
        "step 1: search_pdf needs path, query",
        "step 2: use a script step instead of calling run_python_script",
        "step 3: there's no tool called 'ask_user_question' to use",
    ]
    assert check_draft(parse_workflow({"steps": []}), TOOLS) == ["the workflow has no steps"]


def test_check_draft_looks_inside_loops_and_branches() -> None:
    workflow = parse_workflow(
        {
            "inputs": [{"name": "files", "default": "x"}],
            "steps": [
                {"id": "each", "kind": "loop", "title": "Each", "over": "files", "steps": [
                    {"id": "b", "kind": "branch", "title": "B",
                     "condition": {"left": {"ref": "item"}, "op": "not_empty"},
                     "then": [{"id": "x", "kind": "tool", "title": "X", "tool": "summon_file",
                               "args": {}}]},
                ]},
            ],
        }
    )  # fmt: skip
    assert check_draft(workflow, TOOLS) == ["step 3: there's no tool called 'summon_file' to use"]


def test_check_draft_wants_a_field_not_a_whole_model_result_collected() -> None:
    loop = {
        "id": "each", "kind": "loop", "title": "Each", "over": "files", "collect": "summary",
        "save_as": "summaries",
        "steps": [{"id": "sum", "kind": "llm", "title": "Sum", "prompt": "{{item}}",
                   "fields": [{"name": "text"}], "save_as": "summary"}],
    }  # fmt: skip
    data = {"inputs": [{"name": "files", "default": "x"}], "steps": [loop]}
    assert check_draft(parse_workflow(data), TOOLS) == [
        "step 1: collect a field of summary (like summary.<field>), not the whole result"
    ]
    loop["collect"] = "summary.text"
    assert check_draft(parse_workflow(data), TOOLS) == []
