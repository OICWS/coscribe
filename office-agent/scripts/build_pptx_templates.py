"""Generates coscribe's bundled fill_pptx_template starter decks.

Run with `.venv/bin/python scripts/build_pptx_templates.py` to (re)build
every coscribe-original template under
src/coscribe/builtin_templates/pptx/<id>/template.pptx from scratch (the
one exception is "velis", a real third-party CC0 design fetched and
converted rather than built from python-pptx stock layouts -- see
`build_velis`). Kept as a real, re-runnable script (modern-block's
original template, shipped in 531c1b5, was authored by a one-off script
that wasn't kept -- `build_modern_block` below replaces that with a real,
version-controlled generator) so a template's design can be tweaked in
code and regenerated instead of hand-editing a binary .pptx.

Each template slide is built from one of python-pptx's own default-theme
stock layouts (so it has real TITLE/SUBTITLE/BODY/OBJECT-type
placeholders -- fill_pptx_template only ever writes into those, via
slide.shapes.title / _find_body_placeholder, see tools/presentations.py),
then styled by writing directly into each placeholder's own
<a:lstStyle><a:lvl1pPr><a:defRPr> element. That's deliberate, not
python-pptx's higher-level `.font` API: a placeholder's text_frame starts
with one bare empty paragraph and no runs, so there is nothing for
`.font` to attach to yet -- the lstStyle's level-1 default run properties
are what every future *added* paragraph (via fill_pptx_template's
_add_inline_runs, always run with no explicit per-run formatting) actually
inherits its size/weight/color from. This is exactly how the original
modern-block template achieves it (verified by inspecting its saved XML),
reused here as the one supported way to author a new template that fills
in correctly.

Decorative shapes are added *after* placeholders (append order is z-order
in OOXML) but deliberately positioned so their bounding box never
overlaps a TITLE/BODY/OBJECT/SUBTITLE placeholder's own box -- the same
_check_text_overlaps geometry check write_pptx runs on generated decks
would otherwise flag real (if template-authored, not model-authored)
overlapping text later. A full-bleed background color is set via
slide.background.fill, never an overlay rectangle, specifically to avoid
z-order fights with the placeholders it would otherwise have to sit
behind.
"""

from __future__ import annotations

import io
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.util import Emu

_TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "coscribe" / "builtin_templates" / "pptx"
)

_SLIDE_WIDTH = 12192000  # 13.333in, matches presentations.py's _WIDESCREEN_WIDTH_EMU
_SLIDE_HEIGHT = 6858000  # 7.5in
_TITLE_SLIDE_LAYOUT = 0  # CENTER_TITLE + SUBTITLE
_TITLE_AND_CONTENT_LAYOUT = 1  # TITLE + OBJECT

# "Velis" by Laurens R. Krol (https://github.com/lrkrol/powerpoint,
# lrk-slides-velis.potx), CC0 1.0 -- unlike minimal-light/bold-statement/
# modern-block (coscribe's own original designs), this is a real
# third-party template, fetched and rebuilt here rather than vendored as a
# binary so the provenance/license is auditable from source. See the
# written LICENSE file this script also produces.
_VELIS_SOURCE_URL = "https://raw.githubusercontent.com/lrkrol/powerpoint/master/lrk-slides-velis.potx"
_VELIS_LICENSE_TEXT = """\
Velis (lrk-slides-velis.potx)
by Laurens R. Krol (https://lrkrol.com)
Source: https://github.com/lrkrol/powerpoint

Licensed under CC0 1.0 Universal (Public Domain Dedication):
https://creativecommons.org/publicdomain/zero/1.0/

This coscribe-bundled template.pptx is derived from the original
lrk-slides-velis.potx: converted from PowerPoint-template (.potx) to
PowerPoint (.pptx) content type, reduced to 4 slides (title, content,
content, closing) built from the original's own "Presentation Title" and
"Title and Content" layouts, with each slide's unused secondary
placeholder (a logo slot on the title layout, a caption line on the
content layout) removed so coscribe's fill_pptx_template tooling has
exactly one title and one body placeholder per slide. No other visual
design elements were changed.
"""


