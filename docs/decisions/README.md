# Decision records

One short file per decision that a future session would otherwise reopen or
"fix" because it looks wrong. At most 40 lines: Context, Decision,
Consequences, Status. Name: `NNNN-slug.md`. A record is added in the PR that
makes the decision (or, for the first ones, writes down a decision that was
already made). A decision that changes gets a new record that supersedes the
old one; the old one stays, marked "Superseded by" in its status and in its row below. A record that makes
earlier wording false lists it on a last line, `Retired wording: "...", "..."`; the living
documents may not say it again (`tests/test_doc_freshness.py`).

| # | Decision |
|---|---|
| [0001](0001-fixed-cached-tool-list.md) | The agent's tool list is fixed; tools are deferred |
| [0002](0002-approvals-not-a-sandbox.md) | Approvals and risk tiers, not an OS sandbox (sandbox part superseded by 0021) |
| [0003](0003-secrets-promise.md) | Secrets: the model cannot see a value; nothing more is promised |
| [0004](0004-connectors-sign-in-once-in-the-browser.md) | Connectors sign in once, in the user's own browser |
| [0005](0005-code-is-a-second-runtime.md) | Code is a second runtime, never inside a fixed workflow (agent part superseded by 0028) |
| [0006](0006-changes-reach-main-through-pull-requests.md) | Changes reach `main` only through pull requests |
| [0007](0007-working-with-more-than-one-human.md) | Working with more than one human and their AI sessions |
| [0008](0008-conversations-can-message-each-other.md) | Conversations can message each other (opt-in replaced by 0023) |
| [0009](0009-a-structure-period-then-growth-for-ai-sessions.md) | A structure period, so AI sessions can keep up with growth to 1M lines |
| [0010](0010-office-files-are-compiled-from-whole-page-sources.md) | Office files are compiled from whole-page sources; open-source code is reused as owned copies |
| [0011](0011-workbooks-are-written-as-engine-neutral-blocks.md) | Workbooks are written as engine-neutral blocks; Excel engines come in stages (stage-1 engine superseded by 0012) |
| [0012](0012-the-product-does-not-depend-on-libreoffice.md) | The product does not depend on LibreOffice (what it forbids superseded by 0026) |
| [0013](0013-word-documents-gain-a-document-style-not-a-script-route.md) | Word documents gain a document style, not a script route |
| [0014](0014-office-files-are-shown-in-the-app-as-panels.md) | Office files are shown in the app as panels; changes to them go through the backend (narrowed by 0025 and 0027) |
| [0015](0015-what-coscribe-makes-is-an-artifact.md) | What coscribe makes is an artifact; Slides are positioned HTML sections (partly superseded by 0016) |
| [0016](0016-new-slides-are-svg-pages-edited-slides-become-sections.md) | New slides are written as SVG pages; a slide becomes an HTML section when it is first edited |
| [0017](0017-a-deck-has-a-design-system-its-slides-name-styles-from.md) | A deck has a design system; its slides name text styles from it |
| [0018](0018-sections-may-hold-containers-that-lay-out-their-children.md) | Sections may hold containers that lay out their children |
| [0019](0019-a-docs-artifact-is-the-docx-itself-edited-by-styles.md) | A Docs artifact is the .docx itself, edited in the app block by block, by styles not fonts (narrowed by 0025 and 0027) |
| [0020](0020-design-systems-are-a-library-the-user-keeps.md) | Design systems are a library the user keeps; a deck gets a copy |
| [0021](0021-scripts-run-in-an-appcontainer-held-to-the-folder-settings.md) | Scripts run in an AppContainer held to the folder settings (commands changed by 0022) |
| [0022](0022-commands-run-in-codexs-windows-sandbox.md) | Commands run in Codex's Windows sandbox |
| [0023](0023-every-conversation-can-read-the-others-unless-archived.md) | Every conversation can list, read and message the others unless they are archived |
| [0024](0024-word-documents-are-written-from-a-spec-compiled-onto-a-theme-and-checked.md) | Word documents are written from a spec, compiled onto a theme and checked (not adopted) |
| [0025](0025-the-doc-artifact-is-a-markdown-document-and-the-word-panel-is-frozen.md) | The Doc artifact is a markdown document, and the Word panel is frozen |
| [0026](0026-libreoffice-is-an-optional-engine-found-or-installed-on-request.md) | LibreOffice is an optional engine, found or installed on request |
| [0027](0027-an-artifact-is-what-coscribe-made-from-a-source-it-owns.md) | An artifact is what coscribe made from a source it owns |
| [0028](0028-a-workflow-step-chooses-its-autonomy.md) | A workflow step chooses its autonomy; the graph stays fixed (proposed) |
| [0029](0029-a-test-run-stops-before-a-step-that-cant-be-taken-back.md) | A test run stops before a step that can't be taken back (proposed) |
