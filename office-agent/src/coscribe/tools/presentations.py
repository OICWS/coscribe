"""PowerPoint (.pptx) read/write tools, scoped to a single workspace root.

Mirrors ``tools/documents.py``/``spreadsheets.py`` -- same ``WorkspaceScope``
sandboxing, same shape (a toolkit class + a ``build_*_tools`` factory).

Unlike docx/pdf/xlsx, ``python-pptx`` (the library used here) hasn't had a
release since August 2024 -- it's the standard, complete-enough library for
this, not a "mature and audited" pick like the others. And unlike every
other tool in this package, ``write_pptx`` optionally shells out to headless
LibreOffice (``soffice``) to render the deck to PDF and flag text that
overflows its slide bounds -- the single most common visual defect,
per Anthropic's own published pptx Skill notes (see ``ARCHITECTURE.md`` for
why we don't use that Skill directly: it's script-driven, and coscribe
has no code-execution tool). This is a *diagnostic*, not a requirement --
``soffice`` missing, timing out, or failing to convert just means the write
still succeeds with ``overflow_warnings: []`` and a ``qa_skipped_reason``
explaining why.

``content`` is markdown where a line containing exactly ``---`` separates
slides (the same convention Pandoc/Marp/reveal.js already use), reusing
``documents.py``'s ``parse_blocks``/``Block`` per slide chunk: the first
heading becomes the slide title, remaining blocks become the body. A slide
may have a table *or* bullets/paragraphs, not both -- deliberately
constrained, like ``write_xlsx``'s per-sheet-overwrite rule.

``pdfplumber``/``pptx`` are imported lazily, inside each function that
actually needs them, rather than at module level -- same real, measured
startup-time reasoning as ``documents.py``'s identical change, see that
module's docstring and runtime_lg/README.md's startup-time section.
``PresentationType`` is only ever used as a type annotation (never at
runtime, thanks to ``from __future__ import annotations``), so it's
imported under ``TYPE_CHECKING`` instead of even a lazy runtime import.

``add_pptx_chart`` only builds through python-pptx's high-level
``CategoryChartData``/``add_chart`` API, never hand-written chart XML --
axis/series registration is correct by construction that way. Same
deliberate scoping as ``spreadsheets.py``'s ``add_xlsx_chart``: single-
purpose bar/line/pie charts only, no stacked or secondary-axis/combo
charts -- exactly the configurations Anthropic's own pptx skill documents
as producing schema-valid XML that PowerPoint silently treats as corrupt.
Restricting the feature surface avoids that failure class outright rather
than needing a post-hoc validator. Chart data is a pipe-table string, the
same convention ``write_xlsx`` already uses -- not a list/dict tool
parameter, which no tool in this package uses: aisuite's Gemini schema
inference has broken on less exotic type hints than that (see the
``Optional[int]``/``Optional[str]`` comments below and in
``spreadsheets.py``).

``add_pptx_image``/``set_pptx_notes`` and ``write_pptx``'s/``write_docx``'s
``template_path`` are all pure high-level python-pptx/python-docx API calls,
no raw XML. ``set_pptx_transition`` and ``add_pptx_animation`` are not --
python-pptx has no transition or animation API at all, so both hand-build
OOXML via ``lxml``. ``set_pptx_transition`` is low-risk hand-written XML
(the base ECMA-376 schema has a ``<p:transition>`` element; a fresh slide's
own children are verified to always be ``['cSld', 'clrMapOvr']``, so
appending at the end is always schema-correct here). ``add_pptx_animation``
is the riskiest thing in this file: its ``<p:timing>`` tree structure and
per-effect XML (``fade``/``fly-in``) are cross-checked against
``hugohe3/ppt-master``'s shipped, PowerPoint-authored
``pptx_animation_presets.json`` rather than written from memory, and the
saved file is re-opened and structurally verified before it's allowed to
replace the user's real file -- but that verification is structural, not
visual, since this sandbox has no real PowerPoint to test against, only
LibreOffice. See ``ARCHITECTURE.md`` for the full reasoning.

Every hand-XML write in this file (``set_pptx_transition``,
``add_pptx_animation``, ``edit_pptx_theme_colors``, and the theme-blob
edits inside ``write_pptx``/``fill_pptx_template`` for ``theme=``/CJK-font
handling) also runs through ``_ooxml_validate.assert_ooxml_valid`` before
the mutated element is ever serialized into the file -- a real ECMA-376
schema check (see ``_ooxml_schemas/NOTICE.md``), not just the
application-level checks above. This catches what those checks aren't
designed to: a structurally wrong OOXML document that happens to still
satisfy a narrower "did my specific edit land" check, or that ``prs.save()``
and LibreOffice's own more forgiving parser would silently accept anyway.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from functools import cache, lru_cache
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..runtime.types import tool_metadata
from ._file_locks import locked_by_path
from ._ooxml_validate import assert_ooxml_valid
from ._svg_slide import add_svg_slide
from ._thumbnail import render_all_page_previews, render_thumbnail
from ._workspace import WorkspaceScope
from .documents import Block, _is_separator_row, _split_table_row, parse_blocks, parse_inline_runs
from .pptx_templates import load_builtin_templates as _load_builtin_templates
from .pptx_templates import load_pptx_templates as _load_pptx_templates

if TYPE_CHECKING:
    from pptx.presentation import Presentation as PresentationType

SOFFICE_TIMEOUT = 20.0
OVERFLOW_TOLERANCE_PT = 1.0

_TITLE_AND_CONTENT_LAYOUT = 1
_TITLE_ONLY_LAYOUT = 5
_TWO_CONTENT_LAYOUT = 3

# python-pptx's own bundled default template (used whenever write_pptx gets
# no template_path) is authored for the legacy 4:3 canvas (10in x 7.5in) --
# confirmed live, not assumed: a bare Presentation()'s slide_width/height
# are exactly 9144000/6858000 EMU. Real PowerPoint has defaulted to 16:9
# widescreen since Office 2013, and this is the exact EMU width Microsoft
# itself uses for that preset (13.333...in -- height stays 7.5in, unchanged
# from the 4:3 default).
_WIDESCREEN_WIDTH_EMU = 12192000

# write_pptx's prefab layout library (see builtin_skills/pptx/SKILL.md) --
# a slide chunk's first non-blank line, if it matches this, selects a
# pre-positioned layout instead of the default title+bullets/table shape.
# Positions for each are computed once by the layout functions below from
# the deck's own slide_width/slide_height, not re-derived per call the way
# a run_node_script pptxgenjs script has to -- this is what eliminates the
# overlap-bug class by construction rather than hoping the model's ad-hoc
# positioning math is correct every time.
_LAYOUT_IDS = frozenset({"icon-list", "stat-callout", "two-column", "svg"})
_LAYOUT_PREFIX_RE = re.compile(r"^layout:\s*", re.IGNORECASE)
_LAYOUT_DIRECTIVE_RE = re.compile(
    r"^layout:\s*(?P<id>[a-z-]+)(?:\s+(?P<accent>[0-9A-Fa-f]{6}))?\s*$", re.IGNORECASE
)
_TWO_COLUMN_MARKER_RE = re.compile(r"^>>>$")
_ICON_GLYPH_RE = re.compile(r"^\[(?P<glyph>.{1,4})\]\s*(?P<text>.*)$")
# A glyph of 3+ plain ASCII letters is almost certainly a spelled-out word
# (e.g. "dash", "star", "bolt" -- observed live) rather than a symbol; a
# 1-2 letter glyph (initials like "AI") is still allowed through.
_WORD_LIKE_GLYPH_RE = re.compile(r"^[A-Za-z]{3,}$")
# Plain ASCII only -- observed live, an emoji/pictograph glyph (e.g. a
# multi-color "house" or "rocket" character) renders via the font's own
# built-in color glyph, which ignores the icon circle's font.color.rgb
# override entirely. The result is a deck where some icons are clean
# accent-colored circles and others are mismatched full-color stickers,
# inconsistently, depending on which specific character the model picked --
# not something this code can control once it's an emoji glyph, so it's
# rejected up front instead of shipping an inconsistent-looking deck.
_SAFE_GLYPH_RE = re.compile(r"^[A-Za-z0-9!?*+\-=/#@%&]+$")
_ICON_LIST_MAX_ITEMS = 6
_STAT_CALLOUT_MAX_ITEMS = 4

# write_pptx's `theme` parameter: a small deck-wide color/typography
# identity, tokens named after the same bg/surface/text/accent shape
# that Style Dictionary/DTCG-style design-token systems and CSS-theme
# tools (Marp/Slidev/reveal.js) independently converge on for this exact
# problem (see PPTX_DESIGN.md's "background/theme identity" section) --
# mapped onto OOXML's own native <a:clrScheme> slots, the same mapping
# pptxgenjs's own scheme-color model (tx1/bg1/accent1-6) already uses.
_THEME_COLOR_KEYS = frozenset({"bg", "surface", "text", "accent"})
_THEME_FONT_KEYS = frozenset({"heading_font", "body_font"})
_THEME_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
# extract_pptx_template's own id validation -- same shape as the bundled
# templates' own ids ("bold-statement", "minimal-light", ...), enforced
# here rather than left to whatever a directory name happens to be, since
# this id becomes fill_pptx_template's own template_id argument afterward.
_TEMPLATE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
_THEME_SCHEME_COLOR_TAGS = {
    "bg": "lt1",
    "text": "dk1",
    "surface": "lt2",
    "accent": "accent1",
}
# Every stock python-pptx theme (the default blank Presentation() and all
# 3 bundled coscribe templates fill_pptx_template draws from) ships an
# empty East-Asian typeface (<a:ea typeface=""/>) in both majorFont/
# minorFont -- confirmed by direct XML inspection (PPTX_DESIGN.md §3).
# Every Chinese-language deck this generates therefore has its CJK text
# rendered by whatever fallback font the opening machine's PowerPoint
# happens to pick, never a deliberate choice. A real, common Windows/
# Mac-available CJK typeface, applied unconditionally (not gated on the
# `theme` parameter) since this is a defect fix, not a style choice.
_CJK_FALLBACK_TYPEFACE = "Microsoft YaHei"
# Fractions of slide_width/slide_height for the body content area, measured
# against python-pptx's own default Title-and-Content layout's real
# placeholder geometry (0.5in/1.75in margins on a 10in x 7.5in slide) --
# see _content_area's own docstring.
_MARGIN_FRACTION = 0.05
_CONTENT_TOP_FRACTION = 0.2333
_CONTENT_BOTTOM_FRACTION = 0.9

# The presentationml namespace every <p:...> element in a slide part lives
# in, and the PowerPoint-2010 extension namespace real PowerPoint uses for
# the transition duration attribute (`p14:dur`, milliseconds) -- the base
# ECMA-376 schema only has the three-value `spd` attribute (fast/med/slow),
# `p14:dur` is the real-world attribute PowerPoint itself writes for
# precise timing, confirmed by inspecting real .pptx files' XML.
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"
# Markup Compatibility and Extensibility (ECMA-376 Part 3) -- the mechanism
# that makes a foreign-namespace attribute like `p14:dur` below valid OOXML
# at all: a producer declares the namespace prefix "ignorable" via
# `mc:Ignorable` on an ancestor, and a schema-strict consumer is specified
# to strip that content before validating rather than reject it outright.
# Confirmed live against `_ooxml_schemas/`: `CT_SlideTransition`/`CT_Slide`
# have no `xsd:anyAttribute` wildcard, so `p14:dur` genuinely doesn't
# validate against the bare ECMA-376 schema without this declaration --
# `_ooxml_validate.py` implements the matching strip-before-validate side.
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

# add_pptx_formula's own pair: the PowerPoint-2010 DrawingML extension
# namespace real PowerPoint uses to embed a native Office Math equation
# inside a text paragraph (`<a14:m>`, wrapping `<m:oMathPara>`/`<m:oMath>`),
# and Office Math's own namespace -- confirmed against
# hugohe3/ppt-master's own documented contract (`references/native-
# formula.md`: "Export replaces the whole group with a14:m > m:oMathPara >
# m:oMath"), not guessed. Same foreign-namespace-extension-content shape as
# `p14:dur` above -- `<a:p>`'s base ECMA-376 content model has no slot for
# either `a14:m` or `m:oMath`, so this needs the exact same `mc:Ignorable`
# treatment (`_mark_mce_ignorable`/`_ooxml_validate.py`'s strip-before-
# validate side), reused unmodified rather than duplicated.
_A14_NS = "http://schemas.microsoft.com/office/drawing/2010/main"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
# DrawingML's base namespace -- add_pptx_audio's own <a:audioFile>/
# <a:blip>/<a:xfrm> below, none of which needed a dedicated constant
# until now (every earlier hand-XML feature only ever needed the "p"/
# "p14"/"a14"/"m" namespaces above).
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# PowerPoint 2012/2015 extension namespaces -- set_pptx_transition's own
# pair beyond _P14_NS above, needed for the newer transition effects below
# (Morph is p159; the 2013 "Exciting"-category ones -- Fracture, Crush,
# Airplane, Origami, etc. -- are p15). Same foreign-namespace-extension
# reasoning as _P14_NS/_A14_NS: real PowerPoint XML for these effects,
# confirmed against hugohe3/ppt-master's own reverse-engineered registry
# (`pptx_transitions.py`), not guessed.
_P15_NS = "http://schemas.microsoft.com/office/powerpoint/2012/main"
_P159_NS = "http://schemas.microsoft.com/office/powerpoint/2015/09/main"

_TRANSITION_NAMESPACES = {"p": _P_NS, "p14": _P14_NS, "p15": _P15_NS, "p159": _P159_NS}
# The three extension prefixes a transition (and only a transition -- this
# codebase's only other p14 user, add_pptx_formula, owns a14 instead) can
# ever need mc:Ignorable for -- see _set_slide_transition's own comment on
# why unmarking all three unconditionally before setting a new transition
# is correct rather than fragile.
_TRANSITION_MCE_PREFIXES = ("p14", "p15", "p159")

# PowerPoint's real native transition gallery (48 effects across its own
# Subtle/Exciting/Dynamic Content categories) plus common legacy aliases
# (8 more) -- retyped from hugohe3/ppt-master's own `pptx_transitions.py`
# (`_TRANSITION_SPECS`/`TRANSITION_ALIASES`, commit `6e3ce9c5a3b994a0e223
# a14a0f7eddf42fd0b9f5`), the real element/attribute mapping for each
# effect having been reverse-engineered against actual PowerPoint-authored
# XML there -- not independently guessable, and not worth re-deriving.
# Deliberately narrower than the source in one way: each effect here is
# its own single, sensible default variant (e.g. "push" always defaults to
# `dir="r"`) with no exposed per-transition options (direction/shape/style
# overrides) -- ppt-master's own effect_options system is real, scoped-out
# extra surface for a later pass, not ported here. "prefix" absent means
# the base "p" (ECMA-376) namespace; "fallback" is only meaningful for a
# non-"p" prefix, naming which base transition an older/non-MCE-aware
# reader should see instead (this codebase's own mc:Ignorable-based
# approach doesn't use it today -- see _set_slide_transition -- but it's
# kept in the data for a future AlternateContent-based upgrade).
_TRANSITION_SPECS: dict[str, dict[str, Any]] = {
    "fade": {"element": "fade", "attrs": {}},
    "push": {"element": "push", "attrs": {"dir": "r"}},
    "wipe": {"element": "wipe", "attrs": {"dir": "r"}},
    "split": {"element": "split", "attrs": {"orient": "horz", "dir": "out"}},
    "cover": {"element": "cover", "attrs": {"dir": "r"}},
    "random": {"element": "random", "attrs": {}},
    "blinds": {"element": "blinds", "attrs": {"dir": "vert"}},
    "checkerboard": {"element": "checker", "attrs": {"dir": "horz"}},
    "comb": {"element": "comb", "attrs": {"dir": "horz"}},
    "cut": {"element": "cut", "attrs": {"thruBlk": "0"}},
    "dissolve": {"element": "dissolve", "attrs": {}},
    "random_bars": {"element": "randomBar", "attrs": {"dir": "vert"}},
    "zoom": {"element": "warp", "attrs": {"dir": "in"}, "prefix": "p14", "fallback": "fade"},
    "morph": {
        "element": "morph",
        "attrs": {"option": "byObject"},
        "prefix": "p159",
        "fallback": "fade",
    },
    "reveal": {"element": "reveal", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "shape": {"element": "circle", "attrs": {}},
    "uncover": {"element": "pull", "attrs": {"dir": "r"}},
    "flash": {"element": "flash", "attrs": {}, "prefix": "p14", "fallback": "fade"},
    "fall_over": {
        "element": "prstTrans",
        "attrs": {"prst": "fallOver", "invX": "1"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "drape": {
        "element": "prstTrans",
        "attrs": {"prst": "drape", "invX": "1"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "curtains": {
        "element": "prstTrans",
        "attrs": {"prst": "curtains"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "wind": {
        "element": "prstTrans",
        "attrs": {"prst": "wind"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "prestige": {
        "element": "prstTrans",
        "attrs": {"prst": "prestige"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "fracture": {
        "element": "prstTrans",
        "attrs": {"prst": "fracture"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "crush": {
        "element": "prstTrans",
        "attrs": {"prst": "crush"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "peel_off": {
        "element": "prstTrans",
        "attrs": {"prst": "peelOff", "invX": "1"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "page_curl": {
        "element": "prstTrans",
        "attrs": {"prst": "pageCurlSingle", "invX": "1"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "airplane": {
        "element": "prstTrans",
        "attrs": {"prst": "airplane"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "origami": {
        "element": "prstTrans",
        "attrs": {"prst": "origami"},
        "prefix": "p15",
        "fallback": "fade",
    },
    "clock": {"element": "wheel", "attrs": {"spokes": "1"}},
    "ripple": {"element": "ripple", "attrs": {}, "prefix": "p14", "fallback": "fade"},
    "honeycomb": {"element": "honeycomb", "attrs": {}, "prefix": "p14", "fallback": "fade"},
    "glitter": {"element": "glitter", "attrs": {}, "prefix": "p14", "fallback": "fade"},
    "vortex": {"element": "vortex", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "shred": {"element": "shred", "attrs": {"dir": "out"}, "prefix": "p14", "fallback": "fade"},
    "switch": {"element": "switch", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "flip": {"element": "flip", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "gallery": {"element": "gallery", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "cube": {"element": "prism", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "doors": {"element": "doors", "attrs": {"dir": "vert"}, "prefix": "p14", "fallback": "fade"},
    "box": {"element": "zoom", "attrs": {}},
    "pan": {"element": "pan", "attrs": {"dir": "r"}, "prefix": "p14", "fallback": "fade"},
    "ferris_wheel": {
        "element": "ferris",
        "attrs": {"dir": "r"},
        "prefix": "p14",
        "fallback": "fade",
    },
    "conveyor": {
        "element": "conveyor",
        "attrs": {"dir": "r"},
        "prefix": "p14",
        "fallback": "fade",
    },
    "rotate": {
        "element": "prism",
        "attrs": {"dir": "r", "isContent": "1"},
        "prefix": "p14",
        "fallback": "fade",
    },
    "window": {"element": "window", "attrs": {}, "prefix": "p14", "fallback": "fade"},
    "orbit": {
        "element": "prism",
        "attrs": {"dir": "r", "isContent": "1", "isInverted": "1"},
        "prefix": "p14",
        "fallback": "fade",
    },
    "fly_through": {"element": "flythrough", "attrs": {}, "prefix": "p14", "fallback": "fade"},
}

# Legacy/compatibility names mapping onto one of the 48 canonical keys
# above -- e.g. an older "strips"/"wheel" name a caller (or a round-
# tripped PPTX) might still use.
_TRANSITION_ALIASES: dict[str, str] = {
    "strips": "wipe",
    "circle": "shape",
    "diamond": "shape",
    "newsflash": "flash",
    "plus": "shape",
    "pull": "uncover",
    "wedge": "clock",
    "wheel": "clock",
}

_TRANSITIONS = frozenset({*_TRANSITION_SPECS, *_TRANSITION_ALIASES, "none"})

# add_pptx_shape's own curated subset of python-pptx's ~180-member
# MSO_SHAPE enum (every name below verified against the real installed
# enum, not guessed) -- basic shapes, arrows, flowchart nodes, and
# callouts, the same categories hugohe3/ppt-master's own feature list
# names ("block arrows, chevrons, callouts, flowchart nodes"). Excludes
# the long tail of rarely-used/legacy AutoShapes (line-callout variants
# 1-4 with every border/accent-bar permutation, the 10/12/16/24/32-point
# star sizes, seal/ribbon banners, etc.) -- a smaller, well-named set a
# model can pick from by name alone beats exposing the full enum, the
# same reasoning add_pptx_icon's own curated Lucide subset already uses.
_SHAPE_TYPES = frozenset(
    {
        # Basic
        "RECTANGLE", "ROUNDED_RECTANGLE", "OVAL", "ISOSCELES_TRIANGLE",
        "RIGHT_TRIANGLE", "DIAMOND", "PARALLELOGRAM", "TRAPEZOID", "PENTAGON",
        "HEXAGON", "OCTAGON", "CROSS", "PLAQUE", "CAN", "CUBE",
        # Arrows
        "RIGHT_ARROW", "LEFT_ARROW", "UP_ARROW", "DOWN_ARROW", "LEFT_RIGHT_ARROW",
        "UP_DOWN_ARROW", "BENT_ARROW", "CHEVRON", "NOTCHED_RIGHT_ARROW", "U_TURN_ARROW",
        # Flowchart
        "FLOWCHART_PROCESS", "FLOWCHART_DECISION", "FLOWCHART_TERMINATOR",
        "FLOWCHART_DATA", "FLOWCHART_DOCUMENT", "FLOWCHART_PREPARATION",
        "FLOWCHART_PREDEFINED_PROCESS", "FLOWCHART_CONNECTOR",
        # Callouts
        "ROUNDED_RECTANGULAR_CALLOUT", "RECTANGULAR_CALLOUT", "OVAL_CALLOUT",
        "CLOUD_CALLOUT",
        # Decorative
        "CLOUD", "HEART", "LIGHTNING_BOLT", "SUN", "MOON", "STAR_5_POINT",
        "STAR_4_POINT", "SMILEY_FACE", "DONUT", "NO_SYMBOL", "BLOCK_ARC", "ARC",
    }
)

# add_pptx_hyperlink's scope: external URLs via python-pptx's own public
# Hyperlink API (Shape.click_action.hyperlink/Run.hyperlink), plus
# internal "jump to another slide" links via a *different* public
# python-pptx API this file previously missed: ActionSetting.target_slide
# (Shape.click_action.target_slide, settable to a real Slide object) --
# confirmed by reading python-pptx's own source before assuming otherwise,
# not carried over from an earlier, now-stale assumption that no public
# support existed. That setter already builds the exact real relationship/
# attribute pair hugohe3/ppt-master's own `hyperlink_contract.py` names
# (`SLIDE_REL_TYPE` = the real "…/relationships/slide" type, `action =
# "ppaction://hlinksldjump"`) -- no hand-rolled XML needed at all, unlike
# set_pptx_transition/add_pptx_animation/add_pptx_audio, since python-
# pptx's own maintainers had already wired this up (its `action` property
# even already recognizes `PP_ACTION.NAMED_SLIDE` on the *read* side; only
# the write-side setter had gone unnoticed here). The `"#slide-N"` url
# syntax below borrows ppt-master's own real convention (`hyperlink_
# contract.py`'s `_SLIDE_TARGET_RE`) rather than inventing a new one.
_HYPERLINK_SCHEMES = ("http://", "https://", "mailto:", "ftp://")
_SLIDE_TARGET_RE = re.compile(r"^#slide-([1-9][0-9]*)$")


def _validate_hyperlink_url(url: str) -> None:
    if not url.strip():
        raise ValueError("add_pptx_hyperlink: url must not be empty.")
    if _SLIDE_TARGET_RE.match(url):
        return
    if not url.startswith(_HYPERLINK_SCHEMES):
        raise ValueError(
            f"add_pptx_hyperlink: url {url!r} must start with one of "
            f"{', '.join(_HYPERLINK_SCHEMES)}, or be \"#slide-N\" to jump to slide N -- "
            "got a scheme-less or unsupported value."
        )


def _resolve_slide_jump_target(prs: PresentationType, slide: int, url: str) -> Any | None:
    """None if `url` isn't a `"#slide-N"` target; otherwise the real
    `Slide` object N refers to, raising a helpful, real-count-naming
    error if N is out of range -- matching this file's own established
    out-of-range error convention elsewhere (e.g. `_open_slide`)."""
    match = _SLIDE_TARGET_RE.match(url)
    if match is None:
        return None
    target_number = int(match.group(1))
    slide_count = len(prs.slides)
    if not 1 <= target_number <= slide_count:
        raise ValueError(
            f"add_pptx_hyperlink: slide target {url!r} on slide {slide} is out of range "
            f"-- deck has {slide_count} slides."
        )
    return prs.slides[target_number - 1]


# check_pptx_delivery's own curated cross-platform-safe font list -- real,
# hard-won knowledge (which font *names* ship pre-installed on real
# Windows/Mac machines across scripts, not guessable from first
# principles), retyped and credited from hugohe3/ppt-master's
# `svg_to_pptx/drawingml/utils.py`'s own `PPT_SAFE_FONTS` (commit
# `6e3ce9c5a3b994a0e223a14a0f7edd42fd0b9f5`) -- the same "external
# knowledge, retyped and credited" ceremony level `add_pptx_shape`'s own
# curated `MSO_SHAPE` subset and `set_pptx_transition`'s retyped registry
# already use, not vendored as a whole file (it's one flat set of
# lowercase strings, nothing to meaningfully diff against upstream).
# Lowercase, matching how it's compared (case-insensitively) below.
_SAFE_FONTS = frozenset(
    {
        "microsoft yahei", "simhei", "simsun", "kaiti", "fangsong", "dengxian",
        "microsoft jhenghei", "microsoft jhenghei ui", "pmingliu", "mingliu",
        "mingliu_hkscs", "dfkai-sb",
        "pingfang sc", "heiti sc", "songti sc", "stsong",
        "pingfang tc", "pingfang hk", "heiti tc", "songti tc", "kaiti tc",
        "yu gothic", "yu gothic ui", "yu mincho",
        "meiryo", "meiryo ui",
        "ms gothic", "ms mincho", "ms pgothic", "ms pmincho", "ms ui gothic",
        "malgun gothic", "gulim", "dotum", "batang",
        "nirmala ui", "mangal", "kokila", "aparajita", "utsaah",
        "leelawadee ui", "leelawadee", "cordia new", "angsana new", "browallia new",
        "david", "miriam", "frank ruehl", "gisha", "levenim mt", "narkisim", "aharoni",
        "arial", "arial black", "calibri", "segoe ui", "verdana",
        "helvetica", "helvetica neue", "tahoma", "trebuchet ms",
        "times new roman", "times", "georgia", "cambria", "cambria math", "palatino",
        "garamond", "book antiqua",
        "consolas", "courier new", "menlo", "monaco",
        "impact",
    }
)
# 20MB -- roughly the point real email providers start rejecting or
# aggressively compressing an attachment; a round, memorable threshold
# rather than a precisely-researched one, deliberately conservative so
# the advisory fires before delivery actually fails.
_DELIVERY_MEDIA_ADVISORY_BYTES = 20_000_000


def _analyze_pptx_delivery(file_path: Path) -> dict[str, Any]:
    """The python-pptx-dependent half of check_pptx_delivery -- hidden
    slides, font usage, and per-slide motion (transitions/animations/
    audio) -- split out from the raw-zip-level half so a file broken
    enough that python-pptx can't fully walk it (python-pptx parses
    slide parts lazily, so this can surface anywhere in here, not only
    at `Presentation()` construction) can be caught as one unit by the
    caller and still return the zip-level findings rather than crashing
    the whole audit -- confirmed against a deliberately-corrupted test
    fixture, not assumed sufficient from wrapping only the constructor."""
    from lxml import etree
    from pptx import Presentation
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml.ns import qn

    prs = Presentation(str(file_path))

    hidden_slides = [
        index + 1
        for index, slide in enumerate(prs.slides)
        if (slide.element.get("show") or "").strip().lower() in ("0", "false")
    ]

    fonts_used: set[str] = set()
    for master in prs.slide_masters:
        try:
            theme_root = etree.fromstring(master.part.part_related_by(RT.THEME).blob)
        except (KeyError, etree.XMLSyntaxError):
            # A real, if malformed/unusual, file this read-only audit
            # must still report on rather than crash -- a missing or
            # unparsable theme part is itself worth surfacing via
            # advisories below, not a reason to abort the whole audit.
            continue
        font_scheme = theme_root.find(f".//{qn('a:fontScheme')}")
        if font_scheme is None:
            continue
        for role in ("majorFont", "minorFont"):
            latin = font_scheme.find(f"{qn(f'a:{role}')}/{qn('a:latin')}")
            typeface = latin.get("typeface") if latin is not None else None
            if typeface and typeface != "+mn-lt" and typeface != "+mj-lt":
                fonts_used.add(typeface)
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if run.font.name:
                        fonts_used.add(run.font.name)
    unsafe_fonts = sorted(font for font in fonts_used if font.strip().lower() not in _SAFE_FONTS)

    transition_slides: list[int] = []
    animation_slides: list[int] = []
    audio_slides: list[int] = []
    for index, slide in enumerate(prs.slides, start=1):
        if slide.element.find(f"{{{_P_NS}}}transition") is not None:
            transition_slides.append(index)
        timing = slide.element.find(f"{{{_P_NS}}}timing")
        if timing is None:
            continue
        main_child = timing.find(f".//{{{_P_NS}}}cTn[@nodeType='mainSeq']/{{{_P_NS}}}childTnLst")
        if main_child is not None and len(main_child):
            animation_slides.append(index)
        if timing.find(f".//{{{_P_NS}}}audio") is not None:
            audio_slides.append(index)

    return {
        "slide_count": len(prs.slides),
        "hidden_slides": hidden_slides,
        "fonts_used": sorted(fonts_used),
        "unsafe_fonts": unsafe_fonts,
        "motion": {
            "transitions": transition_slides,
            "animations": animation_slides,
            "audio": audio_slides,
        },
    }


# presetID/presetClass/presetSubtype values and the exact effect-node shape
# (p:set for the visibility toggle, then p:animEffect/p:anim/p:animScale/
# p:animRot/p:animMotion/p:animClr for the motion) are not guessed -- they
# come straight from hugohe3/ppt-master's vendored `pptx_animation_presets.
# json` (`_native_animation_presets/`, commit `6e3ce9c5a3b994a0e223a14a0f7e
# dd42fd0b9f5`, see that directory's own NOTICE.md): a real, PowerPoint-
# authored `<p:cTn>` XML row per effect -- the file's own `source` metadata
# says it was generated by exporting from actual Microsoft PowerPoint 16,
# not hand-derived, 203 presets across entrance/emphasis/exit/path
# (motion-path) categories. `_instantiate_animation_preset_row` below
# parses one preset's row_xml at call time and adapts it to the target
# slide (renumbers ids, retargets spid, scales duration) rather than this
# file hand-rebuilding each effect's XML from scratch -- the earlier design
# (kept in git history) manually reimplemented exactly 6 of these 203
# presets one `if`/`elif` branch at a time; scaling that approach to the
# full catalog by hand was not realistic, so this switched to parsing the
# real authored XML instead, the same shift `set_pptx_transition` made from
# "guess the XML shape" to "retype the real table" (PPTX_DESIGN.md §27).
_ANIMATION_PRESETS_PATH = (
    Path(__file__).parent / "_native_animation_presets" / "animation_presets.json"
)

# The 6 preset names this file exposed before the 203-preset catalog above
# replaced the hand-rolled 6-effect builder -- kept working under their old
# names for backward compatibility, mapped onto the real preset key each
# one turned out to already implement (confirmed by matching presetID/
# presetSubtype/category, not by name similarity).
_ANIMATION_ALIASES = {
    "fade": "entrance_fade",
    "fly-in": "entrance_fly",
    "exit-fade": "exit_fade",
    "exit-fly": "exit_fly",
    "emphasis-grow": "emphasis_grow_shrink",
    "emphasis-spin": "emphasis_spin",
}


@lru_cache(maxsize=1)
def _load_animation_presets() -> dict[str, dict[str, Any]]:
    """Every vendored preset, keyed by its real preset key
    (`"entrance_fade"`, `"path_circle"`, ...) -- loaded and cached once."""
    payload = json.loads(_ANIMATION_PRESETS_PATH.read_text(encoding="utf-8"))
    return {effect["key"]: effect for effect in payload["effects"]}


_ANIMATIONS = frozenset({*_load_animation_presets(), *_ANIMATION_ALIASES})


def _resolve_animation_preset(animation: str) -> dict[str, Any]:
    key = _ANIMATION_ALIASES.get(animation, animation)
    return _load_animation_presets()[key]


@cache
def _animation_preset_expected_children(animation: str) -> frozenset[str]:
    """Distinct direct-child tag names of a preset's own `<p:childTnLst>`
    (e.g. `{"animEffect"}` for a fade, `{"anim"}` for a fly, `{"animMotion"}`
    for a motion path) -- the generalization of the old hand-maintained
    `_ANIMATION_CHILD_TAGS` single-tag map, now derived from the real XML
    instead of listed by hand for each of 203 presets. `"set"` (the shared
    visibility toggle every entrance/exit preset also carries) is excluded
    unless it is the *only* child, since it doesn't disambiguate one preset
    from another the way the motion/effect tag does."""
    from lxml import etree

    preset = _resolve_animation_preset(animation)
    root = etree.fromstring(preset["row_xml"].encode("utf-8"))
    child_lst = root.find(f"{{{_P_NS}}}childTnLst")
    tags = {etree.QName(child).localname for child in child_lst}
    return frozenset(tags - {"set"}) or frozenset({"set"})

# nodeType on the presetID-bearing <p:cTn> encodes which Animation Pane
# "Start" mode this row uses -- confirmed against hugohe3/ppt-master's
# pptx_animations.py (_TRIGGER_NODE_TYPES there, identical mapping), which
# always *overwrites* this attribute at row-instantiation time based on
# the caller's actual trigger, regardless of whatever value the raw
# preset template happened to store. Every preset in the vendored
# animation_presets.json stores nodeType="afterEffect" -- a generic
# placeholder value from the manifest, not a per-preset fact -- so this
# file's own previous "fix" from "clickEffect" to hardcoded "afterEffect"
# (see git history) was itself wrong for the on-click-only trigger this
# file exposed at the time: on-click should be "clickEffect". Found while
# researching trigger-mode support below, not guessed --
# add_pptx_animation's `trigger` parameter now always derives nodeType
# from this table instead of hardcoding one value.
_TRIGGER_NODE_TYPES = {
    "on-click": "clickEffect",
    "with-previous": "withEffect",
    "after-previous": "afterEffect",
}
_TRIGGERS = frozenset(_TRIGGER_NODE_TYPES)

# add_pptx_audio's own, deliberately small format set -- the three real
# content types hugohe3/ppt-master's own narration tooling supports
# (`AUDIO_CONTENT_TYPES` in `svg_to_pptx/pptx_package/narration.py`),
# not independently guessed.
_AUDIO_CONTENT_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav"}
_AUDIO_TRIGGERS = frozenset({"auto", "on-click"})
# 457200 EMU = 0.5in -- the real size PowerPoint itself uses for an
# inserted-audio icon (matches hugohe3/ppt-master's own
# AUDIO_MARKER_SIZE_EMU), not independently chosen.
_AUDIO_MARKER_SIZE_EMU = 457200


def _register_audio_media_part_class() -> None:
    """Register `MediaPart` for every audio content type add_pptx_audio
    uses -- python-pptx's own `PartFactory.part_type_for` dict is a real,
    documented extension point ("Client code can register a subclass of
    |Part| to be used for a package blob based on its content type," per
    its own docstring), but ships with nothing registered for *any*
    media content type, audio or video. Without this, reopening a .pptx
    that already has one audio/video part loads it back as a plain
    |Part| (no `.sha1`), and `Package.get_or_add_media_part`'s own
    dedup-by-sha1 lookup crashes with `AttributeError: 'Part' object has
    no attribute 'sha1'` on every subsequent add_pptx_audio call in the
    same process -- confirmed by hitting exactly that crash on a second
    call before adding this registration, not assumed. A one-time,
    idempotent process-wide side effect, matching how the module-level
    docstring already documents this file lazily importing `pptx`/`lxml`
    inside functions rather than at module scope."""
    from pptx.opc.package import PartFactory
    from pptx.parts.media import MediaPart

    for content_type in _AUDIO_CONTENT_TYPES.values():
        PartFactory.part_type_for[content_type] = MediaPart


def _ctn_numeric_delay(ctn: Any) -> int:
    """Largest direct numeric start delay on one <p:cTn>'s own
    <p:stCondLst>/<p:cond> children (0 if none/non-numeric)."""
    conditions = ctn.find(f"{{{_P_NS}}}stCondLst")
    if conditions is None:
        return 0
    values = [
        int(condition.get("delay", "0"))
        for condition in conditions.findall(f"{{{_P_NS}}}cond")
        if (condition.get("delay") or "").isdigit()
    ]
    return max(values, default=0)


def _row_effective_duration_ms(row: Any) -> int | None:
    """Finite end time (delay + dur) PowerPoint would expose for one
    preset row, or None if nothing in it has a finite numeric duration."""
    ends = []
    for ctn in row.iter(f"{{{_P_NS}}}cTn"):
        if ctn is row:
            continue
        raw_duration = ctn.get("dur")
        if raw_duration is None or not raw_duration.isdigit():
            continue
        ends.append(_ctn_numeric_delay(ctn) + int(raw_duration))
    return max(ends) if ends else None


def _scale_preset_row_duration(row: Any, base_duration_ms: int, requested_duration_ms: int) -> None:
    """Scale every finite duration/delay in one parsed preset row so its
    overall length matches `requested_duration_ms` instead of the
    preset's own authored `base_duration_ms` -- adapted from
    hugohe3/ppt-master's `pptx_animations.py`'s
    `_scale_animation_row_duration` (commit `6e3ce9c5a3b994a0e223a14a0f7e
    dd42fd0b9f5`, see `_native_animation_presets/NOTICE.md`), ported from
    `xml.etree.ElementTree` to this file's own `lxml`. A `dur="1"` node
    (the visibility toggle every entrance/exit preset carries) is left at
    exactly 1ms regardless of ratio -- scaling it down to 0 would make the
    shape appear/disappear instantly with no fade *before* the real motion
    starts, and scaling it up would visibly delay the motion for no
    reason; real PowerPoint-authored XML never scales this node either."""
    ratio = requested_duration_ms / base_duration_ms
    for ctn in row.iter(f"{{{_P_NS}}}cTn"):
        if ctn is row:
            continue
        raw_duration = ctn.get("dur")
        if raw_duration is not None and raw_duration.isdigit():
            numeric_duration = int(raw_duration)
            ctn.set(
                "dur",
                "1" if numeric_duration == 1 else str(max(1, round(numeric_duration * ratio))),
            )
        conditions = ctn.find(f"{{{_P_NS}}}stCondLst")
        if conditions is None:
            continue
        for condition in conditions.findall(f"{{{_P_NS}}}cond"):
            raw_delay = condition.get("delay")
            if raw_delay is not None and raw_delay.isdigit():
                condition.set("delay", str(round(int(raw_delay) * ratio)))

    actual_duration = _row_effective_duration_ms(row)
    if actual_duration is None or actual_duration == requested_duration_ms:
        return
    # Rounding several independently-scaled nodes rarely lands exactly on
    # the requested total -- nudge whichever node(s) actually determine
    # the row's end time so the row's real length matches what was asked
    # for, not just "close after rounding."
    end_nodes = [
        ctn
        for ctn in row.iter(f"{{{_P_NS}}}cTn")
        if ctn is not row
        and (ctn.get("dur") or "").isdigit()
        and _ctn_numeric_delay(ctn) + int(ctn.get("dur", "0")) == actual_duration
    ]
    for ctn in end_nodes:
        delay = _ctn_numeric_delay(ctn)
        if delay >= requested_duration_ms:
            conditions = ctn.find(f"{{{_P_NS}}}stCondLst")
            numeric = (
                [
                    condition
                    for condition in conditions.findall(f"{{{_P_NS}}}cond")
                    if (condition.get("delay") or "").isdigit()
                ]
                if conditions is not None
                else []
            )
            if numeric:
                numeric[-1].set("delay", str(max(0, requested_duration_ms - 1)))
                delay = _ctn_numeric_delay(ctn)
        ctn.set("dur", str(max(1, requested_duration_ms - delay)))


def _instantiate_animation_preset_row(
    row_par: Any,
    effect_id: int,
    shape_id: int,
    animation: str,
    duration_ms: int,
    node_type: str,
    paragraph_index: int | None = None,
) -> int:
    """Instantiate one vendored preset as the innermost <p:cTn
    presetID=...> node (and everything under it), appended as a child of
    `row_par` (a <p:par> already created by the caller). Returns the next
    free id.

    Rather than hand-rebuilding each animation type's XML shape (the
    previous design, practical for the original 6 presets but not for the
    full 203-preset catalog), this parses the preset's own real,
    PowerPoint-authored `row_xml` and adapts it in place: renumbers every
    <p:cTn id=...> sequentially from `effect_id` (the row_xml's own ids
    always start at a fixed 5/6/7... baseline, since it was authored
    against a hypothetical single-shape slide -- they'd collide with a
    real slide's own running id counter otherwise), retargets every
    <p:spTgt spid="2"> placeholder to the real `shape_id`, and -- when the
    preset declares itself duration-scalable -- scales every finite
    duration/delay from the preset's own authored default to
    `duration_ms` via `_scale_preset_row_duration`. A handful of presets
    (instant appear/disappear toggles, discrete state changes like "change
    font") are not duration-scalable at all; for those, `duration_ms` is
    ignored and the preset's own authored timing is kept verbatim --
    real PowerPoint doesn't expose a duration control for these either."""
    from lxml import etree

    preset = _resolve_animation_preset(animation)
    root = etree.fromstring(preset["row_xml"].encode("utf-8"))
    root.set("nodeType", node_type)

    next_id = effect_id
    for ctn in root.iter(f"{{{_P_NS}}}cTn"):
        ctn.set("id", str(next_id))
        next_id += 1

    for sp_tgt in root.iter(f"{{{_P_NS}}}spTgt"):
        sp_tgt.set("spid", str(shape_id))
        if paragraph_index is not None:
            tx_el = etree.SubElement(sp_tgt, f"{{{_P_NS}}}txEl")
            etree.SubElement(
                tx_el, f"{{{_P_NS}}}pRg", st=str(paragraph_index), end=str(paragraph_index)
            )

    base_duration_ms = preset["default_duration_ms"]
    if preset["duration_scalable"] and base_duration_ms is not None:
        _scale_preset_row_duration(root, base_duration_ms, duration_ms)

    row_par.append(root)
    return next_id


def _append_main_seq(root_child: Any, main_seq_id: int) -> Any:
    """Append a fresh, empty <p:seq> (mainSeq) to `root_child` (tmRoot's
    own <p:childTnLst>), returning mainSeq's own (also empty) childTnLst
    for the caller to append its first <p:par> step into -- the shared
    "add animation support to a slide" step, whether that slide has no
    timing tree at all yet (`_new_timing_tree`) or already has one
    without a mainSeq because `add_pptx_audio` created it first
    (`_add_animation_step`'s own mainSeq-missing branch)."""
    from lxml import etree

    seq = etree.SubElement(root_child, f"{{{_P_NS}}}seq", concurrent="1", nextAc="seek")
    main_ctn = etree.SubElement(
        seq, f"{{{_P_NS}}}cTn", id=str(main_seq_id), dur="indefinite", nodeType="mainSeq"
    )
    main_child = etree.SubElement(main_ctn, f"{{{_P_NS}}}childTnLst")
    prev_cond_lst = etree.SubElement(seq, f"{{{_P_NS}}}prevCondLst")
    prev_cond = etree.SubElement(prev_cond_lst, f"{{{_P_NS}}}cond", evt="onPrev", delay="0")
    etree.SubElement(etree.SubElement(prev_cond, f"{{{_P_NS}}}tgtEl"), f"{{{_P_NS}}}sldTgt")
    next_cond_lst = etree.SubElement(seq, f"{{{_P_NS}}}nextCondLst")
    next_cond = etree.SubElement(next_cond_lst, f"{{{_P_NS}}}cond", evt="onNext", delay="0")
    etree.SubElement(etree.SubElement(next_cond, f"{{{_P_NS}}}tgtEl"), f"{{{_P_NS}}}sldTgt")
    return main_child


def _new_timing_tree(slide_element: Any) -> tuple[Any, Any]:
    """Create `slide_element`'s (a <p:sld>) very first <p:timing> tree,
    including its mainSeq -- the tmRoot/seq/mainSeq skeleton every
    animation, regardless of trigger, lives under. Returns (timing,
    mainSeq's own childTnLst)."""
    from lxml import etree

    timing = etree.SubElement(slide_element, f"{{{_P_NS}}}timing")
    tn_lst = etree.SubElement(timing, f"{{{_P_NS}}}tnLst")
    root_par = etree.SubElement(tn_lst, f"{{{_P_NS}}}par")
    root_ctn = etree.SubElement(
        root_par, f"{{{_P_NS}}}cTn", id="1", dur="indefinite", restart="never", nodeType="tmRoot"
    )
    root_child = etree.SubElement(root_ctn, f"{{{_P_NS}}}childTnLst")
    main_child = _append_main_seq(root_child, 2)
    return timing, main_child


def _next_timing_id(timing: Any) -> int:
    existing_ids = [
        int(element.get("id"))
        for element in timing.iter(f"{{{_P_NS}}}cTn")
        if element.get("id") is not None
    ]
    return max(existing_ids, default=2) + 1


def _timing_root_child_list(slide_element: Any) -> Any:
    """Return-or-create the <p:timing>'s tmRoot <p:cTn>'s own
    <p:childTnLst> -- where <p:seq> (add_pptx_animation's mainSeq) and
    <p:audio>/<p:video> (auto-playing media, add_pptx_audio's own node)
    live as siblings. Creates only the minimal tmRoot/childTnLst skeleton
    if the slide has no <p:timing> yet -- deliberately *not* an empty
    mainSeq: `CT_TimeNodeList` (mainSeq's own childTnLst type) requires
    at least one child, so an empty <p:seq> is schema-invalid on its own,
    confirmed by hitting exactly that real validation error before fixing
    this. mainSeq is only ever added once an actual animation needs it
    (see _add_animation_step's own mainSeq-missing branch)."""
    from lxml import etree

    timing = slide_element.find(f"{{{_P_NS}}}timing")
    if timing is None:
        timing = etree.SubElement(slide_element, f"{{{_P_NS}}}timing")
        tn_lst = etree.SubElement(timing, f"{{{_P_NS}}}tnLst")
        root_par = etree.SubElement(tn_lst, f"{{{_P_NS}}}par")
        root_ctn = etree.SubElement(
            root_par,
            f"{{{_P_NS}}}cTn",
            id="1",
            dur="indefinite",
            restart="never",
            nodeType="tmRoot",
        )
        return etree.SubElement(root_ctn, f"{{{_P_NS}}}childTnLst")
    return timing.find(f".//{{{_P_NS}}}cTn[@nodeType='tmRoot']/{{{_P_NS}}}childTnLst")


_AUDIO_EXT_URI = "{DAA4B4D4-6D71-4841-9C94-3DE7FCFB9230}"


def _build_audio_pic_element(
    shape_id: int,
    shape_name: str,
    audio_rid: str,
    media_rid: str,
    poster_rid: str,
    x_emu: int,
    y_emu: int,
    size_emu: int,
) -> Any:
    """<p:pic> shape carrying embedded narration/background audio --
    adapted from hugohe3/ppt-master's own `svg_to_pptx/pptx_package/
    narration.py`'s `_create_audio_pic_element` (commit
    `6e3ce9c5a3b994a0e223a14a0f7edd42fd0b9f5`): real PowerPoint audio XML
    needs BOTH a legacy `<a:audioFile r:link=...>` reference (`EG_Media`,
    the pre-2010 mechanism) and a PowerPoint-2010 `<p14:media
    r:embed=...>` extension (inside `<p:nvPr>`'s own `<p:extLst>`)
    pointing at the SAME media part via two different relationship types
    -- confirmed against python-pptx's own `add_movie`, which builds the
    video equivalent (`<a:videoFile>`) the identical way for the
    identical documented reason ("two relationships to the same part...
    for legacy support for an earlier pre-Office 2010 PowerPoint media
    embedding strategy"). No `mc:Ignorable` needed for `p14:media` here,
    unlike `p14:dur`/`a14:m` elsewhere in this file -- confirmed against
    the real vendored schema: `<p:ext>` (`CT_Extension`/
    `CT_OfficeArtExtension`) already declares an `xsd:any
    processContents="lax"` wildcard, so foreign-namespaced content inside
    an extLst is schema-legitimate on its own, unlike a bare foreign
    attribute/element with no such wildcard slot."""
    from lxml import etree

    pic = etree.Element(f"{{{_P_NS}}}pic")
    nv_pic_pr = etree.SubElement(pic, f"{{{_P_NS}}}nvPicPr")
    c_nv_pr = etree.SubElement(
        nv_pic_pr, f"{{{_P_NS}}}cNvPr", id=str(shape_id), name=shape_name
    )
    etree.SubElement(
        c_nv_pr,
        f"{{{_A_NS}}}hlinkClick",
        {f"{{{_R_NS}}}id": "", "action": "ppaction://media"},
    )
    c_nv_pic_pr = etree.SubElement(nv_pic_pr, f"{{{_P_NS}}}cNvPicPr")
    etree.SubElement(c_nv_pic_pr, f"{{{_A_NS}}}picLocks", noChangeAspect="1")
    nv_pr = etree.SubElement(nv_pic_pr, f"{{{_P_NS}}}nvPr")
    etree.SubElement(nv_pr, f"{{{_A_NS}}}audioFile", {f"{{{_R_NS}}}link": audio_rid})
    ext_lst = etree.SubElement(nv_pr, f"{{{_P_NS}}}extLst")
    ext = etree.SubElement(ext_lst, f"{{{_P_NS}}}ext", uri=_AUDIO_EXT_URI)
    etree.SubElement(ext, f"{{{_P14_NS}}}media", {f"{{{_R_NS}}}embed": media_rid})

    blip_fill = etree.SubElement(pic, f"{{{_P_NS}}}blipFill")
    etree.SubElement(blip_fill, f"{{{_A_NS}}}blip", {f"{{{_R_NS}}}embed": poster_rid})
    stretch = etree.SubElement(blip_fill, f"{{{_A_NS}}}stretch")
    etree.SubElement(stretch, f"{{{_A_NS}}}fillRect")

    sp_pr = etree.SubElement(pic, f"{{{_P_NS}}}spPr")
    xfrm = etree.SubElement(sp_pr, f"{{{_A_NS}}}xfrm")
    etree.SubElement(xfrm, f"{{{_A_NS}}}off", x=str(x_emu), y=str(y_emu))
    etree.SubElement(xfrm, f"{{{_A_NS}}}ext", cx=str(size_emu), cy=str(size_emu))
    prst_geom = etree.SubElement(sp_pr, f"{{{_A_NS}}}prstGeom", prst="rect")
    etree.SubElement(prst_geom, f"{{{_A_NS}}}avLst")
    return pic


def _build_audio_timing_node(shape_id: int, ctn_id: int, start_delay_ms: int | None) -> Any:
    """<p:audio> media-playback timing node -- auto-plays `start_delay_ms`
    after the slide begins, or (when `start_delay_ms` is None) waits for
    the shape's own `ppaction://media` click hyperlink instead
    (`delay="indefinite"`, matching python-pptx's own `add_movie`/
    `_add_video_timing` for the click-triggered case). Adapted from
    hugohe3/ppt-master's own `svg_to_pptx/pptx_package/narration.py`'s
    `_create_audio_timing_element` (commit
    `6e3ce9c5a3b994a0e223a14a0f7edd42fd0b9f5`)."""
    from lxml import etree

    audio = etree.Element(f"{{{_P_NS}}}audio")
    media_node = etree.SubElement(audio, f"{{{_P_NS}}}cMediaNode", vol="80000")
    time_node = etree.SubElement(
        media_node, f"{{{_P_NS}}}cTn", id=str(ctn_id), fill="hold", display="0"
    )
    delay = "indefinite" if start_delay_ms is None else str(start_delay_ms)
    st_cond_lst = etree.SubElement(time_node, f"{{{_P_NS}}}stCondLst")
    etree.SubElement(st_cond_lst, f"{{{_P_NS}}}cond", delay=delay)
    target = etree.SubElement(media_node, f"{{{_P_NS}}}tgtEl")
    etree.SubElement(target, f"{{{_P_NS}}}spTgt", spid=str(shape_id))
    return audio


def _append_step(
    child_lst: Any,
    step_id: int,
    row_id: int,
    shape_id: int,
    animation: str,
    duration_ms: int,
    node_type: str,
    start_ms: int,
    paragraph_index: int | None,
) -> None:
    """Append one more <p:par> step to `child_lst` (a group's own
    childTnLst, or a freshly-created group's), starting `start_ms`
    milliseconds after whatever that group's own trigger condition
    resolves. This is the one shape shared by a click group's first (and
    only, pre-this-feature) step and every with-previous/after-previous
    step chained onto an existing group -- structurally identical, only
    `start_ms` and `node_type` differ."""
    from lxml import etree

    step_par = etree.SubElement(child_lst, f"{{{_P_NS}}}par")
    step_ctn = etree.SubElement(step_par, f"{{{_P_NS}}}cTn", id=str(step_id), fill="hold")
    step_st = etree.SubElement(step_ctn, f"{{{_P_NS}}}stCondLst")
    etree.SubElement(step_st, f"{{{_P_NS}}}cond", delay=str(start_ms))
    step_child = etree.SubElement(step_ctn, f"{{{_P_NS}}}childTnLst")
    row_par = etree.SubElement(step_child, f"{{{_P_NS}}}par")
    _instantiate_animation_preset_row(
        row_par, row_id, shape_id, animation, duration_ms, node_type, paragraph_index
    )


def _step_timing(step_par: Any) -> tuple[int, int]:
    """(start_ms, duration_ms) already recorded for one existing step
    <p:par> (built by _append_step) -- used to compute where the *next*
    with-previous/after-previous step chained onto the same group should
    start. duration_ms is the max `dur` among the step's own descendant
    <p:cTn> elements (every animation type here sets `dur=str(duration_ms)`
    on at least one -- the motion element itself, not just the 1ms
    visibility toggle -- so max() recovers it without per-type
    special-casing)."""
    step_ctn = step_par.find(f"{{{_P_NS}}}cTn")
    cond = step_ctn.find(f"{{{_P_NS}}}stCondLst/{{{_P_NS}}}cond")
    start_ms = int(cond.get("delay"))
    durations = [
        int(element.get("dur"))
        for element in step_ctn.iter(f"{{{_P_NS}}}cTn")
        if element is not step_ctn and element.get("dur") not in (None, "indefinite")
    ]
    return start_ms, max(durations, default=0)


def _add_animation_step(
    slide_element: Any,
    shape_id: int,
    animation: str,
    duration_ms: int,
    trigger: str,
    delay_ms: int,
    paragraph_index: int | None,
) -> None:
    """Add one animation to `slide_element`'s (a <p:sld>) <p:timing> tree,
    creating the tree if this is the slide's first animation.

    trigger="on-click" always starts a brand-new top-level click group --
    PowerPoint plays click groups in the order they appear in childTnLst,
    so on-click animations added in call order play in that order during
    the slideshow, same as this file's behavior before trigger existed.

    trigger="with-previous"/"after-previous" instead chains onto the LAST
    existing group on this slide (whichever kind it is) as one more step,
    computing its start time from that group's own most-recently-added
    step: with-previous starts at the same time as that step, after-
    previous starts once that step's own animation duration has elapsed
    -- `delay_ms` adds any extra gap on top of either. If the slide has
    no groups yet, a with-previous/after-previous call instead becomes
    the anchor of a brand-new group that starts automatically when the
    slide itself begins (no click needed) -- matching real PowerPoint,
    where the very first animation on a slide still plays even if its
    own Start mode is "After Previous"."""
    node_type = _TRIGGER_NODE_TYPES[trigger]
    timing = slide_element.find(f"{{{_P_NS}}}timing")
    if timing is None:
        timing, main_child = _new_timing_tree(slide_element)
        group = None
    else:
        main_child = timing.find(f".//{{{_P_NS}}}cTn[@nodeType='mainSeq']/{{{_P_NS}}}childTnLst")
        if main_child is None:
            # add_pptx_audio already created this slide's <p:timing> tree
            # without a mainSeq (an empty <p:seq> is itself schema-
            # invalid, so it's only ever added once an animation needs
            # it) -- add one now, alongside whatever audio/video nodes
            # are already there, rather than discarding the tree.
            root_child = timing.find(
                f".//{{{_P_NS}}}cTn[@nodeType='tmRoot']/{{{_P_NS}}}childTnLst"
            )
            main_child = _append_main_seq(root_child, _next_timing_id(timing))
            group = None
        else:
            group = main_child[-1] if len(main_child) else None

    next_id = _next_timing_id(timing)

    if trigger == "on-click" or group is None:
        from lxml import etree

        outer_id, step_id, row_id = next_id, next_id + 1, next_id + 2
        outer_par = etree.SubElement(main_child, f"{{{_P_NS}}}par")
        outer_ctn = etree.SubElement(outer_par, f"{{{_P_NS}}}cTn", id=str(outer_id), fill="hold")
        outer_st = etree.SubElement(outer_ctn, f"{{{_P_NS}}}stCondLst")
        etree.SubElement(outer_st, f"{{{_P_NS}}}cond", delay="indefinite")
        if trigger != "on-click":
            # This slide has no existing animation to chain onto, so this
            # with-previous/after-previous call anchors a new group that
            # starts on slide entry instead: it waits for mainSeq (id=2)
            # itself to begin, not a click.
            begin_cond = etree.SubElement(outer_st, f"{{{_P_NS}}}cond", evt="onBegin", delay="0")
            etree.SubElement(begin_cond, f"{{{_P_NS}}}tn", val="2")
        outer_child = etree.SubElement(outer_ctn, f"{{{_P_NS}}}childTnLst")
        _append_step(
            outer_child, step_id, row_id, shape_id, animation, duration_ms, node_type,
            delay_ms, paragraph_index,
        )
        return

    group_ctn = group.find(f"{{{_P_NS}}}cTn")
    group_child = group_ctn.find(f"{{{_P_NS}}}childTnLst")
    prev_start_ms, prev_duration_ms = _step_timing(group_child[-1])
    start_ms = (
        prev_start_ms + delay_ms
        if trigger == "with-previous"
        else prev_start_ms + prev_duration_ms + delay_ms
    )
    step_id, row_id = next_id, next_id + 1
    _append_step(
        group_child, step_id, row_id, shape_id, animation, duration_ms, node_type,
        start_ms, paragraph_index,
    )


def _ensure_paragraph_build(timing: Any, shape_id: int) -> None:
    """Add <p:bldP spid=shape_id grpId="0" build="p"/> to <p:timing>'s
    <p:bldLst> (creating it as tnLst's next sibling if needed -- CT_Timing's
    own child order is tnLst then bldLst) -- the sibling element real
    PowerPoint always emits alongside per-paragraph <p:txEl><p:pRg>
    targeting, declaring that this shape's text frame builds in one
    paragraph at a time rather than all at once. Idempotent per shape --
    a second by_paragraph call for the same shape must not add a
    duplicate bldP (spid, grpId) pair, which real PowerPoint XML never
    repeats."""
    from lxml import etree

    bld_lst = timing.find(f"{{{_P_NS}}}bldLst")
    if bld_lst is None:
        bld_lst = etree.SubElement(timing, f"{{{_P_NS}}}bldLst")
    for existing in bld_lst.findall(f"{{{_P_NS}}}bldP"):
        if existing.get("spid") == str(shape_id):
            return
    etree.SubElement(bld_lst, f"{{{_P_NS}}}bldP", spid=str(shape_id), grpId="0", build="p")


def _verify_animation_readback(
    pptx_path: Path,
    slide: int,
    shape_id: int,
    animation: str,
    trigger: str,
    paragraph_indexes: list[int] | None,
) -> None:
    """Re-open the just-saved file and confirm every animation we intended
    to add is structurally present -- one click/with/after-previous step
    per expected paragraph (or one step targeting the whole shape, if
    `paragraph_indexes` is None) whose effect node targets `shape_id` with
    the right presetID/presetClass/presetSubtype/nodeType, containing
    every one of the preset's own distinguishing motion/effect tags (see
    `_animation_preset_expected_children` -- animEffect for a fade,
    anim/tavLst for a fly, animScale for a grow, animMotion for a motion
    path, and so on for all 203 vendored presets, not a hand-maintained
    per-preset list). This is a *structural* check, not a visual one -- it
    catches "we wrote something that doesn't even parse back as intended"
    (a real risk for hand-written OOXML), not "this looks right in
    PowerPoint" (which this sandbox, with no real PowerPoint, has no way
    to check). presetClass and nodeType must match too, not just
    presetID/presetSubtype -- entrance/exit variants of the same effect
    share identical presetID/presetSubtype in real PowerPoint XML, and the
    three trigger modes share presetID/presetClass/presetSubtype entirely
    (see _TRIGGER_NODE_TYPES's comment) -- so presetClass disambiguates
    entrance/exit/emphasis/path and nodeType disambiguates trigger, and
    skipping either would let this check silently pass on a structurally
    wrong animation or trigger."""
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    target_slide = prs.slides[slide - 1]
    preset = _resolve_animation_preset(animation)
    preset_class = preset["category"][:4]
    preset_id = str(preset["preset_id"])
    preset_subtype = str(preset["preset_subtype"])
    node_type = _TRIGGER_NODE_TYPES[trigger]
    expected_children = _animation_preset_expected_children(animation)
    expected: list[int | None] = (
        list(paragraph_indexes) if paragraph_indexes is not None else [None]
    )

    matches = [
        cTn
        for cTn in target_slide.element.iter(f"{{{_P_NS}}}cTn")
        if cTn.get("presetID") == preset_id
        and cTn.get("presetSubtype") == preset_subtype
        and cTn.get("presetClass") == preset_class
        and cTn.get("nodeType") == node_type
    ]
    found: set[int | None] = set()
    for candidate in matches:
        sp_tgt = candidate.find(f".//{{{_P_NS}}}spTgt")
        if sp_tgt is None or sp_tgt.get("spid") != str(shape_id):
            continue
        if not all(
            candidate.find(f".//{{{_P_NS}}}{tag}") is not None for tag in expected_children
        ):
            continue
        p_rg = sp_tgt.find(f"{{{_P_NS}}}txEl/{{{_P_NS}}}pRg")
        found.add(int(p_rg.get("st")) if p_rg is not None else None)

    missing = [p for p in expected if p not in found]
    if missing:
        raise RuntimeError(
            f"add_pptx_animation: read-back verification failed -- the saved file's "
            f"timing tree doesn't structurally match the {animation!r}/{trigger!r} "
            f"animation just added to shape {shape_id} on slide {slide} "
            f"(paragraph(s) {missing} not found as expected). The file was not modified."
        )


def _render_to_pdf(pptx_path: Path) -> Path | None:
    """Convert `pptx_path` to a PDF via headless LibreOffice, for the
    overflow check below. Returns None -- "QA skipped", not an error -- if
    `soffice` isn't installed, times out, or fails: write_pptx has already
    succeeded by the time this runs, this is a best-effort diagnostic on
    top of it, not a requirement for the write to count as successful."""
    if shutil.which("soffice") is None:
        return None
    out_dir = Path(tempfile.mkdtemp(prefix="coscribe_pptx_qa_"))
    try:
        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(pptx_path),
            ],
            capture_output=True,
            timeout=SOFFICE_TIMEOUT,
            check=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        return None
    pdf_path = out_dir / f"{pptx_path.stem}.pdf"
    return pdf_path if pdf_path.is_file() else None


def _check_overflow(pdf_path: Path) -> list[dict[str, object]]:
    """Flag any word whose bounding box crosses the rendered page's edge --
    the LibreOffice-rendered PDF's page size mirrors the pptx slide size, so
    this is a genuine "this text overflows its slide" signal. Pure
    vector/text analysis via pdfplumber (already a dependency); no
    pdftoppm/poppler-utils or pixel rasterization needed."""
    import pdfplumber

    warnings: list[dict[str, object]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            for word in page.extract_words():
                if (
                    word["x1"] > page.width + OVERFLOW_TOLERANCE_PT
                    or word["bottom"] > page.height + OVERFLOW_TOLERANCE_PT
                ):
                    warnings.append({"slide": index, "text": word["text"]})
    return warnings


# Leftover template/placeholder text write_pptx's own model-written content
# or fill_pptx_template's untouched template shapes (see that tool's own
# "Template slots != source items" docstring paragraph) can leave behind --
# the same class of defect Anthropic's own pptx skill's QA step greps
# `markitdown` output for (`\bx{3,}\b|lorem|ipsum|\bTODO|\[insert`). Kept
# deliberately narrow: real deck content legitimately says "sample" or
# "insert" sometimes (a training deck about "how to insert a chart"), so
# this flags specific, low-false-positive patterns -- literal repeated x's,
# "lorem ipsum", a TODO marker, an unfilled "[insert ...]" bracket, and the
# literal words "placeholder"/"sample text" -- not a broad content-quality
# heuristic.
_PLACEHOLDER_LEFTOVER_RE = re.compile(
    r"\bx{3,}\b|lorem\s+ipsum|\bTODO\b|\[insert|\bplaceholder\b|\bsample\s+text\b",
    re.IGNORECASE,
)


def _leftover_placeholder_warnings(prs: PresentationType) -> list[dict[str, object]]:
    """Flag every shape whose text still matches `_PLACEHOLDER_LEFTOVER_RE`.
    Scans every shape on every slide, not just title/body placeholders --
    fill_pptx_template only ever writes into those two, so a template's
    own other, undesigned-for text (a decorative caption, a leftover
    sample stat) never gets touched by that tool at all, and this is the
    QA net that catches it reaching the user's file regardless."""
    warnings: list[dict[str, object]] = []
    for index, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            match = _PLACEHOLDER_LEFTOVER_RE.search(shape.text_frame.text)
            if match:
                warnings.append({"slide": index, "text": match.group(0)})
    return warnings


def _slide_chunks(content: str) -> list[str]:
    return [chunk for chunk in content.split("\n---\n") if chunk.strip()]


def _clear_slides(prs: PresentationType) -> None:
    """Remove every existing slide from a loaded template, in place.

    Removing only the <p:sldId> list entry leaves the slide's XML part
    orphaned in the zip package, where it collides with a newly-added
    slide's auto-generated part name (e.g. both claiming
    'ppt/slides/slide1.xml') -- verified empirically to produce a
    'Duplicate name' warning and a genuinely corrupt-risk file on save.
    Dropping the relationship first (`drop_rel`) makes python-pptx's
    package writer omit the now-unreachable part entirely, which is the
    correct fix, also verified empirically."""
    from pptx.oxml.ns import qn

    sld_id_lst = prs.slides._sldIdLst  # noqa: SLF001
    for sld_id in list(sld_id_lst):
        r_id = sld_id.get(qn("r:id"))
        prs.part.drop_rel(r_id)
        sld_id_lst.remove(sld_id)


def _widen_stock_layouts(prs: PresentationType, original_width: int) -> None:
    """After widening `prs.slide_width` (a bare `Presentation()`'s stock
    layouts are authored for the old, narrower canvas), rescale every
    TITLE/CENTER_TITLE/SUBTITLE/BODY/OBJECT-type placeholder's left/width
    proportionally to the new width -- top/height untouched, since only
    the width changed, not the height.

    A uniform horizontal scale (rather than e.g. only widening
    right-anchored placeholders) is what keeps this correct for the
    "Two Content" layout's two side-by-side OBJECT placeholders too: both
    columns and the gap between them scale by the same factor, so they
    stay equal width with a proportionally-sized gap, instead of one
    column growing and the other staying put. Verified empirically (not
    just derived): the resulting margins/gaps on every stock layout used
    by this file (Title Slide, Title and Content, Title Only, Two
    Content) render at the same *proportional* position as before, just
    scaled up -- see the write_pptx docstring/SKILL.md for the live
    render this was checked against.

    Critical detail, found live: these stock placeholders start with no
    explicit <a:xfrm> of their own at all (position/size is entirely
    inherited from the slide master) -- `placeholder.top`/`.height`
    resolve that inheritance correctly on *read*, but writing `.left`/
    `.width` makes python-pptx materialize a brand-new <a:xfrm> with
    whichever fields weren't explicitly set defaulting to 0, silently
    discarding the inherited top/height (rendered as a title clipped to
    a zero-height box). Reading top/height first and writing them back
    unchanged is what keeps them intact."""
    from pptx.enum.shapes import PP_PLACEHOLDER

    scale = prs.slide_width / original_width
    if scale == 1:
        return
    relevant = frozenset(
        {
            PP_PLACEHOLDER.TITLE,
            PP_PLACEHOLDER.CENTER_TITLE,
            PP_PLACEHOLDER.SUBTITLE,
            PP_PLACEHOLDER.BODY,
            PP_PLACEHOLDER.OBJECT,
        }
    )
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            for placeholder in layout.placeholders:
                if placeholder.placeholder_format.type in relevant:
                    top, height = placeholder.top, placeholder.height
                    placeholder.left = round(placeholder.left * scale)
                    placeholder.width = round(placeholder.width * scale)
                    placeholder.top = top
                    placeholder.height = height


def _parse_theme_tokens(theme: str) -> dict[str, str]:
    """Parse write_pptx's `theme` parameter: comma-separated key=value
    pairs choosing a deck-wide color/typography identity, e.g.
    "bg=0F172A,text=F8FAFC,accent=38BDF8,heading_font=Georgia". Every
    key is optional -- only the tokens given are changed, the rest keep
    python-pptx's stock theme values. Colors are 6-hex-digit, no '#',
    the same convention `layout: <id> ACCENTHEX` already uses. A plain
    key=value string, not a dict-typed parameter, for the same reason
    every other tool in this package avoids list/dict parameters (see
    this module's docstring)."""
    tokens: dict[str, str] = {}
    if not theme.strip():
        return tokens
    valid_keys = _THEME_COLOR_KEYS | _THEME_FONT_KEYS
    for pair in theme.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(
                f"Malformed theme token {pair!r} -- expected 'key=value', e.g. 'bg=0F172A'."
            )
        key, _, value = pair.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key not in valid_keys:
            raise ValueError(
                f"Unknown theme token {key!r} -- valid keys are {', '.join(sorted(valid_keys))}."
            )
        if key in _THEME_COLOR_KEYS and not _THEME_HEX_RE.match(value):
            raise ValueError(
                f"theme token {key}={value!r} must be a 6-hex-digit color (no '#'), "
                f"e.g. '{key}=0F172A'."
            )
        tokens[key] = value.upper() if key in _THEME_COLOR_KEYS else value
    return tokens


def _set_scheme_color(clr_scheme: Any, tag: str, hex_value: str) -> None:
    """Replace an `<a:clrScheme>` child's color definition in place --
    stock theme colors are a mix of `<a:sysClr>` (dk1/lt1) and
    `<a:srgbClr>` (dk2/lt2/accent1-6/hlink/folHlink); this always writes
    a plain `<a:srgbClr>` regardless of what was there, an explicit
    literal color rather than a system-color reference."""
    from lxml import etree
    from pptx.oxml.ns import qn

    element = clr_scheme.find(qn(f"a:{tag}"))
    if element is None:
        return
    for child in list(element):
        element.remove(child)
    etree.SubElement(element, qn("a:srgbClr")).set("val", hex_value)


def _apply_cjk_font_fix(prs: PresentationType) -> None:
    """Fills in the empty East-Asian typeface every stock theme ships
    with (see `_CJK_FALLBACK_TYPEFACE`'s comment) -- applied to every
    slide master's theme part. python-pptx has no public API for editing
    `<a:fontScheme>` (its `clrScheme`/`fontScheme` support is limited to
    undocumented internals, confirmed still unimplemented as of GitHub
    issue scanny/python-pptx#917), so this edits the theme part's XML
    blob directly and writes it back -- verified empirically to
    round-trip correctly through `prs.save()` and reopening. Only fills
    an *existing* `<a:ea>` element's `typeface` attribute, never inserts
    a new one: `CT_TextFont`'s child order is schema-fixed (latin, ea,
    cs, ...) and every real Office-authored theme already declares all
    three, so this never needs to risk getting that order wrong."""
    from lxml import etree
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml.ns import qn

    for master in prs.slide_masters:
        theme_part = master.part.part_related_by(RT.THEME)
        root = etree.fromstring(theme_part.blob)
        font_scheme = root.find(f".//{qn('a:fontScheme')}")
        if font_scheme is None:
            continue
        changed = False
        for role in ("majorFont", "minorFont"):
            font = font_scheme.find(qn(f"a:{role}"))
            if font is None:
                continue
            ea = font.find(qn("a:ea"))
            if ea is None:
                continue
            ea.set("typeface", _CJK_FALLBACK_TYPEFACE)
            changed = True
        if changed:
            assert_ooxml_valid(root, "_apply_cjk_font_fix")
            theme_part._blob = etree.tostring(  # noqa: SLF001
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )


def _apply_theme(prs: PresentationType, tokens: dict[str, str]) -> None:
    """Applies write_pptx's `theme` tokens to the deck: a solid
    slide-master background fill (`bg`) plus the underlying
    `<a:clrScheme>`/`<a:fontScheme>` XML, so shapes that reference theme
    colors/fonts (via python-pptx's public `theme_color` API, or a
    placeholder's inherited font) pick the new identity up too, not just
    the background -- same lxml-blob-replace approach as
    `_apply_cjk_font_fix`, same round-trip verification. `accent` also
    sets `hlink` (hyperlink color) to the same value, since a deck with
    one accent identity shouldn't have an unrelated blue link color left
    over from the stock theme."""
    from lxml import etree
    from pptx.dml.color import RGBColor
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.oxml.ns import qn

    color_tokens = {k: v for k, v in tokens.items() if k in _THEME_COLOR_KEYS}
    font_tokens = {k: v for k, v in tokens.items() if k in _THEME_FONT_KEYS}

    for master in prs.slide_masters:
        theme_part = master.part.part_related_by(RT.THEME)
        root = etree.fromstring(theme_part.blob)
        clr_scheme = root.find(f".//{qn('a:clrScheme')}")
        if clr_scheme is not None:
            for token_key, scheme_tag in _THEME_SCHEME_COLOR_TAGS.items():
                if token_key in color_tokens:
                    _set_scheme_color(clr_scheme, scheme_tag, color_tokens[token_key])
            if "accent" in color_tokens:
                _set_scheme_color(clr_scheme, "hlink", color_tokens["accent"])
        font_scheme = root.find(f".//{qn('a:fontScheme')}")
        if font_scheme is not None:
            for token_key, role in (("heading_font", "majorFont"), ("body_font", "minorFont")):
                if token_key not in font_tokens:
                    continue
                font = font_scheme.find(qn(f"a:{role}"))
                if font is None:
                    continue
                latin = font.find(qn("a:latin"))
                if latin is None:
                    continue
                latin.set("typeface", font_tokens[token_key])
        assert_ooxml_valid(root, "_apply_theme")
        theme_part._blob = etree.tostring(  # noqa: SLF001
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )

    if "bg" in color_tokens:
        for master in prs.slide_masters:
            master.background.fill.solid()
            master.background.fill.fore_color.rgb = RGBColor.from_string(  # type: ignore[no-untyped-call]
                color_tokens["bg"]
            )


# The real, full <a:clrScheme> vocabulary (all 12 slots ECMA-376 requires
# every theme to declare) -- unlike write_pptx's own `theme` parameter
# above (a simplified bg/text/surface/accent abstraction covering 4 of
# these), edit_pptx_theme_colors below exposes the real slot names
# directly: editing an *existing*, already-designed template wants
# precise control over which of its 6 accents changes, not a simplified
# identity applied uniformly.
_THEME_SCHEME_SLOTS = (
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)


def _parse_theme_color_slots(colors: str) -> dict[str, str]:
    """Parse edit_pptx_theme_colors's `colors` parameter: comma-separated
    key=value pairs naming real <a:clrScheme> slots directly (see
    _THEME_SCHEME_SLOTS) -- the same "key=value,key=value" string
    convention as write_pptx's own `theme` parameter (see
    _parse_theme_tokens), reused for consistency rather than inventing a
    second syntax, but validated against the full real slot vocabulary
    instead of that tool's 4-token abstraction."""
    tokens: dict[str, str] = {}
    if not colors.strip():
        raise ValueError("edit_pptx_theme_colors: colors must not be empty.")
    for pair in colors.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(
                f"Malformed color token {pair!r} -- expected 'key=value', e.g. 'accent1=0F6B5C'."
            )
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in _THEME_SCHEME_SLOTS:
            raise ValueError(
                f"Unknown theme color slot {key!r} -- valid slots are: "
                f"{', '.join(_THEME_SCHEME_SLOTS)}."
            )
        if not _THEME_HEX_RE.match(value):
            raise ValueError(
                f"color {key}={value!r} must be a 6-hex-digit color (no '#'), "
                f"e.g. '{key}=0F6B5C'."
            )
        tokens[key] = value.upper()
    return tokens


def _read_scheme_color(clr_scheme: Any, tag: str) -> str | None:
    """Resolve one <a:clrScheme> slot's current hex value. Stock themes
    mix `<a:sysClr>` (dk1/lt1) and `<a:srgbClr>` (everything else, see
    _set_scheme_color's own comment) -- `<a:srgbClr val="RRGGBB"/>`
    resolves directly; `<a:sysClr .../>` resolves via its own `lastClr`
    attribute, the literal fallback color every real sysClr element
    carries for exactly this purpose. None if the slot is missing
    (shouldn't happen in a real, schema-valid theme -- ECMA-376 requires
    all 12 -- but defensive rather than assumed)."""
    from pptx.oxml.ns import qn

    element = clr_scheme.find(qn(f"a:{tag}"))
    if element is None:
        return None
    srgb = element.find(qn("a:srgbClr"))
    if srgb is not None:
        val: str | None = srgb.get("val")
        return val
    sys_clr = element.find(qn("a:sysClr"))
    if sys_clr is not None:
        last_clr: str | None = sys_clr.get("lastClr")
        return last_clr
    return None


_CHART_TYPES = frozenset({"bar", "line", "pie"})


def _parse_chart_table(content: str) -> tuple[list[str], dict[str, list[float]]]:
    """Parse a pipe-table (same convention as write_xlsx's content) into
    categories (first column) and series (remaining columns, keyed by their
    header cell) -- avoids a list/dict-typed tool parameter entirely, since
    no tool in this package uses one: aisuite's Gemini schema inference has
    broken on less exotic type hints than that (see this module's and
    spreadsheets.py's `Optional[int]` comments), so a plain pipe-table
    string is the safer, already-proven shape."""
    rows: list[list[str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_table_row(line)
        if cells is None:
            raise ValueError(
                f"add_pptx_chart data must be pipe-table rows only (`| cell | cell |`), "
                f"got: {line!r}"
            )
        if _is_separator_row(cells):
            continue
        rows.append(cells)

    if len(rows) < 2:
        raise ValueError("add_pptx_chart data needs a header row plus at least one data row")
    header, *data_rows = rows
    if len(header) < 2:
        raise ValueError(
            "add_pptx_chart data needs a category column plus at least one data column"
        )

    series_names = header[1:]
    categories = [row[0] for row in data_rows]
    series: dict[str, list[float]] = {name: [] for name in series_names}
    for row in data_rows:
        for index, name in enumerate(series_names, start=1):
            raw_value = row[index] if index < len(row) else ""
            try:
                series[name].append(float(raw_value))
            except ValueError:
                raise ValueError(
                    f"add_pptx_chart data cell {raw_value!r} (column {name!r}) is not numeric"
                ) from None
    return categories, series


def _spd_for(duration: float) -> str:
    if duration <= 0.5:
        return "fast"
    if duration <= 1.5:
        return "med"
    return "slow"


def _set_slide_transition(slide_element: Any, transition: str, duration: float) -> None:
    """Replace `slide_element`'s (a <p:sld>) transition, in place.

    Verified empirically (round-tripped through python-pptx with warnings
    treated as errors, and through a real LibreOffice conversion): a fresh
    slide's only children are cSld/clrMapOvr, and per the ECMA-376 schema
    order, <p:transition> belongs immediately after those and before any
    <p:timing>/<p:extLst> -- since neither of those exist yet on any slide
    this tool touches, appending at the end of `slide_element` is always
    schema-correct here. "none" means no <p:transition> element at all,
    which is how PowerPoint itself represents no transition.

    p14:dur (precise millisecond timing) is written for every effect, not
    just the 12 whose own preset element already lives in the base "p"
    namespace -- same reasoning either way: the base ECMA-376 schema only
    has the coarse `spd` (fast/med/slow) attribute, `p14:dur` is what real
    PowerPoint XML actually carries for the exact duration a caller gave.
    A non-"p"-namespaced effect (p14/p15/p159, e.g. "morph"/"vortex")
    means both the effect's own preset element *and* the p14:dur attribute
    need marking -- unmarking all three extension prefixes unconditionally
    before marking exactly the one(s) this transition actually needs is
    simpler and just as correct as tracking what the *previous* transition
    used, since transitions are this codebase's sole owner of p14/p15/p159
    (add_pptx_formula owns a14 instead, untouched here).
    """
    from lxml import etree

    existing = slide_element.find(f"{{{_P_NS}}}transition")
    if existing is not None:
        slide_element.remove(existing)
    for prefix in _TRANSITION_MCE_PREFIXES:
        _unmark_mce_ignorable(slide_element, prefix)
    if transition == "none":
        return
    canonical = _TRANSITION_ALIASES.get(transition, transition)
    spec = _TRANSITION_SPECS[canonical]
    prefix = str(spec.get("prefix", "p"))
    namespace = _TRANSITION_NAMESPACES[prefix]

    transition_element = etree.SubElement(
        slide_element, f"{{{_P_NS}}}transition", nsmap={"p14": _P14_NS}
    )
    transition_element.set("spd", _spd_for(duration))
    transition_element.set(f"{{{_P14_NS}}}dur", str(round(duration * 1000)))
    effect_element = etree.SubElement(
        transition_element,
        f"{{{namespace}}}{spec['element']}",
        nsmap={prefix: namespace} if prefix != "p" else None,
    )
    for key, value in spec["attrs"].items():
        effect_element.set(key, str(value))

    _mark_mce_ignorable(slide_element, "p14")
    if prefix != "p":
        _mark_mce_ignorable(slide_element, prefix)


def _mark_mce_ignorable(root_element: Any, prefix: str) -> None:
    """Add `prefix` to `root_element`'s `mc:Ignorable` token list (creating
    the attribute if absent, a no-op if `prefix` is already there) -- the
    declaration real PowerPoint-authored slides carry alongside any p14:/
    a14:-namespaced extension attribute, and what lets `_ooxml_validate.py`
    tell a real OOXML defect apart from expected, declared extension
    content instead of rejecting both alike."""
    existing_tokens = (root_element.get(f"{{{_MC_NS}}}Ignorable") or "").split()
    if prefix in existing_tokens:
        return
    root_element.set(f"{{{_MC_NS}}}Ignorable", " ".join([*existing_tokens, prefix]))


def _unmark_mce_ignorable(root_element: Any, prefix: str) -> None:
    """Inverse of `_mark_mce_ignorable` -- drops `prefix` from the token
    list (and the attribute entirely once empty), keeping a slide that no
    longer has any p14:-namespaced content from still claiming it does."""
    remaining = [
        token
        for token in (root_element.get(f"{{{_MC_NS}}}Ignorable") or "").split()
        if token != prefix
    ]
    if remaining:
        root_element.set(f"{{{_MC_NS}}}Ignorable", " ".join(remaining))
    elif root_element.get(f"{{{_MC_NS}}}Ignorable") is not None:
        del root_element.attrib[f"{{{_MC_NS}}}Ignorable"]


def _add_inline_runs(paragraph: Any, text: str) -> None:
    """pptx counterpart to ``documents.py``'s ``_add_inline_runs`` -- adds
    ``text`` as one run per ``parse_inline_runs`` fragment so
    ``**bold**``/``*italic*`` render as real formatting instead of literal
    markup characters."""
    for run_text, bold, italic in parse_inline_runs(text):
        run = paragraph.add_run()
        run.text = run_text
        if bold:
            run.font.bold = True
        if italic:
            run.font.italic = True


def _set_title(slide: Any, title: str) -> None:
    # .clear() first -- see _fill_content_placeholder's docstring for why:
    # a freshly-added slide's title placeholder is already empty, so this
    # is a no-op there, but fill_pptx_template reuses this same helper on
    # an *existing* template slide's title, which a real-world template
    # (anyone's own uploaded .pptx, not just coscribe's own bundled ones)
    # almost always ships with real placeholder/sample text already in it.
    text_frame = slide.shapes.title.text_frame
    text_frame.clear()
    _add_inline_runs(text_frame.paragraphs[0], title)


def _add_bullet_slide(prs: PresentationType, title: str, blocks: list[Block]) -> None:
    body_blocks = [block for block in blocks if block.kind != "heading"]
    if not body_blocks:
        # No body content -- Title Only avoids leaving an empty "click to
        # add text" placeholder in the deck.
        slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_ONLY_LAYOUT])
        _set_title(slide, title)
        return
    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_AND_CONTENT_LAYOUT])
    _set_title(slide, title)
    _fill_content_placeholder(slide.placeholders[1], body_blocks)


