import { toolLabel } from "../../lib/toolLabels";
import { formatValue } from "../../lib/workflowLabels";
import type { WorkflowAgentActivity, WorkflowAgentCall } from "../../types/wire.gen";
import type { WorkflowStep } from "../../types/workflow";

/** The tools a finished agent step called, under its output -- declined
 * calls included. */
export function AgentToolsUsed({ activity }: { activity: WorkflowAgentActivity | null | undefined }) {
  const used = activity?.tools_used ?? [];
  if (used.length === 0) return null;
  return (
    <p className="text-xs text-[var(--muted)]">
      Called {used.map(toolLabel).join(", ")}
      {activity?.tokens ? ` · ${activity.tokens.toLocaleString()} tokens` : ""}
    </p>
  );
}

/** The one call a waiting agent step asks about: answering applies to it
 * alone, and the agent goes on either way. */
export function AgentCallRequest({ call, step }: { call: WorkflowAgentCall; step: WorkflowStep }) {
  // A bounded step asks only beyond what saving it allowed; say which limit.
  const beyond =
    step.kind !== "agent" || step.autonomy !== "bounded"
      ? null
      : step.tools.includes(call.tool)
        ? "It’s on this step’s list of tools, but this call reaches outside the folders or sites the step names."
        : "It isn’t on this step’s list of tools, so it needs your yes.";
  const args =
    call.args !== null && typeof call.args === "object" && !Array.isArray(call.args)
      ? Object.entries(call.args as Record<string, unknown>)
      : [];
  return (
    <div className="flex flex-col gap-1.5" data-testid="agent-call-request">
      <p className="text-sm font-medium">The agent wants to use {toolLabel(call.tool)}</p>
      {beyond && <p className="text-[13px] text-[var(--muted)]">{beyond}</p>}
      {args.length > 0 && (
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[13px]">
          {args.map(([key, value]) => (
            <div key={key} className="contents">
              <dt className="text-[var(--muted)]">{key}</dt>
              <dd className="truncate font-mono text-[12px]">{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="text-[13px] text-[var(--muted)]">
        Your answer covers this call only. If you don’t allow it, the agent is told so and carries on without it.
      </p>
    </div>
  );
}
