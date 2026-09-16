import io
import zipfile
from pathlib import Path

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.skills import (
    SkillUploadError,
    build_skill_tools,
    format_skill_listing,
    load_builtin_skills,
    load_skills,
    save_uploaded_skill,
)


def _write_skill(skills_dir: Path, name: str, content: str) -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


SIMPLE_SKILL = """\
---
name: demo
description: A demo skill for testing.
---

# Demo Skill

Do the demo thing.
"""


def test_missing_dir_returns_empty_and_gets_created(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    assert not skills_dir.exists()

    skills = load_skills(skills_dir)

    assert skills == []
    assert skills_dir.is_dir()


def test_parses_well_formed_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo", SIMPLE_SKILL)

    skills = load_skills(tmp_path)

    assert len(skills) == 1
    skill = skills[0]
    assert skill.name == "demo"
    assert skill.description == "A demo skill for testing."
    assert skill.body == "# Demo Skill\n\nDo the demo thing."
    assert skill.dir == tmp_path / "demo"


def test_subdirectory_without_skill_md_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "readme.txt").write_text("hi")

    assert load_skills(tmp_path) == []


def test_malformed_skill_is_skipped_but_siblings_still_load(tmp_path: Path) -> None:
    _write_skill(tmp_path, "broken", "---\nname: broken\n---\nno description field")
    _write_skill(tmp_path, "good", SIMPLE_SKILL)

    skills = load_skills(tmp_path)

    assert [s.name for s in skills] == ["demo"]  # from SIMPLE_SKILL's frontmatter


def test_load_skill_returns_body_and_raises_for_unknown(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo", SIMPLE_SKILL)
    skills = load_skills(tmp_path)
    tools = {tool.__name__: tool for tool in build_skill_tools(skills)}  # type: ignore[attr-defined]

    assert tools["load_skill"](name="demo") == "# Demo Skill\n\nDo the demo thing."
    with pytest.raises(ValueError, match="Unknown skill"):
        tools["load_skill"](name="nope")


def test_read_skill_file_reads_bundled_reference(tmp_path: Path) -> None:
    skill_dir = _write_skill(tmp_path, "demo", SIMPLE_SKILL)
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "notes.md").write_text("extra detail")
    skills = load_skills(tmp_path)
    tools = {tool.__name__: tool for tool in build_skill_tools(skills)}  # type: ignore[attr-defined]

    content = tools["read_skill_file"](name="demo", path="references/notes.md")

    assert content == "extra detail"


