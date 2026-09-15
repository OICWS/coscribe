import re
from pathlib import Path

import pytest

from coscribe.tools.pptx_templates import (
    format_template_listing,
    load_builtin_templates,
    load_pptx_templates,
)


def _write_manifest(template_dir: Path, **fields: object) -> None:
    template_dir.mkdir(parents=True)
    lines = "\n".join(f"{key}: {value!r}" for key, value in fields.items())
    (template_dir / "template.yaml").write_text(lines, encoding="utf-8")


def _write_real_pptx(template_dir: Path, slide_count: int) -> None:
    from pptx import Presentation

    prs = Presentation()
    for _ in range(slide_count):
        prs.slides.add_slide(prs.slide_layouts[6])  # "Blank"
    prs.save(str(template_dir / "template.pptx"))


def test_load_builtin_templates_finds_the_shipped_templates() -> None:
    # Real content shipped with the package (src/coscribe/builtin_templates/),
    # same "only test that touches the real bundled files" pattern as
    # test_skills_tool.py's test_load_builtin_skills_finds_the_three_shipped_skills.
    templates = load_builtin_templates()

    assert {t.id for t in templates} == {
        "modern-block",
        "minimal-light",
        "bold-statement",
        "velis",
        "investor-pitch",
        "academic-research",
        "product-launch",
    }


def test_load_builtin_templates_manifest_fields_are_populated() -> None:
    templates = load_builtin_templates()

    assert templates
    for template in templates:
        assert template.name
        assert template.description
        assert template.accent
        assert template.slide_count == 4


def test_load_builtin_templates_slide_count_matches_real_pptx() -> None:
    from pptx import Presentation

    templates = load_builtin_templates()

    assert templates
    for template in templates:
        prs = Presentation(str(template.path))
        assert len(prs.slides) == template.slide_count


def test_load_builtin_templates_missing_dir_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coscribe.tools.pptx_templates as pptx_templates_module

    monkeypatch.setattr(
        pptx_templates_module, "_BUILTIN_TEMPLATES_DIR", tmp_path / "does-not-exist"
    )

    assert load_builtin_templates() == []


def test_load_builtin_templates_malformed_manifest_is_skipped_but_siblings_still_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coscribe.tools.pptx_templates as pptx_templates_module

    root = tmp_path / "pptx"
    _write_manifest(
        root / "broken",
        id="broken",
        name="Broken",
        description="missing slide_count",
        accent="000000",
    )
    _write_real_pptx(root / "broken", slide_count=1)
    _write_manifest(
        root / "good",
        id="good",
        name="Good",
        description="fine",
        accent="000000",
        slide_count=1,
        slide_roles=["content"],
    )
    _write_real_pptx(root / "good", slide_count=1)
    monkeypatch.setattr(pptx_templates_module, "_BUILTIN_TEMPLATES_DIR", root)

    templates = load_builtin_templates()

    assert [t.id for t in templates] == ["good"]


def test_load_pptx_templates_finds_templates_in_the_given_directory(tmp_path: Path) -> None:
    root = tmp_path / "custom_templates"
    _write_manifest(
        root / "acme",
        id="acme",
        name="Acme",
        description="a custom template",
        accent="112233",
        slide_count=1,
        slide_roles=["content"],
    )
    _write_real_pptx(root / "acme", slide_count=1)

    templates = load_pptx_templates(root)

    assert [t.id for t in templates] == ["acme"]


def test_load_pptx_templates_auto_creates_a_missing_directory(tmp_path: Path) -> None:
    root = tmp_path / "does-not-exist-yet"
    assert not root.exists()

    templates = load_pptx_templates(root)

    assert templates == []
    assert root.is_dir()  # create_if_missing=True, mirroring load_skills


def test_load_pptx_templates_is_separate_from_the_bundled_set(tmp_path: Path) -> None:
    """A custom templates directory never surfaces coscribe's own bundled
    templates and vice versa -- two genuinely separate sources, merged
    only by presentations.py's fill_pptx_template, not conflated here."""
    root = tmp_path / "custom_templates"
    _write_manifest(
        root / "acme", id="acme", name="Acme", description="x", accent="112233",
        slide_count=1, slide_roles=["content"],
    )
    _write_real_pptx(root / "acme", slide_count=1)

    custom_ids = {t.id for t in load_pptx_templates(root)}
    builtin_ids = {t.id for t in load_builtin_templates()}

    assert custom_ids == {"acme"}
    assert "acme" not in builtin_ids
    assert custom_ids.isdisjoint(builtin_ids)


