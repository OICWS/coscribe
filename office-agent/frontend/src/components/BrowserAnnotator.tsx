import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowToolIcon,
  CheckIcon,
  ChevronDownIcon,
  CloseIcon,
  EllipseToolIcon,
  LineToolIcon,
  PencilIcon,
  RectangleToolIcon,
  RedoIcon,
  TextToolIcon,
  TrashIcon,
  UndoIcon,
} from "./icons";
import {
  ANNOTATION_COLORS,
  TEXT_SIZE,
  drawShapes,
  isNegligible,
  renderAnnotated,
  type AnnotationTool,
  type Shape,
} from "../lib/annotation";
import { useClickOutside } from "../lib/useClickOutside";
import type { BrowserCapture } from "./PickedPreview";

export interface PageCapture {
  dataUrl: string;
  title: string;
  url: string;
}

const TOOLS: Array<{ id: AnnotationTool; label: string; icon: (props: { className?: string }) => ReactNode }> = [
  { id: "pen", label: "Pen", icon: PencilIcon },
  { id: "line", label: "Line", icon: LineToolIcon },
  { id: "arrow", label: "Arrow", icon: ArrowToolIcon },
  { id: "rectangle", label: "Rectangle", icon: RectangleToolIcon },
  { id: "ellipse", label: "Ellipse", icon: EllipseToolIcon },
  { id: "text", label: "Text", icon: TextToolIcon },
];

const toolbarButton =
  "flex h-8 items-center justify-center gap-1 rounded-lg px-1.5 text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-35 disabled:hover:bg-transparent";

function Swatch({ color }: { color: string }) {
  return <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-black/15" style={{ background: color }} />;
}

