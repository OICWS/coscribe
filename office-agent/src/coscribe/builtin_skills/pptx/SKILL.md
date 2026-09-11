---
name: PPTX Slides
description: Color palettes, typography, layout rules, write_pptx's prefab layout directives (icon-list/stat-callout/two-column/svg), and fill_pptx_template for filling coscribe's own pre-designed templates -- plus when a slide still needs run_node_script's real pptxgenjs scripting instead. Load before writing any PowerPoint deck, not just an already-agreed-"polished" one.
---
Deeper design guidance for `write_pptx`/`run_node_script`/`add_pptx_chart`/
`add_pptx_image`/`search_images`/`download_image`/
`set_pptx_background_image`/`add_pptx_scrim` -- on top of what's already in
your base instructions (the `---`-slide-separator convention, the
table-xor-bullets rule, and the overflow_warnings feedback loop). This is
where the actual visual-design judgment lives, starting with `write_pptx`'s
`layout:` directive (see "Prefab layouts" below) before writing a single
slide.

## Before writing content

Pick a color palette that fits the topic, not a generic default. A few
starting points -- adapt the actual hex values to the topic, don't reuse
these verbatim across unrelated decks:

| Feel | Primary | Secondary | Accent |
|---|---|---|---|
| Executive / finance | `1E2761` navy | `CADCFC` ice blue | `FFFFFF` white |
| Environment / growth | `2C5F2D` forest | `97BC62` moss | `F5F5F5` cream |
| Energy / consumer | `F96167` coral | `F9E795` gold | `2F3C7E` navy |
| Trust / health | `028090` teal | `00A896` seafoam | `02C39A` mint |
| Minimal / technical | `36454F` charcoal | `F2F2F2` off-white | `212121` black |

One color should dominate (60-70% of the visual weight); the rest are
supporting or accent only. Don't default to blue for every deck regardless
of topic, and don't default to a cream/beige background when none is
specified -- use white or the palette above instead.

