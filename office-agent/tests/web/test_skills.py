# ruff: noqa: E402
"""Web tests: the skill routes and the skills a conversation uses."""

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
)

from .helpers import (
    FakeToolCallingChatModel,
    _client_lg,
    _receive_until,
)


def test_get_commands_lists_skills_by_slug_not_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/commands' "name" field is what the frontend inserts verbatim
    after "/" (Composer.tsx's selectAutocomplete) -- for a skill it has to
    be SkillInfo.slug (a single token, e.g. "skill-creator"), never
    skill.name (a display label that can contain spaces, e.g. "Skill
    Creator") -- a space there could never be typed as one command word
    to begin with. See the WS-level test below for the matching half."""
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/commands")

    commands = {command["name"]: command["description"] for command in response.json()}
    assert "skill-creator" in commands
    assert "word" in commands
    assert "Skill Creator" not in commands
    assert "Word Documents" not in commands


def test_slash_skill_slug_force_loads_a_multiword_named_skill_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, previously-shipped-but-untested bug:
    web/session.py's own skills_by_slug (then named skills_by_name) used
    to be keyed by skill.name.lower() -- for "Skill Creator" that's
    "skill creator", with a space -- but _handle_user_message_locked only
    ever looks up the single word before the first space in the typed
    text ("/word", rest="creator ..."), so no multi-word-named skill
    (every current built-in) could ever actually be force-loaded this
    way. Now keyed by SkillInfo.slug, a single command-safe token."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ok")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_slash_skill") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/skill-creator name a new skill"})
            _receive_until(ws, "tasks_changed")

    assert len(fake_model.received) == 1
    human_messages = [m for m in fake_model.received[0] if isinstance(m, HumanMessage)]
    assert human_messages, "expected a human message in the request"
    text = human_messages[-1].content
    assert isinstance(text, str)
    assert "Skill 'Skill Creator' invoked directly via /skill-creator" in text
    assert "Mirrors Claude Code's own bundled skill-creator" in text
    assert "User request: name a new skill" in text


def test_get_skills_lists_the_builtin_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills")

    assert response.status_code == 200
    names = {s["name"] for s in response.json()}
    assert names == {"PPTX Slides", "Excel Spreadsheets", "Word Documents", "Skill Creator"}


def test_get_skills_tags_builtin_vs_custom_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "skills" / "mine").mkdir(parents=True)
    (tmp_path / "skills" / "mine" / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills")

    by_name = {s["name"]: s["source"] for s in response.json()}
    assert by_name["PPTX Slides"] == "builtin"
    assert by_name["mine"] == "custom"


def test_get_skill_files_lists_a_builtin_skills_real_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.parse import quote

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get(f"/api/skills/{quote('PPTX Slides')}/files")

    assert response.status_code == 200
    assert "SKILL.md" in response.json()["files"]


def test_get_skill_files_lists_nested_paths_for_a_custom_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    (skill_dir / "reference").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    (skill_dir / "reference" / "notes.md").write_text("some notes", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files")

    assert response.status_code == 200
    assert sorted(response.json()["files"]) == ["SKILL.md", "reference/notes.md"]


def test_get_skill_files_paths_are_relative_to_the_skill_with_a_relative_skills_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The shipped default is skills_dir="./skills", so listed paths must be
    # ones the file endpoint can open, not "skills/mine/SKILL.md".
    monkeypatch.chdir(tmp_path)
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, skills_dir=Path("skills")) as client:
        files = client.get("/api/skills/mine/files").json()["files"]
        content = client.get(f"/api/skills/mine/files/{files[0]}")

    assert files == ["SKILL.md"]
    assert content.status_code == 200


def test_get_skill_files_unknown_skill_name_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/no-such-skill/files")

    assert response.status_code == 404


def test_get_skill_file_content_returns_the_real_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nreal body text", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files/SKILL.md")

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "SKILL.md"
    assert "real body text" in body["content"]


def test_get_skill_file_content_rejects_a_path_traversal_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    # A real secret file the traversal attempt tries to escape the skill's
    # own directory to reach -- state_dir is a sibling of skills_dir, both
    # directly under tmp_path (see _client_lg's own settings construction).
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "secret.txt").write_text("do not leak this", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files/../../state/secret.txt")

    assert response.status_code in (400, 404)
    assert "do not leak this" not in response.text


def test_get_skill_file_content_missing_file_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files/no-such-file.md")

    assert response.status_code == 404


def test_get_skill_file_content_unknown_skill_name_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/no-such-skill/files/SKILL.md")

    assert response.status_code == 404


def test_upload_skill_md_appears_in_get_skills_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real regression this guards: skills_by_name used to be a
    # closure snapshot taken once at startup (same staleness shape as
    # switch_model's custom-providers bug) -- an uploaded skill wouldn't
    # show up in GET /api/skills, or be offered to a new session, until
    # the process restarted.
    fake_model = FakeToolCallingChatModel(responses=[])
    md_content = "---\nname: mine\ndescription: my own skill\n---\nbody"
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        upload = client.post(
            "/api/skills/upload",
            files={"file": ("mine.md", md_content, "text/markdown")},
        )
        assert upload.status_code == 200
        assert upload.json() == {"name": "mine", "description": "my own skill", "source": "custom"}

        response = client.get("/api/skills")
        names = {s["name"] for s in response.json()}
        assert "mine" in names

        with client.websocket_connect("/ws/t_upload_skill") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history
    assert "mine" in state["enabled_skills"]


def test_upload_skill_rejects_malformed_skill_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/skills/upload",
            files={"file": ("bad.md", "not even frontmatter", "text/markdown")},
        )

    assert response.status_code == 400
    assert "frontmatter" in response.json()["error"]


def test_upload_skill_rejects_unsupported_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/skills/upload",
            files={"file": ("notes.txt", "whatever", "text/plain")},
        )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["error"]


def test_new_thread_defaults_to_the_builtin_skills_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skills_default") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert sorted(state["enabled_skills"]) == [
        "Excel Spreadsheets",
        "PPTX Slides",
        "Skill Creator",
        "Word Documents",
    ]


def test_switching_a_skill_off_applies_everywhere_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ok")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skill_off_open") as ws:
            ws.receive_json()
            ws.receive_json()
            off = client.post("/api/skills/PPTX%20Slides/enabled", json={"enabled": False})
            ws.send_json({"type": "user_message", "text": "hi"})
            _receive_until(ws, "tasks_changed")
        listing = {s["name"]: s for s in client.get("/api/skills").json()}
        with client.websocket_connect("/ws/t_skill_off_new") as ws:
            state = ws.receive_json()
            ws.receive_json()

    assert off.json() == {"name": "PPTX Slides", "enabled": False}
    assert listing["PPTX Slides"]["enabled"] is False
    assert listing["PPTX Slides"]["source"] == "builtin"
    assert "PPTX Slides" not in state["enabled_skills"]
    # The already-open conversation dropped it at its next turn.
    assert "- PPTX Slides:" not in str(fake_model.received[0][0].content)
    assert "- Word Documents:" in str(fake_model.received[0][0].content)


def test_removing_a_skill_deletes_it_but_builtins_only_switch_off_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    md_content = "---\nname: mine\ndescription: my own skill\n---\nbody"
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post("/api/skills/upload", files={"file": ("mine.md", md_content, "text/markdown")})
        removed = client.delete("/api/skills/mine")
        builtin = client.delete("/api/skills/PPTX%20Slides")
        names = {s["name"] for s in client.get("/api/skills").json()}

    assert removed.json() == {"removed": "mine"}
    assert "mine" not in names
    assert builtin.status_code == 400
    assert "PPTX Slides" in names


def test_adding_a_discover_skill_downloads_and_checks_it_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from coscribe.tools import skill_catalog

    skill_md = b"---\nname: tiny\ndescription: a tiny skill\n---\nDo tiny things."
    catalog = {
        "repo": "anthropics/skills",
        "commit": "abc",
        "skills": [
            {
                "name": "tiny",
                "description": "a tiny skill",
                "license": "Apache-2.0",
                "files": [
                    {"path": "SKILL.md", "size": 1, "sha256": hashlib.sha256(skill_md).hexdigest()},
                    {
                        "path": "scripts/run.py",
                        "size": 1,
                        "sha256": hashlib.sha256(b"x").hexdigest(),
                    },  # noqa: E501
                ],
            }
        ],
    }
    monkeypatch.setattr(skill_catalog, "load_catalog", lambda: catalog)
    monkeypatch.setattr("coscribe.web.routes.skills.load_catalog", lambda: catalog)
    served = {"SKILL.md": skill_md, "scripts/run.py": b"tampered"}
    fetched: list[str] = []

    def fake_download(url: str) -> bytes:
        fetched.append(url)
        return served[url.split("/skills/tiny/", 1)[1]]

    monkeypatch.setattr(skill_catalog, "_download", fake_download)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        before = client.get("/api/skills/catalog").json()
        bad = client.post("/api/skills/catalog/tiny")
        left_behind = [p.name for p in (tmp_path / "skills").iterdir()]
        served["scripts/run.py"] = b"x"
        good = client.post("/api/skills/catalog/tiny")
        listing = {s["name"]: s for s in client.get("/api/skills").json()}
        files = client.get("/api/skills/tiny/files").json()["files"]
        after = client.get("/api/skills/catalog").json()
        again = client.post("/api/skills/catalog/tiny")

    assert [(e["name"], e["added"]) for e in before] == [("tiny", False)]
    assert bad.status_code == 400 and "expected contents" in bad.json()["error"]
    assert left_behind == []
    assert good.json() == {"added": "tiny"}
    assert (
        fetched[0] == "https://raw.githubusercontent.com/anthropics/skills/abc/skills/tiny/SKILL.md"
    )
    assert listing["tiny"]["source"] == "anthropic"
    assert listing["tiny"]["enabled"] is True
    assert files == ["SKILL.md", "scripts/run.py"]
    assert after[0]["added"] is True
    assert again.status_code == 400


def test_saveskill_writes_skill_md_and_makes_it_usable_immediately_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/saveskill's happy path: one curator call proposes description+body
    (name always stays whatever the user typed), "yes" confirms, and the file
    actually lands at <skills_dir>/<slug>/SKILL.md with valid frontmatter.
    Also proves the real point of set_enabled_skills' own skills_by_slug
    refresh: /<slug> force-loads the brand-new skill in this *same*
    session right after saving it, no reconnect needed."""
    curator_decision = json.dumps(
        {
            "decision": "propose",
            "description": "Load when asked to write the weekly status report.",
            "body": "1. Pull last week's numbers.\n2. Summarize wins and blockers.",
        }
    )
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="here's this week's report"),
            AIMessage(content=curator_decision),
            AIMessage(content="following the skill now"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write the weekly status report"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill Weekly Report Format"})
            preview = ws.receive_json()
            assert preview["type"] == "agent_message"
            assert "weekly-report-format" in preview["text"]
            assert "Pull last week's numbers" in preview["text"]
            ws.send_json({"type": "user_message", "text": "yes"})
            saved = ws.receive_json()
            assert saved == {
                "type": "skill_saved",
                "name": "Weekly Report Format",
                "slug": "weekly-report-format",
            }
            state = ws.receive_json()
            assert state["type"] == "state"
            assert "Weekly Report Format" in state["enabled_skills"]

            # /<slug> force-loads it right away, same session, no reconnect.
            ws.send_json({"type": "user_message", "text": "/weekly-report-format go"})
            _receive_until(ws, "tasks_changed")

    skill_path = tmp_path / "skills" / "weekly-report-format" / "SKILL.md"
    assert skill_path.is_file()
    text = skill_path.read_text(encoding="utf-8")
    assert "name: Weekly Report Format" in text
    assert "description: Load when asked to write the weekly status report." in text
    assert "Pull last week's numbers" in text

    assert len(fake_model.received) == 3
    human_messages = [m for m in fake_model.received[-1] if isinstance(m, HumanMessage)]
    assert "Skill 'Weekly Report Format' invoked directly via /weekly-report-format" in (
        human_messages[-1].content
    )


