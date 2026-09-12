/**
 * Path/port resolution -- ported byte-for-byte from office-agent-desktop's
 * (Tauri) src-tauri/src/lib.rs (`app_data_dir`, `free_port`, `server_bin`,
 * `server_log_file`). This file is the one place in this whole shell where
 * "port faithfully, don't just port similarly" actually matters: the
 * Python backend's own `coscribe.cli._app_data_dir()` expects the exact
 * same directory convention regardless of which shell launched it, and
 * `COSCRIBE_APP_DATA_DIR`/`COSCRIBE_SERVER_BIN` are dev/testing overrides
 * both shells already share.
 */

import { app } from "electron";
import { createServer } from "node:net";
import { existsSync, mkdirSync, renameSync, openSync } from "node:fs";
import { join } from "node:path";

/** Per-user application-data directory. `COSCRIBE_APP_DATA_DIR` overrides
 * (this project's own dev/testing convention, shared with the Python
 * side's identical override). Otherwise: `%APPDATA%\coscribe` on Windows,
 * `~/Library/Application Support/coscribe` on macOS, `~/.config/coscribe`
 * elsewhere -- must stay byte-identical to `cli.py`'s `_app_data_dir()`. */
export function appDataDir(): string {
  const override = process.env.COSCRIBE_APP_DATA_DIR;
  if (override) return override;
  if (process.platform === "win32") {
    const appdata = process.env.APPDATA;
    if (appdata) return join(appdata, "coscribe");
  }
  if (process.platform === "darwin") {
    const home = process.env.HOME;
    if (home) return join(home, "Library", "Application Support", "coscribe");
  }
  const home = process.env.HOME ?? ".";
  return join(home, ".config", "coscribe");
}

/** Binds to `127.0.0.1:0`, reads back the OS-assigned port, and releases
 * it immediately -- same collision-free free-port trick `free_port()`
 * (Rust side) uses via `TcpListener::bind`, and the same fallback port
 * (8765) if the OS-assigned lookup somehow fails. There's an inherent,
 * accepted TOCTOU gap between releasing this port and the sidecar
 * actually binding it a moment later -- identical to the Rust version's
 * own behavior, not a new risk introduced here. */
export function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const server = createServer();
    server.unref();
    server.on("error", () => resolve(8765));
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 8765;
      server.close(() => resolve(port));
    });
  });
}

/** Path to the sidecar entrypoint. Resolution order mirrors `server_bin()`
 * (Rust side) exactly:
 *   1. `COSCRIBE_SERVER_BIN` env override.
 *   2. The bundled onedir sidecar shipped via electron-builder's
 *      `extraResources` (production): `<resourcesPath>/sidecar/`.
 *   3. Dev fallback: the repo's own venv, relative to this package
 *      (`office-agent-desktop-electron` -> `../../office-agent/.venv`;
 *      `bin/` on POSIX, `Scripts\` on Windows). */
export function serverBin(): string {
  const override = process.env.COSCRIBE_SERVER_BIN;
  if (override) return override;

  const exeName = process.platform === "win32" ? "coscribe-server.exe" : "coscribe-server";
  const bundled = join(process.resourcesPath, "sidecar", exeName);
  if (existsSync(bundled)) return bundled;

  const devSubdir = process.platform === "win32" ? "Scripts" : "bin";
  const devExeName = process.platform === "win32" ? "coscribe-web.exe" : "coscribe-web";
  return join(__dirname, "..", "..", "..", "office-agent", ".venv", devSubdir, devExeName);
}

/** The sidecar's log file: `<app_data_dir>/logs/coscribe-server.log`,
 * fresh per launch, previous run kept as `.old` -- mirrors
 * `server_log_file()` (Rust side). Returns a raw file descriptor, not a
 * `WriteStream`: `child_process.spawn()`'s `stdio` option requires an
 * already-open fd (a number) or a stream whose own fd is already
 * populated -- `fs.createWriteStream()` opens its fd *asynchronously*,
 * so handing `spawn()` a freshly-created WriteStream races the spawn
 * call and throws `ERR_INVALID_ARG_VALUE` (`fd: null`) almost every
 * time in practice, confirmed live in this sandbox before switching to
 * `openSync()`. Returns `undefined` only if the directory/file can't be
 * created or opened; logging must never block startup, same as the Rust
 * version's `None -> /dev/null` fallback (the caller passes `"ignore"`
 * to `stdio` in that case instead). */
export function serverLogFd(): number | undefined {
  const dir = join(appDataDir(), "logs");
  try {
    mkdirSync(dir, { recursive: true });
  } catch {
    return undefined;
  }
  const path = join(dir, "coscribe-server.log");
  if (existsSync(path)) {
    try {
      renameSync(path, join(dir, "coscribe-server.log.old"));
    } catch {
      // Best-effort rotation, same as the Rust version's `let _ =` on
      // rename -- a failed rotation must never block startup either.
    }
  }
  try {
    return openSync(path, "w");
  } catch {
    return undefined;
  }
}

/** The splash page bundled into this app's own packaged resources
 * (parity with Tauri's `dist/index.html` via `build.frontendDist`). In
 * dev, `app.getAppPath()` already points at this package's own root; in
 * a packaged build it's the app.asar root, which still contains
 * `src/splash/index.html` since it's part of `files` in package.json's
 * `build` config. */
export function splashPagePath(): string {
  return join(app.getAppPath(), "src", "splash", "index.html");
}

export function appDataSubpath(...segments: string[]): string {
  return join(appDataDir(), ...segments);
}
