// coscribe film engine. Everything on screen is a pure function of time:
// film.seek(t) renders the exact frame for t seconds, so the browser player
// and the frame-by-frame video renderer show identical pictures.
(function () {
  const W = 1920, H = 1080;

  // ---------- math ----------
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, k) => a + (b - a) * k;
  // progress of t through [a, b], clamped to 0..1
  const prog = (t, a, b) => (b <= a ? (t >= b ? 1 : 0) : clamp((t - a) / (b - a)));
  const ease = {
    linear: (k) => k,
    inQuad: (k) => k * k,
    outQuad: (k) => 1 - (1 - k) * (1 - k),
    inOutQuad: (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2),
    outCubic: (k) => 1 - Math.pow(1 - k, 3),
    inCubic: (k) => k * k * k,
    inOutCubic: (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2),
    outQuart: (k) => 1 - Math.pow(1 - k, 4),
    inOutQuart: (k) => (k < 0.5 ? 8 * k ** 4 : 1 - Math.pow(-2 * k + 2, 4) / 2),
    outExpo: (k) => (k === 1 ? 1 : 1 - Math.pow(2, -10 * k)),
    inExpo: (k) => (k === 0 ? 0 : Math.pow(2, 10 * k - 10)),
    inOutExpo: (k) => k === 0 ? 0 : k === 1 ? 1 : k < 0.5 ? Math.pow(2, 20 * k - 10) / 2 : (2 - Math.pow(2, -20 * k + 10)) / 2,
    outBack: (k) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(k - 1, 3) + c1 * Math.pow(k - 1, 2); },
  };
  // eased progress: tw(t, a, b, 'outCubic')
  const tw = (t, a, b, e = 'inOutCubic') => ease[e](prog(t, a, b));
  // fade in over [a, a+fi], hold, fade out over [b-fo, b]
  const env = (t, a, b, fi = 0.4, fo = 0.4) => Math.min(tw(t, a, a + fi, 'outQuad'), 1 - tw(t, b - fo, b, 'inQuad'));

  // deterministic pseudo-random (never Math.random: frames must be reproducible)
  function rng(seed) {
    let s = (seed >>> 0) || 1;
    return () => { s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
  }
  const hash = (n) => { let x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); };

  // ---------- DOM ----------
  const SVGNS = 'http://www.w3.org/2000/svg';
  function h(tag, attrs = {}, ...kids) {
    const isSvg = /^(svg|g|path|rect|circle|ellipse|line|polyline|polygon|text|tspan|defs|clipPath|mask|linearGradient|radialGradient|stop|filter|fe[A-Z]\w*|use|pattern|foreignObject)$/.test(tag);
    const e = isSvg ? document.createElementNS(SVGNS, tag) : document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
      else if (k === 'text') e.textContent = v;
      else if (k === 'html') e.innerHTML = v;
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return e;
  }
  // set many style props at once, skipping unchanged values (cheap per-frame updates)
  function css(e, props) {
    for (const [k, v] of Object.entries(props)) {
      const s = typeof v === 'number' && !/opacity|zIndex|scale/.test(k) ? v + 'px' : String(v);
      if (e.style[k] !== s) e.style[k] = s;
    }
  }
  function attr(e, props) { for (const [k, v] of Object.entries(props)) e.setAttribute(k, v); }

  // typewriter: first n characters of str (by code point) for progress k
  const typed = (str, k) => { const a = Array.from(str); return a.slice(0, Math.round(a.length * clamp(k))).join(''); };

  // ---------- film / scenes ----------
  // A scene: { id, start, end, build(layer, ctx) -> update(lt, t) } where lt is
  // time since scene start. The layer is a 1920x1080 absolutely-positioned div,
  // shown only while start <= t < end; scenes may overlap for transitions and
  // stack by z (default: order of registration).
  const scenes = [];
  function scene(def) { scenes.push(def); }

  let stage, lang = 'zh', copy = {}, built = false;
  function T(key) { // localized copy lookup: content/<lang>.js sets window.FILM_COPY
    const v = (copy[key] ?? (window.FILM_COPY_FALLBACK || {})[key]);
    return v == null ? `[${key}]` : v;
  }

  function build(root, opts = {}) {
    lang = opts.lang || 'zh';
    copy = (window.FILM_COPY || {})[lang] || {};
    window.FILM_COPY_FALLBACK = (window.FILM_COPY || {}).zh || {};
    stage = root;
    stage.dataset.lang = lang;
    scenes.forEach((s, i) => {
      s.layer = h('div', { class: 'layer', 'data-scene': s.id });
      css(s.layer, { zIndex: s.z ?? i });
      stage.append(s.layer);
      s.update = s.build(s.layer, { W, H, lang, T }) || (() => {});
    });
    built = true;
  }

  function seek(t) {
    for (const s of scenes) {
      const on = t >= s.start && t < s.end;
      if (on !== s._on) { s.layer.style.display = on ? '' : 'none'; s._on = on; }
      if (on) s.update(t - s.start, t);
    }
  }

  const duration = () => Math.max(0, ...scenes.map((s) => s.end));

  window.F = { W, H, clamp, lerp, prog, ease, tw, env, rng, hash, h, css, attr, typed, scene, scenes, T, build, seek, duration,
    get lang() { return lang; } };
})();
