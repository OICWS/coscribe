import { useEffect, useRef, useState, type ReactNode } from "react";
import { uploadFile } from "../lib/rest";
import type { CommandInfo } from "../types/session";
import type { BrowserCapture } from "./BrowserPanel";
import { ImageLightbox } from "./ImageLightbox";
import { PlusIcon, ReturnIcon, StopIcon } from "./icons";
import type { PptxShapeCapture } from "./PptxShapeOverlay";
import { RunStatus } from "./RunStatus";

/** Commands that get an instant "state" reply and never run an agent
 * turn -- mirrors app.js's INSTANT_COMMANDS (session.py's FIXED_COMMANDS
 * plus /endworkflow and /saveworkflow). */
const INSTANT_COMMANDS = new Set([
  "/plan",
  "/accept-edits",
  "/compact",
  "/clear",
  "/startworkflow",
  "/endworkflow",
  "/saveworkflow",
]);

export interface ComposerSendPayload {
  /** What the chat-log bubble shows -- the raw typed text, unmodified. */
  displayText: string;
  /** What actually gets sent as user_message.text -- includes the
   * workspace attachment note, if any. */
  outgoingText: string;
  images?: string[];
  isInstant: boolean;
}

interface PendingImage {
  name: string;
  dataUrl: string;
  text?: string;
  tag?: string;
  /** Distinguishes a PptxShapeCapture from a plain BrowserCapture/file
   * upload so the outgoing-note wording below matches its real source --
   * undefined covers both a plain upload (no note at all) and a
   * BrowserCapture (the original "from the browser panel" wording, kept
   * as the default so that call site didn't need touching). */
  source?: "pptx";
}

interface PendingFile {
  name: string;
  path: string;
}

interface PendingPaste {
  text: string;
  charCount: number;
}

// A paste under this size just lands in the textarea as normal typed text --
// only a paste big enough to actually clutter the box (a whole doc, a long
// log) gets collapsed into a removable pill, mirroring claude.ai's own
// "Pasted content" card. No single official threshold to match here, so
// this is a deliberately chosen, documented cutover point rather than a
// guess at an exact upstream number.
const PASTE_CARD_MIN_CHARS = 1000;
const PASTE_CARD_MIN_LINES = 12;

interface ComposerProps {
  turnInFlight: boolean;
  totalTokens: number;
  commands: CommandInfo[];
  modePill: ReactNode;
  modelPicker: ReactNode;
  usageRing: ReactNode;
  onSend: (payload: ComposerSendPayload) => void;
  onStop: () => void;
  onLocalError: (message: string) => void;
  /** Set by BrowserPanel's "Add to chat" -- an element it picked, handed
   * down from App.tsx as a prop instead of a DOM event since there's no
   * drag-and-drop surface here to hook. Consumed the moment it changes
   * (appended to pendingImages below), then immediately reported back via
   * onExternalImageConsumed so App.tsx can null it out -- otherwise the
   * same capture would get re-appended on any later unrelated re-render. */
  externalImage: BrowserCapture | null;
  onExternalImageConsumed: () => void;
  /** Same pattern as externalImage/onExternalImageConsumed above, for a
   * shape clicked in a pptx preview (PptxShapeOverlay) instead of an
   * element picked in the Browser panel -- two props rather than
   * generalizing into one queue since there are exactly two sources
   * today and each already has its own dedicated App.tsx state slot. */
  externalPptxCapture: PptxShapeCapture | null;
  onExternalPptxCaptureConsumed: () => void;
}

