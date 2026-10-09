# The title the model gives a conversation is cut to the sidebar's width (shipped)

Why: the instruction asks for at most 6 words (14 characters in Chinese or Japanese), but a model
that answers with a sentence got through: the cap was 80 characters.

## Shipped
- `_clean_title` cuts the title to 40 characters of room (a wide character counts as two) at a
  word when one is near the end, with "…" (`conversation/session.py`, `_fit_title`). The
  frontend does the same for a conversation that has no title yet (`shortLabel`).
- Test: `tests/test_title_length.py`.

## Not verified
- A real model's titles; checked with strings only.