def _fetch_velis_pptx() -> bytes:
    """Downloads the real .potx and rewrites its declared content type
    from ...presentationml.template.main+xml to
    ...presentationml.presentation.main+xml -- the only difference between
    a PowerPoint *template* and a PowerPoint *presentation* is this one
    string in [Content_Types].xml; the OOXML slide/layout/master/theme
    parts themselves are identical, so this is a lossless conversion, not
    a re-authoring. python-pptx's own `Presentation()` loader hard-rejects
    the template content type (see pptx/api.py's own ValueError), so
    fill_pptx_template (and this script) needs the .pptx content type to
    open it at all."""
    raw = urllib.request.urlopen(_VELIS_SOURCE_URL, timeout=30).read()  # noqa: S310
    with zipfile.ZipFile(io.BytesIO(raw)) as zin:
        names = zin.namelist()
        parts = {name: zin.read(name) for name in names}
    content_types = parts["[Content_Types].xml"].decode("utf-8")
    content_types = content_types.replace(
        "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    )
    parts["[Content_Types].xml"] = content_types.encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in names:
            zout.writestr(name, parts[name])
    return buffer.getvalue()


def _remove_placeholder(slide: Any, idx: int) -> None:
    """Removes one placeholder shape (by its layout `idx`) from a freshly
    `add_slide`d slide -- same generic "no public delete API, drop the
    element directly" idiom `delete_pptx_shape` uses (tools/presentations.py).
    Used to strip a layout's secondary placeholder that coscribe's own
    fill_pptx_template has no use for (see callers), so it doesn't linger
    as an empty, unstyled placeholder box."""
    for shape in list(slide.shapes):
        if shape.is_placeholder and shape.placeholder_format.idx == idx:
            shape._element.getparent().remove(shape._element)  # noqa: SLF001
            return
    raise ValueError(f"no placeholder with idx={idx} on this slide")


def build_velis() -> Any:
    """A real third-party CC0 design (see _VELIS_LICENSE_TEXT) -- a
    teal/sage/magenta palette, thin-rule title styling, A4-landscape
    proportions (its own original dimensions, left as authored rather than
    resized to the other 3 templates' 16:9 -- repositioning every shape in
    a hand-authored deck to fit a different aspect ratio would risk
    breaking its design, and a differently-proportioned option is genuine
    variety, not a defect). Layout 0 ("Presentation Title": CENTER_TITLE +
    SUBTITLE) is used for both the title and closing slides -- the same
    "same shape, different content" pattern minimal-light/bold-statement's
    own `_title_slide` already uses twice -- with its unused "Logo" OBJECT
    placeholder (idx 17) removed. Layout 4 ("Title and Content": TITLE +
    BODY + OBJECT) is used for the 2 content slides with its secondary
    BODY caption placeholder (idx 13, a thin "Slide Title" tagline strip
    under the real title -- confirmed via direct geometry inspection) removed,
    since fill_pptx_template's _find_body_placeholder always matches the
    *first* eligible placeholder in on-slide idx order and would otherwise
    put every bullet into that ~0.06in-tall strip instead of the real
    content area."""
    from pptx import Presentation as _Presentation

    source_bytes = _fetch_velis_pptx()
    prs = _Presentation(io.BytesIO(source_bytes))

    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    _remove_placeholder(title_slide, 17)

    for _ in range(2):
        content_slide = prs.slides.add_slide(prs.slide_layouts[4])
        _remove_placeholder(content_slide, 13)

    closing_slide = prs.slides.add_slide(prs.slide_layouts[0])
    _remove_placeholder(closing_slide, 17)

    return prs


def _new_presentation() -> Any:
    prs = Presentation()
    prs.slide_width = Emu(_SLIDE_WIDTH)
    prs.slide_height = Emu(_SLIDE_HEIGHT)
    return prs


def _set_default_style(
    placeholder: Any, *, size: int, bold: bool, color: str, align: str = "l"
) -> None:
    """Write `placeholder`'s text_frame lstStyle/lvl1pPr/defRPr directly --
    see module docstring for why this, not python-pptx's `.font` API."""
    tx_body = placeholder.text_frame._txBody  # noqa: SLF001 -- no public API for lstStyle
    lst_style = tx_body.find(qn("a:lstStyle"))
    for child in list(lst_style):
        lst_style.remove(child)
    lvl1 = lst_style.makeelement(qn("a:lvl1pPr"), {"algn": align})
    lst_style.append(lvl1)
    def_rpr = lvl1.makeelement(qn("a:defRPr"), {"sz": str(size), "b": "1" if bold else "0"})
    lvl1.append(def_rpr)
    solid_fill = def_rpr.makeelement(qn("a:solidFill"), {})
    def_rpr.append(solid_fill)
    srgb = solid_fill.makeelement(qn("a:srgbClr"), {"val": color})
    solid_fill.append(srgb)


def _set_background(slide: Any, color: str) -> None:
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(color)


def _rgb(color: str) -> Any:
    from pptx.dml.color import RGBColor

    return RGBColor.from_string(color)  # type: ignore[no-untyped-call]


def _add_shape(
    slide: Any, shape_type: int, left: int, top: int, width: int, height: int, color: str
) -> Any:
    shape = slide.shapes.add_shape(shape_type, Emu(left), Emu(top), Emu(width), Emu(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(color)
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _add_gradient_shape(
    slide: Any,
    shape_type: int,
    left: int,
    top: int,
    width: int,
    height: int,
    color_start: str,
    color_end: str,
    angle: float = 45,
) -> Any:
    """Same as `_add_shape` but a real two-stop gradient (same-hue tonal
    shift, not a rainbow) instead of a flat fill -- python-pptx's public
    `FillFormat.gradient()`/`.gradient_stops`/`.gradient_angle`, verified
    live to read back the exact colors/angle written. Adds real visual
    depth to a solid color block/bar/rule without changing its geometry
    at all, unlike a flat fill."""
    shape = _add_shape(slide, shape_type, left, top, width, height, color_start)
    shape.fill.gradient()
    stops = shape.fill.gradient_stops
    stops[0].color.rgb = _rgb(color_start)
    stops[1].color.rgb = _rgb(color_end)
    shape.fill.gradient_angle = angle
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _set_gradient_background(
    slide: Any, color_start: str, color_end: str, angle: float = 45
) -> None:
    slide.background.fill.gradient()
    stops = slide.background.fill.gradient_stops
    stops[0].color.rgb = _rgb(color_start)
    stops[1].color.rgb = _rgb(color_end)
    slide.background.fill.gradient_angle = angle


def _title_slide(
    prs: Any,
    *,
    bg: str | None,
    bg_end: str | None = None,
    title_color: str,
    subtitle_color: str,
    rule_color: str,
    rule_color_end: str | None = None,
) -> Any:
    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_SLIDE_LAYOUT])
    if bg is not None:
        if bg_end is not None:
            _set_gradient_background(slide, bg, bg_end)
        else:
            _set_background(slide, bg)
    title = slide.shapes.title
    subtitle = slide.placeholders[1]
    title.left, title.top, title.width, title.height = (
        Emu(914400),
        Emu(3200400),
        Emu(9906000),
        Emu(1143000),
    )
    subtitle.left, subtitle.top, subtitle.width, subtitle.height = (
        Emu(914400),
        Emu(4400400),
        Emu(9906000),
        Emu(685800),
    )
    _set_default_style(title, size=4000, bold=True, color=title_color)
    _set_default_style(subtitle, size=1800, bold=True, color=subtitle_color)
    if rule_color_end is not None:
        _add_gradient_shape(
            slide, MSO_SHAPE.RECTANGLE, 914400, 3017520, 1371600, 54864,
            rule_color, rule_color_end, angle=0,
        )
    else:
        _add_shape(slide, MSO_SHAPE.RECTANGLE, 914400, 3017520, 1371600, 54864, rule_color)
    return slide


def _content_slide(
    prs: Any,
    *,
    title_color: str,
    body_color: str,
    left_spine: str | None = None,
    left_spine_end: str | None = None,
    title_rule_color: str | None = None,
    title_rule_end: str | None = None,
) -> Any:
    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_AND_CONTENT_LAYOUT])
    content_left = 685800 if left_spine is not None else 609600
    content_width = 10088880 if left_spine is not None else 10163200
    title = slide.shapes.title
    body = slide.placeholders[1]
    title.left, title.top, title.width, title.height = (
        Emu(content_left),
        Emu(548640),
        Emu(content_width),
        Emu(914400),
    )
    body.left, body.top, body.width, body.height = (
        Emu(content_left),
        Emu(1645920),
        Emu(content_width),
        Emu(4754880),
    )
    _set_default_style(title, size=3200, bold=True, color=title_color)
    _set_default_style(body, size=1600, bold=False, color=body_color)
    if left_spine is not None:
        if left_spine_end is not None:
            _add_gradient_shape(
                slide, MSO_SHAPE.RECTANGLE, 0, 0, 182880, _SLIDE_HEIGHT,
                left_spine, left_spine_end, angle=90,
            )
        else:
            _add_shape(slide, MSO_SHAPE.RECTANGLE, 0, 0, 182880, _SLIDE_HEIGHT, left_spine)
    if title_rule_color is not None:
        # Same thin-rule motif _title_slide uses under its own title,
        # sized/positioned to fit the gap between this slide's own
        # title and body placeholders (1417638-1645920 EMU here) without
        # touching either -- keeps every content slide honoring the
        # "thin accent-color rule under each title" identity the
        # template's own manifest promises, not just title/closing
        # slides (a real, found-live gap: previously only _title_slide
        # added this rule at all).
        if title_rule_end is not None:
            _add_gradient_shape(
                slide, MSO_SHAPE.RECTANGLE, content_left, 1508760, 1371600, 54864,
                title_rule_color, title_rule_end, angle=0,
            )
        else:
            _add_shape(
                slide, MSO_SHAPE.RECTANGLE, content_left, 1508760, 1371600, 54864,
                title_rule_color,
            )
    return slide


