/** Same shape as the composer's pending images, so a capture joins the
 * message as-is; `text`/`tag` carry what the element says, which a model
 * can't reliably read back off a screenshot. */
export interface BrowserCapture {
  name: string;
  dataUrl: string;
  text?: string;
  tag?: string;
}

export interface PickedElement {
  screenshot: string; // base64 jpeg, no data: prefix yet
  text: string;
  tag: string;
}

export function PickedPreview({
  picked,
  onDiscard,
  onSendToChat,
}: {
  picked: PickedElement;
  onDiscard: () => void;
  onSendToChat: (capture: BrowserCapture) => void;
}) {
  return (
    <div className="flex flex-col gap-2 border-t border-[var(--border)] p-2.5">
      <img
        src={`data:image/jpeg;base64,${picked.screenshot}`}
        alt="Selected element"
        className="max-h-32 w-full rounded-md border border-[var(--border)] object-contain"
      />
      {picked.text && <p className="truncate text-xs text-[var(--muted)]">{picked.text}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" className="rounded-md border border-[var(--border)] px-3 py-1 text-xs" onClick={onDiscard}>
          Discard
        </button>
        <button
          type="button"
          className="rounded-md bg-[var(--primary)] px-3 py-1 text-xs font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)]"
          onClick={() => {
            onSendToChat({
              name: `${picked.tag || "element"}.jpg`,
              dataUrl: `data:image/jpeg;base64,${picked.screenshot}`,
              text: picked.text || undefined,
              tag: picked.tag || undefined,
            });
            onDiscard();
          }}
        >
          Add to chat
        </button>
      </div>
    </div>
  );
}
