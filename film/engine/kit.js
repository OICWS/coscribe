// Shared visual vocabulary for every scene. Scenes should build from these so
// the whole film reads as one piece: same input box, same blue thread, same
// cards, same camera feel. All functions return elements plus an update(...)
// that is a pure function of the values passed in.
(function () {
  const { h, css, attr, clamp, lerp, tw, ease, prog, typed, hash } = F;

  // ---------- camera ----------
  // World container whose transform is driven per frame. x/y are the world
  // point placed at screen centre; s is zoom; r is rotation (deg); rx/ry tilt
  // for a 2.5D feel (keep tilts small, < 12deg).
  function camera(parent, { perspective = 2400 } = {}) {
    const view = h('div', { class: 'abs' });
    css(view, { left: 0, top: 0, width: 1920, height: 1080, perspective: perspective + 'px', perspectiveOrigin: '960px 540px' });
    const world = h('div', { class: 'abs' });
    css(world, { left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '0 0', transformStyle: 'preserve-3d' });
    view.append(world); parent.append(view);
    function set({ x = 960, y = 540, s = 1, r = 0, rx = 0, ry = 0 } = {}) {
      world.style.transform = `translate(960px,540px) rotateX(${rx}deg) rotateY(${ry}deg) rotate(${r}deg) scale(${s}) translate(${-x}px,${-y}px)`;
    }
    set();
    return { view, world, set };
  }
  // interpolate between camera keyframes [{t, x, y, s, r, rx, ry, e}] (e = ease into this key)
  function camPath(keys, t) {
    if (t <= keys[0].t) return keys[0];
    for (let i = 1; i < keys.length; i++) {
      const a = keys[i - 1], b = keys[i];
      if (t <= b.t) {
        const k = ease[b.e || 'inOutCubic'](prog(t, a.t, b.t));
        const o = {};
        for (const p of ['x', 'y', 's', 'r', 'rx', 'ry']) {
          const va = a[p] ?? (p === 's' ? 1 : p === 'x' ? 960 : p === 'y' ? 540 : 0);
          const vb = b[p] ?? va;
          // zoom interpolates in log space so dives feel constant-speed
          o[p] = p === 's' ? Math.exp(lerp(Math.log(va), Math.log(vb), k)) : lerp(va, vb, k);
        }
        return o;
      }
    }
    return keys[keys.length - 1];
  }

  // ---------- backdrops ----------
  // The SURFACE: the app's light canvas.
  function surface(parent) {
    const e = h('div', { class: 'abs' });
    css(e, { inset: 0, background: 'var(--bg)' });
    parent.append(e); return e;
  }
  // The DEPTH: warm near-black with a faint vignette. Scenes fill it with work.
  function depth(parent) {
    const e = h('div', { class: 'abs' });
    css(e, { inset: 0, background: 'radial-gradient(120% 90% at 50% 45%, #1c1a19 0%, #141312 55%, #0d0c0c 100%)' });
    parent.append(e); return e;
  }
  // film grain overlay, seeded by frame so it is deterministic
  function grain(parent, amount = 0.05) {
    const e = h('div', { class: 'abs' });
    css(e, { inset: 0, pointerEvents: 'none', opacity: amount, mixBlendMode: 'overlay',
      backgroundImage: "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='256' height='256'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='256' height='256' filter='url(%23n)'/></svg>\")" });
    parent.append(e);
    return (t) => { const f = Math.floor(t * 24); css(e, { backgroundPosition: `${Math.floor(hash(f) * 256)}px ${Math.floor(hash(f + 7) * 256)}px` }); };
  }

  // ---------- the protagonist: the coscribe input box ----------
  // Measured from the real app (Composer.tsx, empty state, 2026-10-06):
  // 848x54 card, rounded-2xl (16px), p-3, 1px --border, --bg fill, shadow-sm;
  // 14.4px/21.6px IBM Plex Sans text; 28px return button inside on the right;
  // below the card: "+  Add folder" on the left, "deepseek-chat ⌄" on the
  // right, both 14px muted. Attachment chips: rounded-full, --card-bg, text-xs,
  // name + ×. Optional serif greeting above (Georgia 40px, as in EmptyState).
  // Everything is drawn at Z x the real size so it reads at video resolution.
  const Z = 1.45;
  function inputBox(parent, { x = 960, y = 540, placeholder = '', model = 'deepseek-chat', greeting = null } = {}) {
    const W = 848 * Z;
    const root = h('div', { class: 'abs' });
    css(root, { left: x - W / 2, top: y - 27 * Z, width: W, transformOrigin: '50% 50%' });
    const greet = h('div', { text: greeting || '' });
    css(greet, { position: 'absolute', left: 0, right: 0, bottom: '100%', marginBottom: 38 * Z, textAlign: 'center',
      fontFamily: 'Georgia, "Times New Roman", var(--serif)', fontSize: 40 * Z, lineHeight: 1.25, color: 'var(--fg)', display: greeting ? '' : 'none' });
    const card = h('div');
    css(card, { position: 'relative', borderRadius: 16 * Z, padding: 12 * Z, background: 'var(--bg)', border: '1px solid var(--border)',
      boxShadow: `0 ${1 * Z}px ${3 * Z}px rgba(0,0,0,.1), 0 ${1 * Z}px ${2 * Z}px rgba(0,0,0,.06)`, color: 'var(--fg)' });
    const chips = h('div'); css(chips, { display: 'flex', gap: 8 * Z, flexWrap: 'wrap' });
    const text = h('div');
    css(text, { fontSize: 14.4 * Z, lineHeight: 1.5, minHeight: 21.6 * Z, whiteSpace: 'pre-wrap', fontFamily: 'var(--sans)', padding: `0 ${36 * Z}px 0 ${2 * Z}px` });
    const words = h('span'), caret = h('span');
    css(caret, { display: 'inline-block', width: Math.max(2, 1.4 * Z), height: 18 * Z, marginLeft: 1, verticalAlign: -3 * Z, background: 'var(--fg)' });
    const ph = h('span', { text: placeholder });
    css(ph, { color: 'var(--muted)' });
    text.append(words, caret, ph);
    const send = h('span', { html: `<svg width="${15 * Z}" height="${15 * Z}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 10l-5 5 5 5"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>` });
    css(send, { position: 'absolute', right: 12 * Z, bottom: 12 * Z, width: 28 * Z, height: 28 * Z, borderRadius: 8 * Z, display: 'grid', placeItems: 'center', color: 'var(--muted)' });
    card.append(chips, text, send);
    const bar = h('div');
    css(bar, { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 * Z, fontSize: 14 * Z, color: 'var(--muted)', padding: `0 ${2 * Z}px` });
    const left = h('div'); css(left, { display: 'flex', alignItems: 'center', gap: 4 * Z });
    const plus = h('span', { html: `<svg width="${18 * Z}" height="${18 * Z}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>` });
    css(plus, { width: 28 * Z, height: 28 * Z, display: 'grid', placeItems: 'center' });
    left.append(plus, h('span', { text: 'Add folder' }));
    const right = h('div'); css(right, { display: 'flex', gap: 14 * Z, alignItems: 'center' });
    const timer = h('span'); css(timer, { fontFamily: 'var(--sans)', fontVariantNumeric: 'tabular-nums' });
    const modelChip = h('span', { html: `${model}<svg width="${12 * Z}" height="${12 * Z}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" style="margin-left:${6 * Z}px;vertical-align:middle"><path d="M6 9l6 6 6-6"/></svg>` });
    css(modelChip, { paddingRight: 8 * Z });
    right.append(timer, modelChip);
    bar.append(left, right);
    root.append(greet, card, bar); parent.append(root);
    return {
      root, card, text, chips, modelChip, greet, bar,
      // str typed up to progress k (0..1); t drives the caret blink; sent=0..1 animates the send key
      // elapsed: seconds since send (null hides the run timer + token count)
      update({ str = '', k = 1, t = 0, caretOn = true, sent = 0, elapsed = null }) {
        const tm = elapsed == null ? '' : `${Math.floor(elapsed / 60)}:${String(Math.floor(elapsed % 60)).padStart(2, '0')} · ${(elapsed * 0.9 + 0.4).toFixed(1)}k tokens`;
        if (timer.textContent !== tm) timer.textContent = tm;
        const s = typed(str, k);
        if (words.textContent !== s) words.textContent = s;
        ph.style.display = s ? 'none' : '';
        const typing = k > 0 && k < 1;
        caret.style.opacity = !caretOn ? 0 : typing ? 1 : (Math.floor(t * 1.8) % 2 ? 0 : 1);
        css(send, { background: sent > 0 ? 'var(--fg)' : 'transparent', color: sent > 0 ? 'var(--bg)' : (s ? 'var(--fg)' : 'var(--muted)'), transform: `scale(${1 - 0.12 * Math.sin(Math.PI * clamp(sent))})` });
      },
      addChip(label, kind = 'sheet') { const c = fileChip(label, kind); chips.append(c); css(chips, { marginBottom: 8 * Z }); return c; },
    };
  }

  // ---------- file chip (attachment / deliverable) ----------
  // As in Composer.tsx: rounded-full, --border, --card-bg, text-xs, name then ×.
  // kind is kept so scenes can colour things that belong to a file type.
  const KIND = { sheet: ['var(--file-sheet)', 'XLSX'], doc: ['var(--file-doc)', 'DOCX'], slides: ['var(--file-slides)', 'PPTX'], pdf: ['var(--file-pdf)', 'PDF'], code: ['var(--kind-script)', 'PY'], folder: ['var(--kind-tool)', 'DIR'] };
  function fileChip(label, kind = 'sheet', { dark = false, z = Z } = {}) {
    const c = h('span');
    c.dataset.kind = kind;
    css(c, { display: 'inline-flex', alignItems: 'center', gap: 8 * z, padding: `${6 * z}px ${6 * z}px ${6 * z}px ${12 * z}px`, borderRadius: 999,
      border: `1px solid ${dark ? 'var(--d-border)' : 'var(--border)'}`, background: dark ? 'var(--d-card)' : 'var(--card-bg)', fontSize: 12 * z, lineHeight: 1.33, color: dark ? 'var(--d-fg)' : 'var(--fg)', whiteSpace: 'nowrap' });
    const x = h('span', { text: '×' });
    css(x, { width: 16 * z, height: 16 * z, display: 'grid', placeItems: 'center', color: 'var(--muted)', fontSize: 12 * z });
    c.append(h('span', { text: label }), x);
    return c;
  }

  // ---------- generic UI card (task list, reviewer finding, approval...) ----------
  function card(parent, { x, y, w, dark = false, pad = 22 } = {}) {
    const e = h('div', { class: 'abs' });
    css(e, { left: x, top: y, width: w, padding: pad, borderRadius: 14, fontSize: 17, lineHeight: 1.45,
      background: dark ? 'var(--d-bg)' : 'var(--bg)', color: dark ? 'var(--d-fg)' : 'var(--fg)',
      border: `1px solid ${dark ? 'var(--d-border)' : 'var(--border)'}`,
      boxShadow: dark ? '0 30px 80px rgba(0,0,0,.45)' : '0 20px 60px rgba(32,30,29,.10)' });
    parent.append(e); return e;
  }
  // reveal a card: rise + fade (k 0..1)
  function rise(e, k, dy = 24) { css(e, { opacity: ease.outQuad(clamp(k)), transform: `translateY(${(1 - ease.outCubic(clamp(k))) * dy}px)` }); }

  // checklist, like TaskPanel: items go pending -> running (spinner) -> done (check)
  function checklist(parent, items, { dark = false } = {}) {
    const rows = items.map((label) => {
      const r = h('div'); css(r, { display: 'flex', gap: 12, alignItems: 'center', padding: '7px 0' });
      const icon = h('span'); css(icon, { width: 20, height: 20, flex: 'none', display: 'grid', placeItems: 'center' });
      const tx = h('span', { text: label });
      r.append(icon, tx); parent.append(r); return { r, icon, tx, state: '' };
    });
    const muted = dark ? 'var(--d-muted)' : 'var(--muted)', fg = dark ? 'var(--d-fg)' : 'var(--fg)', ok = dark ? 'var(--d-success)' : 'var(--success)', acc = dark ? 'var(--d-accent)' : 'var(--accent)';
    return {
      rows,
      // state per row: 0 pending, (0,1) running, >=1 done; t for spinner rotation
      update(states, t = 0) {
        rows.forEach((row, i) => {
          const s = states[i] ?? 0;
          const st = s >= 1 ? 'done' : s > 0 ? 'run' : 'todo';
          if (st !== row.state) {
            row.state = st;
            row.icon.innerHTML = st === 'done'
              ? `<svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="9" fill="${ok}"/><path d="M6 10.4l2.6 2.6L14 7.6" stroke="#fff" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`
              : st === 'run'
                ? `<svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" stroke="${muted}" stroke-opacity=".3" stroke-width="2" fill="none"/><path d="M10 2a8 8 0 0 1 8 8" stroke="${acc}" stroke-width="2" fill="none" stroke-linecap="round"/></svg>`
                : `<svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" stroke="${muted}" stroke-opacity=".6" stroke-width="1.6" fill="none"/></svg>`;
            row.tx.style.color = st === 'todo' ? muted : fg;
          }
          if (st === 'run') row.icon.firstChild.style.transform = `rotate(${(t * 360) % 360}deg)`;
        });
      },
    };
  }

  // ---------- the blue thread: the agent's work as a drawn line ----------
  // d: SVG path data in layer coordinates. update(k) draws it 0..1 with a bright head.
  function thread(svg, d, { color = 'var(--d-accent)', width = 2.5, glow = true } = {}) {
    const g = h('g');
    const halo = h('path', { d, fill: 'none', stroke: color, 'stroke-width': width * 5, 'stroke-linecap': 'round', opacity: glow ? 0.12 : 0 });
    const p = h('path', { d, fill: 'none', stroke: color, 'stroke-width': width, 'stroke-linecap': 'round' });
    const head = h('circle', { r: width * 2.2, fill: '#fff' });
    g.append(halo, p, head); svg.append(g);
    const L = p.getTotalLength ? p.getTotalLength() : 1000;
    for (const e of [halo, p]) attr(e, { 'stroke-dasharray': L, 'stroke-dashoffset': L });
    return {
      g, length: L,
      update(k, { headOn = true } = {}) {
        k = clamp(k);
        for (const e of [halo, p]) e.setAttribute('stroke-dashoffset', L * (1 - k));
        const pt = p.getPointAtLength(L * k);
        attr(head, { cx: pt.x, cy: pt.y, opacity: headOn && k > 0 && k < 1 ? 1 : 0 });
      },
    };
  }

  // ---------- typography ----------
  // A narrative line (on-screen copy): serif, centred, per-character fade with
  // a slight blur, k 0..1 in, out 0..1 out.
  function line(parent, { x = 960, y = 540, size = 54, color = 'var(--fg)', serif = true, weight = 400, maxW = 1500, align = 'center' } = {}) {
    const e = h('div', { class: 'abs' });
    css(e, { left: x - maxW / 2, top: y, width: maxW, textAlign: align, fontFamily: serif ? 'var(--serif)' : 'var(--sans)', fontSize: size, fontWeight: weight, color, lineHeight: 1.35, transform: 'translateY(-50%)', letterSpacing: '0.02em' });
    parent.append(e);
    let cur = null, spans = [];
    return {
      e,
      update(str, kin = 1, kout = 0) {
        if (str !== cur) { cur = str; e.textContent = ''; spans = Array.from(str).map((ch) => { const s = h('span', { text: ch }); css(s, { display: 'inline-block', whiteSpace: 'pre' }); e.append(s); return s; }); }
        const n = spans.length;
        spans.forEach((s, i) => {
          const ki = clamp((kin * (n + 8) - i) / 8);
          const o = ease.outQuad(ki) * (1 - ease.inQuad(clamp(kout)));
          css(s, { opacity: o, filter: o < 0.99 ? `blur(${(1 - o) * 6}px)` : 'none', transform: `translateY(${(1 - ease.outCubic(ki)) * 10}px)` });
        });
      },
    };
  }

  // monospace number that rolls/counts (for figures)
  const fmt = (v, d = 0) => v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

  window.K = { Z, camera, camPath, surface, depth, grain, inputBox, fileChip, card, rise, checklist, thread, line, fmt, KIND };
})();

// ---------- typing schedule ----------
// Human-ish keystroke times for str typed from t0 over dur seconds: jittered
// intervals, a short thinking pause after the first comma. Returns an array
// of key times (one per character) and registers 'key' sound events.
(function () {
  function typing(str, t0, dur, { seed = 1, sound = true } = {}) {
    const chars = Array.from(str), r = F.rng(seed);
    const gaps = chars.map((c, i) => {
      let g = 0.75 + r() * 0.5;
      if (/[，,、]/.test(chars[i - 1] || '')) g += 2.2;   // pause after a comma
      if (c === ' ') g *= 0.6;
      return g;
    });
    const sum = gaps.reduce((a, b) => a + b, 0);
    let acc = 0;
    const times = gaps.map((g) => { acc += g; return t0 + (acc / sum) * dur; });
    if (sound) times.forEach((t, i) => F.event(t, 'key', { ch: chars[i] }));
    return times;
  }
  // number of characters visible at time t
  const typedCount = (times, t) => { let n = 0; while (n < times.length && times[n] <= t) n++; return n; };
  K.typing = typing;
  K.typedCount = typedCount;
})();