Typography (apply via short titles and paragraph length, since `write_pptx`
doesn't take explicit font sizes): slide titles read as a short phrase, not
a sentence -- if a title needs a subordinate clause, that's body content,
not title content. Body bullets should be short enough that one slide holds
one idea; a bullet that needs two lines to finish a thought is a sign the
slide is trying to do too much.

## Layout variety

Don't put every slide through the same "title + bullets" shape. Vary it
deliberately -- see "Prefab layouts" below for the `layout:` directive
syntax each of these uses:
- A list of discrete points: `layout: icon-list` instead of a plain
  bullet list -- each item gets a colored icon circle, not just a dot.
- A pure-stat slide: `layout: stat-callout`, one short title and up to 4
  number/label pairs as a pipe-table -- let the numbers carry the slide,
  rendered as cards, not buried in a bullet.
- A chart slide: `add_pptx_chart` after `write_pptx`, title states the
  takeaway ("Revenue grew 3x in Q4"), not the axis label ("Revenue by
  Quarter").
- A comparison slide: `layout: two-column` (a real two-content layout,
  correctly spaced) rather than trying to force two columns of bullets
  into one text frame, and rather than a table when the content isn't
  actually tabular data.
- A section-break slide: title only, no body, often a good place for
  `set_pptx_background_image`.

## Icons without a background photo -- the default for a content slide's visual element

`search_images`/`download_image` is for a genuine photo (a section-break
background, a hero image on a title slide) -- **not** the right tool for a
generic icon, and not the default way to give a content slide (a bullet
list of features, a problem statement) a visual element. Two real reasons:
`search_images` results carry no license filtering, and this was verified
live, not assumed -- a downloaded "smart home" background image came back
with a repeated visible watermark baked into the pixels. Say this plainly
if the deck might leave the workspace; don't treat a downloaded image as
pre-cleared. And reaching for a web image for every icon means every
content slide's visual quality depends on what a search happens to
return, which is inconsistent and slow compared to just drawing one.

**For a plain feature/point list, this is exactly what `layout: icon-list`
already is -- use it, not a hand-written `run_node_script` script.** It's
the same "colored circle + glyph" pattern below, except positioned and
looped over your items by tested Python, not typed out fresh by you on
every call -- live-tested, this is where a hand-rolled version has actually
dropped items partway through (a slide meant to have three points
rendering only one, the rest of the slide left blank) in a way `layout:
icon-list` cannot, since it always renders exactly as many circles as
items you gave it. Reach for the pattern below only inside a genuinely
bespoke `run_node_script` slide `layout: icon-list` can't express --
combined with a background photo, mixed with other custom elements, or a
brand icon look via `react-icons` (see below):

```js
// One icon: a filled circle plus a centered short glyph/number on top.
// Reuse this per bullet/feature -- vary the fill color across icons using
// your palette's primary/secondary/accent, don't repeat the same one.
pres.addShape("ellipse", { x: 0.6, y: 1.6, w: 0.5, h: 0.5, fill: { color: "1E2761" } });
pres.addText("1", {
  x: 0.6, y: 1.6, w: 0.5, h: 0.5,
  align: "center", valign: "middle", fontSize: 20, bold: true, color: "FFFFFF",
  margin: 0,
});
```
(add both to the same slide object, e.g. `slide.addShape(...)`/
`slide.addText(...)` -- shown on `pres` here only to keep the snippet
short.) A number works for a numbered list/process; a single bold letter
or a simple Unicode glyph (arrows, checkmarks) works for a feature list.
`rectRadius` on a `roundRect` (a rounded square instead of a full circle)
is the other safe native-shape variant.

If a user asks for a specific brand/icon-font look, or the deck needs
something a native shape genuinely can't express, `react-icons`+`sharp`
(render to SVG, rasterize, embed via `addImage`) is the fuller technique
Anthropic's own pptx Skill uses -- but unlike this project's Python side,
those packages are **not** installed by default here (`run_node_script`'s
own docstring: "this tool cannot install packages on its own"); the user
would need to add them from the Environment settings tab first. Don't
assume they're available -- the native-shape circle above needs nothing
extra and should be the default.

## Backgrounds, images, and legibility

For an actual photo (not a generic icon -- see above): `search_images(query)`
to find candidates, `download_image(url, path)` to fetch the one you
picked into the workspace, then `set_pptx_background_image(path, slide,
image_path)` to apply it -- cropped to cover the slide automatically.

A background photo very often makes title/body text hard to read. Look at
the slide's `preview_path` (or ask `review_work` to) before deciding --
don't assume every background needs a scrim, and don't skip checking either.
When text is hard to read, `add_pptx_scrim(path, slide, opacity, color)`
adds a semi-transparent rectangle behind it, above the photo but below the
text. Pick `color` to contrast with the actual text color on that slide: a
light scrim (`FFFFFF`) under dark text, a dark scrim (`000000`) under light
text. A scrim whose color matches the text barely helps -- this was
verified directly against a rendered slide, not assumed. `opacity` around
`0.35`-`0.55` is a reasonable starting range; heavier if the photo is busy,
lighter if it's already fairly uniform.

## Avoid list (the common tells of an AI-generated deck)

- Accent lines or bars under titles, or decorative stripes down a slide
  edge or card border -- use whitespace or a background tint instead.
- Centered body text or bullet lists -- left-align everything except
  titles.
- A different layout idea on one slide and plain title+bullets everywhere
  else -- commit to a visual approach across the whole deck or keep it
  simple throughout, don't mix.
- Low-contrast text -- light gray on a light or cream background, or dark
  text over a photo with no scrim.
- A background photo applied to every slide out of habit -- a section
  break or a stat callout is a good place for one; a dense bullet slide
  usually isn't.

## Prefab layouts are the default -- run_node_script is the fallback for what they can't express

`write_pptx` supports an optional `layout: <id> [ACCENTHEX]` directive as
the first line of a slide's chunk (right before its `#` heading). Unlike
its bare title+bullets/table default, each layout id is pre-positioned,
non-overlapping-by-construction Python code -- not something either you or
`run_node_script` has to get the coordinates right for on every call. This
is why they're the default now, not `run_node_script`: live-tested
repeatedly, the failure mode of "the model recomputes shape positions from
scratch every call" produced both plain, undesigned bullet-only slides
*and* a real, reproduced layout bug (a subtitle box landing on top of the
body text on some slides but not others) -- a fixed layout can't do either,
by construction.

**`layout: icon-list [ACCENTHEX]`** -- one bullet per item, each rendered
as a colored icon circle + label instead of a plain bullet dot. An item
starting with a short bracketed glyph uses it -- **plain ASCII only: a
letter, digits, initials, or one of `!?*+-=/#@%&`** (`[*]`, `[1]`,
`[AI]`), never an emoji/pictograph and never a spelled-out word (`[📱]`,
`[dash]`, `[star]`) -- both raise `ValueError` rather than rendering
badly, and this was found live, not guessed: an emoji glyph renders using
the font's own built-in color glyph, which ignores the icon circle's
accent-color fill entirely, so some icons come out as a clean colored
circle and others as a mismatched full-color sticker, inconsistently,
within the same deck. A bare bullet (no `[...]`) is auto-numbered. Max 6
items -- split onto another slide above that.
```
layout: icon-list 1E2761
# Feature Highlights
- [*] Real-time energy tracking
- [AI] AI-powered recommendations
- Mobile app control
```
Fixes: plain bullet lists shipped despite being told to add visual
elements -- the single most common defect observed.

**`layout: stat-callout [ACCENTHEX]`** -- the *same* pipe-table syntax
every other table block uses, read as (stat, label) pairs instead of
rendered as a literal grid. Exactly one table, no other content on the
slide. Max 4 stats.
```
layout: stat-callout 028090
# Q4 Highlights
| Stat | Label |
| --- | --- |
| 3x | Revenue growth |
| 12 | New markets |
```
Fixes: a real number either buried in a bullet sentence, or rendered as an
ugly literal 2-column table when it deserved to be the visual focus.

**`layout: two-column`** -- bullets/paragraphs, a line containing exactly
`>>>`, then the right column's bullets/paragraphs. Uses python-pptx's own
stock "Two Content" layout, so the two content areas are already
correctly spaced -- no shape math at all.
```
layout: two-column
# Before vs After
- Manual tracking
- No proactive alerts
>>>
- Real-time dashboards
- Proactive alerts
```
Fixes: a comparison forced into one column of prose, or a table used for
content that isn't actually tabular data. **This layout has no shape of
its own** (it's plain text in two stock placeholders), so
`slides_missing_visual_elements` will correctly flag it unless you
separately add a chart/image on that slide -- this is intended, don't
"fix" it by injecting a decorative shape (see the avoid-list above).

**`layout: svg`** -- for a custom composition none of the other three
layouts can express, but still simple enough to avoid a full
`run_node_script` script. Unlike the other three, nothing after the
directive line goes through this skill's usual markdown grammar (no `#`
heading, no `-` bullets, no pipe table) -- the entire rest of the chunk is
one raw `<svg>...</svg>` document, converted element-for-element into real
native PowerPoint shapes. Only a small, deliberate subset of SVG is
supported -- anything else raises `ValueError` naming `run_node_script` as
the real fallback:
- `<svg width height viewBox?>` root -- establishes the coordinate scale;
  it's uniformly scaled (never stretched) to fit the deck's actual slide
  size, centered with letterboxing if the aspect ratio differs, so don't
  hardcode a 16:9 assumption.
- `<rect x y width height rx? fill?>` -- a rounded rectangle if `rx` is
  present, else a plain rectangle. **A rect at (0, 0) covering the whole
  canvas, listed first, becomes the slide's own background fill instead of
  a shape** -- exactly one such rect per `layout: svg` slide, if any.
- `<circle cx cy r fill?>` / `<ellipse cx cy rx ry fill?>`.
- `<text x y font-family? font-size? font-weight="bold"? fill?>text</text>`
  -- one run, no `<tspan>`. `(x, y)` is the text baseline, same as real SVG.
- `<path d fill?>` -- `d` supports `M/L/H/V/C/Q/Z` (absolute uppercase or
  relative lowercase, including SVG's own implicit-repeat grammar: extra
  coordinate pairs after `M`/`L` without a new letter). No arcs (`A`), no
  shorthand curves (`S`/`T`) -- use `run_node_script` for those. This is
  the way to draw an organic/blob shape, a custom icon, or an abstract
  decorative graphic -- reach for it instead of an image-generation
  approach (this project deliberately has none): zero cost per call, zero
  licensing risk, and it can be colored to match the deck's own theme
  exactly, which a generated photo/illustration can't guarantee.
- `fill="url(#id)"` on any shape above (including the background rect),
  referencing a `<linearGradient id>`/`<radialGradient id>` -- found
  anywhere in the document, `<defs>`-wrapped or not. `<linearGradient
  x1 y1 x2 y2>` (fractions or percentages, SVG's own default direction is
  left-to-right) sets the direction; `<radialGradient>` is always
  centered (no off-center focus point). Each needs at least two
  `<stop offset stop-color>` children -- no `stop-opacity`.
- `fill` (on a shape, or a `<stop>`) accepts a bare or `#`-prefixed
  6-hex-digit color (same convention as everywhere else in this skill), or
  a small set of common CSS color names (`white`, `black`, `navy`, `gold`,
  etc.) -- an unrecognized name raises rather than silently falling back
  to a default color.
- **Not supported at all**: `<line>`/`<polygon>`/`<polyline>`, `<image>`,
  `<g>`/`transform`, filters, clip-paths, `<tspan>`, path arcs/shorthand
  curves, gradient `stop-opacity`, off-center radial gradients. Reach for
  `run_node_script` for any of these.
```
layout: svg
<svg width="960" height="540" viewBox="0 0 960 540">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="1E2761"/>
      <stop offset="100%" stop-color="4C3A9C"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="960" height="540" fill="url(#bg)"/>
  <path d="M700,60 C820,60 900,140 900,260 C900,380 800,460 680,440 Q640,430 660,380 C700,300 660,180 700,60 Z" fill="FFFFFF"/>
  <text x="220" y="260" font-size="36" font-weight="bold" fill="FFFFFF">Real-time tracking</text>
</svg>
```
Fixes: a slide whose composition genuinely doesn't fit a bullet list, an
icon-list, a stat grid, or two columns, without dropping all the way to a
hand-written `pptxgenjs` script for something this small -- including
decorative/hero graphics, since there's no image-generation tool to reach
for instead.

An unrecognized layout id, or a directive line that doesn't parse (bad id,
malformed 6-hex-digit accent), raises immediately naming the four valid
ids -- it never silently falls back to plain bullets.

**Reach for these first.** Only fall back to `run_node_script` for a slide
none of the four can express -- a genuinely custom composition needing a
real photo, an SVG feature `layout: svg` doesn't support (arcs, filters,
groups, off-center radial gradients), or anything else its supported-
element list above doesn't cover. Don't let "this slide is just a title
and three bullets" talk you into plain `write_pptx` either -- `layout:
icon-list` expresses that exact content with a colored icon carrying real
visual weight, for the same one call.

`run_node_script` runs a real Node.js script against `pptxgenjs`
(`require("pptxgenjs")`, already installed), the same library Anthropic's
own pptx Skill is built on, with full control over shape placement, text
boxes, icons, and colored callouts -- this is what a bespoke slide still
needs. Same design rules as `write_pptx`: left-align body text, no
accent lines/bars, one dominant color from a topic-appropriate palette.

Two things about `pptxgenjs` verified directly against the installed
version, not assumed from its docs:
- The default canvas is 10in x 5.625in (`pres.layout = "LAYOUT_WIDE"` for
  the larger 13.3in x 7.5in size) -- set this before adding slides, or
  everything you position assuming the wider canvas will be misplaced.
- A color value must be exactly 6 hex digits (a leading `#` is stripped
  automatically, so `"FF0000"` and `"#FF0000"` both work) -- anything
  else, including an 8-digit hex with alpha, silently falls back to a
  default color rather than erroring, so a slide with an unexpectedly
  wrong color is worth checking for this first.

