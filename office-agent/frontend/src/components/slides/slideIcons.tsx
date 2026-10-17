import type { SVGProps } from "react";

const stroke = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

/** One slide: the Slide view. */
export function SlideViewIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...stroke} {...props}>
      <rect x="4.5" y="4.5" width="15" height="15" rx="1.5" />
    </svg>
  );
}

/** Four slides: the Canvas view. */
export function CanvasViewIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...stroke} {...props}>
      <rect x="5" y="5" width="5" height="5" rx="1" />
      <rect x="14" y="5" width="5" height="5" rx="1" />
      <rect x="5" y="14" width="5" height="5" rx="1" />
      <rect x="14" y="14" width="5" height="5" rx="1" />
    </svg>
  );
}

/** Two slides, one behind the other: bring the thumbnail strip back. */
export function ThumbnailsIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...stroke} {...props}>
      <rect x="8" y="9" width="12" height="9" rx="1.5" />
      <path d="M5 15V7.5A1.5 1.5 0 0 1 6.5 6H16" />
    </svg>
  );
}

/** A frame with its lower part marked: the properties panel below the slide. */
export function PropertiesBelowIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...stroke} {...props}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M4 14.5h16" />
    </svg>
  );
}

/** A frame with its right part marked: the properties panel beside the slide. */
export function PropertiesBesideIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...stroke} {...props}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M14.5 4v16" />
    </svg>
  );
}

export function ChevronLeftIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...stroke} {...props}>
      <path d="M15 18l-6-6 6-6" />
    </svg>
  );
}