_NUMERIC_CELL_RE = re.compile(r"^[¥$€]?-?[\d,]+(\.\d+)?\s*[%MKmk]?$")
_PERCENT_CELL_RE = re.compile(r"^(-?\d+(?:\.\d+)?)%$")


def _looks_numeric_cell(text: str) -> bool:
    """Heuristic for right-aligning a table cell's text -- ¥8.2M/34%/112%/
    2,847 all match, plain words (region/person names) don't. Used only to
    decide alignment, never to reject/transform content, so a false
    negative (a numeric-looking value rendered left-aligned) is harmless."""
    return bool(_NUMERIC_CELL_RE.match(text.strip()))


def _percent_cell_over_100(text: str) -> bool:
    """True for a lone "NNN%" cell over 100 -- a completion-rate/target-
    attainment table's most useful signal, and the one piece of design
    polish a real user directly flagged a competing tool doing that
    coscribe's own tables didn't (see PPTX_DESIGN.md §0)."""
    match = _PERCENT_CELL_RE.match(text.strip())
    return match is not None and float(match.group(1)) > 100


def _set_cell_thin_bottom_border(cell: Any, color_hex: str) -> None:
    """Hairline bottom-only border (hides left/right/top) -- the "minimal
    horizontal-rule table" look instead of PowerPoint's default full grid.
    ``CT_TableCellProperties``'s child sequence is schema-fixed (lnL, lnR,
    lnT, lnB, *then* a fill element) and python-pptx has no generated
    insert-in-order helpers for the ln* elements in this version (`dir()`
    on a real ``tcPr`` has no `_insert_lnL`/etc.) -- inserting in the
    wrong order still round-trips fine through python-pptx's own lenient
    reader (confirmed empirically), but real PowerPoint enforces the
    schema strictly, so this always locates tcPr's existing fill child
    (call ``cell.fill.background()``/``.solid()`` *before* this, never
    after) and inserts lnL/lnR/lnT/lnB immediately before it, guaranteeing
    correct order regardless of what fill was set."""
    from lxml import etree
    from pptx.oxml.ns import qn

    fill_tags = {
        qn("a:noFill"),
        qn("a:solidFill"),
        qn("a:gradFill"),
        qn("a:blipFill"),
        qn("a:pattFill"),
        qn("a:grpFill"),
    }
    tc_pr = cell._tc.get_or_add_tcPr()
    insert_at = len(tc_pr)
    for index, child in enumerate(tc_pr):
        if child.tag in fill_tags:
            insert_at = index
            break
    for offset, (tag, border_color) in enumerate(
        [("a:lnL", None), ("a:lnR", None), ("a:lnT", None), ("a:lnB", color_hex)]
    ):
        line = etree.Element(qn(tag))
        if border_color is None:
            etree.SubElement(line, qn("a:noFill"))
        else:
            line.set("w", "9525")  # 0.75pt hairline
            solid_fill = etree.SubElement(line, qn("a:solidFill"))
            etree.SubElement(solid_fill, qn("a:srgbClr")).set("val", border_color)
        tc_pr.insert(insert_at + offset, line)