def _modern_block_title_slide(
    prs: Any, *, text_left: int, text_width: int, block_left: int, block_width: int
) -> Any:
    navy, navy_light, block_color, block_color_light = "1E2761", "2A3A8F", "CADCFC", "EAF1FF"
    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_SLIDE_LAYOUT])
    _set_gradient_background(slide, navy, navy_light, angle=45)
    title = slide.shapes.title
    subtitle = slide.placeholders[1]
    title.left, title.top, title.width, title.height = (
        Emu(text_left),
        Emu(3566160),
        Emu(text_width),
        Emu(1280160),
    )
    subtitle.left, subtitle.top, subtitle.width, subtitle.height = (
        Emu(text_left),
        Emu(4937760),
        Emu(text_width),
        Emu(731520),
    )
    _set_default_style(title, size=4000, bold=True, color="FFFFFF")
    _set_default_style(subtitle, size=1800, bold=False, color=block_color)
    _add_gradient_shape(
        slide, MSO_SHAPE.RECTANGLE, block_left, 0, block_width, _SLIDE_HEIGHT,
        block_color, block_color_light, angle=45,
    )
    return slide


def _modern_block_content_slide(prs: Any) -> Any:
    navy, navy_light = "1E2761", "2A3A8F"
    slide = prs.slides.add_slide(prs.slide_layouts[_TITLE_AND_CONTENT_LAYOUT])
    title = slide.shapes.title
    body = slide.placeholders[1]
    title.left, title.top, title.width, title.height = (
        Emu(609600),
        Emu(274638),
        Emu(10972800),
        Emu(1143000),
    )
    body.left, body.top, body.width, body.height = (
        Emu(609600),
        Emu(1600200),
        Emu(10972800),
        Emu(4525963),
    )
    _set_default_style(title, size=3200, bold=True, color=navy)
    _set_default_style(body, size=1600, bold=False, color="2B2B2B")
    _add_gradient_shape(
        slide, MSO_SHAPE.RECTANGLE, 0, 6720840, _SLIDE_WIDTH, 137160,
        navy, navy_light, angle=0,
    )
    return slide


