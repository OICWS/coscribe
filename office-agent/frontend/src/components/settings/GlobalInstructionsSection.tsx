import { useEffect, useState } from "react";
import { getMemory, updateMemory } from "../../lib/rest";
import { SettingRow, fieldClass, secondaryButtonClass } from "./SettingRow";

/** Direct editor for MEMORY.md's content, the preferences injected into
 * every conversation's instructions. Saved as you type, once typing
 * pauses, like the rest of Settings. */
export function GlobalInstructionsSection({ active }: { active: boolean }) {
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);

  useEffect(() => {
    if (!active || loaded) return;
    getMemory()
      .then((res) => setContent(res.content))
      .catch(() => setStatus({ text: "Couldn't load Global Instructions.", error: true }))
      .finally(() => setLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, loaded]);

  useEffect(() => {
    if (!editing || draft === content) return;
    const timer = window.setTimeout(async () => {
      try {
        await updateMemory(draft);
        setContent(draft);
        setStatus({ text: "Saved", error: false });
      } catch {
        setStatus({ text: "Couldn't save -- keep typing to try again.", error: true });
      }
    }, 700);
    return () => window.clearTimeout(timer);
  }, [editing, draft, content]);

  const startEditing = () => {
    setDraft(content);
    setStatus(null);
    setEditing(true);
  };

  const stopEditing = () => {
    if (draft !== content) void updateMemory(draft).then(() => setContent(draft));
    setEditing(false);
  };

  return (
    <div className="flex flex-col">
      <SettingRow
        label="Global instructions"
        description="Preferences, conventions or context coscribe should always know. Applies to every new conversation."
        control={
          <button type="button" className={secondaryButtonClass} onClick={editing ? stopEditing : startEditing}>
            {editing ? "Done" : "Edit"}
          </button>
        }
      />
      {editing && (
        <div className="flex flex-col gap-2 pb-2">
          <textarea
            aria-label="Global instructions"
            className={`${fieldClass} h-auto min-h-36 w-full resize-y py-2.5 leading-relaxed`}
            value={draft}
            placeholder="e.g. Always write dates as YYYY-MM-DD. I prefer decks with no more than 6 bullets per slide."
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
          />
          {status && (
            <span className={`text-[13px] ${status.error ? "text-[var(--danger)]" : "text-[var(--muted)]"}`}>
              {status.text}
            </span>
          )}
        </div>
      )}
      {!editing && status?.error && <p className="pb-2 text-[13px] text-[var(--danger)]">{status.text}</p>}
    </div>
  );
}
