// Matches tools/scheduled_tasks.py's SCHEDULED_THREAD_PREFIX -- a
// Scheduled Task's own dedicated thread_id is always "scheduled-
// <trigger_id>", checked by App.tsx to decide whether to show the
// "Scheduled / <name>" breadcrumb instead of the ordinary ThreadHeader.
export const SCHEDULED_THREAD_PREFIX = "scheduled-";

/** Fired on window after ?thread= changes in-page; App.tsx listens for it
 * (and popstate) and rebinds its socket to the thread now in the URL. */
export const THREAD_CHANGE_EVENT = "coscribe:threadchange";

/** In-page switch to another thread: the URL stays the single source of
 * truth for which thread is open, so reload, back/forward, and a pasted
 * link all keep working. Fires the event even when already on that
 * thread, so a Run now from the Scheduled portal still lands on its
 * conversation view. */
export function goToThread(threadId: string): void {
  const url = new URL(window.location.href);
  if (url.searchParams.get("thread") !== threadId) {
    url.searchParams.set("thread", threadId);
    window.history.pushState(null, "", url.toString());
  }
  window.dispatchEvent(new Event(THREAD_CHANGE_EVENT));
}

export function startNewThread(): void {
  goToThread(crypto.randomUUID().slice(0, 8));
}
