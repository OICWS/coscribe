import { lazy, Suspense, useState } from "react";
import type { LogItem } from "../state/reducer";
import { primaryButton, secondaryButton } from "../lib/formStyles";
import { CheckCircleIcon, ClockIcon } from "./icons";

const Markdown = lazy(() => import("./Markdown").then((m) => ({ default: m.Markdown })));

export type PlanItem = Extract<LogItem, { kind: "plan" }>;
export type PlanChoice = "auto" | "manual" | "revise";

const OUTCOME: Record<Exclude<PlanItem["status"], "pending">, string> = {
  auto: "Approved -- carrying it out in auto mode",
  manual: "Approved -- you'll approve each change",
  revise: "Sent back to keep planning",
};

/** The plan the model wrote in plan mode. Approving it leaves plan mode:
 * in auto mode, or approving each change by hand. Or send it back with
 * what to change. */
export function PlanCard({
  item,
  onAnswer,
}: {
  item: PlanItem;
  onAnswer?: (item: PlanItem, choice: PlanChoice, feedback: string) => void;
}) {
  const [revising, setRevising] = useState(false);
  const [feedback, setFeedback] = useState("");
  const pending = item.status === "pending";

  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg)] p-4">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
        {pending ? <ClockIcon className="h-3.5 w-3.5" /> : <CheckCircleIcon className="h-3.5 w-3.5" />}
        Plan
      </div>
      <div className="max-h-[28rem] overflow-y-auto">
        <Suspense fallback={<p className="whitespace-pre-wrap text-sm">{item.plan}</p>}>
          <Markdown text={item.plan} />
        </Suspense>
      </div>
      {item.status !== "pending" && (
        <p className="mt-3 text-[13px] text-[var(--muted)]">{OUTCOME[item.status]}</p>
      )}
      {pending && onAnswer && !revising && (
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className={primaryButton} onClick={() => onAnswer(item, "auto", "")}>
            Yes, and use auto mode
          </button>
          <button type="button" className={secondaryButton} onClick={() => onAnswer(item, "manual", "")}>
            Yes, manually approve edits
          </button>
          <button type="button" className={secondaryButton} onClick={() => setRevising(true)}>
            No, keep planning
          </button>
        </div>
      )}
      {pending && onAnswer && revising && (
        <form
          className="mt-4 flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            onAnswer(item, "revise", feedback);
          }}
        >
          <label className="text-[13px] text-[var(--muted)]" htmlFor={`plan-feedback-${item.id}`}>
            What should change?
          </label>
          <textarea
            id={`plan-feedback-${item.id}`}
            rows={3}
            autoFocus
            className="w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm outline-none focus:border-[var(--border-hover)]"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
          <div className="flex gap-2">
            <button type="submit" className={primaryButton}>
              Send back
            </button>
            <button type="button" className={secondaryButton} onClick={() => setRevising(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
