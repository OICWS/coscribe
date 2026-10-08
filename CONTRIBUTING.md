# Contributing

Thanks for helping. This project is worked on by one maintainer and several AI
coding sessions at once, so the rules below are mostly about not getting in
each other's way. The map of the code and the invariants are in
[`CLAUDE.md`](CLAUDE.md); the detailed working rules are in
[`office-agent/CLAUDE.md`](office-agent/CLAUDE.md).

## Setup

See [`README.md`](README.md). Then check your setup works:
`cd office-agent && bash scripts/check.sh --fast`.

## Making a change

1. **Look first.** Read the open issues and pull requests and `git log origin/main`,
   so you don't repeat or collide with someone's work.
2. **Say which area you are in** when you start (a comment on the issue, or in
   the PR description). Areas: *runtime* (`runtime_lg/`, `runtime/`), *tools*
   (`tools/`), *server* (`web/`), *code module* (`code_runtime/`), *frontend*
   (`office-agent/frontend/`), *desktop* (`office-agent-desktop/`), *docs*.
   Stay inside one area per change; file an issue for what you notice elsewhere.
3. **Write the test with the change.** A bug fix gets a test that failed before.
4. **Run the checks:** `cd office-agent && bash scripts/check.sh --fast` before
   pushing; CI runs the full `bash scripts/check.sh` (frontend `tsc -b` and
   build, oxlint, ruff, mypy, pytest). Touching
   the UI also means looking at it in the running app, not only compiling it.
5. **Record real findings** in `office-agent/ROADMAP.md` (what shipped, what was
   measured, what was *not* verified). Take the next free phase id from
   `origin/main` when you write the entry, because ids collide otherwise.
6. **Commit messages explain why.** Comments in code explain a non-obvious
   reason, never what the line does.

## Where the change goes

`main` is protected: every change, including the maintainer's and doc-only
ones, is a pull request whose `test` check passes, squash-merged by the
maintainer. Branch `<area>/<slug>`, open a draft PR on your first push that
says what you are changing and which hot files it touches, and update it with
`git merge origin/main`. The full rules are in
[`office-agent/CLAUDE.md`](office-agent/CLAUDE.md).

## Reporting problems

- A bug or a request: open an issue with the template.
- A security problem: do not open an issue, see [`SECURITY.md`](SECURITY.md).

By contributing you agree your work is released under the [MIT license](LICENSE)
and that you will follow the [code of conduct](CODE_OF_CONDUCT.md).
