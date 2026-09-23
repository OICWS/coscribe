from pathlib import Path

from coscribe.config import Settings
from coscribe.coordinator import build_coordinator_agent

SIMPLE_SKILL = """\
---
name: demo
description: A demo skill for testing.
---

Do the demo thing.
"""


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "default_model": "anthropic:claude-sonnet-4-5",
        "workspace_root": tmp_path / "workspace",
        "state_dir": tmp_path / "state",
        "skills_dir": tmp_path / "skills",
        # memory_path defaults to "./MEMORY.md" (cwd-relative) if not given
        # here -- pin it under tmp_path so a real MEMORY.md left behind by a
        # manual `coscribe`/`coscribe-web` run in this repo (cwd at
        # test time) can never leak into a test that isn't specifically
        # testing memory_path itself.
        "memory_path": tmp_path / "MEMORY.md",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_instructions_do_not_contain_the_current_date(tmp_path: Path) -> None:
    """Caching regression test: the real-world-date grounding (originally
    a live-reported-bug fix -- with no ground truth for "today," the model
    fell back to its training-cutoff guess, got real correctly-dated
    web_search results back that didn't match that guess, and concluded
    its own tool must be broken) used to be baked into instructions here,
    at build_coordinator_agent time. That put the one genuinely volatile
    string in this whole prompt at the very front of an otherwise-frozen
    system prompt -- poisoning the entire prefix for Anthropic
    cache_control breakpoints, and just as surely defeating Gemini/
    OpenAI-compatible providers' automatic prefix-based caching too. It
    now lives in per-turn message content instead (current_date_note in
    runtime_lg/messages.py, injected by web/session.py's
    _handle_user_message_locked) -- see test_web.py's
    test_date_note_is_per_turn_message_content_not_baked_into_the_system_prompt
    for that half. This test guards the system-prompt side: instructions
    must stay date-free so it can genuinely stay frozen across turns."""
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")

    assert agent.instructions is not None
    assert "Today's real date" not in agent.instructions


def test_empty_local_skills_dir_still_gets_the_builtin_skills(tmp_path: Path) -> None:
    # No local (user-authored) skill exists, but the three package-shipped
    # builtin skills (pptx/excel/word) are always spliced in when
    # skill_names is left at its default (None = "all") -- unlike before
    # load_builtin_skills existed, an empty skills_dir no longer means "no
    # skills at all."
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")

    tool_names = [tool.__name__ for tool in agent.tools]  # type: ignore[attr-defined]
    assert "load_skill" in tool_names
    assert "read_skill_file" in tool_names
    assert agent.instructions is not None
    assert "PPTX Slides:" in agent.instructions
    assert "Excel Spreadsheets:" in agent.instructions
    assert "Word Documents:" in agent.instructions


def test_skills_dir_with_a_skill_adds_it_alongside_the_builtin_ones(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SIMPLE_SKILL, encoding="utf-8")

    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")

    tool_names = [tool.__name__ for tool in agent.tools]  # type: ignore[attr-defined]
    assert "load_skill" in tool_names
    assert "read_skill_file" in tool_names
    assert agent.instructions is not None
    assert "demo: A demo skill for testing." in agent.instructions
    assert "PPTX Slides:" in agent.instructions


def test_skill_creator_is_a_builtin_skill_with_a_skills_dir_note(tmp_path: Path) -> None:
    """Skill Creator ships as a built-in (src/coscribe/builtin_skills/
    skill-creator/) like PPTX/Excel/Word -- always loaded unless
    skill_names filters it out. Its whole point is writing new SKILL.md
    files into settings.skills_dir via write_file, so build_coordinator_
    agent should both name that exact path in the instructions (right
    after the skill listing) and actually make it reachable to the file
    tools -- see the next test for the reachability half."""
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")

    assert agent.instructions is not None
    assert "Skill Creator: Interview the user" in agent.instructions
    assert str(tmp_path / "skills") in agent.instructions


def test_skills_dir_note_absent_when_skill_creator_is_filtered_out(tmp_path: Path) -> None:
    agent = build_coordinator_agent(
        _settings(tmp_path), "thread-1", skill_names={"PPTX Slides"}
    )

    assert agent.instructions is not None
    assert "Skill Creator" not in agent.instructions
    assert str(tmp_path / "skills") not in agent.instructions


def test_write_file_can_actually_reach_skills_dir(tmp_path: Path) -> None:
    """The whole Skill Creator skill is pointless if write_file can't
    actually reach settings.skills_dir -- it's outside workspace_root, so
    without this it would be rejected by WorkspaceScope the same way any
    other out-of-bounds path is. Exercises the real tool function (not
    just checking instructions text) to prove the path is genuinely
    writable, not just mentioned."""
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")
    tools_by_name = {tool.__name__: tool for tool in agent.tools}  # type: ignore[attr-defined]

    skill_path = str(tmp_path / "skills" / "demo" / "SKILL.md")
    tools_by_name["write_file"](
        skill_path, "---\nname: demo\ndescription: A demo.\n---\n\nDo the thing.\n"
    )

    assert (tmp_path / "skills" / "demo" / "SKILL.md").is_file()
    # And readable back too -- Skill Creator checks existing skills first.
    content = tools_by_name["read_file"](skill_path)
    assert "Do the thing." in content


