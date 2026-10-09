# One Interface font for the whole interface: Figtree, the system's, or OpenDyslexic (shipped)

Why: the "Chat font" setting changed only the conversation text (the composer, sidebar, header and
Settings stayed IBM Plex Sans), and its three choices looked the same to a reader of Chinese, whose
characters none of the bundled fonts has, so they all fell back to the same system font. The app is
English-only: no CJK font is bundled.

## Shipped
- Settings > General > **Interface font** replaces Chat font and sets the font of the whole
  interface (`--font-sans`, which `body` and Tailwind's `font-sans` use). Choices: Figtree (default),
  System, OpenDyslexic (an accessibility choice). Source Serif 4 and IBM Plex Sans are no longer
  bundled; IBM Plex Mono stays for code.
- Saved as `COSCRIBE_INTERFACE_FONT` (`default`, `system`, `dyslexic`) in `.env`, validated by the
  server's appearance whitelist. An old `COSCRIBE_CHAT_FONT` in `.env` is ignored, so the default applies.
- Figtree and OpenDyslexic come from `@fontsource` (SIL Open Font License), self-hosted like before.
  Anthropic's own typeface, which the maintainer likes, is proprietary and can't be redistributed
  (`README.md` already said so for its serif).
- Spec: `tests/e2e/settings.spec.ts` (picking OpenDyslexic changes the composer's and the body's font,
  not only the chat's).

## Not verified
- Figtree on Windows at small sizes, and OpenDyslexic's wide letter-spacing in the denser panels
  (checked in Chromium on Linux only).