**`pptxgenjs`'s native shape vocabulary is much wider than "ellipse
icon circle."** `addShape` takes any `pptx.ShapeType.<name>` -- not just
`ellipse`/`roundRect` (the only two named elsewhere in this skill) --
including directional arrows (`rightArrow`, `leftRightArrow`,
`bentArrow`, `curvedRightArrow`, `uturnArrow`, ...), a full flowchart set
(`flowChartProcess`, `flowChartDecision`, `flowChartTerminator`,
`flowChartInputOutput`, `flowChartDocument`, ...), stars, brackets/braces,
callouts, and more -- the complete list is `pptxgenjs`'s own `ShapeType`
enum (`node_modules/pptxgenjs/types/index.d.ts` once installed). Reach
for these instead of `layout: svg`'s own path-drawing for anything that
already has a named preset -- a process-flow diagram, a decision
flowchart, a "step 1 → step 2 → step 3" sequence -- rather than
approximating an arrow out of an SVG `<path>` or falling back to plain
bullets because neither of the four prefab layouts fits a diagram shape.
A connecting line/arrow between two boxes is its own shape
(`pptx.ShapeType.line`, positioned by its own `x/y/w/h` bounding box,
`flipV`/`flipH` for orientation) with `line: { color, width,
beginArrowType, endArrowType }` (`"none" | "arrow" | "triangle" |
"stealth" | "diamond" | "oval"`) for the arrowhead(s) -- not a property
of the two shapes it connects.
```js
// Three-step flow: two process boxes and an arrow between them.
slide.addShape(pres.ShapeType.flowChartProcess, {
  x: 0.5, y: 2, w: 2.2, h: 1, fill: { color: "1E2761" },
});
slide.addText("Collect data", {
  x: 0.5, y: 2, w: 2.2, h: 1, align: "center", valign: "middle",
  color: "FFFFFF", bold: true, fontSize: 14,
});
slide.addShape(pres.ShapeType.line, {
  x: 2.8, y: 2.5, w: 0.7, h: 0,
  line: { color: "1E2761", width: 2, endArrowType: "triangle" },
});
```
Verified directly against the installed package's own TypeScript type
declarations, not assumed from a version-agnostic doc page.

