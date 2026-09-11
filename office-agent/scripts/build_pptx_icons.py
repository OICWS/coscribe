"""Generates coscribe's bundled `add_pptx_icon` icon set.

Run with `.venv/bin/python scripts/build_pptx_icons.py` to (re)fetch and
rasterize the curated Lucide icon set under
src/coscribe/builtin_icons/lucide/<name>.png.

Lucide (https://lucide.dev, ISC license -- see the fetched LICENSE file
this script writes alongside the icons) ships icons as SVG source only,
one bare `<path>`/`<circle>`/... per icon with `stroke="currentColor"`.
`add_pptx_icon` (tools/presentations.py) needs a raster image it can
recolor per call via plain PIL alpha-masking (no SVG rasterization
library is a runtime dependency of coscribe itself -- see that module's
docstring) -- so this script is a one-off *build-time* step: it fetches
each icon's real SVG from Lucide's own GitHub repo, replaces
`currentColor` with a literal black so the icon renders as solid black
strokes, rasterizes to a transparent-background PNG via `cairosvg` (a
*dev-only* tool, deliberately not added to pyproject.toml's runtime
dependencies -- installed just for running this script), and commits
the resulting PNGs to the repo. `add_pptx_icon` then loads a bundled PNG
and recolors every non-transparent pixel to the caller's requested
color at call time, keeping the alpha channel (anti-aliased edges) --
verified this reproduces a clean, non-jagged recolor.

Icon selection: a curated ~50-icon subset covering common
business-deck needs (arrows/trend, status, business/org, charts,
actions, common objects) -- not Lucide's full ~1500-icon set, to keep
the bundle small and the tool's docstring's name list scannable. Add a
name to `ICON_NAMES` and rerun this script to bundle another one.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import cairosvg

_ICONS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "coscribe" / "builtin_icons" / "lucide"
)
_RASTER_SIZE = 256
_LUCIDE_RAW_BASE = "https://raw.githubusercontent.com/lucide-icons/lucide/main"

# Verified live against the real Lucide repo (some Lucide names changed
# across versions, e.g. "check-circle" -> "circle-check-big" -- these are
# the *current* real file names, not guessed).
ICON_NAMES = [
    # Arrows / trend
    "arrow-up", "arrow-down", "arrow-right", "arrow-left",
    "trending-up", "trending-down",
    # Status
    "check", "circle-check-big", "x", "circle-x",
    "triangle-alert", "circle-alert", "info",
    # Business / org
    "briefcase", "building-2", "users", "user", "user-check",
    "target", "dollar-sign",
    # Charts
    "chart-bar", "chart-pie", "chart-line", "chart-column",
    # Actions
    "search", "settings", "plus", "minus", "refresh-cw",
    "download", "upload", "share-2", "link",
    # Common objects
    "calendar", "clock", "mail", "phone", "globe",
    "lightbulb", "rocket", "shield-check", "star", "flag",
    "award", "map-pin", "file-text", "folder", "house",
    "package", "truck", "heart", "thumbs-up", "zap",
    "clipboard-check",
]


def _fetch_svg(name: str) -> str:
    url = f"{_LUCIDE_RAW_BASE}/icons/{name}.svg"
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
        content: bytes = response.read()
        return content.decode("utf-8")


def _rasterize(svg_source: str, out_path: Path) -> None:
    # Literal black, not "currentColor" -- cairosvg has no surrounding
    # CSS `color` to resolve that keyword against.
    black_svg = svg_source.replace("currentColor", "#000000")
    cairosvg.svg2png(
        bytestring=black_svg.encode("utf-8"),
        write_to=str(out_path),
        output_width=_RASTER_SIZE,
        output_height=_RASTER_SIZE,
    )


def _write_license() -> None:
    license_text = urllib.request.urlopen(  # noqa: S310
        f"{_LUCIDE_RAW_BASE}/LICENSE", timeout=15
    ).read().decode("utf-8")
    (_ICONS_DIR / "LICENSE").write_text(
        license_text
        + "\n---\n\n"
        "This directory's .png files are coscribe's own build-time raster "
        "renders (scripts/build_pptx_icons.py) of a subset of Lucide's SVG "
        "icons (https://lucide.dev), redistributed under the ISC license "
        "above.\n"
    )


def main() -> None:
    _ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for name in ICON_NAMES:
        svg_source = _fetch_svg(name)
        _rasterize(svg_source, _ICONS_DIR / f"{name}.png")
        print(f"built {name}.png")
    _write_license()
    print(f"\n{len(ICON_NAMES)} icons written to {_ICONS_DIR}")


if __name__ == "__main__":
    main()
