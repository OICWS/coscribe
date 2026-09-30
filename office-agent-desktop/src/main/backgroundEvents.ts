/**
 * Subscribes to the sidecar's `/internal/events` Server-Sent Events
 * stream and shows a native notification for each event -- a run that
 * finished, failed or is waiting for the user, filtered by the
 * Notifications setting. Clicking one opens its conversation.
 *
 * Reconnects with a fixed short backoff (2s) on any failure: one process
 * talking to one known-local port for the app's whole lifetime.
 */

import { Notification } from "electron";
import { Readable } from "node:stream";
import { notificationLevel } from "./sidecar";

const RECONNECT_DELAY_MS = 2000;

interface BackgroundEvent {
  kind: string; // "wake" | "scheduled_task"
  status: string; // "completed" | "failed" | "needs_approval"
  title: string;
  thread_id?: string;
  /** The server's wording; older servers leave it out. */
  body?: string;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isBackgroundEvent(value: unknown): value is BackgroundEvent {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as Record<string, unknown>).kind === "string" &&
    typeof (value as Record<string, unknown>).status === "string" &&
    typeof (value as Record<string, unknown>).title === "string"
  );
}

function notify(event: BackgroundEvent, open: (threadId: string) => void): void {
  const level = notificationLevel();
  if (level === "off" || (level === "problems" && event.status === "completed")) return;
  const body =
    event.body ||
    (event.status === "failed"
      ? `Failed: ${event.title}`
      : event.status === "needs_approval"
        ? `Waiting for you: ${event.title}`
        : `Finished: ${event.title}`);
  const notification = new Notification({ title: event.body ? event.title : "coscribe", body });
  const threadId = event.thread_id;
  if (threadId) notification.on("click", () => open(threadId));
  notification.show();
}

/** Runs for the life of the app -- an outer loop that reconnects, and an
 * inner loop that reads one SSE frame at a time from whichever
 * connection is currently open. SSE frames this endpoint emits are
 * always exactly one "data: <json>\n\n" -- no multi-line data, no
 * id:/retry: fields -- so splitting on a blank line is sufficient, same
 * as the Rust version's own hand-parsing, not a partial SSE-spec
 * implementation standing in for a fuller one. */
export async function watchBackgroundEvents(port: number, open: (threadId: string) => void): Promise<void> {
  const url = `http://127.0.0.1:${port}/internal/events`;
  for (;;) {
    let response: Response;
    try {
      response = await fetch(url);
    } catch {
      await sleep(RECONNECT_DELAY_MS);
      continue;
    }
    if (!response.ok || !response.body) {
      await sleep(RECONNECT_DELAY_MS);
      continue;
    }

    let buf = "";
    try {
      for await (const chunk of Readable.fromWeb(response.body as never)) {
        buf += (chunk as Buffer).toString("utf-8");
        let idx: number;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, idx + 2);
          buf = buf.slice(idx + 2);
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try {
              const parsed: unknown = JSON.parse(line.slice("data: ".length));
              if (isBackgroundEvent(parsed)) notify(parsed, open);
            } catch {
              // Malformed frame -- same as the Rust version's
              // `if let Ok(event) = serde_json::from_str(...)`, skip it
              // and keep reading rather than tearing down the stream.
            }
          }
        }
      }
    } catch {
      // Dropped stream -- fall through to the reconnect delay below,
      // same as any other connection failure.
    }
    await sleep(RECONNECT_DELAY_MS);
  }
}
