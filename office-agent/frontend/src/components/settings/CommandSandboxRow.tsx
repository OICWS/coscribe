import { useEffect, useState } from "react";
import { getCommandSandbox, removeCommandSandbox, setUpCommandSandbox } from "../../lib/rest";
import type { CommandSandbox } from "../../types/settings";
import { SettingRow, secondaryButtonClass } from "./SettingRow";

function describe(sandbox: CommandSandbox | null, busy: boolean): string {
  if (busy) return "Working… Windows may ask an administrator to approve.";
  if (sandbox === null) return "Checking…";
  switch (sandbox.state) {
    case "ready":
      return "Set up. A command writes only its environment's folders and is offline unless the environment's network is Full; then you approve each one. It can still read most of your files.";
    case "unsupported":
      return "Only available on Windows. Here a command runs as you.";
    case "failed":
      return `Setting it up didn't work: ${sandbox.detail || "no reason given"}`;
    default:
      return "Not set up. Setting it up downloads Codex if needed and asks an administrator once.";
  }
}

/** Codex's Windows sandbox for commands: set up once, with an administrator's approval, or
 * removed. `onChange` reports whether it is ready. */
export function CommandSandboxRow({ active, onChange }: { active: boolean; onChange: (ready: boolean) => void }) {
  const [sandbox, setSandbox] = useState<CommandSandbox | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    getCommandSandbox()
      .then((result) => {
        if (cancelled) return;
        setSandbox(result);
        onChange(result.state === "ready");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [active, onChange]);

  const run = async (action: () => Promise<CommandSandbox | { error: string }>) => {
    setBusy(true);
    try {
      const result = await action();
      const next = "state" in result ? result : { state: "failed" as const, detail: result.error };
      setSandbox(next);
      onChange(next.state === "ready");
    } catch (err) {
      setSandbox({ state: "failed", detail: err instanceof Error ? err.message : "" });
      onChange(false);
    } finally {
      setBusy(false);
    }
  };

  const ready = sandbox?.state === "ready";
  return (
    <SettingRow
      label="Command sandbox"
      description={sandbox?.error ? `Removing it didn't work: ${sandbox.error}` : describe(sandbox, busy)}
      control={
        sandbox && sandbox.state !== "unsupported" ? (
          <button
            type="button"
            className={secondaryButtonClass}
            disabled={busy}
            onClick={() => void run(ready ? removeCommandSandbox : setUpCommandSandbox)}
          >
            {ready ? "Remove" : "Set up"}
          </button>
        ) : null
      }
    />
  );
}
