// VO subtitles, from content/vo.js. Serif, centred near the bottom; ink on the
// light surface, paper-white on the dark depth.
F.scene({ id: 'subtitles', start: 0, end: 1e9, z: 200, build(layer) {
  const lines = window.FILM_VO.lines.filter((l) => l.sub !== false);
  const el = K.line(layer, { y: 900, size: 40, serif: true });
  F.css(layer, { pointerEvents: 'none' });
  return (lt, t) => {
    const l = lines.find((x) => t >= x.in && t < x.out);
    if (!l) { el.e.style.display = 'none'; return; }
    el.e.style.display = '';
    F.css(el.e, { color: l.bg === 'dark' ? '#fff' : 'var(--fg)', textShadow: l.bg === 'dark' ? '0 1px 2px rgba(20,24,60,.25), 0 4px 18px rgba(20,24,60,.22)' : 'none' });
    el.update(l[F.lang] || l.zh, F.prog(t, l.in, l.in + 0.9), F.prog(t, l.out - 0.45, l.out));
  };
} });
