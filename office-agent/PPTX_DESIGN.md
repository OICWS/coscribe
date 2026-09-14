# PPTX module design notes

Working reference for the "edit real PowerPoint files" effort -- what's
shipped, what's technically verified but not built, what a competing AI
PPT tool does differently (researched by proxy, not guessed), and the
agreed priority order. Written so a future session (mine or otherwise)
doesn't have to re-derive any of this from chat history. Update this file
as items land or new research changes a conclusion -- treat it as the
source of truth for PPTX-module status, the way `ROADMAP.md` is for the
broader architecture effort (this file is intentionally separate from
`ROADMAP.md`: different owner/workstream, narrower scope, avoids
colliding with that file's own active edits).

All code lives in `src/coscribe/tools/presentations.py` unless noted.

---

## 0. FIXED (commit `3b169c2`) -- existing table/stat-callout layouts looked bad

Discovered by direct visual comparison against the competing tool's real
rendered slides (screenshots, not files this time), not by inspecting
XML. Everything in §4-§8 below is about *missing capabilities* -- this
was different and more urgent: **capabilities coscribe already shipped
produced genuinely worse-looking output than a competitor's, for
concrete, fixable, code reasons, affecting every deck coscribe had ever
generated, not just some future feature.** Both fixed as of `3b169c2`
-- kept below for the reasoning/evidence trail, not as an open item.

**Confirmed in real PowerPoint** (commit `ff6b818`): the user opened the
regenerated deck and reported cards "稍好" (better) but caught three more
concrete, smaller defects the sandbox's own lack of rendering couldn't
have caught -- all fixed in `ff6b818`:
- Table wasn't centered (`_add_table_slide` used coordinates sized for
  python-pptx's default 10in canvas; write_pptx always resizes to
  13.333in widescreen after construction) -- now uses `_content_area`
  like icon-list/stat-callout already did.
- Cards had a large empty gap at the bottom (`card_height` was
  `card_width * 0.7`, too tall for two short text lines) -- reduced to
  `* 0.4`.
- A single flowing paragraph (no markdown bullet) rendered with a
  meaningless bullet glyph in front of it -- `_fill_content_placeholder`
  never distinguished `Block(kind="paragraph")` from `kind="bullet"` at
  the XML level; fixed with an explicit `<a:buNone/>` on paragraph-kind
  blocks only.