After a `run_node_script` call that writes a `.pptx` file, there's no
`preview_path` the way `write_pptx` returns one -- call
`render_pptx_preview(path)` to render every slide to an image (needs
LibreOffice + poppler-utils; degrades to `preview_skipped_reason` if
either is missing, same as every other preview in this codebase). Its
`preview_paths_csv` can be passed straight through as `review_work`'s
`preview_name` to have the reviewer look at every slide in one call.

## Filling an existing template (`fill_pptx_template`)

Check the available templates (listed in your system instructions, or via
this skill's caller context) *before* reaching for `write_pptx` on any
deck the user will actually look at -- don't wait for them to explicitly
ask for "a template." When a template's fixed slide count is a reasonable
fit for the content, prefer
`fill_pptx_template(path, template_id, content, overwrite)` instead of
`write_pptx`. It fills your title/bullets into an existing template's
existing slides, leaving every one of that template's own decorative
shapes, backgrounds, and images exactly as designed. This is a different
tool from `write_pptx`'s own `template_path` parameter: `template_path`
only inherits a template's theme/fonts and **discards** its actual
designed slide content by clearing every slide before rebuilding from
`content`; `fill_pptx_template` never clears anything -- it writes into
placeholders that already exist on the template's own slides.

**The tradeoff for that real-design guarantee is a fixed shape**:
- The template has a fixed number of slides, and `content`'s
  `---`-separated chunk count must exactly equal it -- there's no way yet
  to repeat a template slide for a variable-length list. If the content
  doesn't fit the template's slide count, either trim it to fit or fall
  back to `write_pptx`.
- Each chunk is title (the first heading) + bullets/paragraphs only --
  **no `layout: <id>` directives and no tables**. Both raise a clear
  error naming `write_pptx` as the alternative. `layout:` positions new
  shapes on a freshly-added slide, which doesn't make sense against an
  existing template slide whose shapes are already placed.
- A chunk's heading/bullets are dropped into whichever placeholder the
  matching template slide actually has (title-type, body-type) -- a
  chunk with content the matching slide has no placeholder for (e.g. a
  heading against a template slide with no title placeholder) raises
  rather than silently discarding it.

Available templates are listed in your system instructions (also visible
via `load_skill("PPTX Slides")`'s caller context). As of this writing,
coscribe ships three, all 4 slides (title, content, content, closing) so
they're interchangeable on shape -- pick by tone:
- **`modern-block`**: solid color blocks and left-aligned typography (no
  photography needed), navy accent -- a good general default.
- **`minimal-light`**: white background, one thin accent rule per title,
  generous margins, no color blocks -- quieter, more editorial; good for
  an internal report or status update.
- **`bold-statement`**: full-bleed dark title/closing slides, a colored
  accent spine down the left edge of content slides -- higher-contrast
  and punchier; good for a short, declarative exec summary or keynote.

Every one of them uses the same slide shape: slide 1 is the title
(heading + subtitle line), slides 2-3 are each one heading + a short
bullet list, slide 4 is a closing call-to-action (heading + one line).
Example call matching that exact shape:
```
fill_pptx_template(
    path="deck.pptx",
    template_id="modern-block",
    content=(
        "# Aurora\n\nSmart home energy intelligence\n"
        "---\n"
        "# The Challenge\n- Rising utility costs\n- No visibility into usage\n"
        "---\n"
        "# How Aurora Solves It\n- Real-time tracking\n- One simple app\n"
        "---\n"
        "# Start Saving Today\n\nVisit aurora.example to get started\n"
    ),
)
```

## Before finishing

**Check `render_pptx_preview`'s three objective fields first -- they need
no rendering and no judgment call, so there's no excuse to skip them.**
`text_overlap_warnings` names pairs of text boxes on the same slide whose
bounding boxes significantly overlap (a real, reproduced bug: a subtitle
box landing on top of the body text on some slides but not others, from a
fixed y-offset that didn't account for how much space the title above it
actually took). `slides_missing_visual_elements` lists slide numbers with
no picture/chart/table/colored shape at all -- background color alone
doesn't count. `low_contrast_warnings` names text whose own color falls
below WCAG's minimum contrast ratio (4.5:1, or 3.0:1 for large text)
against its resolved background -- computed directly from the colors
you already picked, not a rendered check, so it only fires when both
sides have an explicit solid color (text on a photo/gradient isn't
covered by this -- that's what the scrim guidance above and a
`review_work` look are for). Treat any hit the same way you already
treat `overflow_warnings`: fix it (move the overlapping box; add an icon
circle per "Icons without a background photo" above to a flagged slide;
for low contrast, pick a text or fill color further from its
counterpart on the same palette) and call `render_pptx_preview` again
to confirm, before doing anything else.

**You cannot see images yourself -- `preview_path`/`preview_paths` are
filenames, not something you can look at.** `render_pptx_preview` (or
`write_pptx`'s own `preview_path`) only gets you the rendered file; the
only way to actually get a visual check done is `review_work`, whose
reviewer *can* see an image passed as `preview_name`. For any deck of more
than one slide, this is not optional the way a `web_search` call is --
call `render_pptx_preview(path)` and pass its `preview_paths_csv` as
`review_work`'s `preview_name` before considering the deck done. A
single-slide check (just `write_pptx`'s own `preview_path`, or slide 1
only) is not enough: a deck can have a genuinely well-designed title slide
and three plain, undesigned title-and-bullets slides behind it, and title
slides are the easiest ones to get right by accident since they have the
least content to place. Ask the reviewer explicitly to check for overlap,
low contrast, a scrim that didn't help, and -- the most common failure --
any slide that's plain text with no visual element at all; if it names
concrete problems, fix them and re-render/re-review rather than shipping
the deck as-is.
