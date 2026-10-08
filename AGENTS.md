# Agent instructions

The instructions for every AI session in this repository (Claude Code, Codex
and others) are in [`CLAUDE.md`](CLAUDE.md): the layout, what to read first,
the invariants and the checks to run before a commit. Read it and
[`office-agent/CLAUDE.md`](office-agent/CLAUDE.md) before changing anything.

Run before pushing: `cd office-agent && bash scripts/check.sh --fast`; CI runs the full `bash scripts/check.sh`. Changes reach `main` only through pull requests, which the maintainer merges.
