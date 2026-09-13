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