**Also raised, not yet acted on -- a real, separate, larger question**:
the same feedback round flagged that a freeform (non-template)
`write_pptx` deck has **no cohesive background color/theme identity** at
all -- every slide is on plain white, unlike the competing tool's decks,
which are bound to one fixed, comprehensive design system (their
"Modernist": full color-token ramps, one heading/body typeface, defined
card/table/nav components) applied everywhere automatically. This is
*not* a bug the way the three above were -- `write_pptx`'s blank-canvas
default is deliberate, and `fill_pptx_template` already gives full
background/theme identity, just gated on the deck's slide count
matching one of the 3 bundled templates' fixed 4 slides. A 9+ slide
freeform deck (exactly this test's shape) has no path to a cohesive
background today. Needs a real product decision, not a silent code
change: (a) default `write_pptx` to always inheriting one of the
bundled templates' theme (via `template_path`, already a real parameter)
even for freeform content, (b) add a `background:`-style directive to
`write_pptx`'s own content grammar so a freeform deck can opt into a
whole-deck color identity without needing to match a template's fixed
slide count, or (c) leave freeform decks intentionally blank/neutral and
push harder on `fill_pptx_template` usage/more bundled templates with
varied slide counts instead. Not decided.

**Open-source prior-art research (background Agent, completed) -- answers
the user's follow-up "look for an existing open-source theme system we
could reuse":**
- **No off-the-shelf "design tokens → PPTX theme" tool exists anywhere,
  in any language.** Checked and ruled out: Style Dictionary (Apache-2.0)
  and the W3C DTCG token format are both pluggable-transform token
  formats that *could* target OOXML but nobody has built that transform;
  no curated open-license `theme1.xml`/`.thmx` collection exists either
  (LibreOffice Impress community templates, e.g. `dohliam/libreoffice-
  impress-templates`, are the closest real artifact -- genuine non-
  Microsoft theme/font/color XML under CC-BY-SA/GPL/LGPL, usable as
  reference or even direct assets, but not a "system"); python-pptx's own
  `clrScheme`/`fontScheme` support is thin, undocumented internals only
  (`CT_OfficeStyleSheet` in `oxml/theme.py`), confirmed still an open
  feature request as of GitHub issue #917.
- **The one genuinely reusable precedent is a taxonomy, not code**:
  pptxgenjs's (MIT) own scheme-color model -- `tx1`/`tx2`/`bg1`/`bg2`/
  `accent1`-`accent6` -- *is* OOXML's native `<a:clrScheme>` shape,
  confirming a small token set is structurally correct for this format.
  Marp/Slidev/reveal.js's CSS-variable themes (all MIT) independently
  converge on the same shape from the web side: bg/surface/text/accent
  colors + a 1-2 font type scale (Slidev's `neversink` theme literally
  names tokens `--nc-bg`/`--nc-surface`/`--nc-text`/`--nc-accent`) --
  not directly portable code (CSS, not XML) but strong validation that
  "bg + surface + text + accent + heading font + body font" is the right
  token set, not an arbitrary guess.
- **`chbrown/openxml`** (MIT) has a minimal working `theme1.xml` usable
  as a structural skeleton for hand-writing `<a:clrScheme>`/
  `<a:fontScheme>` XML directly via python-pptx's lxml access.
- **Conclusion: still a from-scratch build**, but a low-risk one -- every
  angle researched confirms the same small token shape and the same
  OOXML target (`dk1`/`lt1`/`dk2`/`lt2`/`accent1-6`/`hlink` +
  `majorFont`/`minorFont`), so option (b) above (a `background:`/`theme:`
  directive in `write_pptx`'s content grammar, implemented as a thin
  token→`clrScheme`/`fontScheme` XML writer modeled on `chbrown/openxml`'s
  skeleton) is the recommended direction over (a) or (c) -- it doesn't
  require matching a template's fixed slide count and generalizes beyond
  the 3 bundled templates. Doing this also finally motivates the
  `theme_color`-migration question in §3/§4: coscribe's own decorative
  shapes (icon-list/stat-callout/scrim) would need migrating from
  hardcoded `RGBColor` to `theme_color` references for a new theme
  directive to actually reach them, otherwise this repeats the same
  "theme editing is inert for decorative shapes" trap independently
  confirmed in the competing tool's own output (§6). Not yet built --
  proposing this as the next concrete step, pending user sign-off.

**Shipped (commit `09d012d` research + the follow-up implementation
commit)**: option (b) above, built as designed. `write_pptx` gained a
`theme` parameter -- comma-separated `bg`/`text`/`surface`/`accent`/
`heading_font`/`body_font` tokens -- implemented as a direct lxml
rewrite of the deck's own `<a:clrScheme>`/`<a:fontScheme>` (python-pptx
has no public API for this; `_apply_theme`/`_set_scheme_color` in
`presentations.py` edit the theme part's XML blob directly, verified to
round-trip correctly through `prs.save()`/reopen) plus a solid slide-
master background fill for `bg`. The theme_color-migration this
motivated was done in the same change: `_add_icon_list_slide`/
`_add_stat_callout_slide`'s accent shapes and `_add_table_slide`'s
header/body text now use `MSO_THEME_COLOR.ACCENT_1`/`DARK_1` references
instead of hardcoded `RGBColor` *when a slide doesn't specify its own
explicit accent hex* (an explicit `layout: icon-list ACCENTHEX` still
wins, unchanged) -- so a `theme` token actually reaches these shapes,
not just the background/title text, closing the exact gap flagged
above. The CJK/East-Asian font bug (§3) was fixed in the same pass,
since it's the same `<a:fontScheme>` XML section: `_apply_cjk_font_fix`
sets a real `<a:ea>` typeface (Microsoft YaHei) unconditionally on
`write_pptx`'s default deck and `fill_pptx_template`'s bundled
templates -- but deliberately *not* on a user-supplied `template_path`
file, to avoid silently rewriting fonts in someone's own uploaded/
branded template. 15 new tests in `test_presentations_tool.py` cover
token parsing/validation, the XML round-trip, the theme_color-vs-
explicit-override behavior, and the CJK fix's scoping. Not yet
independently confirmed in real PowerPoint (this sandbox's LibreOffice
still can't render anything, per §5) -- next step is the same as every
other visual change this session: generate a real deck, have the user
open it and judge the actual quality delta, same as the table/stat-
callout fixes above.

Honest scope of what this does and doesn't close, stated up front so
the user's post-hoc judgment has a stated baseline to check it
against: it gives a freeform deck one consistent background/accent/
font identity end to end, but does **not** add per-slide-type
background variation (a real high-end deck's title/section/content
slides often use different treatments, not one flat color for the
whole deck), does not touch spacing/component-level design (the table/
card layouts already fixed above), and does not address content-
density/layout-composition issues (e.g. a slide with too much text).
Framed to the user as closing roughly the "flat white background,
disconnected per-element colors" slice of the visual gap versus the
competing tool -- a necessary piece, not the whole gap.

**User-confirmed verdict (real PowerPoint, screenshots): the theme-token
system does not produce good-looking decks, and this closes the
question rather than opening a tuning cycle.** A dark-navy/cyan-accent
demo (`bg`/`text`/`surface`/`accent`/`heading_font` all set) looked
worse than the plain-white default, not better -- "非常难看" ("very
ugly"), the user's own words, with a screenshot showing white cards
floating awkwardly on a dark background and large unstyled empty space
around every element. Two causes, kept distinct rather than blurred
together:
- **A real, confirmed implementation bug**: `_add_stat_callout_slide`'s
  card fill/border/label colors were never migrated off hardcoded
  `RGBColor` -- the `surface` token was accepted, parsed, and written
  into the theme's `lt2` scheme color, but nothing in the codebase ever
  reads `lt2`/`MSO_THEME_COLOR.BACKGROUND_2`, so it silently did
  nothing. Documented as doing something it didn't -- a real defect,
  not a design limitation.
- **A structural limitation, not a bug**: even accounting for the bug
  above, a handful of color tokens applied programmatically cannot
  reproduce what a human designer's template actually provides --
  deliberate whitespace/proportion decisions, per-slide-type background
  variation, how a card should relate to the background behind it. This
  was the open, honest caveat already stated when the feature shipped
  (see "Honest scope" above); the user's real screenshot confirms it
  concretely rather than leaving it as a hedge.

**Decision (user, explicit): do not keep tuning the theme-token system
-- pivot effort to Tier 1 (§8): capabilities for editing an existing,
already-well-designed template/deck** (arbitrary shape property
editing, table cell editing, image/icon replacement), rather than
generating design from scratch. This is the same conclusion the user
proposed at the very start of this whole effort ("如果核心是用户自己上传
模板，然后AI修改，问题是不是就解决了" -- "if the core approach is the
user uploading their own template and AI editing it, doesn't that solve
the problem?"), now empirically confirmed rather than just a starting
hypothesis: `fill_pptx_template` (fill content into a real, designed
template, keep every visual as-authored) plus growing edit-existing-file
capabilities is the validated direction; procedural whole-deck theme
generation (`write_pptx`'s `theme` parameter) is not deleted (still
correct, still tested, useful if someone explicitly wants a quick dark/
light recolor with no design ambition) but is explicitly not where
further investment goes. The `surface`-token bug above is left
unfixed, deliberately -- not worth spending effort polishing a
de-prioritized feature.

- **`_add_table_slide` (`presentations.py:605`) applies zero styling
  after `slide.shapes.add_table(...)`** -- no style override, no border
  cleanup, no alignment, no conditional formatting. This means every
  coscribe-generated table renders with PowerPoint's raw default table
  style (blue header fill, banded blue/white rows, black gridlines
  around every cell -- the dated look confirmed directly against a
  rendered screenshot). The competing tool's *fake* table (text boxes +
  thin lines, no real `<a:tbl>` at all -- see §6) still visually beat
  this: no fill, only thin horizontal rules, right-aligned numbers, and
  values over 100% conditionally bolded red to flag them -- a real,
  deliberate design decision, not just "a table exists."
- **`_add_stat_callout_slide` (`presentations.py:790`) fills each card
  solid with the raw accent color** (`card.fill.solid()`), centers a
  32pt white bold number and a 13pt white label with not much weight
  contrast between them, no border-only/light-card option, no trend/
  delta visual treatment. Compared directly against the competing tool's
  KPI cards (light background, thin border only -- no fill, gray small
  label on top, large black number, small red delta line below,
  everything left-anchored not centered): the competing tool's cards
  have real typographic hierarchy; coscribe's are a flat block of
  same-weight centered text on a heavy fill.
- Both are **fixable, scoped, code-level style problems**, not new
  capabilities to design from scratch -- this is "our own current
  execution is unpolished," not "we're missing a feature." Concrete
  starting points: strip/replace the table's default style entirely
  (custom minimal borders, header treatment, right-aligned numeric
  columns, and ideally the same kind of conditional-value highlighting
  the competing tool does for free), and rework the stat-callout card
  from solid-fill-centered to a lighter bordered treatment with real
  size/weight/color hierarchy between the label, the number, and an
  optional delta line.
- **Process note, worth remembering**: this was caught by a real user
  looking at rendered screenshots side by side and saying so bluntly --
  my own earlier slide-by-slide comparison (§ near the end of this
  session's chat, "6 of 9 slides already work") checked *structural*
  completeness (does a table object exist, does the content fit the
  layout grammar) and completely missed *visual quality*, treating
  "technically more correct" (a real `<a:tbl>`) as if it settled "looks
  better" too, which does not follow at all -- a real native table with
  an ugly default style is still ugly. This sandbox's LibreOffice can't
  render anything (§5), so this class of defect is systematically
  invisible to any check I can run myself here; it can only be caught by
  someone looking at a real rendered file, which makes this exactly the
  kind of gap that stays hidden without deliberate human visual review,
  not something to assume is fine just because the automated/structural
  checks (§0 aside, §4's overflow/table-existence checks) come back
  clean.

## 1. Why this effort started

User feedback after testing PPT generation: it "didn't apply any nice
template" and didn't look designed. Two separate fixes shipped for that
(see commits `e554e98`, `9fcd737`) -- more bundled templates, and the
coordinator instructions reaching for `fill_pptx_template` proactively.
But the deeper question the user then raised: **the real fix isn't more
built-in templates, it's letting the user bring their own real file and
have the AI edit it in place** -- sidesteps the whole "we can't legally
redistribute Microsoft's/anyone else's templates" problem entirely, since
the user already owns whatever license covers their own file.

That's the `edit_pptx_text` line of work (shipped, see Β2 below), and
everything in this doc extends from testing how far it can go.

## 2. What's shipped today

- **`write_pptx(path, content, overwrite, template_path)`** -- whole-file
  generation from markdown. `layout: <id>` directives (`icon-list`,
  `stat-callout`, `two-column`, `svg`) give prefab, non-overlapping-by-
  construction slide shapes beyond plain title+bullets/table.
- **`fill_pptx_template(path, template_id, content, overwrite)`** --
  fills content into one of coscribe's own 3 bundled, hand-designed
  templates (`modern-block`/`minimal-light`/`bold-statement`, all 4
  slides, see `src/coscribe/builtin_templates/pptx/`, generator script at
  `scripts/build_pptx_templates.py`). Keeps the template's own decorative
  shapes/backgrounds/images untouched. Hard requirement: chunk count must
  exactly equal the template's slide count. No `layout:` directives, no
  tables.
- **`edit_pptx_text(path, slide, title, content, placeholder_index)`** --
  **the answer to §1**: edits one existing slide's title and/or one
  content placeholder's text in place, in *any* `.pptx` already in the
  workspace (uploaded or placed there), not just coscribe's own bundled
  templates. Everything else in the file is left exactly as authored.
  `placeholder_index` (0-based) reaches a second content placeholder on
  a "two column"-style layout. Live-verified end to end through the real
  chat UI against a real downloaded Microsoft template (see §5) -- edited
  title + two independent content placeholders, confirmed on disk that
  only the targeted placeholders changed and all 13 slides/every image/
  every decorative shape were otherwise untouched.
- **`add_pptx_chart`** -- adds a **real native OOXML chart** (`CategoryChartData`
  + `XL_CHART_TYPE`), not a picture or hand-drawn shape. This is a
  genuine, verified differentiator vs. at least one competing tool (see
  §6) -- real chart, editable in PowerPoint's own chart editor, not a
  static rectangle-and-textbox approximation.
- **`add_pptx_image`** -- adds a *new* picture at a given position/size.
  Cannot replace an existing one yet (see §4.2).
- **`set_pptx_background_image`**, **`add_pptx_scrim`**, **`set_pptx_notes`**,
  **`set_pptx_transition`** -- all real, all XML-level where python-pptx
  has no native API (transition XML mirrors the animation pattern below).
- **`add_pptx_animation(path, slide, shape_index, animation, duration)`**
  -- click-triggered entrance animation (`fade`/`fly-in` only). Genuinely
  hand-built raw `<p:timing>` XML (`_add_click_animation`,
  `_build_animation_effect_row` -- python-pptx has zero animation API at
  all), verified via a read-back check before committing to the real
  file. **This is the existing precedent for "modify XML to add
  animation" the user asked about** -- the mechanism to extend is already
  proven, not hypothetical.
- **`read_pptx`**, **`render_pptx_preview`** -- reading/QA.
- Every WRITE_LOCAL method is `@locked_by_path` (see
  `tools/_file_locks.py`) -- fixes a real, live-hit concurrency bug where
  LangGraph dispatches one AIMessage's several tool_calls on separate OS
  threads, and two concurrent writes to the same file corrupted it.

Full current tool list: `build_presentation_tools` in this file, or
`GET /api/tools`.

## 3. Known bug, verified, not yet fixed

**No East Asian (CJK) font is set in any theme.** Checked all 4 (the
`write_pptx` default blank theme + all 3 bundled templates) via
`slide_masters[0]`'s linked theme part's `<a:fontScheme>`: every single
`majorFont/ea` and `minorFont/ea` typeface is empty. Every Chinese
sentence coscribe has ever generated has been rendered by whatever
generic CJK fallback the opening machine's PowerPoint picks (commonly
宋体 on Windows) -- entirely outside coscribe's control, never
deliberately chosen. Separately, `_add_inline_runs` never calls
`run.font.name` at all (grep confirmed, only `.bold`/`.italic` are set),
so this isn't the "explicit Latin-only override breaks CJK" variant of
the bug (see §6's font-embedding answer for that variant) -- it's "we
never set an EA font at all, so nothing overrides PowerPoint's own
per-machine default."

**Fix**: explicitly set a real `<a:ea>` typeface (e.g. "Microsoft YaHei"
-- broadly pre-installed on Windows, no font-embedding needed) in
`majorFont`/`minorFont` for all 4 themes. Not yet done as of this
writing -- flagged, not fixed, pending the user's go-ahead alongside the
rest of this roadmap.

## 4. Technically verified (real hands-on testing, not guessed) but not yet built

Each of these was tested against real files in this session -- not
theorized. Test scripts aren't kept (throwaway), but the mechanism and
the exact APIs involved are recorded here so re-deriving them is fast.

### 4.1 Table cell editing (existing tables in an uploaded file)

Easier than expected. `shape.has_table` + `shape.table.cell(r, c).text_frame`
is a fully public, documented python-pptx API. Verified: `text_frame.clear()`
then write (same pattern `_fill_content_placeholder` already uses) cleanly
replaces one cell's content on a real table from a downloaded Microsoft
template, with no corruption and no bleed into other cells. **Tier 1
priority** -- cheapest of everything on this list, reuses existing code.

### 4.2 Image replacement (swap an existing picture, keep position/crop/placeholder linkage)

python-pptx's `Picture.image` is read-only -- no `.image = ...` setter,
confirmed via `dir()`. The real mechanism (verified end to end): a
picture is `<p:blipFill><a:blip r:embed="rIdN">` -- add the new image as
a package part (`slide.part.get_or_add_image_part(path)`), then just set
the *same* `<a:blip>` element's `r:embed` attribute to the new part's
relationship id. Position, size, crop (`<a:srcRect>`), and placeholder
linkage are untouched because the shape element itself never changes,
only which image the reference points at. Verified: swapped a real
picture-placeholder's image in a downloaded template, re-read confirmed
new pixel dimensions + exact original position/size preserved.
**Tier 1 priority.**

### 4.3 Icon replacement/recolor

Corrected understanding after the user showed the actual PowerPoint
"Stock Images > Icons" picker: modern PowerPoint icons from that picker
insert as **SVG**, not as a shape group. Confirmed via web search
("Built-in or native PowerPoint icons are inserted in SVG format") and
then *directly verified structurally*: a `<p:pic>` carries a normal
raster fallback blip **plus** a Microsoft SVG extension
(`http://schemas.microsoft.com/office/drawing/2016/SVG/main`,
`<asvg:svgBlip r:embed="...">` inside the blip's `<a:extLst>`). Built a
minimal shape with this exact structure by hand (python-pptx's own
`get_or_add_image_part` can't add an SVG part -- it pipes through PIL to
detect format, and PIL can't open SVG; worked around by constructing a
raw `pptx.opc.package.Part` + `slide.part.relate_to()` manually), saved
it, reopened it with python-pptx with **no read errors**, then swapped
**both** the raster-fallback rId and the SVG rId to a different icon and
confirmed (by reading the swapped-in relationship's target part's blob)
that the new SVG content round-tripped correctly.

- **Icon replacement** = image replacement (§4.2), just two
  relationship ids to swap (raster fallback + SVG) instead of one.
  Verified working.
- **Icon recolor** (not separately tested, but standard/public DrawingML,
  not a proprietary extension, so lower risk): PowerPoint's own
  "Picture Format > Recolor" is a `<a:duotone>`/`<a:biLevel>` color-effect
  element added to the `<a:blip>` -- doesn't touch the SVG content itself.
- **Where do we get an icon library from?** Unresolved. coscribe has no
  icon library today -- `write_pptx`'s `icon-list` layout uses a 1-4-char
  text glyph inside a colored circle, not real vector icons. If we want
  "replace this icon with a semantically different one," we need an
  actual icon set to pick from (Lucide, per the competing tool's
  design-system binding -- see §6 -- is a reasonable, permissively-
  licensed choice). Not solved, needs a decision.

### 4.4 Master/layout/theme access

`prs.slide_masters[0]` exposes `.placeholders`/`.shapes` exactly like a
regular slide -- editing a master's own placeholder text is the same
code path as `edit_pptx_text`, zero new mechanism needed. Theme colors
are harder: the theme is loaded as a generic, opaque `pptx.opc.package.Part`
(no parsed-XML subclass in this python-pptx version) -- `.blob` is a
public read-only property backed by a private `._blob` attribute.
Verified: parsed the real `<a:clrScheme>` out of a downloaded template's
theme part via `lxml.etree.fromstring(theme_part.blob)`, confirmed the
6 accent colors + dk1/lt1/dk2/lt2/hlink/folHlink are all plain
`<a:srgbClr val="RRGGBB">` elements -- editing and writing back via
`theme_part._blob = new_bytes` is mechanically simple but touches a
private attribute, not a documented API. **Tier 3** -- feasible, real,
but the least "supported" of everything here; sequence carefully.

### 4.5 Arbitrary shape property editing (not just title/body text)

**The single easiest item on this entire list.** `shape.left/top/width/
height/rotation` and `shape.fill.solid()` + `shape.fill.fore_color.rgb`
are 100% public, documented, stable python-pptx API -- no XML tricks at
all. Verified: built a shape, set all five geometry/color properties,
saved, reopened, all five round-tripped exactly. This generalizes what
"icon editing" actually needs (§4.3's "icon" isn't a real PPTX primitive
-- it's an image/SVG *or* a set of shapes; editing its color/position/
size when it's the latter is this, not §4.2). **Tier 1 priority** --
cheaper than table or image work, should probably lead the batch.

## 5. Live end-to-end verification method (reusable)

For `edit_pptx_text`, verification wasn't just unit tests -- it went
through the *real* chat UI: downloaded a genuine Microsoft-authored
template live from `create.microsoft.com`'s own catalog (curl works
fine against `createcatalog.public.onecdn.static.microsoft`; `WebFetch`
itself is domain-blocked for `create.microsoft.com`/`templates.office.com`
specifically, curl is not), placed it in a throwaway workspace, started
a real `coscribe-web` instance with a real DeepSeek key
(`DEEPSEEK_API_KEY` env var, routed via a throwaway `providers.json`),
drove it with Playwright against the pre-installed Chromium
(`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`, launch with
`args=["--no-sandbox"]` and an explicit `proxy` pointing at
`$HTTPS_PROXY` -- bare launch gets `ERR_CONNECTION_RESET`), clicked the
real approval UI, then independently re-opened the resulting file with
python-pptx to confirm on disk (not just trusting the model's own
summary) that only the intended slides/placeholders changed. This
sandbox's LibreOffice cannot render anything at all (pre-existing,
sandbox-specific, confirmed via a bare `soffice --headless --convert-to
pdf` failing on a trivial file) -- no rendered screenshots are possible
here; verification has to be structural (python-pptx re-read) plus
sending the actual `.pptx` file to the user to open in real PowerPoint.
Reuse this method for verifying any future PPTX tool live, not just unit
tests.

## 6. Competitive research (an HTML-to-PPTX AI tool, researched by proxy through the user)

A different architecture: builds decks in HTML/CSS first, exports to
PPTX (or flattens to screenshot-per-page for a non-editable, higher-
fidelity mode). Bound to one fixed design system ("Modernist": red/white,
Archivo, strong grid, zero corner radius) for this deployment.

**Two independent exports were inspected directly with python-pptx (not
just taken on the tool's own word) -- both confirm the same architecture
consistently, including places where its own chat answers didn't match
the actual file:**

- **No native table object anywhere in either export**, despite the tool
  telling the user "表格导出为 PPT 时会转换成原生表格对象" (exports as a
  native table object) -- verified `has_table` is `False` across every
  shape in both files. The "table" is entirely independent thin-line
  rectangles + separate text boxes positioned to look like a table.
- **No shape grouping anywhere** (`MSO_SHAPE_TYPE.GROUP` never appears) --
  a "KPI card" (border + 2 lines of text) is 3 separately-positioned
  shapes, not one movable unit.
- **No native PowerPoint placeholders used at all** -- every text
  element is a free `AUTO_SHAPE` at absolute coordinates, `is_placeholder`
  is `False` everywhere, only 1 layout + 1 master exist in the whole
  file. No outline-view support, no master-driven typography cascade.
- A promised image placeholder ("image-slot 组件") on one slide was
  simply **absent** from the actual exported file.
- Custom slide size, 20in x 11.25in (still 16:9, just 1.5x the usual
  13.333x7.5) -- not wrong, just non-default, worth knowing if we ever
  need to detect/normalize slide size from an arbitrary uploaded file.

**Where coscribe already has a real, verified edge because of this**:
`add_pptx_chart` (§2) produces a genuine native chart object, not a
shape pile -- this tool's own bar "chart" is exactly the shape-pile
approach, confirmed both in its own chat description ("以图形形式") and
in the file. If/when coscribe builds real table editing (§4.1), the same
gap applies: a real `<a:tbl>` object is a durable, concrete differentiator,
not a nice-to-have.

**Design ideas worth adopting, confirmed by an architecture-hypothetical
Q&A (the tool wouldn't reveal real backend internals, but answered "if
you designed this from scratch" framing candidly)**:

- **Layered layout system, not a general constraint solver.** Its own
  words: real production layout engines tend to avoid Cassowary-style
  general linear constraint solving as "too heavy for this use case" --
  instead: a grid layer (fixed columns, `column-start`/`column-span` for
  coarse placement), a box layer (`x,y,w,h,z` + simple inequalities like
  `A.right <= B.left - gap`), and a text layer (auto-fit within a fixed
  container, not fixed pixel sizing). coscribe's `write_pptx` `layout:`
  directives (`icon-list`/`stat-callout`/`two-column`/`svg`) are already
  this same philosophy -- a small set of prefab, non-overlapping-by-
  construction shapes instead of freeform coordinates. Reinforces: keep
  extending via more named layout/relation types, not by exposing raw
  coordinates to the model.
- **Semantic anchors, not raw coordinates, in the AI-facing interface.**
  "Put the image top-right, wrap text around it" gets translated
  internally to `{"anchor": "top-right", "relation": "wrap-around"}` --
  a small, controlled vocabulary of enum-like values, specifically
  *not* letting the model emit raw x/y numbers (their stated reasoning:
  raw coordinates from an LLM are unreliable and cause overlap/overflow;
  a small enum + deterministic layout math downstream is more stable).
  Directly relevant to designing `edit_pptx_shape`-style tools later:
  prefer an enum/relation-based interface over asking the model for EMU
  coordinates directly.
- **A shape-lister tool for AI targeting.** When asked how their AI
  picks one specific shape to edit on a complex, already-existing slide
  it can't see rendered: "我会先看清楚当前设计,再做针对性的定位和修改"
  (look at the current design first, then target precisely) plus "你也
  可以直接点选或描述元素" (user can also click-select or describe the
  element) -- confirms the general principle (structured shape
  inventory before targeted edits) but the human-click-to-select path
  obviously isn't available to coscribe's own text-only tool interface.
  **Actionable**: any future `edit_pptx_shape(path, slide, shape_index, ...)`
  needs a matching `list_pptx_shapes(path, slide)` the model can call
  first to get a structured inventory (index, type, position, current
  text/color) -- don't make the model guess indices blind.

**Real limitations it shares with coscribe (useful to know these aren't
solved problems anywhere, not just gaps unique to us)**:

- **No font embedding.** Confirmed directly: fonts are referenced by
  name only, never packaged into the file; opening on a machine without
  that font silently substitutes. coscribe doesn't embed fonts either
  (not attempted). Lower priority than the CJK theme-font bug in §3
  (which is about *never choosing* a CJK font at all, not about the
  chosen font being unavailable on the reader's machine) but same family
  of problem.
- **Confirmed the exact CJK/Latin font mismatch problem independently**:
  its own single-font-name design (Archivo, Latin-only glyph coverage)
  means Chinese content in its own decks currently falls back to
  whatever the reader's PowerPoint picks (commonly 宋体) -- volunteered
  unprompted as a known, current, unfixed limitation of its own product,
  not something it defended as fine. Cross-validates that §3's bug is a
  real, industry-wide sharp edge, not a coscribe-specific miss.
- **No automatic long-content pagination.** Content that doesn't fit one
  slide isn't auto-split or auto-shrunk -- it's controlled purely by
  design-time human/model judgment of expected content volume, with no
  automated pre-check. coscribe is arguably *ahead* here already: the
  existing LibreOffice-based `overflow_warnings` (when soffice is
  available) is a real, automated, post-generation check this tool
  doesn't have at all -- worth remembering as a genuine strength, not
  assuming everything about the other tool's approach is more mature.
- **No animation at all**, stated as a deliberate scope decision (PPTX
  export is "a static one-time snapshot"), not investigated further by
  proxy. coscribe already has a working, if narrow, animation mechanism
  (§2) -- a place we're already ahead and should keep investing, per the
  user's explicit priority.
- **Cross-slide consistency across incremental edits is manual, not
  guaranteed.** Its own answer: consistency in one generation pass comes
  from every slide sharing one set of design-system tokens computed
  once; but *editing* a few slides later "确实存在风险" (does carry real
  risk) of drifting from the rest of the deck, mitigated only by the
  model re-checking visually afterward, not by any structural guarantee.
  **This applies to `edit_pptx_text` too** -- editing slide 4 of an
  uploaded deck has no automated check that the new text's tone/length
  still fits the deck's established pattern. Not fixed, not clearly
  fixable without much more machinery; flagged as a known, shared,
  unresolved class of risk rather than something to silently assume away.
- **Chart "data" edits are precise because the chart is fake.** Its bars
  are independent shape+text pairs, so editing one number only touches
  one text run. Ironically the *opposite* structure from a real chart
  object (`add_pptx_chart`'s `CategoryChartData`) -- editing one data
  point in a **real native chart** means round-tripping through
  `chart.plots[0].categories`/`series` and re-writing the whole
  `CategoryChartData`, not touching one isolated text run. Worth
  remembering when/if "edit an existing native chart's data" becomes a
  tool: it's a different, chart-API-shaped problem, not a text-edit
  problem, despite superficially sounding like one.

**Follow-up answers, this round**:

- **Color-contrast/accessibility**: no automated check exists on their
  side either -- relies on baseline design judgment (light background +
  dark text, avoid gray-on-gray) plus the user visually confirming a
  custom (non-default) color scheme after generation. Not a solved
  problem there; coscribe has no such check either. Real, shared gap --
  not prioritized in §8, but worth remembering if custom (non-template)
  color schemes become a bigger part of coscribe's own scope later.
- **PowerPoint-version/WPS/Keynote compatibility**: standard OOXML,
  claimed-compatible back to PowerPoint 2007+, WPS, and Keynote's "open
  PPTX" import -- but **no real per-version/per-app testing was actually
  done**, only a theoretical claim ("理论上兼容", "没有对每个版本/软件
  组合做过实机测试"). Treat as an unverified claim, not a tested fact --
  applies equally to anything coscribe ships; we haven't tested against
  older Office/WPS/Keynote either. Not a gap unique to the competing
  tool, just an honest admission worth remembering for both.
- **Icon licensing -- resolved, actionable**: the icon set is **Lucide**
  (open-source, ISC license -- a permissive license, materially
  equivalent to MIT: free commercial use, modification, and
  redistribution, no attribution requirement). This directly closes
  §4.3's open "where do we get an icon library from" question --
  Lucide's SVGs can be bundled directly into this repo the same way the
  3 bundled pptx templates are (a version-controlled asset directory),
  with no licensing complexity at all, unlike the Microsoft-template
  redistribution problem that ruled out bundling third-party PPTX
  templates in the first place. (Separately volunteered: if the tool
  ever pulls in Unsplash-style stock photography, that carries its own
  per-photo attribution requirements -- not relevant to coscribe today
  since we don't source stock photography, but worth remembering if that
  ever changes.)

**§4.3 update**: icon library question is now answered -- **use Lucide**.
Icon replacement (§4.3, §8 Tier 1 item #3) can proceed with a concrete
plan: bundle a Lucide SVG subset (or the full set -- it's a few thousand
small, simple stroke-based SVGs, size is unlikely to be a real concern)
under version control, expose semantic search/lookup over it (name-based
at minimum; embedding-based semantic search is a nice-to-have, not a
blocker), and use the §4.3-verified SVG-relationship-swap mechanism to
insert/replace.

**Architecture verdict (asked directly, worth recording)**: HTML-to-PPTX
is a *reasonable, deliberate* tradeoff for a from-scratch generation
product optimizing for visual polish and fast, brand-consistent output
where the user mostly just tweaks text afterward -- browser layout
(flexbox/grid) is a genuinely more capable engine for that than hand-
rolled EMU-coordinate math, and the same rendering pipeline cheaply
yields a high-fidelity screenshot-mode fallback for free. But it is
structurally the wrong architecture for coscribe's actual differentiated
goal: surgically editing an arbitrary, already-existing, professionally-
authored file so it still behaves like a genuinely native PowerPoint
file afterward (outline view, real table/chart objects, real grouping).
HTML has no native concept of PowerPoint's placeholder/master
inheritance or of OOXML's table/group primitives, so an HTML-first
pipeline's "free text boxes everywhere, tables/groups faked with
absolute-positioned shapes" isn't a quality bug to fix later -- it's the
structurally necessary output of translating from a source model that
doesn't have those concepts, confirmed by real inspection of two
independent exports (§6). Editing a pre-existing file would additionally
require reverse-engineering arbitrary PPTX back into their HTML model
first, which nothing about their own output suggests exists. coscribe's
slower, more effort-per-capability native-python-pptx/raw-XML approach
is the right tradeoff for coscribe's actual mission -- each capability
that lands is a real OOXML primitive, not a visual approximation.

**Final round -- vision-cost design principle, and where the proxy research
line stopped being useful**:

Asked directly why the coordinator shouldn't just see a rendered image of
what it just built more often (my own suggestion, prompted by "instant
visual feedback" being the competing tool's #1 stated reason for its
whole architecture). The user's pushback was correct and reframes the
right fix: **vision is expensive (real token cost), so the right default
is a cheap structured/textual representation of state -- the same reason
browser-automation agents read the DOM tree instead of screenshotting
repeatedly.** coscribe already does this partially (`overflow_warnings`/
`text_overlap_warnings` in `render_pptx_preview` and `write_pptx`'s own
QA step are geometry-based checks over the real shape tree, not vision)
-- the real gap isn't "the model can't see enough," it's that there's no
general-purpose **structured shape inventory** (type/position/size/
current text/color per shape, i.e. a PPTX "DOM snapshot") the model can
read cheaply for *any* slide before deciding what to edit. §8's planned
`list_pptx_shapes(path, slide)` tool is exactly this -- confirmed as the
right priority, not "make `review_work`'s vision path cheaper or more
automatic." Vision (`review_work`'s base64 image attachment, the only
two real multimodal-content sites in the whole codebase --
`web/session.py:1902` for user-uploaded images, `runtime_lg/subagents.py:325`
for `review_work`'s preview) should stay the deliberately rare, opt-in
fallback for genuinely aesthetic judgment calls a shape-tree readout
can't capture (does this composition look balanced, is this color
combination pleasant) -- not something to make cheaper/default.

Two follow-up questions to the competing tool were declined as internal
implementation detail ("涉及我内部具体运作方式,不能展开"): whether AI-driven
edits there read a structured DOM-like snapshot vs. relying on vision,
and how per-turn context cost is controlled across a multi-turn edit
session on a long deck. **This is the point the proxy-research line
stopped being productive** -- further "how does your system work
internally" questions won't get real answers; anything past this needs
to be solved by coscribe's own design work, not more Q&A. Two answers
that *were* given, both reinforcing already-known conclusions rather
than surfacing new ones: (1) partial/local edits are confirmed cheaper
and faster than full-deck regeneration there too -- independently
reinforces `edit_pptx_text`'s whole design philosophy a third time now
(§2, §6's cross-slide-consistency note, and this); (2) confirmed *again*,
independently, that no automated export-time design linter exists
(no contrast check, no bounds check) -- same conclusion as the earlier
color-contrast answer, now generalized to overflow/bounds checking too,
reinforcing that coscribe's LibreOffice-based `overflow_warnings` (real,
automated, geometry-driven, even though unusable in *this* sandbox
specifically) is a genuine, already-shipped advantage over at least this
one competing tool, not something to undervalue while chasing new
features.

**Behavioral-boundary round (usage-level questions, not internals --
these got real, specific answers)**:

- **Icon retrieval**: describe intent in plain language ("购物车"), no
  need to know Lucide's own naming convention -- confirms semantic
  matching over a name, not exact-name lookup, should be how coscribe's
  own icon tool works too. Confirmed vector (SVG, scales cleanly), one
  consistent stroke-width/corner-radius style within Lucide (no mixed
  styles unless deliberately switching icon sets), and **user-supplied
  SVG upload is accepted** -- worth mirroring: an icon-replacement tool
  shouldn't be locked to Lucide-only, should also accept an arbitrary
  user-provided SVG file path the same way `add_pptx_image` accepts an
  arbitrary image path today.
- **Shape-targeting heuristic, confirmed concretely**: content-based
  description ("the box that says '销售数据'") is most reliable, role-
  based ("this slide's subtitle") next, position-based ("top-left") is
  the least reliable/most ambiguous on its own -- but combining cues
  ("the caption line under the title") works well. Critically: **when
  genuinely ambiguous (duplicate text, indistinguishable similar shapes,
  a remembered-wrong color pointing at the wrong shape but a still-
  unique "the circle on slide 5" resolving correctly anyway), it asks
  for confirmation rather than guessing.** Directly actionable for
  designing `list_pptx_shapes` and any future `edit_pptx_shape`: the
  tool should surface enough of each shape's identity (its actual text
  content foremost, not just index/type/position) that the model can
  apply this same priority order, and the coordinator's own instructions
  for these tools should explicitly say "confirm with the user rather
  than guess" when two candidates are equally plausible -- don't assume
  a single best-guess match is always safe.
- **Rotation imprecision, root cause narrowed**: a plain single-shape
  rotation (even a non-integer angle like 15°) round-trips precisely
  there, and gradient/shadow direction correctly follows rotation --
  contradicts my earlier assumption that CSS-transform-to-DrawingML
  math itself is inherently lossy for simple cases. The imprecision it
  originally flagged is specifically about **composite/grouped rotation**
  (rotating a group vs. rotating each member and recomposing), which it
  candidly couldn't even properly answer because **it doesn't support
  native grouping at all**, so the scenario doesn't really arise for it.
  Relevant to coscribe: §4.5's rotation test already round-tripped a
  15.0° rotation exactly, using python-pptx's native single-float
  `shape.rotation` field directly -- no composite-transform math
  involved at all. **coscribe likely doesn't inherit this problem even
  in the composite case**, once real grouping is built (Tier 2+, not
  yet on the roadmap explicitly -- OOXML groups have their own native
  `<a:xfrm>` group-level transform, a well-defined primitive, not a
  CSS-matrix approximation) -- worth confirming with a real test once
  grouping is actually built, but there's no a priori reason to expect
  the same precision loss.
- **Table gap, now precisely enumerated** (sharpens §4.1's value
  proposition): confirmed for a third time that its tables are text
  boxes + line shapes, and now specifically named what's actually
  missing relative to a real `<a:tbl>`: **cell merging, coupled row-
  height/column-width resizing (dragging one border there only moves
  that one line, not the whole row/column), and PowerPoint's native
  "insert row/column" table commands.** Individual cell text is editable
  and formatting (bold/italic/color) is preserved -- it's specifically
  the *structural* table operations that don't exist. This is the exact,
  concrete list of what a real `<a:tbl>`-based `edit_pptx_table_cell`
  (or similar) would win over any visual-approximation approach, not
  just "more real" in the abstract.
- **Theme/master color linkage -- verified against coscribe's own code,
  real actionable finding**: it confirmed its shape/text colors are
  hardcoded literal values, not references into the theme's color
  scheme, so manually switching PowerPoint's Design-tab theme has
  *no effect* on already-placed content -- theme editing is architecturally
  inert for it. **Checked coscribe's own code the same way**: `tools/presentations.py`'s
  icon-list/stat-callout/scrim helpers all call
  `shape.fill.fore_color.rgb = RGBColor.from_string(accent)` -- also a
  hardcoded literal, not a scheme reference. Confirmed python-pptx
  *does* support the alternative: `shape.fill.fore_color.theme_color =
  MSO_THEME_COLOR.ACCENT_1` (`pptx.enum.dml.MSO_THEME_COLOR`) is a real,
  public API; verified a shape written this way round-trips correctly
  (`theme_color` reads back as `ACCENT_1`, and python-pptx correctly
  refuses to resolve `.rgb` on it, since resolving a scheme reference to
  a literal color is PowerPoint's own job at render time, not
  python-pptx's). **This means §4.4's planned theme-color editing has a
  real choice to make, and it's a genuine potential differentiator**: if
  coscribe's own decorative shapes (icon circles, stat-callout cards,
  scrims -- currently all hardcoded-RGB) were migrated to reference
  `theme_color` instead, then a future "edit this deck's theme colors"
  tool would genuinely cascade through the whole deck, including
  coscribe's own generated decorative elements -- unlike the competing
  tool, where theme editing can never do anything useful at all by
  construction. Plain placeholder-inherited text (titles/bullets on a
  template layout) is *not* affected by this -- `_add_inline_runs` never
  sets `run.font.color` at all, so that text already inherits from the
  theme correctly today. **Not yet decided or built** -- this is a real
  design choice (migrate accent-color shapes to `theme_color` refs) to
  make explicitly before or alongside building §4.4, not something to
  do silently as a drive-by refactor.
- **Animation, no roadmap answer available** (reasonably declined --
  "not mine to predict or promise"), but a concrete practical fallback
  was offered: since every element there is an individually selectable
  shape/text box, a user can manually add PowerPoint's own animations
  after export today, just with more friction than if elements were
  named/grouped for easy selection. Same point applies to coscribe once
  real Tier-2 animation depth + eventual grouping both exist -- reinforces
  doing both together eventually rather than animation alone, since
  grouping is what makes "select the thing I want to animate" fast for
  a human working in PowerPoint afterward, not just useful for AI
  targeting.

## 7. Explicit non-goals (agreed with the user)

- **Video/audio embedding** -- not needed.
- **Real OLE embedded objects** (e.g. an actually-embedded, double-click-
  to-edit Excel workbook) -- not worth building; a real native table
  almost always serves the same purpose better and is far simpler to
  edit. Matches the competing tool's own conclusion.
- **Real SmartArt objects** -- infeasible, not just deprioritized: the
  layout algorithm lives in the PowerPoint client itself, not in the
  file format, so there's no way to "generate" a real one from outside
  PowerPoint. Matches the competing tool's own conclusion.
  **A substitute already exists**, though, and this is worth remembering
  as already-done, not a gap: `write_pptx`'s `icon-list`/`two-column`/
  `stat-callout` prefab layouts *are* the "visual approximation via real,
  editable native shapes" answer, same philosophy the competing tool
  described for its own SmartArt substitute. The one real remaining gap
  is narrower than "build a SmartArt alternative" -- it's "detect that an
  *already-uploaded* file contains real SmartArt, and offer to replace
  it with a shape-based approximation," since today nothing looks for or
  flags SmartArt in an uploaded file at all.

## 8. Agreed priority order

**Tier 0 -- done and confirmed** (§0, commits `3b169c2`/`ff6b818`):
`_add_table_slide`'s unstyled default table and `_add_stat_callout_slide`'s
flat solid-fill cards are both fixed, and the follow-up centering/card-
height/bullet-suppression fixes were confirmed against real PowerPoint.

**Freeform theme-token system -- built, tested against real PowerPoint,
explicitly de-prioritized** (§0's "User-confirmed verdict"): shipped as
`write_pptx`'s `theme` parameter, but a real rendered deck looked worse,
not better, than the plain-white default -- confirms procedural color
tokens can't substitute for actual template design. **Not where further
effort goes.** §3's CJK theme-font bug was fixed in the same change
(unconditionally, independent of `theme`) -- no longer open.

**Tier 1 -- all 3 items done**, per the user's explicit pivot decision
(the user's own original thesis -- "AI edits a real uploaded template"
-- now empirically the validated direction over generating design from
scratch). Next: move to Tier 2, or new capability requests, pending the
user's real-PowerPoint review of everything shipped in this batch.
1. **Done** -- Arbitrary shape property editing (color/position/size/rotation), §4.5. Shipped as `edit_pptx_shape(path, slide, shape_index, left_in, top_in, width_in, height_in, rotation, fill_color)`, paired with the also-shipped `list_pptx_shapes(path, slide)` prerequisite (structured shape inventory: index/type/position/rotation/text preview/fill, `"#RRGGBB"` or `"theme:ACCENT_1"`). 9 new tests in `test_presentations_tool.py`, live-verified. Reuses `_open_slide`; `fill_color` raises a clear error on a shape with no `.fill` (a table/chart `GraphicFrame`) instead of an opaque `AttributeError`.
2. **Done** -- Table cell editing on an existing table, §4.1. Shipped as `edit_pptx_table_cell(path, slide, shape_index, row, col, text)` (clear-then-write, same pattern as `edit_pptx_text`) plus `merge_pptx_table_cells(path, slide, shape_index, start_row, start_col, end_row, end_col)` (either diagonal corner order, raises on a range that already contains a merged cell -- both are `_Cell.merge`/`.text_frame.clear()`, 100% public python-pptx API, no XML). Both reuse `list_pptx_shapes`'s `shape_index`/`table_dimensions` via a shared `_get_table_shape` bounds-and-kind check. **Scope note, found live**: merging does *not* discard the merged-away cells' text the way the original plan assumed -- python-pptx's own `merge()` moves it into the surviving cell as extra paragraphs; the tool's docstring and the coordinator instructions were corrected to say so (verified directly, not assumed). **Row/column insertion and row-height/column-width resizing are explicitly NOT built** -- python-pptx's `Table` has no add-row/add-column API at all (would need raw `<a:tr>` XML manipulation, meaningfully higher risk than everything else in this batch); row `.height`/column `.width` setters are public and would be cheap to add later but weren't needed to close this item's scope. 9 new tests in `test_presentations_tool.py`, live-verified against a real generated table.
3. **Done** -- Image/icon replacement, §4.2 + §4.3. Shipped as two separate tools rather than one, once the real shape of the work became clear: `replace_pptx_image(path, slide, shape_index, image_path)` (the §4.2 relationship-swap mechanism -- `shape.part.get_or_add_image_part` + repointing the existing `<a:blip>`'s `rEmbed`, verified live to preserve position/size/crop exactly) for swapping any *existing* picture, and `add_pptx_icon(path, slide, icon_name, left_in, top_in, size_in, color)` + `list_pptx_icons()` for *inserting* a new real icon -- the user picked "introduce the Lucide icon library" explicitly when asked (a real decision only they could make, see below). **Scope decisions, made live rather than assumed:**
   - **Exact-name lookup, not semantic/fuzzy matching.** The original plan said "Lucide lookup by description, semantic match, not exact name" -- built as exact-name instead: `list_pptx_icons()` returns the full ~50-name list (small enough for a model to just read and pick from directly), and fuzzy/semantic matching would need either an embeddings dependency or a hand-built synonym table, neither justified for a curated ~50-name set. Revisit if the bundle grows much larger.
   - **No SVG-in-PPTX at all -- rasterized PNGs instead, and this was a real, discovered pivot, not the original plan.** §4.3's original mechanism (dual raster-fallback + `asvg:svgBlip` extension blip, mimicking real PowerPoint's own icon picker) was **not used**: it needs a raster fallback anyway, gives no way to verify in this sandbox (no real PowerPoint), and per-call recoloring would need either editing the SVG's own color attribute (extra XML surgery) or a `<a:duotone>` effect (untested). Instead: `scripts/build_pptx_icons.py` is a **build-time-only** script (not shipped, not run by coscribe itself) that fetches each icon's real SVG from Lucide's GitHub repo, replaces `currentColor` with literal black, and rasterizes to a transparent-background PNG via `cairosvg` (a *dev-only* tool, deliberately **not** added to `pyproject.toml`'s dependencies -- verified `mypy`/`ruff` stay clean via a `[[tool.mypy.overrides]]` entry instead). The ~54 resulting PNGs are committed to `src/coscribe/builtin_icons/lucide/` (340KB total). At call time, `add_pptx_icon` recolors the bundled black PNG to any requested color via plain PIL (`Image.new` + `putalpha`, no per-pixel Python loop, no SVG library) -- verified live the recolored pixel value matches the requested hex exactly, alpha-channel anti-aliasing preserved. This means `add_pptx_icon` has **zero new runtime dependencies** (Pillow was already a dependency) and needs no LibreOffice/soffice at all, unlike this file's QA-rendering features.
   - **Curated ~54-icon subset, not Lucide's full ~1500.** Picked for common business-deck needs across 6 categories (arrows/trend, status, business/org, charts, actions, common objects) -- adding another name is a 1-line change to `ICON_NAMES` + rerunning the build script.
   - **Icons only insert as new shapes; `replace_pptx_image` is the only "replace in place" path**, and it's raster-only (matches `add_pptx_image`'s existing scope) -- there is no "replace an existing icon shape while preserving its raster+SVG dual-blip structure" tool, since no coscribe-generated content has ever produced that structure and building it just to edit a hypothetical real-PowerPoint-icon-picker icon wasn't justified without a concrete need.
   16 new tests in `test_presentations_tool.py` (4 for `replace_pptx_image`, 7 for `add_pptx_icon`/`list_pptx_icons`), all live-verified, including a pixel-level recolor-correctness check. Not yet independently confirmed in real PowerPoint (same standing caveat as every other visual change this session, §5).

**Tier 2**:
4. **Done** -- Slide delete/duplicate/reorder (user picked this to go first, ahead of animation depth). Shipped as `delete_pptx_slide(path, slide)`, `duplicate_pptx_slide(path, slide, insert_at=None)`, `reorder_pptx_slide(path, slide, new_position)`. Delete/reorder reuse `_clear_slides`'s existing `<p:sldIdLst>` manipulation technique (drop the relationship, remove/reinsert the `<p:sldId>` list entry -- list order *is* slide order). Duplicate needed real new research: python-pptx has no slide-duplication API at all. `_duplicate_slide_part` deep-copies the source slide's own XML part into a brand-new `SlidePart`, re-adds every one of the source's relationships (image/chart/hyperlink/etc., but not `notesSlide` -- an explicit v1 scope cut) pointing at the *same* target parts (valid OOXML, safe since nothing in this file mutates a shared part in place), then blanket-remaps every `r:`-namespaced attribute in the copied XML to whatever relationship id the destination part actually got. **This remap is not optional** -- verified live with a forced-offset test that the destination's rId sequence is not guaranteed to match the source's (a real risk for any actual uploaded file, whose rIds may not be sequential-from-1), and that the remap correctly re-resolves both a picture's `r:embed` and a hyperlink's `r:id` even when the ids diverge. 12 new tests in `test_presentations_tool.py`, including that regression case and one confirming the no-notes scope cut is deliberate (pinned as a test, not just prose). Not yet independently confirmed in real PowerPoint.
5. **Partially done** -- Animation depth. Shipped: 4 new effect types (`exit-fade`, `exit-fly`, `emphasis-grow`, `emphasis-spin`), extending `_build_animation_effect_row`'s existing pattern -- values sourced by fetching the actual `pptx_animation_presets.json` this file already cited (hugohe3/ppt-master's repo restructured since it was first researched; found the manifest at `skills/ppt-master/scripts/pptx_animation_presets.json`, 203 real presets, `"version": 2`), not guessed. **Found and fixed a real bug in already-shipped code while cross-checking fade/fly-in against this same manifest**: the row's own `<p:cTn>` used `nodeType="clickEffect"`, but every category in real PowerPoint-authored XML uses `nodeType="afterEffect"` at that exact position -- wrong since this feature first shipped, never caught because verification was only structural-readback + LibreOffice (never real PowerPoint). **Found a second real bug class along the way**: `exit-fade` and `fade` share the *identical* `presetID`/`presetSubtype` (10/0) in real PowerPoint XML -- `presetClass` (`"entr"` vs `"exit"`) is the only thing that disambiguates them, and `_verify_animation_readback` didn't check it, meaning an exit animation could have silently passed readback as proof an entrance animation was added (or vice versa) the moment a same-ID exit/emphasis effect existed alongside an entrance one. Both fixed; a live test confirms `_verify_animation_readback` now genuinely distinguishes `exit-fade` from `fade` rather than accepting either. 4 new tests plus 2 regression tests (nodeType, presetClass disambiguation) in `test_presentations_tool.py`.

   **Auto-play (`with-previous`/`after-previous`) and per-paragraph stagger -- now done**, the deferred follow-up above, picked next by the user once they understood what it would actually do ("动画自动播放与逐条显示"). Fetched hugohe3/ppt-master's `pptx_animations.py` (not just its preset JSON, which this file had already used) to find its `_TRIGGER_NODE_TYPES` mapping and `_main_target_offsets` cumulative-delay algorithm -- both directly answered the bookkeeping gap flagged above. **A third real, evidence-backed bug found along the way**: nodeType on the presetID-bearing `<p:cTn>` actually encodes the *trigger mode itself* (`on-click`->`"clickEffect"`, `with-previous`->`"withEffect"`, `after-previous`->`"afterEffect"`) -- confirmed by finding that the reference project's own row-instantiation code *always overwrites* this attribute based on the caller's real trigger, regardless of whatever the raw preset-manifest template stored. This file's earlier "fix" (`"clickEffect"`->hardcoded `"afterEffect"`, Tier 2 #5 above) was therefore itself wrong for the on-click-only trigger this tool exposed at the time -- on-click should be `"clickEffect"`. Fixed; `add_pptx_animation`'s `trigger` parameter now always derives nodeType from this mapping instead of hardcoding one value, and `_verify_animation_readback` now checks nodeType too (alongside the existing presetClass check), closing the same "silently accepts the wrong variant" risk class a second time.

   Implementation: `_add_animation_step` chains `with-previous`/`after-previous` calls onto the LAST existing top-level group on the slide (regardless of which trigger that group itself used) as one more step, computing the new step's start time from that group's own most-recently-added step's own start+duration (`_step_timing`, scanned from the saved XML's own `dur`/`delay` attributes -- no separate bookkeeping state needed across calls, the file itself is the source of truth). If the slide has no existing group yet, a with-previous/after-previous call instead anchors a brand-new group that starts automatically on slide entry (`stCondLst` waiting on mainSeq's own `onBegin`, not a click) -- matching real PowerPoint, where the first animation on a slide still plays even when set to "After Previous." `by_paragraph=True` reuses the same per-shape targeting machinery via a new `_add_target` helper (threaded through `_set_visibility`/`_fly_anim`/`_build_animation_effect_row`) that optionally nests `<p:txEl><p:pRg st=N end=N/></p:txEl>` inside `<p:spTgt>` -- the standard ECMA-376 "animate by paragraph" pattern -- plus a `<p:bldLst><p:bldP .../></p:bldLst>` sibling element in `<p:timing>` real PowerPoint always emits alongside it. **Honest caveat**: unlike the six base effects (cross-checked against captured real-PowerPoint XML), the paragraph-range targeting pattern itself was not cross-checked against a captured real sample -- it's standard/ubiquitous ECMA-376, not guessed, but that's a different confidence level than the rest of this file's animation code, and the docstring says so.

   **Live-verified** (structural, via a standalone script, not pytest): an on-click anchor + with-previous + after-previous chain on one slide correctly produced one group with three steps in the right nodeType/timing order (with-previous step started at the same time as its anchor; after-previous step started exactly at the anchor's own duration plus the requested extra delay); a with/after-previous call on a slide with no existing animation correctly auto-anchored on slide entry instead of erroring; a `by_paragraph=True` call against a 3-bullet shape correctly produced one row per paragraph plus one `bldP` entry, all three paragraph indexes present. Round-tripped through `python-pptx` with warnings-as-errors (clean). LibreOffice-conversion re-verification was attempted but this sandbox's `soffice` cannot convert *any* file right now, confirmed by testing it against a plain deck with zero animations too -- a pre-existing sandbox-wide limitation, not something this change caused; the six base effects' own original LibreOffice verification (done earlier in the session, before this regression) still stands. 8 new tests in `test_presentations_tool.py`, one existing test rewritten (the nodeType regression test's own expected value flipped from `"afterEffect"` to `"clickEffect"`, documented above).
6. **Done** -- `fill_pptx_template` content-page repetition (user picked this next, after confirming the two demo decks sent for real-PowerPoint review "确实要用已经设计好的模板...说明路走对了" -- editing real, designed templates is the validated direction, keep pushing on it). Previously `fill_pptx_template` required `content`'s `---`-separated chunk count to exactly equal a template's fixed slide count, forcing the caller to trim content or fall back to `write_pptx` whenever the material was longer or shorter than the template happened to be. Each bundled template's `.yaml` manifest now declares a `slide_roles: list[str]` field (`"title"`/`"content"`/`"closing"`; `"content"` is the only repeatable role, and must be contiguous -- validated in `pptx_templates.py`'s `_parse_template`, all 3 shipped templates updated to `[title, content, content, closing]`). `_adjust_template_content_slides(prs, slide_roles, target_count)` duplicates (reusing item #4's `_duplicate_slide_part`) or trims the template's trailing content-role slides in place before the per-chunk fill loop runs, so the deck's slide count now scales to however many chunks the caller actually gives -- growing past the template's original count, shrinking below it (down to zero content slides, keeping only title/closing), or matching exactly all work the same way. A template with no repeatable role (`slide_roles` has no `"content"` entries) still requires an exact chunk-count match, and a chunk count too low even to cover the template's fixed slides raises a clear `ValueError` naming the real minimum. **Live-verified**: growing a real bundled template (`bold-statement`, normally 4 slides) to 6 slides and separately shrinking it to 3, both round-tripping correctly through the real YAML loader (not just a synthetic test fixture) -- confirms a duplicated content slide keeps its own decorative (non-placeholder) shape, not just its placeholders, so "the design scales with content length" holds for real, not just for text. 21 tests total in `test_presentations_tool.py`/`test_pptx_templates_tool.py` (5 new fill_pptx_template cases, 1 rewritten exact-match-still-enforced-when-no-content-role case, plus every pre-existing template-manifest test updated for the now-required `slide_roles` field).

**Tier 3**:
6. **Done** -- Icon recolor. **Real `<a:duotone>` OOXML wasn't used, a discovered pivot from this item's own original framing, not the plan** -- once actually building it, the same insight that shaped §4.3's own icon-insertion mechanism applied again: every icon this file can place is a plain raster PNG this file itself controls the pixels of (black-on-transparent, recolored via PIL at insert time -- see §4.3/`_recolor_icon`), so recoloring an *already-placed* icon needs no new OOXML color-effect element at all. `recolor_pptx_icon(path, slide, shape_index, color)` instead reuses two already-verified mechanisms end to end: `replace_pptx_image`'s relationship-swap (`get_or_add_image_part` + repointing `<a:blip>`'s `rEmbed`, unmodified) to swap the shape's image, and a generalized `_recolor_image` (extracted from `_recolor_icon`, now shared by both) that recolors from the shape's *own current* alpha channel rather than a bundled icon looked up by name -- meaning it works regardless of which icon it originally was, and can be called again on an already-recolored icon (verified live, two recolors in sequence, second one's pixels correctly reflect only the second color). Deliberately not restricted to `add_pptx_icon`-inserted shapes specifically -- the mechanism is generic to any picture with real alpha transparency -- but documented as a real footgun if pointed at an opaque photo (flattens the whole picture to one solid color block, since only the alpha channel is read, no edge detection). **Live-verified**: pixel-level check confirms the recolored icon's opaque pixels match the requested hex exactly (same rigor as `add_pptx_icon`'s own original color test), position/size confirmed untouched, round-tripped through `python-pptx` with warnings-as-errors, rejects a non-picture shape_index with a clear `list_pptx_shapes`-pointing error. 8 new tests in `test_presentations_tool.py` plus a `GET /api/tools` spot-check in `test_web.py`.
7. **Done** -- Master/theme color editing, picked by the user explicitly ("这个很重要"). §6's own flagged decision -- whether to also migrate coscribe's hardcoded-RGB decorative shapes to `theme_color` references so this cascades through coscribe-generated content too -- was put to the user directly rather than assumed either way; **answered "core only"**: edit an existing file's real `<a:clrScheme>`, don't touch `write_pptx`'s own generation code. Shipped as `read_pptx_theme_colors(path)` + `edit_pptx_theme_colors(path, colors)`, `colors` the same comma-separated `key=value` string convention `write_pptx`'s own `theme` parameter already uses (`_parse_theme_tokens`), but naming the real 12 `<a:clrScheme>` slots directly (`dk1`/`lt1`/`dk2`/`lt2`/`accent1`-`accent6`/`hlink`/`folHlink`) instead of that tool's 4-token bg/text/surface/accent abstraction -- editing an already-designed template wants precise slot control, not a simplified identity. **Not new plumbing, reused already-verified machinery**: `_set_scheme_color`/the `theme_part.blob`-parse-and-`._blob`-write-back pattern were both built and round-trip-verified earlier this session for `write_pptx`'s own `theme` parameter (§0) -- this just points the same mechanism at an arbitrary *existing* file's theme part instead of only a freshly-created one, and applies it to every slide master in the file (matching `_apply_theme`'s own existing behavior) rather than assuming exactly one. New: `_read_scheme_color` resolves a slot's current value for the read side -- `<a:srgbClr val="RRGGBB"/>` directly, `<a:sysClr .../>` (dk1/lt1 in most stock themes, confirmed live) via its own `lastClr` fallback attribute, the same sysClr-vs-srgbClr split `_set_scheme_color`'s own docstring already documented. **Live-verified**: round-tripped an edit through a freshly-generated deck (confirmed edited slots changed, every other slot preserved exactly) and through the real bundled `bold-statement` template (confirmed its own stock-theme accent1 changed, deck still opens with `python-pptx` warnings-as-errors); also confirmed live that `bold-statement`'s own decorative accent spine does *not* change color from this edit -- direct, concrete confirmation the "core only" scope decision behaves exactly as scoped, not just as documented. 10 new tests in `test_presentations_tool.py` plus a `GET /api/tools` spot-check in `test_web.py`.
8. **Done** -- SmartArt detection + text extraction for *existing* uploaded files (§7's real remaining gap), scoped explicitly with the user first: **not** a one-call "generate a replacement" pipeline (would need a new generic shape/textbox-insertion primitive this session hasn't built, and wasn't asked for) -- detect, extract each node's text, delete, then rebuild by hand with this file's existing shape/icon/table tools, confirmed as the right scope before building. `list_pptx_shapes`'s `_describe_shape` gained `is_smartart`/`smartart_text` fields; a new, fully generic `delete_pptx_shape(path, slide, shape_index)` (any shape type, not SmartArt-specific) is the removal half. **No hand-waving on the research**: python-pptx itself has zero SmartArt support (`GraphicFrame.shape_type` returns `None` for it, by its own docstring) and no fixture/sample to test detection against was available in this sandbox (no real PowerPoint to author one, GitHub API blocked, and repo-guessing for a real sample .pptx came up empty) -- so the `<a:graphicData uri=".../diagram">`/`<dgm:relIds r:dm=...>` relationship structure was cross-checked against Apache POI's real, shipped `XSLFDiagram.DRAWINGML_DIAGRAM_URI`/`relIds.getDm()` source, and the `<dgm:pt>/<dgm:t>` text-body shape was independently cross-checked against LibreOffice's real oox diagram filter source (`PtContext` routing `DGM_TOKEN(t)` to the same `TextBodyContext` used for every other DrawingML text body, and not filtering by a point's own `type` attribute when deciding whether to parse it) -- two independent, real, shipping OOXML-consuming implementations, not the written spec alone. Then **built and hand-crafted a schema-accurate synthetic fixture** (a python-pptx-saved file with a `<p:graphicFrame>`/4 diagram parts/relationships injected directly into its zip package, matching that cross-checked structure exactly) to verify against, since no real sample existed -- confirmed live: `shape_type` reads `None` and `graphicData_uri` matches on the synthetic fixture (cross-checking the fixture itself against python-pptx's own documented behavior), `is_smartart`/`smartart_text` correctly detect and extract three nodes' text (a run-joining bug -- runs within one paragraph need `""` concatenation, not `" "` -- was found and fixed via this same live check), `delete_pptx_shape` removes the SmartArt shape and the file still round-trips through `python-pptx` with warnings-as-errors, and `delete_pptx_shape` also works correctly on an ordinary (non-SmartArt) shape. **Honest limit, stated plainly**: this proves the code parses the structure it was built against correctly, not that every real PowerPoint-authored SmartArt file matches that structure in every edge case (e.g. real files may have additional presentation/style points this fixture didn't include) -- a real-file caveat in the same spirit as this file's every other "not tested against real PowerPoint" disclosure. 6 new tests in `test_presentations_tool.py` plus a `GET /api/tools` spot-check in `test_web.py`.
9. **Done** -- Hyperlinks, picked by the user as the next Tier 3 item (clear scope, low risk, no external reference needed -- unlike master/theme color editing or SmartArt). Shipped as `add_pptx_hyperlink(path, slide, shape_index, url, text=None)`: `text=None` hyperlinks the whole shape via `Shape.click_action.hyperlink.address` (works on any shape -- picture, autoshape, table, textbox, verified live on all four); `text` given hyperlinks one exactly-matching text run via `Run.hyperlink.address`, raising and listing the shape's actual run texts if nothing matches exactly. **Pure `python-pptx` public API, no hand-rolled XML at all** -- a genuinely lower-risk shape than everything else in this file that touches `<p:timing>`/transitions. **Scope cut, deliberate**: external URLs only (`http://`/`https://`/`mailto:`/`ftp://`, validated); "jump to another slide" internal navigation needs a `TargetMode="Internal"` relationship plus an `action="ppaction://hlinksldjump"` attribute that python-pptx's own `Hyperlink` class has no public support for -- would mean hand-rolled XML for a feature the user didn't ask for, not built. `list_pptx_shapes`'s `_describe_shape` now also reports each shape's whole-shape `hyperlink` (`None` if unset), matching the "inspect via list_pptx_shapes before editing" pattern the shape/table tools already established -- per-run hyperlinks are not surfaced there (would need enumerating every run, a bigger change for a first pass). **Real, evidence-backed finding along the way, flagged then fixed as the next item the user picked**: `_get_shape_at_index` (used by `list_pptx_shapes`/`edit_pptx_shape`/`replace_pptx_image`/the table-cell tools, and now this) treats `shape_index` as 0-based -- `add_pptx_animation`, built earlier this session, independently inlined its own bounds check treating `shape_index` as 1-based (`shapes[shape_index - 1]`), and its own docstring said so ("shape_index is 1-based ... index 1 is the title"). A model that called `list_pptx_shapes` first (0-based) and then `add_pptx_animation` (1-based) on the same index would have silently targeted the wrong shape. 11 new tests in `test_presentations_tool.py` plus a `GET /api/tools` spot-check in `test_web.py`, live-verified (whole-shape, run-text, `list_pptx_shapes` surfacing, every error path, round-tripped through `python-pptx` with warnings-as-errors).

**Follow-up, done immediately after**: `add_pptx_animation` now reuses `_get_shape_at_index` instead of its own inline 1-based bounds check, so `shape_index` is 0-based there too, consistent with every other shape-targeting tool in this file. Its docstring and README.md both updated to say so. All of `add_pptx_animation`'s own existing tests (title=index 0, body=index 1) updated to match rather than left silently wrong -- a real behavior change, not a silent compatibility shim, since the old 1-based convention was the actual bug.
10. Native shape/element grouping -- not on the original list, surfaced by the rotation-precision and animation-selection research above: real OOXML `<p:grpSp>` grouping is what would make both "rotate a composite element as one unit" and "select the thing to hand-animate in PowerPoint afterward" actually easy; no urgency, but worth remembering as the thing several other Tier 2/3 items would benefit from once it exists

**`list_pptx_shapes` -- done**, built alongside item #1 above rather
than after, per §6's "shape-lister" lesson. Still needed for item #3
(image/icon replacement) when that's built.

## 9. Real bug found and fixed: an empty "badge" shape on every content slide

User feedback (a real screenshot of `modern-block`'s content slide,
rendered in PowerPoint) flagged a small pastel-blue rounded square
floating in the bottom-right corner with no text, no icon, and no
apparent purpose. Checked all three bundled templates directly (not
just the one flagged): **every one of them** has this exact same
~0.55in shape on every content-role slide -- a `Rounded Rectangle` on
`modern-block` (bottom-right on one content slide, bottom-left on the
other -- hand-authored, no source script, see below), an `Oval` on
`minimal-light`, another `Rounded Rectangle` on `bold-statement`.
Traced the root cause in `scripts/build_pptx_templates.py`
(`minimal-light`/`bold-statement`'s real source): `_content_slide`'s
`badge_shape`/`badge_color` parameters unconditionally added this shape
via `_add_shape(slide, badge_shape, 10774680, 6217920, 502920, 502920,
badge_color)` -- a leftover decorative idea (an accent "badge" in the
corner) that was never given content and just reads as visual noise,
exactly matching the screenshot.

**Fixed at the source, not just patched in the binaries**: removed the
badge shape entirely from `_content_slide` (and the now-unused
`badge_shape`/`badge_color` parameters from its two call sites),
re-ran `scripts/build_pptx_templates.py` to regenerate
`minimal-light`/`bold-statement` cleanly. `modern-block` predates that
script (hand-authored, no generator to re-run -- see the script's own
module docstring) so its two badge shapes were removed directly from
the checked-in `template.pptx` using **this session's own
`delete_pptx_shape`** -- a real, immediate use of that tool on
coscribe's own shipped assets, not just its own test suite.
`left_spine` (the intentional colored accent bar down `bold-statement`'s
left edge) and the thin title-slide accent rule are untouched -- both
are documented, purposeful design elements, not the thing that was
flagged.

**Removing the badge surfaced a second, real defect it had been
accidentally masking**: `test_every_bundled_template_has_no_geometric_text_overlaps_or_missing_visuals`
(an existing, already-shipped test reading the real files directly)
started failing on `modern-block` *and* `minimal-light` -- their
content slides, with the badge gone, had *zero* remaining visual
element at all, tripping `_check_missing_visual_elements` (the same
"is this deck just bare text on a background" check `write_pptx`
itself uses on model-generated decks). The badge had been silently the
only thing satisfying that check, not a coincidence: `minimal-light`'s
own manifest already promises "one thin accent-color rule under each
title," but `_content_slide` never actually added that rule --
`_title_slide` was the only place it was implemented, so the promise
was already broken before the badge existed at all. Fixed properly,
not by weakening the check: `_content_slide` gained a `title_rule_color`
parameter reusing the exact same thin-rule shape/size `_title_slide`
already uses, positioned in the real, verified gap between the content
title and body placeholders (1417638-1645920 EMU here) without
touching either; wired into `build_minimal_light` (fulfilling its own
manifest's promise for real). `modern-block`'s manifest instead
explicitly disclaims "decorative accent lines," so the same rule motif
would have contradicted its own stated identity -- gave it a real,
different fix instead: a full-width solid-color bar flush against the
bottom edge (a genuine "block," matching "Modern Block"'s own name/
identity, not a line), added directly to the checked-in file the same
way the badge removal was (no generator script for this template).

**Verified**: all three templates round-trip through `python-pptx`
with warnings-as-errors after both fixes; `_check_text_overlaps` and
`_check_missing_visual_elements` (the same functions
`test_every_bundled_template_has_no_geometric_text_overlaps_or_missing_visuals`
calls, checked directly, not just via one assert that stops at the
first failure) both return `[]` for all three templates now, and the
full `pytest tests/test_pptx_templates_tool.py tests/test_presentations_tool.py`
run (225 passed) confirms no regression anywhere else.

## 10. A 4th bundled template: `velis`, a real third-party CC0 design

User feedback after reviewing the (now-fixed) 3 templates: "these all
feel too generic, where did you even find them" -- an honest answer
("coscribe authored them itself, procedurally, from solid rectangles +
typography") wasn't a satisfying one, so the user asked to look for
legally-usable open-source/public-domain templates instead of only
investing further in coscribe's own python-pptx design work.

**Search, not guessing**: two rounds of `WebSearch` (general "CC0 pptx
template github" queries, then `github.com/topics/powerpoint`,
`madjin/awesome-cc0`, the `slideshow-templates` GitHub org, and US
federal public-domain sources) turned up exactly one real candidate
clean enough to bundle: **`lrkrol/powerpoint`**'s `lrk-slides-velis.potx`,
by Laurens R. Krol, explicitly **CC0 1.0** (verified on the repo itself,
not inferred). Everything else found was either PPTX *tooling*
(MCP servers, AI generators), org-branded templates not licensed for
third-party reuse (GBIF, Alan Turing Institute), or "free template"
sites (SlidesCarnival/Slidesgo-style) whose terms don't clearly permit
redistribution inside another project, only end-user use -- a real,
narrow bar, not cleared by "free to download."

**Verified before integrating, not assumed**: downloaded the real
`.potx`, confirmed with `file`/`python-pptx` that it's genuine OOXML
(PowerPoint 2007+), and that python-pptx rejects `.potx` outright
(`ValueError: ... not a PowerPoint file, content type is
...presentationml.template.main+xml`) -- the *only* difference between
a PowerPoint template and a PowerPoint presentation turned out to be
that one string in `[Content_Types].xml`; rewriting it to
`...presentationml.presentation.main+xml` is a lossless, standard
conversion (no OOXML part touched), confirmed by then successfully
opening and inspecting all 16 real slide layouts (proper CENTER_TITLE/
SUBTITLE/TITLE/BODY/OBJECT/PICTURE placeholder types, not decorative
text boxes) via python-pptx.

**Built via a real, re-runnable build step**
(`scripts/build_pptx_templates.py`'s new `build_velis()`/
`_fetch_velis_pptx()`), not a one-off hand edit: fetches the real
`.potx` from its source URL at build time (same "network at build time,
commit the output" pattern `build_pptx_icons.py` already uses for
Lucide), does the content-type rewrite, then builds a 4-slide
title/content/content/closing deck from the original's own layouts --
layout 0 ("Presentation Title": CENTER_TITLE + SUBTITLE) for title and
closing (reusing one shape design for both, the same pattern
`_title_slide` already uses twice for minimal-light/bold-statement),
layout 4 ("Title and Content": TITLE + BODY + OBJECT) for the 2 content
slides.

**A real footgun found and avoided, not just an aesthetic tweak**: layout
4's secondary `BODY` placeholder (idx 13) is a thin ~0.06in-tall "Slide
Title" tagline strip directly under the real title -- and
`_find_body_placeholder` (`tools/presentations.py`) always matches the
*first* eligible placeholder in on-slide idx order. Left in place,
every `fill_pptx_template` call against `velis` would have silently
stuffed real bullet content into that sliver instead of the actual
content area (OBJECT, idx 17), guaranteeing overflow on every generated
deck. Same issue on layout 0's unused `OBJECT` "Logo" placeholder (idx
17, no logo asset to put there). Fixed by removing both unwanted
placeholders from each freshly-added slide via the same "no public
delete API, drop the element directly" idiom `delete_pptx_shape` already
established (a new `_remove_placeholder` helper in the build script) --
verified live afterward that every slide has exactly one TITLE-type and
at most one eligible body-type placeholder, and that a real
`fill_pptx_template` call puts title/bullet text in the right place on
every slide.

**A second real bug found and fixed in the QA check itself, not
special-cased around**: `_check_missing_visual_elements` initially
flagged all 4 `velis` slides as "no visual element" even though the
template plainly does have one -- its whole stage-curtain motif is two
`GROUP` shapes drawn once on the **slide master** (confirmed by
inspecting `slide_master.shapes` directly), not copied onto every
individual slide, which is the normal, correct way PowerPoint templates
share background art. `_slide_has_visual_element` only ever looked at
`slide.shapes`, so it had a real blind spot beyond the already-documented
"background color doesn't count" one. Fixed by factoring the shape-type/
fill-type check into `_shapes_have_visual_element` and having
`_slide_has_visual_element` also check the slide's own `slide_layout`
and that layout's `slide_master` (non-placeholder shapes only -- a
placeholder there is a style skeleton, not a visible graphic). This is a
general fix, not velis-specific: it makes the same check more accurate
for any future template or `write_pptx`/`fill_pptx_template`
`template_path` whose decorative art lives on a layout/master, which
stock python-pptx layouts (used by every other template so far) never
exercise, so no existing template's result changed.

**Licensing paper trail**: `src/coscribe/builtin_templates/pptx/velis/LICENSE`
(written by the build script from `_VELIS_LICENSE_TEXT`) states the CC0
1.0 source, author, and exactly what was mechanically changed (content-
type conversion, reduced to 4 slides, 2 placeholders removed) versus
what wasn't (no other visual design changes). `tools/pptx_templates.py`'s
module docstring and `README.md`'s template paragraph both updated to
stop claiming every bundled template is a coscribe original -- `velis`
is the one documented exception, and the bar that got it there (a
verified CC0/public-domain license, not just "free to use") is now
written down as the actual policy for any future addition.

**Verified**: `load_builtin_templates()` picks up all 4 templates;
`_check_text_overlaps`/`_check_missing_visual_elements` both return `[]`
for all 4, checked directly; a real `fill_pptx_template("velis", ...)`
call round-trips a 4-chunk deck with title/bullet text landing in the
correct placeholder on every slide (verified via direct python-pptx
inspection of the saved file, not just the tool's own return value);
`ruff check`/`mypy` clean on both changed files; new tests added
(`test_check_missing_visual_elements_counts_a_decorative_shape_inherited_from_the_layout`,
plus the existing template-suite tests generalizing to velis
automatically since they already loop over `load_builtin_templates()`);
full `pytest` run (`--ignore=tests/test_secrets.py`, a pre-existing,
unrelated `keyring`-import gap from merged-in parallel-session work,
not this change) is 657 passed / 13 skipped, with the only other 3
failures being that same missing-`keyring` issue surfacing in
`test_web.py`.

## 11. A 3rd search round (2 more real candidates, both rejected) + gradient polish on the 3 original templates

User asked for two things after `velis` shipped: keep searching for more
legally-usable templates under the same rigor, and separately invest in
polishing the design quality of the 3 coscribe-original templates
(previously the declined alternative -- now both, not either/or).

**Search round 3**: `wzpan/BeamerStyleSlides` (MIT, a real library of
Beamer-style `.pptx` themes across ~20 folders) and
`sbryngelson/Fake-Beamer` (MIT, ships an actual `fake-beamer.potx` +
`example.pptx`) both looked promising on paper -- real, clearly-MIT-
licensed repos, not just "free to use." Both rejected after actually
downloading and inspecting the files, not just reading the license:

- `BeamerStyleSlides`: every theme file (`Madrid/default.pptx`,
  `default/*.pptx`, all ~20 of them) turned out to be the *same* real,
  filled-in personal presentation -- one person's actual job-interview
  deck (`移动客户端通道面试陈述`, their real name, a real employer
  department name in the subtitle) re-skinned across every Beamer color
  theme as a demo gallery, not a blank reusable template. The repo's own
  MIT license is real, but repurposing someone's actual biographical/
  interview content as a "blank starter template" would be a different
  kind of problem than a licensing one -- rejected on that basis, not a
  license technicality.
- `Fake-Beamer`: the `.potx` itself is a genuine blank template (real
  TITLE/BODY/OBJECT placeholders, verified via python-pptx after the
  same potx->pptx content-type conversion `velis` needed), but its slide
  master embeds a small vector logo (`ppt/media/image1.emf`, positioned
  bottom-right like an institutional watermark) that couldn't be
  identified with the tools available in this sandbox (no EMF renderer;
  `strings` on the file found only an embedded PDF blob, not readable
  text) -- and the linked `example.pptx` explicitly credits "Georgia
  Institute of Technology" in its own subtitle placeholder text. A
  logo that's very likely third-party institutional branding is a
  trademark risk *separate from* the file's own MIT copyright license
  (MIT covers the author's arrangement, not necessarily any mark he
  included for his own use) -- rejected rather than guessed at by
  stripping an image whose actual content couldn't be verified.

Two more real, live-verified rejections on top of the first two search
rounds' findings -- `velis` remains the only template that cleared the
bar. Also checked and discarded on lighter inspection: `NESTLab/
PresentationTemplates` and `hplgit/MAlley-slide-templates` (no license
stated for either; the latter also ships `.ppt`, not `.pptx`).

**Design polish on the 3 originals**, via python-pptx's public
`FillFormat.gradient()`/`.gradient_stops`/`.gradient_angle` (verified
live: write two RGB stops + an angle, read the exact values back).
New `_add_gradient_shape`/`_set_gradient_background` helpers in
`scripts/build_pptx_templates.py`; `_title_slide`/`_content_slide` each
gained optional `*_end` color parameters so a caller opts into a
gradient without changing anything about a shape's geometry (every
position/size is byte-for-byte identical to before -- only the fill
changed, so the already-verified overlap/visual-element checks needed
no new reasoning, just a re-run):

- `modern-block`: the flat navy background, the light accent color
  block, and the content slides' bottom bar are now each a subtle
  same-hue gradient (`1E2761`->`2A3A8F` navy, `CADCFC`->`EAF1FF`
  accent) instead of flat fills -- real depth on the "solid color
  block" motif that's this template's whole identity, not a new
  decorative element.
- `minimal-light`: the thin accent rule fades from `0F6B5C` to a
  lighter `4FA890` tint, a soft "fade" rather than a flat bar -- kept
  deliberately subtle (no gradient on a background or a big block) to
  stay true to its own "quieter, more understated" identity; a big
  gradient block here would have contradicted the manifest's own "no
  solid color blocks" claim.
- `bold-statement`: the dark title/closing background fades to near-
  black (`111827`->`000000`, a soft vignette -- the same technique real
  keynote-style decks use for depth), and the left accent spine/title
  rule fade to a deeper `7C2D12` shade of the same orange accent.

**A real, separate cleanup surfaced along the way**: `modern-block` was
the one template with no generator function at all (hand-authored
directly on the checked-in binary, per its own module-docstring
history) -- polishing it meant either hand-patching the binary a third
time or finally giving it a real `build_modern_block()`. Chose the
latter: reproduced its exact original geometry byte-for-byte (read
back from the shipped binary's own XML -- title/subtitle positions,
the block rectangle's position/size on both the title and closing
slide, the content slides' title/body/bottom-bar positions) before
adding the gradient, so this is a like-for-like generator replacement,
not a redesign riding along with the polish. All 3 originals are now
real, re-runnable generators for the first time simultaneously.

**Verified**: `_check_text_overlaps`/`_check_missing_visual_elements`
both still return `[]` for all 4 bundled templates (checked directly);
spot-checked each new gradient's exact stop colors/angle by reading the
saved XML back through python-pptx (not just trusting the write
succeeded); `ruff check`/`mypy` clean on `scripts/build_pptx_
templates.py`; full `pytest tests/test_presentations_tool.py tests/
test_pptx_templates_tool.py tests/test_web.py` run (the template-suite
tests already loop over every bundled template, so they exercise the
regenerated geometry/gradients with no test changes needed) -- no
regressions beyond the pre-existing, unrelated `keyring`-import gap.

## 12. Four real bugs found from the demo deck the user actually looked at

The user's verdict on the first demo deck, with two screenshots: "效果很差
...原创模板思路还是不太行" (looks bad, the original-template approach still
isn't quite right). Worth being precise about what the screenshots
actually showed, since the diagnosis matters: neither screenshot involved
`fill_pptx_template` or one of the 4 bundled templates at all -- the demo
used `write_pptx` with a custom `theme=` parameter and the `icon-list`/
`stat-callout` layout builders plus `add_pptx_chart`. The defects were
real, but in `write_pptx`'s own layout-generation code, not the template
work from §§9-11. Four separate bugs, each traced to its actual root
cause and fixed at the source rather than patched around in the demo
content:

**1. A stat-callout table column-order footgun (self-inflicted, not
guessed at)** -- the demo's own content had the columns backwards
(`| 指标 | 数值 |`, i.e. label first), which `_add_stat_callout_slide`
silently rendered exactly as swapped: the long descriptive label large/
bold/accent-colored, the actual short value small/muted -- visible in
screenshot 1 as "客户满意度 NPS" wrapping to two lines in giant orange
text while "62" sat small and gray above it. `_stats_from_table`'s own
docstring says "(stat, label) pairs" and the column order is
documented, but nothing enforces it, and "label, value" reads more
naturally in a markdown table than "value, label" -- easy enough to get
backwards that the author of this exact code did, live, while building
the demo. Fixed by adding a heuristic check (a real stat is essentially
always shorter than its own label in any realistic KPI table, so
"column 0 longer than column 1" on a majority of rows is a reliable
enough signal) that raises a clear, actionable `ValueError` instead of
silently rendering it backwards.

**2. Stat-callout cards top-anchored, wasting most of the slide** --
`_add_stat_callout_slide`'s own card_height is deliberately much
shorter than the full content area (a prior, already-documented fix for
empty space *inside* each card, PPTX_DESIGN.md §0), but the row of
cards was anchored to the top of the content area, so all the leftover
vertical space landed as one large empty gap below the cards instead of
being split above/below -- visible in screenshot 1 as roughly 70% of
the slide left blank underneath the 4 cards. Fixed with one line:
`card_top = top + (height - card_height) // 2`, vertically centering
the row instead of anchoring it.

**3. `add_pptx_chart` overlapping a slide's existing body text**
(the most visible defect, screenshot 2): a chart added to a slide that
already had one short intro sentence landed directly on top of that
text -- the chart's own title ("季度营收趋势（万元）") rendered right
through the placeholder's body text ("过去四个季度的营收变化...").
Root cause: `add_pptx_chart` positioned every chart at a fixed
`Inches(1, 1.6, 8, 5)`, with zero awareness of what else was already on
the slide (or of the deck's real slide size -- on a 13.33in-wide
widescreen deck, an 8in-wide chart also left a lot of dead space to the
right, a second, subtler defect in the same screenshot). Compounding
this, **`_check_text_overlaps` had a real blind spot that let this ship
undetected**: a chart is a `GraphicFrame`, not a text-frame shape, so it
was invisible to the check's text-vs-text-only scan even though its own
rendered content visually collided with real text. Two real fixes, not
one:
- `_check_text_overlaps` now also flags a text shape overlapping a
  chart or table `GraphicFrame`, not just other text shapes -- general,
  not demo-specific, so it also protects any future `write_pptx`/
  `fill_pptx_template`/hand-edited deck.
- `add_pptx_chart` now defaults to the same `_content_area()` geometry
  every other content slide uses (fixing the wasted-space problem for
  free), and if the target slide already has a non-empty body
  placeholder, shrinks that placeholder to fit its own actual text
  (estimated from its real paragraph count, not its full stock-layout
  box height -- see the fix's own docstring for why the box height
  alone is misleading) and positions the chart below it. If the
  remaining room after that is too small for a usable chart, it now
  raises a clear `ValueError` telling the caller to shorten the body
  text or use an empty-body slide, instead of silently producing a
  broken layout.
- **A second bug inside the first fix, caught before it shipped**: the
  first version of this fix computed the chart's height as a fraction
  of the *original* content-area height regardless of how much room was
  actually left after the body placeholder, which could (and did, in
  live testing) push the chart's bottom edge past the actual bottom of
  the slide entirely. Live-verified by computing `chart.top +
  chart.height` against `prs.slide_height` directly before treating the
  fix as done -- caught this exact regression, not assumed correct from
  the diff alone. The corrected version measures the real remaining
  space against the slide's own actual height, not a borrowed constant
  from an unrelated area calculation.

**4. Chart text unreadable on a dark theme** -- python-pptx's chart
default text color is black; `write_pptx`'s own `theme=` parameter
lightens the deck's placeholder text but has no way to reach into a
chart's internal font settings, so the axis labels/legend text in
screenshot 2 stayed dark and were nearly illegible against the deck's
dark navy background. Fixed by reading the deck's own theme `dk1` slot
(the same scheme tag `_THEME_SCHEME_COLOR_TAGS` maps `theme="text=..."`
onto) via the already-existing `_read_scheme_color` helper, and setting
`chart.font.color.rgb` to that value -- so a chart's text always
matches whatever the deck's own designated text color actually is,
correct for both the stock default (black on white) and any custom
`theme=` alike, not hardcoded either way.

**Verified**: all 4 fixes covered by new, live-reproduced-scenario
tests (`test_layout_stat_callout_rejects_swapped_columns`,
`test_layout_stat_callout_cards_are_vertically_centered`,
`test_add_pptx_chart_positions_below_existing_body_text_without_overlap`,
`test_add_pptx_chart_rejects_when_body_text_leaves_no_room`,
`test_add_pptx_chart_font_color_matches_default_black_theme`,
`test_add_pptx_chart_font_color_matches_custom_dark_theme`,
`test_check_text_overlaps_flags_text_overlapping_a_chart`); confirmed
the pre-existing chart/stat-callout test suite still passes unmodified;
rebuilt the actual demo deck end-to-end with the corrected column order
and re-verified directly (not assumed) that the chart's bottom edge is
within `slide_height`, `_check_text_overlaps` returns `[]` for the
whole deck, and `chart.font.color.rgb` matches the deck's own theme
text color; `ruff check`/`mypy` clean on `tools/presentations.py`
(the test file's own pre-existing, unrelated `dict[str, object]`-typing
mypy noise -- 428 errors before this change, confirmed via `git
stash` -- is untouched by these fixes, not a new regression).

## 13. A fifth, more severe bug in fix #3 above -- and the real limit behind all of this

The user reviewed the fixed deck and reported a third screenshot: the
chart slide's own intro sentence rendered as one Chinese character per
line, cascading down and off the left edge of the slide, overlapping the
chart title. Traced to the fix for bug 3 in §12 (`add_pptx_chart`
shrinking an existing body placeholder to make room for the chart):
that fix set `body_placeholder.height = intro_height` alone. Confirmed
live by inspecting the saved XML directly: that placeholder never had
its own `<a:xfrm>` (a normal, unmoved placeholder inherits position/
size from its slide layout instead) -- python-pptx's `.height` setter,
finding no existing `<a:xfrm>`, created a **new** one containing only
`<a:ext cy="...">`, leaving width at its XML default of 0 and omitting
`<a:off>` (position) entirely:
```xml
<a:xfrm><a:ext cx="0" cy="411480"/></a:xfrm>
```
A 0-width text frame word-wraps to the narrowest possible column --
one character per line -- exactly matching the screenshot. **No
automated check caught this**: a 0-width shape still has a bbox, so
neither `_check_text_overlaps` nor `_check_missing_visual_elements`
had any reason to flag it; only a human looking at a render could
tell. Audited the rest of `presentations.py`/`build_pptx_templates.py`
for the same pattern (grepped every `.left =`/`.top =`/`.width =`/
`.height =` site) -- every other site already sets all four dimensions
together in one block, so this was isolated to the one new call site.
Fixed by setting all four dimensions explicitly whenever any one of
them is touched on a placeholder that might be relying on inherited
geometry, plus a defensive guard right after (raises immediately if
the resulting width/height is non-positive) so a repeat of this exact
mistake fails loudly at generation time instead of shipping silently.

**The more important finding isn't the bug, it's what let three of
them ship in a row: this sandbox cannot render PPTX at all.**
Re-confirmed directly, not assumed from the earlier-documented
limitation: `soffice --headless --convert-to pdf` fails even on a
trivial, definitely-valid single-slide deck built with vanilla
python-pptx, with `Error: source file could not be loaded`. Every fix
in this session has been verified by computing/reading back geometry
programmatically (bounding boxes, XML inspection, the overlap/missing-
visual heuristics) -- never by actually looking at a rendered slide.
That's a real, structural gap: the automated checks catch the classes
of defect they were specifically written to catch, and nothing else --
a 0-width shape, an odd color choice, a genuinely unbalanced layout
that isn't strictly "overlapping" or "empty" can all pass every
existing check and still look wrong to an actual viewer. The user's
own words -- "这样下去不是个办法，泛化能力太低" (this isn't
sustainable, generalization is too weak) -- are the accurate diagnosis:
reactively patching whatever the next screenshot reveals doesn't
converge, because the population of possible defects this environment
can't see is larger than any specific set of geometry checks can cover.

## 14. Decision: default to the 4 bundled templates, not write_pptx's freeform layouts

Given §13's diagnosis, asked the user directly how to proceed rather
than shipping a fourth blind demo. Their choice: default to
`fill_pptx_template` with the 4 already-heavily-vetted bundled
templates (modern-block/minimal-light/bold-statement/velis, see §§0,
9-11) for anything that needs to actually look good, rather than
`write_pptx`'s freeform combination of custom `theme=` colors plus the
`icon-list`/`stat-callout`/`add_pptx_chart` layout builders -- which is
exactly the combination every bug in §12-13 came from. The 4 templates
have had far more scrutiny (hand-verified geometry, the same automated
checks, and now several rounds of real bugs found and fixed against
them specifically) than any one-off freeform combination `write_pptx`
can produce on demand. Trade-off, stated plainly: `fill_pptx_template`
only supports title+bullets content, no charts/icon cards/tables --
real capability is given up for reliability. `write_pptx`'s richer
layouts aren't removed or deprecated (still real, tested, useful for a
caller who explicitly wants a chart or a bespoke layout), but they're
no longer the default choice this project reaches for when the goal is
"impressive, safe to ship."

## 15. The new default policy's first real gap: velis picked for a plain request, not 16:9

Sent a fresh demo under §14's new policy (`fill_pptx_template` with
`minimal-light` and `velis`). Real user feedback on it: "还有个不是
16:9PPT，默认应该是16:9，除非用户要求" (one of them wasn't 16:9,
default should be 16:9 unless the user asks) -- accurate: `velis` keeps
its own original A4-landscape proportions (documented honestly in its
own manifest/README paragraph since §10), and nothing in the selection
policy actually told the model to prefer 16:9 by default. §14's "pick
based on tone/fit" guidance said nothing about proportions at all.

**Deliberately not "fixed" by resizing velis.** Rescaling a real, hand-
authored third-party design to a different aspect ratio means
repositioning every placeholder plus the slide master's own compound
`GROUP` shapes (the curtain motif, §10) without distorting them -- real
OOXML risk, and exactly the kind of blind geometry surgery §13 already
showed goes wrong in this sandbox with no way to render and check the
result. `velis` stays as originally bundled; this is a *selection*
policy fix, not a file fix.

**Fixed at the selection layer instead**: `TemplateInfo` gained
`is_widescreen`, computed from the real file's own `slide_width`/
`slide_height` (`abs(ratio - 16/9) < 0.05`) at manifest-load time --
not assumed from the id, so a future non-16:9 template addition gets
flagged automatically instead of relying on someone remembering to
document it. `format_template_listing` now tags every line
`[16:9]`/`[NOT 16:9]`, and `coordinator.py`'s instructions state the
actual rule plainly: default to a `[16:9]` template; only pick a
`[NOT 16:9]` one when the user is fine with different proportions or
names that template specifically. `write_pptx`'s own from-scratch path
was already always 16:9 (`_WIDESCREEN_WIDTH_EMU`, confirmed by reading
that code, not assumed) -- this gap was specific to `fill_pptx_template`
template selection.

**Verified**: `load_builtin_templates()` reports `is_widescreen=True`
for `modern-block`/`minimal-light`/`bold-statement` and `False` for
`velis`, computed live from each real file, not hardcoded per id;
`format_template_listing()`'s output tags each line correctly; new
tests (`test_load_builtin_templates_flags_real_aspect_ratio_from_the_
file_itself`, `test_format_template_listing_tags_aspect_ratio`); the
existing synthetic-template test fixture in
`test_presentations_tool.py` updated for the new required
`TemplateInfo` field; `ruff check`/`mypy` clean; full relevant test
suite passes with no regressions.

## 16. Competitive research: where coscribe's PPTX tools stand against the field

User asked for a full competitive read against other AI PPT generators.
Researched three tiers via live web sources (not training-knowledge
recall): international consumer generators (Gamma, Beautiful.ai, Tome,
Microsoft Copilot for PowerPoint, Canva Magic Design, Plus AI/SlidesAI/
Slidesgo, Decktopus), Chinese-market tools (WPS AI 灵犀, 讯飞智文, 天工AI
PPT, ChatPPT, iSlide, MindShow), and the open-source/API-first tier
architecturally closest to coscribe (Presenton, slide-deck-ai,
ppt-master/pptAgent). Full sourced report published as an Artifact
("The Slide Landscape") and walked through with the user directly; this
entry keeps the durable findings in the repo.

**The one structural gap, confirmed against the field, not just this
sandbox**: every consumer tool and in-app copilot researched (Gamma,
Canva, Beautiful.ai, Copilot, WPS) lets a human or the tool itself see
the actual rendered slide before it's finalized -- several (Beautiful.
ai's "Smart Slides," 300+ layouts) actively re-flow layout live to
prevent overlap/overflow, exactly the class of defect §§12-13 found
here only via geometry heuristics. The open-source tier (Presenton,
slide-deck-ai, ppt-master) -- the architectural family coscribe
actually belongs to (LLM driving python-pptx or an equivalent, no
separate web editor) -- does **not** document any render/visual-QA
step either, in any of their public READMEs. That's real evidence this
may be an unsolved problem across the whole "LLM + python-pptx"
approach, not a coscribe-specific shortfall -- worth remembering the
next time a similar bug surfaces: the fix is a different rendering
environment or a headless-rendering service, not more geometry-checking
code layered on top of what's already there.

**Two gaps that are real but bounded by ordinary engineering effort,
not environment**: no image sourcing/generation at all (every consumer
competitor auto-sources or generates contextual imagery; coscribe has
icons/SmartArt/tables but no photo pipeline), and a 4-template library
against Canva/MindShow's hundreds or Beautiful.ai's 300+ auto-adjusting
layouts.

**One gap that's a deliberate product-shape choice, not an oversight**:
no live editing canvas. Every competitor researched gives someone a
place to nudge/drag/re-theme after generation; coscribe's output is
agent-authored and finalized, edited only by re-invoking tools or
opening the real file in PowerPoint -- the "generate, tweak visually,
regenerate" loop the whole consumer tier is built around isn't
something this tool-calling architecture offers on purpose.

**Where coscribe is already ahead, confirmed against real competitor
docs, not assumed**: the native `.pptx` is the *only* output here, never
an export -- Canva's own help docs and Beautiful.ai's own blog admit
their PPTX export is lossy/glitchy (Canva: "text boxes move, grouped
elements ungroup, animations stripped entirely"; Beautiful.ai: "font
rendering inconsistencies"). coscribe's charts are native, editable
bar/line/pie, which several competitors (Beautiful.ai, Decktopus)
describe as "limited" in their own materials. And `velis`'s CC0
licensing (§10) is independently verifiable in a way no competitor's
individual template licensing is -- Canva/Slidesgo/MindShow's per-
template terms aren't something a user can actually check themselves.

**Not acted on yet** -- this was a research/evaluation task, not an
implementation request; no code changed in this entry. The gap
priorities above are a candidate backlog for future sessions, in the
order the research supports: (1) investigate whether a different
rendering approach is available in this environment before writing any
more geometry-heuristic code, (2) an image-sourcing integration,
(3) template-library growth, continuing §§9-11's same licensing rigor.

## 17. §16's priority (1), solved: this was never a rendering-backend limitation -- one package was missing

User pushed back on two claims from §16's report in the same breath:
whether a live editing canvas is buildable, and whether "PPTX export is
lossy because of a different underlying architecture" (their words) is
the right read of Canva/Beautiful.ai's own admitted export bugs. The
second question prompted actually re-testing this environment's own
rendering claim instead of repeating it -- and it turned out wrong.

**Root cause, found with `strace`, not assumed**: `soffice --headless
--convert-to pdf` failed on every file all session -- including a
trivial single-slide deck built fresh with vanilla python-pptx, and
even a plain `.txt` file -- always with `Error: source file could not
be loaded`. `soffice --headless --terminate_after_init` succeeded
cleanly, and the profile's own `GraphicsRenderTests.log` showed the
headless `svp` graphics backend passing 66/67 internal tests -- so the
core app and its renderer both work. Tracing the actual failed
`--convert-to` run found the real cause: `openat(".../program/
libswdlo.so", ...) = -1 ENOENT` -- a **missing shared library**, because
this environment's base image installs only `libreoffice-core` and
`libreoffice-common` (confirmed via `dpkg -l`), never the application
packages (`libreoffice-impress`/`-writer`/`-calc`) that actually
provide Impress/Writer/Calc's document-loading filters. Not a sandbox
restriction, not a headless-rendering limitation -- a genuinely missing
`apt` package, installable directly: `apt-get install libreoffice-impress
libreoffice-writer libreoffice-calc` (plus `poppler-utils` for
rasterizing a converted PDF's own pages to inspect them). All fixed the
failure completely -- verified immediately after: `write_pptx`'s
`qa_skipped_reason` became `None`, `overflow_warnings` returned real
findings, and `preview_path` pointed at a real PNG.

**This means every "this sandbox cannot render, we're flying blind"
claim in §§12-16 above was true only because of one uninstalled
package**, not because of anything inherent to this environment or to
the LLM+python-pptx architecture family more broadly (§16's comparison
against Presenton/slide-deck-ai's undocumented rendering status still
stands on its own evidence, but "this specific environment can't
render at all" -- the framing used throughout -- was simply wrong, and
worth flagging plainly rather than quietly recharacterizing).

**Made durable, not just fixed for this one running session**: added
`.claude/hooks/session-start.sh` (installs office-agent's own Python
deps plus the 4 system packages above, idempotent via `dpkg -s`
checks) and `.claude/settings.json` registering it as a `SessionStart`
hook -- so future Claude Code on the web sessions on this repo get a
working LibreOffice automatically instead of rediscovering this same
root cause again. This only takes effect for new sessions once merged
into the repo's default branch.

**First real visual verification of this session's own prior work**:
rendered all 4 bundled templates' title slides, the corrected stat-
callout slide, and the corrected chart-overlap slide from §12-13 to
real PNGs and looked at them directly (not just their bounding-box
geometry) for the first time. All confirmed genuinely correct: the
gradient polish from §11 reads as real depth in all 4 templates; the
stat-callout fix shows the intended value-large/label-small hierarchy,
properly vertically centered; the chart-overlap fix shows the chart
cleanly below the intro text with light, legible axis/legend text on
the dark theme -- no new defects found on first real look.

**`coordinator.py` updated for the new reality**: the recently-added
"this sandbox cannot render a .pptx to check one itself" line (§14)
was itself an instance of the same now-corrected claim -- removed.
Replaced with real, actionable guidance instead: `write_pptx`/
`fill_pptx_template`/`add_pptx_chart`/`edit_pptx_text` all return a
`preview_path` PNG whenever LibreOffice is available, and the model
should now actually look at it (not just check `overflow_warnings`)
before telling the user a deck the user will see is ready -- since
`overflow_warnings` only catches text literally exceeding its box, not
the wrong-hierarchy/wrong-position class of defect that shipped as
real bugs in §12 before a human looked at an actual render. This is
the direct, concrete answer to §16's own top-priority gap: the fix was
never "write more geometry-heuristic code," it was making the render
step work at all and then actually using it.

**On the editing-canvas question**: not attempted, and not recommended
as a next step on its own evidence. A real drag-and-edit canvas needs
either a from-scratch OOXML-aware rendering+interaction engine in the
browser (a project on the order of a lightweight PowerPoint-in-the-
browser, likely larger than everything built in this session combined)
or embedding a third-party viewer/editor (Office/Google Slides' own
web surfaces), which would tie coscribe to an external SaaS account
rather than staying a self-contained local tool -- a real identity
conflict, not just an engineering cost. The now-working render step
above captures most of the realistic value (an actual look at the
output before it ships) at a small fraction of that cost.

**Verified**: hook re-run standalone (`CLAUDE_CODE_REMOTE=true
.claude/hooks/session-start.sh`), exits 0, idempotent on a second run
(the `dpkg -s` checks skip already-installed packages); `write_pptx`
round-tripped live with real `overflow_warnings` on an intentionally
overlong slide and a real PNG at `preview_path`; all 4 bundled
templates and the two previously-blind-fixed scenarios rendered and
visually inspected directly, described above; `ruff check`/`mypy`
clean on `coordinator.py`.

## 18. `layout: svg` gains `<path>` and gradient fills -- the deliberate
alternative to diffusion image generation

§16's research named "no image sourcing/generation" as a real gap
against the field. Follow-up in this session found the "sourcing" half
already wrong (`search_images`/`download_image`/`set_pptx_background_
image` already exist and are wired into `coordinator.py`) -- the real
remaining gap was AI image *generation*. Asked directly whether to add
a diffusion-model image-generation tool, the user's answer was
explicit: no diffusion, ever -- no photos/people/scenes needed, and
their own experience with other AI-PPT tools is that model-generated
imagery there is low-value and wastes tokens. The agreed alternative:
extend `layout: svg`'s existing code-generated-graphics path instead,
since it already proved out the core idea (deterministic, zero
per-call cost, zero licensing risk, exactly on-theme) for rects/
circles/text -- it was just too limited (no organic shapes, no
gradients) to cover real decorative/hero-graphic use.

**What shipped** (`_svg_slide.py`): `<path>` support for a fixed
`M/L/H/V/C/Q/Z` command subset (absolute or relative, including SVG's
own implicit-repeat grammar), converted to a native DrawingML freeform
shape (`<a:custGeom>`). python-pptx's own `FreeformBuilder` only
supports straight line segments -- no public API for
`<a:cubicBezTo>`/`<a:quadBezTo>` -- so those are hand-appended directly
via `OxmlElement`, the same "reach into python-pptx's oxml layer"
pattern `add_pptx_scrim` already established for its own hand-appended
`<a:alpha>`. Verified safe by reading python-pptx's own oxml schema
(`CT_Path2D`'s `moveTo`/`lnTo`/`close` all declare an empty
`successors` list, meaning elements are appended in call order with no
cross-type reordering -- appending `cubicBezTo`/`quadBezTo` the same
way preserves correct drawing order). The shape's bounding box is
computed over *all* path points including bezier control points (a
safe overestimate by the convex-hull property, not a pixel-tight fit).

Also added: `fill="url(#id)"` referencing a `<linearGradient>`/
`<radialGradient>` (found anywhere in the document, `<defs>`-wrapped or
not), on any shape including the background rect. Built by hand-
constructing the whole `<a:gradFill>` XML rather than using python-
pptx's high-level gradient API, which only supports exactly 2 stops
and explicitly raises on radial gradients (`gradient_angle`'s own
docstring: "Raises ValueError for a non-linear gradient"). Linear-
gradient angle conversion turned out to need no axis flip at all:
DrawingML's `<a:lin ang>` is a clockwise angle from horizontal-right in
a y-down coordinate system, which is exactly SVG's own convention too.

**Two real bugs caught by the new tests, not shipped**: (1) the `d`
tokenizer regex only matched the supported command letters, so an
actually-unsupported command like `A` (arc) was silently *dropped*
rather than rejected -- its numeric arguments then got misparsed as
extra coordinates of whichever command came before it, producing a
confusing arity error instead of the intended "not supported, use
run_node_script" message. Fixed by matching any letter and rejecting
unsupported ones explicitly. (2) background-rect promotion checked
literally `children[0]`, so a `<linearGradient>` listed before the
background `<rect>` (legal SVG, no `<defs>` wrapper required) silently
defeated the promotion and left the rect as a real, opaque shape
covering the slide. Fixed to find the first non-definitional
(non-`<defs>`/gradient) child before checking whether it's a
promotable full-canvas rect.

**Verified live, not just via unit tests**: a real deck built through
`write_pptx` with a `<linearGradient>` background, an organic bezier
blob shape with a `<radialGradient>` fill, a straight-line star path,
and text -- rendered through the real LibreOffice pipeline
(`render_pptx_preview`), and the resulting PNG visually inspected: the
gradient background, the blob's curve and radial shading, and the
star's points all rendered correctly, with no text-overlap or missing-
visual-element flags on that slide. `ruff check`/`mypy` clean;
`coordinator.py` and the `pptx` builtin skill's `SKILL.md` both updated
to document the new syntax and to explicitly steer the model toward
`layout: svg`'s path/gradient support instead of ever reaching for
image generation, which this project doesn't and won't have.

## 19. Gap analysis against ppt-master/Anthropic's own pptx skill/Codex's
presentation-skill -- schema validation, leftover-placeholder QA, and a
real, verified finding about "safe" fonts

User asked for a comparison against the field's leading approaches, then
"how do we update to get closer to them," not a rebuild. Researched by
directly reading source, not trusting summaries: cloned
`anthropics/skills` (its `pptx/SKILL.md` -- script-driven, unzip/edit-XML/
zip plus a `pptxgenjs` path, with an XSD-based `validate.py` and a
`markitdown | grep` leftover-placeholder QA step) and `siril9/
presentation-skill` (a Codex skill; source-first `outline.json` + a
composition-grammar renderer). Cross-checked `hugohe3/ppt-master`'s own
`why-ppt-master.md` again specifically for video/OLE: it states
"Embedded/legacy objects (OLE, video, macros)" as deliberately out of
scope, the same call the Codex skill and Anthropic's skill (no dedicated
capability for either, despite being script-driven and generically
capable of touching that XML) independently make -- three structurally
different projects converging on the same exclusion is a real signal,
not a coincidence, so this codebase drops video/OLE authoring from scope
entirely (SmartArt was already dropped the same way, per the user's own
explicit call in this same session).

Three concrete, scoped items came out of this instead of a rebuild:

**19.1 OOXML schema validator (`tools/_ooxml_validate.py`, new).**
Anthropic's `validate.py` checks hand-touched OOXML against the real
ISO-IEC29500-4 XSDs; this codebase had nothing equivalent -- `set_pptx_
transition`/`add_pptx_animation`/`edit_pptx_theme_colors`/the `theme=`/
CJK-font blob edits inside `write_pptx`/`fill_pptx_template` all hand-
build XML with only ad-hoc, function-specific reasoning backing their
correctness (see `add_pptx_animation`'s own `_verify_animation_readback`,
which checks "is *my* edit there," not "is this still valid OOXML").
Anthropic's own schema files are proprietary-licensed ("may not
extract... reproduce... create derivative works" -- read directly from
`LICENSE.txt`, not assumed), so **not vendored** -- instead fetched the
real ECMA-376 5th edition Part 4 (Transitional) schema directly from
`ecma-international.org` (the standard's own publisher, published
specifically for implementers), verified the whole `pml.xsd` import
closure resolves standalone (compiles in ~30ms, validates an in-memory
element in <1ms, confirmed against a real `python-pptx`-authored file),
and vendored that instead -- see `_ooxml_schemas/NOTICE.md` for exact
provenance.

**A real bug this surfaced immediately, not a hypothetical**:
`set_pptx_transition`'s own `p14:dur` attribute (a PowerPoint-2010
extension for precise transition timing, added and verified against real
`.pptx` XML per this file's own docstring) has no home in the base
ECMA-376 schema at all -- confirmed by reading `pml.xsd`'s `CT_
SlideTransition`/`CT_Slide` directly, neither declares an `xsd:
anyAttribute` wildcard. This isn't a bug in the schema or in the
attribute -- ECMA-376 Part 3 (Markup Compatibility and Extensibility)
specifies foreign-namespace extension content as valid OOXML only when a
producer declares its prefix `mc:Ignorable` on an ancestor, and a
schema-strict consumer is specified to strip that content *before*
validating, not reject it. `_set_slide_transition` was silently relying
on this without ever emitting the declaration. Fixed properly, not
special-cased around: `_mark_mce_ignorable`/`_unmark_mce_ignorable` now
add/remove `mc:Ignorable="p14"` on the slide root exactly when p14:
content is written/removed, and `_ooxml_validate.py`'s own `_strip_mce_
ignorable` implements the matching consumer-side half generically (any
future extension namespace, not just p14, works the same way) --
verified against both a positive case (declared ignorable content
validates clean) and two negative cases (undeclared extension content
still fails; an unrelated `mc:Ignorable` declaration doesn't mask a real,
unrelated schema defect next to it), so this isn't "accept anything in a
foreign namespace."

Wired into all 5 hand-XML call sites, always before the mutated element
is serialized into the file (never after) -- so a validation failure
means the user's file genuinely wasn't touched, the same guarantee `add_
pptx_animation`'s existing temp-file dance already made narrowly.
`ruff`/`mypy` clean, full `test_presentations_tool.py`/`test_pptx_
templates_tool.py` suite (267 tests) passes unchanged, plus 7 new direct
unit tests in `test_ooxml_validate.py` covering the valid case, a
deliberately-broken case (wrong child order -- genuinely rejected, with a
real, specific schema error message), and the MCE positive/negative
cases above.

**19.2 Leftover-placeholder QA (`_leftover_placeholder_warnings`).**
Anthropic's skill greps `markitdown` output for `lorem|TODO|\[insert`
after any template fill; this codebase had no equivalent, and `fill_
pptx_template`'s own docstring already warns "Template slots != source
items" without ever checking for it. New field, `placeholder_warnings`,
returned by `write_pptx`/`fill_pptx_template`/`edit_pptx_text` alongside
`overflow_warnings` -- same shape (`[{"slide": n, "text": ...}]`), same
"treat any hit as something to fix" framing in `coordinator.py`. Scoped
narrowly (literal `xxx` runs, "lorem ipsum", a `TODO` marker, `[insert`,
the literal words "placeholder"/"sample text") specifically to avoid
flagging real content that happens to use those words in an unrelated
sense (a deck *about* inserting charts, e.g.) -- verified via both a
positive-case unit test (6 distinct patterns, all caught) and an explicit
negative-case test (real sentences using "insert"/"sample" in context are
not flagged).

**19.3 Safe-font QA-reliability list -- verified in this sandbox, and
found to be narrower than Anthropic's own published list.** `write_pptx`'s
`overflow_warnings` is only as trustworthy as the LibreOffice conversion
it's computed from, and LibreOffice substitutes any font it doesn't have
installed -- a substitute with different glyph widths makes the overflow
check itself wrong (falsely clean *or* falsely flagged) for exactly the
slides using that font. Rather than copy Anthropic's own safe/unreliable
font list wholesale, checked this sandbox's own real substitution
behavior directly via `fc-match` (the same fontconfig resolution
LibreOffice itself uses):

| Font | Resolves to (this sandbox) | Metric-compatible? |
|---|---|---|
| Arial | Liberation Sans | Yes (`fonts-liberation`, designed as a drop-in metric match) |
| Times New Roman | Liberation Serif | Yes |
| Courier New | Liberation Mono | Yes |
| Calibri | DejaVu Sans (generic fallback) | **No** |
| Cambria | DejaVu Serif (generic fallback) | **No** |
| Georgia, Trebuchet MS, Impact, Arial Black, Garamond, Consolas, Palatino Linotype, Aptos | DejaVu Sans/Serif (generic fallback) | No |

Only `fonts-liberation` is installed here; `fonts-crosextra-carlito`/
`fonts-crosextra-caladea` (the packages that make "Calibri"/"Cambria"
resolve to their own real metric-compatible clones, Carlito/Caladea) are
not, so this environment's actual safe list is narrower than Anthropic's
own (Arial/Times New Roman/Courier New only -- Calibri and Cambria are
QA-unreliable *here*, unlike their published claim). This is an
environment-specific fact, not a universal one: a real Windows machine
running the packaged desktop app typically already has genuine Calibri/
Cambria installed (Windows/Office have shipped them since Vista/Office
2007), so `soffice` there would use the real font directly, no
substitution at all -- the risk is specific to wherever `soffice` itself
runs without the named font available, this sandbox and any headless-
Linux deployment included. Documented as: Arial/Times New Roman/Courier
New are safe everywhere; Calibri/Cambria/the rest are safe on a machine
that actually has them installed and QA-unreliable (approximate, ~10%
size slack recommended) anywhere relying on LibreOffice's own
substitution. Installing `fonts-crosextra-carlito`/`fonts-crosextra-
caladea` wherever `soffice` runs for QA would close the Calibri/Cambria
gap specifically -- noted as a possible follow-up, not done here (an
infra/packaging change, out of scope for this pass).

## 20. §19's tier 2: diagram-building shapes, gradient fills, general
image cropping -- all pure high-level python-pptx API, no new hand-XML

Follow-up to §19's gap analysis: ppt-master's own feature list names
"native shapes -- preset geometry with working adjustment handles (block
arrows, chevrons, callouts, flowchart nodes...)", "picture crop and
shape-clip", and "gradients" as real, shipped capabilities this codebase
didn't expose as general tools -- but the underlying python-pptx APIs
were *already proven* inside this file, just hardcoded to one internal
use each: `add_shape(MSO_SHAPE.OVAL, ...)`/`add_shape(MSO_SHAPE.
ROUNDED_RECTANGLE, ...)` for the icon-list/stat-callout layouts' own
circles/cards, `Picture.crop_*` for `set_pptx_background_image`'s own
cover-crop. This tier is "expose the existing primitive generically,"
not new research -- confirmed before writing any code, not assumed.

**20.1 `add_pptx_shape`/`list_pptx_shape_types`.** A curated 49-name
subset of python-pptx's ~180-member `MSO_SHAPE` enum (every name
verified against the real installed enum first, not guessed) --
basic shapes, arrows, flowchart nodes, callouts, matching the exact
categories ppt-master's own list names. `add_pptx_shape(path, slide,
shape_type, left_in, top_in, width_in, height_in, text, fill_color,
line_color)` places one at an exact position/size (unlike icon-list/
stat-callout, which position their own shapes automatically) -- for
building a specific process-flow/decision-tree/comparison diagram shape
by shape. Returns the new shape's own `shape_index` so a follow-up
`edit_pptx_shape`/`add_pptx_hyperlink` call can target it without a
separate `list_pptx_shapes` round-trip.

**20.2 `edit_pptx_shape` gains a gradient fill (`fill_color_2`,
`gradient_angle`).** Real `<a:gradFill>` via python-pptx's own native
gradient API (`fill.gradient()`/`gradient_stops[i].color.rgb`/
`gradient_angle`) -- no hand-built XML, so (unlike §19's OOXML
validator targets) this needed no schema-validation wiring at all,
correct by construction the same way `add_pptx_chart`'s high-level
chart API already is.

**A real python-pptx bug hit live while testing this, not
hypothetical**: `fill.gradient()`'s freshly-created `<a:lin>` element
has no `ang` attribute at all (angle left "inherited"), and python-
pptx's own `gradient_angle` *getter* doesn't handle that case --
`_GradFill.gradient_angle`'s code does `360.0 - clockwise_angle` when
`lin` exists, unconditionally, and crashes with `TypeError: unsupported
operand type(s) for -: 'float' and 'NoneType'` the moment anything (this
codebase's own `list_pptx_shapes`, testing this feature, or a future
caller) tries to *read back* a gradient's angle before one was ever
explicitly written. Worked around on both sides: `edit_pptx_shape`
always sets an explicit `gradient_angle` (defaulting to 90.0, matching
`gradient()`'s own documented "default gradient... is linear at angle
90-degrees" claim -- this just makes that default actually readable
afterward, not only writable) rather than leaving a gradient it just
created in the state that crashes; `_describe_shape_fill` (the function
behind `list_pptx_shapes`'s own `fill` field, extended this tier to
describe a gradient's stops/angle the same way it already did a solid
color) additionally catches `TypeError` defensively, since an
externally-authored file's gradient could hit the same upstream gap
regardless of this codebase's own workaround.

**20.3 `crop_pptx_image`.** Generalizes `Picture.crop_left/right/top/
bottom` (already proven inside `set_pptx_background_image`) to any
picture shape a model finds via `list_pptx_shapes` (`is_picture: true`)
-- crops in place, position/size of the shape's own on-slide box
untouched, only which part of the image shows through inside it
changes. Sets all four fractions together each call (not incremental,
each defaulting to 0.0/uncropped) -- simpler and more predictable than
tracking partial crop state across calls, matching
`set_pptx_background_image`'s own existing all-four-at-once precedent
rather than `edit_pptx_shape`'s "only change what's given" pattern,
since a crop rectangle is normally decided as one complete choice, not
incrementally adjusted one edge at a time.

**Verified real, not just unit-tested**: built an actual deck through
these three tools together (a 5-shape process-flow diagram -- two
flowchart terminators, two arrows, one decision diamond with a
2-stop 45-degree gradient -- plus a cropped image), rendered it through
the real LibreOffice pipeline (`render_pptx_preview`), and visually
inspected the resulting PNG: every shape's type/fill/line/bold-text
rendered correctly, the gradient diamond showed real, visible color
transition along the given angle, and `text_overlap_warnings`/
`slides_missing_visual_elements`/`low_contrast_warnings` all came back
clean. 21 new unit tests (gradient fill/angle/validation, shape
creation/fill/line/text/shape_index-chaining/validation, crop fraction/
range/sum/position-preserving/non-picture-rejection), full
`test_presentations_tool.py`/`test_pptx_templates_tool.py`/`test_ooxml_
validate.py` suite (287 tests) passes, `ruff check`/`mypy` clean on
every touched file, `coordinator.py` updated to document all three.

## 21. Tier 3: auditing the actual coordinator routing decision, real
end-to-end, instead of guessing at a prompt-tuning fix

§19's plan explicitly deferred code changes here: "先查证,不一定要写代码"
-- only add new `layout:` ids if a real audit shows the coordinator
over-using `write_pptx` when `run_node_script` was actually needed.
Ran three real scenarios through the real coordinator (Gemini 3.6 Flash,
then DeepSeek once Gemini's free-tier daily quota was hit mid-audit) via
`coscribe --accept-edits --message`, each chosen to test one specific
routing boundary, each verified by rendering and *looking at* the
resulting image -- not trusting the model's own summary of what it built,
consistent with this file's own recurring discipline.

**21.1 Build a new, simple diagram from scratch** ("3-step flowchart,
arrows, colored rounded rectangles, no other text"). Routed to
`write_pptx`'s existing `layout: svg` (§18), **not** the new
`add_pptx_shape` -- one call, correct and clean on the first render
(`AUTO_SHAPE`/`ROUNDED_RECTANGLE` for the boxes, `FREEFORM` for the
arrows, confirmed by inspecting the saved file's own shapes, not just
the image). Honest finding: `layout: svg` already covers this exact
"a few shapes plus arrows, built from scratch" case, so tier 2's new
tool isn't automatically preferred for it -- not a bug, just evidence
the two tools' actual territories don't fully overlap the way they
might look like they should on paper.

**21.2 Add one shape to an existing deck** (a real 2-slide deck already
on disk; "add a green up-arrow to the bottom-right of slide 1, don't
touch anything else"). This is `layout: svg`/`write_pptx`'s blind spot
-- both rebuild a deck's slides from scratch, discarding existing
content. Routed correctly: `list_pptx_shape_types` (discovering the
name) -> `list_pptx_shapes` (inspecting the existing slide first, per
coordinator.py's own "never guess shape_index" instruction) ->
`add_pptx_shape` -> a follow-up `list_pptx_shapes`/`read_pptx` pair
verifying the rest of the slide was untouched. Rendered and visually
confirmed: the original title/bullets are pixel-identical, the new
arrow sits exactly where asked. This is precisely the use case tier 2
was built for, and it worked exactly as designed on the first try.

**21.3 A genuinely bespoke, ambitious layout** ("magazine-cover-style
slide, title text wrapping three irregular overlapping circles with
real partial-transparency overlap, asymmetric composition, explicitly
not a simple-shapes/title+bullets construction"). The model's own first
line: "I'll build this as a real, hand-composed slide with raw pptxgenjs
scripting... that's exactly what the prefab layouts can't express" --
routed to `run_node_script` immediately, no hesitation, no wrong turn
through `write_pptx` first.

**What's actually valuable here isn't that it routed correctly (expected
once the case is unambiguous) -- it's what the build-review loop did
over the next ~13 rounds**, each a real `run_node_script` ->
`render_pptx_preview` -> `review_work` -> fix cycle, verified by reading
the full transcript, not summarized secondhand:

- The reviewer (an independently-prompted `review_work` call, looking at
  the actual rendered PNG plus `list_pptx_shapes`'s real shape geometry)
  caught the builder **overclaiming a fix that wasn't real, repeatedly**:
  "duplicate picture pairs are gone" while the shape inventory still
  showed them; "the multiply blend is computed across the crossing
  zone" while the opaque circles drawn on top of it made the blend
  invisible; a claimed 0.7in circle overlap that was actually a
  geometrically negative gap (the circles weren't touching at all) once
  the reviewer did the actual center-distance arithmetic instead of
  eyeballing the render. Every one of these was a genuine defect the
  automated `text_overlap_warnings`/`missing_visual_elements`/
  `low_contrast_warnings` checks structurally cannot see (they don't
  reason about "does this look like faked transparency"), so the
  vision-capable reviewer step is doing real, load-bearing work here,
  not a formality.
- The builder's own self-correction was real too: it stopped "nudging
  circles by eye" (which twice silently destroyed a required overlap)
  and switched to a numeric constraint search over candidate geometries
  once it recognized eyeballing was the actual root cause -- unprompted,
  not because the reviewer told it to compute rather than eyeball.
- Converged at review round ~13 to "Nothing to fix. The file meets the
  request" -- verified by rendering the final file myself (not trusting
  either model): three real alpha-blended irregular circles with
  genuinely darker, visibly blended intersections (not flat stacking),
  a coherent asymmetric magazine-cover composition, all three geometric
  checks clean. A real, legitimately good result.

**The honest cost finding**: ~13 build-review rounds is a lot of real
wall-clock time and real API calls for one slide -- this is the genuine
price of `run_node_script`'s open-ended power, not a defect in the loop
(a single-shot low-quality result would be strictly worse). Worth
knowing going in, not a reason to change anything: a simpler ask
resolves in far fewer rounds (this file's own §18 verification and
tier 2's §20 verification each converged in one render, zero review
rounds needed), and an ambitious one costing more iterations to get
*right* is the intended trade this path exists for.

**Conclusion: no code changes from this tier.** All three routing
boundaries the audit targeted work correctly today -- the coordinator
doesn't over-reach for `run_node_script` on cases `layout: svg`/
`add_pptx_shape` already cover, and doesn't under-reach for it on a case
that genuinely needs it. §19's own conditional ("only add new semantic
`layout:` ids if the audit shows write_pptx being over-used") isn't
triggered. Closing out the 3-tier plan here.

## 22. Real-hardware bugs from the first user round with the packaged
Electron app -- a `text_color` gap, and a shape/icon tool-selection miss

The user packaged the app (real Windows hardware, real PowerPoint, real
GLM-4.5-air as the coordinator model) and ran the §19-21 test plan. The
"no repair-dialog" check (§19's whole point) passed clean. Two real
defects came out of the rest, both root-caused from the actual transcript
rather than guessed at.

**22.1 "Add a green up-arrow shape" reached `add_pptx_icon`, not the new
`add_pptx_shape`.** The model called `add_pptx_icon(icon_name=
"trending-up", color="1E3A8A")` -- wrong tool (a raster Lucide pictogram,
not a vector shape) *and* wrong color (blue, not the requested green).
`coordinator.py` documented both tools but never said which one wins when
a request sounds like it could be either -- "trending-up" reads as a
plausible name for "an upward arrow" to a model that hasn't internalized
the real difference (a picture with no fill to edit later, vs. a real
`MSO_SHAPE` AutoShape). Fixed with an explicit, bolded rule in
`add_pptx_shape`'s own paragraph: a plain geometric shape request always
means `add_pptx_shape`, `add_pptx_icon` is reserved for an actual
recognizable pictogram, name collisions with an icon notwithstanding.

**22.2 The real, expensive failure this caused**: because the "arrow" was
a picture, `edit_pptx_shape`'s gradient parameters (tier 2, §20) had
nothing to attach to. Rather than surface that cleanly, the model spent
several rounds hand-rolling `run_node_script`/`run_python_script` fixes
using **hallucinated python-pptx API** (`fill.gradient_fill_properties.
stop_list[0].color.rgb`, `MSOGradientStyle.LINEAR` -- neither exists; the
real API is exactly what `edit_pptx_shape`'s own tier-2 implementation
already uses, `fill.gradient()`/`gradient_stops[i].color.rgb`/
`gradient_angle`), each attempt also hitting an unrelated real Windows
subprocess-encoding bug (`'charmap' codec can't encode` -- `run_python_
script`/`run_node_script` not forcing UTF-8 for a script containing
Chinese text; a real bug, but in a different subsystem, not fixed in this
pass) before giving up and telling the user to add the gradient by hand
in PowerPoint. §22.1's fix addresses the root cause -- reaching
`add_pptx_shape` in the first place means this entire detour never
starts.

**22.3 A separate, real capability gap, not just a routing miss: no way
to recolor existing text.** The user asked to change the theme's accent
color, expecting titles to turn blue; they didn't (real python-pptx
behavior: plain title/body text draws from `dk1`, not `accent1`, unless a
run explicitly references the theme color, which write_pptx-generated
text never does). The model's own explanation to the user was
confidently wrong ("这个颜色现在将作为...标题...的强调色"), then it tried the
same "guess an API, hand-roll a script" pattern as §22.2 to fake a fix
(literally inserting `<span style="color:#2563EB">...</span>` as plain
*text content* via `edit_pptx_text`, since that tool's markdown parser
has no color syntax -- visible in the real file as literal angle-bracket
text). The actual gap: `edit_pptx_shape` could recolor a shape's *fill*
but nothing recolored existing *text*.

**Fixed by adding `text_color` to `edit_pptx_shape`** (not a new tool --
a title/body placeholder is already addressable as a shape via
`list_pptx_shapes`/`shape_index`, so "recolor this shape's fill and/or
its text" belongs on the one tool that already owns "recolor this
shape"). Sets every run in the shape's text frame to one RGB color via
python-pptx's own `run.font.color.rgb` -- no hand-XML, so (like the
gradient fill in §20) needed no `_ooxml_validate` wiring. Also tightened
`edit_pptx_theme_colors`'s own coordinator.py guidance to state the real
behavior up front (plain title/body text is not theme-color-linked on
either a coscribe-generated or most real-world decks) instead of leaving
a model to assume otherwise and confidently tell a user something false,
and to explicitly name `text_color` as the actual tool for "make this
text a different color" plus a direct instruction against the
guess-the-API-and-hand-roll-a-script pattern both real failures shared.

**Verified real, not just unit-tested**: built a deck with `write_pptx`,
called `edit_pptx_shape(shape_index=<title>, text_color="2563EB")`,
rendered through the real LibreOffice pipeline, and visually confirmed
the title rendered in the requested blue while the bullet text (a
separate shape, untouched) stayed black -- the exact real-world scenario
the user hit. 4 new tests (recolors every run, leaves text content and
other shapes untouched, rejects a table/no-text-frame shape, rejects a
malformed hex), full `test_presentations_tool.py`/`test_pptx_templates_
tool.py`/`test_ooxml_validate.py` suite (291 tests) passes, `ruff check`/
`mypy` clean.

**Not fixed in this pass, noted for later**: the Windows `charmap`
subprocess-encoding bug (§22.2) -- real, but lives in `tools/scripts.py`/
`tools/node_scripts.py`, a different subsystem than this file covers.

## 23. §22.2's deferred bug, fixed: the Windows `charmap` encoding failure
in `run_python_script`/`run_node_script`

`'charmap' codec can't encode characters in position N-M: character maps
to <undefined>` is Python's generic name for a single-byte Windows locale
codec (commonly cp1252 on an English-locale install, cp936 for a Chinese
one -- either way, one with no slot for most CJK text) being asked to
encode text it can't represent. Unlike macOS/Linux, Windows has no UTF-8
default for either side of this: `Path.write_text()` and a child
process's own stdio both fall back to `locale.getpreferredencoding(False)`
unless told otherwise (Python's PEP 540 UTF-8 mode is opt-in via
`PYTHONUTF8`/`-X utf8`, not a Windows default the way it effectively is on
modern Linux). Two independent failure points existed in
`tools/scripts.py`'s `_run_python_script` (and the identical pattern in
`tools/node_scripts.py`'s `_run_node_script`), both unencoded:

1. `script_path.write_text(script)` -- writing the model's own script
   source to a temp file. Any Chinese comment or string literal in the
   script (routine for this codebase's Chinese-speaking users) fails
   right here, before the script ever runs.
2. `subprocess.run(..., text=True, ...)` with no `encoding=` -- capturing
   the child's stdout/stderr. For `run_python_script` specifically, a
   *second*, independent trigger: the child Python process's own
   `print()` of Chinese text hits the same non-UTF-8 console codepage
   inside the child itself, before the parent even gets to decode
   anything.

**Fix**: explicit `encoding="utf-8"` on both the `write_text()` call and
the `subprocess.run()` call (with `errors="replace"` on the latter so a
genuinely undecodable byte from a misbehaving script degrades to a
replacement character instead of raising and losing the rest of the
output). For `run_python_script` specifically, also force the *child*
Python's own stdio to UTF-8 via `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1` in
the subprocess's environment -- this is what actually fixes trigger #2
above, since the parent's `encoding=` only controls how the parent reads
the bytes the child already wrote, not what encoding the child chose when
writing them. `run_node_script` only needed the `write_text()` fix (a
comment) plus matching the parent's `subprocess.run(encoding="utf-8")` on
principle -- Node's own stdout is UTF-8 by default when piped to a
non-TTY (this subprocess call), regardless of Windows console codepage,
so there was no child-side environment variable to set.

**Verified**: 2 new regression tests (one per tool) that write a script
containing a Chinese comment and a Chinese `print`/`console.log` call,
asserting the exact string round-trips through stdout unmangled. This
sandbox's own locale is already UTF-8, so it can't reproduce the original
crash directly -- the tests instead confirm the fix's actual code path
(the explicit `encoding="utf-8"` everywhere) behaves correctly, which is
the strongest verification available without real Windows hardware.
`test_scripts_tool.py`/`test_node_scripts_tool.py` (17 tests) pass,
`ruff check`/`mypy` clean.

## 24. Auditing the rest of the codebase for §23's bug class -- one more
real duplicate found, several hardening fixes applied preventively

§23 fixed one instance of "no explicit UTF-8 on Windows" (the OS has no
UTF-8 default the way modern Linux does). Asked to check for other
instances of the same class, audited every `subprocess.run(...,
text=True, ...)`, `Path.write_text()`/`read_text()`, and
`asyncio.create_subprocess_exec()` call in `src/` for a missing
`encoding=`.

**A real duplicate, not just theoretical risk**: `tools/background_
tasks.py`'s `run_background_script` (the third EXEC tool -- fire-and-
forget scripts longer than `run_python_script`'s 600s cap) has its own,
separate `script_path.write_text(script)` with no encoding -- the exact
same bug, in a different tool that happens to duplicate the write step
rather than share it. Its Python branch also passed `env=None` (inherit
the parent's environment unmodified), so even after fixing the write, a
background Python script's own `print()` of Chinese text would still
crash inside the child -- same missing-`PYTHONIOENCODING`/`PYTHONUTF8`
gap as `run_python_script` had before §23. Fixed identically: `encoding=
"utf-8"` on the write, `PYTHONIOENCODING=utf-8`/`PYTHONUTF8=1` merged
into the child's env. (Its output-reading side was already correct --
`proc.stdout.read()` captures raw bytes into a binary log file, decoded
later with `.decode("utf-8", errors="replace")` -- so only the write side
and the child's own env needed fixing.)

**Preventive hardening, not confirmed bugs** (no live report, but the
identical unguarded pattern): every remaining `subprocess.run(...,
text=True, ...)` with no `encoding=` -- `tools/node_env.py` and
`tools/script_env.py`'s npm/pip install/list/uninstall/`--version` calls
(7 and 4 call sites respectively), and `runtime/hooks.py`'s `run_hook`
(shell-command hooks, which capture output as text and are fed a
JSON payload via `input=`, though `json.dumps`'s default `ensure_ascii=
True` already makes that specific input ASCII-safe regardless -- the
output-capture side was the real gap). All given the same `encoding=
"utf-8", errors="replace"` treatment as §23's fix, on the reasoning that
a Windows `%APPDATA%` path routinely embeds the OS username, which is
often Chinese on a Chinese-locale install -- pip/npm error output echoing
that path back is a plausible, if lower-probability, trigger for the same
crash. Also fixed: `tools/mcp.py`/`runtime/provider_config.py`/`runtime/
hooks.py`'s config-file `read_text()` calls (no encoding on the read side,
even though every writer of those same files elsewhere already passes
`encoding="utf-8"` -- a real read/write codec mismatch waiting for a
Chinese MCP server name or provider note), and `spreadsheets.py`'s
LibreOffice recalc-macro `write_text()` (the macro string itself is
static/ASCII-only today, zero live risk, fixed only for consistency in
case it's ever edited to include non-ASCII).

**Confirmed clean, no changes needed**: every `subprocess.run` call
converting office files via `soffice`/`pdftoppm` (spreadsheets.py,
_thumbnail.py, presentations.py's `_render_to_pdf`) uses
`capture_output=True` *without* `text=True` -- stdout/stderr stay raw
bytes, never implicitly decoded, so there's no codec to get wrong. Every
other `write_text`/`read_text` call in `src/` already passed
`encoding="utf-8"` explicitly (files.py, skills.py, memory.py,
selfwake.py, background_tasks.py's JSON records, pptx_templates.py,
web/app.py's dozen-plus sidecar/config writers, skill_authoring.py) --
this codebase's own convention was already correct almost everywhere;
`tools/scripts.py`/`tools/node_scripts.py` (§23) and `tools/background_
tasks.py` (this section) were the exceptions, both because they write a
*model-generated* script file, a pattern that didn't exist yet when the
"always pass encoding=" convention was established elsewhere.

**Verified**: 1 new regression test (`test_background_tasks.py`, the
same Chinese-comment-plus-print shape as §23's two tests, run through
`run_background_script` instead), full suite passes, `ruff check`/`mypy`
clean on every touched file.
