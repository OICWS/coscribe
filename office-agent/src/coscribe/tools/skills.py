"""Skill loading: SKILL.md discovery and progressive-disclosure tools.

Same convention as Claude Code's own skills (see ARCHITECTURE.md's "Skill
加载机制"): a Skill is a directory with a SKILL.md (YAML frontmatter
name+description, then markdown instructions), plus optional
scripts/references/assets. Metadata (name+description) is always visible to
the Coordinator via its instructions; the full body is loaded on demand via
load_skill. Bundled scripts can be read via read_skill_file but not run --
coscribe has no code-execution tool yet, so script-dependent skills are
only partially usable until that lands.

Two sources, both parsed the same way: `load_skills(settings.skills_dir)`
scans a user/deployment-local directory (gitignored, empty by default --
nothing ships there) for skills a user or org authors themselves;
`load_builtin_skills()` scans `src/coscribe/builtin_skills/`, coscribe's
own bundled skills (pptx/excel/word design guidance), shipped with the
package so a fresh install has real content without the user writing any
SKILL.md themselves. `coordinator.py` combines both.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..runtime.types import tool_metadata

logger = logging.getLogger(__name__)


def slugify_skill_name(name: str) -> str:
    """Kebab-case a skill's display name into its directory slug -- e.g.
    "Weekly Report Format" -> "weekly-report-format". Same convention the
    Skill Creator meta-skill's own step 5 already documents by hand
    (`builtin_skills/skill-creator/SKILL.md`); this is the mechanical
    version /saveskill (runtime_lg/skill_authoring.py) uses instead of
    asking the curator model to invent one -- a slug has to be
    deterministic and filesystem-safe, not a judgment call."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "skill"


# /saveskill's curator call (runtime_lg/skill_authoring.py's
# propose_skill_save_lg) -- the alternative to tools/workflows.py's own
# /saveworkflow, deciding what to write into a NEW skill's description/
# body instead of a workflow's mode/steps. See that module's own
# docstring for why this is a separate mechanism, not unified with
# workflow-save's own curator.
SKILL_SAVE_CURATOR_INSTRUCTIONS = """\
You are deciding what to write when the user asks to save the
conversation below as a reusable Skill named "<name>" (a SKILL.md
coscribe can load again in a future, unrelated conversation). You are
NOT executing anything -- only writing the skill's own description and
body.

You'll see the proposed skill name and the full conversation transcript.
You may also see a previous clarification exchange, if the user already
answered an earlier question from you about this same save.

A Skill is generalized, reusable KNOWLEDGE OR PROCEDURE -- how to do a
kind of task -- not a literal transcript of this one conversation, and
not a fixed sequence of tool calls replayed with the same exact
arguments every time (that's what a saved *workflow* already does --
don't write another one of those under a different name). Write the
body as direct instructions to a future agent picking this up cold,
generalized from what actually happened here: what the task is, what to
watch out for (a real mistake made and corrected in this conversation is
worth calling out explicitly, since it's exactly the kind of thing worth
not repeating), what good output looks like. Strip out anything specific
to just this one instance (a particular file name, a particular number)
unless it's a genuinely fixed part of the procedure every time, not an
example value.

Decide one of two things:

1. If a clear, generalizable procedure is identifiable in the
transcript, write it:
   - "description": one line stating the trigger condition -- when a
     future agent should load this skill. This is the ONLY thing visible
     before the skill is loaded, so it has to be specific enough to fire
     at the right moment and not otherwise. State it as "load when/before
     X", not a vague summary of the content.
   - "body": the actual instructions, in Markdown -- direct guidance to
     a future agent, not commentary about the skill itself.

2. If you genuinely can't tell what should be captured -- the
conversation covers multiple unrelated tasks with no single clear
procedure, or there isn't enough here yet to generalize from -- ask ONE
short, specific clarifying question instead of guessing. Only do this
when actually unclear; most conversations with a real task in them have
an obvious answer and should not be questioned needlessly.

Respond with ONLY a JSON object, one of exactly these two shapes,
nothing else:
{"decision": "clarify", "question": "..."}
{"decision": "propose", "description": "...", "body": "..."}
"""


