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


def _parse_skill(skill_dir: Path, skill_md: Path) -> SkillInfo:
    text = skill_md.read_text(encoding="utf-8")
    _, raw_frontmatter, body = text.split("---", 2)
    frontmatter = yaml.safe_load(raw_frontmatter) or {}
    name, description = frontmatter.get("name"), frontmatter.get("description")
    if not name or not description:
        raise ValueError("SKILL.md frontmatter needs name and description")
    return SkillInfo(name=str(name), description=str(description), dir=skill_dir, body=body.strip())


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