def test_saveskill_asks_a_clarifying_question_when_curator_is_unsure_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curator_decision = json.dumps(
        {"decision": "clarify", "question": "What should this skill teach?"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="ok, done"), AIMessage(content=curator_decision)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "did a bunch of unrelated stuff"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill mystery"})
            question = ws.receive_json()

    assert question == {"type": "agent_message", "text": "What should this skill teach?"}
    assert not (tmp_path / "skills" / "mystery").exists()


def test_saveskill_refuses_to_shadow_a_builtin_skill_slug_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slug colliding with a built-in (e.g. "word", from load_builtin_
    skills()) would silently shadow it for every future /<slug> lookup --
    skills_by_slug keeps whichever of load_builtin_skills()/load_skills()
    is scanned last for a repeated key. Refused outright rather than
    silently letting a new local skill hijack a built-in's command."""
    curator_decision = json.dumps(
        {"decision": "propose", "description": "Load whenever.", "body": "Do the thing."}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="ok"), AIMessage(content=curator_decision)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hi"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill Word"})
            preview = ws.receive_json()
            assert preview["type"] == "agent_message"
            ws.send_json({"type": "user_message", "text": "yes"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "/word is already a built-in skill" in error["message"]
    assert not (tmp_path / "skills" / "word").exists()


def test_saveskill_discarded_on_a_non_yes_answer_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curator_decision = json.dumps(
        {"decision": "propose", "description": "Load whenever.", "body": "Do the thing."}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="ok"), AIMessage(content=curator_decision)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hi"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill throwaway"})
            ws.receive_json()  # preview
            ws.send_json({"type": "user_message", "text": "no thanks"})
            discarded = ws.receive_json()

    assert discarded["type"] == "agent_message"
    assert "Discarded" in discarded["text"]
    assert not (tmp_path / "skills" / "throwaway").exists()