@dataclass
class SkillInfo:
    name: str
    description: str
    dir: Path
    body: str

    @property
    def slug(self) -> str:
        """The single-token identifier for the /<slug> chat command (see
        web/session.py's skills_by_name and web/app.py's /api/commands) --
        the skill's own directory name, lowercased. `name` is a human-
        readable display label that can contain spaces ("Skill Creator",
        "Word Documents"), so it can't be typed as a single command token
        directly; the directory name is already guaranteed unique (one
        skill per directory, per _scan_skills_dir) and already matches
        this convention for every skill the Skill Creator meta-skill
        itself creates (its own step 5: "pick a kebab-case directory slug
        from the skill's name"), so this reuses it rather than deriving a
        second, possibly-colliding slug from `name` independently."""
        return self.dir.name.lower()


def load_skills(skills_dir: str | Path) -> list[SkillInfo]:
    """Scan skills_dir's immediate subdirectories for a SKILL.md each.

    A subdirectory without one isn't a skill (silently skipped); a SKILL.md
    that's missing or has malformed frontmatter is a skill someone meant to
    write but got wrong -- logged and skipped, not fatal to startup.
    """
    return _scan_skills_dir(Path(skills_dir), create_if_missing=True)


_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent.parent / "builtin_skills"


def load_builtin_skills() -> list[SkillInfo]:
    """Scan coscribe's own bundled skills (src/coscribe/builtin_skills/) --
    shipped with the package itself, distinct from load_skills'
    settings.skills_dir, which is a user/deployment-local, gitignored
    directory (see .gitignore's `/skills/`) that never ships any content.
    Built-in skills need this separate, fixed location so a fresh install
    has real skill content available without the user authoring their own
    SKILL.md files first -- exactly the non-technical-user gap this exists
    to close."""
    return _scan_skills_dir(_BUILTIN_SKILLS_DIR, create_if_missing=False)


def _scan_skills_dir(root: Path, *, create_if_missing: bool) -> list[SkillInfo]:
    if create_if_missing:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.is_dir():
        return []
    skills = []
    for entry in sorted(root.iterdir()):
        skill_md = entry / "SKILL.md"
        if not entry.is_dir() or not skill_md.is_file():
            continue
        try:
            skills.append(_parse_skill(entry, skill_md))
        except Exception:
            logger.warning("Skipping skill in %r: failed to parse SKILL.md", entry, exc_info=True)
    return skills


def parse_skill_frontmatter(text: str) -> tuple[str, str, str]:
    """Parse a SKILL.md's YAML frontmatter (name, description) and body --
    shared by disk-scanning (_parse_skill) and upload validation
    (save_uploaded_skill) below, so both apply the exact same rules."""
    try:
        _, raw_frontmatter, body = text.split("---", 2)
    except ValueError:
        raise ValueError("SKILL.md must start with a '---' YAML frontmatter block") from None
    frontmatter = yaml.safe_load(raw_frontmatter) or {}
    name, description = frontmatter.get("name"), frontmatter.get("description")
    if not name or not description:
        raise ValueError("SKILL.md frontmatter needs name and description")
    return str(name), str(description), body.strip()


def _parse_skill(skill_dir: Path, skill_md: Path) -> SkillInfo:
    name, description, body = parse_skill_frontmatter(skill_md.read_text(encoding="utf-8"))
    return SkillInfo(name=name, description=description, dir=skill_dir, body=body)


class SkillUploadError(ValueError):
    """Raised by save_uploaded_skill on anything the caller should surface
    to the user as a 400, not a 500 -- bad structure, name collision, a
    zip entry that escapes its own folder."""


def save_uploaded_skill(skills_dir: str | Path, filename: str, content: bytes) -> SkillInfo:
    """Validate and write an uploaded skill into skills_dir/<slug>/ -- the
    real half of Settings > Skills > Add > Upload skill
    (docs/ui-references/skills-add-uploadskills.png). Two accepted shapes,
    matching that screenshot's own bullet points:
    - a bare `.md` file: becomes skills_dir/<slug>/SKILL.md directly.
    - a `.zip`/`.skill` archive: must contain a SKILL.md, either at the
      archive's top level or one directory level down (the shape you get
      zipping a folder named after the skill) -- any sibling
      scripts/references/assets entries are extracted as-is, matching the
      standard skill folder layout this module's own docstring describes;
      no per-file validation beyond the zip-slip guard below.
    No security/malware scanning -- deliberately out of scope, see
    ROADMAP.md's Phase 8am item 4.
    """
    root = Path(skills_dir)
    root.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    if suffix == ".md":
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillUploadError("SKILL.md must be UTF-8 text") from exc
        try:
            name, _, _ = parse_skill_frontmatter(text)
        except ValueError as exc:
            raise SkillUploadError(str(exc)) from exc
        target = root / slugify_skill_name(name)
        if target.exists():
            raise SkillUploadError(f"A skill named {name!r} already exists")
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(content)
        return _parse_skill(target, target / "SKILL.md")
    if suffix in (".zip", ".skill"):
        return _save_uploaded_skill_archive(root, content)
    raise SkillUploadError(f"Unsupported file type {suffix!r} -- use .md, .zip, or .skill")


