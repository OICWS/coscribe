# Office evaluation

Decision 0010 (`docs/decisions/0010-office-files-are-compiled-from-whole-page-sources.md`) says each
step of the office route is decided by measurement. This is the measurement: real tasks, sent to
coscribe's real agent, checked automatically, with cost and time recorded. Part 1 covers PowerPoint
(`--kind pptx`, the default); part 2 adds Excel (`--kind xlsx`) and Word (`--kind docx`), below. It calls a model, so it is run by hand
and is not part of CI; only its deterministic parts are tested (`tests/test_evals_pptx.py`,
`tests/test_evals_xlsx.py`, `tests/test_evals_docx.py`). It changes nothing under `src/`.

## Run

```bash
cd office-agent
export DEEPSEEK_API_KEY=...            # the default model is deepseek:deepseek-flash
python -m evals.run --tasks all --out evals/runs/baseline
python -m evals.run --tasks 01,07 --out evals/runs/try          # some tasks
python -m evals.run --check-only --out evals/runs/baseline      # re-check, no model calls
python -m evals.run --kind xlsx --out evals/runs/excel          # the Excel tasks
python -m evals.run --kind docx --out evals/runs/word           # the Word tasks
```

Long runs go off-peak on DeepSeek (weekdays 9:00-12:00 and 14:00-18:00 Beijing time cost double;
see `CLAUDE.md`). The default reasoning effort is `low` (`--effort none` leaves the model's own).
`evals/runs/` is gitignored.

## A task

`pptx/tasks/<NN-slug>/` holds:

- `brief.md`: what a user would type.
- `inputs/`: files copied into the task's fresh workspace (the brief names them).
- `expect.json`: `facts` (each must appear in the deck; a list inside is alternative spellings),
  `pages` (`[min, max]`), and optionally `absent` (text an edit must remove), `allowed_numbers`
  (numbers the deck may carry that no input has), `pictures_min`, `template` (an input deck whose
  layouts the result must keep).

`pptx/make_inputs.py` rebuilds the binary inputs (images, the deck to edit, the template copy).

## What a run writes

Under `--out`, one folder per task: `result.json` (status, wall time, model calls, input / cached /
output / reasoning tokens, cost, every tool call by name, tool errors, the agent's last message),
`checks.json`, `workspace/` (inputs and what the agent made) and `render/` (one PNG per page); and
`summary.md` / `summary.json` for the whole run.

How a session is built: `ChatSessionLG` with the configured model and the built-in skills, in a
throwaway workspace, `run_approval_mode = "skip"` (a scheduled run's tier: every gated call goes
ahead and nobody is asked). A question the agent puts to the user is answered with "decide it
yourself" and counted. Usage comes from the model's own responses, not from the UI events.

## Checks (`pptx_checks.py`)

| Check | What it says |
|---|---|
| `opens` | python-pptx opens the file |
| `facts` | every fact in `expect.json` is on a slide (tables, groups and chart data included); `absent` text is gone |
| `inventions` | numbers on slides that appear in no input (page numbers and integers up to 31 are skipped); a list to read, not a failure: a sum the model computed is listed too |
| `pages` | page count inside the range |
| `editability` | coscribe's own tools can work on the deck: every slide has a title that `read_pptx` shows, and `list_pptx_shapes` returns for every slide (a deck made of groups crashes it today: #135) |
| `render` | LibreOffice to PDF to one PNG per page |
| `layout` | coscribe's own QA: words past the page edge, overlapping text boxes, low contrast, slides with no visual element |
| `pictures`, `template` | the images were used; the user's template layouts were kept |

## Excel (`--kind xlsx`)

`xlsx/tasks/<NN-slug>/` has the same `brief.md`, `inputs/` and `expect.json`; `xlsx/make_inputs.py` rebuilds
the binary inputs. An address is `Sheet!B2` or `Sheet!B2:B13`. `expect.json` lists what the workbook must
hold (a key left out is not checked):

| Key | What it says |
|---|---|
| `facts`, `absent`, `allowed_numbers` | as for decks, over the text of every sheet after recalculation, dates as `YYYY-MM-DD` |
| `sheets` | these sheets exist |
| `cells` | `{"at", "value", "tol"?, "type"?}`: the value after LibreOffice recalculated a copy; numbers within `tol` (0.005), text exactly (case and spaces count), `"type": "date"` needs a real date |
| `formulas` / `constants` | every cell of these ranges is a formula / is not one: a total typed in as a number is the finding |
| `number_formats` | `{"at", "any_of"}`: the format contains one of the strings (`元`, `%`) |
| `bold` | these cells are bold |
| `used_range` | per sheet, the range nothing may be written outside of |
| `original`, `untouched`, `conditional_formats_min` | an edit: the input workbook; the styles (number format, font, fill, border, alignment) of these ranges are as the input had them, and its conditional formats and validations are still there |
| `probe` | `{"set": {address: value}, "expect": [cells]}`: inputs are changed on a copy, which is recalculated; a model of formulas moves, one of pasted numbers does not |
| `charts` | `{"kind", "title", "points_min", "axis_titles"}`: a chart of that kind with that title, plotting at least that many points |
| `validations` | `{"at", "values"}`: a dropdown list over every cell of the range offering exactly those values |

The `recalc` check records what LibreOffice found (formula count, error cells). The workbook is rendered the
same way as a deck, one PNG per printed page, for the judge. The styles compared fold the spellings
LibreOffice changes when it saves (`\-`, a quoted `"$"`, the alpha byte of a color), because the agent's own
`recalc_xlsx` saves through LibreOffice; what is left is a style that was lost.

`tests/xlsx_reference.py` holds a good answer to every task. A test recalculates each through LibreOffice
and requires all checks to pass, so a task nobody can satisfy, or an `expect.json` that rejects a right
answer, fails in the test run (it skips without LibreOffice).

## Word (`--kind docx`)

`docx/tasks/<NN-slug>/` has the same `brief.md`, `inputs/` and `expect.json`; `docx/make_inputs.py`
rebuilds the binary inputs. The text is read from the document's XML in two views: *accepted* (tracked
changes applied: what a reader sees after "Accept all", and what `facts` and `inventions` look at;
python-docx's `paragraph.text` leaves tracked insertions out) and *rejected* (every change undone). A
key left out of `expect.json` is not checked:

| Key | What it says |
|---|---|
| `facts`, `absent`, `allowed_numbers`, `pages` | as for decks; `pages` counts the pages LibreOffice prints |
| `headings` | `{"text", "level"?}` found in this order among the paragraphs in a Title or Heading style; bold text in a Normal paragraph is not a heading |
| `toc` | a real table-of-contents field is there, not the words "Table of contents" |
| `tables` | `{"rows_min", "cols_min", "header"?}`, each satisfied by a different table of the document |
| `images_min`, `image_fit` | enough pictures; none wider than the text area (page width minus margins) |
| `captions` | `{"style", "starts_with", "below_image"?}`: a paragraph in that style, directly under a picture |
| `tracked` | `{"min_ins", "min_del", "rejected_equals_original"}`: the edit is a redline, and rejecting all of it gives the input's text back, so nothing was changed without a mark |
| `original` + `styles`, `header_has`, `footer_has`, `style_uses`, `keep_styles` | the user's file is still the base: its styles are defined, its header and footer text is there, the text is set in its styles (at least that many paragraphs) and every style the original used is still used |

`markdown` runs on every task: `#`, `**`, `-` bullets, table rules or backticks typed into the text
instead of applied as formatting, the sign of markdown pasted into paragraphs. `inventions` lists the
numbers in the document that no input has, per paragraph, as for decks.

`tests/docx_reference.py` holds a good answer to every task (built with python-docx, the tracked changes
by hand in XML). A test runs each through the checks, and a second one, skipped without LibreOffice,
counts the pages for real; so a task nobody can satisfy, or an `expect.json` that rejects a right answer,
fails in the test run. Tests that break one thing in a good answer (a flattened table, a bold-only
heading, a stripped header, a too-wide picture) show that each check can fail.

## The blind judge (`judge.py`)

```bash
python -m evals.judge evals/runs/baseline --rounds 3                  # each deck scored alone
python -m evals.judge evals/runs/a evals/runs/b --rounds 3            # the two runs' decks together
python -m evals.judge evals/runs/a --judge gemini:gemini-3.5-flash    # a judge from another vendor
python -m evals.judge evals/runs/excel --kind xlsx --rounds 3         # workbooks: their own rubric
python -m evals.judge evals/runs/word --kind docx --rounds 3          # documents: their own rubric
```

A vision model sees the user's brief and the rendered pages and scores design, content and coherence
from 1 to 5, with the three worst defects. With several runs, the decks of one task go into one request
under random labels (`Deck P`, `Deck Q`...), drawn again every round, so neither order nor name says
which run made which. Each round is written to `<run>/<task>/judge/`; the mean and spread
(population standard deviation) over the rounds are what `compare.py` shows. The default judge is
DeepSeek, which also made the decks, so it may favour its own style; a Gemini judge is optional and the
free tier allows about 20 requests a day per model. Each record names its judge, the runs judged
together and the seed, and the file name carries a short id of that set of runs, so judging another
pairing never overwrites an earlier one. A run with no rendered pages is left out of the request (and
named); a round that fails is reported and the batch goes on, with a non-zero exit at the end. At most
8 runs can be judged together.

## Comparing two runs (`compare.py`)

```bash
python -m evals.compare evals/runs/baseline evals/runs/engine --out compare.md
python -m evals.compare evals/runs/excel-a evals/runs/excel-b --kind xlsx
```

One Markdown file with three tables, a row per task and the two runs side by side: the judge's scores,
the automatic checks, and cost, time, tool calls and tokens. A task only one run has shows `-`; the mean
and total rows cover only the tasks both runs have, and say how many. Scores come from one judge
(`--judge`, default DeepSeek) and one kind of request: each deck alone, or with `--joint` the two runs
judged in one request; the two kinds are never averaged together. Run the same code twice first to see how far two runs of it differ.

## Does a model write a Doc? (`doc_route.py`, decision 0025)

Twelve briefs, not tasks with a checker per kind: seven that ask for writing with no format named
(or only what the person will do with it), three that ask for Word by name, a template or tracked
changes, two that want an answer in chat. It classifies what the agent left (a Doc, a `.docx`, plain
Markdown, nothing), reads a Doc with the app's parsers (raw html, a chart that does not parse, facts
missing) and reports whether the Docs skill was loaded:

```bash
python -m evals.doc_route --out evals/runs/doc-route     # run it off-peak
python -m evals.doc_route --check-only --out evals/runs/doc-route
```

## Do saved workflows survive a site that changes? (`workflow/`, #754)

Two form sites (an expense claim and a supplier registration) and the ways real sites change:
- reworded labels;
- a cookie dialog over the page;
- a new required field, with or without data for it;
- a review page;
- a new reference format;
- dates as DD/MM/YYYY;
- a decoy button that only saves a draft;
- a form that appears late;
- reordered fields.

`python -m evals.workflow.run --list` names them all.

Each scenario builds the task in one of two ways:
- the steps a person recording the original site would get (`fixed`);
- one bounded agent step per row (`agent`).

It drives a running desktop app, because the browser tools run in its built-in browser. The steps:
1. Run the task.
2. If the run fails, follow its investigation: approve what it asks, answer its questions, and save its last revision as "Save changes" would.
3. Run the task again.

The ground truth is what the site stored, not what the run says.

```bash
python -m evals.workflow.run --app http://127.0.0.1:PORT --workspace APP_WORKSPACE --out evals/runs/wf
python -m evals.workflow.run ... --only expenses --builds agent --no-repair
```

The app's browser must be allowed 127.0.0.1 (Browser panel > Always allow), or every run waits on
the prompt.

`--mark-submit` marks the fixed workflows' final submit irreversible (decision 0029), so a
repair's test runs stop before it; the side effects column then shows what still got through.

Verdicts:
- `PASS`: the first run filed every row correctly.
- `REPAIRED`: the run after the investigation's revision filed every row correctly.
- `SAFE_STOP`: a row lacked a required value and nothing was filed.
- `ASKED`: the run paused to ask a person and filed nothing; the eval says no, and doesn't look into it.
- `GUESSED`: a row lacked a required value and something was filed anyway.
- `FALSE_SUCCESS`: the run said completed but the site holds something else, such as a draft, a wrong value or a missing row.
- `FAILED`: neither run filed the rows correctly.

`summary.md` also lists:
- what the investigation asked;
- the records the site got while it was being repaired (its test runs are real submissions);
- whether its revision was still tied to the task.
