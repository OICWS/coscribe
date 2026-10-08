import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CloseIcon } from "./icons";

import { type AttachmentView } from "../lib/attachments";

function pdfPageUrl(path: string, threadId: string, page: number, width: number): string {
  const query = new URLSearchParams({ path, page: String(page), width: String(width) });
  if (threadId) query.set("thread_id", threadId);
  return `/api/attachment/pdf/page?${query}`;
}

function extensionLabel(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toUpperCase() : "FILE";
}

function thumbnailSrc(view: AttachmentView, threadId: string): string | null {
  if (view.kind === "image") return view.src;
  if (view.kind === "pdf") return pdfPageUrl(view.path, threadId, 1, 360);
  return null;
}

async function copyImage(src: string): Promise<void> {
  const image = new Image();
  image.src = src;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  canvas.getContext("2d")?.drawImage(image, 0, 0);
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (blob) await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
}

interface MenuAt {
  x: number;
  y: number;
}

const MENU_ITEM = "flex w-full items-center justify-between gap-8 px-5 py-2 text-left text-[15px] hover:bg-[var(--card-bg)]";

/** Right-click menu of an image or a PDF's page. Other files have none. */
function ImageMenu({ at, src, onClose }: { at: MenuAt; src: string; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (event: Event) => {
      if (event instanceof MouseEvent && ref.current?.contains(event.target as Node)) return;
      onClose();
    };
    const key = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("mousedown", close);
    window.addEventListener("blur", close);
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("blur", close);
      window.removeEventListener("keydown", key);
      window.removeEventListener("scroll", close, true);
    };
  }, [onClose]);

  const hasSelection = (window.getSelection()?.toString() ?? "") !== "";
  const address = src.startsWith("data:") ? null : new URL(src, window.location.href).href;
  const run = (work: () => void | Promise<void>) => () => {
    onClose();
    void Promise.resolve(work()).catch(() => {});
  };
  return createPortal(
    <div
      ref={ref}
      role="menu"
      className="fixed z-[60] min-w-[17rem] rounded-2xl border border-[var(--border)] bg-[var(--panel-bg)] py-2 shadow-[var(--shadow)]"
      style={{ left: Math.min(at.x, window.innerWidth - 280), top: Math.min(at.y, window.innerHeight - 220) }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <button type="button" role="menuitem" className={MENU_ITEM} onClick={run(() => copyImage(src))}>
        Copy Image
      </button>
      {address && (
        <button type="button" role="menuitem" className={MENU_ITEM} onClick={run(() => navigator.clipboard.writeText(address))}>
          Copy Image Address
        </button>
      )}
      <div className="my-2 border-t border-[var(--border)]" />
      <button
        type="button"
        role="menuitem"
        disabled={!hasSelection}
        className={`${MENU_ITEM} disabled:text-[var(--muted)] disabled:hover:bg-transparent`}
        onClick={run(() => navigator.clipboard.writeText(window.getSelection()?.toString() ?? ""))}
      >
        Copy <span className="text-[var(--muted)]">Ctrl+C</span>
      </button>
      <div className="my-2 border-t border-[var(--border)]" />
      <button type="button" role="menuitem" className={MENU_ITEM} onClick={run(() => document.execCommand("selectAll"))}>
        Select All <span className="text-[var(--muted)]">Ctrl+A</span>
      </button>
    </div>,
    document.body,
  );
}

/** One attached file in a row of them: a picture for an image or a PDF's
 * first page, a name and a type for anything else. Click shows it; the × on
 * hover removes it. */
