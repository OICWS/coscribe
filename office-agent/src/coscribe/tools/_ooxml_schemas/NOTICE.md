# Vendored schema files

`transitional/*.xsd` are Ecma International's own published XML Schema
files for **ECMA-376, 5th edition, Part 4 (Transitional Migration
Features)** -- the conformance class real-world `.pptx` files (including
every file PowerPoint, python-pptx, and pptxgenjs write) actually use, as
opposed to the rarely-used "Strict" class defined in Part 1.

Fetched directly from Ecma International's own site, not copied from any
third party's repackaging:

```
https://ecma-international.org/wp-content/uploads/ECMA-376-4_5th_edition_december_2016.zip
  -> OfficeOpenXML-XMLSchema-Transitional.zip
```

Ecma International publishes its standards for free public download
specifically so implementers can build conforming software against them;
these files are the standard's own schema definitions, not Anthropic's or
any other vendor's derived work -- see `_ooxml_validate.py` for how
they're used (structural validation of hand-written OOXML fragments
before they're allowed to reach a user's file).

`pml.xsd` (the presentationml schema `write_pptx`/`add_pptx_animation`/
etc. actually validate against) transitively imports a subset of the
other files here (drawingml, shared types, VML, math) -- the full
26-file set is kept together rather than hand-pruned, so a future schema
update can just replace the whole directory without re-deriving which
files are load-bearing.

`wml.xsd` (the wordprocessingml schema `write_docx`/`documents.py`
validate against, via `assert_wml_valid`) is used the same way, but has
one dependency the pml.xsd chain never exercised: `<xsd:import
namespace="http://www.w3.org/XML/1998/namespace"/>` -- needed because
`xml:space="preserve"` appears throughout real Word documents (every
`<w:t>`/`<w:instrText>` this codebase writes sets it). Ecma's own
published `wml.xsd` declares that import with **no `schemaLocation`**
(confirmed by reading the file -- not an omission on our part), so it
can't resolve on its own. `xml.xsd` here is **not** an Ecma/ECMA-376
file -- it's the W3C's own normative schema for the `xml:` namespace,
fetched directly from `https://www.w3.org/2001/xml.xsd` (the URI XML
Namespaces 1.0 itself names as this namespace's schema document,
freely published by the W3C for exactly this kind of import). It is
never referenced by editing the vendored `wml.xsd` on disk -- doing
that would make it no longer byte-identical to Ecma's own download --
instead `_ooxml_validate.py`'s `_compiled_schema` patches the missing
`schemaLocation` onto an in-memory copy of the parsed tree before
compiling, the same "operate on a copy, never the source" discipline
`_strip_mce_ignorable` already uses in that file.
