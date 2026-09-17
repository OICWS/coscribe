# Vendored file: `tracks.py`

`tracks.py` is vendored, essentially verbatim, from
[`SecurityRonin/docx-mcp`](https://github.com/SecurityRonin/docx-mcp),
pinned to commit `9c0c0b7694d8123e82fe6b7c480899890dca0695` (2026-07-29).
MIT-licensed -- the full license text is `LICENSE.docx-mcp` in this
directory (copied unmodified from that repository's own `LICENSE`).

## Why this file, and not the rest of the project

`docx-mcp` is a ~13,000-line, ~40-mixin MCP server built around its own
document lifecycle (`docx_mcp/document/base.py`'s `BaseMixin`: unzip a
`.docx` into a temp dir, parse every XML part, track which parts were
touched, repack on save, plus zip-bomb/zip-slip protection, auto-repair,
and versioned `.bak` files). Adopting that whole system would mean running
a second, parallel document-lifecycle architecture alongside coscribe's
own (`python-docx`'s own open/mutate-in-memory/`save()`, which
`write_docx` already uses) -- out of scope for what this vendoring is for.

`tracks.py` (the `TracksMixin` class) is the one piece worth taking as-is:
real, hard-won track-changes logic --- cascading context-anchored fuzzy
text matching (exact → context_before → context_after → doc-global
uniqueness), multi-run-spanning delete/insert with `rPr` inheritance,
word-level diff-minimised replace, and accept/reject scoped by author.
Reading its actual import graph, it depends on exactly three names from
`base.py` (the `W` namespace constant, `_now_iso`, `_preserve`) -- not the
lifecycle machinery -- so those three are extracted into `_constants.py`
(copied, not summarized) instead of vendoring `base.py` in full.

The five methods `TracksMixin` calls on `self` (`_require`, `_find_para`,
`_next_markup_id`, `_make_run`, `_mark`) are coscribe's own small adapter
(`documents.py`'s `_TrackedDocxHost`), wrapping an already-open
`python-docx` `Document` instead of docx-mcp's unzip/repack lifecycle --
coscribe already has the document open in memory by the time any of these
tools run, so there's nothing to unzip. Only `_require` and `_mark` are
genuinely adapter-specific (they touch docx-mcp's own `self._trees`/
`self._modified` in the original, which coscribe has no equivalent of and
doesn't need -- python-docx re-serializes the whole tree on `save()`
regardless of what was touched). `_find_para`, `_next_markup_id`, and
`_make_run` never actually read `self` in the original despite being
written as bound methods, so they're copied verbatim as plain functions
into `_constants.py` and bound via `staticmethod()` in the adapter --
proven collision-avoidance logic (`_next_markup_id` in particular checks
six different markup-id namespaces, not just ins/del, to stay safe if the
document already has coscribe's own comment anchors from `write_docx`),
not reinvented.

## What was changed

Only `tracks.py`'s one import line (`from .base import ...` →
`from ._constants import ...`). No logic was altered. `_constants.py`'s
contents (`W`, `W14`, `_now_iso`, `_preserve`, `_find_para`,
`_next_markup_id`, `_make_run`) are copied verbatim from
`docx_mcp/document/base.py` at the same pinned commit, with the three
methods that never read `self` turned into plain functions (identical
bodies).