export function AttachmentCard({
  view,
  threadId,
  onOpen,
  onRemove,
}: {
  view: AttachmentView;
  threadId: string;
  onOpen: () => void;
  onRemove?: () => void;
}) {
  const [menu, setMenu] = useState<MenuAt | null>(null);
  const thumbnail = thumbnailSrc(view, threadId);
  return (
    <div className="group relative h-[132px] w-[132px] shrink-0">
      <button
        type="button"
        title={view.name}
        aria-label={`Open ${view.name}`}
        className="relative block h-full w-full overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--bg)] text-left hover:border-[var(--muted)]"
        onClick={onOpen}
        onContextMenu={(e) => {
          e.preventDefault();
          if (thumbnail) setMenu({ x: e.clientX, y: e.clientY });
        }}
      >
        {thumbnail ? (
          <img src={thumbnail} alt={view.name} className="h-full w-full object-cover object-top" draggable={false} />
        ) : (
          <span className="line-clamp-3 break-all px-3.5 pt-3 text-[15px] leading-snug">{view.name}</span>
        )}
        {view.kind !== "image" && (
          <span
            className={`absolute bottom-2.5 left-2.5 rounded-md px-2 py-0.5 text-xs font-medium ${
              view.kind === "pdf"
                ? "border border-[var(--border)] bg-[var(--bg)]/90 text-[var(--fg)]"
                : "bg-[var(--card-bg)] text-[var(--muted)]"
            }`}
          >
            {view.kind === "pdf" ? "PDF" : extensionLabel(view.name)}
          </span>
        )}
      </button>
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove ${view.name}`}
          className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/60 text-xs text-white opacity-0 hover:bg-black/80 group-hover:opacity-100 focus-visible:opacity-100"
          onClick={onRemove}
        >
          ×
        </button>
      )}
      {menu && thumbnail && <ImageMenu at={menu} src={thumbnail} onClose={() => setMenu(null)} />}
    </div>
  );
}

/** A file shown on its own: the picture or the pages when it has them, and a
 * plain line when it can't be shown here. */
export function AttachmentDialog({
  view,
  threadId,
  onClose,
}: {
  view: AttachmentView;
  threadId: string;
  onClose: () => void;
}) {
  const [pages, setPages] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ at: MenuAt; src: string } | null>(null);

  useEffect(() => {
    const key = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);

  useEffect(() => {
    if (view.kind !== "pdf") return;
    let cancelled = false;
    const query = new URLSearchParams({ path: view.path });
    if (threadId) query.set("thread_id", threadId);
    fetch(`/api/attachment/pdf?${query}`)
      .then((res) => res.json() as Promise<{ pages?: number; error?: string }>)
      .then((data) => {
        if (cancelled) return;
        if (data.pages) setPages(data.pages);
        else setError(data.error ?? "This PDF can't be shown here.");
      })
      .catch(() => !cancelled && setError("This PDF can't be shown here."));
    return () => {
      cancelled = true;
    };
  }, [view, threadId]);

  const showable = view.kind !== "file";
  const imageProps = (src: string, alt: string) => ({
    src,
    alt,
    draggable: false,
    onContextMenu: (e: React.MouseEvent) => {
      e.preventDefault();
      setMenu({ at: { x: e.clientX, y: e.clientY }, src });
    },
  });

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div
        role="dialog"
        aria-label={view.name}
        className={`flex max-h-full flex-col overflow-hidden rounded-3xl bg-[var(--panel-bg)] shadow-[var(--shadow)] ${
          showable ? "w-full max-w-[34rem]" : "min-h-[26rem] w-full max-w-[40rem]"
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 px-7 pb-3 pt-6">
          <h2 className="min-w-0 break-words text-2xl font-semibold leading-tight">{view.name}</h2>
          <button
            type="button"
            aria-label="Close"
            autoFocus
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--border)] hover:bg-[var(--card-bg)]"
            onClick={onClose}
          >
            <CloseIcon className="h-5 w-5" />
          </button>
        </div>
        <div className={`min-h-0 flex-1 overflow-y-auto px-7 pb-7 ${showable ? "" : "flex items-center justify-center"}`}>
          {view.kind === "image" && (
            <img {...imageProps(view.src, view.name)} className="mx-auto max-h-[70vh] max-w-full rounded-lg object-contain" />
          )}
          {view.kind === "pdf" && (
            <>
              {error && <p className="py-10 text-center text-sm text-[var(--muted)]">{error}</p>}
              {!error && pages === null && <p className="py-10 text-center text-sm text-[var(--muted)]">Loading...</p>}
              {pages !== null && (
                <div className="flex flex-col items-center gap-4">
                  {Array.from({ length: Math.min(pages, 50) }, (_, i) => (
                    <img
                      key={i}
                      {...imageProps(pdfPageUrl(view.path, threadId, i + 1, 900), `Page ${i + 1}`)}
                      loading="lazy"
                      className="w-full max-w-[26rem] rounded-lg border border-[var(--border)] shadow-sm"
                    />
                  ))}
                  <p className="text-sm text-[var(--muted)]">
                    {pages} {pages === 1 ? "page" : "pages"}
                    {pages > 50 ? " (the first 50 are shown)" : ""}
                  </p>
                </div>
              )}
            </>
          )}
          {view.kind === "file" && (
            <p className="text-[15px]">File previews are not supported for this file type</p>
          )}
        </div>
      </div>
      {menu && <ImageMenu at={menu.at} src={menu.src} onClose={() => setMenu(null)} />}
    </div>,
    document.body,
  );
}
