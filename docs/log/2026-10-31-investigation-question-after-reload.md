# A question an investigation asks survives closing the tab (#719; shipped)

Why: in a live test, the AI was looking into a failed browser workflow and asked which cost
centre to use, since the site had added a required field the data didn't have. The question
went only to the tab that was open at that moment. A tab opened afterwards showed the thread
as busy with no question. The investigation waited on an answer nobody could give until the
person pressed Stop. A run or an investigation is not tied to a tab, so its turn goes on
waiting when the tab closes, unlike a turn the person started from a tab.

## Shipped
- `conversation/approvals.py`: keeps each question, approval, plan or task draft the turn is
  waiting on until it is answered. A sub-agent's approval is left out, because its task and
  panel already keep it.
- `conversation/history.py`: after the history replay, a tab is sent what the turn is still
  waiting on. That covers a tab opened later and the resync that replaces what a watching tab
  shows.
- Test in `tests/web/test_scheduled.py`: an investigation's question reaches tabs opened after
  it was asked, and the answer from the last one goes to the model.

## Not verified
- On Windows.
- Live in the app after the fix. The bug was reproduced live on Linux.