def build_modern_block() -> Any:
    """Solid color blocks, navy accent, no decorative lines -- a real,
    re-runnable generator (this template previously had none, hand-
    authored directly on the checked-in binary; geometry here is copied
    byte-for-byte from that original, verified by inspecting its saved
    XML, so this is a like-for-like replacement, not a redesign). Every
    flat block/bar from the original (navy background, light accent
    block, bottom bar) is now a subtle same-hue gradient instead --
    real depth via python-pptx's own gradient fill support, not a new
    decorative element."""
    prs = _new_presentation()
    _modern_block_title_slide(
        prs, text_left=853135, text_width=7315200, block_left=8534095, block_width=3657600
    )
    for _ in range(2):
        _modern_block_content_slide(prs)
    _modern_block_title_slide(
        prs, text_left=3657600, text_width=7680960, block_left=0, block_width=3047695
    )
    return prs


def build_minimal_light() -> Any:
    """White background, one thin accent rule per slide, no color blocks
    -- a quieter, more editorial look than modern-block's solid blocks.
    The rule is a subtle accent-to-lighter-tint gradient rather than a
    flat bar, a soft "fade" more in keeping with this template's own
    understated identity than a solid block would be."""
    accent = "0F6B5C"
    accent_light = "4FA890"
    prs = _new_presentation()
    _title_slide(
        prs, bg=None, title_color="1A1A1A", subtitle_color=accent,
        rule_color=accent, rule_color_end=accent_light,
    )
    for _ in range(2):
        _content_slide(
            prs, title_color="1A1A1A", body_color="2B2B2B",
            title_rule_color=accent, title_rule_end=accent_light,
        )
    _title_slide(
        prs, bg=None, title_color="1A1A1A", subtitle_color=accent,
        rule_color=accent, rule_color_end=accent_light,
    )
    return prs


