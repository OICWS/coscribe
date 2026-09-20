// Matches tools/scheduled_tasks.py's SCHEDULED_THREAD_PREFIX -- a
// Scheduled Task's own dedicated thread_id is always "scheduled-
// <trigger_id>", checked by App.tsx to decide whether to show the
// "Scheduled / <name>" breadcrumb instead of the ordinary ThreadHeader.
export const SCHEDULED_THREAD_PREFIX = "scheduled-";

/** Full-page reload to a different thread -- there is no in-page
 * thread-switching machinery (App.tsx binds its whole WS connection to
 * one thread_id for the page's lifetime), deliberately not ported.
 * Shared by NavRail.tsx's own session-switcher, App.tsx's Scheduled-task
 * "open" handler, and every Run-now action (ScheduledTaskDetail,
 * RunPanel's card menu, NavRail's sidebar row) that needs to land the
 * user on the fired trigger's own conversation afterward. */
export function goToThread(threadId: string): void {
  window.location.href = `${window.location.pathname}?thread=${threadId}`;
}