/** Drop-up list for the toolbar's tool and color pickers. */
function PickerMenu<T extends string>({
  label,
  button,
  options,
  selected,
  onSelect,
}: {
  label: string;
  button: ReactNode;
  options: Array<{ id: T; label: string; leading: ReactNode }>;
  selected: T;
  onSelect: (id: T) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false), open);
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`${toolbarButton} ${open ? "bg-[var(--card-bg)] text-[var(--fg)]" : ""}`}
        onClick={() => setOpen((v) => !v)}
      >
        {button}
        <ChevronDownIcon className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          role="menu"
          aria-label={label}
          className="absolute bottom-full left-0 z-30 mb-2 min-w-44 rounded-xl border border-[var(--border)] bg-[var(--panel-bg)] py-1.5 shadow-[var(--shadow)]"
        >
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              role="menuitemradio"
              aria-checked={option.id === selected}
              className="flex w-full items-center gap-2.5 px-3.5 py-1.5 text-left text-sm text-[var(--fg)] hover:bg-[var(--card-bg)]"
              onClick={() => {
                onSelect(option.id);
                setOpen(false);
              }}
            >
              {option.leading}
              <span className="flex-1">{option.label}</span>
              {option.id === selected && <CheckIcon className="h-4 w-4 text-blue-500" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** A still screenshot of the current page to draw on -- pen, line,
 * arrow, rectangle, ellipse, text -- and send to the chat, for pointing
 * at something faster than describing it. */
export function BrowserAnnotator({
  capture,
  onClose,
  onSendToChat,
}: {
  capture: PageCapture;
  onClose: () => void;
  onSendToChat: (capture: BrowserCapture) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const draftRef = useRef<Shape | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [tool, setTool] = useState<AnnotationTool>("pen");
  const [color, setColor] = useState(ANNOTATION_COLORS[0].value);
  const [shapes, setShapes] = useState<Shape[]>([]);
  const [past, setPast] = useState<Shape[][]>([]);
  const [future, setFuture] = useState<Shape[][]>([]);
  const [textDraft, setTextDraft] = useState<{ x: number; y: number; value: string } | null>(null);
  const [sending, setSending] = useState(false);

  const commit = (next: Shape[]) => {
    setPast((p) => [...p, shapes]);
    setFuture([]);
    setShapes(next);
  };
  const undo = () => {
    if (!past.length) return;
    setFuture((f) => [shapes, ...f]);
    setShapes(past[past.length - 1]);
    setPast((p) => p.slice(0, -1));
  };
  const redo = () => {
    if (!future.length) return;
    setPast((p) => [...p, shapes]);
    setShapes(future[0]);
    setFuture((f) => f.slice(1));
  };

  const redraw = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const all = draftRef.current ? [...shapes, draftRef.current] : shapes;
    drawShapes(ctx, all, dpr);
  };

  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: Math.round(entry.contentRect.width), height: Math.round(entry.contentRect.height) });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(redraw);

  const commitText = () => {
    if (!textDraft) return;
    const shape: Shape = { kind: "text", color, at: { x: textDraft.x, y: textDraft.y }, text: textDraft.value };
    setTextDraft(null);
    if (!isNegligible(shape)) commit([...shapes, shape]);
  };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (textDraft) return;
      const mod = e.ctrlKey || e.metaKey;
      if (e.key === "Escape") onClose();
      else if (mod && e.key.toLowerCase() === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if (mod && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const pointAt = (e: React.PointerEvent) => {
    const box = canvasRef.current!.getBoundingClientRect();
    return { x: e.clientX - box.left, y: e.clientY - box.top };
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (e.button !== 0) return;
    const p = pointAt(e);
    if (tool === "text") {
      // Otherwise the press moves focus off the text box it's about to open.
      e.preventDefault();
      if (textDraft) commitText();
      setTextDraft({ x: p.x, y: p.y - TEXT_SIZE / 2, value: "" });
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    draftRef.current = tool === "pen" ? { kind: "pen", color, points: [p] } : { kind: tool, color, from: p, to: p };
    redraw();
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const draft = draftRef.current;
    if (!draft) return;
    const p = pointAt(e);
    if (draft.kind === "pen") draft.points.push(p);
    else if (draft.kind !== "text") draft.to = p;
    redraw();
  };
  const onPointerUp = () => {
    const draft = draftRef.current;
    draftRef.current = null;
    if (draft && !isNegligible(draft)) commit([...shapes, draft]);
    else redraw();
  };

  const send = async () => {
    setSending(true);
    try {
      const dataUrl = await renderAnnotated(capture.dataUrl, shapes, size.width || 1);
      const notes = shapes.flatMap((s) => (s.kind === "text" ? [`"${s.text}"`] : []));
      let text = `Annotated screenshot of the browser page "${capture.title}" (${capture.url}) -- the drawn marks point at what the user means`;
      if (notes.length) text += `; notes written on it: ${notes.join(", ")}`;
      onSendToChat({ name: "annotation.jpg", dataUrl, text, source: "annotation" });
      onClose();
    } catch {
      // The drawing stays open, so nothing is lost and "Add to chat" can be retried.
    } finally {
      setSending(false);
    }
  };

  const currentTool = TOOLS.find((t) => t.id === tool)!;
  const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;

  return (
    <div ref={wrapperRef} className="absolute inset-0 overflow-hidden" data-testid="browser-annotator">
      <img src={capture.dataUrl} alt="" draggable={false} className="pointer-events-none absolute inset-0 h-full w-full select-none" />
      <canvas
        ref={canvasRef}
        aria-label="Drawing area"
        width={Math.round(size.width * dpr)}
        height={Math.round(size.height * dpr)}
        style={{ width: size.width, height: size.height }}
        className={`absolute inset-0 touch-none ${tool === "text" ? "cursor-text" : "cursor-crosshair"}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      />
      {textDraft && (
        <input
          autoFocus
          aria-label="Annotation text"
          className="absolute min-w-24 border-b border-dashed bg-transparent p-0 font-semibold outline-none"
          style={{
            left: textDraft.x,
            top: textDraft.y,
            color,
            borderColor: color,
            fontSize: TEXT_SIZE,
            lineHeight: 1.2,
            width: Math.max(96, (textDraft.value.length + 2) * TEXT_SIZE * 0.6),
          }}
          value={textDraft.value}
          onChange={(e) => setTextDraft({ ...textDraft, value: e.target.value })}
          onBlur={commitText}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitText();
            if (e.key === "Escape") setTextDraft(null);
          }}
        />
      )}

      <div
        role="toolbar"
        aria-label="Annotate"
        className="absolute bottom-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-1 whitespace-nowrap rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] p-1.5 shadow-[var(--shadow)]"
      >
        <PickerMenu
          label="Drawing tool"
          button={<currentTool.icon className="h-4 w-4" />}
          options={TOOLS.map((t) => ({ id: t.id, label: t.label, leading: <t.icon className="h-4 w-4 text-[var(--muted)]" /> }))}
          selected={tool}
          onSelect={setTool}
        />
        <PickerMenu
          label="Color"
          button={<Swatch color={color} />}
          options={ANNOTATION_COLORS.map((c) => ({ id: c.value, label: c.name, leading: <Swatch color={c.value} /> }))}
          selected={color}
          onSelect={setColor}
        />
        <span className="mx-1 h-5 w-px bg-[var(--border)]" />
        <button type="button" title="Undo" aria-label="Undo" disabled={!past.length} className={toolbarButton} onClick={undo}>
          <UndoIcon className="h-4 w-4" />
        </button>
        <button type="button" title="Redo" aria-label="Redo" disabled={!future.length} className={toolbarButton} onClick={redo}>
          <RedoIcon className="h-4 w-4" />
        </button>
        <button
          type="button"
          title="Clear drawing"
          aria-label="Clear drawing"
          disabled={!shapes.length}
          className={toolbarButton}
          onClick={() => commit([])}
        >
          <TrashIcon className="h-4 w-4" />
        </button>
        <button type="button" title="Stop annotating" aria-label="Stop annotating" className={`${toolbarButton} ml-1`} onClick={onClose}>
          <CloseIcon className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={sending}
          className="ml-1 h-8 rounded-lg bg-[var(--primary)] px-3.5 text-sm font-medium text-[var(--primary-fg)] hover:bg-[var(--primary-hover)] disabled:opacity-60"
          onClick={() => void send()}
        >
          Add to chat
        </button>
      </div>
    </div>
  );
}
