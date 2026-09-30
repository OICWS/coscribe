import { useState } from "react";
import { CloseIcon } from "../icons";

/** A page capture as a small thumbnail that opens full size. */
export function Screenshot({ name, alt }: { name: string; alt: string }) {
  const [open, setOpen] = useState(false);
  const src = `/api/screenshots/${encodeURIComponent(name)}`;
  return (
    <>
      <button
        type="button"
        className="block w-40 shrink-0 overflow-hidden rounded-lg border border-[var(--border)] hover:border-[var(--muted)]"
        title={alt}
        onClick={() => setOpen(true)}
      >
        <img src={src} alt={alt} className="block h-24 w-full object-cover object-top" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={alt}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6"
          onClick={() => setOpen(false)}
        >
          <button
            type="button"
            aria-label="Close"
            className="absolute right-4 top-4 rounded-md p-1.5 text-white hover:bg-white/10"
            onClick={() => setOpen(false)}
          >
            <CloseIcon className="h-5 w-5" />
          </button>
          <img src={src} alt={alt} className="max-h-full max-w-full rounded-lg shadow-xl" />
        </div>
      )}
    </>
  );
}
