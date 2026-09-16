/** Full-size click-to-preview overlay for a thumbnail -- shared between
 * Composer's pending-attachment row and ChatLog's sent-message view, the
 * two places an image thumbnail is clickable. Same overlay/click-outside-
 * to-close convention as ConfirmDialog, just no buttons: image fills as
 * much of the viewport as it can without cropping, click anywhere (or the
 * image itself) to close. */
export function ImageLightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
      onClick={onClose}
    >
      <img
        src={src}
        alt={alt}
        className="max-h-full max-w-full cursor-zoom-out rounded-lg object-contain shadow-[var(--shadow)]"
        onClick={onClose}
      />
    </div>
  );
}
