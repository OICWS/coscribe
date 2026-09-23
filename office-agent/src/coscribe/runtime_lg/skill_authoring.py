"""/saveskill's curator + writer -- turns a live conversation into a new
SKILL.md. A Skill captures generalized, reusable KNOWLEDGE -- how to do a
kind of task, written as instructions a future agent follows with
judgment -- as opposed to a scheduled task (tools/scheduled_tasks.py),
which is one concrete job to run again. One curator call decides to
clarify or propose, then a preview + explicit yes/no confirm happens
before anything is written -- see web/session.py's _handle_save_skill and
_handle_pending_skill_save_proposal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from ..tools.skills import SKILL_SAVE_CURATOR_INSTRUCTIONS, slugify_skill_name
from .messages import extract_text, render_transcript_lg

__all__ = ["SkillSaveProposal", "propose_skill_save_lg", "write_skill_lg"]


def _extract_json_object(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]


@dataclass
class SkillSaveProposal:
    """One curator round's outcome -- never persisted; the caller
    (web/session.py's /saveskill handler) holds this in memory while
    awaiting the user's answer/confirmation, and only calls
    write_skill_lg once they confirm. `decision` is "clarify" or
    "propose"; description/body are only meaningful when decision ==
    "propose"."""

    decision: str
    question: str | None = None
    description: str | None = None
    body: str | None = None


async def propose_skill_save_lg(
    name: str,
    *,
    messages: list[Any],
    model: Any,
    clarification_history: list[tuple[str, str]],
) -> SkillSaveProposal:
    """One curator call: clarify vs. propose a skill's description/body,
    failing open to a clarifying question. `name` is always the user's
    own typed command argument, never proposed by the model (the model
    decides *how to capture it*, the user decides *what it's called*)."""
    clarification_block = ""
    if clarification_history:
        clarification_block = "\n\nPrevious clarification exchange:\n" + "\n".join(
            f"Q: {question}\nA: {answer}" for question, answer in clarification_history
        )
    prompt = (
        f'Proposed skill name: "{name}"\n\n'
        f"Conversation transcript:\n{render_transcript_lg(messages)}"
        f"{clarification_block}"
    )
    fallback = SkillSaveProposal(
        decision="clarify",
        question=(
            "I couldn't tell what reusable procedure to capture from this "
            "conversation -- what should this skill teach a future agent to do?"
        ),
    )
    try:
        response = await model.ainvoke(
            [
                SystemMessage(content=SKILL_SAVE_CURATOR_INSTRUCTIONS),
                HumanMessage(content=prompt),
            ]
        )
        data = json.loads(_extract_json_object(extract_text(response.content) or ""))
    except Exception:  # noqa: BLE001 -- fail open to a question, never crash
        return fallback
    if not isinstance(data, dict):
        return fallback

    decision = data.get("decision")
    if decision == "clarify":
        question = data.get("question")
        if isinstance(question, str) and question.strip():
            return SkillSaveProposal(decision="clarify", question=question.strip())
        return fallback

    if decision == "propose":
        description = data.get("description")
        body = data.get("body")
        if (
            isinstance(description, str)
            and description.strip()
            and isinstance(body, str)
            and body.strip()
        ):
            return SkillSaveProposal(
                decision="propose", description=description.strip(), body=body.strip()
            )

    return fallback


def write_skill_lg(
    name: str, description: str, body: str, *, skills_dir: str | Path
) -> dict[str, Any]:
    """Writes the confirmed proposal to <skills_dir>/<slug>/SKILL.md --
    plain filesystem I/O, not the write_file *tool*: the user's explicit
    slash-command confirmation already *is* the approval, no separate
    WRITE_LOCAL gate on top of it needed. Overwrites in place if the
    slug already names an existing *user* skill (re-running /saveskill
    with the same name is an update, same as Skill Creator's own "editing
    an existing skill" guidance) -- but web/session.py's caller is
    responsible for refusing a slug that collides with a *built-in*
    skill before ever calling this, since that collision would silently
    shadow the built-in for every /<slug> lookup afterward, not just
    overwrite a file this function owns."""
    slug = slugify_skill_name(name)
    skill_dir = Path(skills_dir) / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    # yaml.safe_dump, not an f-string -- name/description are curator-
    # model/user-authored text, not hand-written like the built-in
    # skills' own frontmatter, so they can't be trusted to avoid
    # characters (a colon, a leading quote/dash/#) that would break
    # _parse_skill's yaml.safe_load on a naively interpolated block.
    frontmatter = yaml.safe_dump(
        {"name": name, "description": description.replace("\n", " ").strip()},
        allow_unicode=True,
        sort_keys=False,
    )
    skill_md.write_text(f"---\n{frontmatter}---\n{body}\n", encoding="utf-8")
    return {"name": name, "slug": slug, "path": str(skill_md)}