def _add_table_slide(prs: PresentationType, title: str, rows: list[list[str]]) -> None:
    """Real, live-reported bug this fixes (PPTX_DESIGN.md §0): building a
    table with no styling at all leaves PowerPoint's own default table
    style applied -- a dated, heavy blue-banded grid a real user directly
    compared against a competing tool's output and found worse-looking,
    despite that competitor's "table" not even being a real table object.
    Strips the default style (no fill/banding), replaces the full grid
    with hairline bottom-only rules, right-aligns numeric-looking cells,
    and bolds+flags any lone "NNN%" cell over 100 in a warm accent color --
    a completion-rate table's single most useful visual signal, and
    specifically the one polish detail a competing tool was confirmed to
    do that this function didn't.

    Real, live-reported bug this `_content_area` call fixes too: the
    previous hardcoded `Inches(0.5), Inches(9)` placement was sized for
    python-pptx's own default 10in-wide blank Presentation(), but
    write_pptx always resizes the deck to widescreen
    (`_WIDESCREEN_WIDTH_EMU`, 13.333in) -- on the real slide width the
    table actually ends up on, that fixed 9in width left ~3.8in of dead
    space on the right, visibly off-center. icon-list/stat-callout
    already computed position from the deck's own real dimensions via
    `_content_area`; this did not, confirmed directly against a real
    rendered screenshot."""
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_THEME_COLOR
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Pt

    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_ONLY_LAYOUT])
    _set_title(slide, title)
    n_cols = max(len(row) for row in rows)
    left, top, width, height = _content_area(prs)
    table_shape = slide.shapes.add_table(len(rows), n_cols, left, top, width, height)
    table = table_shape.table
    table.first_row = False
    table.horz_banding = False

    # theme_color, not a literal RGB -- follows whatever the deck's own
    # <a:clrScheme> dk1 is (the stock near-black default, or write_pptx's
    # `theme` token's `text` value), so a table stays readable if the
    # deck sets a dark background instead of staying hardcoded dark-gray-
    # on-white. flagged_color stays a literal: "over 100%" is a semantic
    # warning color, not part of the deck's own identity.
    flagged_color = RGBColor(0xB3, 0x26, 0x1E)  # type: ignore[no-untyped-call]
    header_border = "333333"
    body_border = "D9D9D9"

    for row_index, row in enumerate(rows):
        is_header = row_index == 0
        for col_index in range(n_cols):
            cell_text = row[col_index] if col_index < len(row) else ""
            cell = table.cell(row_index, col_index)
            cell.fill.background()
            _set_cell_thin_bottom_border(cell, header_border if is_header else body_border)
            cell.margin_top = Pt(6)
            cell.margin_bottom = Pt(6)
            paragraph = cell.text_frame.paragraphs[0]
            paragraph.alignment = (
                PP_ALIGN.RIGHT
                if not is_header and _looks_numeric_cell(cell_text)
                else PP_ALIGN.LEFT
            )
            _add_inline_runs(paragraph, cell_text)
            flagged = not is_header and _percent_cell_over_100(cell_text)
            for run in paragraph.runs:
                run.font.size = Pt(12)
                if is_header:
                    run.font.bold = True
                    run.font.color.theme_color = MSO_THEME_COLOR.DARK_1
                elif flagged:
                    run.font.bold = True
                    run.font.color.rgb = flagged_color
                else:
                    run.font.color.theme_color = MSO_THEME_COLOR.DARK_1


def _parse_layout_directive(chunk: str) -> tuple[str, str, str]:
    """Split an optional ``layout: <id> [ACCENTHEX]`` directive off the
    first non-blank line of a slide chunk. Returns ``(layout_id,
    accent_hex, remaining_chunk)`` -- ``layout_id`` is ``""`` when the
    first non-blank line isn't a layout directive at all, which is the
    existing title+bullets/table path, byte-for-byte unchanged.
    ``accent_hex`` is ``""`` when no explicit hex was given -- the
    icon-list/stat-callout builders treat that as "follow the deck's
    theme accent color" rather than a hardcoded fallback hex, so a
    slide's accent tracks whatever `write_pptx`'s `theme` parameter (or
    the stock theme, if none was given) sets accent1 to. A line that
    *does* start with ``layout:`` but doesn't fully match (bad id,
    malformed hex) raises rather than silently falling through as plain
    body text, since that would be a confusing way to fail."""
    lines = chunk.splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        if not _LAYOUT_PREFIX_RE.match(line):
            return "", "", chunk
        match = _LAYOUT_DIRECTIVE_RE.match(line)
        if match is None:
            raise ValueError(
                f"Malformed layout directive {line!r} -- expected "
                f"'layout: <id> [ACCENTHEX]' with id one of "
                f"{', '.join(sorted(_LAYOUT_IDS))} and an optional 6-hex-digit color."
            )
        layout_id = match.group("id").lower()
        if layout_id not in _LAYOUT_IDS:
            raise ValueError(
                f"Unknown layout {layout_id!r} -- valid layouts are "
                f"{', '.join(sorted(_LAYOUT_IDS))}."
            )
        accent = (match.group("accent") or "").upper()
        remaining = "\n".join(lines[:index] + lines[index + 1 :])
        return layout_id, accent, remaining
    return "", "", chunk


def _content_area(prs: PresentationType) -> tuple[int, int, int, int]:
    """(left, top, width, height) in EMU for the body area below the title
    placeholder -- expressed as fractions of the deck's own
    slide_width/slide_height (measured against python-pptx's own default
    Title-and-Content layout's real placeholder geometry: 0.5in/1.75in
    margins on a 10in x 7.5in slide), not hardcoded inches, so it still
    holds under a differently-sized `template_path` deck, not just the
    default 4:3 canvas."""
    # `or` fallback, not an assert: python-pptx types slide_width/slide_height
    # as `Length | None` (only None for a malformed/sizeless deck, which
    # never happens for a real .pptx), and 9144000/6858000 EMU is exactly
    # python-pptx's own default 10in x 7.5in -- the same size this
    # function's constants were measured against.
    slide_width = prs.slide_width or 9144000
    slide_height = prs.slide_height or 6858000
    left = int(slide_width * _MARGIN_FRACTION)
    top = int(slide_height * _CONTENT_TOP_FRACTION)
    width = int(slide_width * (1 - 2 * _MARGIN_FRACTION))
    height = int(slide_height * (_CONTENT_BOTTOM_FRACTION - _CONTENT_TOP_FRACTION))
    return left, top, width, height


def _icon_list_items(blocks: list[Block]) -> list[tuple[str, str]]:
    """(glyph, label) pairs from a `layout: icon-list` slide's bullet
    blocks -- `- [X] label` uses X as the glyph, a bare bullet is
    auto-numbered."""
    if not blocks:
        raise ValueError("layout: icon-list needs at least one bullet item.")
    if any(block.kind not in ("bullet", "number") for block in blocks):
        raise ValueError(
            "layout: icon-list only accepts bullet items -- no paragraphs or tables."
        )
    if len(blocks) > _ICON_LIST_MAX_ITEMS:
        raise ValueError(
            f"layout: icon-list supports at most {_ICON_LIST_MAX_ITEMS} items "
            f"({len(blocks)} given) -- split the rest onto another slide."
        )
    items: list[tuple[str, str]] = []
    for index, block in enumerate(blocks, start=1):
        match = _ICON_GLYPH_RE.match(block.text)
        if match:
            glyph = match.group("glyph")
            if not _SAFE_GLYPH_RE.match(glyph):
                raise ValueError(
                    f"layout: icon-list glyph {glyph!r} isn't plain ASCII -- an emoji or "
                    "other pictograph character renders using the font's own built-in "
                    "color glyph, which ignores the icon circle's accent color and looks "
                    "like a mismatched sticker (verified live: some emoji render clean, "
                    "others don't, inconsistently within the same deck). Use a bold "
                    "letter, digit(s), or one of !?*+-=/#@%& instead (e.g. 'AI', '1', "
                    "'*'), or drop the '[...]' prefix entirely to auto-number."
                )
            if _WORD_LIKE_GLYPH_RE.match(glyph):
                raise ValueError(
                    f"layout: icon-list glyph {glyph!r} looks like a spelled-out word, "
                    "not a symbol -- a circle this small can't fit one legibly. Use a "
                    "single character, digit(s), or a symbol instead (e.g. '*', '1', "
                    "'AI'), or drop the '[...]' prefix entirely to auto-number."
                )
            items.append((glyph, match.group("text")))
        else:
            items.append((str(index), block.text))
    return items


def _add_icon_list_slide(
    prs: PresentationType, title: str, items: list[tuple[str, str]], accent: str
) -> None:
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_THEME_COLOR
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt

    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_ONLY_LAYOUT])
    _set_title(slide, title)

    left, top, width, height = _content_area(prs)
    row_height = height // len(items)
    icon_diameter = min(int(row_height * 0.7), int(width * 0.08))
    label_gap = int(icon_diameter * 0.35)

    for index, (glyph, label) in enumerate(items):
        row_top = top + index * row_height
        icon_top = row_top + (row_height - icon_diameter) // 2
        icon = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, left, icon_top, icon_diameter, icon_diameter
        )
        icon.fill.solid()
        # No explicit per-slide accent (empty string) follows the deck's
        # own theme accent color (accent1) instead of a hardcoded literal
        # -- so a write_pptx `theme` token actually reaches this shape,
        # not just the background/title text. See PPTX_DESIGN.md's
        # theme_color-migration note.
        if accent:
            icon.fill.fore_color.rgb = RGBColor.from_string(accent)  # type: ignore[no-untyped-call]
        else:
            icon.fill.fore_color.theme_color = MSO_THEME_COLOR.ACCENT_1
        icon.line.fill.background()
        icon_frame = icon.text_frame
        icon_frame.word_wrap = True
        icon_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        icon_frame.margin_left = icon_frame.margin_right = 0
        icon_frame.margin_top = icon_frame.margin_bottom = 0
        icon_paragraph = icon_frame.paragraphs[0]
        icon_paragraph.alignment = PP_ALIGN.CENTER
        icon_run = icon_paragraph.add_run()
        icon_run.text = glyph
        icon_run.font.bold = True
        icon_run.font.size = Pt(min(24, max(11, round(icon_diameter / 914400 * 22))))
        icon_run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)  # type: ignore[no-untyped-call]

        label_left = left + icon_diameter + label_gap
        label_width = left + width - label_left
        label_box = slide.shapes.add_textbox(label_left, row_top, label_width, row_height)
        label_frame = label_box.text_frame
        label_frame.word_wrap = True
        label_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        label_frame.margin_left = label_frame.margin_right = 0
        _add_inline_runs(label_frame.paragraphs[0], label)


def _stats_from_table(rows: list[list[str]]) -> list[tuple[str, str]]:
    """(stat, label) pairs from a `layout: stat-callout` slide's pipe-table
    -- the *same* table grammar every other table block uses, just read as
    stat/label pairs instead of rendered as a literal grid; the header row
    is discarded, mirroring `_parse_chart_table`'s header/data split."""
    if len(rows) < 2:
        raise ValueError(
            "layout: stat-callout's table needs a header row plus at least one stat row."
        )
    header, *data_rows = rows
    if len(header) < 2:
        raise ValueError("layout: stat-callout's table needs a stat column and a label column.")
    if len(data_rows) > _STAT_CALLOUT_MAX_ITEMS:
        raise ValueError(
            f"layout: stat-callout supports at most {_STAT_CALLOUT_MAX_ITEMS} stats "
            f"({len(data_rows)} given) -- split the rest onto another slide."
        )
    pairs = [(row[0] if row else "", row[1] if len(row) > 1 else "") for row in data_rows]
    # Real, live-reproduced mistake this catches: the first column must be
    # the short headline value ("+18.4%", "62") and the second the longer
    # descriptive label ("季度营收增长") -- _add_stat_callout_slide renders
    # column 0 large/bold/accent-colored and column 1 small/muted, so a
    # caller that puts the label first (an easy mix-up -- "label, value"
    # reads more naturally than "value, label" in a table) gets a card
    # with a wrapped, oversized label and a shrunken value instead of an
    # error, which only shows up as a rendering defect, not a caught
    # mistake. A stat is essentially always shorter than its own label in
    # any real KPI table, so "column 0 longer than column 1" on a
    # majority of rows is a reliable enough signal to raise instead of
    # silently rendering it backwards.
    swapped = sum(1 for stat, label in pairs if label and len(stat) > len(label))
    if swapped > len(pairs) // 2:
        raise ValueError(
            "layout: stat-callout's table columns look swapped -- the FIRST column "
            "must be the short headline value (e.g. '+18.4%', '62', '3,140') and the "
            "SECOND column the longer descriptive label (e.g. '季度营收增长'), not the "
            "other way around. Example: header '| 数值 | 指标 |', then a row like "
            "'| +18.4% | 季度营收增长 |'."
        )
    return pairs


