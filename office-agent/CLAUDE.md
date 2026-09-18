# Working in this repo

This is `coscribe` (office-agent). Start here, then follow the pointers
below to the doc that actually has the detail you need -- this file is
deliberately short; it's a map, not the content.

## Where the real context lives

- `README.md` -- what coscribe is, setup, feature docs.
- `../ARCHITECTURE.md` -- why the runtime is built the way it is.
- `ROADMAP.md` -- the running log of shipped/planned architecture and
  frontend work, phase-numbered. **Phase 8am** (near the end) is the
  current frontend-redesign direction -- recorded, not yet built. Its
  reference screenshots are real files at `docs/ui-references/*.png`
  (private repo only) -- `Read` them directly before touching any of
  that work; the prose description alone is not enough for pixel-level
  UI fidelity.
- `PPTX_DESIGN.md` -- every PPTX-tool design decision, numbered
  sections, chronological. Read before touching anything in
  `tools/presentations.py`.
- `src/coscribe/runtime_lg/README.md` -- the LLM-runtime migration
  history, real bugs found live, and open research (e.g. the
  tool-loading context-cost investigation and the provider tool-search
  gap).

Every one of these is written the same way: real findings, real
numbers, real bugs -- not guessed. Match that bar in anything you add
to them, and check them before assuming something is undocumented.

## Code review before every commit: Open Code Review, delegation mode

Decided explicitly (2026-09-16, to cut token spend on review passes):
before committing any code change, run a review pass through
`ocr` (`alibaba/open-code-review`, installed globally via
`npm install -g @alibaba-group/open-code-review` -- reinstall if a
fresh container doesn't have it) in **delegation mode** -- no Claude
Code plugin/marketplace registration needed, no separate LLM/API key
for OCR itself:

```bash
ocr delegate preview --commit <sha>       # or --from/--to for a range
ocr delegate rule <changed-file> [<changed-file> ...]
```

`preview` lists which changed files are actually reviewable (filters
out e.g. markdown). `rule` returns the resolved, precision-tuned rule
set for those files' language, grouped by content -- read it, then
review the real diff yourself against those rules (you are the "host
agent" in OCR's own terms; it does file selection/rule-matching, you do
the judgment). Verified live before adopting: correctly stayed silent
on an already-verified real commit (no false positives), and correctly
flagged a deliberately-planted mutable-default-argument bug in a
synthetic test. Favor precision over recall per the rule set's own
instruction -- only raise something you're actually confident about.

## Code comments: WHY only, never WHAT or history

A comment earns its place by explaining a non-obvious design reason --
a hidden constraint, an invariant, a workaround, something that would
surprise a reader. Real drift caught and corrected 2026-09-18: comments
kept accumulating three other kinds that don't belong in code at all --

- **Restating WHAT the code does** ("renders X as Y ⟨chip⟩") -- the
  code already says this; delete.
- **History/evolution narration** ("this used to X", "a user reported
  Y, this closes that gap", "real gap this closes: ...") -- this is a
  commit-message sentence wearing a `#`. Put it in the commit message
  (or PR description) instead, never in the code.
- **Product/UX reasoning or restated user intent** ("用户想要...于是
  ...", "a manual button read as unnecessary friction so...") -- same
  answer, same destination: commit message, not a comment.

Before writing a comment, ask whether it survives with every sentence
that isn't a "why" removed, and whether a reader with zero memory of
this conversation still needs it. If not, it goes in the commit
message or gets deleted outright -- never both.

## Dual-repo sync -- every commit goes to both

Private (`OICWS/project`, branch `claude/local-office-agent-system-k2cle1-13gepc`)
is where real development happens. Public (`OICWS/coscribe`, `dev` and
`main`, kept in lockstep) is a fork missing a few things on purpose
(no Tauri legacy shell, no `docs/ui-references/` screenshots) --
expect exactly those diffs when cherry-picking, nothing else.

```bash
# 1. Commit + push to the private branch first, full test suite green,
#    ruff/mypy clean, OCR review pass done.
git push -u origin claude/local-office-agent-system-k2cle1-13gepc

# 2. Cherry-pick to the public repo's dev
cd /home/user/coscribe && git checkout dev
git remote add private-project https://github.com/OICWS/project
git fetch private-project claude/local-office-agent-system-k2cle1-13gepc --depth=5
git cherry-pick <sha>   # skip any commit that's private-only (e.g. adding docs/ui-references/ images)
git diff private-project/claude/local-office-agent-system-k2cle1-13gepc HEAD -- <touched files>
#   ^ confirm the only diffs are the known, expected ones (Tauri wording, missing ui-references/)
git push origin dev

# 3. Fast-forward main to match
git fetch origin main && git checkout -B main origin/main
git merge --ff-only dev && git push origin main

# 4. Clean up
git remote remove private-project && git checkout dev
```

`git fetch` through this environment's proxy can return a stale
cached read -- if a branch state looks wrong, cross-check with
`git ls-remote origin <ref>` (a live query) before trusting `fetch`.

## Test discipline

Full suite (`pytest tests/`, ~4-5 min, runs in the background) green
and `ruff check`/`mypy` clean before every commit -- no exceptions,
including doc-only changes (confirms nothing else broke). A test that
writes real files must `monkeypatch.chdir(tmp_path)` first if it (or
code it calls) can fall back to a relative path -- a real, live-hit
mistake this session made once already (see `test_web.py`'s own
`test_post_provider_persists_an_absolute_path_not_a_cwd_relative_one`
for the established pattern to copy).

## Model-name / provider facts: verify, don't guess

This session repeatedly found stale/wrong model names and provider
capability assumptions from training-data priors (e.g. a hardcoded
`"claude-opus-4-6"`, an assumed-wrong DeepSeek model id). Anything
about a specific model name, a vendor's current API capability, or
"is this library still maintained" gets checked live (WebSearch/
WebFetch against the vendor's own docs) before it goes in code, a
default value, or documentation -- not answered from memory.
