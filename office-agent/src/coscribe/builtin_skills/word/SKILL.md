---
name: Word Documents
description: When and how to use write_docx's table-of-contents, comments, tracked-changes, and template features -- load before writing a substantial Word document.
---
Deeper guidance for `write_docx`/`read_docx` -- on top of what's already in
your base instructions (the markdown subset, and the "no partial editing,
rewrite the whole content and overwrite=True" rule).

## Table of contents

A line that is exactly `[TOC]` inserts a real Word TOC field, built from
the document's own heading levels (`#`/`##`/`###`). It only works if the
document actually uses headings for its sections -- a document written as
plain paragraphs with bold text standing in for headings produces an empty
or missing TOC, since Word has no way to tell that text is structural. For
any document long enough to want a TOC, use real `#`/`##`/`###` lines for
every section and subsection, not just for the ones that happen to need
one. The TOC shows placeholder page numbers until the user opens the file
in Word, which recalculates it automatically on open -- don't describe it
as broken or incomplete in your response.

## Comments vs. tracked changes -- pick the one that matches the ask

These are two different things and answer different requests:

- `{{comment: your comment text}}` at the end of a heading/paragraph/
  bullet/numbered-item line attaches a real Word comment anchored to that
  line, visible in Word's Comments pane. Use this for *feedback on* content
  that isn't changing -- a note, a question, a flag for the reader. Not
  recognized inside table cells.
- `track_changes=True` marks everything this call writes as a tracked
  insertion instead of final content, and (with `template_path`) marks the
  template's old body as a tracked deletion instead of removing it
  outright. Use this when the document itself should change and the
  *change* is what needs review -- a redline the user can accept or reject
  in Word.

Don't reach for `track_changes=True` when what's actually wanted is a
comment on unchanged text, or vice versa -- they produce visibly different
documents and Word review workflows expect the right one.

## Templates

`template_path` carries over an existing document's styles and page setup
(useful for matching a user's letterhead or house style) but its own body
content is removed (or tracked-deleted, with `track_changes=True`) before
your new content is added -- it is a *style* source, not something your
new content gets appended to. Tables in a template are always removed
outright, tracked changes or not, since table-level tracked deletions
aren't supported.

## Before finishing

For a document with real structure (a TOC, multiple sections, a template
applied, tracked changes), look at the rendered `preview_path` or consider
asking `review_work` to check it -- whether that extra step is worth it is
your judgment call for the specific document, not a fixed rule. A one-
paragraph memo doesn't need it; a multi-section report going to someone
else usually does.
