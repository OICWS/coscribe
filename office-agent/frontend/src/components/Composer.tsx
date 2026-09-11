import { useEffect, useRef, useState, type ReactNode } from "react";
import { uploadFile } from "../lib/rest";
import type { CommandInfo } from "../types/session";
import type { BrowserCapture } from "./BrowserPanel";
import { PlusIcon, ReturnIcon, StopIcon } from "./icons";
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
}

interface PendingFile {
  name: string;
  path: string;
}

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
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [autocompleteMatches, setAutocompleteMatches] = useState<CommandInfo[]>([]);
  const [autocompleteIndex, setAutocompleteIndex] = useState(-1);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const autocompleteOpen = autocompleteMatches.length > 0;

  useEffect(() => {
    if (!externalImage) return;
    setPendingImages((prev) => [...prev, externalImage]);
    onExternalImageConsumed();
  }, [externalImage, onExternalImageConsumed]);

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
    if (!text.trim() && pendingImages.length === 0 && pendingFiles.length === 0) return;
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
    // Browser-panel "Select an element" captures include the element's
    // own extracted text alongside the screenshot -- previously read
    // into `picked.text`/shown in BrowserPanel's own preview, but then
    // silently dropped at "Add to chat": the model only ever received
    // the image, never the text it can't reliably read back out of a
    // picture (small text, a truncated/cropped icon label, etc). Same
    // "(...)" contextual-note convention as the file-attachment note
    // above, not a separate mechanism.
    const picksWithText = pendingImages.filter((img) => img.text);
    if (picksWithText.length > 0) {
      const note = picksWithText
        .map((img) => `(Selected <${img.tag ?? "element"}> from the browser panel -- text: "${img.text}")`)
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

  const canSend = value.trim().length > 0 || pendingImages.length > 0 || pendingFiles.length > 0;

  return (
    <div className="mx-auto w-full max-w-[760px] px-4 pb-4">
      {(pendingImages.length > 0 || pendingFiles.length > 0) && (
        <div className="mb-2 flex flex-wrap gap-2">
          {pendingImages.map((img, i) => (
            <span
              key={`img-${i}`}
              className="flex max-w-[220px] items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--card-bg)] py-1.5 pl-3 pr-1.5 text-xs"
            >
              <span className="truncate">{img.name}</span>
              <button
                type="button"
                aria-label={`Remove ${img.name}`}
                className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--border)]"
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
        </div>
      )}

      <div className="relative rounded-2xl border border-[var(--border)] bg-[var(--bg)] p-3 shadow-sm">
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
  );
}