def _add_stat_callout_slide(
    prs: PresentationType, title: str, stats: list[tuple[str, str]], accent: str
) -> None:
    """Real, live-reported bug this fixes (PPTX_DESIGN.md §0): the
    previous design filled each card solid with the raw accent color and
    centered two same-weight white text lines on it -- a flat block, not
    a "designed" card. A real user directly compared this against a
    competing tool's KPI cards (light background, border only, a small
    muted label above a large emphasized number) and found ours
    noticeably worse despite being the "more native" implementation.
    Rewritten to the same light-card, real-typographic-hierarchy shape:
    white/bordered instead of solid-filled, a small muted label first,
    then a large accent-colored number -- left-aligned, not centered,
    matching the reference layout's own visual rhythm. `stats`' own
    (stat, label) tuple order/meaning is unchanged (stat is still the
    headline value); only which one renders first/larger changed."""
    from pptx.dml.color import RGBColor
    from pptx.enum.dml import MSO_THEME_COLOR
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Pt

    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_ONLY_LAYOUT])
    _set_title(slide, title)

    left, top, width, height = _content_area(prs)
    gap = int(width * 0.03)
    card_width = (width - gap * (len(stats) - 1)) // len(stats)
    # 0.4, not the previous 0.7 -- a label line + one emphasized number
    # line don't need a near-square card; the previous ratio left a real
    # user-reported large empty gap at the bottom of every card, visible
    # in a rendered screenshot.
    card_height = min(height, int(card_width * 0.4))
    # Vertically centered in the content area, not anchored to its top --
    # a real, live-reproduced defect: card_height is deliberately much
    # smaller than the full content area (see the 0.4 ratio comment
    # above), and anchoring at `top` alone dumped the entire leftover
    # space below the cards as a large empty gap for the rest of the
    # slide instead of splitting it above/below.
    card_top = top + (height - card_height) // 2
    border_color = RGBColor(0xE0, 0xE0, 0xE0)  # type: ignore[no-untyped-call]
    label_color = RGBColor(0x70, 0x70, 0x70)  # type: ignore[no-untyped-call]

    for index, (stat, label) in enumerate(stats):
        card_left = left + index * (card_width + gap)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, card_left, card_top, card_width, card_height
        )
        card.fill.solid()
        card.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)  # type: ignore[no-untyped-call]
        card.line.color.rgb = border_color
        card.line.width = Pt(0.75)
        frame = card.text_frame
        frame.word_wrap = True
        frame.vertical_anchor = MSO_ANCHOR.TOP
        pad = int(card_width * 0.08)
        frame.margin_left = frame.margin_right = pad
        frame.margin_top = int(card_height * 0.14)
        frame.margin_bottom = pad

        label_paragraph = frame.paragraphs[0]
        label_paragraph.alignment = PP_ALIGN.LEFT
        label_run = label_paragraph.add_run()
        label_run.text = label
        label_run.font.size = Pt(12)
        label_run.font.color.rgb = label_color

        stat_paragraph = frame.add_paragraph()
        stat_paragraph.alignment = PP_ALIGN.LEFT
        stat_paragraph.space_before = Pt(8)
        stat_run = stat_paragraph.add_run()
        stat_run.text = stat
        stat_run.font.bold = True
        stat_run.font.size = Pt(28)
        # Same theme-accent-when-unspecified logic as _add_icon_list_slide.
        if accent:
            stat_run.font.color.rgb = RGBColor.from_string(accent)  # type: ignore[no-untyped-call]
        else:
            stat_run.font.color.theme_color = MSO_THEME_COLOR.ACCENT_1


def _split_two_column(body_text: str) -> tuple[str, str]:
    """Split a `layout: two-column` slide's post-title content on a line
    containing exactly `>>>` -- same line-scan style as the module's own
    `_slide_chunks`."""
    lines = body_text.splitlines()
    marker_index = next(
        (i for i, line in enumerate(lines) if _TWO_COLUMN_MARKER_RE.match(line.strip())), None
    )
    if marker_index is None:
        raise ValueError(
            "layout: two-column needs a line containing exactly '>>>' separating the "
            "left column's content from the right column's."
        )
    left = "\n".join(lines[:marker_index])
    right = "\n".join(lines[marker_index + 1 :])
    return left, right


def _fill_content_placeholder(placeholder: Any, blocks: list[Block]) -> None:
    """Real, live-found bug this .clear() fixes: every existing caller
    hands this a placeholder from a slide this code itself just added
    (always empty), so appending straight into paragraphs[0] was
    invisible. fill_pptx_template reuses this same helper on an *existing*
    template slide's own body placeholder instead -- which, for any
    real-world template (a user's own uploaded .pptx, not just coscribe's
    own bundled ones, which are deliberately authored with empty
    placeholders), almost always already has real sample text and extra
    paragraphs in it. Confirmed live against a real Microsoft-authored
    template ("The science of public speaking"): without this .clear(),
    filling "NEW BULLET A" into a placeholder already containing "Know
    your material in advance" produced the single garbled run "Know your
    material in advanceNEW BULLET A", and any of the template's *original*
    paragraphs past the first one (blocks[1:] only ever *adds* new
    paragraphs, never removes old ones) survived untouched in the final
    file. TextFrame.clear()/_Paragraph.clear() are python-pptx's own
    primitives for exactly this: remove every paragraph but the first,
    then strip that first paragraph's content while preserving its
    paragraph-level formatting (alignment, bullet/indent level) so newly
    added runs still inherit the placeholder's own designed look.

    Real, live-reported bug this ``_suppress_bullet_for_paragraph`` call
    fixes: this function never distinguished a markdown ``Block(kind=
    "paragraph")`` (a plain flowing line, no leading ``-``/``*``) from
    ``Block(kind="bullet")`` at the XML level -- every block just became
    a new paragraph with no bullet-related formatting touched at all.
    Harmless for coscribe's own bundled templates/write_pptx layouts,
    whose "Title and Content" placeholder happens to auto-bullet every
    paragraph by inherited layout default -- but that same inheritance
    means a single flowing paragraph (a dense-text slide, no bullets
    intended at all) rendered with a meaningless bullet glyph in front
    of it, confirmed directly against a real rendered screenshot."""
    text_frame = placeholder.text_frame
    if not blocks:
        return
    text_frame.clear()
    _add_inline_runs(text_frame.paragraphs[0], blocks[0].text)
    _suppress_bullet_for_paragraph(text_frame.paragraphs[0], blocks[0])
    for block in blocks[1:]:
        paragraph = text_frame.add_paragraph()
        _add_inline_runs(paragraph, block.text)
        _suppress_bullet_for_paragraph(paragraph, block)


def _suppress_bullet_for_paragraph(paragraph: Any, block: Block) -> None:
    """No-op for bullet/number-kind blocks -- their existing, not-
    reported-as-broken behavior (inheriting whatever bullet character
    the placeholder/layout already applies) is untouched. For a plain
    ``kind="paragraph"`` block, adds an explicit ``<a:buNone/>`` to the
    paragraph's own ``<a:pPr>``, which overrides that same inherited
    default -- verified this round-trips cleanly through python-pptx
    with no schema-order issues (``<a:buNone/>`` as the sole ``pPr``
    child is always schema-valid, unlike table cell borders' fixed
    multi-child sequence, see ``_set_cell_thin_bottom_border``)."""
    if block.kind != "paragraph":
        return
    from lxml import etree
    from pptx.oxml.ns import qn

    p_pr = paragraph._p.get_or_add_pPr()
    etree.SubElement(p_pr, qn("a:buNone"))


def _find_body_placeholder(slide: Any, index: int = 0) -> Any | None:
    """The `index`-th (0-based, in on-slide order) non-title placeholder on
    `slide` whose type is BODY, OBJECT, or SUBTITLE -- the realistic set of
    "receives paragraph/bullet text" placeholder types a real slide can
    have (confirmed live against python-pptx's own stock layouts: "Title
    and Content" uses OBJECT for its content placeholder, not BODY; "Title
    Slide" uses SUBTITLE for its second placeholder). Used by
    fill_pptx_template/edit_pptx_text to fill an *existing* slide's own
    placeholder, unlike _fill_content_placeholder's other callers, which
    always operate on a placeholder from a slide this code itself just
    added.

    `index` exists for edit_pptx_text's placeholder_index parameter --
    real templates commonly have a "two column" layout with *two*
    independent content placeholders on one slide (confirmed against a
    real Microsoft-authored template), and index=0 (the default, and
    fill_pptx_template's only behavior) can only ever reach the first one.

    Real, live-found bug this .has_text_frame check fixes: an OBJECT
    placeholder slot can already hold a native table (a GraphicFrame, not
    a text-frame shape) rather than plain text -- confirmed against a real
    Microsoft-authored template whose "Agenda" slide's content placeholder
    was exactly this. Without the check, this function still matched it
    (its placeholder_format.type is OBJECT same as a text one), and the
    caller's `.text_frame` access below crashed with an opaque
    AttributeError: 'PlaceholderGraphicFrame' object has no attribute
    'text_frame' instead of the same clean, actionable "no body/content
    placeholder" ValueError a slide with no eligible placeholder at all
    already produces -- treating a table-holding slot as ineligible here
    gets both cases the same clear error."""
    from pptx.enum.shapes import PP_PLACEHOLDER

    eligible = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.SUBTITLE}
    title_shape = slide.shapes.title
    matches = []
    for placeholder in slide.placeholders:
        if title_shape is not None and placeholder.shape_id == title_shape.shape_id:
            continue
        if placeholder.placeholder_format.type in eligible and placeholder.has_text_frame:
            matches.append(placeholder)
    if index < 0 or index >= len(matches):
        return None
    return matches[index]


def _add_two_column_slide(
    prs: PresentationType, title: str, left_blocks: list[Block], right_blocks: list[Block]
) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[_TWO_CONTENT_LAYOUT])
    _set_title(slide, title)
    _fill_content_placeholder(slide.placeholders[1], left_blocks)
    _fill_content_placeholder(slide.placeholders[2], right_blocks)


def _shape_bbox(shape: Any) -> tuple[int, int, int, int] | None:
    if shape.left is None or shape.top is None or shape.width is None or shape.height is None:
        return None
    return shape.left, shape.top, shape.width, shape.height


