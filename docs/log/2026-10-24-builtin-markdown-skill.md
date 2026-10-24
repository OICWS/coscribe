# Built-in Markdown notes skill, off by default (shipped)

Why: Allen's `markdown` skill (markdownlint-clean GFM and math) should be
switchable under Settings > Skills > Built-in.

## Shipped
- `builtin_skills/markdown/SKILL.md`, bundled like the other built-in skills.
- `DEFAULT_OFF_SKILLS` in `tools/skill_catalog.py`: `skills.json` gains an
  `enabled` list, so a default-off built-in is offered only after the person
  switches it on. Existing `disabled` entries behave as before.

## Not verified
- The Settings toggle in the running app (covered by a unit test of the state only).

## Follow-ups
- none
