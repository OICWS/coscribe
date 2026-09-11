/**
 * Sidecar process lifecycle -- ported from office-agent-desktop's (Tauri)
 * src-tauri/src/lib.rs (the `Command` block in `run()`'s `setup()`, plus
 * `should_keep_running_in_background` and the `RunEvent::Exit` kill
 * logic).
 */

import { spawn, type ChildProcess } from "node:child_process";
import { readFileSync } from "node:fs";
import { parse as parseDotenv } from "dotenv";
import { appDataDir, appDataSubpath, serverBin, serverLogFd } from "./paths";

let child: ChildProcess | undefined;

/** Starts coscribe-web as a managed sidecar on `port`, pointed at
 * per-user app-data directories rather than coscribe's own cwd-relative
 * defaults -- mirrors the Rust `Command` block exactly: same args, same
 * env vars, same log-file redirection, same self-exit-on-orphan
 * belt-and-suspenders. */
export function startSidecar(port: number): void {
  const dataDir = appDataDir();
  const logFd = serverLogFd();

  child = spawn(
    serverBin(),
    ["--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: dataDir,
      env: {
        ...process.env,
        COSCRIBE_WORKSPACE_ROOT: appDataSubpath("workspace"),
        COSCRIBE_STATE_DIR: appDataSubpath("state"),
        COSCRIBE_SKILLS_DIR: appDataSubpath("skills"),
        COSCRIBE_MEMORY_PATH: appDataSubpath("MEMORY.md"),
        // The sidecar self-exits if this app dies abruptly (crash, dev-
        // watcher restart) -- belt-and-suspenders alongside this app's
        // own before-quit kill below. The explicit PID matters if
        // packaging ever changes; see coscribe/web/app.py's
        // _exit_when_orphaned docstring.
        COSCRIBE_EXIT_WITH_PARENT: "1",
        COSCRIBE_PARENT_PID: String(process.pid),
      },
      stdio: ["ignore", logFd ?? "ignore", logFd ?? "ignore"],
      // Built-in option, no raw CREATE_NO_WINDOW flag needed -- the
      // sidecar is a console binary; without this a console window would
      // flash when this GUI app spawns it on Windows.
      windowsHide: true,
    },
  );

  child.on("error", (err) => {
    // eslint-disable-next-line no-console
    console.error(`[coscribe] failed to start server sidecar: ${err.message}`);
    child = undefined;
  });
}

/** `undefined` distinguishes "spawn() itself failed, there is no child
 * and never will be" from "still running" -- same distinction the Rust
 * watcher thread makes (`None => true` vs `Some(c) => try_wait()`), since
 * conflating them left an earlier version of this logic sitting through
 * the full startup timeout with a misleading "taking longer than
 * expected" message instead of an immediate, accurate failure. */
export function sidecarProcess(): ChildProcess | undefined {
  return child;
}

/** Killed on exit -- an orphaned server left running after the app quits
 * is exactly the kind of bug that's invisible until someone notices
 * coscribe-server still eating CPU/RAM with no window open. */
export function killSidecar(): void {
  if (child) {
    child.kill();
    child = undefined;
  }
}

/** Whether closing the main window should hide it and keep the sidecar
 * running (true, the shipped default) or let the close proceed as a
 * real quit. User-controlled via the web Settings panel's General tab
 * (`COSCRIBE_BACKGROUND_ON_CLOSE`, written to `.env` the same way every
 * other coscribe setting is) -- read fresh from that file on every
 * close rather than cached at startup, so a setting changed mid-session
 * takes effect at the very next close with no restart needed. Defaults
 * true (unset, missing file, or unparseable all keep today's shipped
 * behavior) -- only an explicit `false` opts out. Uses the `dotenv`
 * package's own `parse()`, not a hand-rolled line parser, for the same
 * reason the Rust side chose `dotenvy` over one: correct quoting
 * semantics for a file the web Settings panel's own `set_key()`-style
 * writer produces. */
export function shouldKeepRunningInBackground(): boolean {
  const envPath = appDataSubpath(".env");
  let raw: string;
  try {
    raw = readFileSync(envPath, "utf-8");
  } catch {
    return true;
  }
  let parsed: Record<string, string>;
  try {
    parsed = parseDotenv(raw);
  } catch {
    return true;
  }
  const value = parsed.COSCRIBE_BACKGROUND_ON_CLOSE;
  if (value === undefined) return true;
  return value.trim().toLowerCase() !== "false";
}
