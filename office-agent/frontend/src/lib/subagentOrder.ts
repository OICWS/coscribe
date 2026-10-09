interface Chained {
  task_id: string;
  description: string;
  continues?: string;
}

/** `tasks` (newest first) with each follow-up next to the task it carries on
 * from: a chain sits where its newest member would, newest first within it. */
export function orderFollowUps<T extends Chained>(tasks: T[]): T[] {
  const byId = new Map(tasks.map((task) => [task.task_id, task]));
  const rootOf = (task: T): string => {
    const seen = new Set<string>();
    let current = task;
    while (current.continues && byId.has(current.continues) && !seen.has(current.task_id)) {
      seen.add(current.task_id);
      current = byId.get(current.continues)!;
    }
    return current.task_id;
  };
  const chains = new Map<string, T[]>();
  for (const task of tasks) {
    const root = rootOf(task);
    chains.set(root, [...(chains.get(root) ?? []), task]);
  }
  return [...chains.values()].flat();
}

/** The description of the task `task` carries on from, when it is listed. */
export function followUpOf(task: Chained, tasks: Chained[]): string | null {
  if (!task.continues) return null;
  return tasks.find((t) => t.task_id === task.continues)?.description ?? null;
}
