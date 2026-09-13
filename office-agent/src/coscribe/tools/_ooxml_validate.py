"""Structural OOXML schema validation for `presentations.py`'s hand-written
XML -- `set_pptx_transition`, `add_pptx_animation`, `edit_pptx_theme_colors`,
and the theme-blob edits inside `write_pptx`/`fill_pptx_template`
(`_apply_theme`/`_apply_cjk_font_fix`). Those are the only places this
package writes OOXML python-pptx has no public API for (see
presentations.py's own module docstring); this is the safety net a
schema-conformant library call doesn't need, catching a wrong-order or
missing-required-field mistake in hand-built XML that `prs.save()` +
LibreOffice's own more forgiving parser would silently let through.

`_ooxml_schemas/transitional/pml.xsd` (see that directory's NOTICE.md for
provenance) is the real presentationml schema real-world `.pptx` files --
PowerPoint's own, python-pptx's, pptxgenjs's -- conform to. Its own
`xsd:import` chain pulls in drawingml (`dml-main.xsd`, the same namespace
`<a:theme>` lives in), so one compiled schema validates both a `<p:sld>`
slide element and an `<a:theme>` theme-part root -- confirmed empirically,
not assumed: both validate clean against this same compiled object for a
real python-pptx-authored file, and the schema does reject real defects
(verified with a deliberately reordered `<p:transition>` before `<p:cSld>`,
which the ECMA-376 sequence requires the other way around).

Every call site in `presentations.py` runs this *before* the mutated
element is serialized back into the file being written -- `assert_valid`
raising here means the user's file was never touched, the same guarantee
`add_pptx_animation`'s own `_verify_animation_readback` already makes for
its own narrower, application-level check. This is a stronger, general
check on top of that one, not a replacement for it: readback confirms
*the specific animation we meant to add is there*; schema validation
confirms *the whole slide is still a schema-conformant OOXML document*,
catching classes of mistake the readback check was never designed to
notice (e.g. an attribute on an unrelated element, or a child added in
the wrong position elsewhere in the tree).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

_SCHEMA_DIR = Path(__file__).parent / "_ooxml_schemas" / "transitional"
_PML_XSD = _SCHEMA_DIR / "pml.xsd"

# ECMA-376 Part 3's Markup Compatibility and Extensibility namespace -- the
# vendored Part 4 (Transitional) schema this module validates against
# doesn't itself encode MCE's leniency (`CT_Slide`/`CT_SlideTransition`
# have no `xsd:anyAttribute` wildcard, confirmed by reading pml.xsd
# directly), because MCE processing is specified as a step a conformant
# consumer runs *before* schema validation, not something the base schema
# expresses. Real PowerPoint-authored files rely on exactly this: an
# extension attribute/element in a foreign namespace (e.g. `p14:dur` on
# `<p:transition>`, see presentations.py's `_set_slide_transition`) is
# valid OOXML only because its namespace prefix is declared ignorable via
# `mc:Ignorable` on an ancestor -- `_strip_mce_ignorable` below is this
# module's side of that same mechanism, so a real, deliberate extension
# doesn't get flagged as if it were a genuine schema defect.
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_MC_IGNORABLE = f"{{{_MC_NS}}}Ignorable"


@lru_cache(maxsize=1)
def _compiled_schema() -> Any:
    """Compiled once per process (~30ms measured), cached after that --
    every call site below shares this one object rather than recompiling
    per call."""
    from lxml import etree

    return etree.XMLSchema(etree.parse(str(_PML_XSD)))


def _strip_mce_ignorable(element: Any) -> Any:
    """Return a deep copy of `element` with every namespace listed in an
    `mc:Ignorable` declaration -- on `element` itself or any descendant --
    removed: the ignorable attributes stripped, elements in an ignorable
    namespace dropped along with their whole subtree, and the now-consumed
    `mc:Ignorable` attribute itself removed (it isn't part of the base
    schema's own attribute list either -- keeping it would just trade one
    false positive for another). Operates on a copy; the caller's real,
    in-memory element (the one about to be saved) is never touched by
    validation. A no-op copy when nothing declares any ignorable
    namespace, which is the common case for most of this file's writes."""
    import copy

    working = copy.deepcopy(element)

    # Every prefix->URI binding visible anywhere in the tree, not just at
    # whichever element carries `mc:Ignorable` -- a real OOXML part often
    # binds an extension prefix (e.g. "p14") on the specific descendant
    # that actually uses it (python-pptx/coscribe both do: see
    # `_set_slide_transition`'s own `nsmap={"p14": ...}` on the
    # `<p:transition>` child, not the `<p:sld>` root the Ignorable
    # declaration sits on) while the declaration itself lives higher up.
    # `mc:Ignorable`'s value is a plain space-separated token list, not a
    # QName an XML processor resolves positionally, so resolving each
    # token against the tree's full binding set (rather than strictly the
    # declaring element's own in-scope bindings) is what actually matches
    # real, valid OOXML content instead of rejecting it.
    all_bindings: dict[str, str] = {}
    for descendant in working.iter():
        all_bindings.update(descendant.nsmap)

    ignorable_namespaces: set[str] = set()
    for descendant in working.iter():
        tokens = descendant.get(_MC_IGNORABLE)
        if not tokens:
            continue
        ignorable_namespaces.update(
            all_bindings[prefix] for prefix in tokens.split() if prefix in all_bindings
        )
        del descendant.attrib[_MC_IGNORABLE]
    if not ignorable_namespaces:
        return working

    def _namespace_of(qname: str) -> str | None:
        return qname[1:].split("}", 1)[0] if qname.startswith("{") else None

    for descendant in list(working.iter()):
        parent = descendant.getparent()
        if parent is None:
            continue  # never remove the root itself
        if _namespace_of(descendant.tag) in ignorable_namespaces:
            parent.remove(descendant)
            continue
        for attr_name in list(descendant.attrib):
            if _namespace_of(attr_name) in ignorable_namespaces:
                del descendant.attrib[attr_name]
    return working


def ooxml_errors(element: Any) -> list[str]:
    """Validate an already-parsed lxml `element` (a `<p:sld>` or `<a:theme>`
    root, still living in memory) against the ECMA-376 Transitional schema,
    after stripping any content a declared `mc:Ignorable` namespace marks
    as extension content (see `_strip_mce_ignorable`). Returns
    human-readable error strings, empty if valid."""
    schema = _compiled_schema()
    if schema.validate(_strip_mce_ignorable(element)):
        return []
    return [str(error) for error in schema.error_log]


def assert_ooxml_valid(element: Any, context: str) -> None:
    """Raise `RuntimeError` if `element` fails schema validation. Call this
    strictly before the element (or the blob it's serialized into) is
    written to the real file -- every call site in `presentations.py` does,
    so the error message's "not modified" claim is always true, not
    aspirational."""
    errors = ooxml_errors(element)
    if not errors:
        return
    raise RuntimeError(
        f"{context}: the generated OOXML failed schema validation -- the file "
        f"was not modified. Errors:\n" + "\n".join(errors)
    )
