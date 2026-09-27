import { useEffect, useState } from "react";
import { getScriptEnvInterpreter, setScriptEnvInterpreter } from "../../lib/rest";
import { fieldClass, secondaryButtonClass } from "./SettingRow";

interface PythonInterpreterSectionProps {
  active: boolean;
}

// Auto-detection (sys.executable, then py/python3/python found on PATH)
// has a real ceiling on Windows: a GUI app's inherited PATH can be stale
// relative to a freshly-installed Python (Explorer's own environment
// block doesn't refresh until logoff/logon), and the WindowsApps
// python.exe/python3.exe "app execution alias" stubs resolve via
// shutil.which without being real interpreters -- no amount of smarter
// auto-detection closes either gap. This mirrors VS Code's Python
// extension, which hits the identical wall and solves it the same way:
// auto-detect as the default, plus an always-available manual "enter
// interpreter path" escape hatch (their own fallback is a path field, not
// a full native file-browser, before offering "Find..." on top of it) --
// same shape here, kept to a plain path field for now.
export function PythonInterpreterSection({ active }: PythonInterpreterSectionProps) {
  const [configured, setConfigured] = useState<string | null>(null);
  const [autoDetected, setAutoDetected] = useState<string[]>([]);
  const [path, setPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

  const refresh = () => {
    getScriptEnvInterpreter()
      .then((info) => {
        setConfigured(info.configured);
        setAutoDetected(info.auto_detected);
        setPath(info.configured ?? "");
      })
      .catch((err) => {
        setStatus({ text: err instanceof Error ? err.message : "Could not load interpreter info.", error: true });
      });
  };

  useEffect(() => {
    if (active) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const save = async () => {
    setSaving(true);
    setStatus(null);
    try {
      const result = await setScriptEnvInterpreter(path.trim());
      if (!result.success) {
        setStatus({ text: result.error || "Could not use that interpreter.", error: true });
        return;
      }
      setStatus({
        text: path.trim() ? "Saved. The script environment will be rebuilt with this interpreter next time it's used." : "Cleared -- back to auto-detection.",
        error: false,
      });
      refresh();
    } catch (err) {
      setStatus({ text: err instanceof Error ? err.message : "Could not use that interpreter.", error: true });
    } finally {
      setSaving(false);
    }
  };

  const pickAutoDetected = (candidate: string) => {
    setPath(candidate);
  };

  return (
    <div className="flex flex-col gap-3 pt-4">
      <div>
        <div className="text-[15px]">Interpreter</div>
        <p className="mt-1 text-[13px] leading-snug text-[var(--muted)]">
          {configured
            ? "Scripts run with the Python below."
            : "Found automatically. Set a path only if scripts fail to run or the wrong Python is used."}
        </p>
      </div>

      <div className="flex items-center gap-2">
        <input
          aria-label="Python interpreter path"
          className={`${fieldClass} min-w-0 flex-1 font-mono text-[13px]`}
          placeholder="Blank to find it automatically, or a python.exe path"
          value={path}
          disabled={saving}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
        />
        <button
          type="button"
          className={secondaryButtonClass}
          disabled={saving || path.trim() === (configured ?? "")}
          onClick={save}
        >
          {saving ? "Checking…" : "Save"}
        </button>
      </div>

      {autoDetected.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-[var(--muted)]">
          <span>Found:</span>
          {autoDetected.map((candidate) => (
            <button
              key={candidate}
              type="button"
              title={candidate}
              className="max-w-48 truncate rounded-md border border-[var(--border)] bg-[var(--field-bg)] px-2 py-0.5 font-mono hover:border-[var(--border-hover)]"
              onClick={() => pickAutoDetected(candidate)}
            >
              {candidate}
            </button>
          ))}
        </div>
      )}

      {status && <p className={`text-[13px] ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</p>}
    </div>
  );
}
