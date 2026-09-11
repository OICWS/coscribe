import { useEffect, useState } from "react";
import { getMemory, updateMemory } from "../../lib/rest";
import { SettingRow } from "./SettingRow";

/** Direct editor for MEMORY.md's own content -- modeled on Cowork's
 * "Global instructions" row (an "Edit" button that opens a text box for
 * preferences/conventions the model should always know). coscribe
 * already had the underlying concept (MEMORY.md, injected into every
 * session's instructions -- see coordinator.py's format_memory_section)
 * and a path to it in the Workspace tab's own fields, but no way to see
 * or change its *content* without leaving the app to edit the file by
 * hand, or asking the agent in chat to use its `remember` tool. This is
 * a separate mini-form, not wired into SettingsModal's own dirty/Save-
 * bar tracking (which is keyed to plain .env fields) -- memory content
 * is a bigger, multi-line edit that reads better with its own explicit
 * Save, not bundled with unrelated General-tab changes. */
export function GlobalInstructionsSection({ active }: { active: boolean }) {
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

  useEffect(() => {
    if (!active || loaded) return;
    getMemory()
      .then((res) => setContent(res.content))
      .catch(() => setStatus({ text: "Couldn't load Global Instructions.", error: true }))
      .finally(() => setLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, loaded]);

  const startEditing = () => {
    setDraft(content);
    setStatus(null);
    setEditing(true);
  };

  const save = async () => {
    setSaving(true);
    setStatus(null);
    try {
      await updateMemory(draft);
      setContent(draft);
      setEditing(false);
      setStatus({ text: "Saved.", error: false });
    } catch {
      setStatus({ text: "Couldn't save -- try again.", error: true });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <SettingRow
        label="Global Instructions"
        description="Preferences, conventions, or context coscribe should always know. Applies to every new session."
        control={
          <button
            type="button"
            className="rounded-md border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--card-bg)]"
            onClick={editing ? () => setEditing(false) : startEditing}
          >
            {editing ? "Close" : "Edit"}
          </button>
        }
      />
      {editing && (
        <div className="flex flex-col gap-2 pb-4">
          <textarea
            className="min-h-32 w-full resize-y rounded-md border border-[var(--border)] bg-transparent px-2 py-1.5 text-sm outline-none focus:border-[var(--accent)]"
            value={draft}
            placeholder="e.g. Always write dates as YYYY-MM-DD. I prefer decks with no more than 6 bullets per slide."
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="rounded-md bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)] disabled:opacity-40"
              disabled={saving || draft === content}
              onClick={save}
            >
              Save
            </button>
            {status && <span className={`text-sm ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</span>}
          </div>
        </div>
      )}
      {!editing && status && (
        <p className={`-mt-2 pb-4 text-xs ${status.error ? "text-red-500" : "text-[var(--muted)]"}`}>{status.text}</p>
      )}
    </div>
  );
}
