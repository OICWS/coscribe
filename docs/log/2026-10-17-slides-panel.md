# Slides panel, part B: a built deck opens in the viewer panel, as the mockups draw it

Why: the maintainer's mockups of 2026-10-17 are the Slides panel: the slide on a grey stage, a
floating toolbar, a thumbnail strip, speaker notes, a properties panel and a Canvas view (issue
#420, PR 2 of 3). This is the read-only half; editing comes with the section editor.

## Shipped
- A .pptx opened from Task details goes to the viewer panel, in the main window or its own
  window, like a Word file (`ViewerPanel` picks the viewer by extension).
- `components/slides/`:
  - **Header**: the deck's name with a menu (Open in PowerPoint, Show in folder), then the
    dock's controls.
  - **Toolbar** at the stage's top right: the properties panel and the zoom menu. The zoom menu
    has Zoom in (Ctrl +), Zoom out (Ctrl −), Fit the slide, the current zoom, 25–200 %, and Undo
    and Redo (disabled until editing). 100 % draws the slide 1920 pixels wide.
  - **Slide view**: the slide fitted and centred, drawn from part A's SVG as an image.
  - **Bottom bar**: speaker notes, Slide/Canvas, and the chevron that hides the thumbnails. In
    Canvas view the chevron is disabled and its tooltip says why.
  - **Strip**: the thumbnail strip, with the current slide outlined and numbered.
  - **Mini bar**: with the strip hidden, a floating pill with previous, page, next, notes and
    show thumbnails.
  - **Notes**: the speaker notes under the slide.
  - **Properties**: the slide's transition and background. It sits below the slide, or beside it
    from 880 px, as the two mockups show. The fields show the values; changing them comes with
    the editor.
  - **Canvas view**: every slide in a grid. A click picks one; a double-click or Enter opens it.
  - **Keys**: arrows and Page Up/Down move between slides, Home and End go to the ends, and
    Ctrl + / − / 0 zoom.
- A deck not built by coscribe shows a short note and Open in PowerPoint.
- `--stage-bg`, the grey under the slide, in light and dark.
- `src/lib/slides.test.ts`: fit, zoom steps, transition names.

## Checked
- In Chromium against a live server, the evaluation's six-slide Q3 deck: every state above.
  Screenshots are in the PR.

## Not verified
- The Electron app on Windows (the panel in its own window, fonts). Nothing in it is desktop
  specific.

## Not in this part
- The insert toolbar, the format bar, comments, "+" new slide and editing notes: the editor.
- Download and Present: part C.