def _save_uploaded_skill_archive(root: Path, content: bytes) -> SkillInfo:
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            # SKILL.md at the top level or exactly one folder down (the
            # shape you get zipping a folder) -- prefer the shallowest
            # match.
            names = zf.namelist()
            candidates = [n for n in names if Path(n).name == "SKILL.md" and n.count("/") <= 1]
            if not candidates:
                raise SkillUploadError(
                    "Archive must contain a SKILL.md at its top level or one folder down"
                )
            skill_md_name = min(candidates, key=lambda n: n.count("/"))
            prefix = skill_md_name.rsplit("/", 1)[0] + "/" if "/" in skill_md_name else ""

            try:
                text = zf.read(skill_md_name).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SkillUploadError("SKILL.md must be UTF-8 text") from exc
            try:
                name, _, _ = parse_skill_frontmatter(text)
            except ValueError as exc:
                raise SkillUploadError(str(exc)) from exc
            target = root / slugify_skill_name(name)
            if target.exists():
                raise SkillUploadError(f"A skill named {name!r} already exists")
            resolved_target = target.resolve()

            for member in zf.namelist():
                if not member.startswith(prefix) or member == prefix:
                    continue
                relative = member[len(prefix) :]
                dest = (target / relative).resolve()
                if not dest.is_relative_to(resolved_target):
                    raise SkillUploadError("Archive contains an entry outside its own folder")
                if member.endswith("/"):
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(member))
            return _parse_skill(target, target / "SKILL.md")
    except zipfile.BadZipFile as exc:
        raise SkillUploadError("Not a valid zip archive") from exc


def format_skill_listing(skills: list[SkillInfo]) -> str:
    """Render the always-visible "name: description" block for the system
    instructions -- level 1 of progressive disclosure."""
    lines = ["Available skills (call load_skill(name) before following one):"]
    lines += [f"- {skill.name}: {skill.description}" for skill in skills]
    return "\n".join(lines)


def build_skill_tools(skills: list[SkillInfo]) -> list[Callable[..., Any]]:
    """Return the load_skill/read_skill_file tool callables, bound to the
    given skills -- levels 2 and 3 of progressive disclosure."""
    by_name = {skill.name: skill for skill in skills}

    def load_skill(name: str) -> str:
        """Load a skill's full instructions before following them.

        Args:
            name: skill name, as listed in "Available skills"
        """
        skill = by_name.get(name)
        if skill is None:
            raise ValueError(f"Unknown skill: {name!r}. Available: {sorted(by_name)}")
        return skill.body

    def read_skill_file(name: str, path: str) -> str:
        """Read a bundled reference/asset file within a skill's own directory
        (e.g. a REFERENCE.md the skill's instructions point you to). Cannot
        run bundled scripts -- coscribe has no code-execution tool yet.

        Args:
            name: skill name
            path: file path relative to the skill's own directory
        """
        skill = by_name.get(name)
        if skill is None:
            raise ValueError(f"Unknown skill: {name!r}")
        resolved = (skill.dir / path).resolve()
        try:
            resolved.relative_to(skill.dir.resolve())
        except ValueError as exc:
            raise PermissionError(f"Path escapes skill directory: {path}") from exc
        if not resolved.is_file():
            raise ValueError(f"File does not exist in skill {name!r}: {path}")
        return resolved.read_text(encoding="utf-8")

    return [
        tool_metadata(load_skill, risk_category="READ", category="skills"),
        tool_metadata(read_skill_file, risk_category="READ", category="skills"),
    ]