def build_bold_statement() -> Any:
    """Dark full-bleed title/closing slides, a left accent spine on
    content slides -- higher-contrast, punchier than modern-block or
    minimal-light, for an exec-summary/keynote-style deck. The dark
    background fades to near-black (a soft vignette, the same technique
    real keynote-style decks use for depth) and the spine/rule fade to a
    deeper shade of the same accent, instead of both being flat fills."""
    accent = "C2410C"
    accent_deep = "7C2D12"
    dark = "111827"
    dark_deep = "000000"
    prs = _new_presentation()
    _title_slide(
        prs, bg=dark, bg_end=dark_deep, title_color="FFFFFF", subtitle_color=accent,
        rule_color=accent, rule_color_end=accent_deep,
    )
    for _ in range(2):
        _content_slide(
            prs, title_color="111827", body_color="2B2B2B",
            left_spine=accent, left_spine_end=accent_deep,
        )
    _title_slide(
        prs, bg=dark, bg_end=dark_deep, title_color="FFFFFF", subtitle_color=accent,
        rule_color=accent, rule_color_end=accent_deep,
    )
    return prs


def build_investor_pitch() -> Any:
    """Dark throughout (not just the title, unlike bold-statement's dark-
    cover/white-content split) with exactly one accent color, reserved --
    a real style tendency, not guessed: hugohe3/ppt-master's own
    `investor-pitch` design spec (commit `6e3ce9c5a3b994a0e223a14a0f7edd42fd0b9f5`,
    `templates/styles/investor-pitch/templates/design_spec.md`) describes
    its "dark-tech" visual default as "[c]ommit to a small range with one
    accent reserved for evidence -- the traction curve, the decisive
    number, the ask. Keep that accent rare". That spec is pure
    methodology/copy guidance (page-role vocabulary, evidence discipline)
    with no SVG or concrete palette of its own to port -- only the
    tendency (dark field, one rare accent, confident scale) is real,
    reusable design direction; the palette and every slide built here are
    coscribe-original, through this script's own python-pptx pipeline,
    not ppt-master's SVG/workspace one."""
    accent = "22D3EE"
    accent_deep = "0E7490"
    dark = "0B0F14"
    dark_deep = "000000"
    prs = _new_presentation()
    _title_slide(
        prs, bg=dark, bg_end=dark_deep, title_color="FFFFFF", subtitle_color=accent,
        rule_color=accent, rule_color_end=accent_deep,
    )
    for _ in range(2):
        slide = _content_slide(
            prs, title_color="FFFFFF", body_color="CBD5E1",
            title_rule_color=accent, title_rule_end=accent_deep,
        )
        _set_background(slide, dark)
    _title_slide(
        prs, bg=dark, bg_end=dark_deep, title_color="FFFFFF", subtitle_color=accent,
        rule_color=accent, rule_color_end=accent_deep,
    )
    return prs


