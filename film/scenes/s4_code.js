// Sentence 4 · code (77.4–94.0). S4-02..S4-07.
// The dive lands in the code depth (#232120): ten lines of Python grow along
// the blue thread, four edge-case rows light up and settle as the code that
// handles them is written, pytest goes red then green, the approval card asks
// before the script runs, the run surfaces three anomalies, and the table
// shrinks into the fourth chip on the light surface.
(function () {
  const { h, css, clamp, lerp, tw, ease, prog, T, L } = F;
  const S0 = 77.4, S1 = 94.0;

  // ---------- palette (app dark tokens) ----------
  const COL = {
    fg: '#f7f5f3', text: '#e4e0dd', muted: '#a19d9b', kw: '#8cb4f0', fn: '#a9cbf5', str: '#7fcf98', num: '#f0a076',
    com: 'rgba(161,157,155,.85)', accent: '#4b8fe3', ok: '#8fd19f', bad: '#f09a9a', warn: '#e8b85c',
  };
  const MONO = '"IBM Plex Mono", "Noto Sans SC", ui-monospace, monospace';
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // ---------- a tiny Python highlighter (app tokens only) ----------
  const KW = new Set(['def', 'return', 'for', 'in', 'import', 'from', 'as', 'True', 'False', 'None', 'and', 'or', 'not', 'lambda', 'if', 'else', 'with', 'print']);
  function tokens(s) {
    const out = [];
    const re = /(#.*$)|(f?"(?:[^"\\]|\\.)*"?)|(\b\d[\d_]*(?:\.\d+)?\b)|([A-Za-z_]\w*)|(\s+)|([^\sA-Za-z_\d#"]+)/g;
    let m, prevWord = '';
    while ((m = re.exec(s))) {
      let cls = 'p';
      if (m[1]) cls = 'com';
      else if (m[2]) cls = 'str';
      else if (m[3]) cls = 'num';
      else if (m[4]) {
        if (KW.has(m[4])) cls = 'kw';
        else if (prevWord === 'def') cls = 'fn';
        else cls = 'id';
        prevWord = m[4];
      }
      out.push({ a: m.index, b: m.index + m[0].length, cls });
    }
    return out;
  }
  const TOKSTYLE = {
    p: `color:${COL.text};opacity:.72`, id: `color:${COL.text}`, kw: `color:${COL.kw}`, fn: `color:${COL.fn};font-weight:500`,
    str: `color:${COL.str}`, num: `color:${COL.num}`, com: `color:${COL.com}`,
  };
  // HTML for a code line; mark = [a,b] diff range; caret = index or null
  function hl(s, mark, caret, ma = 0.2) {
    const cuts = new Set([0, s.length]);
    const toks = tokens(s);
    toks.forEach((tk) => { cuts.add(tk.a); cuts.add(tk.b); });
    if (mark) { cuts.add(mark[0]); cuts.add(mark[1]); }
    if (caret != null) cuts.add(caret);
    const pts = [...cuts].filter((x) => x >= 0 && x <= s.length).sort((a, b) => a - b);
    let html = '';
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i];
      if (mark && ma > 0 && a === mark[1] && a > mark[0]) html += '</span>';
      if (caret === a) html += '<span class="s4caret"></span>';
      if (mark && ma > 0 && a === mark[0] && mark[1] > a) html += `<span style="background:rgba(143,209,159,${ma});border-radius:3px;box-shadow:0 0 0 2px rgba(143,209,159,${ma})">`;
      const b = pts[i + 1];
      if (b == null || b <= a) continue;
      const tk = toks.find((x) => x.a <= a && x.b > a);
      const st = tk ? TOKSTYLE[tk.cls] : TOKSTYLE.p;
      html += `<span style="${st}">${esc(s.slice(a, b))}</span>`;
    }
    if (mark && ma > 0 && mark[1] === s.length && mark[1] > mark[0]) html += '</span>';
    return html;
  }

  // ---------- the code (script §4.4) ----------
  const COMMENT_COL = 72;
  const pad = (code, n = COMMENT_COL) => code + ' '.repeat(Math.max(2, n - code.length));
  const C4 = L('# 2026/10/3 与 2026-10-03 对齐', '# align 2026/10/3 with 2026-10-03');
  const C5 = L('# 空值不当作 0', '# a blank is not a zero');
  const C6 = L('# 重复上传只算一次', '# a re-upload counts once');
  const L4_0 = '    merged["date"] = pd.to_datetime(merged["date"])';
  const L6_0 = '    return merged.drop_duplicates()';
  const INS4 = ', errors="coerce"', INS6 = '["order_id", "source"]';
  const typeOp = (t0, t1, text) => ({ k: 'type', t0, t1, text });
  const LINES = [
    { ops: [typeOp(78.66, 79.00, 'def merge_daily(erp: pd.DataFrame, pos: pd.DataFrame, shop: pd.DataFrame) -> pd.DataFrame:')] },
    { ops: [typeOp(79.00, 79.34, `    frames = [normalize(df, src) for df, src in ((erp, "ERP"), (pos, "POS"), (shop, "${L('电商', 'Webshop')}"))]`)] },
    // one real typo, caught and fixed with backspace
    { ops: [typeOp(79.34, 79.66, '    merged = pd.concat(frames, ignore_index=Ture'), { k: 'del', t0: 79.74, t1: 79.82, n: 3 }, typeOp(79.82, 79.88, 'rue)')] },
    { ops: [typeOp(79.88, 80.16, pad(L4_0) + C4), { k: 'ins', t0: 82.95, t1: 83.10, at: L4_0.length - 1, eatAt: L4_0.length, text: INS4 }] },
    { ops: [typeOp(80.16, 80.44, pad('    merged = merged.dropna(subset=["date", "amount"])') + C5)] },
    { ops: [typeOp(80.44, 80.72, pad(L6_0) + C6), { k: 'ins', t0: 83.22, t1: 83.38, at: L6_0.length - 1, eatAt: L6_0.length, text: INS6 }] },
    { ops: [] },
    { ops: [typeOp(80.84, 81.16, 'def flag_anomalies(daily: pd.DataFrame, window: int = 28, k: float = 2.5) -> pd.DataFrame:')] },
    { ops: [typeOp(81.16, 81.46, '    by_store = daily.groupby(["store", "date"])["amount"].sum().unstack("store")')] },
    { ops: [typeOp(81.46, 81.70, '    baseline = by_store.rolling(window, min_periods=7).median()')] },
    { ops: [typeOp(81.70, 82.06, '    return by_store[(by_store / baseline > k) | (by_store < 0)].stack().rename("anomaly").reset_index()')] },
  ];
  // visible text of a line at time t (+ diff mark and caret position)
  function lineState(line, t) {
    let s = '', mark = null, caret = null, active = false;
    for (const op of line.ops) {
      if (t < op.t0) break;
      const k = prog(t, op.t0, op.t1);
      if (op.k === 'type') { const n = Math.round(op.text.length * k); s += op.text.slice(0, n); caret = s.length; active = k < 1; }
      else if (op.k === 'del') { const n = Math.round(op.n * k); s = s.slice(0, s.length - n); caret = s.length; active = true; }
      else if (op.k === 'ins') {
        const n = Math.round(op.text.length * k);
        s = s.slice(0, op.at) + op.text.slice(0, n) + s.slice(op.at, op.eatAt) + s.slice(op.eatAt + n);
        mark = n ? [op.at, op.at + n] : null; caret = op.at + n; active = k < 1;
      }
    }
    return { s, mark, caret, active };
  }
  const ALLOPS = [];
  LINES.forEach((ln, i) => ln.ops.forEach((o) => ALLOPS.push(Object.assign(o, { line: i }))));
  ALLOPS.sort((a, b) => a.t0 - b.t0);
  const lineStart = (i) => (LINES[i].ops[0] ? LINES[i].ops[0].t0 : null);
  const lineEnd = (i) => { const o = LINES[i].ops.filter((x) => x.k !== 'ins'); return o.length ? o[o.length - 1].t1 : null; };
  // time at which the first-pass text of line i reaches substring sub
  function reachAt(i, sub) { const op = LINES[i].ops[0]; const idx = op.text.indexOf(sub); return op.t0 + ((idx + sub.length) / op.text.length) * (op.t1 - op.t0); }

  // ---------- edge cases (S4-03 ghost rows) ----------
  const GHOSTS = [
    { label: L('空值', 'nulls'), rows: [['POS', '2026-10-03', 'SZ-NS-014', null]], lit: lineEnd(4), test: 'fail' },
    { label: L('重复行', 'duplicate rows'), rows: [['ERP', '10293', '2026-10-03', '1,280.00'], ['ERP', '10293', '2026-10-03', '1,280.00']], lit: lineEnd(5), test: 'fail' },
    { label: L('日期格式不一', 'mixed date formats'), rows: [['2026/10/3', 'vs', '2026-10-03']], lit: lineEnd(3), test: 'fail' },
    { label: L('负数退款', 'negative refunds'), rows: [['EC-20261003-00871', '−2,980.00']], lit: reachAt(10, '(by_store < 0)'), test: 'pass' },
  ];

  // ---------- pytest (S4-04) ----------
  const COLS = 112;
  const bar = (title, ch = '=') => { const n = COLS - title.length - 2; return ch.repeat(Math.floor(n / 2)) + ' ' + title + ' ' + ch.repeat(Math.ceil(n / 2)); };
  const trunc = (s) => (s.length > COLS ? s.slice(0, COLS - 3) + '...' : s);
  const R = COL.bad, G = COL.ok, M = COL.muted, X = COL.text;
  // each line: [segments [text, color]], print time
  const TERM = [];
  const tl = (at, ...segs) => TERM.push({ at, segs });
  tl(82.20, ['$ ', M], ['pytest -q', X]);
  tl(82.36, ['..FFF', null], [' '.repeat(COLS - 11) + '[100%]', R]);   // dots line is filled progressively
  const fails = [
    ['test_merge_drops_duplicate_orders', ['    def test_merge_drops_duplicate_orders():', '        erp, pos, shop = load_fixture("dup_upload")', '>       assert len(merge_daily(erp, pos, shop)) == 4'], 'E       assert 5 == 4', 'test_daily_check.py:31: AssertionError', 'assert 5 == 4'],
    ['test_dates_in_two_formats_align', ['    def test_dates_in_two_formats_align():', '>       merged = merge_daily(*load_fixture("mixed_dates"))'], 'E   ValueError: time data "2026/10/3" doesn\'t match format "%Y-%m-%d", at position 1. You might want to try:', 'test_daily_check.py:38: ValueError', 'ValueError: time data "2026/10/3" doesn\'t match format "%Y-%m-%d", at position 1. You might want to try:'],
    ['test_empty_amount_is_dropped_not_zero', ['    def test_empty_amount_is_dropped_not_zero():', '>       merged = merge_daily(*load_fixture("blank_amount"))'], 'E   ValueError: time data "n/a" doesn\'t match format "%Y-%m-%d", at position 2. You might want to try:', 'test_daily_check.py:45: ValueError', 'ValueError: time data "n/a" doesn\'t match format "%Y-%m-%d", at position 2. You might want to try:'],
  ];
  {
    let at = 82.60;
    const step = 0.0045;
    tl(at, [bar('FAILURES'), R]);
    for (const [name, src, err, loc] of fails) {
      tl(at += step, [bar(name, '_'), R]);
      tl(at += step, ['', X]);
      for (const s of src) tl(at += step, [s, s.startsWith('>') ? X : M]);
      tl(at += step, [trunc(err), R]);
      tl(at += step, ['', X]);
      tl(at += step, [loc, M]);
    }
    tl(at += step, [bar('short test summary info'), X]);
    for (const [name, , , , short] of fails) tl(at += 0.012, ['FAILED', R], [trunc(` test_daily_check.py::${name} - ${short}`), X]);
    tl(82.79, ['3 failed', R], [', ', R], ['2 passed', G], [' in 0.38s', R]);
    tl(83.62, ['$ ', M], ['pytest -q', X]);
    tl(83.72, ['.....', null], [' '.repeat(COLS - 11) + '[100%]', G]);
    tl(84.00, ['5 passed in 0.41s', G]);
  }
  const RUN1_DOTS = [82.36, 82.43, 82.50, 82.535, 82.57], RUN2_DOTS = [83.72, 83.79, 83.86, 83.92, 83.97];

  // ---------- sound-sync events ----------
  F.event(S0, 'hum', { dur: 1.2 });                                  // digital hum under the dive whoosh
  LINES.forEach((ln) => ln.ops.forEach((op) => {
    if (op.k === 'type') F.event(op.t0, 'keys', { dur: +(op.t1 - op.t0).toFixed(3), n: op.text.length }); // light fast key burst
    if (op.k === 'del') F.event(op.t0, 'backspace', { n: op.n });
    if (op.k === 'ins') F.event(op.t0, 'snap', { what: 'diff' });       // a fix lands in the code
  }));
  GHOSTS.forEach((g, i) => F.event(g.lit, 'tick', { what: 'edge-case', i }));
  F.event(82.20, 'keys', { dur: 0.05, n: 1, what: 'run tests' });
  F.event(82.50, 'thud', { what: 'tests red' });
  F.event(83.62, 'keys', { dur: 0.05, n: 1, what: 'run tests' });
  F.event(84.00, 'chime', { what: 'tests green' });
  F.event(86.05, 'paper', { what: 'approval card rises', soft: true });
  F.event(87.85, 'click', { what: 'Approve' });
  F.event(88.80, 'tick', { what: 'row count settles' });
  [89.55, 89.90, 90.25].forEach((t, i) => F.event(t, 'pop', { step: i }));   // three rising light notes
  F.event(92.50, 'whoosh', { what: 'table shrinks into chip', soft: true });
  F.event(93.32, 'drop', { what: 'chip settles' });

  F.scene({
    id: 's4_code', start: S0, end: S1, z: 10,
    build(layer) {
      // per-scene style (caret)
      layer.append(h('style', { text: `.s4caret{display:inline-block;width:2px;height:21px;margin-right:-2px;vertical-align:-5px;background:${COL.accent}}` }));
      const bg = h('div', { class: 'abs' });
      css(bg, { inset: 0, background: 'radial-gradient(130% 100% at 50% 42%, #282624 0%, #232120 50%, #1a1918 100%)' });
      layer.append(bg);

      // ---------- planes ----------
      const view = h('div', { class: 'abs' }); css(view, { inset: 0, transformOrigin: '960px 540px' }); layer.append(view);
      const mkPlane = () => { const p = h('div', { class: 'abs' }); css(p, { left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '0 0' }); view.append(p); return p; };
      const farP = mkPlane(), midP = mkPlane(), world = mkPlane(), nearP = mkPlane();
      // parallax: plane factor p relative to the main camera
      const place = (el, c, p) => {
        const s = Math.exp(Math.log(c.s) * p), x = 960 + (c.x - 960) * p, y = 540 + (c.y - 540) * p;
        el.style.transform = `translate(960px,540px) scale(${s.toFixed(5)}) translate(${(-x).toFixed(2)}px,${(-y).toFixed(2)}px)`;
      };

      // far: raw CSV exports of the three systems (canvas, drawn once fonts are in)
      const farC = h('canvas', { width: 3200, height: 2100 }); css(farC, { position: 'absolute', left: -640, top: -520, width: 3200, height: 2100, opacity: 0.2 });
      farP.append(farC);
      const midC = h('canvas', { width: 1700, height: 1500 }); css(midC, { position: 'absolute', left: 1240, top: 720, width: 1700, height: 1500, opacity: 0.2 });
      midP.append(midC);
      let drawn = false;
      function drawCanvases() {
        const r = F.rng(404);
        const g = farC.getContext('2d');
        g.clearRect(0, 0, 3200, 2100);
        g.filter = 'blur(1.6px)';
        g.font = '15px "IBM Plex Mono"';
        g.fillStyle = 'rgba(247,245,243,.55)';
        const stores = ['SZ-NS-014', 'BJ-CY-002', 'SH-PD-031', 'GZ-TH-008', 'CD-JJ-019', 'HZ-XH-011', 'WH-WC-006', 'NJ-GL-022'];
        for (let col = 0; col < 5; col++) {
          for (let row = 0; row < 86; row++) {
            const sys = (col + row) % 3, st = stores[Math.floor(r() * stores.length)], amt = (r() * 9000 + 40).toFixed(2);
            const id = 10000 + Math.floor(r() * 9000), d = 1 + Math.floor(r() * 30);
            let s;
            if (sys === 0) s = `ERP,2026-09-${String(d).padStart(2, '0')},${st},${id},${amt},CNY`;
            else if (sys === 1) s = `2026/9/${d};POS-${7000 + Math.floor(r() * 900)};${st};${amt.replace('.', ',')}`;
            else s = `EC-202609${String(d).padStart(2, '0')}-${String(Math.floor(r() * 99999)).padStart(5, '0')},2026-09-${String(d).padStart(2, '0')}T${String(Math.floor(r() * 24)).padStart(2, '0')}:${String(Math.floor(r() * 60)).padStart(2, '0')},${r() < 0.06 ? '-' : ''}${amt}`;
            if (r() < 0.04) s = s.replace(/[\d.,]+$/, '');
            g.globalAlpha = 0.35 + r() * 0.65;
            g.fillText(s, 40 + col * 640, 40 + row * 24);
          }
        }
        const m = midC.getContext('2d');
        m.clearRect(0, 0, 1700, 1500);
        m.filter = 'blur(1.4px)';
        m.font = '17px "IBM Plex Mono"';
        const test = [
          ['import pandas as pd', 'kw'], ['from daily_check import merge_daily, flag_anomalies', 'kw'], ['', ''],
          ['def test_anomaly_flagged_at_3x_baseline():', 'def'], ['    daily = load_fixture("spike_3x")', ''], ['    flagged = flag_anomalies(daily)', ''], ['    assert ("SZ-NS-014", "2026-10-03") in pairs(flagged)', ''], ['', ''],
          ['def test_quiet_day_has_no_anomaly():', 'def'], ['    assert flag_anomalies(load_fixture("quiet")).empty', ''], ['', ''],
          ['def test_merge_drops_duplicate_orders():', 'def'], ['    erp, pos, shop = load_fixture("dup_upload")', ''], ['    assert len(merge_daily(erp, pos, shop)) == 4', ''], ['', ''],
          ['def test_dates_in_two_formats_align():', 'def'], ['    merged = merge_daily(*load_fixture("mixed_dates"))', ''], ['    assert merged["date"].nunique() == 1', ''], ['', ''],
          ['def test_empty_amount_is_dropped_not_zero():', 'def'], ['    merged = merge_daily(*load_fixture("blank_amount"))', ''], ['    assert 0 not in merged["amount"].tolist()', ''],
        ];
        test.forEach(([s, k], i) => { m.fillStyle = k === 'def' ? COL.fn : k === 'kw' ? COL.kw : COL.text; m.fillText(s, 30, 40 + i * 30); });
        drawn = true;
      }

      // near: a few large out-of-focus figures drifting past the lens
      const NEAR = [['4,812', -120, 140, 120], ['2026/10/3', 1500, 930, 96], ['order_id', 1720, 120, 84], ['−2,980.00', 120, 1010, 90]];
      const nearEls = NEAR.map(([s, x, y, size]) => {
        const e = h('div', { class: 'abs', text: s });
        css(e, { left: x, top: y, fontFamily: MONO, fontSize: size, color: COL.fg, opacity: 0.06, filter: 'blur(7px)', whiteSpace: 'pre' });
        nearP.append(e); return e;
      });

      // ---------- main plane: the code ----------
      const LY0 = 236, LH = 31, CODE_X = 178, GUT_X = 158;
      const file = h('div', { class: 'abs' });
      css(file, { left: CODE_X, top: 176, fontFamily: MONO, fontSize: 14, color: COL.muted, letterSpacing: '.02em', display: 'flex', gap: 14, alignItems: 'center' });
      file.innerHTML = `<span style="color:${COL.text}">daily_check.py</span><span style="opacity:.55">reports/ · ERP_export/ · POS_export/ · ${esc(T('s4_chips')[2])}</span>`;
      world.append(file);
      const rule = h('div', { class: 'abs' }); css(rule, { left: 110, top: 204, width: 1120, height: 1, background: 'rgba(247,245,243,.08)' }); world.append(rule);
      const curLine = h('div', { class: 'abs' }); css(curLine, { left: 110, width: 1120, height: LH, background: 'rgba(247,245,243,.035)', borderRadius: 4 }); world.append(curLine);
      const lineEls = LINES.map((ln, i) => {
        const y = LY0 + i * LH;
        const num = h('div', { class: 'abs', text: String(i + 1) });
        css(num, { left: 100, width: 40, top: y, height: LH, lineHeight: LH + 'px', textAlign: 'right', fontFamily: MONO, fontSize: 14, color: COL.muted, opacity: 0.3 });
        const code = h('div', { class: 'abs' });
        css(code, { left: CODE_X, top: y, height: LH, lineHeight: LH + 'px', fontFamily: MONO, fontSize: 17, whiteSpace: 'pre', color: COL.text });
        world.append(num, code);
        return { num, code, key: '' };
      });
      // the blue thread: arrives from where we dived in, then runs down the gutter
      const svg = h('svg', { class: 'full', viewBox: '0 0 1920 1080' }); css(svg, { overflow: 'visible' });
      world.append(svg);
      const ARRIVE = `M 640 470 C 520 450 ${GUT_X} 360 ${GUT_X} ${LY0 + 4}`;
      const TERM_Y = 612;
      const thrD = `${ARRIVE} L ${GUT_X} ${TERM_Y - 6}`;
      const thrHalo = h('path', { d: thrD, fill: 'none', stroke: COL.accent, 'stroke-width': 11, 'stroke-linecap': 'round', opacity: 0.1 });
      const thrP = h('path', { d: thrD, fill: 'none', stroke: COL.accent, 'stroke-width': 2.2, 'stroke-linecap': 'round' });
      const thrHead = h('circle', { r: 4.6, fill: '#fff' });
      svg.append(thrHalo, thrP, thrHead);
      const thr = { length: thrP.getTotalLength(), g: svg };
      // draw the segment [a, b] (fractions of the length) with a head at b
      thr.update = (a, b, headOn) => {
        const Lt = thr.length, A = clamp(a) * Lt, B = Math.max(A, clamp(b) * Lt);
        for (const e of [thrHalo, thrP]) { e.setAttribute('stroke-dasharray', `${(B - A).toFixed(1)} ${Lt * 3}`); e.setAttribute('stroke-dashoffset', (-A).toFixed(1)); e.style.opacity = B - A < 0.5 ? 0 : ''; }
        const pt = thrP.getPointAtLength(B);
        F.attr(thrHead, { cx: pt.x, cy: pt.y, opacity: headOn && B > A ? 1 : 0 });
      };
      const tmp = h('path', { d: ARRIVE }); svg.append(tmp); const LA = tmp.getTotalLength(); tmp.remove();
      const threadLen = (y) => (LA + Math.max(0, y - (LY0 + 4))) / thr.length;

      // ghost rows: edge cases floating out of focus until handled
      const GX = 1270;
      const GY = [250, 336, 446, 526];
      const ghostEls = GHOSTS.map((g, i) => {
        const e = h('div', { class: 'abs' });
        css(e, { left: GX, top: GY[i], width: 470, transformOrigin: '0 50%' });
        const lab = h('div', { text: g.label });
        css(lab, { fontFamily: 'var(--sans)', fontSize: 14, color: COL.muted, marginBottom: 5, letterSpacing: '.02em' });
        e.append(lab);
        const rowsWrap = h('div'); css(rowsWrap, { position: 'relative', paddingLeft: 14 });
        const barEl = h('div'); css(barEl, { position: 'absolute', left: 0, top: 3, bottom: 3, width: 2, borderRadius: 1, background: COL.accent });
        rowsWrap.append(barEl);
        g.rows.forEach((cells, j) => {
          const r = h('div'); css(r, { display: 'flex', gap: 14, fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: COL.text, whiteSpace: 'pre' });
          if (j === 1) css(r, { opacity: 0.6, transform: 'translate(5px, -2px)' });
          cells.forEach((c) => {
            const s = h('span', { text: c == null ? ' ' : c });
            if (c == null) css(s, { width: 62, height: 18, marginTop: 3, border: `1px dashed ${COL.muted}`, borderRadius: 3 });
            if (c === 'vs') css(s, { color: COL.muted });
            if (/^−/.test(c || '')) css(s, { color: COL.num });
            if (c === '2026/10/3') css(s, { color: COL.num });
            r.append(s);
          });
          rowsWrap.append(r);
        });
        e.append(rowsWrap);
        world.append(e);
        const r = F.rng(70 + i);
        return { e, barEl, lab, dx: 40 + r() * 50, dy: (r() - 0.5) * 30, rot: (r() - 0.5) * 3 };
      });

      // terminal block under the code
      const termBox = h('div', { class: 'abs' });
      css(termBox, { left: 110, top: TERM_Y, width: 1120, height: 0, overflow: 'hidden', webkitMaskImage: 'linear-gradient(transparent 0, #000 34px)', maskImage: 'linear-gradient(transparent 0, #000 34px)', borderRadius: 10, background: 'rgba(10,9,9,.42)', border: '1px solid rgba(247,245,243,.08)' });
      const termInner = h('div', { class: 'abs' }); css(termInner, { left: 18, top: 0, width: 1090 });
      termBox.append(termInner); world.append(termBox);
      const TLH = 21, TVIS = 8, TPAD = 13;
      const termLines = TERM.map((ln, i) => {
        const e = h('div'); css(e, { height: TLH, lineHeight: TLH + 'px', fontFamily: MONO, fontSize: 13.4, whiteSpace: 'pre', color: COL.text, overflow: 'hidden' });
        const spans = ln.segs.map(([s, c]) => { const sp = h('span', { text: s }); if (c) css(sp, { color: c }); e.append(sp); return sp; });
        termInner.append(e);
        return { e, spans, at: ln.at, shown: null };
      });
      const dotsLines = [termLines[1], termLines[termLines.length - 2]];

      // corner labels (screen space)
      const rt = h('div', { class: 'abs', text: 'Codex runtime' });
      css(rt, { left: 56, top: 46, fontFamily: MONO, fontSize: 13, color: COL.muted, letterSpacing: '.06em', opacity: 0 });
      const ex = h('div', { class: 'abs', text: T('example') });
      css(ex, { right: 56, bottom: 40, fontFamily: 'var(--sans)', fontSize: 13, color: COL.muted, letterSpacing: '.08em', opacity: 0 });
      layer.append(rt, ex);

      const scrim = h('div', { class: 'abs' }); css(scrim, { inset: 0, background: '#1a1918', opacity: 0 }); layer.append(scrim);
      // ---------- approval card (S4-05), dark app theme, ToolCallRow + ApprovalDetail ----------
      const cardWrap = h('div', { class: 'abs' });
      css(cardWrap, { left: 960, top: 470, width: 0, height: 0 });
      const card = h('div');
      css(card, { position: 'absolute', left: -330, width: 660, top: -150, borderRadius: 12, border: `1px solid ${COL.accent}`, background: '#322f2e',
        padding: '8px 12px', fontSize: 14, lineHeight: '1.45', color: COL.fg, fontFamily: 'var(--sans)', boxShadow: '0 30px 80px rgba(0,0,0,.45)', transformOrigin: '50% 50%' });
      const desc = L('合并并校验三个系统的日报', 'Merge and check the three daily exports');
      const head = h('div');
      css(head, { display: 'flex', alignItems: 'center', gap: 6 });
      head.innerHTML = `<span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Approve: Ran a command: <code style="border-radius:4px;background:rgba(247,245,243,.12);padding:2px 4px;font-family:${MONO};font-size:.85em">${esc(desc)}</code></span>` +
        `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${COL.muted}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none;transform:rotate(90deg)"><path d="M9 6l6 6-6 6"/></svg>`;
      const det = h('div'); css(det, { marginTop: 6, display: 'flex', flexDirection: 'column', gap: 6 });
      const dline = h('div', { text: desc }); css(dline, { fontSize: 12, color: COL.muted });
      const RUNNER = [
        'import pandas as pd',
        'from daily_check import load_exports, merge_daily, flag_anomalies',
        '',
        `erp, pos, shop = load_exports("ERP_export/", "POS_export/", "${T('s4_chips')[2]}")`,
        'daily = merge_daily(erp, pos, shop)',
        'anomalies = flag_anomalies(daily)',
        `anomalies.to_excel("reports/${L('日报异常', 'daily-anomalies')}_2026-10-03.xlsx", index=False)`,
        `print(f"${L('合并 3 个来源 · ', 'Merged 3 sources · ')}{len(erp) + len(pos) + len(shop):,}${L(' 行 → ', ' rows → ')}{len(daily):,}${L(' 行', ' rows')}")`,
      ];
      const pre = h('pre');
      css(pre, { margin: 0, borderRadius: 6, background: 'rgba(0,0,0,.2)', padding: '6px 8px', fontFamily: MONO, fontSize: 12, lineHeight: '1.5', whiteSpace: 'pre-wrap', wordBreak: 'break-all' });
      pre.innerHTML = RUNNER.map((s) => hl(s, null, null) || ' ').join('\n');
      const guard = h('div', { text: L('写入仅限工作区 reports/', 'Writes limited to workspace reports/') }); css(guard, { fontSize: 12, color: COL.muted });
      det.append(dline, pre, guard);
      const btns = h('div'); css(btns, { marginTop: 8, display: 'flex', gap: 8 });
      const approve = h('span', { text: 'Approve' });
      css(approve, { borderRadius: 6, background: COL.accent, color: '#fff', padding: '4px 12px', fontSize: 14, display: 'inline-block' });
      const deny = h('span', { text: 'Deny' });
      css(deny, { borderRadius: 6, border: '1px solid rgba(247,245,243,.14)', padding: '3px 11px', fontSize: 14, display: 'inline-block' });
      btns.append(approve, deny);
      const approved = h('div', { text: 'Approved' }); css(approved, { marginTop: 4, fontSize: 12, color: COL.muted, display: 'none' });
      card.append(head, det, btns, approved);
      cardWrap.append(card); layer.append(cardWrap);
      // mouse pointer
      const cursor = h('div', { class: 'abs', html: '<svg width="30" height="42" viewBox="0 0 20 28"><path d="M2 2 L2 21 L6.6 16.8 L9.8 24.2 L13 22.8 L9.9 15.6 L16 15.6 Z" fill="#fff" stroke="#141312" stroke-width="1.3" stroke-linejoin="round"/></svg>' });
      css(cursor, { left: 0, top: 0, filter: 'drop-shadow(0 2px 4px rgba(0,0,0,.4))', opacity: 0 });
      layer.append(cursor);

      // ---------- run output (S4-06) ----------
      const out = h('div', { class: 'abs' });
      css(out, { left: 270, top: 250, width: 1380, transformOrigin: '0 0', opacity: 0 });
      const outPath = h('div', { text: `reports/${L('日报异常', 'daily-anomalies')}_2026-10-03.xlsx` });
      css(outPath, { fontFamily: MONO, fontSize: 14, color: COL.muted, letterSpacing: '.02em', marginBottom: 14 });
      const prog1 = h('div'); css(prog1, { fontFamily: 'var(--sans)', fontSize: 27, color: COL.fg, marginBottom: 34, fontVariantNumeric: 'tabular-nums' });
      const progA = h('span', { text: L('合并 3 个来源 · 4,812 行 → ', 'Merged 3 sources · 4,812 rows → ') });
      const progN = h('span', { text: '4,812' }); css(progN, { fontFamily: MONO, fontSize: 26 });
      const progB = h('span', { text: L(' 行', ' rows') });
      prog1.append(progA, progN, progB);
      const GRID = '120px 330px 170px 1fr';
      const hdr = h('div'); css(hdr, { display: 'grid', gridTemplateColumns: GRID, gap: 0, padding: '0 0 10px 22px', fontSize: 14, color: COL.muted, letterSpacing: '.04em', borderBottom: '1px solid rgba(247,245,243,.14)' });
      [L('来源', 'Source'), L('门店 / 订单', 'Store / order'), L('日期', 'Date'), L('现象', 'What it found')].forEach((s) => hdr.append(h('span', { text: s })));
      const ROWS = [
        ['POS', L('华南 · 深圳南山店', 'South · Shenzhen Nanshan'), '2026-10-03', L('金额 <b>¥186,420</b>，为 28 日中位数 <b>4.1</b> 倍，疑似重复上传', '<b>¥186,420</b>, <b>4.1×</b> the 28-day median; likely a duplicate upload')],
        ['ERP', L('华北 · 北京朝阳店', 'North · Beijing Chaoyang'), '2026-10-03', L('当日无记录，而 POS 有 <b>312</b> 单', 'No record that day, while POS shows <b>312</b> orders')],
        [L('电商', 'Webshop'), 'EC-20261003-00871', '2026-10-03', L('金额 <b>−¥2,980</b>，退款未标记', '<b>−¥2,980</b>, a refund nobody flagged')],
      ];
      const rowEls = ROWS.map(([src, store, date, what]) => {
        const r = h('div'); css(r, { display: 'grid', gridTemplateColumns: GRID, alignItems: 'center', padding: '17px 0 17px 22px', fontSize: 20, color: COL.text, borderBottom: '1px solid rgba(247,245,243,.08)', position: 'relative' });
        const dot = h('span'); css(dot, { position: 'absolute', left: 2, top: '50%', width: 7, height: 7, marginTop: -3.5, borderRadius: 4, background: COL.warn });
        const a = h('span', { text: src }); css(a, { fontFamily: MONO, fontSize: 16, color: COL.muted });
        const b = h('span', { text: store }); css(b, { fontFamily: /^EC-/.test(store) ? MONO : 'var(--sans)', fontSize: /^EC-/.test(store) ? 17 : 20 });
        const c = h('span', { text: date }); css(c, { fontFamily: MONO, fontSize: 16, color: COL.muted });
        const d = h('span', { html: what.replace(/<b>/g, `<b style="font-weight:500;color:${COL.fg};font-family:${MONO};font-size:.92em">`) });
        r.append(dot, a, b, c, d);
        return r;
      });
      out.append(outPath, prog1, hdr, ...rowEls);
      layer.append(out);

      // ---------- S4-07: back on the light surface ----------
      const surf = h('div', { class: 'abs' }); css(surf, { inset: 0, opacity: 0, visibility: 'hidden' });
      layer.append(surf);
      K.surface(surf);
      const scam = K.camera(surf);
      const box = K.inputBox(scam.world, { y: 540, placeholder: T('placeholder') });
      const chipDefs = [['s1_out', 'sheet'], ['s2_out', 'slides'], ['s3_out', 'doc'], ['s4_out', 'code']];
      const chips = chipDefs.map(([k, kind]) => box.addChip(T(k), kind));
      const sgrain = K.grain(surf, 0.035);
      // the flying deliverable: a chip that grows out of the shrinking table
      const flyer = K.fileChip(T('s4_out'), 'code');
      css(flyer, { position: 'absolute', left: 0, top: 0, transformOrigin: '0 0', opacity: 0, boxShadow: '0 10px 30px rgba(0,0,0,.18)' });
      layer.append(flyer);
      const grainU = K.grain(layer, 0.04);

      const stageScale = () => layer.getBoundingClientRect().width / 1920;
      const toStage = (r) => { const st = layer.getBoundingClientRect(), sc = st.width / 1920; return { x: (r.left - st.left) / sc, y: (r.top - st.top) / sc, w: r.width / sc, h: r.height / sc }; };

      // ---------- camera ----------
      const CAM = [
        { t: 77.4, x: 640, y: 470, s: 0.62 },
        { t: 79.1, x: 620, y: 330, s: 1.30, e: 'outCubic' },
        { t: 80.2, x: 840, y: 380, s: 1.16 },
        { t: 81.3, x: 930, y: 430, s: 1.08 },
        { t: 82.2, x: 930, y: 500, s: 1.04 },
        { t: 82.8, x: 900, y: 470, s: 1.07 },
        { t: 83.5, x: 860, y: 420, s: 1.12 },
        { t: 84.3, x: 900, y: 500, s: 1.06 },
        { t: 86.2, x: 940, y: 480, s: 1.02, e: 'inOutQuad' },
        { t: 94.0, x: 950, y: 470, s: 0.94, e: 'outQuad' },
      ];

      return (lt, t) => {
        if (!drawn && document.fonts.check('15px "IBM Plex Mono"')) drawCanvases();
        const c = K.camPath(CAM, t);
        // slow orbital drift for life
        c.x += Math.sin(t * 0.35) * 6; c.y += Math.cos(t * 0.29) * 4;
        place(world, c, 1); place(midP, c, 0.82); place(farP, c, 0.55);
        place(nearP, c, 1.35);

        // world recedes behind the card and the run output
        const back = tw(t, 85.7, 86.3, 'inOutCubic');
        const back2 = tw(t, 88.4, 89.0, 'inOutCubic');
        const recede = Math.max(back * 0.75, back2 * 1.25);
        css(view, { transform: `scale(${1 - 0.04 * recede})`, filter: recede > 0.01 ? `blur(${(recede * 3.6).toFixed(2)}px) brightness(${(1 - 0.48 * recede).toFixed(3)})` : 'none' });

        css(scrim, { opacity: (Math.max(back * 0.25, back2 * 0.5)).toFixed(3) });
        // corner labels
        css(rt, { opacity: 0.85 * tw(t, 78.0, 78.7, 'outQuad') * (1 - tw(t, 92.3, 92.8)) });
        css(ex, { opacity: 0.55 * tw(t, 78.4, 79.0, 'outQuad') * (1 - tw(t, 92.3, 92.8)) });

        // ---- code lines ----
        // the caret sits where the latest edit happened, while the agent is writing
        let cop = null;
        for (const o of ALLOPS) if (t >= o.t0) cop = o;
        const caretLine = cop && (t < 82.08 || (cop.k === 'ins' && t < cop.t1 + 0.2)) ? cop.line : -1;
        const ma = +(0.2 * (1 - tw(t, 84.3, 85.2))).toFixed(3);
        LINES.forEach((ln, i) => {
          const st = lineState(ln, t);
          const caret = i === caretLine ? st.caret : null;
          const key = st.s + '|' + (st.mark ? st.mark.join(',') + ':' + ma : '') + '|' + caret;
          const el = lineEls[i];
          if (key !== el.key) { el.key = key; el.code.innerHTML = hl(st.s, st.mark, caret, ma); }
          css(el.num, { opacity: st.s.length || (i === 6 && t > 80.8) ? 0.5 : 0 });
        });
        // current-line highlight follows the caret
        if (caretLine >= 0) css(curLine, { top: LY0 + caretLine * LH });
        css(curLine, { opacity: caretLine >= 0 ? 1 : 0 });

        // ---- thread ----
        const li = lastTyped(t);
        const yLine = (i) => LY0 + i * LH + LH / 2;
        let yHead = yLine(Math.max(0, li));
        if (li >= 1) { const s0 = lineStart(li); yHead = lerp(yLine(prevTyped(li)), yLine(li), ease.outCubic(prog(t, s0, s0 + 0.12))); }
        if (t > 82.05) yHead = lerp(yHead, TERM_Y - 6, ease.inOutCubic(prog(t, 82.05, 82.25)));
        const kArr = ease.inOutCubic(prog(t, 77.85, 78.66));
        const kThread = t < 78.66 ? kArr * threadLen(LY0 + 4) : threadLen(yHead);
        const kTail = ease.inOutCubic(prog(t, 78.5, 79.4)) * threadLen(LY0 + 4) * 0.999;
        thr.update(kTail, kThread, t < 82.3);
        thr.g.style.opacity = 1 - tw(t, 84.2, 85.2) * 0.55;

        // ---- ghosts ----
        ghostEls.forEach((g, i) => {
          const G = GHOSTS[i];
          const k = tw(t, G.lit, G.lit + 0.45, 'outCubic');
          const failing = G.test === 'fail' && t >= 82.5 && t < 84.0;
          const passed = t >= 84.0;
          css(g.e, {
            opacity: (0.24 + 0.76 * k) * tw(t, 78.2, 79.2, 'outQuad'),
            filter: k < 0.98 ? `blur(${((1 - k) * 2.2).toFixed(2)}px)` : 'none',
            transform: `translate(${((1 - k) * g.dx).toFixed(1)}px, ${((1 - k) * g.dy).toFixed(1)}px) rotate(${((1 - k) * g.rot).toFixed(2)}deg) scale(${(0.95 + 0.05 * k).toFixed(4)})`,
          });
          css(g.barEl, { background: failing ? COL.bad : passed ? COL.ok : COL.accent, opacity: k });
          css(g.lab, { color: failing ? COL.bad : passed ? COL.ok : COL.muted });
        });

        // ---- terminal ----
        const unfold = tw(t, 82.02, 82.26, 'outCubic');
        css(termBox, { height: Math.round(unfold * (TPAD * 2 + TVIS * TLH)), opacity: unfold > 0 ? 1 : 0 });
        let printed = 0;
        termLines.forEach((tl2, i) => { const on = t >= tl2.at; if (on !== tl2.shown) { tl2.e.style.visibility = on ? 'visible' : 'hidden'; tl2.shown = on; } if (on) printed = i + 1; });
        // smooth scroll: follow the last printed line, with a short ease
        let target = Math.max(0, printed - TVIS);
        const lastAt = printed ? termLines[printed - 1].at : 0;
        const prevTarget = Math.max(0, printed - 1 - TVIS);
        const sc = lerp(prevTarget, target, ease.outCubic(prog(t, lastAt, lastAt + 0.05)));
        css(termInner, { top: TPAD - sc * TLH });
        // progressive dots
        const dotsStr = (times, finalStr) => { let n = 0; times.forEach((x) => { if (t >= x) n++; }); return finalStr.slice(0, n); };
        const d1 = dotsStr(RUN1_DOTS, '..FFF'), d2 = dotsStr(RUN2_DOTS, '.....');
        const setDots = (ln, s, done) => {
          const html = Array.from(s).map((ch) => `<span style="color:${ch === 'F' ? COL.bad : COL.ok}">${ch}</span>`).join('');
          if (ln.spans[0].innerHTML !== html) ln.spans[0].innerHTML = html;
          ln.spans[1].style.visibility = done ? 'visible' : 'hidden';
        };
        setDots(dotsLines[0], d1, d1.length === 5);
        setDots(dotsLines[1], d2, d2.length === 5);

        // ---- approval card ----
        const kc = tw(t, 85.8, 86.35, 'outCubic');
        const kOut = tw(t, 88.25, 88.75, 'inOutCubic');
        const isApproved = t >= 87.9;
        btns.style.display = isApproved ? 'none' : 'flex';
        approved.style.display = isApproved ? '' : 'none';
        css(card, { borderColor: isApproved ? 'rgba(247,245,243,.14)' : COL.accent });
        css(cardWrap, {
          opacity: (kc * (1 - kOut)).toFixed(3), display: kc > 0 && kOut < 1 ? '' : 'none',
          transform: `translateY(${((1 - kc) * 40 - kOut * 30).toFixed(1)}px) scale(${(1.5 * (0.97 + 0.03 * kc) * (1 - 0.06 * kOut)).toFixed(4)})`,
          filter: kOut > 0.01 ? `blur(${(kOut * 4).toFixed(2)}px)` : 'none',
        });
        // pointer: enters from lower right, rests on Approve 0.4 s, clicks
        if (t > 86.4 && t < 88.6 && cardWrap.style.display !== 'none') {
          const r = toStage(approve.getBoundingClientRect());
          const tx = r.x + r.w * 0.55, ty2 = r.y + r.h * 0.6;
          const km = tw(t, 86.55, 87.45, 'inOutCubic');
          const sx = lerp(1720, tx, km), sy = lerp(1030, ty2, km) - Math.sin(Math.PI * km) * 60;
          const press = t >= 87.85 && t < 87.97;
          css(cursor, { opacity: tw(t, 86.5, 86.75) * (1 - tw(t, 88.15, 88.45)), transform: `translate(${(sx - 3).toFixed(1)}px, ${(sy - 3).toFixed(1)}px) scale(${press ? 0.92 : 1})` });
          const hover = t >= 87.4;
          css(approve, { background: hover ? '#66a3ec' : COL.accent, transform: `scale(${press ? 0.95 : 1})` });
        } else css(cursor, { opacity: 0 });

        // ---- run output ----
        const ko = tw(t, 88.55, 89.1, 'outCubic');
        // count settles 4,812 -> 4,790
        const n = Math.round(lerp(4812, 4790, ease.outQuart(prog(t, 88.75, 89.35))));
        const ns = K.fmt(n);
        if (progN.textContent !== ns) progN.textContent = ns;
        css(prog1, { opacity: ko });
        css(outPath, { opacity: ko * 0.9 });
        css(hdr, { opacity: tw(t, 89.2, 89.6, 'outQuad') });
        rowEls.forEach((r, i) => {
          const a = 89.55 + i * 0.35;
          const k = tw(t, a - 0.1, a + 0.55, 'outCubic');
          css(r, { opacity: ease.outQuad(clamp(k * 1.4)), transform: `translateY(${((1 - k) * 70).toFixed(1)}px)` });
        });
        // S4-07: shrink into the chip slot
        const kS = tw(t, 92.5, 93.35, 'inOutCubic');
        // surface fades in over the depth
        const kSurf = tw(t, 92.55, 93.45, 'inOutCubic');
        css(surf, { opacity: kSurf.toFixed(3), visibility: t >= 92.3 ? 'visible' : 'hidden' });
        if (t >= 92.3) {
          scam.set({ x: 960, y: 540, s: lerp(1.2, 1.215, prog(t, 92.5, 94.0)) });
          box.update({ str: '', k: 1, t, caretOn: true });
          chips.slice(0, 3).forEach((ch, i) => {
            const k = tw(t, 92.75 + i * 0.12, 93.2 + i * 0.12, 'outCubic');
            css(ch, { opacity: k, transform: `translateY(${((1 - k) * 10).toFixed(1)}px) scale(${(0.96 + 0.04 * k).toFixed(4)})` });
          });
          sgrain(t);
        }
        const outBase = { x: 270, y: 250, w: 1380, h: out.offsetHeight || 380 };
        if (t >= 92.4) {
          const slot = toStage(chips[3].getBoundingClientRect());
          // table: centre travels to the slot centre, scale down to chip height-ish
          const cx0 = outBase.x + outBase.w / 2, cy0 = outBase.y + outBase.h / 2;
          const cx1 = slot.x + slot.w / 2, cy1 = slot.y + slot.h / 2;
          const sEnd = slot.w / outBase.w;
          const sc2 = Math.exp(lerp(0, Math.log(sEnd), kS));
          const cx = lerp(cx0, cx1, kS), cy = lerp(cy0, cy1, kS) - Math.sin(Math.PI * kS) * 40;
          css(out, { transformOrigin: '50% 50%', left: cx - outBase.w / 2, top: cy - outBase.h / 2, transform: `scale(${sc2.toFixed(4)})`, opacity: (ko * (1 - tw(t, 92.85, 93.2))).toFixed(3) });
          // the chip grows out of it and lands
          const kf = tw(t, 92.85, 93.15, 'outQuad');
          const fs = lerp(1.9, 1, ease.outCubic(prog(t, 92.75, 93.35)));
          css(flyer, { opacity: t < 93.4 ? kf : 0, transform: `translate(${(cx - slot.w * fs / 2).toFixed(1)}px, ${(cy - slot.h * fs / 2).toFixed(1)}px) scale(${(fs * slot.w / Math.max(1, flyerW())).toFixed(4)})` });
          css(chips[3], { opacity: t >= 93.35 ? 1 : 0, transform: `scale(${t >= 93.35 ? 1 + 0.04 * Math.sin(Math.PI * prog(t, 93.35, 93.6)) : 1})` });
        } else {
          css(out, { transformOrigin: '0 0', left: outBase.x, top: outBase.y, transform: `translateY(${((1 - ko) * 24).toFixed(1)}px)`, opacity: ko.toFixed(3) });
          css(flyer, { opacity: 0 });
          css(chips[3], { opacity: 0 });
        }
        grainU(t);
      };

      // helpers that need the built DOM
      function flyerW() { return flyer.offsetWidth || 1; }
      function lastTyped(t) { let li = -1; LINES.forEach((ln, i) => { const s = lineStart(i); if (s != null && t >= s) li = i; }); return li; }
      function prevTyped(i) { for (let j = i - 1; j >= 0; j--) if (lineStart(j) != null) return j; return 0; }
    },
  });
})();
