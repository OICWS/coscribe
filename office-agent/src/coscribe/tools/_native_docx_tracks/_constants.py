"""Namespace constants + four small pure-function helpers `tracks.py` needs,
extracted verbatim from `docx_mcp/document/base.py` (see NOTICE.md) rather
than vendoring the whole 519-line `BaseMixin` -- coscribe already has an
open `python-docx` document tree by the time these are needed, so it
doesn't need docx-mcp's own unzip/parse/repack lifecycle. `_find_para`,
`_next_markup_id`, and `_make_run` are methods in the original (`self.`
bound) but never actually read `self` -- copied here as plain functions,
identical logic, and `documents.py`'s host adapter binds them via
`staticmethod()`."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preserve(t_el: etree._Element, text: str) -> None:
    """Set text on a <w:t> or <w:delText> element with xml:space=preserve."""
    t_el.text = text
    t_el.set(XML_SPACE, "preserve")


def _find_para(root: etree._Element, para_id: str) -> etree._Element | None:
    for p in root.iter(f"{W}p"):
        if p.get(f"{W14}paraId") == para_id:
            return p
    return None


def _next_markup_id(doc: etree._Element) -> int:
    """Next available ID for ins/del/comment/bookmark markup."""
    max_id = 0
    for tag in (
        f"{W}ins",
        f"{W}del",
        f"{W}commentRangeStart",
        f"{W}commentRangeEnd",
        f"{W}bookmarkStart",
        f"{W}bookmarkEnd",
    ):
        for el in doc.iter(tag):
            eid = el.get(f"{W}id")
            if eid:
                with contextlib.suppress(ValueError):
                    max_id = max(max_id, int(eid))
    return max_id + 1


def _make_run(text: str, rpr_bytes: bytes | None) -> etree._Element:
    """Build a <w:r> element with optional copied rPr."""
    r = etree.Element(f"{W}r")
    if rpr_bytes:
        r.append(etree.fromstring(rpr_bytes))
    t = etree.SubElement(r, f"{W}t")
    _preserve(t, text)
    return r
