/**
 * Subscribes to the sidecar's `/internal/events` Server-Sent Events
 * stream and shows a native toast for every event -- ported from
 * office-agent-desktop's (Tauri) src-tauri/src/background_events.rs.
 *
 * Fires a notification on **every** completion, success or failure,
 * regardless of whether the main window is currently visible (same
 * deliberate choice the Rust version made, not an oversight -- see that
 * file's own docs). Deliberately does not try to make the notification
 * click focus the window, same reasoning as the Rust version: routing a
 * click back into the app needs platform-specific toast-activation
 * wiring this sandbox has no way to verify, so this stays a plain toast
 * until a real Windows machine confirms a click-to-focus wiring works.
 *
 * Reconnects with a fixed short backoff (2s) on any failure -- same
 * reasoning as the Rust version: one process talking to one known-local
 * port for the app's whole lifetime, not a client hitting a shared
 * remote service that backoff etiquette matters for.
 */

import { Notification } from "electron";
import { Readable } from "node:stream";

const RECONNECT_DELAY_MS = 2000;

interface BackgroundEvent {
  kind: string; // "wake" | "scheduled_task"
  status: string; // "completed" | "failed"
  title: string;
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

function notify(event: BackgroundEvent): void {
  const body =
    event.kind === "scheduled_task" && event.status === "failed"
      ? `Scheduled task failed: ${event.title}`
      : event.kind === "scheduled_task"
        ? `Scheduled task completed: ${event.title}`
        : event.status === "failed"
          ? `Background task failed: ${event.title}`
          : `Background task finished: ${event.title}`;
  new Notification({ title: "coscribe", body }).show();
}

/** Runs for the life of the app -- an outer loop that reconnects, and an
 * inner loop that reads one SSE frame at a time from whichever
 * connection is currently open. SSE frames this endpoint emits are
 * always exactly one "data: <json>\n\n" -- no multi-line data, no
 * id:/retry: fields -- so splitting on a blank line is sufficient, same
 * as the Rust version's own hand-parsing, not a partial SSE-spec
 * implementation standing in for a fuller one. */
export async function watchBackgroundEvents(port: number): Promise<void> {
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
              if (isBackgroundEvent(parsed)) notify(parsed);
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
