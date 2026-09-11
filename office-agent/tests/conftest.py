import pytest


@pytest.fixture(autouse=True)
def _fast_libreoffice_by_default(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_pptx/fill_pptx_template/add_pptx_chart's overflow-QA step and
    write_xlsx's formula-recalculation step each unconditionally shell out
    to a real headless LibreOffice process, regardless of whether the test
    calling them cares about that step at all. That's correct behavior for
    the small number of tests marked `real_libreoffice` (they exist
    specifically to verify real LibreOffice conversion), but once soffice
    is actually installed and working (as it now is in this environment --
    see the SessionStart hook), it turns every ordinary content/geometry
    test that happens to call write_pptx/write_xlsx into a real, slow
    LibreOffice subprocess launch too, which is most of this suite.

    Short-circuits both to their already-well-tested "soffice unavailable"
    branch by default. A test that genuinely needs the real behavior opts
    back in with `@pytest.mark.real_libreoffice`, which still skips
    cleanly (via that file's own `_libreoffice_actually_works()`-style
    probe) in an environment where soffice isn't actually usable.
    """
    if request.node.get_closest_marker("real_libreoffice") is not None:
        return
    monkeypatch.setattr("coscribe.tools.presentations._render_to_pdf", lambda pptx_path: None)
    monkeypatch.setattr(
        "coscribe.tools.spreadsheets._recalc_xlsx",
        lambda file_path, timeout=None: {
            "status": "skipped",
            "skipped_reason": "disabled in the test suite for speed -- see tests/conftest.py",
        },
    )