def test_load_builtin_templates_slide_count_mismatch_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coscribe.tools.pptx_templates as pptx_templates_module

    root = tmp_path / "pptx"
    _write_manifest(
        root / "mismatch",
        id="mismatch",
        name="Mismatch",
        description="claims 3 slides",
        accent="000000",
        slide_count=3,
    )
    _write_real_pptx(root / "mismatch", slide_count=1)
    monkeypatch.setattr(pptx_templates_module, "_BUILTIN_TEMPLATES_DIR", root)

    assert load_builtin_templates() == []


def test_format_template_listing() -> None:
    templates = load_builtin_templates()

    listing = format_template_listing(templates)

    assert "modern-block" in listing
    assert "Modern Block" in listing


def test_load_builtin_templates_flags_real_aspect_ratio_from_the_file_itself() -> None:
    """Real, live user feedback: a bundled template ("velis") kept its own
    original A4-landscape proportions, not 16:9, and got picked for a
    plain deck request with no stated preference -- surprising, since
    16:9 is the expected default. `is_widescreen` is computed from the
    real .pptx's own slide_width/slide_height, not guessed from the id,
    so a future non-16:9 template addition is flagged automatically."""
    templates = {t.id: t for t in load_builtin_templates()}

    assert templates["modern-block"].is_widescreen is True
    assert templates["minimal-light"].is_widescreen is True
    assert templates["bold-statement"].is_widescreen is True
    assert templates["investor-pitch"].is_widescreen is True
    assert templates["academic-research"].is_widescreen is True
    assert templates["product-launch"].is_widescreen is True
    assert templates["velis"].is_widescreen is False


def test_format_template_listing_tags_aspect_ratio() -> None:
    templates = load_builtin_templates()

    listing = format_template_listing(templates)

    assert "modern-block" in listing
    assert re.search(r"modern-block:.*\[16:9\]", listing)
    assert re.search(r"velis:.*\[NOT 16:9\]", listing)


def test_every_bundled_template_has_no_geometric_text_overlaps_or_missing_visuals() -> None:
    """Structural QA for every real, shipped template -- runs the exact
    same geometry-only/color checks write_pptx runs on a model-generated
    deck (no LibreOffice needed, so this runs everywhere, unlike the
    overflow check). scripts/build_pptx_templates.py deliberately
    positions each template's decorative shapes to never overlap a
    placeholder's own box -- this is what actually proves that, for
    minimal-light and bold-statement specifically (modern-block predates
    that script and was hand-verified separately when it shipped).
    _check_low_contrast is included too -- every bundled template's own
    text/background color choices should already clear WCAG's minimum
    ratio; a hit here would mean a real design defect in a shipped
    template, not a false positive (confirmed live: all four pass with
    zero warnings before this assertion was added)."""
    from pptx import Presentation

    from coscribe.tools.presentations import (
        _check_low_contrast,
        _check_missing_visual_elements,
        _check_text_overlaps,
    )

    for template in load_builtin_templates():
        prs = Presentation(str(template.path))
        assert _check_text_overlaps(prs) == [], template.id
        assert _check_missing_visual_elements(prs) == [], template.id
        assert _check_low_contrast(prs) == [], template.id


def test_fill_pptx_template_round_trips_through_every_real_bundled_template(
    tmp_path: Path,
) -> None:
    """Behavioral proof that each real, shipped template (not a synthetic
    fixture -- see the other fill_pptx_template tests in
    test_presentations_tool.py for those) actually works through the real
    tool end to end: title/body content round-trips back out via
    read_pptx for every template currently bundled, one call per
    template.id, each with exactly as many '---'-separated chunks as
    that template's own slide_count (fill_pptx_template's own fixed-
    count constraint, see its docstring)."""
    from coscribe.tools.presentations import PresentationToolkit

    toolkit = PresentationToolkit(str(tmp_path))
    content = "\n---\n".join(
        [
            "# Title Slide",
            "## First Point\n- one\n- two",
            "## Second Point\n- three\n- four",
            "# Closing Slide",
        ]
    )
    for template in load_builtin_templates():
        assert template.slide_count == 4, (
            f"{template.id}: this test's fixed 4-chunk content assumes every "
            "bundled template has slide_count == 4 -- update the content above "
            "if that ever stops being true"
        )
        result = toolkit.fill_pptx_template(f"{template.id}.pptx", template.id, content)
        assert result["slide_count"] == 4
        read_back = toolkit.read_pptx(f"{template.id}.pptx")
        assert "Title Slide" in read_back
        assert "First Point" in read_back
        assert "Second Point" in read_back
        assert "Closing Slide" in read_back
