import { useCodeStatus } from "../lib/useCodeStatus";
import { CodeDownload } from "./CodeModuleStatus";

/** A new code conversation's heading, and anything that stands in the
 * way of its first message: the download, or a model Codex can't use. */
export function CodeHome({ onOpenSettings }: { onOpenSettings: () => void }) {
  const code = useCodeStatus();
  const problem = code.status?.model_problem;
  return (
    <div className="mb-7 flex w-full max-w-[880px] flex-col items-center gap-4 px-4">
      <h1
        className="text-center text-[40px] font-normal leading-tight text-[var(--fg)]"
        style={{ fontFamily: "Georgia, 'Times New Roman', serif" }}
      >
        What should we build?
      </h1>
      <p className="max-w-[560px] text-center text-sm text-[var(--muted)]">
        The code module writes and runs code in your folder: scripts that process your files, tools
        you'll reuse, fixes for a script that fails. You approve what it runs.
      </p>
      {code.status && !code.status.installed && (
        <div className="w-full max-w-[560px]">
          <CodeDownload code={code} compact />
        </div>
      )}
      {problem && (
        <p className="max-w-[560px] text-center text-sm text-[var(--danger)]">
          {problem}{" "}
          <button type="button" className="underline" onClick={onOpenSettings}>
            Choose its model in Settings
          </button>
        </p>
      )}
    </div>
  );
}
