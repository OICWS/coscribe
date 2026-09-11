"""Built-in PowerPoint template discovery: manifest loading for
``fill_pptx_template``'s small, fixed set of coscribe-owned, hand-designed
``.pptx`` template files.

Same convention as ``tools/skills.py``'s builtin skills: a template is a
directory under ``src/coscribe/builtin_templates/pptx/`` with a
``template.pptx`` and a ``template.yaml`` manifest (id/name/description/
accent/slide_count/slide_roles). Unlike skills, there is currently no user-local
equivalent of ``load_skills(settings.skills_dir)`` -- every template is
version-controlled in this repo; a user-supplied template directory is
future work, not this v1. Most templates here are coscribe's own
python-pptx-built originals (see ``scripts/build_pptx_templates.py``);
one (``velis``) is a real third-party design, bundled only because its
actual upstream license (CC0 1.0, verified against the source repo, not
just "free to use") clearly permits redistribution inside another
project -- that bar rules out the vast majority of "free PowerPoint
template" sources, which is why this remains the exception rather than
the norm (see that same script's ``_VELIS_LICENSE_TEXT`` and the
template's own bundled ``LICENSE`` file for the paper trail).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TemplateInfo:
    id: str
    name: str
    description: str
    accent: str
    slide_count: int
    slide_roles: list[str]
    dir: Path
    path: Path  # dir / "template.pptx"
    is_widescreen: bool  # real slide_width/slide_height is ~16:9, not just claimed


_BUILTIN_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "builtin_templates" / "pptx"

_REQUIRED_MANIFEST_FIELDS = ("id", "name", "description", "accent", "slide_count", "slide_roles")

# The only three slide roles fill_pptx_template understands. "content" is
# the sole repeatable one -- _adjust_template_content_slides (in
# presentations.py) duplicates/deletes only "content"-role slides to
# match a caller's actual chunk count, never "title"/"closing".
_VALID_SLIDE_ROLES = frozenset({"title", "content", "closing"})


def load_builtin_templates() -> list[TemplateInfo]:
    """Scan coscribe's own bundled pptx templates
    (``src/coscribe/builtin_templates/pptx/``). A malformed manifest, a
    missing ``template.pptx``, or a ``slide_count`` that doesn't match the
    real file's actual slide count (a cheap, catchable authoring mistake
    if the ``.pptx`` is hand-edited without updating the manifest) is
    logged and skipped -- not fatal to startup, same policy as
    ``load_builtin_skills``."""
    if not _BUILTIN_TEMPLATES_DIR.is_dir():
        return []
    templates = []
    for entry in sorted(_BUILTIN_TEMPLATES_DIR.iterdir()):
        manifest_path = entry / "template.yaml"
        if not entry.is_dir() or not manifest_path.is_file():
            continue
        try:
            templates.append(_parse_template(entry, manifest_path))
        except Exception:
            logger.warning(
                "Skipping template in %r: failed to parse/validate", entry, exc_info=True
            )
    return templates


def _parse_template(template_dir: Path, manifest_path: Path) -> TemplateInfo:
    from pptx import Presentation  # lazy -- same reasoning as presentations.py's own imports

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    missing = [field for field in _REQUIRED_MANIFEST_FIELDS if not manifest.get(field)]
    if missing:
        raise ValueError(f"template.yaml missing required field(s): {missing}")
    pptx_path = template_dir / "template.pptx"
    if not pptx_path.is_file():
        raise ValueError(f"template.pptx not found next to template.yaml in {template_dir}")
    prs = Presentation(str(pptx_path))
    real_slide_count = len(prs.slides)
    if real_slide_count != int(manifest["slide_count"]):
        raise ValueError(
            f"template.yaml slide_count={manifest['slide_count']!r} doesn't match "
            f"template.pptx's actual {real_slide_count} slide(s)"
        )
    slide_roles = manifest["slide_roles"]
    if not isinstance(slide_roles, list) or len(slide_roles) != real_slide_count:
        raise ValueError(
            f"template.yaml slide_roles must be a list with exactly "
            f"{real_slide_count} entries (one per slide), got {slide_roles!r}"
        )
    invalid_roles = sorted({str(role) for role in slide_roles} - _VALID_SLIDE_ROLES)
    if invalid_roles:
        raise ValueError(
            f"template.yaml slide_roles has invalid value(s) {invalid_roles} -- "
            f"valid roles are {sorted(_VALID_SLIDE_ROLES)}"
        )
    # "content" (the only repeatable role, see _VALID_SLIDE_ROLES's
    # comment) must form one contiguous block -- fill_pptx_template's
    # duplicate/delete logic assumes "insert/remove right after the last
    # content slide" always keeps the deck's slide order sensible, which
    # isn't true if content slides were interleaved with title/closing.
    content_indices = [i for i, role in enumerate(slide_roles) if role == "content"]
    if content_indices and content_indices != list(
        range(content_indices[0], content_indices[-1] + 1)
    ):
        raise ValueError(
            f"template.yaml slide_roles' 'content' entries must be contiguous, "
            f"got {slide_roles!r}"
        )
    # Whether the real file is actually ~16:9, not assumed from the id/
    # description -- "velis" (§13/14 of PPTX_DESIGN.md) is the one bundled
    # template that keeps its own original A4-landscape proportions rather
    # than 16:9, and a caller choosing among templates needs to know this
    # without opening the file itself. 16/9 ~= 1.778; a small tolerance
    # (0.05) covers real-world 16:9 decks that round slide_width/height to
    # slightly different EMU values, not just an exact ratio match.
    slide_width = prs.slide_width or 0
    slide_height = prs.slide_height or 1
    is_widescreen = abs((slide_width / slide_height) - (16 / 9)) < 0.05
    return TemplateInfo(
        id=str(manifest["id"]),
        name=str(manifest["name"]),
        description=str(manifest["description"]),
        accent=str(manifest["accent"]),
        slide_count=real_slide_count,
        slide_roles=[str(role) for role in slide_roles],
        dir=template_dir,
        path=pptx_path,
        is_widescreen=is_widescreen,
    )


def format_template_listing(templates: list[TemplateInfo]) -> str:
    """Render the always-visible "id: name -- description" block for the
    system instructions -- necessary for ``fill_pptx_template`` to be
    usable at all (the model needs to know a valid ``template_id`` exists),
    distinct from any future ``GET /api/templates`` REST endpoint or
    frontend picker UI. Each line is tagged with its real aspect ratio
    (read from the file itself, see ``TemplateInfo.is_widescreen``) --
    real, live user feedback: a non-16:9 template was picked for a plain
    request with no stated proportions preference, and 16:9 is the
    expected default for a PowerPoint deck absent a reason not to."""
    lines = [
        "Available PPTX templates (pass the id as fill_pptx_template's "
        "template_id) -- default to a 16:9 one unless the user is fine "
        "with different proportions or asks for one by name/description:"
    ]
    lines += [
        f"- {t.id}: {t.name} -- {t.description} "
        f"[{'16:9' if t.is_widescreen else 'NOT 16:9'}]"
        for t in templates
    ]
    return "\n".join(lines)
