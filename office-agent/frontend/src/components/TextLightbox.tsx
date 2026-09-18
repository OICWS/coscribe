/** Full-content click-to-preview overlay for a pasted-text attachment --
 * same overlay/click-outside-to-close convention as ImageLightbox, just for
 * plain text too long to read inline (the composer's pending-paste chip).
 * Click the backdrop to close; click inside the text box does not, so the
 * text stays selectable/copyable without accidentally dismissing it. */
export function TextLightbox({ text, onClose }: { text: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
    >
      <div
        className="max-h-full w-full max-w-2xl overflow-y-auto rounded-lg bg-[var(--bg)] p-4 text-sm text-[var(--fg)] shadow-[var(--shadow)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="whitespace-pre-wrap">{text}</div>
      </div>
    </div>
  );
}
