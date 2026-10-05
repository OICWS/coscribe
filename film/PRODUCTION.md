# coscribe film: production notes

A ~120 s promo film ("一句话的背后"), made entirely in code. One HTML timeline
renders both the web version (`index.html`) and the video (`render/render.py`).
The script and storyboard is `script/剧本与分镜.md`; it is the source of truth for
timing, on-screen text, example data, VO and music cues.

## How it works
- `engine/core.js` (`F`): every scene is a pure function of time. `F.scene({id, start, end, z, build(layer, ctx) -> update(lt, t)})`.
  `lt` = seconds since scene start, `t` = film time. Helpers: `tw(t,a,b,ease)`, `prog`, `env`, `lerp`, `clamp`, `ease.*`,
  `rng(seed)` / `hash(n)` (never `Math.random`), `h(tag, attrs, ...kids)` (HTML or SVG), `css(el, props)`, `L(zh, en)`, `T(key)`,
  `F.event(t, name, data)` to register a sound-sync event (the audio build reads `window.FILM_EVENTS`).
- `engine/kit.js` (`K`): the shared visual vocabulary. `camera` + `camPath` (2.5D camera), `surface`, `depth`, `grain`,
  `inputBox` (the protagonist), `fileChip`, `card` + `rise`, `checklist` (TaskPanel style), `thread` (the blue agent line),
  `line` (per-character serif text reveal), `typing`/`typedCount`, `fmt`, `CHIP_SLOT`.
- `engine/film.css`: the app's own design tokens (light surface + dark depth).
- `scenes/surfaces.js`: the four typed sentences on the light surface and the iris dive into each depth (z 100).
- `scenes/subtitles.js`: VO subtitles from `content/vo.js` (z 200). Scenes do NOT draw VO lines themselves.
- `scenes/s1_excel.js` … `s7_close.js`: the depth worlds, the montage, the workflow climax and the close.
- `content/copy.js`, `content/vo.js`: shared copy and the VO timeline (zh / en).
- `fonts/build_fonts.py`: copies only the font chunks the film uses (`--all` = every chunk, for development only).
- `audio/`: score, SFX and VO are synthesised in Python and mixed to `audio/out/mix_<lang>.m4a`.
- `render/render.py`: headless Chromium, frame by frame, piped to ffmpeg. `--stills`, `--contact N --start a --end b` for review.

## Rules for scene code
- Deterministic: no `Math.random`, `Date`, CSS transitions/animations, or timers. Everything derives from `t`.
- Fast: a frame must update in well under 100 ms. Reuse elements; only change styles per frame. Use one `<canvas>` for very dense
  layers (thousands of glyphs or cells); draw text there with the loaded families ("IBM Plex Mono", "Noto Sans SC", …).
- Look: restrained and authentic. Only the app's tokens and file-type colours. No gradient blobs, glows used as decoration,
  icon-card grids, or emoji. Real UI fragments must match the app's real components (office-agent/frontend/src/components).
  Motion is eased and layered (parallax, focus shifts). Nothing moves linearly unless it is a mechanical thing like scrolling text.
- Bilingual: all visible text via `F.L(zh, en)` or `T(key)`. Check both languages render and fit.
- Mark example data subtly: a small "示例 / Example" tag in a corner of each depth world.

## Run
    python3 fonts/build_fonts.py --all                     # dev fonts (any glyph); run without --all before committing
    python3 render/render.py --lang zh --contact 0.5 --start 11 --end 30
    python3 render/render.py --lang zh --stills 14 22.5
    python3 render/render.py --lang zh                     # full film -> out/film_zh.mp4
    python3 -m http.server -d film 8000                    # then open http://localhost:8000/?lang=zh
