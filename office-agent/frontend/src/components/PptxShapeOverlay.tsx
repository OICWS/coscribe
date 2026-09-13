import { useEffect, useRef, useState } from "react";

/** Same shape as BrowserPanel.tsx's BrowserCapture (Composer.tsx's
 * pendingImages list takes either interchangeably) -- `source: "pptx"`
 * is what lets Composer's outgoing-note wording tell the two apart
 * (see that file's own comment). `text` here already carries the full
 * structured locator (path/slide/shape_index) a model needs to call
 * edit_pptx_shape/edit_pptx_text directly, not just a loose description
 * the way a picked web-page element's text is -- a saved .pptx's shape
 * index is a stable, precise address in a way a live DOM element index
 * never is, so there's real value in giving the model that address
 * outright instead of making it re-derive one via list_pptx_shapes. */
export interface PptxShapeCapture {
  name: string;
  dataUrl: string;
  text: string;
  tag: string;
  source: "pptx";
}

interface PptxShapeInfo {
  index: number;
  shape_type: string | null;
  left_in: number;
  top_in: number;
  width_in: number;
  height_in: number;
  text_preview: string | null;
  is_table: boolean;
  is_picture: boolean;
  is_smartart: boolean;
}

interface PptxShapesResponse {
  shape_count: number;
  shapes: PptxShapeInfo[];
  slide_width_in: number;
  slide_height_in: number;
}

/** Wraps an already-rendered pptx slide preview `<img>` (the same PNG
 * ChatLog.tsx already shows for write_pptx/edit_pptx_*'s preview_path)
 * with clickable regions over each shape, fetched from GET
 * /api/pptx-shapes -- see that endpoint's own comment in web/app.py.
 * Clicking a shape crops the already-loaded image to that shape's own
 * bounding box (client-side canvas, no extra network round trip) and
 * hands the crop plus a precise text locator to onPick, which App.tsx
 * feeds into Composer exactly like a Browser-panel "Select" capture --
 * same interaction, same downstream plumbing, a different source.
 *
 * Fails soft: if the shapes fetch 400s (a since-deleted file, a slide
 * number that no longer exists after a later edit) the plain `<img>`
 * still renders underneath, just without clickable regions -- a worse
 * but not broken experience, matching how a browser tab degrades when
 * a page enhancement fails rather than blanking the whole view. */
export function PptxShapeOverlay({
  src,
  alt,
  className,
  path,
  slide,
  onPick,
}: {
  src: string;
  alt: string;
  className?: string;
  path: string;
  slide: number;
  onPick: (capture: PptxShapeCapture) => void;
}) {
  const [shapes, setShapes] = useState<PptxShapesResponse | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    let cancelled = false;
    setShapes(null);
    const params = new URLSearchParams({ path, slide: String(slide) });
    fetch(`/api/pptx-shapes?${params}`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data: PptxShapesResponse | null) => {
        if (!cancelled) setShapes(data);
      })
      .catch(() => {
        if (!cancelled) setShapes(null);
      });
    return () => {
      cancelled = true;
    };
  }, [path, slide]);

  const pick = (shape: PptxShapeInfo) => {
    const img = imgRef.current;
    const dims = shapes;
    if (!img || !dims || !img.naturalWidth || !img.naturalHeight) return;
    const scaleX = img.naturalWidth / dims.slide_width_in;
    const scaleY = img.naturalHeight / dims.slide_height_in;
    const cropX = Math.max(0, Math.round(shape.left_in * scaleX));
    const cropY = Math.max(0, Math.round(shape.top_in * scaleY));
    const cropW = Math.max(1, Math.round(shape.width_in * scaleX));
    const cropH = Math.max(1, Math.round(shape.height_in * scaleY));
    const canvas = document.createElement("canvas");
    canvas.width = cropW;
    canvas.height = cropH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, cropX, cropY, cropW, cropH, 0, 0, cropW, cropH);
    const label = shape.is_table
      ? "table"
      : shape.is_picture
        ? "picture"
        : shape.is_smartart
          ? "SmartArt"
          : shape.shape_type ?? "shape";
    const textPart = shape.text_preview ? ` -- text: "${shape.text_preview}"` : "";
    onPick({
      name: `slide${slide}-shape${shape.index}.png`,
      dataUrl: canvas.toDataURL("image/png"),
      tag: label,
      source: "pptx",
      text: `"${path}", slide ${slide}, shape_index ${shape.index} (${label})${textPart}`,
    });
  };

  return (
    <div className="relative inline-block">
      <img ref={imgRef} src={src} alt={alt} className={className} draggable={false} />
      {shapes?.shapes.map((shape) => {
        const leftPct = (shape.left_in / shapes.slide_width_in) * 100;
        const topPct = (shape.top_in / shapes.slide_height_in) * 100;
        const widthPct = (shape.width_in / shapes.slide_width_in) * 100;
        const heightPct = (shape.height_in / shapes.slide_height_in) * 100;
        const isHovered = hovered === shape.index;
        return (
          <button
            key={shape.index}
            type="button"
            title={shape.text_preview ?? shape.shape_type ?? "shape"}
            onMouseEnter={() => setHovered(shape.index)}
            onMouseLeave={() => setHovered((v) => (v === shape.index ? null : v))}
            onClick={(e) => {
              e.stopPropagation();
              pick(shape);
            }}
            className="absolute cursor-pointer border-2 border-transparent transition-colors"
            style={{
              left: `${leftPct}%`,
              top: `${topPct}%`,
              width: `${widthPct}%`,
              height: `${heightPct}%`,
              borderColor: isHovered ? "var(--accent)" : "transparent",
              backgroundColor: isHovered ? "color-mix(in srgb, var(--accent) 15%, transparent)" : "transparent",
            }}
          />
        );
      })}
    </div>
  );
}
