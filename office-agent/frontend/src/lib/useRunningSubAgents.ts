import { useEffect, useState } from "react";
import type { SubAgentTask } from "../types/session";
import { getSubAgentTasks } from "./rest";

const POLL_MS = 4000;

/** How many of this conversation's sub-agents are working, and how many of
 * those wait on the user -- for the badge on the Sub Agents button, which
 * must show it with the panel closed. Re-read when the server says the list
 * changed (`tick`), and every few seconds while any is running or a turn is
 * under way (a task starting says nothing until it first changes). */
export function useRunningSubAgents(
  threadId: string,
  tick: number,
  enabled: boolean,
  turnInFlight: boolean,
) {
  const [tasks, setTasks] = useState<SubAgentTask[]>([]);
  const active = tasks.filter((t) => t.status === "running" || t.status === "needs_approval");
  const anyActive = active.length > 0;

  useEffect(() => {
    if (!enabled) {
      setTasks([]);
      return;
    }
    let cancelled = false;
    const refresh = () =>
      getSubAgentTasks(threadId)
        .then((list) => {
          if (!cancelled) setTasks(list);
        })
        .catch(() => {});
    refresh();
    if (!anyActive && !turnInFlight) return () => void (cancelled = true);
    const timer = setInterval(refresh, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [threadId, tick, enabled, anyActive, turnInFlight]);

  return {
    running: active.length,
    waiting: active.filter((t) => t.status === "needs_approval").length,
  };
}