def _bbox_overlap_fraction(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    overlap_w = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    overlap_h = max(0, min(ay + ah, by + bh) - max(ay, by))
    intersection = overlap_w * overlap_h
    if intersection == 0:
        return 0.0
    smaller_area = min(aw * ah, bw * bh)
    return intersection / smaller_area if smaller_area else 0.0


_TEXT_OVERLAP_THRESHOLD = 0.15


def _check_text_overlaps(prs: Any) -> list[dict[str, object]]:
    """Flag pairs of non-empty text shapes on the same slide whose bounding
    boxes overlap significantly -- a purely geometric check that needs no
    rendering (no LibreOffice/poppler-utils), so it runs unconditionally.
    Catches a real, reproduced bug directly: `run_node_script`-generated
    scripts sometimes place a subtitle text box at a fixed y-offset that
    overlaps the body content box on some slides but not others (depending
    on how much vertical space the title/eyebrow above it actually took).
    A slight/incidental overlap (rounding, a label brushing a border) isn't
    flagged -- only overlap covering a real fraction of the smaller shape
    is, to avoid false positives from deliberate close spacing.

    Also flags a text shape overlapping a chart or table -- a real,
    live-reproduced defect this check couldn't see before: a chart/table
    is a `GraphicFrame`, not a text-frame shape, so it was invisible to
    the text-vs-text scan above even when its own rendered content (axis
    labels, a chart title, cell text) visually collided with a nearby
    placeholder's text. `add_pptx_chart`'s own positioning now avoids
    this (see its docstring), but this check catches the general case --
    any tool or hand-edit that places a chart/table too close to text."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    warnings: list[dict[str, object]] = []
    for slide_index, slide in enumerate(prs.slides, start=1):
        text_shapes = [
            shape
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text.strip() and _shape_bbox(shape)
        ]
        blocking_shapes = [
            shape
            for shape in slide.shapes
            if shape.shape_type in (MSO_SHAPE_TYPE.CHART, MSO_SHAPE_TYPE.TABLE)
            and _shape_bbox(shape)
        ]
        for i, shape_a in enumerate(text_shapes):
            bbox_a = _shape_bbox(shape_a)
            assert bbox_a is not None  # filtered above
            for shape_b in text_shapes[i + 1 :]:
                bbox_b = _shape_bbox(shape_b)
                assert bbox_b is not None  # filtered above
                if _bbox_overlap_fraction(bbox_a, bbox_b) > _TEXT_OVERLAP_THRESHOLD:
                    warnings.append(
                        {
                            "slide": slide_index,
                            "text_a": shape_a.text_frame.text.strip()[:60],
                            "text_b": shape_b.text_frame.text.strip()[:60],
                        }
                    )
            for blocker in blocking_shapes:
                blocker_bbox = _shape_bbox(blocker)
                assert blocker_bbox is not None  # filtered above
                if _bbox_overlap_fraction(bbox_a, blocker_bbox) > _TEXT_OVERLAP_THRESHOLD:
                    warnings.append(
                        {
                            "slide": slide_index,
                            "text_a": shape_a.text_frame.text.strip()[:60],
                            "text_b": f"<{blocker.shape_type} shape>",
                        }
                    )
    return warnings


def _shapes_have_visual_element(shapes: Any) -> bool:
    from pptx.enum.dml import MSO_FILL_TYPE
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    visual_shape_types = {
        MSO_SHAPE_TYPE.PICTURE,
        MSO_SHAPE_TYPE.LINKED_PICTURE,
        MSO_SHAPE_TYPE.CHART,
        MSO_SHAPE_TYPE.TABLE,
        MSO_SHAPE_TYPE.GROUP,
        MSO_SHAPE_TYPE.FREEFORM,
        MSO_SHAPE_TYPE.DIAGRAM,
    }
    visual_fill_types = {
        MSO_FILL_TYPE.SOLID,
        MSO_FILL_TYPE.GRADIENT,
        MSO_FILL_TYPE.PATTERNED,
        MSO_FILL_TYPE.PICTURE,
        MSO_FILL_TYPE.TEXTURED,
    }
    for shape in shapes:
        if shape.shape_type in visual_shape_types:
            return True
        try:
            if shape.fill.type in visual_fill_types:
                return True
        except (AttributeError, TypeError, ValueError):
            continue
    return False


def _slide_has_visual_element(slide: Any) -> bool:
    """A slide "has a visual element" if it does directly, or inherits one
    from its own slide layout or that layout's slide master -- real,
    hand-authored PowerPoint templates commonly put decorative art
    (background graphics, accent bars) on the layout/master rather than
    copying it into every individual slide (that's the whole point of a
    layout/master: shared background art rendered under the slide's own
    content, never literally present in the slide's own shape tree). Only
    non-placeholder shapes count at the layout/master level -- a
    placeholder there is a text-style skeleton, not a visible graphic, so
    it can't satisfy this check on its own. Found live: coscribe's own
    bundled "velis" template (a real third-party design, not one of
    coscribe's python-pptx-built originals) draws its whole curtain-motif
    background as two GROUP shapes on the slide master, which the
    slide-only version of this check couldn't see at all -- a false
    positive, not an actual missing-visual defect."""
    if _shapes_have_visual_element(slide.shapes):
        return True
    layout = slide.slide_layout
    if _shapes_have_visual_element(shape for shape in layout.shapes if not shape.is_placeholder):
        return True
    return _shapes_have_visual_element(
        shape for shape in layout.slide_master.shapes if not shape.is_placeholder
    )


def _check_missing_visual_elements(prs: Any) -> list[int]:
    """1-based slide numbers with no picture/chart/table/colored shape at
    all -- just unfilled text on whatever the slide's own background is.
    A slide-level background color doesn't count (pptxgenjs sets that as
    `slide.background`, a property of the slide itself, not a shape), so a
    plain-colored "themed" slide with only unfilled text boxes still gets
    flagged -- this matches the actual reported defect directly: a deck
    can look designed from its background/typography alone while every
    slide underneath is still just a title and a bullet list."""
    return [
        index
        for index, slide in enumerate(prs.slides, start=1)
        if not _slide_has_visual_element(slide)
    ]


_LOW_CONTRAST_RATIO_SMALL_TEXT = 4.5
_LOW_CONTRAST_RATIO_LARGE_TEXT = 3.0
# WCAG 2.1's own definition of "large text" -- https://www.w3.org/TR/WCAG21/#dfn-large-scale
_LARGE_TEXT_MIN_PT = 18.0
_LARGE_TEXT_BOLD_MIN_PT = 14.0


def _relative_luminance(rgb: Any) -> float:
    """WCAG 2.1's relative-luminance formula -- the basis its own
    contrast-ratio formula is built on.
    https://www.w3.org/TR/WCAG21/#dfn-relative-luminance"""

    def channel(value: int) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def _contrast_ratio(rgb_a: Any, rgb_b: Any) -> float:
    luminance_a, luminance_b = _relative_luminance(rgb_a), _relative_luminance(rgb_b)
    lighter, darker = max(luminance_a, luminance_b), min(luminance_a, luminance_b)
    return (lighter + 0.05) / (darker + 0.05)


def _solid_rgb(fill: Any) -> Any | None:
    """`fill`'s own explicit solid RGB color, or None if it isn't one --
    no fill at all, a gradient/picture/pattern fill, or a theme/scheme
    color. A scheme color's actual rendered value lives in the theme XML,
    which this deliberately doesn't chase -- a shape using one is skipped
    by _check_low_contrast below rather than guessed at."""
    from pptx.enum.dml import MSO_COLOR_TYPE, MSO_FILL_TYPE

    try:
        if fill.type != MSO_FILL_TYPE.SOLID:
            return None
        color = fill.fore_color
        return color.rgb if color.type == MSO_COLOR_TYPE.RGB else None
    except (AttributeError, TypeError, ValueError):
        return None


def _slide_background_rgb(slide: Any) -> Any | None:
    """The nearest explicit solid background color for `slide` -- its
    own, else its layout's, else its master's (PowerPoint's own
    inheritance order for an unset background) -- or None if none of the
    three sets one explicitly (an inherited theme color, a gradient/
    picture background, or genuinely no background set anywhere)."""
    rgb = _solid_rgb(slide.background.fill)
    if rgb is not None:
        return rgb
    layout = slide.slide_layout
    rgb = _solid_rgb(layout.background.fill)
    if rgb is not None:
        return rgb
    return _solid_rgb(layout.slide_master.background.fill)


def _check_low_contrast(prs: Any) -> list[dict[str, object]]:
    """Flags text whose own color and its resolved background fall below
    WCAG 2.1's minimum contrast ratio (4.5:1 for ordinary text, 3.0:1 for
    "large" text -- WCAG's own >=18pt, or >=14pt bold, definition) -- a
    purely computed check, no rendering needed, so (like
    text_overlap_warnings/slides_missing_visual_elements above) it always
    runs. Prompted by reading a much larger third-party PPTX-generation
    skill's (github.com/hugohe3/ppt-master) own visual-review rubric,
    which flags the same defect from a rendered screenshot; coscribe
    doesn't need to render to check this, since the exact colors involved
    are already known from the object model the moment a run's own font
    color and its resolved background are both an explicit solid RGB.

    Deliberately conservative, not exhaustive -- only checks a run whose
    OWN font color is an explicit RGB (never a theme/scheme color, which
    would need the theme XML to resolve to its real rendered value)
    against a background resolved the same way (the shape's own solid
    fill, else the slide's/layout's/master's -- see _slide_background_rgb).
    A run or background that can't resolve this way -- a gradient/picture
    fill, a scheme color, text sitting on top of a photo or another shape
    purely by z-order -- is silently skipped rather than guessed at; the
    existing scrim guidance (PPTX Slides skill) plus a human/review_work
    look already cover exactly that harder, pixel-level case. A run with
    no explicit `font.size` is treated as small text (the stricter 4.5:1
    threshold) rather than assumed large, so an unset size can't hide a
    real defect."""
    from pptx.enum.dml import MSO_COLOR_TYPE

    warnings: list[dict[str, object]] = []
    for slide_index, slide in enumerate(prs.slides, start=1):
        slide_bg = _slide_background_rgb(slide)
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            bg = _solid_rgb(shape.fill) or slide_bg
            if bg is None:
                continue
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    text = run.text.strip()
                    if not text:
                        continue
                    color = run.font.color
                    if color.type != MSO_COLOR_TYPE.RGB:
                        continue
                    fg = color.rgb
                    size_pt = run.font.size.pt if run.font.size is not None else None
                    bold = bool(run.font.bold)
                    is_large = size_pt is not None and (
                        size_pt >= _LARGE_TEXT_MIN_PT
                        or (bold and size_pt >= _LARGE_TEXT_BOLD_MIN_PT)
                    )
                    threshold = (
                        _LOW_CONTRAST_RATIO_LARGE_TEXT
                        if is_large
                        else _LOW_CONTRAST_RATIO_SMALL_TEXT
                    )
                    ratio = _contrast_ratio(fg, bg)
                    if ratio < threshold:
                        warnings.append(
                            {
                                "slide": slide_index,
                                "text": text[:60],
                                "text_color": str(fg),
                                "background_color": str(bg),
                                "contrast_ratio": round(ratio, 2),
                                "required_ratio": threshold,
                            }
                        )
    return warnings


def _emu_to_inches(value: int | None) -> float | None:
    return round(value / 914400, 2) if value is not None else None


# SmartArt: python-pptx has no first-class detection or reading for it at
# all -- `GraphicFrame.shape_type` simply returns None for this case (its
# own docstring says so explicitly: "This value is None when none of
# these four types apply, for example when the shape contains
# SmartArt"). There's also no way to *generate* real SmartArt from
# outside PowerPoint -- the layout algorithm lives in the client itself,
# not the file format (confirmed: Apache POI, a mature OOXML library,
# only ever *reads* SmartArt via a `@Beta`-flagged best-effort class,
# never writes it). What's buildable and genuinely useful instead:
# detecting that an *uploaded* file's shape is SmartArt and extracting
# its nodes' own text, so the model can see what a diagram actually said
# before deciding how to rebuild the slide with this file's other tools.
#
# The graphicData uri and <dgm:relIds>'s `dm` relationship-id below are
# cross-checked against Apache POI's real, working
# `XSLFDiagram.DRAWINGML_DIAGRAM_URI` constant and its
# `relIds.getDm()`-based data-model-part resolution -- not guessed. The
# <dgm:pt>/<dgm:t> text-body structure is cross-checked against
# LibreOffice's real oox filter source (`PtContext::onCreateContext`
# routes `DGM_TOKEN(t)` to the exact same `TextBodyContext` class used
# for every other DrawingML text body, and doesn't filter by a point's
# own `type` attribute when deciding whether to parse its text) -- both
# real, independent, working OOXML-consuming implementations, not just
# the written spec.
_SMARTART_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"


def _is_smartart(shape: Any) -> bool:
    """True if `shape` is a <p:graphicFrame> containing real SmartArt."""
    from pptx.shapes.graphfrm import GraphicFrame

    if not isinstance(shape, GraphicFrame):
        return False
    graphic_data_uri: str | None = shape._graphicFrame.graphicData_uri  # noqa: SLF001
    return graphic_data_uri == _SMARTART_NS


def _smartart_text(shape: Any) -> list[str]:
    """Every SmartArt node's own text, one entry per <dgm:pt> that has
    non-empty text (skipping structural/transition points, which in
    practice never carry a <dgm:t> child at all -- see this section's
    own comment on LibreOffice's real parser). Returns [] if `shape`
    isn't SmartArt or its data-model part can't be resolved (should not
    happen for a real, schema-valid file, but this is a best-effort
    read, not a requirement)."""
    from pptx.oxml.ns import qn

    if not _is_smartart(shape):
        return []
    graphic_data = shape._graphicFrame.graphic.graphicData  # noqa: SLF001
    rel_ids = graphic_data.find(f"{{{_SMARTART_NS}}}relIds")
    if rel_ids is None:
        return []
    dm_rId = rel_ids.get(qn("r:dm"))
    if dm_rId is None:
        return []
    try:
        data_part = shape.part.related_part(dm_rId)
    except KeyError:
        return []

    from lxml import etree

    root = etree.fromstring(data_part.blob)
    texts: list[str] = []
    for pt in root.iter(f"{{{_SMARTART_NS}}}pt"):
        t_element = pt.find(f"{{{_SMARTART_NS}}}t")
        if t_element is None:
            continue
        # Runs within one paragraph concatenate directly (no separator,
        # same as python-pptx's own text_frame.text within a paragraph);
        # separate paragraphs within one point get a space between them.
        paragraphs = [
            "".join(run.text or "" for run in paragraph.iter(qn("a:t")))
            for paragraph in t_element.iter(qn("a:p"))
        ]
        joined = " ".join(p for p in paragraphs if p).strip()
        if joined:
            texts.append(joined)
    return texts


def _describe_fill_color(color: Any) -> str | None:
    """One `ColorFormat`'s own value as `"#RRGGBB"`/`"theme:ACCENT_1"` --
    the shared per-color logic `_describe_shape_fill` uses for both a
    solid fill's single color and a gradient fill's two-or-more stops."""
    from pptx.enum.dml import MSO_COLOR_TYPE

    if color.type == MSO_COLOR_TYPE.SCHEME:
        return f"theme:{color.theme_color.name}"
    if color.type == MSO_COLOR_TYPE.RGB:
        return f"#{color.rgb}"
    return None


def _describe_shape_fill(shape: Any) -> str | dict[str, object] | None:
    """A shape's fill: a solid fill's color as `"#RRGGBB"` (literal) or
    `"theme:ACCENT_1"` (a `theme_color` reference); a gradient fill (see
    `edit_pptx_shape`'s `fill_color_2`) as `{"type": "gradient", "colors":
    [...], "angle": <degrees or None>}`, one entry in `colors` per stop,
    in gradient order -- lets a model confirm what it just set via
    `list_pptx_shapes` the same way it can for a solid color. `None` when
    the shape has no fill concept at all (a table/chart `GraphicFrame`,
    which has no `.fill` attribute), no fill set, or a picture/pattern
    fill (out of scope -- `list_pptx_shapes` only needs enough to target
    or verify a plain accent-colored/gradient shape, not describe every
    fill type OOXML supports)."""
    from pptx.enum.dml import MSO_FILL

    fill = getattr(shape, "fill", None)
    if fill is None:
        return None
    try:
        fill_type = fill.type
    except (AttributeError, TypeError):
        return None
    if fill_type == MSO_FILL.GRADIENT:
        angle: float | None
        try:
            angle = fill.gradient_angle
        except ValueError:
            angle = None  # non-linear (e.g. radial) -- angle doesn't apply
        except TypeError:
            # Real python-pptx bug, hit live while testing this: a fresh
            # `fill.gradient()`'s <a:lin> element has no `ang` attribute
            # at all (angle "inherited"), and gradient_angle's own getter
            # does `360.0 - None` in that case instead of treating it the
            # same as no <a:lin> at all. edit_pptx_shape works around this
            # by always setting an explicit angle after calling
            # `.gradient()` (see below), but a gradient from an externally
            # -authored file could still hit this, so this is defensive
            # here too, not just relying on that workaround.
            angle = None
        return {
            "type": "gradient",
            "colors": [_describe_fill_color(stop.color) for stop in fill.gradient_stops],
            "angle": angle,
        }
    if fill_type != MSO_FILL.SOLID:
        return None
    return _describe_fill_color(fill.fore_color)


def _describe_shape(index: int, shape: Any) -> dict[str, object]:
    """One `list_pptx_shapes` entry -- enough for a text-only model to
    pick a target (`shape_index`) for `edit_pptx_shape`/`replace_pptx_image`/
    the table-cell-editing tools without guessing: type, a short text
    preview, position/size in inches (matching `edit_pptx_shape`'s own
    `*_in` parameters), rotation, fill color if it's a plain solid fill,
    whole-shape hyperlink address (matching `add_pptx_hyperlink`'s own
    whole-shape mode) if one is set -- `"#slide-N"` for an internal slide
    jump, same syntax `add_pptx_hyperlink`'s own `url` accepts, not the
    raw internal relationship target -- and whether it's real SmartArt
    (`is_smartart`) -- if so, `smartart_text` lists every node's own
    text, since neither python-pptx nor this file can generate or edit
    real SmartArt (the layout algorithm lives in PowerPoint itself, not
    the file format); this is what lets a model see what a diagram
    actually said before deciding how to rebuild the slide with this
    file's other tools."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    hyperlink: str | None
    try:
        jump_target = shape.click_action.target_slide
    except ValueError:
        # A real, if unusual, PowerPoint construct this tool never
        # writes itself but must not crash reading back: a "next
        # slide"/"previous slide" navigation action on the last/first
        # slide, where python-pptx's own target_slide resolution raises
        # rather than returning None.
        jump_target = None
    if jump_target is not None:
        slides = shape.part.package.presentation_part.presentation.slides
        # The same "#slide-N" syntax add_pptx_hyperlink's own `url`
        # accepts -- reading this field back and feeding it straight into
        # another add_pptx_hyperlink call must round-trip, not show the
        # raw internal relationship target (e.g. "slide2.xml", which
        # isn't even guaranteed to match display order after slides are
        # reordered/duplicated).
        hyperlink = f"#slide-{slides.index(jump_target) + 1}"
    else:
        hyperlink = shape.click_action.hyperlink.address

    text_preview: str | None = None
    if shape.has_text_frame:
        text = shape.text_frame.text.strip()
        if text:
            text_preview = text if len(text) <= 80 else text[:77] + "..."
    is_smartart = _is_smartart(shape)
    info: dict[str, object] = {
        "index": index,
        "name": shape.name,
        "shape_type": str(shape.shape_type) if shape.shape_type is not None else None,
        "left_in": _emu_to_inches(shape.left),
        "top_in": _emu_to_inches(shape.top),
        "width_in": _emu_to_inches(shape.width),
        "height_in": _emu_to_inches(shape.height),
        "rotation": shape.rotation,
        "is_placeholder": shape.is_placeholder,
        "is_table": shape.has_table,
        "is_picture": shape.shape_type == MSO_SHAPE_TYPE.PICTURE,
        "is_smartart": is_smartart,
        "smartart_text": _smartart_text(shape) if is_smartart else None,
        "text_preview": text_preview,
        "fill": _describe_shape_fill(shape),
        "hyperlink": hyperlink,
    }
    if shape.has_table:
        table = shape.table
        info["table_dimensions"] = {"rows": len(table.rows), "cols": len(table.columns)}
    return info


def _get_shape_at_index(target_slide: Any, slide_number: int, shape_index: int) -> Any:
    """Shared bounds check for every tool that targets one shape by
    index -- raises a clear, actionable error (naming list_pptx_shapes)
    rather than an opaque IndexError."""
    shapes = list(target_slide.shapes)
    if not 0 <= shape_index < len(shapes):
        raise ValueError(
            f"shape_index {shape_index} out of range: slide {slide_number} has "
            f"{len(shapes)} shape(s) -- call list_pptx_shapes first."
        )
    return shapes[shape_index]


def _get_table_shape(target_slide: Any, slide_number: int, shape_index: int) -> Any:
    """Shared bounds-and-kind validation for edit_pptx_table_cell/
    merge_pptx_table_cells -- raises a clear, actionable error (naming
    list_pptx_shapes) rather than an opaque AttributeError if shape_index
    is out of range or doesn't point at a table."""
    shape = _get_shape_at_index(target_slide, slide_number, shape_index)
    if not shape.has_table:
        raise ValueError(
            f"Shape {shape_index} on slide {slide_number} ({shape.shape_type}) isn't a "
            f"table -- call list_pptx_shapes first to find the right shape_index "
            f"(table shapes have is_table: true)."
        )
    return shape


def _get_picture_shape(target_slide: Any, slide_number: int, shape_index: int) -> Any:
    """Shared bounds-and-kind validation for replace_pptx_image/
    recolor_pptx_icon/crop_pptx_image -- raises a clear, actionable
    error if shape_index doesn't point at a picture (a `<p:pic>` shape,
    python-pptx's `MSO_SHAPE_TYPE.PICTURE`)."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    shape = _get_shape_at_index(target_slide, slide_number, shape_index)
    if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
        raise ValueError(
            f"Shape {shape_index} on slide {slide_number} ({shape.shape_type}) isn't a "
            f"picture -- call list_pptx_shapes first to find the right shape_index."
        )
    return shape


# add_pptx_icon's bundled icon set: a curated ~50-icon subset of Lucide
# (https://lucide.dev, ISC license -- see builtin_icons/lucide/LICENSE),
# rasterized to black-on-transparent PNG at build time by
# scripts/build_pptx_icons.py (not fetched or rendered at runtime -- no
# SVG-rasterization library is a runtime dependency of this package).
# `_recolor_icon` below reads a bundled PNG and swaps solid black for the
# caller's requested color at call time, keeping the alpha channel (so
# anti-aliased edges stay clean) -- this is why the bundled assets are
# plain black, not pre-colored per icon.
_ICONS_DIR = Path(__file__).resolve().parent.parent / "builtin_icons" / "lucide"
_ICON_NAMES = frozenset(p.stem for p in _ICONS_DIR.glob("*.png"))


def _recolor_image(image: Any, color_hex: str) -> Any:
    """Returns a new PIL Image: every pixel of `image` recolored to
    `color_hex`, its own alpha channel preserved untouched -- works on
    any RGBA-convertible image with meaningful transparency, not just a
    bundled icon, since only the alpha channel (the silhouette shape)
    is read, never the source's existing color. This is what makes
    recolor_pptx_icon possible: an icon already placed on a slide can
    be recolored starting from its own current pixels, without needing
    to know which bundled icon name it originally was (or whether it
    came from add_pptx_icon at all)."""
    from PIL import Image

    red, green, blue = (int(color_hex[i : i + 2], 16) for i in (0, 2, 4))
    recolored = Image.new("RGBA", image.size, (red, green, blue, 255))
    recolored.putalpha(image.convert("RGBA").getchannel("A"))
    return recolored


def _recolor_icon(icon_path: Path, color_hex: str) -> Any:
    """Returns a PIL Image: the bundled black-on-transparent icon PNG
    with every non-transparent pixel recolored to `color_hex`, alpha
    channel (anti-aliased edges) preserved untouched. Uses PIL's own
    `putalpha` rather than a per-pixel Python loop over all 256x256
    pixels -- verified both produce an identical result, this is just
    the fast path."""
    from PIL import Image

    return _recolor_image(Image.open(icon_path), color_hex)


# The relationships namespace every r:id/r:embed/r:link attribute lives
# in, regardless of which element or local attribute name carries it --
# used by _duplicate_slide_part to blanket-remap every such attribute in
# one pass rather than enumerating every possible tag (a:blip r:embed,
# a:hlinkClick r:id, c:chart r:id, a:videoFile r:link, ...).
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _duplicate_slide_part(prs: PresentationType, source_slide: Any) -> str:
    """Deep-copies `source_slide`'s own XML part into a new slide part in
    the same package. Returns the new part's relationship id (from the
    presentation part) -- not yet inserted into `<p:sldIdLst>`, the
    caller positions it there.

    Every relationship the source slide has -- its slideLayout, every
    image/chart/hyperlink -- is re-added to the new part pointing at the
    SAME target part, not a deep copy of the target: valid OOXML (a part
    can be related-to by more than one other part), and safe here
    specifically because nothing in this module ever mutates a shared
    target part in place -- `replace_pptx_image`, the only tool that
    changes what a blip points at, always adds a brand-new image part
    and repoints one shape's own blip, never edits an existing image
    part's bytes in place. Every r:id-bearing attribute in the copied
    XML (`<a:blip r:embed>`, `<a:hlinkClick r:id>`, ...) is remapped
    whenever the newly assigned relationship id differs from the
    source's own -- verified live this isn't guaranteed to match (a
    synthetic forced-offset test confirmed the remap still resolves
    every reference correctly, not just in the common case where the
    ids happen to line up).

    Speaker notes are NOT carried over (`notesSlide` relationships are
    skipped) -- a deliberate v1 scope cut, not an oversight: correctly
    duplicating a notes part needs this same clone-plus-remap machinery
    applied recursively to it, and nothing has asked for duplicated
    notes yet. A chart on a duplicated slide will share its
    chart-plus-embedded-workbook part with the original (charts aren't
    deep-copied either) -- a real caveat for a future chart-data-editing
    tool to revisit, not a concern for anything that exists today."""
    from copy import deepcopy

    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.parts.slide import SlidePart

    presentation_part = prs.part
    package = presentation_part.package
    source_part = source_slide.part

    partname = presentation_part._next_slide_partname  # noqa: SLF001
    new_element = deepcopy(source_part._element)  # noqa: SLF001
    new_part = SlidePart(partname, source_part.content_type, package, new_element)

    rid_remap: dict[str, str] = {}
    for old_rid, rel in source_part.rels.items():
        if rel.reltype == RT.NOTES_SLIDE:
            continue
        if rel.is_external:
            new_rid = new_part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            new_rid = new_part.relate_to(rel.target_part, rel.reltype)
        if new_rid != old_rid:
            rid_remap[old_rid] = new_rid

    if rid_remap:
        for element in new_element.iter():
            for attr_name, attr_value in list(element.attrib.items()):
                if attr_name.startswith(f"{{{_R_NS}}}") and attr_value in rid_remap:
                    element.set(attr_name, rid_remap[attr_value])

    return presentation_part.relate_to(new_part, RT.SLIDE)


def _adjust_template_content_slides(
    prs: PresentationType, slide_roles: list[str], target_count: int
) -> list[str]:
    """Grows or shrinks a loaded template's "content"-role slides in
    place to reach `target_count` slides total -- duplicating or
    deleting only "content"-role slides, never "title"/"closing" -- so
    fill_pptx_template's slide count adapts to however many chunks the
    caller actually gave it, instead of the previous hard requirement
    that the chunk count exactly equal the template's fixed slide count.
    Returns the adjusted role list (same length/order as `prs.slides`
    after this call).

    Reuses the exact same `<p:sldIdLst>`/`_duplicate_slide_part`
    machinery `delete_pptx_slide`/`duplicate_pptx_slide` already use and
    this session already verified live (forced-rId-offset test, etc.) --
    this is the same operation, just applied from inside
    fill_pptx_template instead of as a standalone tool call. Growing
    duplicates the *last* content slide repeatedly (each duplicate is an
    identical clone of the same design, so which specific content slide
    is duplicated from doesn't affect the result); shrinking removes
    trailing content slides, keeping the earlier one(s)."""
    from pptx.oxml.ns import qn

    content_positions = [i for i, role in enumerate(slide_roles) if role == "content"]
    n_fixed = len(slide_roles) - len(content_positions)
    n_content_needed = target_count - n_fixed
    if n_content_needed < 0 or (n_content_needed > 0 and not content_positions):
        if content_positions:
            detail = f"{n_fixed} fixed slide(s) plus at least one content slide"
            min_valid = n_fixed + 1
        else:
            detail = f"{n_fixed} fixed slide(s), no repeatable content slide"
            min_valid = n_fixed
        raise ValueError(
            f"this template needs at least {min_valid} chunk(s) total "
            f"({detail}) -- got {target_count}."
        )
    n_content_current = len(content_positions)
    sld_id_lst = prs.slides._sldIdLst  # noqa: SLF001

    if n_content_needed > n_content_current:
        insert_at = content_positions[-1] + 1 if content_positions else 0
        last_index = content_positions[-1] if content_positions else -1
        for _ in range(n_content_needed - n_content_current):
            source_slide = prs.slides[last_index]
            new_rid = _duplicate_slide_part(prs, source_slide)
            new_sld_id = sld_id_lst.add_sldId(new_rid)
            sld_id_lst.remove(new_sld_id)
            last_index += 1
            sld_id_lst.insert(last_index, new_sld_id)
        return (
            slide_roles[:insert_at]
            + ["content"] * (n_content_needed - n_content_current)
            + slide_roles[insert_at:]
        )

    if n_content_needed < n_content_current:
        to_remove = set(content_positions[n_content_needed:])
        sld_id_elements = list(sld_id_lst)
        for index in sorted(to_remove, reverse=True):
            r_id = sld_id_elements[index].get(qn("r:id"))
            prs.part.drop_rel(r_id)
            sld_id_lst.remove(sld_id_elements[index])
        return [role for i, role in enumerate(slide_roles) if i not in to_remove]

    return slide_roles


# DrawingML shape effects (drop shadow / glow / soft edge) -- CT_EffectList,
# a <p:spPr> child shared by every shape kind (autoshape/picture/connector),
# real schema in _ooxml_schemas/transitional/dml-main.xsd. python-pptx has
# zero public API for this (its own ShadowFormat can only *suppress* an
# *inherited* theme shadow -- shape.shadow.inherit = False, used by
# add_pptx_scrim above -- never create/customize one), so every effect
# element below is hand-built. CT_EffectList's real child order is fixed
# (blur, fillOverlay, glow, innerShdw, outerShdw, prstShdw, reflection,
# softEdge), each optional and maxOccurs=1 -- _apply_shape_effect below
# always inserts a new effect at the correct position relative to whichever
# other supported effects (if any) are already present, rather than
# assuming effectLst is either empty or already holds only this tool's own
# children. Verified live in a REPL: python-pptx's own oxml class for
# <p:spPr> (pptx.oxml.shapes.shared.CT_ShapeProperties) already declares
# effectLst = ZeroOrOne("a:effectLst", successors=...), so
# spPr.get_or_add_effectLst() alone (no hand-XML needed) already inserts
# <a:effectLst> at the schema-correct position within <p:spPr> -- confirmed
# for both autoshapes and pictures.
_EFFECT_LIST_CHILD_LOCALNAMES = (
    "blur",
    "fillOverlay",
    "glow",
    "innerShdw",
    "outerShdw",
    "prstShdw",
    "reflection",
    "softEdge",
)
_SHAPE_EFFECT_LOCALNAMES = {"glow": "glow", "shadow": "outerShdw", "soft_edge": "softEdge"}
_SHAPE_EFFECT_TYPES = frozenset({"shadow", "glow", "soft_edge", "none"})


def _shape_effect_color_element(color: str, opacity: float) -> Any:
    """<a:srgbClr val="RRGGBB"><a:alpha val="NNNNN"/></a:srgbClr> -- the same
    EG_ColorChoice pattern add_pptx_scrim already hand-builds for its own
    <a:solidFill>, reused here for <a:glow>/<a:outerShdw>."""
    from pptx.oxml.xmlchemy import OxmlElement

    srgb_clr = OxmlElement("a:srgbClr")
    srgb_clr.set("val", color)
    alpha = OxmlElement("a:alpha")
    alpha.set("val", str(round(opacity * 100000)))
    srgb_clr.append(alpha)
    return srgb_clr


def _build_shape_effect_element(
    effect: str, color: str, opacity: float, size_pt: float, distance_pt: float, direction: float
) -> Any:
    from pptx.oxml.xmlchemy import OxmlElement
    from pptx.util import Pt

    if effect == "glow":
        glow = OxmlElement("a:glow")
        glow.set("rad", str(Pt(size_pt)))
        glow.append(_shape_effect_color_element(color, opacity))
        return glow
    if effect == "soft_edge":
        soft_edge = OxmlElement("a:softEdge")
        soft_edge.set("rad", str(Pt(size_pt)))
        return soft_edge
    # "shadow" -- CT_OuterShadowEffect. dir is in 60,000ths of a degree,
    # clockwise from 3 o'clock -- the same OOXML angle convention shape
    # rotation (xfrm@rot) and every other DrawingML angle attribute use.
    outer_shdw = OxmlElement("a:outerShdw")
    outer_shdw.set("blurRad", str(Pt(size_pt)))
    outer_shdw.set("dist", str(Pt(distance_pt)))
    outer_shdw.set("dir", str(round(direction * 60000) % 21600000))
    outer_shdw.set("rotWithShape", "0")
    outer_shdw.append(_shape_effect_color_element(color, opacity))
    return outer_shdw


def _apply_shape_effect(
    shape: Any,
    effect: str,
    color: str,
    opacity: float,
    size_pt: float,
    distance_pt: float,
    direction: float,
) -> None:
    """Inserts (or, for effect="none", removes) one effect element inside
    this shape's <a:effectLst>, at the position CT_EffectList's own fixed
    child order requires relative to whichever other supported effect
    type(s) (if any) are already present. Re-applying the same `effect`
    replaces its own existing element in place (find-and-remove first --
    the schema caps each child tag at one), rather than raising or
    stacking an invalid duplicate; applying a *different* `effect` leaves
    any other effect type already on this shape untouched, so effects
    compose across separate calls (e.g. shadow, then soft_edge, leaves
    both)."""
    from pptx.oxml.ns import qn

    effect_lst = shape._element.spPr.get_or_add_effectLst()  # noqa: SLF001

    if effect == "none":
        for local_name in _SHAPE_EFFECT_LOCALNAMES.values():
            existing = effect_lst.find(qn(f"a:{local_name}"))
            if existing is not None:
                effect_lst.remove(existing)
        return

    target_local = _SHAPE_EFFECT_LOCALNAMES[effect]
    target_rank = _EFFECT_LIST_CHILD_LOCALNAMES.index(target_local)
    existing = effect_lst.find(qn(f"a:{target_local}"))
    if existing is not None:
        effect_lst.remove(existing)

    local_by_tag = {qn(f"a:{name}"): name for name in _EFFECT_LIST_CHILD_LOCALNAMES}
    insert_at = 0
    for child in effect_lst:
        child_local = local_by_tag.get(child.tag)
        if child_local is None or _EFFECT_LIST_CHILD_LOCALNAMES.index(child_local) >= target_rank:
            break
        insert_at += 1

    new_element = _build_shape_effect_element(
        effect, color, opacity, size_pt, distance_pt, direction
    )
    effect_lst.insert(insert_at, new_element)


class PresentationToolkit:
    def __init__(
        self,
        root: str | Path,
        *,
        state_dir: str | Path | None = None,
        custom_templates_dir: str | Path | None = None,
        extra_readable: Sequence[str | Path] = (),
        extra_writable: Sequence[str | Path] = (),
    ) -> None:
        self._scope = WorkspaceScope(
            root, extra_readable=extra_readable, extra_writable=extra_writable
        )
        self._state_dir = state_dir
        self._custom_templates_dir = custom_templates_dir

    def _check_readable(self, path: str) -> Path:
        file_path = self._scope.resolve(path)
        if not file_path.exists():
            raise ValueError(f"File does not exist: {path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        return file_path

    def _check_writable(self, path: str, overwrite: bool) -> Path:
        file_path = self._scope.resolve(path, write=True)
        if file_path.exists() and file_path.is_dir():
            raise ValueError(f"Path is a directory: {path}")
        if file_path.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    @locked_by_path
    def write_pptx(
        self,
        path: str,
        content: str,
        overwrite: bool = True,
        template_path: str = "",
        theme: str = "",
    ) -> dict[str, object]:
        from pptx import Presentation
        from pptx.util import Emu

        theme_tokens = _parse_theme_tokens(theme)
        file_path = self._check_writable(path, overwrite)
        if template_path:
            template_file = self._check_readable(template_path)
            prs = Presentation(str(template_file))
            required_layouts = max(_TITLE_AND_CONTENT_LAYOUT, _TITLE_ONLY_LAYOUT)
            if len(prs.slide_layouts) <= required_layouts:
                raise ValueError(
                    f"Template {template_path!r} only has {len(prs.slide_layouts)} slide "
                    f"layout(s); write_pptx needs at least {required_layouts + 1}."
                )
            _clear_slides(prs)
        else:
            prs = Presentation()
            original_width = prs.slide_width
            prs.slide_width = Emu(_WIDESCREEN_WIDTH_EMU)
            _widen_stock_layouts(prs, original_width)
            # Only the freshly-created default deck, not a user-supplied
            # template_path -- silently rewriting fonts in someone's own
            # uploaded/branded template would be an unwanted side effect,
            # see this function's `theme` docstring.
            _apply_cjk_font_fix(prs)
        if theme_tokens:
            _apply_theme(prs, theme_tokens)
        for chunk in _slide_chunks(content):
            layout_id, accent, remaining_chunk = _parse_layout_directive(chunk)

            if layout_id == "two-column":
                if len(prs.slide_layouts) <= _TWO_CONTENT_LAYOUT:
                    raise ValueError(
                        f"Template only has {len(prs.slide_layouts)} slide layout(s); "
                        f"layout: two-column needs at least {_TWO_CONTENT_LAYOUT + 1}."
                    )
                left_text, right_text = _split_two_column(remaining_chunk)
                left_blocks = parse_blocks(left_text)
                right_blocks = parse_blocks(right_text)
                left_headings = [b for b in left_blocks if b.kind == "heading"]
                title = left_headings[0].text if left_headings else ""
                left_body = [b for b in left_blocks if b.kind != "heading"]
                if any(b.kind == "table" for b in left_body) or any(
                    b.kind == "table" for b in right_blocks
                ):
                    raise ValueError(
                        "layout: two-column doesn't support tables -- use "
                        "bullets/paragraphs on each side."
                    )
                if any(b.kind == "heading" for b in right_blocks):
                    raise ValueError(
                        "layout: two-column's title must come before the '>>>' marker."
                    )
                _add_two_column_slide(prs, title, left_body, right_blocks)
                continue

            if layout_id == "svg":
                # Unlike the other three prefab layouts, the rest of the chunk
                # is raw SVG markup, not this module's shared markdown grammar
                # -- it must not go through parse_blocks at all.
                add_svg_slide(prs, remaining_chunk)
                continue

            blocks = parse_blocks(remaining_chunk)
            headings = [block for block in blocks if block.kind == "heading"]
            title = headings[0].text if headings else ""
            table_blocks = [block for block in blocks if block.kind == "table"]
            other_blocks = [
                block for block in blocks if block.kind not in ("heading", "table")
            ]

            if layout_id == "icon-list":
                if table_blocks:
                    raise ValueError(
                        "layout: icon-list doesn't support tables -- use bullets only."
                    )
                items = _icon_list_items(other_blocks)
                _add_icon_list_slide(prs, title, items, accent)
                continue
            if layout_id == "stat-callout":
                if other_blocks or len(table_blocks) != 1:
                    raise ValueError(
                        "layout: stat-callout needs exactly one pipe-table of "
                        "stat/label rows and no other content."
                    )
                stats = _stats_from_table(table_blocks[0].rows)
                _add_stat_callout_slide(prs, title, stats, accent)
                continue

            if table_blocks and other_blocks:
                raise ValueError(
                    "A slide can have a table or bullets/paragraphs, not both -- "
                    "split it across two slides."
                )
            if table_blocks:
                self._require_single_table(table_blocks)
                _add_table_slide(prs, title, table_blocks[0].rows)
            else:
                _add_bullet_slide(prs, title, blocks)
        prs.save(str(file_path))

        overflow_warnings: list[dict[str, object]] = []
        qa_skipped_reason: str | None = None
        pdf_path = _render_to_pdf(file_path)
        if pdf_path is None:
            qa_skipped_reason = "LibreOffice (soffice) not found or conversion failed"
        else:
            try:
                overflow_warnings = _check_overflow(pdf_path)
            finally:
                shutil.rmtree(pdf_path.parent, ignore_errors=True)

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)

        return {
            "path": self._scope.relative(file_path),
            "slide_count": len(prs.slides),
            "bytes_written": file_path.stat().st_size,
            "overflow_warnings": overflow_warnings,
            "placeholder_warnings": _leftover_placeholder_warnings(prs),
            "qa_skipped_reason": qa_skipped_reason,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def fill_pptx_template(
        self,
        path: str,
        template_id: str,
        content: str,
        overwrite: bool = True,
    ) -> dict[str, object]:
        """Fill content into one of coscribe's own bundled starter
        templates, keeping every one of its existing slides' own designed
        visual content (backgrounds, decorative shapes, images) exactly as
        authored -- unlike write_pptx's own template_path, which clears a
        template's slides before rebuilding from scratch. This tool only
        ever writes text into a slide's own pre-existing TITLE/BODY-type
        placeholder; it never repositions a shape.

        `content`'s `---`-separated chunk count no longer has to exactly
        equal the template's fixed slide count: the template's
        repeatable "content"-role slide(s) (every bundled template has
        exactly one design used twice -- see its own description) are
        duplicated or trimmed to match however many chunks you actually
        give, so a template scales to real content length instead of
        forcing you to trim to fit or fall back to write_pptx. The
        template's non-repeatable slides (title, closing) still need
        exactly one matching chunk each, in their fixed position (title
        first if the template has one, closing last if it has one) --
        each chunk is title + bullets/paragraphs only (no `layout:`
        directives, no tables -- write_pptx handles both).
        """
        from pptx import Presentation

        all_templates = _load_builtin_templates()
        if self._custom_templates_dir is not None:
            all_templates += _load_pptx_templates(self._custom_templates_dir)
        templates_by_id = {t.id: t for t in all_templates}
        template = templates_by_id.get(template_id)
        if template is None:
            raise ValueError(
                f"Unknown template {template_id!r}. Available: {sorted(templates_by_id)}"
            )
        file_path = self._check_writable(path, overwrite)
        prs = Presentation(str(template.path))
        # Coscribe's own bundled templates, not a user-supplied file --
        # safe (and worth doing unconditionally) to fix their empty
        # East-Asian typeface the same way write_pptx's own default deck
        # does. See _CJK_FALLBACK_TYPEFACE's comment.
        _apply_cjk_font_fix(prs)
        chunks = _slide_chunks(content)
        _adjust_template_content_slides(prs, template.slide_roles, len(chunks))
        slides = list(prs.slides)
        for index, (slide, chunk) in enumerate(zip(slides, chunks, strict=True), start=1):
            layout_id, _accent, remaining = _parse_layout_directive(chunk)
            if layout_id:
                raise ValueError(
                    f"fill_pptx_template doesn't support 'layout:' directives "
                    f"(found {layout_id!r} on slide chunk {index}) -- layout: "
                    f"positions new shapes on a freshly-added slide, and this "
                    f"tool fills an existing template slide's existing "
                    f"placeholders instead. Use write_pptx for layout: <id> "
                    f"slides."
                )
            blocks = parse_blocks(remaining)
            if any(block.kind == "table" for block in blocks):
                raise ValueError(
                    f"fill_pptx_template doesn't support tables yet (slide "
                    f"chunk {index} has one) -- use write_pptx for a table "
                    f"slide."
                )
            headings = [block for block in blocks if block.kind == "heading"]
            title = headings[0].text if headings else ""
            body_blocks = [block for block in blocks if block.kind != "heading"]
            if title:
                if slide.shapes.title is None:
                    raise ValueError(
                        f"Slide {index} of template {template_id!r} has no title "
                        f"placeholder to receive chunk {index}'s heading "
                        f"{title!r} -- remove the heading or use a different "
                        f"template."
                    )
                _set_title(slide, title)
            if body_blocks:
                body_placeholder = _find_body_placeholder(slide)
                if body_placeholder is None:
                    raise ValueError(
                        f"Slide {index} of template {template_id!r} has no "
                        f"body/content placeholder to receive chunk {index}'s "
                        f"bullets or paragraphs -- remove the content or use a "
                        f"different template."
                    )
                _fill_content_placeholder(body_placeholder, body_blocks)
        prs.save(str(file_path))

        overflow_warnings: list[dict[str, object]] = []
        qa_skipped_reason: str | None = None
        pdf_path = _render_to_pdf(file_path)
        if pdf_path is None:
            qa_skipped_reason = "LibreOffice (soffice) not found or conversion failed"
        else:
            try:
                overflow_warnings = _check_overflow(pdf_path)
            finally:
                shutil.rmtree(pdf_path.parent, ignore_errors=True)

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)

        return {
            "path": self._scope.relative(file_path),
            "template_id": template_id,
            "slide_count": len(prs.slides),
            "bytes_written": file_path.stat().st_size,
            "overflow_warnings": overflow_warnings,
            "placeholder_warnings": _leftover_placeholder_warnings(prs),
            "qa_skipped_reason": qa_skipped_reason,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    def extract_pptx_template(
        self,
        source_path: str,
        template_id: str,
        name: str,
        description: str,
        overwrite: bool = False,
    ) -> dict[str, object]:
        """Distill a reusable template from an existing reference deck
        (e.g. one the user just attached -- their own company's branded
        .pptx) so fill_pptx_template can reuse its exact design (theme
        colors, fonts, decorative shapes, master/layout structure) for
        new content afterward, instead of hand-rebuilding that design
        shape by shape or guessing at colors.

        Classifies the reference deck's own real slides into the same
        three roles every bundled template already uses: the first slide
        is "title", the last slide is "closing" (only if the deck has
        more than one slide), everything in between is "content" (the
        only repeatable role -- fill_pptx_template duplicates/trims it to
        fit however much content you actually give later). Every slide
        must already have a real, usable title and/or body placeholder
        (the same kind fill_pptx_template writes into) -- a slide that
        doesn't is a real limitation this reports as an error, not a
        silent skip: redesign or remove that slide in the reference deck
        first, or use write_pptx instead of a template for this deck.

        Once this returns, template_id is immediately usable as
        fill_pptx_template's own template_id argument -- no separate
        registration step.

        Args:
            source_path: path to the reference .pptx already in the workspace
            template_id: a short id for the new template (lowercase
                letters/digits/hyphens, e.g. "acme-corp") -- what
                fill_pptx_template's template_id argument refers to
                afterward
            name: a short human-readable name (e.g. "Acme Corp")
            description: one sentence describing the template's look
                (colors, mood) -- shown alongside the bundled templates'
                own descriptions wherever a template is picked
            overwrite: if a template with this id already exists, replace
                it (default False -- raises instead, since silently
                replacing a named template other calls may already
                reference is a different risk than overwriting one file)
        """
        import yaml
        from pptx import Presentation
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT

        if self._custom_templates_dir is None:
            raise ValueError(
                "No custom templates directory is configured -- extract_pptx_template "
                "has nowhere to save the new template."
            )
        if not _TEMPLATE_ID_RE.match(template_id):
            raise ValueError(
                f"template_id {template_id!r} must be lowercase letters/digits/hyphens "
                f"only (e.g. 'acme-corp')."
            )
        templates_dir = Path(self._custom_templates_dir)
        template_dir = templates_dir / template_id
        if template_dir.exists() and not overwrite:
            raise FileExistsError(
                f"A template with id {template_id!r} already exists at {template_dir} -- "
                f"pass overwrite=True to replace it, or pick a different template_id."
            )

        file_path = self._check_readable(source_path)
        prs = Presentation(str(file_path))
        slide_count = len(prs.slides)
        if slide_count == 0:
            raise ValueError(f"{source_path!r} has no slides -- nothing to extract.")

        slide_roles: list[str] = []
        for index, slide in enumerate(prs.slides):
            if index == 0:
                role = "title"
            elif slide_count > 1 and index == slide_count - 1:
                role = "closing"
            else:
                role = "content"
            has_title = slide.shapes.title is not None
            has_body = _find_body_placeholder(slide) is not None
            if not has_title and not has_body:
                raise ValueError(
                    f"Slide {index + 1} of {source_path!r} has no usable title or "
                    f"body placeholder for fill_pptx_template to write into later -- "
                    f"extract_pptx_template needs every slide in the reference deck "
                    f"to have at least one. Redesign or remove that slide in the "
                    f"reference deck, or use write_pptx instead of a template for "
                    f"this deck."
                )
            slide_roles.append(role)

        accent = "6366F1"  # a reasonable default if the theme has no accent1 at all
        if prs.slide_masters:
            theme_part = prs.slide_masters[0].part.part_related_by(RT.THEME)
            from lxml import etree
            from pptx.oxml.ns import qn

            root = etree.fromstring(theme_part.blob)
            clr_scheme = root.find(f".//{qn('a:clrScheme')}")
            if clr_scheme is not None:
                accent = _read_scheme_color(clr_scheme, "accent1") or accent

        template_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(file_path), str(template_dir / "template.pptx"))
        manifest = {
            "id": template_id,
            "name": name,
            "description": description,
            "accent": accent,
            "slide_count": slide_count,
            "slide_roles": slide_roles,
        }
        (template_dir / "template.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )

        return {
            "template_id": template_id,
            "name": name,
            "slide_count": slide_count,
            "slide_roles": slide_roles,
            "accent": accent,
            "saved_to": str(template_dir),
        }

    @locked_by_path
    def edit_pptx_text(
        self,
        path: str,
        slide: int,
        title: Optional[str] = None,  # noqa: UP045
        content: Optional[str] = None,  # noqa: UP045
        placeholder_index: int = 0,
    ) -> dict[str, object]:
        """Edit one existing slide's title and/or body text in an
        already-existing .pptx -- any file already in the workspace
        (something the user placed there or uploaded), not just one of
        coscribe's own bundled templates the way fill_pptx_template is
        restricted to. Everything else in the file (theme, other slides,
        images, decorative shapes, the *other* placeholders on this same
        slide) is left exactly as authored -- this only ever replaces the
        text inside the one/two placeholders you name."""
        if title is None and content is None:
            raise ValueError("edit_pptx_text needs at least one of title or content to change.")
        prs, file_path, target_slide = self._open_slide(path, slide)
        if title is not None:
            if target_slide.shapes.title is None:
                raise ValueError(
                    f"Slide {slide} of {path!r} has no title placeholder to receive "
                    f"new title text -- read_pptx the slide first to see what's "
                    f"actually there."
                )
            _set_title(target_slide, title)
        if content is not None:
            blocks = parse_blocks(content)
            if any(block.kind == "table" for block in blocks):
                raise ValueError(
                    "edit_pptx_text doesn't support tables -- edit_pptx_text is "
                    "for a slide's own title/bullet placeholders only."
                )
            body_placeholder = _find_body_placeholder(target_slide, placeholder_index)
            if body_placeholder is None:
                raise ValueError(
                    f"Slide {slide} of {path!r} has no content placeholder at "
                    f"placeholder_index={placeholder_index} to receive new body "
                    f"text -- read_pptx the slide first to see what's actually "
                    f"there, or try a different placeholder_index."
                )
            _fill_content_placeholder(body_placeholder, blocks)
        prs.save(str(file_path))

        overflow_warnings: list[dict[str, object]] = []
        qa_skipped_reason: str | None = None
        pdf_path = _render_to_pdf(file_path)
        if pdf_path is None:
            qa_skipped_reason = "LibreOffice (soffice) not found or conversion failed"
        else:
            try:
                overflow_warnings = _check_overflow(pdf_path)
            finally:
                shutil.rmtree(pdf_path.parent, ignore_errors=True)

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)

        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "overflow_warnings": overflow_warnings,
            "placeholder_warnings": _leftover_placeholder_warnings(prs),
            "qa_skipped_reason": qa_skipped_reason,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    def list_pptx_shapes(self, path: str, slide: int) -> dict[str, object]:
        """Structured inventory of every shape on one slide of an
        existing .pptx -- index, type, position/size, rotation, a short
        text preview, and fill color if it's a plain solid fill. Call
        this before `edit_pptx_shape` (or any table-editing tool) on a
        slide you haven't inspected yet: `shape_index` there is this
        list's own `index`, and guessing it blind on a slide with
        several shapes is exactly the failure mode this tool exists to
        avoid.

        `slide_width_in`/`slide_height_in` are the whole deck's own
        canvas size (every slide in one .pptx shares one size) -- besides
        being useful context for reasoning about layout, this is also
        what web/app.py's GET /api/pptx-shapes (the click-to-target-a-
        shape preview feature) uses to turn each shape's inch-based
        bbox into an on-screen percentage overlay, independent of
        whatever pixel size the rendered preview image happens to be."""
        from pptx import Presentation

        file_path = self._check_readable(path)
        prs = Presentation(str(file_path))
        slide_count = len(prs.slides)
        if not 1 <= slide <= slide_count:
            raise ValueError(f"Slide {slide} out of range: deck has {slide_count} slides")
        target_slide = prs.slides[slide - 1]
        shapes = [_describe_shape(index, shape) for index, shape in enumerate(target_slide.shapes)]
        return {
            "slide": slide,
            "shape_count": len(shapes),
            "shapes": shapes,
            "slide_width_in": _emu_to_inches(prs.slide_width),
            "slide_height_in": _emu_to_inches(prs.slide_height),
        }

    @locked_by_path
    def edit_pptx_shape(
        self,
        path: str,
        slide: int,
        shape_index: int,
        left_in: Optional[float] = None,  # noqa: UP045
        top_in: Optional[float] = None,  # noqa: UP045
        width_in: Optional[float] = None,  # noqa: UP045
        height_in: Optional[float] = None,  # noqa: UP045
        rotation: Optional[float] = None,  # noqa: UP045
        fill_color: str = "",
        fill_color_2: str = "",
        gradient_angle: Optional[float] = None,  # noqa: UP045
        text_color: str = "",
    ) -> dict[str, object]:
        """Change an existing shape's geometry, fill color, and/or text
        color in place, on any already-existing .pptx -- call
        `list_pptx_shapes` first to find `shape_index` (this is that
        list's own `index`, 0-based in on-slide order) and confirm
        you're targeting the right shape; text *content*, other shapes,
        and the rest of the deck are untouched. Every parameter is
        optional -- only the ones given are changed.

        `fill_color` alone sets a plain solid fill, same as before. Give
        `fill_color_2` too for a two-stop linear gradient (`fill_color`
        is the first stop, `fill_color_2` the second) -- real OOXML
        `<a:gradFill>` via python-pptx's own native gradient API, not a
        picture or hand-built XML. `gradient_angle` (degrees, 0 =
        left-to-right, 90 = top-to-bottom, increasing clockwise) only
        applies alongside `fill_color_2`; omit it to keep python-pptx's
        own default 90-degree (top-to-bottom) gradient.

        `text_color` recolors every run of text already inside the
        shape (a title/body placeholder counts -- it's a shape like any
        other via `list_pptx_shapes`) -- this is the tool for "make this
        title/paragraph a different color," not `edit_pptx_theme_colors`
        (see that tool's own docstring: changing a theme accent color
        only recolors elements that explicitly *reference* that theme
        color -- coscribe's own decorative shapes and, just as often,
        plain title/body text, do not -- so it routinely leaves text
        exactly the color it already was)."""
        from pptx.dml.color import RGBColor
        from pptx.util import Inches

        if fill_color_2 and not fill_color:
            raise ValueError(
                "edit_pptx_shape: fill_color_2 needs fill_color too -- fill_color "
                "is the gradient's first stop, fill_color_2 its second."
            )
        if gradient_angle is not None and not fill_color_2:
            raise ValueError(
                "edit_pptx_shape: gradient_angle only applies alongside a "
                "two-color gradient fill -- pass fill_color_2 too."
            )
        if (
            left_in is None
            and top_in is None
            and width_in is None
            and height_in is None
            and rotation is None
            and not fill_color
            and not text_color
        ):
            raise ValueError(
                "edit_pptx_shape needs at least one property to change (position, "
                "size, rotation, fill_color, or text_color)."
            )
        if fill_color and not _THEME_HEX_RE.match(fill_color):
            raise ValueError(
                f"fill_color {fill_color!r} must be a 6-hex-digit color (no '#'), e.g. '38BDF8'."
            )
        if fill_color_2 and not _THEME_HEX_RE.match(fill_color_2):
            raise ValueError(
                f"fill_color_2 {fill_color_2!r} must be a 6-hex-digit color (no '#'), "
                f"e.g. '38BDF8'."
            )
        if text_color and not _THEME_HEX_RE.match(text_color):
            raise ValueError(
                f"text_color {text_color!r} must be a 6-hex-digit color (no '#'), e.g. '38BDF8'."
            )
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_shape_at_index(target_slide, slide, shape_index)
        if left_in is not None:
            shape.left = Inches(left_in)
        if top_in is not None:
            shape.top = Inches(top_in)
        if width_in is not None:
            shape.width = Inches(width_in)
        if height_in is not None:
            shape.height = Inches(height_in)
        if rotation is not None:
            shape.rotation = rotation
        if fill_color:
            fill = getattr(shape, "fill", None)
            if fill is None:
                raise ValueError(
                    f"Shape {shape_index} on slide {slide} ({shape.shape_type}) has no "
                    f"fill to set -- fill_color only applies to a shape with a fill "
                    f"(not a table/chart)."
                )
            if fill_color_2:
                fill.gradient()
                stops = fill.gradient_stops
                stops[0].color.rgb = RGBColor.from_string(fill_color)  # type: ignore[no-untyped-call]
                stops[1].color.rgb = RGBColor.from_string(fill_color_2)  # type: ignore[no-untyped-call]
                # Always set an explicit angle, not just when the caller
                # gave one -- a fresh `fill.gradient()`'s <a:lin> element
                # has no `ang` attribute at all, and python-pptx's own
                # gradient_angle *getter* crashes with a TypeError trying
                # to read that back (`360.0 - None`), confirmed live while
                # testing this. 90.0 matches gradient()'s own documented
                # default (top-to-bottom) -- this just makes that default
                # actually readable afterward instead of only writable.
                fill.gradient_angle = gradient_angle if gradient_angle is not None else 90.0
            else:
                fill.solid()
                fill.fore_color.rgb = RGBColor.from_string(fill_color)  # type: ignore[no-untyped-call]
        if text_color:
            if not shape.has_text_frame:
                raise ValueError(
                    f"Shape {shape_index} on slide {slide} ({shape.shape_type}) has no "
                    f"text to color -- text_color only applies to a shape with a text "
                    f"frame (not a table/chart/picture)."
                )
            rgb = RGBColor.from_string(text_color)  # type: ignore[no-untyped-call]
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = rgb
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "shape": _describe_shape(shape_index, shape),
        }

    @locked_by_path
    def delete_pptx_shape(self, path: str, slide: int, shape_index: int) -> dict[str, object]:
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_shape_at_index(target_slide, slide, shape_index)
        element = shape._element  # noqa: SLF001
        element.getparent().remove(element)
        prs.save(str(file_path))
        return {"path": self._scope.relative(file_path), "slide": slide, "shape_index": shape_index}

    def list_pptx_shape_types(self) -> list[str]:
        """Every `shape_type` name `add_pptx_shape` accepts, sorted -- a
        curated subset of python-pptx's full MSO_SHAPE enum (basic
        shapes, arrows, flowchart nodes, callouts). No path/slide
        argument: this is a static list, the same regardless of which
        file or slide you're working on."""
        return sorted(_SHAPE_TYPES)

    def list_pptx_transition_types(self) -> list[str]:
        """Every `transition` name `set_pptx_transition` accepts, sorted --
        PowerPoint's real native transition gallery (48 effects) plus 8
        legacy aliases. No path/slide argument: this is a static list, the
        same regardless of which file or slide you're working on."""
        return sorted(_TRANSITIONS - {"none"})

    def list_pptx_animation_types(self) -> list[str]:
        """Every `animation` name `add_pptx_animation` accepts, sorted --
        PowerPoint's real native animation gallery (203 presets, grouped
        by the `entrance_`/`emphasis_`/`exit_`/`path_` prefix) plus the 6
        legacy short aliases. No path/slide argument: this is a static
        list, the same regardless of which file or slide you're working
        on."""
        return sorted(_ANIMATIONS)

    @locked_by_path
    def add_pptx_shape(
        self,
        path: str,
        slide: int,
        shape_type: str,
        left_in: float,
        top_in: float,
        width_in: float,
        height_in: float,
        text: str = "",
        fill_color: str = "",
        line_color: str = "",
    ) -> dict[str, object]:
        """Add a new preset-geometry shape (an arrow, flowchart node,
        callout, or basic shape -- call `list_pptx_shape_types()` for
        the full curated set) onto an existing slide, at the exact
        position/size you give -- unlike write_pptx's own icon-list/
        stat-callout layouts, which position their own shapes
        automatically, this is for building a specific diagram (a
        process flow of arrows and boxes, a comparison of callouts)
        shape by shape. `text`, if given, is centered inside the shape
        (markdown `**bold**`/`*italic*` supported, same as every other
        text tool in this file). `fill_color`/`line_color` are
        6-hex-digit colors (no '#'); omit either to keep the shape's own
        default theme styling. Returns the new shape's own
        `shape_index` -- pass it straight to `edit_pptx_shape`/
        `add_pptx_hyperlink`/`delete_pptx_shape` to keep working on it."""
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
        from pptx.util import Inches

        if shape_type not in _SHAPE_TYPES:
            raise ValueError(
                f"Unknown shape_type {shape_type!r} -- call list_pptx_shape_types() "
                f"to see the available names."
            )
        if fill_color and not _THEME_HEX_RE.match(fill_color):
            raise ValueError(
                f"fill_color {fill_color!r} must be a 6-hex-digit color (no '#'), "
                f"e.g. '38BDF8'."
            )
        if line_color and not _THEME_HEX_RE.match(line_color):
            raise ValueError(
                f"line_color {line_color!r} must be a 6-hex-digit color (no '#'), "
                f"e.g. '38BDF8'."
            )
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = target_slide.shapes.add_shape(
            getattr(MSO_SHAPE, shape_type),
            Inches(left_in),
            Inches(top_in),
            Inches(width_in),
            Inches(height_in),
        )
        if text:
            text_frame = shape.text_frame
            text_frame.word_wrap = True
            text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
            paragraph = text_frame.paragraphs[0]
            paragraph.alignment = PP_ALIGN.CENTER
            _add_inline_runs(paragraph, text)
        if fill_color:
            shape.fill.solid()
            shape.fill.fore_color.rgb = RGBColor.from_string(fill_color)  # type: ignore[no-untyped-call]
        if line_color:
            shape.line.color.rgb = RGBColor.from_string(line_color)  # type: ignore[no-untyped-call]
        shape_index = len(target_slide.shapes) - 1
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_type": shape_type,
            "shape_index": shape_index,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def add_pptx_formula(
        self,
        path: str,
        slide: int,
        latex: str,
        left_in: float,
        top_in: float,
        width_in: float,
        height_in: float,
        display: bool = True,
    ) -> dict[str, object]:
        """Add a real, natively-editable PowerPoint equation (the same
        Office Math object PowerPoint's own Insert > Equation creates)
        onto an existing slide, at the exact position/size you give --
        for actual mathematical notation (fractions, roots, summations,
        matrices), not a text approximation typed with Unicode
        characters. Always creates a fresh text box containing exactly
        one formula, the same "new object at a position" shape as
        add_pptx_shape above -- for a formula that belongs alongside
        other text in an existing paragraph, this isn't that (v1 scope).

        `latex` is the documented Microsoft 365 LaTeX equation syntax --
        the same input PowerPoint's own equation editor accepts when you
        type LaTeX and press space, e.g.
        r"\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}". Unsupported/malformed LaTeX
        raises ValueError with the real compiler error, not a silent
        best-effort guess.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to add the equation to
            latex: the LaTeX source for the equation
            left_in: horizontal position in inches from the slide's left edge
            top_in: vertical position in inches from the slide's top edge
            width_in: text box width in inches
            height_in: text box height in inches
            display: True (default) for a centered, standalone block
                equation; False for a smaller inline-sized expression.
        """
        from lxml import etree
        from pptx.util import Inches

        from ._native_formula import (
            FormulaCompileError,
            compile_latex_to_inline_omml,
            compile_latex_to_omml,
        )

        try:
            omml_xml = (
                compile_latex_to_omml(latex) if display else compile_latex_to_inline_omml(latex)
            )
        except FormulaCompileError as exc:
            raise ValueError(f"Could not compile LaTeX formula {latex!r}: {exc}") from exc

        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = target_slide.shapes.add_textbox(
            Inches(left_in), Inches(top_in), Inches(width_in), Inches(height_in)
        )
        paragraph = shape.text_frame.paragraphs[0]
        p_element = paragraph._p  # noqa: SLF001
        # `<a14:m>` wrapping `<m:oMathPara>`/`<m:oMath>` -- the exact shape
        # hugohe3/ppt-master's own documented contract uses (see _A14_NS's
        # comment above). A fresh text box's first paragraph has no runs
        # yet, so this is simply appended as the paragraph's sole content.
        math_root = etree.fromstring(omml_xml.encode("utf-8"))
        a14_wrapper = etree.SubElement(p_element, f"{{{_A14_NS}}}m", nsmap={"a14": _A14_NS})
        a14_wrapper.append(math_root)

        _mark_mce_ignorable(target_slide.element, "a14")
        assert_ooxml_valid(target_slide.element, "add_pptx_formula")
        prs.save(str(file_path))

        shape_index = len(target_slide.shapes) - 1
        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "display": display,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def replace_pptx_image(
        self, path: str, slide: int, shape_index: int, image_path: str
    ) -> dict[str, object]:
        """Swap an existing picture's image in place, on any already-
        existing .pptx -- position, size, and crop are all untouched
        because the picture shape itself never changes, only which image
        its `<a:blip>` points at (`Picture.image` has no setter in
        python-pptx; this is the verified real mechanism, see
        PPTX_DESIGN.md §4.2). Call `list_pptx_shapes` first to find the
        picture's `shape_index` (`is_picture: true`). Raster images only
        (PNG/JPEG/GIF/BMP/TIFF -- same formats
        `add_pptx_image` supports); not SVG."""
        image_file = self._check_readable(image_path)
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_picture_shape(target_slide, slide, shape_index)
        _image_part, r_id = shape.part.get_or_add_image_part(str(image_file))
        shape._element.blipFill.blip.rEmbed = r_id  # noqa: SLF001
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def crop_pptx_image(
        self,
        path: str,
        slide: int,
        shape_index: int,
        crop_left: float = 0.0,
        crop_right: float = 0.0,
        crop_top: float = 0.0,
        crop_bottom: float = 0.0,
    ) -> dict[str, object]:
        """Crop an existing picture shape in place -- each `crop_*` is a
        fraction (0.0-1.0) of the image's own original width/height to
        trim from that edge; the shape's on-slide position/size are
        untouched, only which part of the image shows through inside
        that same box changes (`Picture.crop_*`, the exact mechanism
        `set_pptx_background_image` already uses for its own cover-crop
        -- native python-pptx API, no hand-built XML). Call
        `list_pptx_shapes` first to find the picture's `shape_index`
        (`is_picture: true`). Sets all four values together every call
        (not incremental) -- pass only the edges you actually want
        trimmed, the rest default to 0.0 (uncropped)."""
        if not (0.0 <= crop_left < 1.0 and 0.0 <= crop_right < 1.0):
            raise ValueError("crop_left/crop_right must each be within [0.0, 1.0).")
        if not (0.0 <= crop_top < 1.0 and 0.0 <= crop_bottom < 1.0):
            raise ValueError("crop_top/crop_bottom must each be within [0.0, 1.0).")
        if crop_left + crop_right >= 1.0:
            raise ValueError(
                f"crop_left ({crop_left}) + crop_right ({crop_right}) must be < 1.0 "
                f"-- otherwise no part of the image's width would remain visible."
            )
        if crop_top + crop_bottom >= 1.0:
            raise ValueError(
                f"crop_top ({crop_top}) + crop_bottom ({crop_bottom}) must be < 1.0 "
                f"-- otherwise no part of the image's height would remain visible."
            )
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_picture_shape(target_slide, slide, shape_index)
        shape.crop_left = crop_left
        shape.crop_right = crop_right
        shape.crop_top = crop_top
        shape.crop_bottom = crop_bottom
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "crop_left": crop_left,
            "crop_right": crop_right,
            "crop_top": crop_top,
            "crop_bottom": crop_bottom,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    def list_pptx_icons(self) -> list[str]:
        """Every icon name `add_pptx_icon` accepts, sorted -- a curated
        subset of Lucide (https://lucide.dev, ISC license), not the
        full ~1500-icon set. No path/slide argument: this is a static
        list, the same regardless of which file or slide you're
        working on."""
        return sorted(_ICON_NAMES)

    @locked_by_path
    def add_pptx_icon(
        self,
        path: str,
        slide: int,
        icon_name: str,
        left_in: float = 1.0,
        top_in: float = 1.0,
        size_in: float = 1.0,
        color: str = "1F2937",
    ) -> dict[str, object]:
        """Insert a real icon (rasterized from Lucide, see
        `list_pptx_icons`) onto an existing slide, as a new square
        picture -- unlike `layout: icon-list`'s glyph-in-a-circle
        (write_pptx's own bundled shape, not a real icon), this is an
        actual recognizable pictogram. Call `list_pptx_icons()` first if
        you're not sure `icon_name` is bundled; an unknown name raises
        immediately rather than silently doing nothing."""
        if icon_name not in _ICON_NAMES:
            raise ValueError(
                f"Unknown icon {icon_name!r} -- call list_pptx_icons() to see the "
                f"available names."
            )
        if not _THEME_HEX_RE.match(color):
            raise ValueError(f"color {color!r} must be a 6-hex-digit color (no '#').")
        prs, file_path, target_slide = self._open_slide(path, slide)

        from pptx.util import Inches

        recolored = _recolor_icon(_ICONS_DIR / f"{icon_name}.png", color)
        buffer = BytesIO()
        recolored.save(buffer, format="PNG")
        buffer.seek(0)
        target_slide.shapes.add_picture(
            buffer, Inches(left_in), Inches(top_in), width=Inches(size_in), height=Inches(size_in)
        )
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "icon_name": icon_name,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def recolor_pptx_icon(
        self, path: str, slide: int, shape_index: int, color: str
    ) -> dict[str, object]:
        if not _THEME_HEX_RE.match(color):
            raise ValueError(f"color {color!r} must be a 6-hex-digit color (no '#').")
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_picture_shape(target_slide, slide, shape_index)

        from PIL import Image

        current_image = Image.open(BytesIO(shape.image.blob))
        recolored = _recolor_image(current_image, color)
        buffer = BytesIO()
        recolored.save(buffer, format="PNG")
        buffer.seek(0)
        _image_part, r_id = shape.part.get_or_add_image_part(buffer)
        shape._element.blipFill.blip.rEmbed = r_id  # noqa: SLF001
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "color": color.upper(),
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def edit_pptx_table_cell(
        self, path: str, slide: int, shape_index: int, row: int, col: int, text: str
    ) -> dict[str, object]:
        """Replace one cell's text in an existing table, in place, on
        any already-existing .pptx -- call `list_pptx_shapes` first to
        find the table's `shape_index` and its `table_dimensions`
        (rows/cols); `row`/`col` are 0-based. Every other cell, and the
        rest of the deck, is untouched."""
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_table_shape(target_slide, slide, shape_index)
        table = shape.table
        n_rows, n_cols = len(table.rows), len(table.columns)
        if not 0 <= row < n_rows or not 0 <= col < n_cols:
            raise ValueError(
                f"cell (row={row}, col={col}) out of range: table has {n_rows} row(s) "
                f"x {n_cols} column(s) -- call list_pptx_shapes first to confirm "
                f"table_dimensions."
            )
        cell = table.cell(row, col)
        # .clear() first -- same reasoning as _set_title/_fill_content_placeholder:
        # this REPLACES the cell's existing content instead of appending onto it.
        cell.text_frame.clear()
        _add_inline_runs(cell.text_frame.paragraphs[0], text)
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "row": row,
            "col": col,
        }

    @locked_by_path
    def merge_pptx_table_cells(
        self,
        path: str,
        slide: int,
        shape_index: int,
        start_row: int,
        start_col: int,
        end_row: int,
        end_col: int,
    ) -> dict[str, object]:
        """Merge a rectangular range of cells in an existing table into
        one, on any already-existing .pptx -- (start_row, start_col) and
        (end_row, end_col) are opposite corners of the range (either
        diagonal, either order), 0-based. Every merged-away cell's
        existing text is kept, appended into the resulting cell as an
        extra paragraph (python-pptx's own merge behavior, verified
        live) -- not discarded, so clear an unwanted cell's text with
        `edit_pptx_table_cell` first if that's not wanted in the merged
        result. Call `list_pptx_shapes` first to find the table's
        `shape_index` and `table_dimensions`. Raises if the range
        already contains a
        merged cell."""
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_table_shape(target_slide, slide, shape_index)
        table = shape.table
        n_rows, n_cols = len(table.rows), len(table.columns)
        for row, col, label in (
            (start_row, start_col, "start_row/start_col"),
            (end_row, end_col, "end_row/end_col"),
        ):
            if not 0 <= row < n_rows or not 0 <= col < n_cols:
                raise ValueError(
                    f"{label} (row={row}, col={col}) out of range: table has "
                    f"{n_rows} row(s) x {n_cols} column(s) -- call list_pptx_shapes "
                    f"first to confirm table_dimensions."
                )
        table.cell(start_row, start_col).merge(table.cell(end_row, end_col))
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "merged_range": {
                "start_row": start_row,
                "start_col": start_col,
                "end_row": end_row,
                "end_col": end_col,
            },
        }

    @staticmethod
    def _require_single_table(table_blocks: list[Block]) -> None:
        if len(table_blocks) > 1:
            raise ValueError("A slide can have at most one table.")

    def _open_slide(self, path: str, slide: int) -> tuple[PresentationType, Path, Any]:
        """Load `path`, validate `slide` is in range, and return (prs,
        file_path, target_slide) -- the load-validate-locate sequence every
        tool that modifies one existing slide repeats."""
        from pptx import Presentation

        file_path = self._check_readable(path)
        prs = Presentation(str(file_path))
        slide_count = len(prs.slides)
        if not 1 <= slide <= slide_count:
            raise ValueError(f"Slide {slide} out of range: deck has {slide_count} slides")
        return prs, file_path, prs.slides[slide - 1]

    @locked_by_path
    def delete_pptx_slide(self, path: str, slide: int) -> dict[str, object]:
        """Remove one slide from an existing .pptx, in place -- every
        other slide, and their own order, is untouched. Same drop-the-
        relationship-then-remove-the-list-entry mechanism `_clear_slides`
        already uses when write_pptx builds on a `template_path`."""
        from pptx.oxml.ns import qn

        prs, file_path, _target_slide = self._open_slide(path, slide)
        sld_id_lst = prs.slides._sldIdLst  # noqa: SLF001
        sld_id_element = list(sld_id_lst)[slide - 1]
        r_id = sld_id_element.get(qn("r:id"))
        prs.part.drop_rel(r_id)
        sld_id_lst.remove(sld_id_element)
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "deleted_slide": slide,
            "slide_count": len(prs.slides),
        }

    @locked_by_path
    def duplicate_pptx_slide(
        self, path: str, slide: int, insert_at: Optional[int] = None  # noqa: UP045
    ) -> dict[str, object]:
        """Duplicate one slide in an existing .pptx, in place -- every
        shape, its exact position/size/formatting, and every image the
        source slide has are copied. Speaker notes are not (see
        `_duplicate_slide_part`'s docstring). Defaults to inserting the
        copy immediately after the source; pass `insert_at` (1-based) to
        place it elsewhere instead."""
        prs, file_path, source_slide = self._open_slide(path, slide)
        slide_count = len(prs.slides)
        target_position = insert_at if insert_at is not None else slide + 1
        if not 1 <= target_position <= slide_count + 1:
            raise ValueError(
                f"insert_at {target_position} out of range: deck has {slide_count} "
                f"slide(s), valid positions are 1 to {slide_count + 1}."
            )
        new_rid = _duplicate_slide_part(prs, source_slide)
        sld_id_lst = prs.slides._sldIdLst  # noqa: SLF001
        new_sld_id = sld_id_lst.add_sldId(new_rid)
        sld_id_lst.remove(new_sld_id)
        sld_id_lst.insert(target_position - 1, new_sld_id)
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "source_slide": slide,
            "new_slide": target_position,
            "slide_count": len(prs.slides),
        }

    @locked_by_path
    def reorder_pptx_slide(self, path: str, slide: int, new_position: int) -> dict[str, object]:
        """Move one slide to a new 1-based position in an existing
        .pptx, in place -- every slide's own content is untouched, only
        the deck's slide order changes."""
        prs, file_path, _target_slide = self._open_slide(path, slide)
        slide_count = len(prs.slides)
        if not 1 <= new_position <= slide_count:
            raise ValueError(
                f"new_position {new_position} out of range: deck has {slide_count} slide(s)."
            )
        sld_id_lst = prs.slides._sldIdLst  # noqa: SLF001
        sld_id_element = list(sld_id_lst)[slide - 1]
        sld_id_lst.remove(sld_id_element)
        sld_id_lst.insert(new_position - 1, sld_id_element)
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "old_position": slide,
            "new_position": new_position,
        }

    @locked_by_path
    def add_pptx_chart(
        self, path: str, slide: int, chart_type: str, data: str, title: str = ""
    ) -> dict[str, object]:
        from pptx.chart.data import CategoryChartData
        from pptx.enum.chart import XL_CHART_TYPE
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT

        if chart_type not in _CHART_TYPES:
            raise ValueError(
                f"Unknown chart_type {chart_type!r}. Use one of: {', '.join(sorted(_CHART_TYPES))}"
            )
        categories, series = _parse_chart_table(data)
        if chart_type == "pie" and len(series) > 1:
            raise ValueError("pie charts take exactly one data column plus the category column")

        prs, file_path, target_slide = self._open_slide(path, slide)

        # python-pptx's chart_data submodule ships no return-type annotations
        # on these two methods (unlike the rest of the library, which does),
        # so strict mode sees them as untyped calls -- confirmed there's no
        # types-python-pptx package on PyPI to fill the gap.
        chart_data = CategoryChartData()  # type: ignore[no-untyped-call]
        chart_data.categories = categories
        for name, values in series.items():
            chart_data.add_series(name, values)  # type: ignore[no-untyped-call]

        chart_xl_type = {
            "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
            "line": XL_CHART_TYPE.LINE,
            "pie": XL_CHART_TYPE.PIE,
        }[chart_type]

        # Real, live-reproduced defect this avoids: a fixed Inches(1, 1.6,
        # 8, 5) box ignored both the deck's real slide size (leaving a lot
        # of unused space on a widescreen deck) and any text already on the
        # slide's own body/content placeholder, so a chart added next to a
        # short intro sentence rendered directly on top of it. Default to
        # the same content-area geometry every other content slide uses.
        left, top, width, height = _content_area(prs)
        slide_height = prs.slide_height or 6858000
        body_placeholder = _find_body_placeholder(target_slide)
        if (
            body_placeholder is not None
            and body_placeholder.has_text_frame
            and body_placeholder.text_frame.text.strip()
        ):
            body_bbox = _shape_bbox(body_placeholder)
            if body_bbox is not None:
                b_left, b_top, b_width, b_height = body_bbox
                # Shrink the placeholder to fit its own text instead of
                # trusting its full box height -- a *second* real,
                # live-reproduced defect this fixes: a stock "Title and
                # Content" layout's body placeholder is boxed generously
                # enough for a full bullet list regardless of how little
                # text it actually holds, so computing the chart's
                # position from that oversized box left almost no room
                # and pushed the chart off the bottom of the slide
                # entirely. One paragraph's worth of space per actual
                # paragraph is a rough but bounded estimate (doesn't
                # account for a paragraph wrapping across multiple visual
                # lines, since nothing in this sandbox can render text to
                # measure that precisely) -- good enough for the realistic
                # case this exists for (a short intro line/caption above a
                # chart), not a general-purpose text layout engine.
                paragraph_count = max(len(body_placeholder.text_frame.paragraphs), 1)
                per_paragraph_height = int(slide_height * 0.06)
                intro_height = min(b_height, per_paragraph_height * paragraph_count)
                # A THIRD real, live-reproduced defect, more severe than
                # the first two: this placeholder never had its own
                # <a:xfrm> (it inherits position/size from the layout, the
                # normal state for an unmoved placeholder) -- setting
                # `.height` alone makes python-pptx create a *new*, mostly
                # empty <a:xfrm> with only <a:ext cy=...> populated,
                # leaving width at 0 and the offset missing entirely. The
                # actual, live result: the placeholder rendered as a
                # near-zero-width sliver, wrapping to one character per
                # line and spilling off the edge of the slide -- caught
                # from a real rendered screenshot, not found by any
                # automated check (a 0-width shape still "has" a bbox, so
                # _check_text_overlaps/_check_missing_visual_elements had
                # no reason to flag it). All four dimensions must be set
                # together whenever a placeholder's inherited geometry is
                # touched at all.
                body_placeholder.left = b_left
                body_placeholder.top = b_top
                body_placeholder.width = b_width
                body_placeholder.height = intro_height
                # Guard against a repeat of the bug this whole block's
                # comment above describes -- if a future edit here ever
                # sets only one dimension again, fail loudly at generation
                # time instead of silently shipping a shape that renders
                # as an unreadable sliver. Zero-or-negative is never a
                # legitimate size for a real, visible shape.
                if body_placeholder.width <= 0 or body_placeholder.height <= 0:
                    raise ValueError(
                        "internal error: body placeholder ended up with a "
                        f"non-positive size (width={body_placeholder.width}, "
                        f"height={body_placeholder.height}) while making room "
                        "for the chart -- this is a bug in add_pptx_chart, not "
                        "the caller's input."
                    )
                gap = int(slide_height * 0.03)
                new_top = b_top + intro_height + gap
                usable_bottom = slide_height - int(slide_height * _MARGIN_FRACTION)
                remaining = usable_bottom - new_top
                min_chart_height = int(slide_height * 0.35)
                if remaining < min_chart_height:
                    raise ValueError(
                        f"Slide {slide}'s existing body text leaves too little room "
                        f"for a chart below it -- shorten the body text, or add the "
                        f"chart to a slide whose body placeholder is empty (a "
                        f"title-only chunk)."
                    )
                top = new_top
                height = remaining

        graphic_frame = target_slide.shapes.add_chart(
            chart_xl_type, left, top, width, height, chart_data
        )
        chart = graphic_frame.chart
        if title:
            chart.has_title = True
            chart.chart_title.text_frame.text = title
        # Match the deck's own designated text color (its theme's `dk1`
        # slot -- the "text" role write_pptx's own `theme` parameter writes
        # into, see _THEME_SCHEME_COLOR_TAGS) instead of leaving python-
        # pptx's chart default (black), which is unreadable on a dark
        # theme's background -- another real, live-reproduced defect: a
        # chart's axis/legend text stayed black even after `theme=
        # "bg=...,text=F8FAFC,..."` lightened everything else on the slide.
        from lxml import etree
        from pptx.oxml.ns import qn

        theme_part = target_slide.slide_layout.slide_master.part.part_related_by(RT.THEME)
        theme_root = etree.fromstring(theme_part.blob)
        clr_scheme = theme_root.find(f".//{qn('a:clrScheme')}")
        text_color = _read_scheme_color(clr_scheme, "dk1") if clr_scheme is not None else None
        if text_color:
            from pptx.dml.color import RGBColor

            chart.font.color.rgb = RGBColor.from_string(text_color)  # type: ignore[no-untyped-call]
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "chart_type": chart_type,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def add_pptx_image(
        self,
        path: str,
        slide: int,
        image_path: str,
        left: float = 1.0,
        top: float = 1.0,
        width: Optional[float] = None,  # noqa: UP045
        height: Optional[float] = None,  # noqa: UP045
    ) -> dict[str, object]:
        from pptx.util import Inches

        image_file = self._check_readable(image_path)
        prs, file_path, target_slide = self._open_slide(path, slide)

        target_slide.shapes.add_picture(
            str(image_file),
            Inches(left),
            Inches(top),
            width=Inches(width) if width is not None else None,
            height=Inches(height) if height is not None else None,
        )
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def set_pptx_background_image(
        self, path: str, slide: int, image_path: str
    ) -> dict[str, object]:
        """python-pptx's slide.background.fill only supports solid()/
        gradient(), no picture fill at all -- so a background image is a
        full-slide `add_picture` at (0, 0) plus two things python-pptx
        does support natively that a naive stretch-to-fill would skip:
        a CSS `background-size: cover`-style crop (via Picture.crop_*,
        computed from the image's own real aspect ratio, not the slide's)
        so the image isn't distorted, and moving the resulting <p:pic>
        element to be the first shape child of the slide's spTree (right
        after the always-present nvGrpSpPr/grpSpPr) so it renders behind
        every existing placeholder/shape -- add_picture appends at the
        end (front-most) by default, confirmed empirically before writing
        this, which is the opposite of what a background needs."""
        from PIL import Image
        from pptx.oxml.ns import qn

        image_file = self._check_readable(image_path)
        prs, file_path, target_slide = self._open_slide(path, slide)

        with Image.open(image_file) as image:
            image_width, image_height = image.size

        slide_width = prs.slide_width
        slide_height = prs.slide_height
        image_ratio = image_width / image_height
        slide_ratio = slide_width / slide_height

        crop_left = crop_right = crop_top = crop_bottom = 0.0
        if image_ratio > slide_ratio:
            # Image is relatively wider than the slide -- crop its sides.
            visible_fraction = slide_ratio / image_ratio
            crop_left = crop_right = (1 - visible_fraction) / 2
        elif image_ratio < slide_ratio:
            # Image is relatively taller than the slide -- crop top/bottom.
            visible_fraction = image_ratio / slide_ratio
            crop_top = crop_bottom = (1 - visible_fraction) / 2

        picture = target_slide.shapes.add_picture(
            str(image_file), 0, 0, width=slide_width, height=slide_height
        )
        picture.crop_left = crop_left
        picture.crop_right = crop_right
        picture.crop_top = crop_top
        picture.crop_bottom = crop_bottom

        sp_tree = target_slide.shapes._spTree
        pic_element = picture._element
        sp_tree.remove(pic_element)
        group_props = sp_tree.find(qn("p:grpSpPr"))
        insert_at = list(sp_tree).index(group_props) + 1 if group_props is not None else 0
        sp_tree.insert(insert_at, pic_element)

        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def add_pptx_scrim(
        self, path: str, slide: int, opacity: float = 0.35, color: str = "000000"
    ) -> dict[str, object]:
        """A full-slide semi-transparent rectangle, layered above any
        background picture but below every other shape -- keeps title/body
        text legible over a busy photo background, the same "scrim" idea a
        real slide-design tool uses. `python-pptx` has no transparency API
        on `FillFormat` at all, so `<a:alpha>` is hand-appended under the
        `<a:srgbClr>` element `fill.solid()` already builds -- verified
        empirically (a real LibreOffice-rendered black rectangle at 35%
        alpha over a white slide came back mid-gray, not solid black, so
        the transparency is genuinely applied, not silently ignored)
        before writing this. Standalone rather than a `set_pptx_background_
        image` parameter, deliberately -- it composes with a review step
        that decides *whether* a slide actually needs it, rather than
        applying it unconditionally to every background image."""
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.oxml.ns import qn
        from pptx.oxml.xmlchemy import OxmlElement

        prs, file_path, target_slide = self._open_slide(path, slide)

        shape = target_slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(color)  # type: ignore[no-untyped-call]
        shape.line.fill.background()
        shape.shadow.inherit = False

        srgb_clr = shape.fill._xPr.find(qn("a:solidFill")).find(qn("a:srgbClr"))
        alpha = OxmlElement("a:alpha")
        alpha.set("val", str(int(opacity * 100000)))
        srgb_clr.append(alpha)

        sp_tree = target_slide.shapes._spTree
        scrim_element = shape._element
        sp_tree.remove(scrim_element)
        existing_pics = sp_tree.findall(qn("p:pic"))
        if existing_pics:
            insert_at = list(sp_tree).index(existing_pics[-1]) + 1
        else:
            group_props = sp_tree.find(qn("p:grpSpPr"))
            insert_at = list(sp_tree).index(group_props) + 1 if group_props is not None else 0
        sp_tree.insert(insert_at, scrim_element)

        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def add_pptx_shape_effect(
        self,
        path: str,
        slide: int,
        shape_index: int,
        effect: str,
        color: str = "000000",
        opacity: float = 0.4,
        size_pt: float = 8.0,
        distance_pt: float = 4.0,
        direction: float = 45.0,
    ) -> dict[str, object]:
        """Apply a drop shadow, glow, or soft edge to one shape -- any
        kind (autoshape, picture, connector), since CT_EffectList (the
        `<p:spPr>` child that carries these) is shared by all of them.
        python-pptx has no public API for this at all -- its own
        ShadowFormat can only suppress an inherited theme shadow
        (`shape.shadow.inherit = False`, used by add_pptx_scrim above),
        never create or customize one -- so every effect element this
        writes is hand-built against the real ECMA-376 schema
        (`_ooxml_schemas/transitional/dml-main.xsd`'s CT_EffectList/
        CT_GlowEffect/CT_OuterShadowEffect/CT_SoftEdgesEffect).

        Deliberately narrower than the full schema, matching
        set_pptx_transition/add_pptx_animation's own precedent -- only
        the two color-bearing effects real slide design actually reaches
        for (shadow, glow) and one color-free effect (soft_edge) are
        supported; blur/fillOverlay/innerShdw/prstShdw/reflection are
        not."""
        if effect not in _SHAPE_EFFECT_TYPES:
            raise ValueError(f"effect {effect!r} must be one of {sorted(_SHAPE_EFFECT_TYPES)}.")
        if effect != "none":
            if not _THEME_HEX_RE.match(color):
                raise ValueError(f"color {color!r} must be a 6-hex-digit color (no '#').")
            if not 0.0 <= opacity <= 1.0:
                raise ValueError(f"opacity {opacity} must be between 0.0 and 1.0.")
            if size_pt <= 0:
                raise ValueError(f"size_pt {size_pt} must be positive.")
            if effect == "shadow" and distance_pt < 0:
                raise ValueError(f"distance_pt {distance_pt} must be non-negative.")

        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_shape_at_index(target_slide, slide, shape_index)
        _apply_shape_effect(shape, effect, color.upper(), opacity, size_pt, distance_pt, direction)
        assert_ooxml_valid(target_slide.element, "add_pptx_shape_effect")
        prs.save(str(file_path))

        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_path, preview_skipped_reason = render_thumbnail(file_path, state_dir)
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "effect": effect,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }

    @locked_by_path
    def set_pptx_notes(self, path: str, slide: int, notes: str) -> dict[str, object]:
        prs, file_path, target_slide = self._open_slide(path, slide)
        target_slide.notes_slide.notes_text_frame.text = notes
        prs.save(str(file_path))
        return {"path": self._scope.relative(file_path), "slide": slide}

    @locked_by_path
    def set_pptx_transition(
        self, path: str, slide: int, transition: str, duration: float = 1.0
    ) -> dict[str, object]:
        if transition not in _TRANSITIONS:
            raise ValueError(
                f"Unknown transition {transition!r}. Use one of: "
                f"{', '.join(sorted(_TRANSITIONS))}"
            )
        prs, file_path, target_slide = self._open_slide(path, slide)
        _set_slide_transition(target_slide.element, transition, duration)
        assert_ooxml_valid(target_slide.element, "set_pptx_transition")
        prs.save(str(file_path))
        return {"path": self._scope.relative(file_path), "slide": slide, "transition": transition}

    @locked_by_path
    def add_pptx_animation(
        self,
        path: str,
        slide: int,
        shape_index: int,
        animation: str,
        duration: float = 0.5,
        trigger: str = "on-click",
        delay: float = 0.0,
        by_paragraph: bool = False,
    ) -> dict[str, object]:
        if animation not in _ANIMATIONS:
            raise ValueError(
                f"Unknown animation {animation!r}. Use one of: {', '.join(sorted(_ANIMATIONS))}"
            )
        if trigger not in _TRIGGERS:
            raise ValueError(
                f"Unknown trigger {trigger!r}. Use one of: {', '.join(sorted(_TRIGGERS))}"
            )
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_shape_at_index(target_slide, slide, shape_index)
        shape_id = shape.shape_id
        duration_ms = max(1, round(duration * 1000))
        delay_ms = max(0, round(delay * 1000))

        paragraph_indexes: list[int] | None = None
        if by_paragraph:
            if not shape.has_text_frame:
                raise ValueError(
                    f"add_pptx_animation: shape {shape_index} on slide {slide} has no text "
                    "frame -- by_paragraph needs a shape with text."
                )
            paragraph_indexes = [
                index
                for index, paragraph in enumerate(shape.text_frame.paragraphs)
                if paragraph.text.strip()
            ]
            if not paragraph_indexes:
                raise ValueError(
                    f"add_pptx_animation: shape {shape_index} on slide {slide} has no "
                    "non-empty paragraphs to animate by_paragraph."
                )
            for paragraph_index in paragraph_indexes:
                _add_animation_step(
                    target_slide.element, shape_id, animation, duration_ms, trigger, delay_ms,
                    paragraph_index,
                )
            timing = target_slide.element.find(f"{{{_P_NS}}}timing")
            _ensure_paragraph_build(timing, shape_id)
        else:
            _add_animation_step(
                target_slide.element, shape_id, animation, duration_ms, trigger, delay_ms, None
            )

        assert_ooxml_valid(target_slide.element, "add_pptx_animation")

        # Save to a temp file and verify before touching the real file --
        # if the read-back check fails, the user's file is left untouched
        # rather than silently left in a possibly-broken state.
        tmp_path = file_path.with_name(f"{file_path.name}.animation-tmp")
        prs.save(str(tmp_path))
        try:
            _verify_animation_readback(
                tmp_path, slide, shape_id, animation, trigger, paragraph_indexes
            )
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        tmp_path.replace(file_path)

        preset = _resolve_animation_preset(animation)
        actual_duration_ms = (
            duration_ms
            if preset["duration_scalable"] and preset["default_duration_ms"] is not None
            else preset["default_duration_ms"]
        )
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "animation": animation,
            "trigger": trigger,
            "by_paragraph": by_paragraph,
            "paragraph_count": len(paragraph_indexes) if paragraph_indexes is not None else None,
            "duration_ms": actual_duration_ms,
        }

    @locked_by_path
    def add_pptx_audio(
        self,
        path: str,
        slide: int,
        audio_path: str,
        left_in: float = 0.3,
        top_in: float = 0.3,
        trigger: str = "auto",
        start_delay: float = 0.0,
        hidden: bool = False,
    ) -> dict[str, object]:
        if trigger not in _AUDIO_TRIGGERS:
            raise ValueError(
                f"Unknown trigger {trigger!r}. Use one of: {', '.join(sorted(_AUDIO_TRIGGERS))}"
            )
        audio_file = self._check_readable(audio_path)
        ext = audio_file.suffix.lower()
        if ext not in _AUDIO_CONTENT_TYPES:
            raise ValueError(
                f"add_pptx_audio: unsupported audio format {ext!r} on {audio_path!r} -- "
                f"use one of: {', '.join(sorted(_AUDIO_CONTENT_TYPES))}"
            )
        prs, file_path, target_slide = self._open_slide(path, slide)

        import io

        from lxml import etree
        from pptx.media import SPEAKER_IMAGE_BYTES, Video
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT
        from pptx.oxml import parse_xml
        from pptx.util import Inches

        # `Video` is python-pptx's own generic media-blob wrapper (used
        # internally by `add_movie`) -- nothing in it is video-specific,
        # confirmed by reading its source before reusing it here. Its own
        # `get_or_add_media_part`/`relate_to`/`get_or_add_image_part` are
        # the same real relationship-writing plumbing `add_movie` uses,
        # reused directly rather than hand-rolled, EXCEPT for the actual
        # audio relationship type: `add_movie`'s own higher-level
        # `get_or_add_video_media_part` hardcodes `RT.VIDEO` for the
        # legacy relationship, which would be semantically wrong for
        # audio (and mismatched against this method's own `<a:audioFile>`
        # element) -- built directly with the real `RT.AUDIO` type
        # instead, confirmed to exist in python-pptx's own constants.
        _register_audio_media_part_class()
        video = Video.from_path_or_file_like(str(audio_file), _AUDIO_CONTENT_TYPES[ext])
        slide_part = target_slide.part
        media_part = slide_part.package.get_or_add_media_part(video)
        audio_rid = slide_part.relate_to(media_part, RT.AUDIO)
        media_rid = slide_part.relate_to(media_part, RT.MEDIA)
        # The default "media loudspeaker" icon python-pptx already bundles
        # for `add_movie`'s own poster-frame fallback -- reused as-is
        # rather than vendoring hugohe3/ppt-master's own separate icon,
        # since coscribe already ships python-pptx as a real dependency.
        _, poster_rid = slide_part.get_or_add_image_part(io.BytesIO(SPEAKER_IMAGE_BYTES))

        shape_id = target_slide.shapes._next_shape_id
        shape_name = audio_file.name
        if hidden:
            x_emu = y_emu = -_AUDIO_MARKER_SIZE_EMU
        else:
            x_emu, y_emu = int(Inches(left_in)), int(Inches(top_in))

        pic = _build_audio_pic_element(
            shape_id,
            shape_name,
            audio_rid,
            media_rid,
            poster_rid,
            x_emu,
            y_emu,
            _AUDIO_MARKER_SIZE_EMU,
        )
        # `_build_audio_pic_element` returns a plain lxml element -- python-
        # pptx's own shape factory (invoked whenever `target_slide.shapes`
        # is iterated, which `render_pptx_preview`/`list_pptx_shapes` and
        # this method's own shape_index computation below all do) expects
        # its custom `CT_Picture` subclass instead, keyed off a parser
        # python-pptx registers globally -- round-tripping through
        # `parse_xml(etree.tostring(...))` re-parses with that same
        # parser, confirmed empirically to yield the real `CT_Picture`
        # class regardless of the (arbitrary, auto-generated) namespace
        # prefixes `etree.tostring` emits for Clark-notation tags.
        target_slide.shapes._spTree.append(parse_xml(etree.tostring(pic)))
        shape_index = len(list(target_slide.shapes)) - 1

        root_child = _timing_root_child_list(target_slide.element)
        timing = target_slide.element.find(f"{{{_P_NS}}}timing")
        next_id = _next_timing_id(timing)
        start_delay_ms = None if trigger == "on-click" else max(0, round(start_delay * 1000))
        root_child.append(_build_audio_timing_node(shape_id, next_id, start_delay_ms))

        assert_ooxml_valid(target_slide.element, "add_pptx_audio")
        prs.save(str(file_path))

        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "trigger": trigger,
            "start_delay": (None if start_delay_ms is None else round(start_delay_ms / 1000, 3)),
            "hidden": hidden,
        }

    @locked_by_path
    def add_pptx_hyperlink(
        self,
        path: str,
        slide: int,
        shape_index: int,
        url: str,
        text: Optional[str] = None,  # noqa: UP045
    ) -> dict[str, object]:
        _validate_hyperlink_url(url)
        prs, file_path, target_slide = self._open_slide(path, slide)
        shape = _get_shape_at_index(target_slide, slide, shape_index)
        jump_target = _resolve_slide_jump_target(prs, slide, url)

        if text is None:
            if jump_target is not None:
                shape.click_action.target_slide = jump_target
            else:
                shape.click_action.hyperlink.address = url
            prs.save(str(file_path))
            return {
                "path": self._scope.relative(file_path),
                "slide": slide,
                "shape_index": shape_index,
                "url": url,
                "text": None,
            }

        if not shape.has_text_frame:
            raise ValueError(
                f"add_pptx_hyperlink: shape {shape_index} on slide {slide} has no text "
                "frame -- omit `text` to hyperlink the whole shape instead."
            )
        matching_run = None
        available_texts: list[str] = []
        for paragraph in shape.text_frame.paragraphs:
            for run in paragraph.runs:
                available_texts.append(run.text)
                if run.text == text:
                    matching_run = run
                    break
            if matching_run is not None:
                break
        if matching_run is None:
            raise ValueError(
                f"add_pptx_hyperlink: no run with exact text {text!r} found in shape "
                f"{shape_index} on slide {slide} -- that shape's runs are: "
                f"{available_texts!r}. The text must match one whole run exactly; "
                "hyperlinking part of a run isn't supported."
            )
        if jump_target is not None:
            from pptx.action import ActionSetting

            # Run has no public `.click_action` the way Shape does (only
            # the external-URL-only `.hyperlink`) -- ActionSetting itself
            # is fully run-compatible (its own constructor's type union
            # already includes CT_TextCharacterProperties, a run's own
            # `rPr` element type), confirmed empirically before relying
            # on it, matching Shape.click_action's own construction
            # pattern (`ActionSetting(cNvPr, self)` there).
            ActionSetting(matching_run._r.get_or_add_rPr(), matching_run).target_slide = (  # noqa: SLF001
                jump_target
            )
        else:
            matching_run.hyperlink.address = url
        prs.save(str(file_path))
        return {
            "path": self._scope.relative(file_path),
            "slide": slide,
            "shape_index": shape_index,
            "url": url,
            "text": text,
        }

    def check_pptx_delivery(self, path: str) -> dict[str, object]:
        import zipfile
        from collections import Counter

        file_path = self._check_readable(path)

        with zipfile.ZipFile(file_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            name_counts = Counter(info.filename for info in infos)
            duplicate_parts = sorted(name for name, count in name_counts.items() if count > 1)
            try:
                corrupt_member = archive.testzip()
            except (NotImplementedError, RuntimeError) as exc:
                corrupt_member = f"<unreadable: {exc}>"
            media_infos = [info for info in infos if info.filename.startswith("ppt/media/")]
            media_total_bytes = sum(info.file_size for info in media_infos)
            media_largest = [
                {"part": info.filename, "bytes": info.file_size}
                for info in sorted(media_infos, key=lambda info: info.file_size, reverse=True)[
                    :5
                ]
            ]

        try:
            pptx_report = _analyze_pptx_delivery(file_path)
        except Exception as exc:
            # A file broken enough that python-pptx itself can't fully
            # load/walk it (corrupt beyond just a duplicate/oversized
            # part -- python-pptx parses slide parts lazily, so this can
            # surface anywhere in _analyze_pptx_delivery, not only at
            # Presentation() construction) must still return whatever
            # the raw-zip pass above already found, rather than crashing
            # this read-only audit outright -- hit for real against a
            # deliberately-corrupted test fixture, not a hypothetical.
            advisories: list[str] = []
            if corrupt_member:
                advisories.append(f"ZIP integrity failed at {corrupt_member!r}.")
            if duplicate_parts:
                advisories.append(f"Duplicate package parts: {', '.join(duplicate_parts)}.")
            advisories.append(
                f"Could not fully analyze this file as a presentation "
                f"({type(exc).__name__}: {exc}) -- only the raw package-level checks "
                "below could run."
            )
            return {
                "path": self._scope.relative(file_path),
                "zip_integrity": "corrupt" if corrupt_member else "ok",
                "corrupt_member": corrupt_member,
                "duplicate_parts": duplicate_parts,
                "slide_count": None,
                "hidden_slides": None,
                "fonts": None,
                "media": {
                    "count": len(media_infos),
                    "total_bytes": media_total_bytes,
                    "largest": media_largest,
                },
                "motion": None,
                "advisories": advisories,
            }

        advisories = []
        if corrupt_member:
            advisories.append(f"ZIP integrity failed at {corrupt_member!r}.")
        if duplicate_parts:
            advisories.append(f"Duplicate package parts: {', '.join(duplicate_parts)}.")
        if pptx_report["hidden_slides"]:
            advisories.append(
                f"{len(pptx_report['hidden_slides'])} hidden slide(s) won't show "
                f"during a normal slideshow: {pptx_report['hidden_slides']}."
            )
        if pptx_report["unsafe_fonts"]:
            advisories.append(
                f"Font(s) not on the common cross-platform-safe list: "
                f"{', '.join(pptx_report['unsafe_fonts'])} -- may substitute or render "
                "differently on a machine that doesn't have them installed."
            )
        if media_total_bytes > _DELIVERY_MEDIA_ADVISORY_BYTES:
            advisories.append(
                f"Embedded media totals {media_total_bytes / 1_000_000:.1f}MB across "
                f"{len(media_infos)} file(s) -- consider compressing images before "
                "sending, especially by email."
            )

        return {
            "path": self._scope.relative(file_path),
            "zip_integrity": "corrupt" if corrupt_member else "ok",
            "corrupt_member": corrupt_member,
            "duplicate_parts": duplicate_parts,
            "slide_count": pptx_report["slide_count"],
            "hidden_slides": pptx_report["hidden_slides"],
            "fonts": {"used": pptx_report["fonts_used"], "unsafe": pptx_report["unsafe_fonts"]},
            "media": {
                "count": len(media_infos),
                "total_bytes": media_total_bytes,
                "largest": media_largest,
            },
            "motion": pptx_report["motion"],
            "advisories": advisories,
        }

    def read_pptx_theme_colors(self, path: str) -> dict[str, object]:
        from lxml import etree
        from pptx import Presentation
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT
        from pptx.oxml.ns import qn

        file_path = self._check_readable(path)
        prs = Presentation(str(file_path))
        master = prs.slide_masters[0]
        theme_part = master.part.part_related_by(RT.THEME)
        root = etree.fromstring(theme_part.blob)
        clr_scheme = root.find(f".//{qn('a:clrScheme')}")
        colors: dict[str, object] = dict.fromkeys(_THEME_SCHEME_SLOTS)
        if clr_scheme is not None:
            for slot in _THEME_SCHEME_SLOTS:
                colors[slot] = _read_scheme_color(clr_scheme, slot)
        return {"path": self._scope.relative(file_path), "colors": colors}

    @locked_by_path
    def edit_pptx_theme_colors(self, path: str, colors: str) -> dict[str, object]:
        from lxml import etree
        from pptx import Presentation
        from pptx.opc.constants import RELATIONSHIP_TYPE as RT
        from pptx.oxml.ns import qn

        tokens = _parse_theme_color_slots(colors)
        file_path = self._check_readable(path)
        prs = Presentation(str(file_path))
        for master in prs.slide_masters:
            theme_part = master.part.part_related_by(RT.THEME)
            root = etree.fromstring(theme_part.blob)
            clr_scheme = root.find(f".//{qn('a:clrScheme')}")
            if clr_scheme is None:
                continue
            for slot, hex_value in tokens.items():
                _set_scheme_color(clr_scheme, slot, hex_value)
            assert_ooxml_valid(root, "edit_pptx_theme_colors")
            theme_part._blob = etree.tostring(  # noqa: SLF001
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
        prs.save(str(file_path))
        return {"path": self._scope.relative(file_path), "colors": tokens}

    def read_pptx(self, path: str, slide: Optional[int] = None) -> str:  # noqa: UP045
        from pptx import Presentation

        file_path = self._check_readable(path)
        prs = Presentation(str(file_path))
        slide_count = len(prs.slides)
        if slide is not None:
            if not 1 <= slide <= slide_count:
                raise ValueError(f"Slide {slide} out of range: deck has {slide_count} slides")
            return self._render_slide(prs.slides[slide - 1])
        return "\n\n---\n\n".join(
            self._render_slide(s, with_heading=True) for s in prs.slides
        )

    def render_pptx_preview(self, path: str, max_slides: int = 8) -> dict[str, object]:
        from pptx import Presentation

        file_path = self._check_readable(path)
        state_dir = Path(self._state_dir) if self._state_dir is not None else None
        preview_names, skipped_reason = render_all_page_previews(
            file_path, state_dir, max_pages=max_slides
        )
        prs = Presentation(str(file_path))
        return {
            "preview_paths": preview_names,
            "preview_paths_csv": ",".join(preview_names),
            "preview_skipped_reason": skipped_reason,
            "text_overlap_warnings": _check_text_overlaps(prs),
            "slides_missing_visual_elements": _check_missing_visual_elements(prs),
            "low_contrast_warnings": _check_low_contrast(prs),
        }

    @staticmethod
    def _render_slide(slide: Any, with_heading: bool = False) -> str:
        title_shape = slide.shapes.title
        title = title_shape.text if title_shape is not None else ""
        title_shape_id = title_shape.shape_id if title_shape is not None else None
        lines = [f"## {title}"] if with_heading else []
        for shape in slide.shapes:
            if title_shape_id is not None and shape.shape_id == title_shape_id:
                continue
            if shape.has_table:
                for row in shape.table.rows:
                    lines.append("| " + " | ".join(cell.text for cell in row.cells) + " |")
            elif shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    if paragraph.text:
                        lines.append(f"- {paragraph.text}")
        return "\n".join(lines)


def build_presentation_tools(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    custom_templates_dir: str | Path | None = None,
    extra_readable: Sequence[str | Path] = (),
    extra_writable: Sequence[str | Path] = (),
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call, bound to `root`
    (plus any user-configured extra_readable/extra_writable directories)."""
    toolkit = PresentationToolkit(
        root,
        state_dir=state_dir,
        custom_templates_dir=custom_templates_dir,
        extra_readable=extra_readable,
        extra_writable=extra_writable,
    )

    # `Optional[int]`, not `int | None`: aisuite's Tools.__infer_from_signature
    # only unwraps typing.Optional (checks `get_origin(t) is Union`), and PEP
    # 604 `X | None` has origin `types.UnionType` instead -- it slips through
    # unwrapped and gets serialized as the literal string "int | None" for
    # the "type" field, which Gemini's strict OpenAPI-subset schema rejects.
    def read_pptx(path: str, slide: Optional[int] = None) -> str:  # noqa: UP045
        """Read a PowerPoint (.pptx) file under the workspace as markdown.

        Without `slide`, every slide is rendered as a `## <title>` heading
        (empty if the slide has none) followed by its bullets/table, joined
        by `---` -- a full deck overview in one call. With `slide` (1-based),
        only that slide is rendered -- use this to avoid spending context on
        slides you don't need in a large deck.

        Args:
            path: file to read, relative to the workspace root
            slide: 1-based slide number to read; omit to read every slide
        """
        return toolkit.read_pptx(path=path, slide=slide)

    def render_pptx_preview(path: str, max_slides: int = 8) -> dict[str, object]:
        """Render every slide of a PowerPoint file to an image so you (or
        review_work) can actually look at the rendered result, not just its
        text content. Call this after building/editing a deck with
        run_node_script, which -- unlike write_pptx -- returns no
        preview_path of its own; read_pptx alone can't catch a plain,
        undesigned slide, since bullet text reads the same whether or not
        the slide behind it looks good.

        Needs LibreOffice (`soffice`) and poppler-utils (`pdftoppm`)
        installed -- when either is missing, or conversion fails,
        `preview_paths` comes back empty and `preview_skipped_reason` says
        why; this is never an error.

        Pass the returned `preview_paths_csv` straight through as
        review_work's `preview_name` argument to have the reviewer look at
        every rendered slide in one call.

        Three more fields need no rendering at all, so they're always
        populated regardless of LibreOffice/poppler-utils: `text_overlap_warnings`
        flags pairs of text boxes on the same slide whose bounding boxes
        significantly overlap (a real, reproduced defect -- a subtitle box
        landing on top of the body text), `slides_missing_visual_elements`
        lists 1-based slide numbers with no picture/chart/table/colored
        shape at all, just plain text -- a slide's background color alone
        doesn't count -- and `low_contrast_warnings` flags text whose own
        color falls below WCAG's minimum contrast ratio against its
        resolved background (computed directly from the known colors, not
        a rendered pixel check -- so it only catches text/background pairs
        with an explicit solid color on both sides; text on a photo or a
        gradient needs the existing scrim guidance and a human/review_work
        look instead). All three are objective checks, not a design
        opinion: treat any hit as something to actually fix (move the
        overlapping box, add a shape/icon/image to a flagged slide, darken/
        lighten the text or its background) before calling the deck done,
        the same way you'd react to overflow_warnings.

        Args:
            path: the .pptx file to render, relative to the workspace root
            max_slides: cap on how many slides to render (default 8) -- a
                large deck's every slide isn't worth the token cost of
                reviewing each one
        """
        return toolkit.render_pptx_preview(path=path, max_slides=max_slides)

    def write_pptx(
        path: str,
        content: str,
        overwrite: bool = True,
        template_path: str = "",
        theme: str = "",
    ) -> dict[str, object]:
        """Create a PowerPoint (.pptx) file under the workspace from structured text.

        `content` is markdown where a line containing exactly `---` on its
        own separates slides. Within each slide: the first `#`/`##`/`###`
        heading becomes the slide title; `-`/`*` bullets and plain
        paragraphs become the slide body. A slide may have a `| a | b |`
        pipe-table instead of bullets, but not both -- put a table on its
        own slide. This is whole-file overwrite, same contract as
        write_docx/write_pdf.

        Good slide design (apply when writing `content`): short titles,
        one idea per slide, left-align body text (never centered), no
        decorative accent lines/bars under titles. If text overflows its
        slide, the response's `overflow_warnings` will say so (when
        LibreOffice is installed) -- shorten the content or split the slide
        and call write_pptx again.

        The response's `preview_path` (when LibreOffice is installed) names
        a rendered thumbnail of the first slide the user can see in the
        chat UI -- not something to fetch or parse yourself.

        With no `template_path`, the deck is real 16:9 widescreen (13.33in
        x 7.5in) -- python-pptx's own bare-default canvas is the legacy 4:3
        shape, which this corrects to what every modern PowerPoint deck
        actually uses. Chinese/Japanese/Korean text always renders in a
        real CJK typeface (Microsoft YaHei), not whatever fallback font
        the opening machine happens to pick.

        `template_path`, if given, is an existing .pptx file (e.g. one with
        the user's own branding/theme) to build the new deck on: its theme,
        fonts, and colors carry over, and its own slides are removed before
        the new ones from `content` are added. The template needs at least
        the standard "Title and Content" and "Title Only" layouts; a
        template missing those raises a clear error rather than a
        confusing one deep inside slide creation.

        `theme`, if given, gives a from-scratch deck (no `template_path`
        needed) a cohesive background/color/typography identity instead of
        the plain-white default -- comma-separated `key=value` tokens:
        `bg` (slide background), `text` (body/title text color), `surface`
        (a secondary background tone, e.g. for cards), `accent` (the
        color layout: icon-list/stat-callout slides use when they don't
        specify their own accent hex, plus hyperlinks), `heading_font`,
        `body_font`. Colors are 6-hex-digit, no `#`. Every key is
        optional. Example: `"bg=0F172A,text=F8FAFC,accent=38BDF8,
        heading_font=Georgia"` for a dark navy deck with a cyan accent.
        Applies to the whole deck (every slide, including ones added via
        `layout:` directives), not per-slide.

        `heading_font`/`body_font` pick which font PowerPoint itself
        renders with -- this tool's own `overflow_warnings` is only as
        accurate as that. Arial/Calibri/Cambria/Times New Roman/Courier
        New/Bookman Old Style/Century Schoolbook render true-to-width in
        both the overflow check *and* real Office (every common Office
        install has them, or a metric-compatible stand-in); anything else
        (Georgia, Trebuchet MS, Impact, Arial Black, Garamond, Consolas,
        Palatino Linotype -- and never Aptos, unreliable on old and new
        Office installs alike) renders correctly in real PowerPoint but
        makes `overflow_warnings` itself approximate, since whatever
        machine LibreOffice runs on for this check may substitute a
        different-width font for it -- size that font's own text
        containers with ~10% extra slack rather than trusting a clean
        overflow check at face value.

        Args:
            path: file to write, relative to the workspace root
            content: deck content in the slide-separated markdown described above
            overwrite: whether to replace the file if it already exists
            template_path: optional .pptx file to use as the deck's starting
                theme/template, relative to the workspace root
            theme: optional comma-separated color/font tokens giving a
                freeform deck a cohesive identity -- see above
        """
        return toolkit.write_pptx(
            path=path,
            content=content,
            overwrite=overwrite,
            template_path=template_path,
            theme=theme,
        )

    def fill_pptx_template(
        path: str, template_id: str, content: str, overwrite: bool = True
    ) -> dict[str, object]:
        """Create a PowerPoint (.pptx) file by filling content into one of
        coscribe's own bundled starter templates -- unlike write_pptx's
        template_path, which discards a template's own slide content and
        keeps only its theme/fonts, this keeps every one of the template's
        existing slides' actual designed visuals (backgrounds, decorative
        shapes, images) exactly as authored, and only writes your text into
        that slide's own title/body placeholder.

        Reach for this instead of write_pptx when the user wants a deck
        that looks like one of coscribe's own pre-designed templates, not
        one generated from scratch -- call load_skill("PPTX Slides") first
        to see the available template ids and each one's exact slide-by-
        slide shape.

        `content` uses the same `---`-separated-chunk convention as
        write_pptx. The chunk count no longer has to exactly match the
        template's fixed slide count: its repeatable content-slide design
        is duplicated or trimmed to fit however many chunks you give it,
        so a template scales to real content length. Its non-repeatable
        slides (title, closing) still need exactly one chunk each, in
        their fixed position (title first, closing last). Each chunk is
        title (first heading) + bullets/paragraphs only -- no `layout:
        <id>` directives and no tables (write_pptx handles both of
        those).

        Args:
            path: file to write, relative to the workspace root
            template_id: id of a bundled template (see load_skill("PPTX Slides"))
            content: deck content in the slide-separated markdown described above
            overwrite: whether to replace the file if it already exists
        """
        return toolkit.fill_pptx_template(
            path=path, template_id=template_id, content=content, overwrite=overwrite
        )

    def extract_pptx_template(
        source_path: str,
        template_id: str,
        name: str,
        description: str,
        overwrite: bool = False,
    ) -> dict[str, object]:
        """Distill a reusable template from an existing reference deck --
        e.g. one the user just attached (their own company's branded
        .pptx) -- so fill_pptx_template can reuse its exact design (theme
        colors, fonts, decorative shapes, master/layout structure) for
        new content afterward, instead of hand-rebuilding that design
        shape by shape or guessing at colors from a description.

        Classifies the reference deck's own real slides into the same
        three roles every bundled template already uses: the first slide
        is "title", the last slide is "closing" (only if the deck has
        more than one slide), everything in between is "content" (the
        only repeatable role). Every slide must already have a real,
        usable title and/or body placeholder -- a slide that doesn't
        raises a clear error naming which slide, rather than silently
        skipping it; redesign or remove that slide in the reference
        deck, or use write_pptx instead of a template for this deck.

        Once this returns, template_id is immediately usable as
        fill_pptx_template's own template_id argument in the same turn --
        no separate registration step, and it persists across future
        conversations too (it's saved alongside the bundled templates,
        just in your own local templates directory).

        Args:
            source_path: path to the reference .pptx already in the workspace
            template_id: a short id for the new template (lowercase
                letters/digits/hyphens, e.g. "acme-corp")
            name: a short human-readable name (e.g. "Acme Corp")
            description: one sentence describing the template's look
                (colors, mood)
            overwrite: if a template with this id already exists, replace
                it. Defaults to False -- raises instead.
        """
        return toolkit.extract_pptx_template(
            source_path=source_path,
            template_id=template_id,
            name=name,
            description=description,
            overwrite=overwrite,
        )

    def edit_pptx_text(
        path: str,
        slide: int,
        title: Optional[str] = None,  # noqa: UP045
        content: Optional[str] = None,  # noqa: UP045
        placeholder_index: int = 0,
    ) -> dict[str, object]:
        """Change what an existing slide *says*, without touching how it
        looks -- the tool for a real PowerPoint file the user already put
        in the workspace or uploaded (their own company template, a deck
        someone sent them), not just one of coscribe's own bundled
        templates (that's fill_pptx_template, which is restricted to
        those and needs the whole deck's content up front). This edits
        one slide's title and/or one content placeholder's text in place;
        the file's theme, every other slide, images, and any decorative
        shapes are left byte-for-byte as they were.

        Call read_pptx(path, slide) first to see what a slide currently
        says (and how many bullets/paragraphs are already there) before
        deciding what to replace it with.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to edit (read_pptx shows the count)
            title: new title text, completely replacing the slide's
                existing title. Omit to leave the title untouched. Raises
                if this slide has no title placeholder at all.
            content: new body text (markdown bullets/paragraphs -- same
                convention as write_pptx, but no tables and no `layout:`
                directives), completely replacing one content
                placeholder's existing text. Omit to leave all content
                placeholders on this slide untouched.
            placeholder_index: which content placeholder `content` targets,
                0-based in on-slide order -- almost every slide only has
                one, so leave this at 0 unless the slide has a "two
                column" layout with a second, independent content area you
                also want to change (call this again with
                placeholder_index=1 for the second one).
        """
        return toolkit.edit_pptx_text(
            path=path,
            slide=slide,
            title=title,
            content=content,
            placeholder_index=placeholder_index,
        )

    def delete_pptx_slide(path: str, slide: int) -> dict[str, object]:
        """Remove one slide from an existing PowerPoint (.pptx) file --
        every other slide, and their order, is untouched.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to delete
        """
        return toolkit.delete_pptx_slide(path=path, slide=slide)

    def duplicate_pptx_slide(
        path: str, slide: int, insert_at: Optional[int] = None  # noqa: UP045
    ) -> dict[str, object]:
        """Duplicate one slide in an existing PowerPoint (.pptx) file --
        every shape, its exact position/size/formatting, and every image
        on the source slide are copied. Speaker notes are not carried
        over to the copy.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to duplicate
            insert_at: 1-based position for the new copy. Omit to insert
                it immediately after the source slide.
        """
        return toolkit.duplicate_pptx_slide(path=path, slide=slide, insert_at=insert_at)

    def reorder_pptx_slide(path: str, slide: int, new_position: int) -> dict[str, object]:
        """Move one slide to a new position in an existing PowerPoint
        (.pptx) file -- every slide's own content is untouched, only the
        deck's slide order changes.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to move
            new_position: 1-based position to move it to
        """
        return toolkit.reorder_pptx_slide(path=path, slide=slide, new_position=new_position)

    def list_pptx_shapes(path: str, slide: int) -> dict[str, object]:
        """List every shape on one slide of an existing PowerPoint
        (.pptx) file -- index, type, position/size (inches), rotation, a
        short text preview, fill color (if it's a plain solid fill, as
        `"#RRGGBB"` or `"theme:ACCENT_1"`), whole-shape hyperlink if one
        is set, and whether it's real SmartArt (`is_smartart`) -- if so,
        `smartart_text` lists every node's own text, since neither this
        tool nor python-pptx can generate or edit real SmartArt (the
        layout algorithm lives in PowerPoint itself). See its text here,
        then delete_pptx_shape it and rebuild the idea with this file's
        other shape/icon/table tools if the user wants it replaced.

        Call this before edit_pptx_shape/delete_pptx_shape (or before
        editing a table's cells) on any slide you haven't already
        inspected -- the `shape_index` those tools need is this list's
        own `index`, and guessing it blind on a slide with several
        shapes risks editing the wrong one.

        Args:
            path: the .pptx file to inspect, relative to the workspace root
            slide: 1-based slide number to list shapes on
        """
        return toolkit.list_pptx_shapes(path=path, slide=slide)

    def delete_pptx_shape(path: str, slide: int, shape_index: int) -> dict[str, object]:
        """Remove one shape from an existing slide, in place -- every
        other shape and the rest of the deck are untouched. Works on any
        shape type (a plain autoshape/textbox, a picture, a table, a
        chart, or SmartArt).

        Call list_pptx_shapes first to find the right shape_index (this
        is that list's own `index`, 0-based) and confirm you're removing
        the right one -- this can't be undone by this tool. Real
        SmartArt's own node text (from that same list's `smartart_text`)
        is not preserved automatically; note it down before deleting if
        you'll need it to rebuild the idea with other shapes afterward.

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number the shape is on
            shape_index: 0-based shape order on that slide
        """
        return toolkit.delete_pptx_shape(path=path, slide=slide, shape_index=shape_index)

    def edit_pptx_shape(
        path: str,
        slide: int,
        shape_index: int,
        left_in: Optional[float] = None,  # noqa: UP045
        top_in: Optional[float] = None,  # noqa: UP045
        width_in: Optional[float] = None,  # noqa: UP045
        height_in: Optional[float] = None,  # noqa: UP045
        rotation: Optional[float] = None,  # noqa: UP045
        fill_color: str = "",
        fill_color_2: str = "",
        gradient_angle: Optional[float] = None,  # noqa: UP045
        text_color: str = "",
    ) -> dict[str, object]:
        """Move, resize, rotate, and/or recolor an existing shape (its
        fill and/or its text color) on an existing PowerPoint (.pptx)
        file's slide, without touching its text *content* or any other
        shape -- the tool for adjusting one element of an already-
        designed deck (a real uploaded template, or one coscribe already
        generated) rather than rebuilding the slide. A title/body
        placeholder is a shape like any other here -- find it via
        list_pptx_shapes and use text_color to recolor it.

        Call list_pptx_shapes(path, slide) first to find the right
        shape_index and confirm its current position/size/fill before
        changing it.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index the shape is on
            shape_index: which shape to edit, 0-based in on-slide order --
                this is list_pptx_shapes's own "index" field for this shape
            left_in: new horizontal position in inches from the slide's
                left edge. Omit to leave unchanged.
            top_in: new vertical position in inches from the slide's top
                edge. Omit to leave unchanged.
            width_in: new width in inches. Omit to leave unchanged.
            height_in: new height in inches. Omit to leave unchanged.
            rotation: new rotation in degrees clockwise (0-360). Omit to
                leave unchanged.
            fill_color: new solid fill color, 6-hex-digit, no '#' (e.g.
                "38BDF8"). Omit to leave the fill unchanged. Only applies
                to a shape that has a fill (not a table/chart). Alone,
                sets a plain solid fill.
            fill_color_2: second gradient stop -- give this alongside
                fill_color for a two-stop linear gradient fill instead of
                solid (fill_color is the first stop). Omit for a solid fill.
            gradient_angle: gradient direction in degrees (0 = left-to-
                right, 90 = top-to-bottom, increasing clockwise). Only
                applies alongside fill_color_2; omit to keep the default
                90-degree top-to-bottom gradient.
            text_color: new color for every run of text already in the
                shape, 6-hex-digit, no '#'. Omit to leave text color
                unchanged. Only applies to a shape with a text frame
                (not a table/chart/picture). This is the tool for "make
                this title/paragraph a different color" -- changing a
                deck's theme accent color (edit_pptx_theme_colors)
                usually does NOT recolor plain title/body text, since
                that text typically doesn't reference the theme's accent
                color at all.
        """
        return toolkit.edit_pptx_shape(
            path=path,
            slide=slide,
            shape_index=shape_index,
            left_in=left_in,
            top_in=top_in,
            width_in=width_in,
            height_in=height_in,
            rotation=rotation,
            fill_color=fill_color,
            fill_color_2=fill_color_2,
            gradient_angle=gradient_angle,
            text_color=text_color,
        )

    def replace_pptx_image(
        path: str, slide: int, shape_index: int, image_path: str
    ) -> dict[str, object]:
        """Swap an existing picture's image in place on an existing
        PowerPoint (.pptx) file's slide -- position, size, and crop are
        all kept exactly as they were, only the image itself changes.
        The tool for replacing a photo/icon already placed in a real
        uploaded deck (or one coscribe already generated) with a
        different one, without touching its placement.

        Call list_pptx_shapes(path, slide) first to find the picture's
        shape_index (is_picture: true).

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index the picture is on
            shape_index: which shape is the picture, 0-based in on-slide
                order -- this is list_pptx_shapes's own "index" field
            image_path: path to the new image file already on disk,
                relative to the workspace root. Raster formats only
                (PNG/JPEG/GIF/BMP/TIFF, same as add_pptx_image) -- not SVG.
        """
        return toolkit.replace_pptx_image(
            path=path, slide=slide, shape_index=shape_index, image_path=image_path
        )

    def list_pptx_shape_types() -> list[str]:
        """List every shape_type name add_pptx_shape accepts -- a
        curated subset of python-pptx's full preset-geometry shape enum
        (basic shapes, arrows, flowchart nodes, callouts). No arguments:
        this is a static list, not specific to any file or slide."""
        return toolkit.list_pptx_shape_types()

    def add_pptx_shape(
        path: str,
        slide: int,
        shape_type: str,
        left_in: float,
        top_in: float,
        width_in: float,
        height_in: float,
        text: str = "",
        fill_color: str = "",
        line_color: str = "",
    ) -> dict[str, object]:
        """Add a new preset-geometry shape (an arrow, flowchart node,
        callout, or basic shape) onto an existing PowerPoint (.pptx)
        file's slide, at an exact position/size -- for building a
        specific diagram (a process flow of arrows and boxes, a
        comparison of callouts) shape by shape, unlike write_pptx's own
        icon-list/stat-callout layouts, which position their own shapes
        automatically.

        Call list_pptx_shape_types() first to see the available names.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to add the shape to
            shape_type: one of list_pptx_shape_types()'s names, e.g.
                "RIGHT_ARROW", "FLOWCHART_DECISION", "ROUNDED_RECTANGLE"
            left_in: horizontal position in inches from the slide's left edge
            top_in: vertical position in inches from the slide's top edge
            width_in: shape width in inches
            height_in: shape height in inches
            text: optional text centered inside the shape (markdown
                **bold**/*italic* supported). Omit for no text.
            fill_color: optional solid fill color, 6-hex-digit, no '#'
                (e.g. "38BDF8"). Omit to keep the shape's default theme fill.
            line_color: optional outline color, 6-hex-digit, no '#'. Omit
                to keep the shape's default theme outline.
        """
        return toolkit.add_pptx_shape(
            path=path,
            slide=slide,
            shape_type=shape_type,
            left_in=left_in,
            top_in=top_in,
            width_in=width_in,
            height_in=height_in,
            text=text,
            fill_color=fill_color,
            line_color=line_color,
        )

    def add_pptx_formula(
        path: str,
        slide: int,
        latex: str,
        left_in: float,
        top_in: float,
        width_in: float,
        height_in: float,
        display: bool = True,
    ) -> dict[str, object]:
        """Add a real, natively-editable PowerPoint equation (the same
        Office Math object PowerPoint's own Insert > Equation creates)
        onto an existing PowerPoint (.pptx) file's slide, at an exact
        position/size -- for actual mathematical notation (fractions,
        roots, summations, matrices), not a text approximation typed
        with Unicode characters. Always creates a fresh text box
        containing exactly one formula, the same "new object at a
        position" shape as add_pptx_shape.

        `latex` is the documented Microsoft 365 LaTeX equation syntax --
        the same input PowerPoint's own equation editor accepts when you
        type LaTeX and press space. Unsupported/malformed LaTeX raises a
        clear error rather than a silent best-effort guess.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to add the equation to
            latex: the LaTeX source for the equation, e.g.
                "\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}"
            left_in: horizontal position in inches from the slide's left edge
            top_in: vertical position in inches from the slide's top edge
            width_in: text box width in inches
            height_in: text box height in inches
            display: True (default) for a centered, standalone block
                equation; False for a smaller inline-sized expression.
        """
        return toolkit.add_pptx_formula(
            path=path,
            slide=slide,
            latex=latex,
            left_in=left_in,
            top_in=top_in,
            width_in=width_in,
            height_in=height_in,
            display=display,
        )

    def crop_pptx_image(
        path: str,
        slide: int,
        shape_index: int,
        crop_left: float = 0.0,
        crop_right: float = 0.0,
        crop_top: float = 0.0,
        crop_bottom: float = 0.0,
    ) -> dict[str, object]:
        """Crop an existing picture shape on an existing PowerPoint
        (.pptx) file's slide, in place -- the shape's on-slide position/
        size are untouched, only which part of the image shows through
        inside that same box changes.

        Call list_pptx_shapes(path, slide) first to find the picture's
        shape_index (is_picture: true).

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index the picture is on
            shape_index: which shape is the picture, 0-based in on-slide
                order -- this is list_pptx_shapes's own "index" field
            crop_left: fraction (0.0-1.0) of the image's own width to trim
                from its left edge. Defaults to 0.0 (no crop).
            crop_right: fraction (0.0-1.0) of the image's own width to trim
                from its right edge. Defaults to 0.0 (no crop).
            crop_top: fraction (0.0-1.0) of the image's own height to trim
                from its top edge. Defaults to 0.0 (no crop).
            crop_bottom: fraction (0.0-1.0) of the image's own height to
                trim from its bottom edge. Defaults to 0.0 (no crop).
        """
        return toolkit.crop_pptx_image(
            path=path,
            slide=slide,
            shape_index=shape_index,
            crop_left=crop_left,
            crop_right=crop_right,
            crop_top=crop_top,
            crop_bottom=crop_bottom,
        )

    def list_pptx_icons() -> list[str]:
        """List every icon name add_pptx_icon accepts -- a curated
        subset of Lucide (https://lucide.dev, ISC license), not its
        full ~1500-icon set. No arguments: this is a static list, not
        specific to any file."""
        return toolkit.list_pptx_icons()

    def add_pptx_icon(
        path: str,
        slide: int,
        icon_name: str,
        left_in: float = 1.0,
        top_in: float = 1.0,
        size_in: float = 1.0,
        color: str = "1F2937",
    ) -> dict[str, object]:
        """Insert a real icon onto an existing slide in a PowerPoint
        (.pptx) file, as a new square picture -- unlike write_pptx's
        `layout: icon-list` (a glyph inside a plain colored circle),
        this is an actual recognizable pictogram (rasterized from
        Lucide). Use this to add a real icon to a slide you're editing,
        or alongside other shapes on a freshly-built one.

        Call list_pptx_icons() first if you're not sure icon_name is
        bundled -- an unknown name raises immediately.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index to add the icon to
            icon_name: one of list_pptx_icons()'s names, e.g. "check",
                "trending-up", "lightbulb"
            left_in: horizontal position in inches from the slide's left edge
            top_in: vertical position in inches from the slide's top edge
            size_in: width and height in inches (icons are square)
            color: 6-hex-digit fill color, no '#' (default a dark
                slate gray, "1F2937")
        """
        return toolkit.add_pptx_icon(
            path=path,
            slide=slide,
            icon_name=icon_name,
            left_in=left_in,
            top_in=top_in,
            size_in=size_in,
            color=color,
        )

    def recolor_pptx_icon(path: str, slide: int, shape_index: int, color: str) -> dict[str, object]:
        """Change the color of an icon or logo-like picture already on a
        slide, in place -- position, size, and crop are untouched, only
        the pixels change (same relationship-swap mechanism as
        `replace_pptx_image`).

        Works by re-coloring the shape's own *current* image from its
        existing alpha (transparency) channel, not by re-fetching a
        bundled icon by name -- so it recolors any icon `add_pptx_icon`
        placed (regardless of which one), and will also work on any
        other transparent-background picture with a real silhouette.
        It does **not** detect edges or shapes: every pixel that isn't
        already transparent becomes the new solid color. Using it on an
        opaque photo (no real transparency) will flatten the whole
        picture to one solid color block -- only use this on icons,
        logos, or other transparent-background pictograms.

        Call `list_pptx_shapes` first to find `shape_index`
        (`is_picture: true`).

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number the icon is on
            shape_index: 0-based shape order on that slide
            color: new 6-hex-digit fill color, no '#'
        """
        return toolkit.recolor_pptx_icon(
            path=path, slide=slide, shape_index=shape_index, color=color
        )

    def edit_pptx_table_cell(
        path: str, slide: int, shape_index: int, row: int, col: int, text: str
    ) -> dict[str, object]:
        """Replace one cell's text in an existing table on an existing
        PowerPoint (.pptx) file's slide, without touching any other cell
        or shape -- the tool for correcting/updating one value in an
        already-built table (a real uploaded template's table, or one
        coscribe already generated) instead of rebuilding the whole
        slide.

        Call list_pptx_shapes(path, slide) first to find the table's
        shape_index and its table_dimensions (rows/cols) -- row/col here
        are 0-based and must be within those dimensions.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index the table is on
            shape_index: which shape is the table, 0-based in on-slide
                order -- this is list_pptx_shapes's own "index" field
            row: 0-based row index of the cell to change
            col: 0-based column index of the cell to change
            text: new text for the cell, completely replacing its
                existing content
        """
        return toolkit.edit_pptx_table_cell(
            path=path, slide=slide, shape_index=shape_index, row=row, col=col, text=text
        )

    def merge_pptx_table_cells(
        path: str,
        slide: int,
        shape_index: int,
        start_row: int,
        start_col: int,
        end_row: int,
        end_col: int,
    ) -> dict[str, object]:
        """Merge a rectangular range of cells into one, in an existing
        table on an existing PowerPoint (.pptx) file's slide -- e.g. for
        a spanning header cell over several columns. Every merged-away
        cell's existing text is kept, appended as an extra paragraph
        into the resulting cell -- not discarded, so clear an unwanted
        cell's text with edit_pptx_table_cell first if you don't want it
        in the merged result.

        Call list_pptx_shapes(path, slide) first to find the table's
        shape_index and table_dimensions. Raises if the range already
        contains a merged cell.

        Args:
            path: path to an existing .pptx already in the workspace
            slide: 1-based slide index the table is on
            shape_index: which shape is the table, 0-based in on-slide
                order -- this is list_pptx_shapes's own "index" field
            start_row: 0-based row index of one corner of the range
            start_col: 0-based column index of one corner of the range
            end_row: 0-based row index of the opposite corner
            end_col: 0-based column index of the opposite corner
        """
        return toolkit.merge_pptx_table_cells(
            path=path,
            slide=slide,
            shape_index=shape_index,
            start_row=start_row,
            start_col=start_col,
            end_row=end_row,
            end_col=end_col,
        )

    def add_pptx_chart(
        path: str, slide: int, chart_type: str, data: str, title: str = ""
    ) -> dict[str, object]:
        """Add a chart to an existing slide in a PowerPoint (.pptx) file.

        `data` is a pipe-table (same convention as write_xlsx's content): the
        first column is the category axis, every other column is its own
        data series named from that column's header cell, e.g.:

            | Quarter | Revenue | Profit |
            | --- | --- | --- |
            | Q1 | 100 | 20 |
            | Q2 | 120 | 25 |

        `chart_type` is `"bar"`, `"line"`, or `"pie"` -- a pie chart takes
        exactly one data column (a second series has no meaningful
        rendering as pie slices).

        The response's `preview_path` (when LibreOffice is installed) names
        a rendered thumbnail of the slide the user can see in the chat UI --
        not something to fetch or parse yourself.

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number to add the chart to
            chart_type: "bar", "line", or "pie"
            data: pipe-table rows as described above
            title: optional chart title
        """
        return toolkit.add_pptx_chart(
            path=path, slide=slide, chart_type=chart_type, data=data, title=title
        )

    def add_pptx_image(
        path: str,
        slide: int,
        image_path: str,
        left: float = 1.0,
        top: float = 1.0,
        width: Optional[float] = None,  # noqa: UP045
        height: Optional[float] = None,  # noqa: UP045
    ) -> dict[str, object]:
        """Add a picture to an existing slide in a PowerPoint (.pptx) file.

        `image_path` must be a readable image file already on disk (e.g. one
        the user attached, or one generated by another tool). Position and
        size are in inches; `width`/`height` default to the image's native
        aspect ratio scaled from whichever one is given, or the image's
        actual size if neither is given (python-pptx's own default).

        Args:
            path: presentation file to modify, relative to the workspace root
            slide: 1-based slide number to add the image to
            image_path: image file to insert, relative to the workspace root
            left: distance from the slide's left edge, in inches
            top: distance from the slide's top edge, in inches
            width: image width in inches; omit to keep the native aspect ratio
            height: image height in inches; omit to keep the native aspect ratio
        """
        return toolkit.add_pptx_image(
            path=path,
            slide=slide,
            image_path=image_path,
            left=left,
            top=top,
            width=width,
            height=height,
        )

    def set_pptx_background_image(path: str, slide: int, image_path: str) -> dict[str, object]:
        """Set a full-bleed background image on one slide in a PowerPoint
        (.pptx) file, behind its existing title/content/other shapes.

        The image is cropped (not stretched) to cover the whole slide
        without distortion, the same way CSS `background-size: cover`
        works -- picked automatically from the image's own aspect ratio,
        nothing to configure. Use search_images + download_image first to
        find and fetch a candidate image, or point at one already in the
        workspace. Images sourced via search_images are NOT filtered by
        license -- treat a deck built with one as a draft, and say so if
        the user seems to be about to send or publish it, rather than
        silently treating the image as clear to use externally.

        Args:
            path: presentation file to modify, relative to the workspace root
            slide: 1-based slide number to set the background on
            image_path: image file to use, relative to the workspace root
        """
        return toolkit.set_pptx_background_image(path=path, slide=slide, image_path=image_path)

    def add_pptx_scrim(
        path: str, slide: int, opacity: float = 0.35, color: str = "000000"
    ) -> dict[str, object]:
        """Add a full-slide semi-transparent color layer to one slide in a
        PowerPoint (.pptx) file, between its background image (if any) and
        its other shapes -- keeps title/body text legible over a busy
        photo background instead of leaving it low-contrast.

        Not applied automatically by set_pptx_background_image -- add it
        only when text actually needs it (e.g. review_work flags a
        legibility problem after you set a background image), not as a
        blanket default on every background -- an unnecessary scrim reads
        as decorative filler the same way an unnecessary accent bar does.

        Pick `color` to contrast with the slide's actual text color, not
        its own default of black -- a dark scrim under dark title text
        barely helps (verified: still low-contrast against a busy photo);
        a light scrim (e.g. "FFFFFF") under dark text washes the photo out
        enough to read clearly while staying recognizable.

        Args:
            path: presentation file to modify, relative to the workspace root
            slide: 1-based slide number to add the scrim to
            opacity: 0.0 (fully transparent) to 1.0 (fully opaque); 0.35 is
                a reasonable starting point for dark text/light overlay or
                vice versa
            color: hex RGB color, no leading "#", e.g. "000000" for a dark
                scrim (light text over it) or "FFFFFF" for a light scrim
                (dark text over it)
        """
        return toolkit.add_pptx_scrim(path=path, slide=slide, opacity=opacity, color=color)

    def add_pptx_shape_effect(
        path: str,
        slide: int,
        shape_index: int,
        effect: str,
        color: str = "000000",
        opacity: float = 0.4,
        size_pt: float = 8.0,
        distance_pt: float = 4.0,
        direction: float = 45.0,
    ) -> dict[str, object]:
        """Apply a drop shadow, glow, or soft edge to one shape (any kind --
        autoshape, picture, connector) on a slide in a PowerPoint (.pptx)
        file.

        Calling this again with a *different* `effect` on the same shape
        adds that effect alongside whatever it already has (e.g. shadow,
        then soft_edge, leaves both). Calling it again with the *same*
        `effect` replaces that effect's own settings in place.
        `effect="none"` removes every effect this tool manages from the
        shape (color/opacity/size_pt/distance_pt/direction are ignored in
        that case).

        Args:
            path: presentation file to modify, relative to the workspace root
            slide: 1-based slide number the shape is on
            shape_index: which shape on that slide -- call list_pptx_shapes
                first to find it
            effect: "shadow", "glow", "soft_edge", or "none" to clear
            color: hex RGB color, no leading "#" -- shadow/glow only,
                ignored for soft_edge/none
            opacity: 0.0 (fully transparent) to 1.0 (fully opaque) --
                shadow/glow only
            size_pt: blur radius in points (shadow), glow radius (glow), or
                softening radius (soft_edge)
            distance_pt: how far the shadow is offset from the shape --
                shadow only, ignored otherwise
            direction: shadow offset direction in degrees, 0 = right,
                90 = down, increasing clockwise (PowerPoint's own angle
                convention) -- shadow only, ignored otherwise
        """
        return toolkit.add_pptx_shape_effect(
            path=path,
            slide=slide,
            shape_index=shape_index,
            effect=effect,
            color=color,
            opacity=opacity,
            size_pt=size_pt,
            distance_pt=distance_pt,
            direction=direction,
        )

    def set_pptx_notes(path: str, slide: int, notes: str) -> dict[str, object]:
        """Set the speaker notes for one slide in a PowerPoint (.pptx) file.

        Replaces that slide's notes entirely (not an append).

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number to set notes on
            notes: the full speaker-notes text for that slide
        """
        return toolkit.set_pptx_notes(path=path, slide=slide, notes=notes)

    def set_pptx_transition(
        path: str, slide: int, transition: str, duration: float = 1.0
    ) -> dict[str, object]:
        """Set the slide-change transition for one slide in a PowerPoint (.pptx) file.

        `transition` is any of PowerPoint's own real native transitions --
        call `list_pptx_transition_types()` for the full list (48 effects
        across PowerPoint's own Subtle/Exciting/Dynamic Content gallery
        categories -- "fade"/"push"/"wipe"/"morph"/"vortex"/"honeycomb"/
        "cube"/"page_curl", etc. -- plus a few legacy aliases), or "none".
        Each effect uses PowerPoint's own sensible default variant (e.g.
        "push" always enters from the right) -- no direction/shape/style
        customization yet. `duration` is in seconds and controls how long
        the transition animation takes when advancing *into* this slide.

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number to set the transition on
            transition: any name from list_pptx_transition_types(), or "none"
            duration: transition length in seconds
        """
        return toolkit.set_pptx_transition(
            path=path, slide=slide, transition=transition, duration=duration
        )

    def list_pptx_transition_types() -> list[str]:
        """List every `transition` name `set_pptx_transition` accepts --
        PowerPoint's own real native transition gallery (48 effects) plus
        8 legacy aliases. No arguments: this is a static list, not
        specific to any file."""
        return toolkit.list_pptx_transition_types()

    def list_pptx_animation_types() -> list[str]:
        """List every `animation` name `add_pptx_animation` accepts --
        PowerPoint's own real native animation gallery (203 presets across
        entrance/emphasis/exit/motion-path categories) plus 6 legacy
        aliases. No arguments: this is a static list, not specific to any
        file."""
        return toolkit.list_pptx_animation_types()

    def add_pptx_animation(
        path: str,
        slide: int,
        shape_index: int,
        animation: str,
        duration: float = 0.5,
        trigger: str = "on-click",
        delay: float = 0.0,
        by_paragraph: bool = False,
    ) -> dict[str, object]:
        """Add an animation to one shape on a slide.

        `shape_index` is 0-based, in the order shapes were added to the
        slide (the same convention `list_pptx_shapes`/`edit_pptx_shape`/
        `replace_pptx_image` use -- call `list_pptx_shapes` first to find
        it rather than guessing): for a slide from `write_pptx`, index 0
        is the title and index 1 is the body content/table; each later
        `add_pptx_image` or `add_pptx_chart` call on that slide appends
        one more shape after those.

        `animation` is any of PowerPoint's own 203 real animation
        presets -- call `list_pptx_animation_types()` for the full list,
        grouped by category (`"entrance_*"`: shape starts hidden, then
        reveals; `"exit_*"`: shape is visible, then hides; `"emphasis_*"`:
        shape stays visible throughout; `"path_*"`: shape moves along a
        motion path, then stays at the end position). The original six
        short names this tool exposed before the full catalog still work
        as aliases: `"fade"`->`"entrance_fade"`, `"fly-in"`->
        `"entrance_fly"`, `"exit-fade"`->`"exit_fade"`, `"exit-fly"`->
        `"exit_fly"`, `"emphasis-grow"`->`"emphasis_grow_shrink"`,
        `"emphasis-spin"`->`"emphasis_spin"`. A handful of presets (instant
        appear/disappear toggles, discrete state changes like changing a
        shape's font) aren't duration-adjustable in real PowerPoint either
        -- for those, `duration` is ignored and the result's own
        `duration_ms` reports what was actually used instead of silently
        pretending the requested value took effect.

        `trigger` is PowerPoint's own Animation Pane "Start" setting for
        this animation:
        - `"on-click"` (default): plays the next time the presenter
          advances past this slide -- one click, one animation. Calling
          this again with `trigger="on-click"` adds one more independent
          click group, played in the order you called them in.
        - `"with-previous"`: plays at the same time as whatever animation
          you most recently added to this slide, no extra click needed.
        - `"after-previous"`: plays automatically right when that
          previous animation finishes, no extra click needed -- use this
          to chain a sequence that auto-plays once the presenter clicks
          into the slide (the first animation in the chain can itself be
          `"after-previous"`; it just starts on slide entry, same as real
          PowerPoint). `delay` adds an extra gap (seconds) on top of
          whichever trigger you chose, e.g. a short pause between
          with-previous elements or between one after-previous step and
          the next.

        `by_paragraph=True` animates each of the shape's non-empty text
        paragraphs as its own step instead of animating the whole shape
        at once -- the standard way to reveal a bulleted list one bullet
        at a time. Combine with `trigger="on-click"` for one click per
        bullet, or `trigger="after-previous"` (with a small `delay`) to
        have the bullets cascade in automatically. Raises if the shape
        has no text frame or no non-empty paragraphs.

        This writes hand-built animation XML (python-pptx itself has no
        animation API) and is verified after saving to structurally match
        what was requested, but has only been tested against LibreOffice
        in this environment, not real PowerPoint -- treat it as best-
        effort, and have the user confirm it looks right in PowerPoint
        before relying on it for anything important. The by_paragraph
        paragraph-range targeting in particular is a standard, common
        ECMA-376 pattern but -- unlike the six base effects above -- has
        not been cross-checked against a captured real-PowerPoint sample.

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number the shape is on
            shape_index: 0-based shape order on that slide (see above)
            animation: one of list_pptx_animation_types()'s names, or one
                of the six legacy short aliases above
            duration: animation length in seconds (ignored for the few
                presets that aren't duration-adjustable -- see result's
                own duration_ms)
            trigger: "on-click", "with-previous", or "after-previous"
            delay: extra gap in seconds before this animation starts, on
                top of its trigger's own natural timing
            by_paragraph: animate each non-empty paragraph in the shape's
                text frame as its own step, instead of the whole shape
        """
        return toolkit.add_pptx_animation(
            path=path,
            slide=slide,
            shape_index=shape_index,
            animation=animation,
            duration=duration,
            trigger=trigger,
            delay=delay,
            by_paragraph=by_paragraph,
        )

    def add_pptx_audio(
        path: str,
        slide: int,
        audio_path: str,
        left_in: float = 0.3,
        top_in: float = 0.3,
        trigger: str = "auto",
        start_delay: float = 0.0,
        hidden: bool = False,
    ) -> dict[str, object]:
        """Embed an audio file (mp3/m4a/wav) on a slide -- background
        music, a voiceover/narration track, or a sound effect -- as a
        real PowerPoint media shape with a speaker icon, not just an
        attached file.

        `trigger="auto"` (default) starts playback automatically once the
        slide begins, `start_delay` seconds later (0 by default -- no
        wait). `trigger="on-click"` instead waits for the shape's own
        speaker icon to be clicked during the slideshow, matching
        PowerPoint's own "Start: On Click" option; `start_delay` is
        ignored for this trigger. Only one audio track's timing is set up
        per call -- call this again for a second track on the same or a
        different slide.

        `hidden=True` moves the speaker icon off the visible slide canvas
        instead of placing it at `(left_in, top_in)` -- use this for
        background music/narration nobody should see or accidentally
        click, keeping `trigger="auto"` so it still plays. Leave it
        `False` (the default) for a sound effect or narration the
        presenter might want to see and re-trigger manually.

        This writes hand-built OOXML (python-pptx's own `add_movie` only
        supports video, and reusing it as-is for audio would leave the
        wrong element name and relationship type on the shape) and is
        schema-validated before saving, but has only been tested against
        LibreOffice in this environment, not real PowerPoint -- treat it
        as best-effort, and have the user confirm playback sounds right
        in PowerPoint before relying on it for anything important.

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number to add the audio to
            audio_path: the audio file to embed, relative to the
                workspace root -- .mp3, .m4a, or .wav
            left_in: horizontal position of the speaker icon, in inches
                (ignored if hidden=True)
            top_in: vertical position of the speaker icon, in inches
                (ignored if hidden=True)
            trigger: "auto" or "on-click"
            start_delay: seconds after slide entry before playback starts
                (only used when trigger="auto")
            hidden: move the speaker icon off-canvas instead of placing
                it on the visible slide
        """
        return toolkit.add_pptx_audio(
            path=path,
            slide=slide,
            audio_path=audio_path,
            left_in=left_in,
            top_in=top_in,
            trigger=trigger,
            start_delay=start_delay,
            hidden=hidden,
        )

    def add_pptx_hyperlink(
        path: str, slide: int, shape_index: int, url: str, text: Optional[str] = None  # noqa: UP045
    ) -> dict[str, object]:
        """Add a clickable hyperlink to an external URL, either on a
        specific run of text or on a whole shape.

        Call `list_pptx_shapes` first to find `shape_index` (this is
        that list's own `index`, 0-based in on-slide order) and to see
        each shape's current `hyperlink` (None if it has none).

        If `text` is omitted, the *whole shape* becomes clickable (its
        `click_action` -- works on any shape: a picture, an icon, an
        autoshape, a table). If `text` is given, it must exactly match
        one whole text run's text within that shape's text frame (e.g.
        one word or phrase that has its own distinct formatting, or an
        entire short paragraph if it's all one run) -- the hyperlink is
        added to just that run, not the rest of the shape's text.
        Raises, listing the shape's actual run texts, if no run matches
        exactly; hyperlinking part of a run (a substring within it)
        isn't supported.

        `url` is either an external destination -- must start with
        "http://", "https://", "mailto:", or "ftp://" -- or `"#slide-N"`
        (1-based) to make this an *internal* "jump to slide N" navigation
        link instead, PowerPoint's own real click-action for this (not a
        generic external link to some internal address).

        This is pure `python-pptx` public API (`Run.hyperlink`/
        `Shape.click_action.hyperlink` for external URLs,
        `Shape.click_action.target_slide` for slide jumps), no
        hand-written XML.

        Args:
            path: file to modify, relative to the workspace root
            slide: 1-based slide number the shape is on
            shape_index: 0-based shape order on that slide (see above)
            url: destination URL (e.g. "https://example.com") or
                "#slide-N" to jump to slide N within this presentation
            text: exact text of one run to hyperlink; omit to hyperlink
                the whole shape instead
        """
        return toolkit.add_pptx_hyperlink(
            path=path, slide=slide, shape_index=shape_index, url=url, text=text
        )

    def check_pptx_delivery(path: str) -> dict[str, object]:
        """Read-only audit of a finished .pptx before sending it --
        package integrity, font portability, media footprint, hidden
        slides, and a motion summary. Doesn't render or open the file
        visually (pair with render_pptx_preview/review_work for that);
        this is about problems that only show up in the file's own
        structure.

        `zip_integrity`: `"ok"` or `"corrupt"` (with `corrupt_member`
        naming which internal part, if any, failed a raw ZIP CRC check --
        a genuinely broken file, not a cosmetic issue).
        `duplicate_parts`: internal part names that appear more than
        once in the archive (should never happen in a file this project
        's own tools produced; a real defect if it does).
        `slide_count`/`hidden_slides`: 1-based slide numbers marked
        hidden (won't show during a normal slideshow -- easy to leave
        behind by accident after duplicating/reordering slides).
        `fonts.used`/`fonts.unsafe`: every font name actually referenced
        (theme major/minor fonts plus any per-run override), and the
        subset not on a curated list of fonts that ship pre-installed on
        real Windows/Mac machines -- an unsafe font may silently
        substitute (changing line breaks/sizing) on a machine that
        doesn't have it.
        `media`: total embedded media size/count and the 5 largest files
        by byte size -- a quick way to see what's bloating the file
        before emailing it.
        `motion`: which 1-based slide numbers have a transition, a real
        object animation, or an add_pptx_audio-style audio track --
        useful to sanity-check a complex deck's own motion actually
        landed where expected.
        `advisories`: human-readable summary strings for anything above
        worth flagging; empty if nothing stood out.

        Args:
            path: file to inspect, relative to the workspace root
        """
        return toolkit.check_pptx_delivery(path=path)

    def read_pptx_theme_colors(path: str) -> dict[str, object]:
        """Read an existing .pptx file's real master theme color palette
        (its `<a:clrScheme>`) -- the same colors PowerPoint's own Design
        tab color picker edits, not any one slide's own shape colors.

        Returns all 12 real slots: `dk1`/`lt1` (the base dark/light
        pair most placeholder text and backgrounds are ultimately
        derived from), `dk2`/`lt2` (a secondary dark/light pair), `accent1`
        through `accent6`, and `hlink`/`folHlink` (hyperlink colors) --
        each a 6-hex-digit string (no `#`), or `None` if that slot is
        genuinely missing (shouldn't happen in a real, valid template).
        Call this before `edit_pptx_theme_colors` to see what's actually
        there rather than guessing which slot is "the primary blue."

        Args:
            path: file to read, relative to the workspace root
        """
        return toolkit.read_pptx_theme_colors(path=path)

    def edit_pptx_theme_colors(path: str, colors: str) -> dict[str, object]:
        """Change one or more of an existing .pptx file's real master
        theme colors in place -- the same edit PowerPoint's own Design
        tab "Customize Colors" dialog makes, not a single shape's fill.

        Because every placeholder/theme-color-referencing shape across
        every slide in the file draws from these 12 shared slots, this
        can genuinely re-color an entire already-designed deck's accent
        identity (or restyle a client's branded template) in one call --
        but for that exact reason it's also the most far-reaching edit
        in this whole tool file: call `read_pptx_theme_colors` first to
        see the current palette, and confirm with the user which slot
        (`accent1`? `dk1`?) actually corresponds to the color they mean
        before changing it, since slot names don't self-evidently map to
        "the color used in the title" without looking.

        Plain hardcoded-RGB shape fills (not created via this tool) are
        untouched -- only shapes/text that reference a theme color (via
        `theme_color`, or that inherit a template layout's own
        theme-linked placeholder styling) visibly change.

        `colors` is comma-separated `key=value` pairs naming real
        `<a:clrScheme>` slots directly (`dk1`, `lt1`, `dk2`, `lt2`,
        `accent1`-`accent6`, `hlink`, `folHlink`), each a 6-hex-digit
        color (no `#`) -- only the slots you name are changed, e.g.
        `"accent1=0F6B5C,accent2=C2410C"`.

        Args:
            path: file to modify, relative to the workspace root
            colors: comma-separated slot=hexcolor pairs, e.g.
                "accent1=0F6B5C,hlink=0F6B5C"
        """
        return toolkit.edit_pptx_theme_colors(path=path, colors=colors)

    return [
        tool_metadata(read_pptx, risk_category="READ", category="documents"),
        tool_metadata(render_pptx_preview, risk_category="READ", category="documents"),
        tool_metadata(write_pptx, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(fill_pptx_template, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(
            extract_pptx_template, risk_category="WRITE_LOCAL", category="documents"
        ),
        tool_metadata(edit_pptx_text, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(delete_pptx_slide, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(duplicate_pptx_slide, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(reorder_pptx_slide, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(list_pptx_shapes, risk_category="READ", category="documents"),
        tool_metadata(edit_pptx_shape, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(delete_pptx_shape, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(list_pptx_shape_types, risk_category="READ", category="documents"),
        tool_metadata(add_pptx_shape, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(add_pptx_formula, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(replace_pptx_image, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(crop_pptx_image, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(list_pptx_icons, risk_category="READ", category="documents"),
        tool_metadata(add_pptx_icon, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(recolor_pptx_icon, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(edit_pptx_table_cell, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(merge_pptx_table_cells, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(add_pptx_chart, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(add_pptx_image, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(
            set_pptx_background_image, risk_category="WRITE_LOCAL", category="documents"
        ),
        tool_metadata(add_pptx_scrim, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(add_pptx_shape_effect, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(set_pptx_notes, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(set_pptx_transition, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(
            list_pptx_transition_types, risk_category="READ", category="documents"
        ),
        tool_metadata(add_pptx_animation, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(
            list_pptx_animation_types, risk_category="READ", category="documents"
        ),
        tool_metadata(add_pptx_audio, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(add_pptx_hyperlink, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(check_pptx_delivery, risk_category="READ", category="documents"),
        tool_metadata(read_pptx_theme_colors, risk_category="READ", category="documents"),
        tool_metadata(edit_pptx_theme_colors, risk_category="WRITE_LOCAL", category="documents"),
    ]
