import { useEffect, useState } from "react";
import type { SubAgentTask } from "../types/session";
import { getSubAgentTasks } from "./rest";

// A backstop: the server says when the list changes.
const POLL_MS = 10000;

/** How many of this conversation's sub-agents are working, and how many of
 * those wait on the user -- for the badge on the Sub Agents button, which
 * must show it with the panel closed. Re-read when the server says the list
 * changed (`tick`), and every few seconds while any is running. */
export function useRunningSubAgents(threadId: string, tick: number, enabled: boolean) {
  // Keyed by thread, so another conversation's count is never shown under
  // this one while its own is on the way.
  const [read, setRead] = useState<{ threadId: string; tasks: SubAgentTask[] }>({ threadId, tasks: [] });
  const tasks = read.threadId === threadId ? read.tasks : [];
  const active = tasks.filter((t) => t.status === "running" || t.status === "needs_approval");
  const anyActive = active.length > 0;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const refresh = () =>
      getSubAgentTasks(threadId)
        .then((list) => {
          if (!cancelled) setRead({ threadId, tasks: list });
        })
        .catch(() => {});
    refresh();
    if (!anyActive) return () => void (cancelled = true);
    const timer = setInterval(refresh, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [threadId, tick, enabled, anyActive]);

  return {
    running: enabled ? active.length : 0,
    waiting: enabled ? active.filter((t) => t.status === "needs_approval").length : 0,
  };
}
