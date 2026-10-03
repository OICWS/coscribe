"""Shared by scripts.py's run_python_script and node_scripts.py's
run_node_script -- both capture a child process's stdout/stderr via
`subprocess.run(capture_output=True)` and, before this, echoed it back
into the model's context completely uncapped. Real, live-reported cost
problem: a real ~9.3M-token PPTX-generation run spent a meaningful,
entirely avoidable slice of that on exactly this -- a script printing a
large DataFrame repr or a verbose library's own stdout chatter went
straight back into context in full, every single call.

Keeps both ends of the output, not just the head -- unlike a plain
`text[:limit]` cut, this doesn't throw away whatever printed last, which
is disproportionately likely to be the actual result or the final
traceback line a debugging turn most needs to see.
"""

from __future__ import annotations

_TRUNCATED_HEAD_CHARS = 4_000
_TRUNCATED_TAIL_CHARS = 4_000
# Kept well under head+tail's own combined 8000 so a merely-borderline
# result (a couple thousand characters over) isn't truncated into a
# *smaller* string than an untouched one would have been -- this only
# ever kicks in for genuinely large output.
MAX_SCRIPT_OUTPUT_CHARS = 20_000


def truncate_script_output(text: str) -> str:
    """No-op for anything at or under MAX_SCRIPT_OUTPUT_CHARS. Past that,
    keeps the first/last `_TRUNCATED_HEAD_CHARS`/`_TRUNCATED_TAIL_CHARS`
    with a marker naming exactly how many characters were cut from the
    middle -- honest about what's missing, not a silent cut."""
    if len(text) <= MAX_SCRIPT_OUTPUT_CHARS:
        return text
    omitted = len(text) - _TRUNCATED_HEAD_CHARS - _TRUNCATED_TAIL_CHARS
    head = text[:_TRUNCATED_HEAD_CHARS]
    tail = text[-_TRUNCATED_TAIL_CHARS:]
    return f"{head}\n\n...[{omitted:,} characters truncated]...\n\n{tail}"


# What one read may hand the model: about 20-30k tokens. A whole 5 MB CSV
# (over a million) used to go straight into the context, and the next model
# call failed on its length.
MAX_READ_CHARS = 80_000


def cap_read_output(text: str, how_to_continue: str) -> str:
    """`text` unchanged if it fits; else its first lines up to
    MAX_READ_CHARS, then a note saying how much was left and
    `how_to_continue` -- never a silent cut."""
    if len(text) <= MAX_READ_CHARS:
        return text
    cut = text.rfind("\n", 0, MAX_READ_CHARS)
    shown = text[: cut if cut > MAX_READ_CHARS // 2 else MAX_READ_CHARS]
    lines_shown = shown.count("\n") + 1
    return (
        f"{shown}\n\n[Showing the first {len(shown):,} of {len(text):,} characters "
        f"({lines_shown:,} lines); {how_to_continue}]"
    )