def test_read_skill_file_rejects_path_escape(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo", SIMPLE_SKILL)
    (tmp_path / "secret.txt").write_text("nope")
    skills = load_skills(tmp_path)
    tools = {tool.__name__: tool for tool in build_skill_tools(skills)}  # type: ignore[attr-defined]

    with pytest.raises(PermissionError):
        tools["read_skill_file"](name="demo", path="../secret.txt")


def test_read_skill_file_raises_for_unknown_skill_or_missing_file(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo", SIMPLE_SKILL)
    skills = load_skills(tmp_path)
    tools = {tool.__name__: tool for tool in build_skill_tools(skills)}  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="Unknown skill"):
        tools["read_skill_file"](name="nope", path="notes.md")
    with pytest.raises(ValueError, match="does not exist"):
        tools["read_skill_file"](name="demo", path="notes.md")


def test_skill_tools_are_low_risk_no_approval(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo", SIMPLE_SKILL)
    skills = load_skills(tmp_path)
    tools = {tool.__name__: tool for tool in build_skill_tools(skills)}  # type: ignore[attr-defined]

    for name in ("load_skill", "read_skill_file"):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "READ"
        assert metadata.requires_approval is False


def test_format_skill_listing(tmp_path: Path) -> None:
    _write_skill(tmp_path, "demo", SIMPLE_SKILL)
    skills = load_skills(tmp_path)

    listing = format_skill_listing(skills)

    assert "load_skill(name)" in listing
    assert "- demo: A demo skill for testing." in listing


def test_load_builtin_skills_finds_the_four_shipped_skills() -> None:
    # Real content shipped with the package (src/coscribe/builtin_skills/),
    # distinct from load_skills' user-local, gitignored directory -- this
    # is the only test that touches the real bundled files rather than a
    # tmp_path fixture, since load_builtin_skills has no dir parameter to
    # redirect.
    skills = load_builtin_skills()

    names = {s.name for s in skills}
    assert names == {"PPTX Slides", "Excel Spreadsheets", "Word Documents", "Skill Creator"}
    for skill in skills:
        assert skill.description
        assert skill.body


def test_load_builtin_skills_bodies_reference_the_real_tool_names() -> None:
    by_name = {s.name: s for s in load_builtin_skills()}

    assert "add_pptx_scrim" in by_name["PPTX Slides"].body
    assert "icon-list" in by_name["PPTX Slides"].body
    assert "layout: svg" in by_name["PPTX Slides"].body
    assert "fill_pptx_template" in by_name["PPTX Slides"].body
    assert "format_xlsx_cells" in by_name["Excel Spreadsheets"].body
    assert "[TOC]" in by_name["Word Documents"].body


# ---------------------------------------------------------------------
# save_uploaded_skill -- Settings > Skills > Add > Upload skill's real
# backend half (docs/ui-references/skills-add-uploadskills.png).
# ---------------------------------------------------------------------


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_upload_md_file_writes_skill_md_under_its_slug(tmp_path: Path) -> None:
    skill = save_uploaded_skill(tmp_path, "whatever.md", SIMPLE_SKILL.encode("utf-8"))

    assert skill.name == "demo"
    assert (tmp_path / "demo" / "SKILL.md").read_text(encoding="utf-8") == SIMPLE_SKILL
    # A fresh load_skills scan finds it too, not just the return value.
    assert [s.name for s in load_skills(tmp_path)] == ["demo"]


def test_upload_md_file_rejects_malformed_frontmatter(tmp_path: Path) -> None:
    with pytest.raises(SkillUploadError, match="name and description"):
        save_uploaded_skill(tmp_path, "bad.md", b"---\nname: bad\n---\nno description")


def test_upload_md_file_name_collision_is_rejected(tmp_path: Path) -> None:
    save_uploaded_skill(tmp_path, "a.md", SIMPLE_SKILL.encode("utf-8"))

    with pytest.raises(SkillUploadError, match="already exists"):
        save_uploaded_skill(tmp_path, "b.md", SIMPLE_SKILL.encode("utf-8"))


def test_upload_zip_with_top_level_skill_md_and_references(tmp_path: Path) -> None:
    archive = _zip_bytes(
        {
            "SKILL.md": SIMPLE_SKILL,
            "references/notes.md": "extra detail",
        }
    )

    skill = save_uploaded_skill(tmp_path, "demo.zip", archive)

    assert skill.name == "demo"
    notes = tmp_path / "demo" / "references" / "notes.md"
    assert notes.read_text(encoding="utf-8") == "extra detail"


def test_upload_zip_with_skill_md_one_folder_down(tmp_path: Path) -> None:
    archive = _zip_bytes(
        {"demo-skill/SKILL.md": SIMPLE_SKILL, "demo-skill/scripts/run.py": "print(1)"}
    )

    skill = save_uploaded_skill(tmp_path, "bundle.skill", archive)

    assert skill.name == "demo"
    assert (tmp_path / "demo" / "scripts" / "run.py").read_text(encoding="utf-8") == "print(1)"


def test_upload_zip_without_skill_md_is_rejected(tmp_path: Path) -> None:
    archive = _zip_bytes({"readme.txt": "hi"})

    with pytest.raises(SkillUploadError, match="must contain a SKILL.md"):
        save_uploaded_skill(tmp_path, "bad.zip", archive)


def test_upload_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = _zip_bytes({"SKILL.md": SIMPLE_SKILL, "../../escape.txt": "nope"})

    with pytest.raises(SkillUploadError, match="outside its own folder"):
        save_uploaded_skill(tmp_path, "evil.zip", archive)


def test_upload_rejects_unsupported_extension(tmp_path: Path) -> None:
    with pytest.raises(SkillUploadError, match="Unsupported file type"):
        save_uploaded_skill(tmp_path, "notes.txt", b"whatever")


def test_upload_rejects_bad_zip_bytes(tmp_path: Path) -> None:
    with pytest.raises(SkillUploadError, match="Not a valid zip"):
        save_uploaded_skill(tmp_path, "broken.zip", b"not actually a zip")