export function Composer({
  turnInFlight,
  totalTokens,
  commands,
  modePill,
  modelPicker,
  usageRing,
  onSend,
  onStop,
  onLocalError,
  externalImage,
  onExternalImageConsumed,
  externalPptxCapture,
  onExternalPptxCaptureConsumed,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [pendingPastes, setPendingPastes] = useState<PendingPaste[]>([]);
  const [lightboxSrc, setLightboxSrc] = useState<{ src: string; alt: string } | null>(null);
  const [autocompleteMatches, setAutocompleteMatches] = useState<CommandInfo[]>([]);
  const [autocompleteIndex, setAutocompleteIndex] = useState(-1);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Counts nested dragenter/dragleave pairs (they fire once per child
  // element the pointer crosses, not just once for the whole drop zone) --
  // a plain boolean flickers off every time the pointer passes over a
  // child during the drag, which reads as the highlight randomly blinking.
  const dragCounterRef = useRef(0);

  const autocompleteOpen = autocompleteMatches.length > 0;

  useEffect(() => {
    if (!externalImage) return;
    setPendingImages((prev) => [...prev, externalImage]);
    onExternalImageConsumed();
  }, [externalImage, onExternalImageConsumed]);

  useEffect(() => {
    if (!externalPptxCapture) return;
    setPendingImages((prev) => [...prev, externalPptxCapture]);
    onExternalPptxCaptureConsumed();
  }, [externalPptxCapture, onExternalPptxCaptureConsumed]);

  const resize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };

  const hideAutocomplete = () => {
    setAutocompleteMatches([]);
    setAutocompleteIndex(-1);
  };

  const updateAutocomplete = (text: string) => {
    if (!text.startsWith("/") || text.includes(" ")) {
      hideAutocomplete();
      return;
    }
    const prefix = text.slice(1).toLowerCase();
    const matches = commands.filter((cmd) => cmd.name.toLowerCase().startsWith(prefix));
    // Nothing left to complete once the typed prefix is already an exact
    // full match of the (only) remaining candidate.
    if (matches.length === 1 && matches[0].name.toLowerCase() === prefix) {
      hideAutocomplete();
      return;
    }
    setAutocompleteMatches(matches);
    setAutocompleteIndex(matches.length > 0 ? 0 : -1);
  };

  const selectAutocomplete = (index: number) => {
    const cmd = autocompleteMatches[index];
    if (!cmd) return;
    setValue(`/${cmd.name} `);
    hideAutocomplete();
    requestAnimationFrame(() => {
      resize();
      textareaRef.current?.focus();
    });
  };

  const submit = () => {
    const text = value;
    if (
      !text.trim() &&
      pendingImages.length === 0 &&
      pendingFiles.length === 0 &&
      pendingPastes.length === 0
    )
      return;
    // Real, user-reported bug: nothing gated a new message while a turn
    // was already in flight -- the Send button visually swaps to Stop in
    // that state (see the turnInFlight ? ... below), but Enter still ran
    // this function directly, so typing and hitting Enter sent a second
    // message anyway. The backend doesn't reject it either (a second
    // handle_user_message call just queues behind the running turn's
    // _turn_lock) -- so it wasn't unsafe, just silently confusing: the
    // bubble appears immediately (the optimistic local echo below) with
    // no sign it's not actually being worked on yet. "/stop" is the one
    // deliberate exception -- App.tsx's onSend special-cases that exact
    // text to reach the running turn directly instead of queuing, so it
    // must stay sendable regardless of turnInFlight or it would have no
    // way to reach a turn stuck deep enough that the Stop button itself
    // isn't rendering the way the user expects.
    if (turnInFlight && text.trim().toLowerCase() !== "/stop") return;

    // "/stop" reaches the currently-running turn directly (a dedicated WS
    // "stop" message, not "user_message") -- App.tsx's onSend special-cases
    // displayText for this, same as it already does for the Stop button.
    let outgoingText = text;
    if (pendingFiles.length > 0) {
      const note = `(Attached files, now in the workspace: ${pendingFiles.map((f) => f.path).join(", ")})`;
      outgoingText = text ? `${text}\n\n${note}` : note;
    }
    // Unlike pendingFiles above, a paste has no workspace path to point
    // at -- it's inline text that never left the browser -- so its full
    // content goes straight into outgoingText verbatim (what the model
    // sees is identical to what a normal, uncollapsed paste would have
    // produced). displayText below is left untouched, same as the file
    // note above: the chat bubble stays short, the model still gets
    // everything.
    if (pendingPastes.length > 0) {
      const pasted = pendingPastes.map((p) => p.text).join("\n\n");
      outgoingText = outgoingText ? `${outgoingText}\n\n${pasted}` : pasted;
    }
    // Browser-panel "Select an element" captures include the element's
    // own extracted text alongside the screenshot -- previously read
    // into `picked.text`/shown in BrowserPanel's own preview, but then
    // silently dropped at "Add to chat": the model only ever received
    // the image, never the text it can't reliably read back out of a
    // picture (small text, a truncated/cropped icon label, etc). Same
    // "(...)" contextual-note convention as the file-attachment note
    // above, not a separate mechanism. A pptx-shape pick's own `.text`
    // is already the full note content (a precise path/slide/shape_index
    // locator, not just a loose description -- see PptxShapeOverlay's
    // own comment), so it's used verbatim instead of wrapped in the
    // browser-panel-specific phrasing.
    const picksWithText = pendingImages.filter((img) => img.text);
    if (picksWithText.length > 0) {
      const note = picksWithText
        .map((img) =>
          img.source === "pptx"
            ? `(Selected from the pptx preview: ${img.text})`
            : `(Selected <${img.tag ?? "element"}> from the browser panel -- text: "${img.text}")`,
        )
        .join("\n");
      outgoingText = outgoingText ? `${outgoingText}\n\n${note}` : note;
    }
    const commandWord = text.trim().split(/\s+/, 1)[0]?.toLowerCase();
    onSend({
      displayText: text || "(attachment sent)",
      outgoingText,
      images: pendingImages.length > 0 ? pendingImages.map((f) => f.dataUrl) : undefined,
      isInstant: INSTANT_COMMANDS.has(commandWord ?? ""),
    });
    setValue("");
    setPendingImages([]);
    setPendingFiles([]);
    setPendingPastes([]);
    requestAnimationFrame(resize);
  };

  const performSend = () => {
    if (autocompleteOpen && autocompleteIndex >= 0) {
      selectAutocomplete(autocompleteIndex);
      return;
    }
    submit();
    hideAutocomplete();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (autocompleteOpen) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setAutocompleteIndex((i) => (i + 1) % autocompleteMatches.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setAutocompleteIndex((i) => (i - 1 + autocompleteMatches.length) % autocompleteMatches.length);
        return;
      }
      if (event.key === "Tab" || event.key === "Enter") {
        event.preventDefault();
        selectAutocomplete(autocompleteIndex);
        return;
      }
      if (event.key === "Escape") {
        hideAutocomplete();
        return;
      }
    }

    if (event.key !== "Enter" || event.nativeEvent.isComposing) return;
    if (event.ctrlKey || event.metaKey) {
      // Native textarea behavior has no "insert newline" for Ctrl/Cmd+Enter
      // (unlike bare Enter or Shift+Enter) -- insert it by hand.
      event.preventDefault();
      const el = event.currentTarget;
      const start = el.selectionStart;
      const end = el.selectionEnd;
      const next = value.slice(0, start) + "\n" + value.slice(end);
      setValue(next);
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = start + 1;
        resize();
      });
      return;
    }
    if (!event.shiftKey) {
      event.preventDefault();
      performSend();
    }
  };

  const onPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    // A copied *file* (e.g. from Finder/Explorer, or a screenshot tool
    // that puts image bytes straight on the clipboard) shows up in
    // clipboardData.files, not as text -- previously fell through to
    // "let it behave natively," which for a plain <textarea> means
    // nothing happens at all. Route it through the same onFileChosen
    // path drag-and-drop already uses.
    if (event.clipboardData.files.length > 0) {
      event.preventDefault();
      for (const file of Array.from(event.clipboardData.files)) void onFileChosen(file);
      return;
    }
    const text = event.clipboardData.getData("text/plain");
    if (!text) return;
    const lineCount = text.split("\n").length;
    if (text.length < PASTE_CARD_MIN_CHARS && lineCount < PASTE_CARD_MIN_LINES) return;
    event.preventDefault();
    setPendingPastes((prev) => [...prev, { text, charCount: text.length }]);
  };

  const onDragEnter = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!event.dataTransfer.types.includes("Files")) return;
    dragCounterRef.current += 1;
    setIsDraggingOver(true);
  };

  const onDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setIsDraggingOver(false);
  };

  const onDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    // Required for onDrop to fire at all -- a plain drag target rejects
    // the drop by default unless dragover is explicitly prevented.
    event.preventDefault();
  };

  const onDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragCounterRef.current = 0;
    setIsDraggingOver(false);
    const files = Array.from(event.dataTransfer.files);
    for (const file of files) {
      void onFileChosen(file);
    }
  };

  const onFileChosen = async (file: File) => {
    if (file.type.startsWith("image/")) {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as string);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      setPendingImages((prev) => [...prev, { name: file.name, dataUrl }]);
      return;
    }
    const result = await uploadFile(file);
    if ("error" in result) {
      onLocalError(`couldn't attach ${file.name} -- ${result.error}`);
      return;
    }
    setPendingFiles((prev) => [...prev, { name: file.name, path: result.path }]);
  };

  const canSend =
    value.trim().length > 0 ||
    pendingImages.length > 0 ||
    pendingFiles.length > 0 ||
    pendingPastes.length > 0;

  return (
    <>
    <div
      data-testid="composer"
      className="mx-auto w-full max-w-[760px] px-4 pb-4"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {(pendingImages.length > 0 || pendingFiles.length > 0 || pendingPastes.length > 0) && (
        <div className="mb-2 flex flex-wrap gap-2">
          {pendingImages.map((img, i) => (
            <span
              key={`img-${i}`}
              className="group relative flex h-14 w-14 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[var(--border)]"
            >
              <button
                type="button"
                title={`Preview ${img.name}`}
                className="h-full w-full cursor-zoom-in"
                onClick={() => setLightboxSrc({ src: img.dataUrl, alt: img.name })}
              >
                <img src={img.dataUrl} alt={img.name} className="h-full w-full object-cover" />
              </button>
              <button
                type="button"
                aria-label={`Remove ${img.name}`}
                className="absolute right-0.5 top-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-black/60 text-xs text-white opacity-0 hover:bg-black/80 group-hover:opacity-100"
                onClick={() => setPendingImages((prev) => prev.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </span>
          ))}
          {pendingFiles.map((f, i) => (
            <span
              key={`file-${i}`}
              className="flex max-w-[220px] items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--card-bg)] py-1.5 pl-3 pr-1.5 text-xs"
            >
              <span className="truncate">{f.name}</span>
              <button
                type="button"
                aria-label={`Remove ${f.name}`}
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--border)]"
                onClick={() => setPendingFiles((prev) => prev.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </span>
          ))}
          {pendingPastes.map((p, i) => (
            <span
              key={`paste-${i}`}
              className="flex max-w-[220px] items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--card-bg)] py-1.5 pl-3 pr-1.5 text-xs"
            >
              <span className="truncate">Pasted ({p.charCount.toLocaleString()} chars)</span>
              <button
                type="button"
                aria-label="Remove pasted content"
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--border)]"
                onClick={() => setPendingPastes((prev) => prev.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      <div
        className={`relative rounded-2xl border p-3 shadow-sm transition-colors ${
          isDraggingOver
            ? "border-[var(--accent)] bg-[var(--card-bg)]"
            : "border-[var(--border)] bg-[var(--bg)]"
        }`}
      >
        {isDraggingOver && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-2xl text-sm text-[var(--accent)]">
            Drop to attach
          </div>
        )}
        {autocompleteOpen && (
          <div className="absolute bottom-full left-3 right-3 mb-1 max-h-60 overflow-y-auto rounded-[10px] border border-[var(--border)] bg-[var(--bg)] shadow-[var(--shadow)]">
            {autocompleteMatches.map((cmd, i) => (
              <div
                key={cmd.name}
                className={`flex items-center justify-between gap-3 px-3 py-2 text-sm ${i === autocompleteIndex ? "bg-[var(--card-bg)]" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  selectAutocomplete(i);
                }}
              >
                <span className="font-mono text-[var(--accent)]">/{cmd.name}</span>
                <span className="truncate text-xs text-[var(--muted)]">{cmd.description}</span>
              </div>
            ))}
          </div>
        )}

        <textarea
          ref={textareaRef}
          className="max-h-40 w-full resize-none bg-transparent px-0.5 pr-9 text-[0.9rem] leading-normal outline-none"
          rows={1}
          value={value}
          placeholder="Type / for commands"
          onChange={(event) => {
            setValue(event.target.value);
            updateAutocomplete(event.target.value);
            resize();
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />

        {/* Send/Stop lives inside the input box itself, bottom-right --
         * a small bordered icon button (feather's corner-down-left
         * "return" glyph) instead of the old large filled-accent circle
         * sitting in its own row below. */}
        {turnInFlight ? (
          <button
            type="button"
            title="Stop"
            className="absolute bottom-3 right-3 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[var(--fg)] text-[var(--bg)]"
            onClick={onStop}
          >
            {/* rounded-full, not rounded-lg like the button itself -- this
             * relies on the classic "colored top border only" CSS spinner
             * trick (a transparent ring with just one edge colored, spun
             * via animate-spin), which only reads as a smooth arc on a
             * true circle. On a rounded *rectangle* the colored segment is
             * a flat edge with two corners, which rotates into a stray
             * blob poking out past a corner instead -- a real, reported
             * bug ("a weird symbol spinning"), not a hypothetical. */}
            <span className="absolute inset-[-3px] animate-spin rounded-full border-2 border-transparent border-t-[var(--fg)]" />
            <StopIcon className="h-[13px] w-[13px]" />
          </button>
        ) : (
          <button
            type="button"
            title="Send"
            className="absolute bottom-3 right-3 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[var(--border)] text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-40 disabled:hover:bg-transparent"
            onClick={performSend}
            disabled={!canSend}
          >
            <ReturnIcon className="h-[15px] w-[15px]" />
          </button>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 pt-2">
        <div className="flex items-center gap-0.5">
          {modePill}
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) onFileChosen(file);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            title="Attach a file or photo"
            className="flex h-7 w-7 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
            onClick={() => fileInputRef.current?.click()}
          >
            <PlusIcon className="h-[18px] w-[18px]" />
          </button>
        </div>
        <RunStatus turnInFlight={turnInFlight} totalTokens={totalTokens} />
        <div className="flex items-center gap-1.5">
          {modelPicker}
          {usageRing}
        </div>
      </div>
    </div>
    {lightboxSrc && (
      <ImageLightbox src={lightboxSrc.src} alt={lightboxSrc.alt} onClose={() => setLightboxSrc(null)} />
    )}
    </>
  );
}
