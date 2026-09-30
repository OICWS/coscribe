import { createContext } from "react";
import type { DraftTest, WorkflowDraftEntry } from "../../lib/transcriptGrouping";

/** What every draft card in a conversation shares: the tests run on the
 * drafts, which were saved, and what the buttons do. */
export interface DraftCards {
  latestDraftId: string | null;
  tests: Map<string, DraftTest>;
  /** Draft id -> the task it was saved into. */
  saved: Map<string, { triggerId: string; name: string }>;
  /** Steps finished of the test running now, if any. */
  testProgress: { done: number; total: number } | null;
  onReview?: (entry: WorkflowDraftEntry) => void;
  onTestAgain?: (entry: WorkflowDraftEntry) => void;
  onContinue?: () => void;
  onOpenTask?: (triggerId: string) => void;
}

export const DraftCardsContext = createContext<DraftCards>({
  latestDraftId: null,
  tests: new Map(),
  saved: new Map(),
  testProgress: null,
});
