# Plan: what is open now

Short on purpose. What shipped lives in [`docs/log/`](log/) (newest) and
[`office-agent/ROADMAP.md`](../office-agent/ROADMAP.md) (short index of the older history); why things
are the way they are lives in [`docs/decisions/`](decisions/) and
[`ARCHITECTURE.md`](../ARCHITECTURE.md). Update this file in the PR that
changes the picture; keep it under about 150 lines.

Last reviewed: 2026-10-15.

## Next, in order

00. **LibreOffice as an optional engine** (record 0026, which supersedes 0012 in what it forbids). Built in this order, one PR each: detection and the Settings › Capabilities card; render to PDF and page images, the Word page-level check and the reviewer reading those pages; the install button; preview and conversion of Office files without Microsoft Office.
000. **Settings search index guard.** Settings search lists its rows by hand in `frontend/src/components/settings/searchIndex.ts`; add a test that fails when a `SettingRow` label is missing from it.
0. **Structure period: done** (record 0009; features paused from 2026-10-11, only the maintainer lifts it; closing report `docs/metrics/2026-10-19-structure-period-close.md`; what stays open is listed there).
   Steps, each one PR or less, from `docs/assessments/2026-10-11-scale-to-1m-lines.md`:
   1. Done: the ratchet and the encoding lint rule; `check.sh` and CI enforce the ratchet (a worse number fails; `tests/test_ratchet.py` keeps that wired).
   2. Done: coverage measured in CI and the two 60-second tests fixed; pytest in parallel
      waits until the tests are shown independent. The coverage floor is 88 (CI: 88.58).
   3. Done: the stale references fixed and `tests/test_doc_references.py` keeps them fixed.
   4. Done: a snapshot of the tool schemas and the system prompt (`tests/tool_schemas.txt`).
   5. Reviews and "not verified" items recorded where they can be found: the PR template
      and the rules ask for an issue and a linked GitHub review (done). A ledger of issues
      for the not-verified items was dropped: the maintainer tests by using the app, and
      what a script can check on Windows goes into a Windows job in CI (step 13 of the
      assessment, brought forward: the next step after this one).
   6. A Windows job in CI (`.github/workflows/windows.yml`): nightly, report only; fix or
      fence what it shows, then make it required after two green weeks.
   7. Public interfaces and cards, one package per PR: `workflows` is the pilot (its card,
      its `__init__.py` as the interface, `tests/test_module_cards.py`); the next package
      follows if the pilot reads well.
   8. The ratchet hardened after the second review: `--update` only tightens (`--accept-worse`
      is the maintainer's), `check.sh` enforces it, `pyproject.toml` is a CODEOWNERS path and
      the Windows run is red when a test fails. Changed-line coverage stays report-only. Measured
      (issue #94, `docs/assessments/2026-10-13-diff-coverage.md`): of the last 20 PRs that changed
      `src/`, 17 measurable, 2 would fail at 80% (one test each), 6 at 90% (pure moves and a
      Windows-only branch). The maintainer decides whether to enforce.
   9. Golden-file tests (assessment step 9). Part 1 (issue #86): the pptx XML is stable once
      canonicalised; 5 decks have goldens (`office-agent/tests/test_office_golden.py`). Part 2
      (issue #89): 18 decks so far, images, charts, icons, shapes and every layout directive added (issue #93: the canonicaliser opens
      the workbook embedded with a chart and blanks its build time); the master, layouts and theme
      are kept once in a shared golden. Next: templates and SmartArt edits, then audio and CJK
      text. Part 3 (issue #402 item 7): docx has 16 goldens (structure edits and comment
      threads added), xlsx 11 workbooks (`tests/test_xlsx_golden.py`, `tests/xlsx_canonical.py`);
      more tools (pdf, `recalc_xlsx`) the same way.
   10. Approval decision table (issue #103): done in `tests/test_approval_decisions.py` (the
       precedence of the standing approvals, plan mode, the exec policy and asking; Auto mode's
       pause rules; the code module's allowances, including a damaged Settings > Code file; the
       question and draft tools) and `tests/test_approval_records.py` (cards and audit records).
       The reviewer's own side (what it is shown, each odd reply or error) is in the same table
       file.
   Batch 6 (#232), state on 2026-10-15:
   - Done: the Windows job exists (nightly, report only); mutation survivors #221 to #227 and
     #165; the browser-panel port (#209); package cards for every package; guardrails 9
     (security audit and Dependabot, major versions ignored), 10 (dead code ratchet) and 11
     (flaky tests reported, then quarantined with an end date).
   - Done: `conversation/session.py` is split (1,472 lines; hooks, folder and skill changes,
     the turn stream and the approval resume are their own modules).
   - Done: aisuite is no longer a dependency (#299): one `pip install` resolves, the hand-run
     google-genai step is gone from CI and the session hook. Gemini 3.x has effort levels
     again on langchain-google-genai 4.3 (`<4.4` until the google-genai pin can move, #402).
     Gemini 2.x levels via `thinking_budget` are not built: Google no longer serves 2.x to new keys
     (see `docs/log/2026-10-18-gemini-2x-and-libreoffice-inventory.md`).
   - Done: `tools/presentations.py` is a 83-line entry point (parts 1 to 11 of its split; the
     toolkit is `office/pptx/toolkit.py` and the topic mixins, the tool wrappers are
     `office/pptx/tool_wrappers.py`, 88 lines, plus five `wrappers_*.py` topic modules). It was one
     1,464-line function before part 11.
   - Done: `deep_imports` is 37 (exit criterion 40 or fewer; #298).
   - Open: the Windows job must be green 14 days before it is required. It was red on 10-14
     (#267: `os.replace` raised PermissionError in `tools/bookkeeping/tasks.py`; fixed 10-15). The first green
     nightly after the fix was run 23 on 10-15 (main at 8a08b0d), but 10-16 (5 tests, #339, fixed in #340)
     and 10-17 (5 tests, three of them in tests other sessions added that day; fixed in the PR that
     links it) and 10-18 (one test that bounded wall-clock time, 0.84 s against 0.4 s on the runner;
     fixed to wait on events) were red. Green nights followed on 10-19 to 10-23 (5 of 14). 10-24 was
     red because a test waited 10 s for a reply on a slow runner (the wait is now 30 s; #610,
     checked by a manual Windows run on the fix branch before it merged). The maintainer decided
     on 2026-10-24 that this night does not restart the count: it stays at 5 of 14, 10-24 is
     neither green nor red, and the next green scheduled night is the 6th. Green nights followed on
     10-25 and 10-26 (7 of 14). 10-27 was red on three tests added on 10-26 and 10-27: putting a
     kept Doc version back turned a CRLF file into LF (a product bug, fixed), and a LibreOffice
     route test ran a shell-script launcher (skipped on Windows like its sibling file). That failure
     is after the 10-24 exception, so the count restarts at 0 and the next green scheduled night is
     the 1st.
   - Done: `test_tool_schema_snapshot` is skipped on Python 3.13 (cloud containers), where docstring
     indentation changes every tool description; CI runs 3.11 and is the check (#252).
   - For the maintainer: switch on Dependabot security updates, secret scanning and push
     protection in the repository settings (listed in #263).

0a. **Structure phase 2: components** (#348, decision 0009): the component the unit a session works
   in (card, `__init__.py` interface, tests by path; slice <= 15 files, 150k tokens). Done: the
   component map and generated `CODEOWNERS` (#349); `tools` cut into `tools.office.{docx,xlsx,pdf,pptx}`
   (#350), `tools.exec` (#351), `tools.web` (#352) and `tools.bookkeeping` (item 1); a generated
   `protected` contract per package and a `layers` contract for the office formats, with 3 imports
   (3 of 42 lines) left to shrink (item 2, 6b: the pptx engine); `scripts/slice.py`
   and the discovery protocol in `AGENTS.md` (item 3,
   the measured sample changes are still open). Also done: long functions and fan-out split
   (item 4), `web/tests` split by path (item 5), frontend ratchet and knip guardrails (item 6),
   `docs/security/review-pack.md` (item 7), `scripts/review_pack.py` and
   `scripts/weekly_metrics.py` (item 8). Done: the fresh-session measurement
   (`docs/metrics/2026-10-17-fresh-session-slice.md`, all inside budget; open: a frontend card).
   Open: reading the first `audit.yml` run and the CodeQL alerts after 2026-10-19 (item 7). The next stage is Batch 8 (#388): Origin/Host validation (#390), the first audit and
   CodeQL read, the 281 "Not verified" items sorted (`docs/metrics/2026-10-17-not-verified-triage.md`:
   20 checks for a person), the fresh-session measurement, prompt-injection tests, small items.

   Batch 10 (#425, the last structure batch): item 1 done (a workbook with a cell note now gets its
   formula values), item 3 done (the ratchet was already enforced; now a test keeps it wired), item 4 done (the workflow spec lives in `workflow_spec`, below `tools`; the layers contract ignores nothing). item 5 closed with a reason and item 6 done (the inventory found no product call). Items 8 to 10 (Slides tests, added 10-18): item 8 done (a Playwright spec for the Slides panel, nightly in `e2e.yml`), item 9 done (a property test for arranging and undo), item 10 done (the editor's source functions tested in jsdom, judged by the server's parser). Item 2 done except the CodeQL alerts, which the maintainer reads; item 7 done (the closing report).
0b. **The office route** (decision 0010, `docs/assessments/2026-10-13-office-route.md`):
   new decks compiled from whole-page SVG sources, edits through the page source and upgraded
   object tools; open-source code reused as owned copies. Done: the evaluation and the baseline
   of today's route (`office-agent/evals/`), the engine's compiler (#141), its checks and folder
   build (#198, PR 1), the build tool and an `engine` route in the evaluation (PR 2), the
   measurement (PR 3: the engine wins new decks, design 3.97 against 3.11, at 0.94 times the
   cost), and new decks built from pages in the product (PR 4; a user's template and edits stay on
   today's tools), the PowerPoint instructions cut to what templates and edits need (prompt 22%
   shorter). PowerPoint part 2 so far: native charts and tables, the user's template built from
   pages, Windows font metrics, speaker notes, a design spec from ppt-master's styles, icons,
   SVG illustrations, talk-or-read density, the table and unit-number checks and a repair cap,
   a deck in half the time (#304: no preview loop, no reviewer round; about 6 minutes and 0.4
   yuan a 16-page deck), redesigning a user's deck (task 15: 5 minutes, every picture kept) and
   licensed photos. Edits inside the compiled groups work (tasks 07 and 11, about 2 minutes).
   PowerPoint is done for now; left, each only when a real case asks for it: a check of names
   (people, companies) against the sources, not only numbers; lifting a slide back to SVG
   (ppt-master's 20k-line PPTX-to-SVG code; task 15 showed a redesign does not need it); a
   group's own rotate and scale. Next: Excel, then Word, the same way: their baselines are done
   (#164: Excel PR #181, Word with it; the `current` route on eight tasks each), and the two
   defects they found are fixed (#193 column widths, #219; #197 Word contents list, #216).
   Excel research done (`docs/assessments/2026-10-15-excel-quality.md`): on two new open tasks
   a workbook written whole scored 4.1 on design against 2.8-3.2 for the many-small-tools
   route, at the same cost. Next: a `build_xlsx` tool writing blocks of cells with their
   styles in one call, as Claude for Excel's `set_cell_range` does (part 1 unused, part 2 the
   skill and the measurement), with print-layout, clipped-text and edit-style checks. Parts 1a, 1b and
   1c are done (1c: the ten single-purpose writers removed, tasks 11 and 12 added; 1d,
   showing a workbook inside the app, was tried and dropped: the user opens it in Excel): the model writes workbooks with write_xlsx_cells and modify_xlsx (design 4.18 on
   the open tasks against 2.88, at the same cost and two thirds of the time). Its engine is openpyxl, with IronCalc for formula values (no Excel needed); real Excel through
   COM, and an Office.js add-in, are later engines behind the same interface, chosen from real use.
   **No LibreOffice in the product** (decision 0012, #319): step 1 (no Office needed) is IronCalc
   for formula values and resvg for a built deck's page previews, PDF pages with PDFium; step 2
   (done; `scripts/check_office.py` passed every case with Microsoft Office on Windows, WPS
   not yet checked), conversion and the exact previews of Word, Excel and PowerPoint files
   through Office (COM), or WPS;
   step 3 (done), the LibreOffice code paths removed, LibreOffice kept for development and the
   evaluation.
   Word research done (`docs/assessments/2026-10-16-word-quality.md`): a document written whole by
   a script scored 4.2-4.3 on design against 3.7-3.9 for `write_docx`, with content level; the gap
   is layout `write_docx` cannot set (a shaded table header, an East Asian font, heading styles,
   page numbers), and the script route added facts and costs more. A new document without a
   template gets a house style compiled onto real Word styles (#325, decision 0013): design 4.0-4.2
   against 3.7 in the same judging, content no lower, cheaper; the gate's 4.2 design was missed by
   0.1 and the maintainer accepted it. Open: the open tasks' layout (09, 10).
   Restructuring a user's document (insert a section, a list, a table or a contents list; move or
   delete a section) is one `edit_docx_structure` call (#329): on task 07 about half the time and
   cost of the scripts it replaced. With `read_docx(view="structure")` (#331) the model checks a
   document without a script too: no script on tasks 07 and 11, 18-47 s a task.
   Instructions are changed by cutting first and measuring after: a shorter prompt that scores
   the same is the better one.
   Viewing the files a run produced, inside the app (Word review with its comments, slides,
   sheets): designs reviewed by the maintainer 2026-10-16. First the window's panels: the panel
   switches moved into the title bar (done), several panels open at once, arranged by
   dragging (#334, done), then the viewers as panels (decision 0014,
   `docs/assessments/2026-10-17-file-viewers.md`): the Word review tools and routes (done, #343),
   the Word panel (read-only, merged #346). Any panel can open in a window of its own (desktop).
   Direction changed 2026-10-17: what coscribe makes becomes an artifact (Slides first, then
   Docs, Design/Dashboard, Sheets last), edited in the app and exported to the Office format; the
   Slides design is settled (decision 0015, `docs/assessments/2026-10-17-slides-artifact.md`):
   positioned HTML sections. Step 1 (#392, done): the section language and its HTML → PPTX
   compiler (text, shapes, pictures, drawings, tables, charts, icons, transitions, build-ins), and
   a measurement against the SVG pages. Result, decision 0016: new slides stay SVG pages; a slide
   becomes a section when the user first edits it. Step 2 (#414, done): the SVG page → section
   converter (content unchanged) and decks that mix the two. The Slides panel, as the
   maintainer's mockups of 2026-10-17 draw it: read-only view, Present, Download and comments
   sent to coscribe (done, #420). Step 4, editing (#431): sections drawn by the server, the edit
   API, select/move/resize, text and the format bar, inserting, notes and properties, new slides
   and reorder, undo including coscribe's builds. The user's edits and undos are told to coscribe at
   the start of its next turn (#462, done). Containers (decision 0018) are in the model's Slides guide, so coscribe writes rows and
   columns of cards the user can then edit. A picture takes radius, border and opacity; pictures, shapes, text boxes and cards take an outer shadow (Effects). The chart panel offers type, stacking, title, legend, labels and series colours (and, since 2026-10-21, axis titles, the value range and number format). A human edit refused to be overwritten was judged not worth its code
   (a narrow window: an edit made while a turn runs). Plain shapes of an SVG page (rectangles, circles, lines) become the
   section's own shapes when the slide is first edited, so the user can select and style them; anything else stays a drawing
   with a note in the panel (user editing is for what is easy to edit, the rest goes through comments and the conversation).
   A line is dragged by its ends and may rise to the right (`data-direction="up"`).
   Step 5, opening the user's own .pptx for editing in the panel, is not planned (maintainer, 2026-10-21): a template
   is changed through the conversation, and the panel shows only decks coscribe built. A read-only view of any
   .pptx (through the office program's PDF) is done; its New slide, reorder and comment controls are not yet hidden. Artifact kinds the maintainer listed to come after
   Artifacts page (2026-10-21, issue 497): an Artifacts entry under New session opens a gallery of what the conversations
   made (.pptx, .docx and .xlsx, from a registry of what conversations created, `GET /api/artifacts`; an artifact outlives its conversation and opens in a new one rooted at its folder), as cards or as rows by
   day, with All and Pinned, search, a type filter, "Make something new" for Docs, Slides, Sheets and Dashboard (not yet
   makeable), and one file menu (card and panel name: Rename, Duplicate, Pin, Copy path, Delete to the recycle bin). Open: Version history for documents (the maintainer finds it useful; not yet),
   tools for a conversation to list and read existing artifacts (next), scheduled runs' files, dashboards, and pins kept on the server (now in the browser).
   New-conversation box in Artifacts (maintainer, 2026-10-22, agreed): choosing Docs, Slides, Sheets or Dashboard opens the conversation with a mode chip
   in the composer and a placeholder for that kind; the kind goes to coscribe as an instruction in the first message (what the user wants made and what coscribe
   should do), replacing the bare jump. Three steps: (1) the chips, placeholders and the first-message instruction, no design systems yet; (2) the design-system library
   (decision 0020, accepted: Settings > Design systems, Colors, Typography, Spacing, Corner radius, Assets, Overview; per user, a deck gets a copy; a checker flags what is
   off-system; made from a blank form, a "describe it" conversation of type Design System, or from a .pptx), with Design System as the fifth kind in Artifacts;
   (3) the Slides chip's choice of a system, "Make a new design system first" and "Manage design systems".
   Step 2 so far: the store and its routes (`tools/design_systems.py`, `/api/design-systems`) and Settings > Design systems with the blank form (list, new, edit, delete) are in; next assets, the "describe it" conversation, the .pptx extraction and the Design System kind in Artifacts.
   Step 3 is in: a Slides start can name a design system (the chip's menu, a note in the first message, a copy kept for the conversation, the deck beginning as that copy). Next: assets, the "describe it" conversation, the .pptx extraction, the Design System kind in Artifacts, and the checker that flags what is off-system (the checker is in, 2026-10-23: a build of a deck that began from a library system warns, page by page, of colors, fonts and text styles the system does not have; the rest is still next). Design systems are also cards on the Artifacts page (2026-10-23, issue 569): a kind of their own, opening Settings on the system, with their own menu (Rename, Duplicate, Pin, Delete) and a tile that opens the new-system form. Next, the "describe it" conversation: design note `docs/assessments/2026-10-23-describe-a-design-system.md`, accepted 2026-10-23; both parts are in (2026-10-23: the deferred tool `save_design_system` with its first-message note, the tile's Blank form / Describe it menu, the chip and the end-of-turn card), and a template .pptx is read by `read_pptx_design` (2026-10-26: colors, fonts, text sizes). Next: "Describe it" in Settings, and assets (logos, template thumbnails) kept with a system.
   Design (2026-10-22, issue 551, done: log `2026-10-22-design-artifact.md`): an `.html` page written by a conversation is a Design artifact, drawn in a sandboxed frame (card, panel, Export menu with HTML and PDF); a lone `.svg` is not an artifact. Dashboard steps 1 to 3 are in (#721: a page plus `name.dashboard/` datasets, served with the data and d3; the tile and the Dashboard skill; six briefs made and working with DeepSeek; where each figure came from, the data time and Refresh in the panel). Open: PNG/.ico export. Next (design note `docs/assessments/2026-10-29-design-and-dashboard.md`, accepted in its direction): Dashboard as a page plus registered datasets (every number shows its source), Design as a canvas of artboards, and a tool that shows coscribe its page. Design canvas: the source, the server, the canvas view and the Design skill writing canvases with shared variables are in (2026-10-30); the variables are edited in the panel and artboards show light or dark (2026-10-30); `look_at_page` is in (2026-10-30, #739: coscribe sees each page as the app draws it); links between artboards work in Play (2026-10-30); each artboard plays on the canvas or full screen with its own Play and Edit (2026-10-30); next the evals again with it, a shared part artboards include, text edited in place, a properties panel for a board's page (its elements as a tree, size, text, the CSS rules that apply, edits written back to the page), a Design start naming a library design system.
   Docs (2026-10-21, decision 0019 accepted; research in docs/assessments/2026-10-21-docx-editing.md; editor library Tiptap 3, built first then evaluated): the .docx itself, previewed and edited in the Word panel by styles (headings,
   bold, italic, lists, links, tables, images), only the changed blocks rewritten, the person's Word edits never overwritten. Order:
   block reader and writer, editor panel (done), the person's edits told to coscribe (done), pictures shown in the text and added from a file or paste (done), then divider and page break (done), picture size and alt text (done), undo of a change made behind the editor (done), Export as Word, PDF and Markdown (done; PDF through the print dialog, to check in the desktop app), the chat's end-of-turn file card and the chip with a hover card in a reply (done), undo across coscribe's turns. Version history is wanted, not in v1.
   Word documents are still written from markdown: a spec route (a head and components, decision 0024) was built, measured against markdown on 14 tasks and not adopted, `docs/assessments/2026-10-26-word-spec-gate.md`.
   Doc artifact (design note `docs/assessments/2026-10-26-doc-artifact.md`, decision 0025 accepted 2026-10-26): a markdown document people and coscribe edit together, comments for coscribe as on a deck, export to Markdown, PDF and Word; the Word panel is frozen. Steps 1 to 4 of six are in (`tools/office/doc`: the format, blocks with line ranges, block-level edits, the routes, kept versions, the note of what the person changed, comments anchored to their block, pictures, the Word export; `frontend/src/components/doc`: the editor with comments, callouts, charts and pictures) and the artifact listing and side folder (step 5, server); opening a Doc from a chip and exporting it (step 5, frontend) is in review, then the Docs skill (step 6). Routing measured 2026-10-29 (`docs/log/2026-10-29-doc-writing-rules.md`): open, a Doc still fills in plans and steps the brief did not give (a proposal's migration phases).
   Editing at the same time as coscribe (2026-10-22): a write tool refuses a Word, Excel or PowerPoint file the person has saved in the app since the model last read it, and the result tells the model to read again (`runtime_lg/edit_guard.py`, done). The Word editor that joined coscribe's change with the person's typing was removed (decision 0027); a Doc's editor does it its own way.
   Run in background (#510, 2026-10-22, design `docs/assessments/2026-10-22-run-in-background.md`, decided): a running `run_python_script` or `run_node_script` can be handed to the Background tasks panel. Done: the backend (`tools/exec/running.py`, `detach_command` on the socket), the button on the running row with its Playwright spec, and Stop ending the script a tool call waits on.
   Chat (maintainer, 2026-10-21, agreed, not started): (1) a tool row opens to plain "name: value" lines, `$ command` and its
   output for a command, a sentence for a file read or write, and a "Raw" fold for the JSON; (2) a Plan card: in Plan mode the
   conversation shows one live card (items ticking off, the commands folded inside it) that goes when the plan is done,
   beside the full view in the Plan page; design first, then build.
   Slides (2026-10-21): dashboard (HTML, CSS and JS with the data as an attachment), a plain single-file HTML artifact,
   a slides artifact edited online and exported to .pptx or PDF, a document artifact exported to Word, PDF or Markdown;
   the .pptx, .docx and .xlsx skills stay the way native Office files are made. The
   docx-preview actions: accept, reject, reply, resolve, comment, "Ask coscribe" on a selection and the reviewer
   name are in the panel (#473). Without a reviewer name, actions carry the computer's user name.

1. **Make the repository easy to join** (governance review, 2026-10-08).
   - Done: `main` protected by a ruleset (PR only, squash, `test` required),
     `scripts/check.sh` shared by CI and local, root `CLAUDE.md` and
     `AGENTS.md`, templates.
   - Done: `ROADMAP.md` compacted (old one archived in `docs/history/`), plan,
     decisions and log in place, package dependencies checked in CI.
   - Done: `conversation/session.py` and its helpers moved to `conversation/` (the CLI uses
     it; it no longer imports `cli` or FastAPI).
   - Done: the setup page, request models and provider catalog are out of `web/app.py`;
     every HTTP route is in `web/routes/` (one `router(state)` each).
   - Done: the two WebSocket routes are in `web/routes/ws.py`.
   - Done: the server tests are in `tests/web/`, one file per domain.
2. **Working with more than one person** (record 0007, accepted): kurtosisOC is a
   collaborator and an owner on every `.github/CODEOWNERS` line. Left: an admin turns on
   "require code owner review" in the repository ruleset (a settings change, not a PR).

3. **Startup and idle cost of the server** (#478): measured in `docs/metrics/2026-10-20-startup-cost.md`
   (about 1.9 s to ready, 135 MiB idle; the Office libraries are ~0.2 s and ~25 MiB of it). Next: lazy
   imports of those libraries with an import budget in the ratchet; the sidecar started earlier in the
   Electron shell.

4. **The folder settings as the real limit for scripts** (#560, kurtosisOC, all of it including
   the UI; decision 0021). Prototype in #561. Shipped: the AppContainer launcher, folder grants
   and the self-test (part 1), every script tool and background task running in it where the
   self-test passed (part 2), Edit environment as a list of pages with the conversation's
   folders, its Scripts page (fail closed, the audited per-conversation opt-in to run without
   isolation, the self-test result and re-test), "Read, write and delete" as a third folder
   setting with `delete_file` to the Recycle Bin, and Edit environment > Network (none, limited
   to listed domains, or full). Codex's commands are off (2026-10-23). A script's approval card
   says whether it runs isolated. Grants on folders no longer in use are taken off at start, and
   all of them at uninstall. Reads stay limited to the conversation's folders: no whole-profile
   switch, a read-only folder instead (2026-10-23). The scripts' Python is chosen in Settings >
   Environment from the ones found on the computer; coscribe's own (3.12) is downloaded only on
   request (#586). #560 is closed. Open: a PyPI mirror setting for networks that block PyPI.

5. **One place for what coscribe may run** (#588, kurtosisOC). Shipped: deleting the open
   conversation (#589), Settings › Capabilities (#590), named environments with the picker below
   the message box and the move of each conversation's own settings into them, scheduled tasks
   choosing an environment, commands in Codex's Windows sandbox (decision 0022, proposed: needs
   the maintainer's independent review), with the person's private folders hidden from it (#601),
   and the sandbox built into the desktop app, so it needs no Codex download (#607); a private
   folder holding a command's folder hides what is beside it rather than itself, so PowerShell
   starts there.
   Codex's own commands in the code module run in the same sandbox (#616).
   Open: hiding other
   drives' user folders.

6. **Secrets out of what scripts and commands inherit** (#667, then the proxy setting #666,
   kurtosisOC). Shipped: scripts and commands start from a list of names, the proxy without its
   password, not from the server's environment (#668); the commands Codex runs in the code
   module get no provider keys or proxy password (#670); the proxy setting in Settings, its
   password in the keychain (#672). Open: a company proxy on a real Windows machine (NTLM or
   Kerberos are not supported, Basic only).

7. **The `tools` interface** (#677, record 0009). Done: who uses the 230 names
   (`docs/metrics/2026-10-28-tools-interface-usage.md`). In review: the 10 unused names dropped and
   the size ratcheted (#680); the `tools` card and `slice.py` list only the names `tools` owns, the
   maintainer-side choice for B (#692); four fresh-session samples across a component edge and the
   card lines they missed (#681). Open: moving callers to import from the sub-components, which
   makes `deep_imports`, `fan_out` and `coordinator.py`'s size worse today and needs the metric to
   count a declared sub-component as an entry first.

8. **Workflows whose steps choose their autonomy** (#719, kurtosisOC; decision 0028). One PR
   per phase: P0 the record (done); P1 an agent step and an injected runner (P1a, the step in
   the spec and on the review card, and P1b, the engine running it through the conversation's
   runner, a sub-agent: done; an agent that ends silently no longer passes as done, in review;
   open there: a cost cap, an L3 step delegating); P2 the app (P2a,
   showing, editing and answering agent steps, done; P2b a read-only canvas, in review;
   then the run's statuses on it); P3
   step-by-step drafting with test cases and dry runs; P4 the repair layers (a step's exception
   handler, the run's investigation, a repair proposed as a fixed branch); P5 parallel steps,
   sub-workflows, triggers on events, state across runs, versions.

## Needs the maintainer's testing (could not be verified here)

- Sign-in of the 13 connectors added for the skill plugins (only the
  registration was tested), and of the 19 added before them.
- The guided setup for HubSpot and Google (Gmail, Calendar, Drive): follows the
  vendors' guides, tested only against a local OAuth server. Google's Workspace
  MCP servers are a Developer Preview; whether HubSpot accepts a `localhost`
  redirect is not documented.
- Secrets against a real Windows Credential Manager (scripted in `windows-more-checks.yml`;
  the Settings > Secrets screen stays by hand), and a real remote connector with a header from
  Settings > Secrets. The 20 checks for a person, with what is scripted: `docs/manual-checks.md`.
- The attachment cards and the new question-card colours in the desktop app; the
  thin scrollbar on pages in the built-in browser.
- The code module on real Windows.
- Decision 0028 (a workflow step chooses its autonomy) is still "proposed" while it is built in
  phases from #719: accept, change or reject it.

## Open problems, not reproduced or not solved

- Notion showed no tools for the maintainer; cause not found.
- The built-in browser panel opened during a connector's first connection
  (sign-in pages should open in the system browser); not reproduced.
- A Hugging Face connector call once hung a turn; a 120 s limit and an automatic
  reconnect were added, the cause is unknown.
- Slack is not offered: its MCP is said to be limited to Marketplace or internal
  apps (third-party source, not checked against Slack's docs).
- A sent message does not keep its attached files as cards; the file picker takes
  one file at a time.

## Later (wanted, not scheduled)

- The rest of `office-agent/README.md` (browser, MCP, hooks, Web UI, desktop) rewritten as the
  Files and Scripts sections were: what exists and where to read more, no copied parameters.

- A reasoning-effort choice for the user (#202): done (the scale and its mapping per model, the
  pill and slider, the evaluation on the same scale). Left: a look in the dark theme.
- The context panel: show the deferred system and MCP tools as their own rows (not counted in
  the window), and find why system tools read 38.2k at the start of a conversation while the
  bound tools measure about 4.9k tokens (`tests/tool_schemas.txt`).

- Require every review thread to be resolved before a pull request can merge (a ruleset
  setting): not needed at today's size; the maintainer decided on 2026-10-11 to wait.
- A second model family (Codex) reviewing invariant paths: when the second maintainer
  joins (decision 0009).

- Conversations reading and messaging each other (records 0008 and 0023, built: every non-archived conversation is reachable; the Edit environment dialog still has its old messages section until the redesign in issue 560 lands).

- A callback helper so a script can use a secret (needs its own local
  authentication).
- A terminal UI.
- Settings in Claude's grouping (2026-10-22): the code module under Capabilities (#537), and a
  Sessions category. Sessions has Prompt suggestions, background agents and archiving inactive conversations. Not built: a
  bypass-permissions gate (no such mode exists; a decision first). Desktop only, built: flash on
  attention and keep the computer awake while a turn runs (only the open conversation counts). Capabilities items that are not
  built: AI-powered artifacts, inline charts, a domain allowlist for the web tools.