def test_skill_names_none_means_every_skill_unfiltered(tmp_path: Path) -> None:
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1", skill_names=None)

    assert agent.instructions is not None
    assert "PPTX Slides:" in agent.instructions
    assert "Excel Spreadsheets:" in agent.instructions
    assert "Word Documents:" in agent.instructions


def test_skill_names_subset_only_splices_those(tmp_path: Path) -> None:
    agent = build_coordinator_agent(
        _settings(tmp_path), "thread-1", skill_names={"PPTX Slides"}
    )

    assert agent.instructions is not None
    assert "PPTX Slides:" in agent.instructions
    assert "Excel Spreadsheets:" not in agent.instructions
    assert "Word Documents:" not in agent.instructions
    tool_names = [tool.__name__ for tool in agent.tools]  # type: ignore[attr-defined]
    assert "load_skill" in tool_names


def test_skill_names_empty_set_splices_no_skills_at_all(tmp_path: Path) -> None:
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1", skill_names=set())

    tool_names = [tool.__name__ for tool in agent.tools]  # type: ignore[attr-defined]
    assert "load_skill" not in tool_names
    assert "read_skill_file" not in tool_names
    assert "Available skills" not in (agent.instructions or "")


def test_remember_tool_present_even_with_no_memory_file_yet(tmp_path: Path) -> None:
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")

    tool_names = [tool.__name__ for tool in agent.tools]  # type: ignore[attr-defined]
    assert "remember" in tool_names
    assert agent.instructions is not None
    assert "Remembered facts" not in agent.instructions


def test_all_tool_schemas_are_gemini_compatible(tmp_path: Path) -> None:
    """Regression test for a real bug: aisuite's Tools.__infer_from_signature
    (the schema builder every provider's tool spec comes from, including
    Gemini's) only unwraps `typing.Optional[X]` -- it checks
    `get_origin(t) is Union`, and PEP 604 `X | None` has origin
    `types.UnionType` instead, so it slips through unwrapped and gets
    serialized as the literal string "X | None" for the JSON-schema "type"
    field. Gemini's strict OpenAPI-subset schema validation then rejects the
    whole request with 400 INVALID_ARGUMENT (hit for real with read_xlsx's
    `sheet` and read_pptx's `slide` params). Guard every current and future
    built-in tool against reintroducing this by asserting every parameter's
    schema "type" is one of the real JSON Schema primitives."""
    from aisuite.utils.tools import Tools

    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")
    specs = Tools(list(agent.tools)).tools("openai")  # type: ignore[arg-type]

    valid_types = {"string", "integer", "number", "boolean", "array", "object"}
    for spec in specs:
        function = spec["function"]
        for param_name, schema in function["parameters"]["properties"].items():
            assert schema["type"] in valid_types, (
                f"{function['name']}'s {param_name!r} param has schema type "
                f"{schema['type']!r} -- likely an unwrapped `X | None` annotation"
            )


def test_remembered_facts_appear_in_instructions_once_file_has_content(tmp_path: Path) -> None:
    memory_path = tmp_path / "MEMORY.md"
    memory_path.write_text("- The user prefers metric units.\n", encoding="utf-8")

    agent = build_coordinator_agent(_settings(tmp_path, memory_path=memory_path), "thread-1")

    assert agent.instructions is not None
    assert "Remembered facts from earlier sessions:" in agent.instructions
    assert "The user prefers metric units." in agent.instructions


def test_no_extra_dirs_configured_adds_no_instructions_note(tmp_path: Path) -> None:
    agent = build_coordinator_agent(_settings(tmp_path), "thread-1")

    assert agent.instructions is not None
    assert "outside the workspace root" not in agent.instructions


def test_extra_dirs_are_named_by_path_in_instructions(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    shared = tmp_path / "shared"
    agent = build_coordinator_agent(
        _settings(tmp_path, extra_readable_dirs=[downloads], extra_writable_dirs=[shared]),
        "thread-1",
    )

    assert agent.instructions is not None
    assert str(downloads) in agent.instructions
    assert str(shared) in agent.instructions
    assert "(read only)" in agent.instructions
    assert "(read and write)" in agent.instructions
    assert "check these directories" in agent.instructions
    # Regression: the model previously only checked extra dirs when the user
    # explicitly said "readable"/"writable directory" -- a broad "what files
    # do you have access to" only looked at workspace_root.
    assert "check the workspace root" in agent.instructions
    assert "not just the workspace root by default" in agent.instructions


def test_writable_dir_is_not_also_listed_as_read_only(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    agent = build_coordinator_agent(
        _settings(tmp_path, extra_readable_dirs=[shared], extra_writable_dirs=[shared]),
        "thread-1",
    )

    assert agent.instructions is not None
    assert agent.instructions.count(str(shared)) == 1
    assert "(read and write)" in agent.instructions