def build_academic_research() -> Any:
    """Even more restrained than minimal-light: a single flat hairline
    (no gradient fade) a third as tall, and a muted slate accent instead
    of a saturated one -- hugohe3/ppt-master's own `academic-research`
    design spec (same commit/provenance as investor-pitch above,
    `templates/styles/academic-research/templates/design_spec.md`)
    describes its "swiss-minimal" visual default as "[e]ffectively none"
    decoration: "[h]airline rules, restrained emphasis... [a]void
    gradient backgrounds... and any device that adds visual weight
    without adding information" and a "plain, highly legible sans-serif".
    Same "tendency only, coscribe-original build" relationship to the
    source as investor-pitch."""
    slate = "475569"
    prs = _new_presentation()
    _title_slide(
        prs, bg=None, title_color="0F172A", subtitle_color=slate,
        rule_color=slate,
    )
    for _ in range(2):
        _content_slide(
            prs, title_color="0F172A", body_color="1E293B",
            title_rule_color=slate,
        )
    _title_slide(
        prs, bg=None, title_color="0F172A", subtitle_color=slate,
        rule_color=slate,
    )
    return prs


def build_product_launch() -> Any:
    """Near-black field, one vivid accent used only for the thin rule (not
    a color block), a much larger statement scale than any other bundled
    template's title -- hugohe3/ppt-master's own `product-launch` design
    spec (same commit/provenance as investor-pitch above,
    `templates/styles/product-launch/templates/design_spec.md`) describes
    its "photo-editorial" visual default as "[c]onfident sans-serif
    hierarchy with a large, well-spaced statement scale for reveals...
    [l]et scale contrast -- not ornament -- signal what matters" and
    "reserve one accent for the reveal... [d]o not use a semantic status
    color decoratively". Same "tendency only, coscribe-original build"
    relationship to the source as investor-pitch; no product photography
    is bundled or implied -- fill_pptx_template only ever fills text."""
    accent = "FB3A5D"
    accent_deep = "9F1239"
    dark = "0A0A0A"
    prs = _new_presentation()
    slide = _title_slide(
        prs, bg=None, title_color="FFFFFF", subtitle_color="A3A3A3",
        rule_color=accent, rule_color_end=accent_deep,
    )
    _set_background(slide, dark)
    _set_default_style(slide.shapes.title, size=4800, bold=True, color="FFFFFF")
    for _ in range(2):
        slide = _content_slide(
            prs, title_color="FFFFFF", body_color="D4D4D4",
            title_rule_color=accent, title_rule_end=accent_deep,
        )
        _set_background(slide, dark)
        _set_default_style(slide.shapes.title, size=3600, bold=True, color="FFFFFF")
    slide = _title_slide(
        prs, bg=None, title_color="FFFFFF", subtitle_color="A3A3A3",
        rule_color=accent, rule_color_end=accent_deep,
    )
    _set_background(slide, dark)
    _set_default_style(slide.shapes.title, size=4800, bold=True, color="FFFFFF")
    return prs


_BUILDERS = {
    "modern-block": build_modern_block,
    "minimal-light": build_minimal_light,
    "bold-statement": build_bold_statement,
    "velis": build_velis,
    "investor-pitch": build_investor_pitch,
    "academic-research": build_academic_research,
    "product-launch": build_product_launch,
}


def main() -> None:
    for template_id, builder in _BUILDERS.items():
        out_dir = _TEMPLATES_DIR / template_id
        out_dir.mkdir(parents=True, exist_ok=True)
        prs = builder()
        prs.save(str(out_dir / "template.pptx"))
        print(f"wrote {out_dir / 'template.pptx'} ({len(prs.slides)} slides)")
        if template_id == "velis":
            license_path = out_dir / "LICENSE"
            license_path.write_text(_VELIS_LICENSE_TEXT, encoding="utf-8")
            print(f"wrote {license_path}")


if __name__ == "__main__":
    main()
