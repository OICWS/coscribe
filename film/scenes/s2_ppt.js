// Sentence 2 · the management briefing (S2-02 … S2-07), film time 33.0–50.6.
//
// One 2.5D world seen through a dolly camera (real depth, so layers parallax):
//   z = -1400   the source workbook 销售下滑分析.xlsx: five dense sheets, out of focus
//   z ≈ 0       the storyline (问题 → 原因 → 建议), whose nine titles become nine slides
//   z -900…+700 drifting fragments of the deck's own vocabulary (layouts, sizes, tools)
// The blue thread draws the storyline; the titles drop into 16:9 frames that are
// laid out on the user's own template; the thread pulls one figure down through
// the layers to its cell and its evidence; a contrast warning is fixed; the deck
// rises, fans out and folds into the chip that sentence 3's input box picks up.
(function () {
  const { h, css, clamp, lerp, tw, ease, prog, env, L, T } = F;
  const T0 = 33.0, T1 = 50.6;
  const P = 2400;                       // perspective (px)
  const ZH = F.lang === 'zh';

  // ---------- example content (script §4.2) ----------
  const TITLES = [
    L('40 家门店销售下滑：原因与建议', 'Sales decline across 40 stores: causes and proposals'),
    L('结论先行：下滑 6.8%，七成来自家居品类折扣加深', 'The answer first: down 6.8%, seven-tenths of it from deeper Home discounts'),
    L('不是客流：客流 −1.2%，客单价 −5.7%', 'Not footfall: footfall −1.2%, average ticket −5.7%'),
    L('不是新店：剔除新店后仍 −6.1%', 'Not new stores: still −6.1% without them'),
    L('是折扣：家居品类折扣率 12% → 19%', 'Discounts: Home category discount rate 12% → 19%'),
    L('集中在华南 8 家门店', 'Concentrated in 8 South China stores'),
    L('数据修正说明：华东 3 月重复入账已剔除', 'Data correction: East China March duplicates removed'),
    L('建议：收紧折扣、调整陈列、参与招标', 'Proposals: tighten discounts, re-plan displays, bid'),
    L('下一步与时间表', 'Next steps and timeline'),
  ];
  const WORDS = [L('问题', 'Problem'), L('原因', 'Cause'), L('建议', 'Proposal')];
  const DECK = L('销售下滑：原因与建议', 'Sales decline: causes and proposals');

  // the user's company template: restrained, neutral, one small accent
  const TPL = { ink: '#201e1d', body: '#3d3936', gray: '#86817c', rule: '#e4e1dd', bar: '#cfcac4', accent: '#c4561f', red: '#c93a3a' };
  const PALETTE = ['#201e1d', '#86817c', '#e4e1dd', '#c4561f'];
  // before the template is applied: six unrelated colours (app tokens)
  const MESSY = ['#2d62b8', '#b8456a', '#3d8f56', '#b87a14', '#6a4db3', '#17767c'];

  // ---------- helpers ----------
  const rgb = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const mix = (a, b, k) => { const A = rgb(a), B = rgb(b); return `rgb(${A.map((v, i) => Math.round(lerp(v, B[i], clamp(k)))).join(',')})`; };
  // static style (strings for unitless values)
  const st = (e, o) => { for (const [k, v] of Object.entries(o)) e.style[k] = typeof v === 'number' && !/opacity|zIndex|fontWeight|lineHeight|flex/.test(k) ? v + 'px' : String(v); return e; };
  const div = (parent, o = {}, text) => { const e = h('div', text != null ? { text } : {}); st(e, { position: 'absolute', ...o }); parent.append(e); return e; };
  // slide-internal text (pt == px on a 960-wide slide)
  const tx = (parent, str, x, y, o = {}) => div(parent, {
    left: x, top: y, width: o.w ?? 'auto', fontSize: o.size ?? 18, fontWeight: o.weight ?? 400, color: o.color ?? TPL.ink,
    lineHeight: o.lh ?? 1.3, fontFamily: o.font ?? 'var(--sans)', textAlign: o.align ?? 'left', whiteSpace: o.wrap ? 'normal' : 'nowrap',
    letterSpacing: o.ls ?? '0', fontVariantNumeric: 'tabular-nums',
  }, str);
  // camera with a real dolly: x,y = world point at screen centre; d = dolly toward the scene
  function camPath(keys, t) {
    if (t <= keys[0].t) return keys[0];
    for (let i = 1; i < keys.length; i++) {
      const a = keys[i - 1], b = keys[i];
      if (t <= b.t) {
        const k = ease[b.e || 'inOutCubic'](prog(t, a.t, b.t)), o = {};
        for (const p of ['x', 'y', 'd', 'rx', 'ry']) o[p] = lerp(a[p] ?? 0, b[p] ?? a[p] ?? 0, k);
        return o;
      }
    }
    return keys[keys.length - 1];
  }

  // ---------- layout (world coordinates, z = 0 plane) ----------
  const COL = [150, 750, 1350];                    // storyline / TOC column lefts
  const WORD_Y = 318, RULE_Y = 384, TOC_Y = 424, TOC_PITCH = ZH ? 50 : 70;
  const S_TOC = 0.66, S_GRID = 0.583, S_FAN = 0.92;
  const TITLE_AT = [56, 58];                       // title origin inside a slide
  const GRID_C = [397, 997, 1597], GRID_R = [197, 547, 897];
  const ZOFF = [-70, 30, -130, 90, 0, -40, 130, -90, 40];
  const DROP_ORDER = [0, 1, 2, 3, 4, 5, 6, 8, 7];   // 建议 lands last, on top
  const FAN_ANG = [-9, 8, -6.5, 6, -4, 3.5, -2, 1.5, 0];
  const RANK = []; DROP_ORDER.forEach((s, r) => { RANK[s] = r; });

  // ---------- timings ----------
  const TH = [34.3, 36.3];                         // storyline thread
  const tGrid = (i) => 38.0 + 0.05 * i;            // TOC line -> frame
  const BUILD_ORDER = [4, 1, 3, 7, 0, 5, 2, 8, 6]; // centre first, then around
  const tBuild = (i) => 38.85 + 0.12 * BUILD_ORDER.indexOf(i);
  const T_THEME = 40.2;                            // swatches converge, accents unify
  const TOOLS = [39.2, 40.0, 40.8];
  const A0 = 42.0, B0 = 44.6;                      // quality shots
  const tDrop = (i) => 46.15 + 0.27 * RANK[i];
  const T_FOLD = 49.15, T_FLY = 49.55, T_LAND = 50.3;

  F.scene({
    id: 's2_ppt', start: T0, end: T1, z: 10,
    build(layer) {
      // warm depth (a hint of slides orange in the near-black)
      const bg = div(layer, { inset: 0, background: 'radial-gradient(120% 95% at 50% 42%, #201b18 0%, #161311 52%, #0d0b0a 100%)' });
      const view = div(layer, { left: 0, top: 0, width: 1920, height: 1080, perspective: P + 'px', perspectiveOrigin: '960px 540px' });
      const world = div(view, { left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '0 0', transformStyle: 'preserve-3d' });
      const place = (e, X, Y, Z, extra = '') => { e.style.transform = `translate3d(${X}px,${Y}px,${Z}px)${extra}`; };

      // ===== the workbook plane (z -1400) =====
      const ZW = -1400;
      const E = { x: 520, y: 1250, cols: [240, 300, 180, 160], rowH: 36 };      // 结论 sheet
      const C4 = { x: E.x + 44 + 240 + 300, y: E.y + 28 + 3 * E.rowH, w: 180, h: 36 };
      C4.cx = C4.x + C4.w / 2; C4.cy = C4.y + C4.h / 2;
      const FAR = { x0: -760, y0: -560, x1: 2760, y1: 2440, k: 0.5 };
      const NEAR = { x0: 160, y0: 860, x1: 2120, y1: 2260, k: 1 };
      const mkCanvas = (R) => {
        const c = h('canvas', { width: Math.round((R.x1 - R.x0) * R.k), height: Math.round((R.y1 - R.y0) * R.k) });
        st(c, { position: 'absolute', left: 0, top: 0, width: R.x1 - R.x0, height: R.y1 - R.y0, transformOrigin: '0 0' });
        world.append(c); place(c, R.x0, R.y0, ZW); return c;
      };
      const farC = mkCanvas(FAR), nearC = mkCanvas(NEAR);
      const wbText = drawWorkbookText();
      let drawn = false;
      const drawAll = () => {
        drawWorkbook(farC, FAR, 1.4); drawWorkbook(nearC, NEAR, 0); drawn = true;
      };
      document.fonts.load('15px "IBM Plex Mono"', '0123456789.-%');
      document.fonts.load('15px "Noto Sans SC"', wbText);
      document.fonts.load('500 15px "Noto Sans SC"', wbText);
      document.fonts.load('500 17px "IBM Plex Sans"', 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz×');
      document.fonts.ready.then(drawAll);

      // C4 highlight + reference tag (in the workbook plane, just in front)
      const c4box = div(world, { left: 0, top: 0, width: C4.w, height: C4.h, border: '2px solid var(--d-sheet)', borderRadius: 2, opacity: 0 });
      place(c4box, C4.x, C4.y, ZW + 2);
      const c4tag = div(world, { left: 0, top: 0, padding: '5px 10px', borderRadius: 6, background: '#1d2a21', border: '1px solid rgba(127,207,152,.45)',
        color: 'var(--d-sheet)', fontFamily: 'var(--mono)', fontSize: 17, whiteSpace: 'nowrap', opacity: 0 }, L('结论!C4 = −6.8%', 'Summary!C4 = −6.8%'));
      place(c4tag, C4.x + C4.w + 18, C4.y - 2, ZW + 4);
      const c4anchor = div(world, { left: 0, top: 0, width: 2, height: 2 }); place(c4anchor, C4.cx - 1, C4.y - 1, ZW + 2);

      // evidence: the reviewer's log, drawn like an opened tool row (ChatLog ToolCallRow)
      const EV = { x: 960, y: 1790, w: 660 };
      const ev = div(world, { left: 0, top: 0, width: EV.w, borderRadius: 15, border: '1px solid var(--d-border)', background: 'var(--d-card)',
        padding: '12px 15px 14px', color: 'var(--d-fg)', fontSize: 17, opacity: 0 });
      place(ev, EV.x, EV.y, ZW + 120);
      const evHead = div(ev, { position: 'relative', display: 'flex', alignItems: 'center', gap: 7 });
      evHead.append(h('span', { text: 'Asked a reviewer to check the work' }));
      const chev = h('span', { html: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>' });
      st(chev, { color: 'var(--d-muted)', display: 'inline-flex', transform: 'rotate(90deg)' });
      evHead.append(chev);
      const pre = div(ev, { position: 'relative', marginTop: 10, borderRadius: 8, background: 'rgba(247,245,243,.07)', padding: '10px 13px',
        fontFamily: 'var(--mono)', fontSize: 15, lineHeight: 1.75, color: 'var(--d-muted)', whiteSpace: 'pre' });
      const evLines = [
        'evidence:',
        '  run_python_script #2 → duplicate_rows = 2316',
        '  run_python_script #3 → yoy_total = -0.068',
        '  run_python_script #4 → yoy_footfall = -0.012',
      ].map((s) => div(pre, { position: 'relative' }, s));
      const evHit = evLines[2];
      const evAnchor = div(evHit, { left: -6, top: 13, width: 2, height: 2 });

      // ===== drifting fragments (vocabulary of the deck) =====
      const FRAG = ['layout: stat-callout', 'layout: two-column', 'template_path', '16 : 9', '40 pt', '28 pt', '18 pt', '12 pt', '#c4561f', '−6.8%',
        '12% → 19%', L('¥3.84 亿', '¥384M'), 'add_pptx_chart', 'render_pptx_preview', 'slide 5 / 9',
        'text_overlap_warnings: 0', '−1.2%', '−5.7%', '−6.1%', L('华南', 'South'), 'SUMIFS', L('结论!C4', 'Summary!C4'), 'write_pptx',
        'stat-callout', 'title · 28 pt', 'body · 18 pt', L('公司模板.pptx', 'company-template.pptx'), '9 slides', '−18.4%', 'low_contrast_warnings',
        L('家居', 'Home'), 'chart: column', '#201e1d', 'margin 56', 'grid 12'];
      const fr = F.rng(2207);
      const frags = FRAG.map((s, i) => {
        const zz = fr(), Z = zz < 0.62 ? -950 + zz / 0.62 * 650 : 260 + (zz - 0.62) / 0.38 * 440;
        const X = -300 + fr() * 2600, Y = -250 + fr() * 1500;
        const e = div(world, { left: 0, top: 0, whiteSpace: 'nowrap', fontFamily: /[一-鿿]/.test(s) && s.length < 4 ? 'var(--serif)' : 'var(--mono)',
          fontSize: 15 + fr() * 6, color: 'var(--d-muted)' }, s);
        // depth of field without a filter layer per fragment: a text-shadow blur is far cheaper to paint
        const blur = Math.abs(Z + 150) / 200;
        if (blur > 0.6) st(e, { color: 'transparent', textShadow: `0 0 ${blur.toFixed(1)}px rgba(161,157,155,.95)` });
        place(e, X, Y, Z);
        return { e, Z, base: 0.14 + 0.18 * (1 - Math.abs(Z + 150) / 900), ph: fr() * 6.28 };
      });

      // ===== storyline (z 0) =====
      const svgW = h('svg', { class: 'full' }); world.append(svgW);
      const thread = K.thread(svgW, `M 40 ${RULE_Y} L 1890 ${RULE_Y}`, { width: 2.5 });
      const headX = (t) => 40 + 1850 * threadK(t);
      function threadK(t) { const k = prog(t, TH[0], TH[1]); return 0.55 * ease.inOutQuad(k) + 0.45 * k; }
      const wordT = COL.map((x) => { let t = TH[0]; while (t < TH[1] && headX(t) < x + 10) t += 0.005; return t; });
      const words = WORDS.map((w, c) => K.line(world, { x: COL[c] + 600, y: WORD_Y, size: 62, color: 'var(--d-fg)', maxW: 1200, align: 'left' }));
      const arrows = [0, 1].map((c) => {
        const x0 = COL[c] + (ZH ? 190 : 300), x1 = COL[c + 1] - 70, y = WORD_Y + 2;
        const p = h('path', { d: `M ${x0} ${y} L ${x1} ${y} M ${x1 - 11} ${y - 7} L ${x1} ${y} L ${x1 - 11} ${y + 7}`, fill: 'none', stroke: 'rgba(247,245,243,.42)', 'stroke-width': 1.6, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
        svgW.append(p);
        const len = x1 - x0 + 30; p.setAttribute('stroke-dasharray', len); p.setAttribute('stroke-dashoffset', len);
        let ta = TH[0]; while (ta < TH[1] && headX(ta) < x0) ta += 0.005;
        return { p, len, t: ta, t1: ta + (x1 - x0) / 1850 * (TH[1] - TH[0]) * 1.1 };
      });
      const tocNums = [], tocT = [];
      for (let i = 0; i < 9; i++) {
        const c = Math.floor(i / 3), r = i % 3;
        const ti = wordT[c] + 0.42 + r * 0.36;
        tocT.push(ti);
        F.event(ti, 'softkey', { i: i + 1 });
        const n = div(world, { left: 0, top: 0, fontFamily: 'var(--mono)', fontSize: 14, color: 'var(--d-muted)', opacity: 0 }, String(i + 1).padStart(2, '0'));
        place(n, COL[c], TOC_Y + r * TOC_PITCH + 3, 0);
        tocNums.push(n);
      }
      F.event(TH[0], 'thread', { dur: TH[1] - TH[0] });
      wordT.forEach((t, c) => F.event(t, 'word', { i: c }));

      // ===== the nine slides =====
      const slides = TITLES.map((title, i) => makeSlide(i, title));
      slides.forEach((s) => world.append(s.root));
      F.event(38.0, 'frames');
      slides.forEach((s, i) => F.event(tBuild(i) + 0.18, 'knock', { slide: i + 1 }));
      F.event(39.45, 'grow');
      F.event(T_THEME, 'chord');

      // ===== screen-space overlays =====
      const over = div(layer, { inset: 0, pointerEvents: 'none' });
      // A: thread pulled from the figure down through the layers
      const svgA = h('svg', { class: 'full' }); over.append(svgA);
      const pullHalo = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 12, 'stroke-linecap': 'round', opacity: 0.12 });
      const pull = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 2.5, 'stroke-linecap': 'round' });
      const pullHead = h('circle', { r: 5.5, fill: '#fff' });
      svgA.append(pullHalo, pull, pullHead);
      F.event(A0 + 0.12, 'silk'); F.event(42.9, 'cell'); F.event(43.3, 'silk'); F.event(43.9, 'tick');
      F.event(44.0, 'whoosh', { dur: 0.6 });
      F.event(B0 + 0.55, 'roll', { dur: 0.45 }); F.event(B0 + 1.0, 'check');
      F.event(45.72, 'lift');
      DROP_ORDER.forEach((s, r) => F.event(tDrop(s) + 0.42, 'paper', { gain: +(1 - r * 0.07).toFixed(2), i: r + 1 }));
      F.event(T_FOLD, 'collapse'); F.event(T_LAND, 'drop');

      // type scale readout (top-left) and template palette (top-right)
      const spec = div(over, { left: 34, top: 150, fontFamily: 'var(--mono)', fontSize: 15, color: 'var(--d-muted)', lineHeight: 1.9, opacity: 0 });
      const SPEC = [[40, L('标题页', 'Cover')], [28, L('页标题', 'Title')], [18, L('正文', 'Body')], [12, L('注释', 'Caption')]];
      const specRows = SPEC.map(([n, lab]) => {
        const r = div(spec, { position: 'relative', display: 'flex', gap: 14 });
        const num = h('span'); st(num, { color: 'var(--d-fg)', width: 64, textAlign: 'right' });
        const lb = h('span', { text: lab });
        r.append(num, lb); return { num, n };
      });
      const pal = div(over, { left: 1566, top: 150, width: 330, height: 110, opacity: 0 });
      const swR = F.rng(77);
      const sw = MESSY.map((c, i) => {
        const e = div(pal, { left: 0, top: 0, width: 26, height: 26, borderRadius: 4, background: c, boxShadow: '0 0 0 1px rgba(247,245,243,.12)' });
        return { e, c, sx: swR() * 300, sy: swR() * 60, sr: (swR() - 0.5) * 50, to: [0, 1, 1, 2, 3, 3][i] };
      });
      const palLabels = PALETTE.map((c, j) => div(pal, { left: j * 82 * 0.9, top: 40, fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--d-muted)', opacity: 0 }, c));

      // tool rows: an opened run (ChatLog ToolRunGroupView, dark theme)
      const tools = div(over, { left: 1560, top: 700, width: 336, borderRadius: 15, border: '1px solid var(--d-border)', overflow: 'hidden',
        background: 'rgba(35,33,32,.92)', fontSize: 15.5, color: 'var(--d-fg)', opacity: 0 });
      const TOOL_ROWS = [['Write PowerPoint', L('管理层汇报.pptx', 'management-briefing.pptx')], ['Add PowerPoint chart', null], ['Render PowerPoint preview', null]];
      const toolRows = TOOL_ROWS.map(([lab, obj], i) => {
        const r = div(tools, { position: 'relative', display: 'flex', alignItems: 'center', gap: 10, padding: '10px 15px', borderTop: i ? '1px solid var(--d-border)' : 'none' });
        const label = h('span'); st(label, { flex: '1', whiteSpace: 'nowrap', overflow: 'hidden' });
        const lt = h('span', { text: lab }); label.append(lt);
        if (obj) { const code = h('code', { text: obj }); st(code, { marginLeft: '6px', fontFamily: 'var(--mono)', fontSize: '0.85em', background: 'rgba(247,245,243,.12)', borderRadius: '4px', padding: '2px 5px' }); label.append(code); }
        const ch = h('span', { html: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>' });
        st(ch, { color: 'var(--d-muted)', display: 'inline-flex' });
        r.append(label, ch);
        return { r, label, state: '' };
      });
      const toolOut = div(tools, { position: 'relative', borderTop: '1px solid var(--d-border)', background: 'rgba(247,245,243,.07)', padding: '9px 15px',
        fontFamily: 'var(--mono)', fontSize: 13.5, lineHeight: 1.7, color: 'var(--d-muted)', whiteSpace: 'pre', height: 0, overflow: 'hidden' });
      toolOut.textContent = 'slides: 9\ntext_overlap_warnings: 0\nlow_contrast_warnings: 1';
      TOOLS.forEach((t, i) => F.event(t, 'row', { i }));

      // the deliverable chip that flies into sentence 3's box (light: it lands on the surface)
      const chip = K.fileChip(T('s2_out'), 'slides');
      st(chip, { position: 'absolute', left: 0, top: 0, transformOrigin: '50% 50%', opacity: 0, whiteSpace: 'nowrap' });
      over.append(chip);
      let chipW = 0, chipH = 0;
      // centre of the folded deck / chip on its way to sentence 3's box (the surface camera sits at ~1.2)
      const flyPos = (t) => {
        const sc3 = lerp(1.2, 1.23, prog(t, 49.5, 55.2)), k = tw(t, T_FLY, T_LAND, 'inOutCubic');
        const gx = 960 + (543 - 960) * sc3 + (chipW || 220) * sc3 / 2, gy = 540 + (509.5 - 540) * sc3;
        return { x: lerp(960, gx, k), y: lerp(452, gy, k) - Math.sin(Math.PI * k) * 46, k, sc3 };
      };

      // example tag
      const tag = div(over, { left: 44, top: 1024, fontFamily: 'var(--mono)', fontSize: 12.5, color: 'var(--d-muted)', border: '1px solid var(--d-border)',
        borderRadius: 999, padding: '3px 10px', letterSpacing: '.04em', opacity: 0 }, T('example'));

      const grainU = K.grain(layer, 0.04);

      // ---------- camera ----------
      const S2 = slides[1], S5 = slides[4];
      const inSlide = (i, ix, iy, s = S_GRID) => ({ x: GRID_C[Math.floor(i / 3)] + (ix - 480) * s, y: GRID_R[i % 3] + (iy - 270) * s });
      const NUM = inSlide(1, 476, 222), SUB = inSlide(4, ZH ? 400 : 470, 150);
      const EVC = { x: EV.x + EV.w / 2 - 20, y: EV.y + 80 };
      const KEYS = [
        { t: 33.0, x: 520, y: 430, d: -2200, rx: 12, ry: 0 },
        { t: 34.7, x: 560, y: 436, d: 520, rx: 3, ry: -2, e: 'outCubic' },
        { t: 35.45, x: 860, y: 440, d: 560, rx: 2.5, ry: -1, e: 'inOutQuad' },
        { t: 36.2, x: 1330, y: 444, d: 580, rx: 2, ry: 1, e: 'linear' },
        { t: 37.55, x: 940, y: 448, d: 120, rx: 2, ry: 1.5, e: 'inOutCubic' },
        { t: 37.95, x: 944, y: 452, d: 140, rx: 2, ry: 1.5, e: 'linear' },
        { t: 39.1, x: 1142, y: 574, d: -760, rx: 6, ry: -4, e: 'inOutCubic' },
        { t: 41.2, x: 1146, y: 572, d: -700, rx: 5, ry: 2.5, e: 'inOutQuad' },
        { t: 42.0, x: NUM.x, y: NUM.y, d: 1180, rx: 0, ry: 0, e: 'inOutCubic' },
        { t: 42.2, x: NUM.x + 4, y: NUM.y + 6, d: 1200, e: 'linear' },
        { t: 43.0, x: C4.cx + 60, y: C4.cy + 10, d: 1480, rx: 0, ry: 0, e: 'inOutCubic' },
        { t: 43.25, x: C4.cx + 70, y: C4.cy + 30, d: 1500, e: 'linear' },
        { t: 43.95, x: EVC.x, y: EVC.y, d: 1580, e: 'inOutCubic' },
        { t: 44.05, x: EVC.x, y: EVC.y - 4, d: 1590, e: 'linear' },
        { t: 44.62, x: SUB.x, y: SUB.y + 40, d: 1300, rx: 0, ry: 0, e: 'inOutCubic' },
        { t: 45.68, x: SUB.x + 12, y: SUB.y + 40, d: 1330, e: 'linear' },
        { t: 46.45, x: 960, y: 540, d: 0, rx: 0, ry: 0, e: 'inOutCubic' },
        { t: 51, x: 960, y: 540, d: 0 },
      ];

      // ---------- per-frame ----------
      let lastFocus = null;
      return (lt, t) => {
        if (!drawn && document.fonts.status === 'loaded') drawAll();
        const c = camPath(KEYS, t);
        world.style.transform = `translate(960px,540px) translateZ(${c.d}px) rotateX(${c.rx}deg) rotateY(${c.ry}deg) translate(${-c.x}px,${-c.y}px)`;

        // workbook: far (pre-blurred) always, near (sharp) only in shot A
        const focusA = env(t, 42.35, 44.25, 0.55, 0.35);
        const wbO = lerp(0.42, 0.95, focusA) * (1 - 0.45 * tw(t, 45.8, 46.8));
        css(farC, { opacity: wbO * (1 - 0.6 * focusA) });
        css(nearC, { opacity: focusA * 0.98, display: focusA > 0.001 ? '' : 'none' });

        // fragments: gentle drift; fade those the dolly brings too close
        for (const f of frags) {
          const dist = P - f.Z - c.d;
          const o = f.base * clamp((dist - 500) / 700) * (0.55 + 0.45 * Math.sin(t * 0.6 + f.ph) ** 2) * (1 - 0.6 * tw(t, 45.8, 46.6));
          css(f.e, { opacity: o.toFixed(3), display: o < 0.004 ? 'none' : '' });
        }

        // storyline
        const outS = tw(t, 37.95, 38.6, 'inOutQuad');
        thread.update(threadK(t), { headOn: t < TH[1] + 0.05 });
        css(svgW, { opacity: 1 - outS });
        words.forEach((w, c2) => {
          w.update(WORDS[c2], prog(t, wordT[c2] - 0.05, wordT[c2] + 0.45), outS);
        });
        arrows.forEach((a) => a.p.setAttribute('stroke-dashoffset', a.len * (1 - tw(t, a.t, a.t1, 'inOutQuad'))));
        tocNums.forEach((n, i) => css(n, { opacity: (tw(t, tocT[i], tocT[i] + 0.3, 'outQuad') * (1 - tw(t, 37.9, 38.3))).toFixed(3) }));

        // slides
        slides.forEach((s, i) => s.update(t, c));

        // shot A: highlight, pull, cell, evidence
        const ring = tw(t, A0 - 0.15, A0 + 0.2, 'outCubic') * (1 - tw(t, 43.6, 44.0));
        S2.ring.setAttribute('stroke-dashoffset', 520 * (1 - ring));
        css(S2.ring, { opacity: ring > 0 ? 1 : 0 });
        const k1 = tw(t, A0 + 0.12, 42.9, 'inOutQuad'), k2 = tw(t, 43.3, 43.9, 'inOutQuad');
        const pullO = 1 - tw(t, 44.05, 44.4);
        if (t > A0 && pullO > 0) {
          const st0 = layer.getBoundingClientRect(), sc = st0.width / 1920;
          const pt = (e) => { const r = e.getBoundingClientRect(); return [(r.left + r.width / 2 - st0.left) / sc, (r.top + r.height / 2 - st0.top) / sc]; };
          const a0 = pt(S2.numAnchor), a1 = pt(c4anchor), a2 = pt(evAnchor);
          const d1 = `M ${a0[0]} ${a0[1]} C ${a0[0]} ${a0[1] + 260}, ${a1[0] - 40} ${a1[1] - 300}, ${a1[0]} ${a1[1]}`;
          const d2 = ` C ${a1[0]} ${a1[1] + 180}, ${a2[0] - 160} ${a2[1] - 40}, ${a2[0]} ${a2[1]}`;
          pull.setAttribute('d', d1); const L1 = pull.getTotalLength();
          pull.setAttribute('d', d1 + d2); const LL = pull.getTotalLength();
          const drawnL = k1 * L1 + k2 * (LL - L1);
          for (const e of [pull, pullHalo]) { e.setAttribute('d', d1 + d2); e.setAttribute('stroke-dasharray', `${drawnL} ${LL + 10}`); }
          const hp = pull.getPointAtLength(drawnL);
          F.attr(pullHead, { cx: hp.x, cy: hp.y, opacity: (k1 > 0 && k1 < 1) || (k2 > 0 && k2 < 1) ? 1 : 0 });
          css(svgA, { opacity: pullO, display: '' });
        } else css(svgA, { display: 'none' });
        const cellK = tw(t, 42.85, 43.1, 'outCubic') * (1 - tw(t, 44.1, 44.4));
        css(c4box, { opacity: cellK });
        css(c4tag, { opacity: cellK, transform: `translate3d(${C4.x + C4.w + 18}px,${C4.y - 2 - 8 * (1 - cellK)}px,${ZW + 4}px)` });
        const evK = tw(t, 43.0, 43.45, 'outCubic') * (1 - tw(t, 44.1, 44.45));
        css(ev, { opacity: evK });
        const hit = tw(t, 43.85, 44.05);
        css(evHit, { color: hit > 0.5 ? 'var(--d-fg)' : 'var(--d-muted)', background: `rgba(75,143,227,${(0.22 * hit).toFixed(3)})`, borderRadius: '4px', margin: '0 -6px', padding: '0 6px' });

        // shot B: the low-contrast subtitle, measured and fixed
        S5.contrast(t);

        // overlays during the layout build
        const ovO = env(t, 38.6, 41.5, 0.5, 0.45);
        css(spec, { opacity: ovO * 0.95, display: ovO > 0 ? '' : 'none' });
        specRows.forEach((r, i) => {
          const settle = prog(t, 38.9 + i * 0.12, 40.3 + i * 0.1);
          const jit = settle < 1 ? Math.round((F.hash(Math.floor(t * 18) + i * 31) - 0.5) * 16 * (1 - settle)) : 0;
          const s = `${r.n + jit} pt`;
          if (r.num.textContent !== s) r.num.textContent = s;
          r.num.style.color = settle < 1 ? 'var(--d-muted)' : 'var(--d-fg)';
        });
        css(pal, { opacity: ovO * 0.95, display: ovO > 0 ? '' : 'none' });
        const kc = tw(t, T_THEME - 0.75, T_THEME, 'inOutCubic');
        sw.forEach((s) => {
          const tx2 = s.to * 82 * 0.9, ty2 = 0;
          css(s.e, { transform: `translate(${lerp(s.sx, tx2, kc)}px,${lerp(s.sy, ty2, kc)}px) rotate(${lerp(s.sr, 0, kc)}deg)`,
            background: kc >= 1 ? PALETTE[s.to] : mix(s.c, PALETTE[s.to], ease.inQuad(kc)), width: lerp(26, 64, tw(t, T_THEME, T_THEME + 0.3, 'outCubic')) });
        });
        palLabels.forEach((l, j) => css(l, { opacity: tw(t, T_THEME + 0.1 + j * 0.05, T_THEME + 0.5 + j * 0.05) }));

        const toolO = env(t, TOOLS[0] - 0.1, 41.75, 0.3, 0.4);
        css(tools, { opacity: toolO, display: toolO > 0 ? '' : 'none', transform: `translateY(${(1 - ease.outCubic(clamp((t - TOOLS[0] + 0.1) / 0.4))) * 14}px)` });
        toolRows.forEach((r, i) => {
          const on = t >= TOOLS[i] - 0.05, running = on && t < TOOLS[i] + 0.8;
          css(r.r, { display: on ? 'flex' : 'none' });
          const stt = running ? 'run' : 'done';
          if (on && r.state !== stt) {
            r.state = stt;
            if (running) st(r.label, { background: 'linear-gradient(90deg, #a19d9b 0%, #a19d9b 35%, #f7f5f3 50%, #a19d9b 65%, #a19d9b 100%) 0 0 / 250% 100%', webkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' });
            else st(r.label, { background: 'none', color: 'var(--d-fg)' });
          }
          if (running) r.label.style.backgroundPosition = `${100 - ((t - TOOLS[i]) / 0.8) * 100 * 1.3}% 0`;
        });
        const outH = tw(t, TOOLS[2] + 0.75, TOOLS[2] + 1.05, 'outCubic');
        css(toolOut, { height: 76 * outH, padding: outH > 0 ? '9px 15px' : '0 15px', display: outH > 0 ? '' : 'none' });

        // chip flight into sentence 3's input box
        if (!chipW && t > 46) { chip.style.display = ''; chipW = chip.offsetWidth; chipH = chip.offsetHeight; }
        const chipO = tw(t, T_FLY + 0.22, T_FLY + 0.5, 'outQuad');
        if (chipO > 0) {
          const fp = flyPos(t);
          const s = lerp(2.0, fp.sc3, ease.outCubic(fp.k));
          css(chip, { opacity: chipO, display: '', transform: `translate(${fp.x - chipW / 2}px,${fp.y - chipH / 2}px) scale(${s})` });
        } else css(chip, { display: 'none' });

        css(tag, { opacity: 0.75 * env(t, 34.6, 49.3, 0.6, 0.4) });
        grainU(t);
      };

      // =====================================================================
      function makeSlide(i, title) {
        const root = h('div');
        st(root, { position: 'absolute', left: 0, top: 0, width: 960, height: 540, transformOrigin: '480px 270px' });
        const paper = div(root, { inset: 0, background: '#ffffff' });
        const edge = div(root, { inset: 0, border: '1px solid rgba(0,0,0,0)' });
        const guides = h('svg', { width: 960, height: 540 });
        st(guides, { position: 'absolute', left: 0, top: 0, overflow: 'visible' });
        const content = div(root, { inset: 0 });
        root.append(guides);
        root.append(edge);
        // layout guides (blue: the agent's construction lines)
        const gl = [];
        const gLine = (x1, y1, x2, y2, w = 1) => { const l = h('line', { x1, y1, x2, y2, stroke: '#4b8fe3', 'stroke-width': w, 'stroke-dasharray': '1000', 'stroke-dashoffset': 1000 }); guides.append(l); gl.push(l); };
        gLine(56, 0, 56, 540, 1.4); gLine(904, 0, 904, 540, 1.4); gLine(0, 44, 960, 44, 1.4); gLine(0, 500, 960, 500, 1.4);
        for (let k = 1; k < 12; k++) { const x = 56 + k * (848 / 12); gLine(x, 44, x, 500, 0.8); }
        const blocks = [], bars = [], accents = [];
        const block = (e, dt = 0, dy = -14) => { blocks.push({ e, dt, dy }); e.style.opacity = 0; return e; };
        const accent = (e, prop = 'background') => { accents.push({ e, prop, m: MESSY[(i * 2 + accents.length) % 6] }); return e; };
        const cover = i === 0;

        // template chrome
        if (!cover) {
          block(accent(div(content, { left: 56, top: 40, width: 34, height: 4 })), 0, -6);
          block(div(content, { left: 56, top: 500, width: 848, height: 1, background: TPL.rule }), 0.02, 0);
          block(tx(content, DECK, 56, 508, { size: 11, color: TPL.gray }), 0.04, 0);
          block(tx(content, String(i + 1).padStart(2, '0'), 856, 508, { size: 11, color: TPL.gray, font: 'var(--mono)', w: 48, align: 'right' }), 0.04, 0);
        } else {
          block(accent(div(content, { left: 56, top: 196, width: 5, height: 120 })), 0, 0);
        }
        // the title (it is the TOC line until the frame forms around it)
        const tEl = tx(content, title, TITLE_AT[0], TITLE_AT[1], { size: 28, weight: 500, w: cover ? 560 : 820, wrap: true, lh: 1.25 });
        tEl.style.transformOrigin = '0 0';

        const ret = { root, paper, edge, guides, gl, tEl, blocks, bars, accents };
        const B = SLIDE_BODY[i];
        if (B) B(content, { block, accent, bars, ret });

        let cache = {};
        ret.update = (t, cam) => {
          // ---- pose ----
          const c = Math.floor(i / 3), r = i % 3;
          const toc = { x: COL[c] + 38 + (480 - TITLE_AT[0]) * S_TOC, y: TOC_Y + r * TOC_PITCH + (270 - TITLE_AT[1]) * S_TOC, z: 0, s: S_TOC, rot: 0 };
          const grid = { x: GRID_C[c], y: GRID_R[r], z: ZOFF[i], s: S_GRID, rot: 0 };
          const fanA = FAN_ANG[RANK[i]];
          const fan = { x: 960 + fanA * 15, y: 452 + Math.abs(fanA) * 3.2, z: RANK[i] * 4, s: S_FAN, rot: fanA };
          let p;
          const kg = tw(t, tGrid(i), tGrid(i) + 0.9, 'inOutCubic');
          const td = tDrop(i);
          if (t < 45.7) p = mixPose(toc, grid, kg);
          else if (t < td) { const kl = ease.inCubic(prog(t, 45.7 + 0.03 * (8 - i), 46.12)); p = { ...grid, y: grid.y - 1700 * kl, z: grid.z + 200 * kl }; }
          else {
            const kd = prog(t, td, td + 0.52);
            const above = { x: fan.x + fanA * 8, y: -560, z: fan.z, s: S_FAN, rot: fanA * 2.4 + (RANK[i] % 2 ? 7 : -7) };
            p = { x: lerp(above.x, fan.x, ease.outCubic(kd)), y: lerp(above.y, fan.y, ease.outCubic(kd)), z: fan.z, s: S_FAN, rot: lerp(above.rot, fan.rot, ease.outQuart(kd)) };
            const breathe = 1 + 0.012 * tw(t, 46.6, 49.1, 'inOutQuad');
            p.s *= breathe; p.x = 960 + (p.x - 960) * breathe; p.y = 452 + (p.y - 452) * breathe;
            // fold the fan into a stack, then fly the stack into the chip
            const kf = tw(t, T_FOLD, T_FOLD + 0.45, 'inOutCubic');
            if (kf > 0) { const stk = { x: 960 + (RANK[i] - 8) * 1.4, y: 452 + (8 - RANK[i]) * 1.6, z: fan.z, s: S_FAN * breathe, rot: 0 }; p = mixPose(p, stk, kf); }
            if (t > T_FLY) {
              const fp = flyPos(t);
              p = { ...p, x: fp.x + (p.x - 960) * (1 - fp.k), y: fp.y + (p.y - 452) * (1 - fp.k), s: p.s * lerp(1, 0.09, ease.inOutCubic(Math.min(1, fp.k * 1.25))) };
            }
          }
          const vis = t < 45.7 || t >= td ? 1 - prog(t, T_FLY + 0.3, T_FLY + 0.5) : (prog(t, 45.7, 46.12) < 1 ? 1 : 0);
          const key = `${p.x.toFixed(2)},${p.y.toFixed(2)},${p.z.toFixed(1)},${p.s.toFixed(4)},${p.rot.toFixed(2)}`;
          if (key !== cache.key) { cache.key = key; root.style.transform = `translate3d(${p.x - 480}px,${p.y - 270}px,${p.z}px) rotate(${p.rot}deg) scale(${p.s})`; }
          css(root, { opacity: vis.toFixed(3), display: vis > 0.001 ? '' : 'none' });

          // depth of field: blur by distance from the plane in focus
          const dist = P - p.z - cam.d;
          const focusDist = t < 41.3 ? P - cam.d : t < 45.75 ? P - 0 - cam.d : dist;
          let blur = Math.abs(dist - focusDist) / 140;
          if (t > 41.3 && t < 45.75) {
            const kin = tw(t, 41.3, 41.9);
            let b = 2.5;
            if (i === 1) b = 2.5 * tw(t, 43.0, 43.4);
            if (i === 4) b = 2.5 * (1 - tw(t, 44.0, 44.5));
            blur = lerp(Math.abs(ZOFF[i]) / 140, b, kin);
          }
          blur = blur < 0.6 ? 0 : Math.round(blur * 2) / 2;
          if (blur !== cache.blur) { cache.blur = blur; root.style.filter = blur > 0.2 ? `blur(${blur}px)` : 'none'; }

          // ---- paper, title colour, edge ----
          // the page opens out of the title line: a white strip behind the title, then the full 16:9
          const kp = tw(t, tGrid(i) + 0.3, tGrid(i) + 0.95, 'inOutCubic');
          const ks = tw(t, tGrid(i) + 0.02, tGrid(i) + 0.22, 'outQuad');
          if (cache.th == null && t > tGrid(i)) cache.th = tEl.offsetHeight;
          const lineB = 540 - (58 + (cache.th || 35) + 8);
          const clip = kp >= 1 ? 'none' : `inset(${lerp(50, 0, kp).toFixed(1)}px ${lerp(40, 0, kp).toFixed(1)}px ${lerp(lineB, 0, kp).toFixed(1)}px ${lerp(46, 0, kp).toFixed(1)}px)`;
          if (clip !== cache.clip) { cache.clip = clip; paper.style.clipPath = clip; }
          css(paper, { opacity: ks.toFixed(3), display: ks > 0 ? '' : 'none' });
          const tc = ks >= 1 ? TPL.ink : mix('#f7f5f3', TPL.ink, ks);
          if (tc !== cache.tc) { cache.tc = tc; tEl.style.color = tc; }
          const kd2 = t >= td ? tw(t, td + 0.1, td + 0.4) : 0;
          const eo = kd2 > 0 ? `1.5px solid ${mix('#e4e1dd', TPL.accent, kd2)}` : '1px solid rgba(0,0,0,0)';
          if (eo !== cache.eo) { cache.eo = eo; edge.style.border = eo; }
          const sh = t >= td ? '0 26px 70px rgba(0,0,0,.5)' : (kp >= 1 ? '0 10px 28px rgba(0,0,0,.35)' : 'none');
          if (sh !== cache.sh) { cache.sh = sh; root.style.boxShadow = sh; }
          const kT = tw(t, tocT[i], tocT[i] + 0.45, 'outCubic');
          const kc = cover ? tw(t, tGrid(i) + 0.2, tGrid(i) + 0.9, 'inOutCubic') : 0;
          css(tEl, { opacity: kT.toFixed(3), transform: `translate(${24 * kc}px,${138 * kc + (1 - kT) * 12}px) scale(${lerp(1, 40 / 28, kc)})` });

          // ---- guides ----
          const kgd = tw(t, tGrid(i) + 0.85, tGrid(i) + 1.4, 'outCubic'), gout = 1 - tw(t, 40.5, 41.1);
          const gk = (kgd * 1000).toFixed(0);
          if (gk !== cache.gk) { cache.gk = gk; gl.forEach((l, j) => l.setAttribute('stroke-dashoffset', 1000 - Math.min(1000, kgd * 1000 * (1 + j * 0.04)))); }
          css(guides, { opacity: (0.55 * gout).toFixed(3), display: gout > 0 && kgd > 0 ? '' : 'none' });

          // ---- blocks drop in, bars grow, accents unify ----
          const tb = tBuild(i);
          blocks.forEach((b) => {
            const k = prog(t, tb + b.dt, tb + b.dt + 0.32);
            const kk = Math.round(k * 200) / 200;
            if (kk !== b.k) { b.k = kk; b.e.style.opacity = ease.outQuad(kk); b.e.style.transform = kk < 1 ? `translateY(${(1 - ease.outCubic(kk)) * b.dy}px)` : 'none'; }
          });
          bars.forEach((b) => {
            const k = Math.round(ease.outCubic(prog(t, b.t, b.t + b.dur)) * 300) / 300;
            if (k !== b.k) { b.k = k; b.e.style.transform = b.dir === 'x' ? `scaleX(${k})` : `scaleY(${k})`; if (b.lab) b.lab.style.opacity = clamp((k - 0.8) / 0.2); }
          });
          const ka = tw(t, T_THEME - 0.3, T_THEME, 'inOutQuad');
          const kak = Math.round(ka * 50) / 50;
          if (kak !== cache.ka) { cache.ka = kak; accents.forEach((a) => { a.e.style[a.prop] = mix(a.m, a.to || TPL.accent, kak); }); }
        };
        return ret;
      }
    },
  });

  function mixPose(a, b, k) { return { x: lerp(a.x, b.x, k), y: lerp(a.y, b.y, k), z: lerp(a.z, b.z, k), s: Math.exp(lerp(Math.log(a.s), Math.log(b.s), k)), rot: lerp(a.rot, b.rot, k) }; }

  // ---------- slide bodies (960 × 540, 1 px = 1 pt) ----------
  const bt = (i) => 38.85 + 0.12 * [4, 1, 3, 7, 0, 5, 2, 8, 6].indexOf(i);
  function vbar(parent, ctx, x, base, w, hgt, color, t, lab) {
    const e = div(parent, { left: x, top: base - hgt, width: w, height: hgt, background: color, transformOrigin: '50% 100%', transform: 'scaleY(0)' });
    ctx.bars.push({ e, t, dur: 0.6, dir: 'y', lab });
    if (lab) lab.style.opacity = 0;
    return e;
  }
  function hbar(parent, ctx, x, y, len, hgt, color, t, lab) {
    const e = div(parent, { left: x, top: y, width: len, height: hgt, background: color, transformOrigin: '0 50%', transform: 'scaleX(0)' });
    ctx.bars.push({ e, t, dur: 0.6, dir: 'x', lab });
    if (lab) lab.style.opacity = 0;
    return e;
  }
  const SLIDE_BODY = [
    // 1 · cover
    (p, { block }) => {
      block(tx(p, L('管理层汇报 · 2026 年 10 月', 'Management briefing · October 2026'), 80, 300, { size: 18, color: TPL.gray }), 0.1);
      block(tx(p, L('数据来源：销售下滑分析.xlsx（2025-10 至 2026-09）', 'Source: sales-decline-analysis.xlsx (Oct 2025 – Sep 2026)'), 80, 462, { size: 12, color: TPL.gray }), 0.2);
    },
    // 2 · the answer first: three stat callouts
    (p, ctx) => {
      const { block, ret } = ctx;
      const cards = [
        [L('12 个月销售额', '12-month sales'), L('¥3.84 亿', '¥384M'), L('2025-10 至 2026-09', 'Oct 2025 – Sep 2026'), TPL.gray],
        [L('同比', 'Year on year'), '−6.8%', L('40 家门店合计', 'All 40 stores'), TPL.red],
        [L('来自家居品类折扣', 'From Home discounts'), L('约 70%', '≈ 70%'), L('折扣率 12% → 19%', 'Discount rate 12% → 19%'), TPL.gray],
      ];
      cards.forEach(([lab, num, sub, sc], j) => {
        const x = 56 + j * 292;
        const card = block(div(p, { left: x, top: 150, width: 268, height: 158, border: `1px solid ${TPL.rule}`, borderRadius: 3 }), 0.12 + j * 0.1, -18);
        tx(card, lab, 19, 17, { size: 12, color: TPL.gray });
        const n = tx(card, num, 18, 40, { size: 40, weight: 500, color: TPL.ink, ls: '-0.01em' });
        tx(card, sub, 19, 114, { size: 12, color: sc });
        if (j === 1) {
          ret.numAnchor = div(card, { left: 60, top: 95, width: 2, height: 2 });
          const ring = h('svg', { width: 200, height: 90 }); st(ring, { position: 'absolute', left: 6, top: 34, overflow: 'visible' });
          const rr = h('rect', { x: 0, y: 0, width: 168, height: 64, rx: 8, fill: 'none', stroke: '#4b8fe3', 'stroke-width': 2.5, 'stroke-dasharray': 520, 'stroke-dashoffset': 520 });
          ring.append(rr); card.append(ring); ret.ring = rr;
        }
      });
      block(tx(p, L('下滑的七成来自家居品类折扣加深；客流与新店都不是主因。', 'Seven-tenths of the decline comes from deeper Home discounts; footfall and new stores are not the cause.'),
        56, 350, { size: 18, color: TPL.body, w: 848, wrap: true, lh: 1.5 }), 0.45);
    },
    // 3 · not footfall: horizontal bars
    (p, ctx) => {
      const { block } = ctx;
      const t = bt(2) + 0.35;
      const rows = [[L('客流', 'Footfall'), 1.2, TPL.bar], [L('客单价', 'Avg. ticket'), 5.7, null]];
      block(div(p, { left: 160, top: 168, width: 1, height: 150, background: TPL.gray }), 0.1, 0);
      rows.forEach(([lab, v, col], j) => {
        const y = 186 + j * 70;
        block(tx(p, lab, 56, y + 4, { size: 18, color: TPL.body, w: 96 }), 0.1);
        const val = tx(p, `−${v.toFixed(1)}%`, 172 + v * 55, y + 4, { size: 18, weight: 500 });
        const b = hbar(p, ctx, 161, y, v * 55, 34, col || TPL.accent, t + j * 0.12, val);
        if (!col) ctx.accent(b);
      });
      block(tx(p, L('同比变化 · 2025-10 至 2026-09', 'Year-on-year change · Oct 2025 – Sep 2026'), 56, 340, { size: 12, color: TPL.gray }), 0.2);
      block(div(p, { left: 600, top: 168, width: 1, height: 150, background: TPL.rule }), 0.1, 0);
      block(tx(p, L('客流基本持平；下滑来自每一单买得更少。', 'Footfall barely moved; each basket got smaller.'), 630, 186, { size: 18, color: TPL.body, w: 270, wrap: true, lh: 1.55 }), 0.25);
    },
    // 4 · not new stores: two columns
    (p, ctx) => {
      const { block } = ctx;
      const t = bt(3) + 0.35, base = 432;
      block(div(p, { left: 96, top: base, width: 340, height: 1, background: TPL.gray }), 0.08, 0);
      [[L('全部 40 店', 'All 40 stores'), 6.8, TPL.bar], [L('剔除 6 家新店', 'Without 6 new'), 6.1, null]].forEach(([lab, v, col], j) => {
        const x = 130 + j * 160;
        const val = tx(p, `−${v.toFixed(1)}%`, x - 10, base - v * 34 - 30, { size: 18, weight: 500, w: 120, align: 'center' });
        const b = vbar(p, ctx, x, base, 100, v * 34, col || TPL.accent, t + j * 0.12, val);
        if (!col) ctx.accent(b);
        block(tx(p, lab, x - 30, base + 10, { size: 12, color: TPL.gray, w: 160, align: 'center' }), 0.15);
      });
      block(tx(p, '−6.1%', 560, 168, { size: 40, weight: 500 }), 0.2);
      block(tx(p, L('剔除 2026 年新开 6 店后的同比', 'YoY without the 6 stores opened in 2026'), 562, 222, { size: 12, color: TPL.gray }), 0.25);
      block(tx(p, L('新店只解释了 0.7 个百分点。', 'New stores explain only 0.7 points.'), 562, 268, { size: 18, color: TPL.body, w: 340, wrap: true, lh: 1.5 }), 0.3);
    },
    // 5 · discounts: native column chart + the low-contrast subtitle
    (p, ctx) => {
      const { block, ret } = ctx;
      const t = bt(4) + 0.3, base = 444;
      const sub = block(tx(p, L('家居品类月度折扣率（%），2025-10 至 2026-09', 'Home category monthly discount rate (%), Oct 2025 – Sep 2026'), 56, 104, { size: 14, color: '#9a9a9a' }), 0.05, -8);
      [5, 10, 15, 20].forEach((v) => {
        block(div(p, { left: 96, top: base - v * 13, width: 520, height: 1, background: '#efedea' }), 0.06, 0);
        block(tx(p, String(v), 56, base - v * 13 - 8, { size: 12, color: TPL.gray, w: 28, align: 'right' }), 0.06, 0);
      });
      block(div(p, { left: 96, top: base, width: 520, height: 1, background: TPL.gray }), 0.06, 0);
      const vals = [12.0, 12.4, 12.9, 13.6, 14.2, 15.0, 15.8, 16.6, 17.3, 18.0, 18.6, 19.0];
      const mon = ['10', '11', '12', '1', '2', '3', '4', '5', '6', '7', '8', '9'];
      vals.forEach((v, j) => {
        const x = 106 + j * 42.5;
        const lab = j === 0 || j === 11 ? tx(p, `${v.toFixed(0)}%`, x - 8, base - v * 13 - 22, { size: 12, weight: 500, w: 46, align: 'center' }) : null;
        const b = vbar(p, ctx, x, base, 30, v * 13, j === 11 ? TPL.accent : TPL.bar, t + j * 0.045, lab);
        if (j === 11) ctx.accent(b);
        block(tx(p, mon[j], x - 5, base + 8, { size: 12, color: TPL.gray, w: 40, align: 'center' }), 0.1, 0);
      });
      block(div(p, { left: 660, top: 160, width: 1, height: 284, background: TPL.rule }), 0.12, 0);
      block(tx(p, '−18.4%', 690, 158, { size: 40, weight: 500 }), 0.2);
      block(tx(p, L('家居品类销售同比', 'Home category sales, YoY'), 692, 212, { size: 12, color: TPL.gray }), 0.25);
      block(tx(p, L('折扣率从 12% 升至 19%，销售反而下滑。', 'Discount rate up from 12% to 19%; sales still fell.'), 692, 258, { size: 18, color: TPL.body, w: 210, wrap: true, lh: 1.55 }), 0.3);
      // shot B overlay: ring + contrast readout (the agent's annotation, dark UI)
      const ringSvg = h('svg', { width: 960, height: 540 }); st(ringSvg, { position: 'absolute', left: 0, top: 0, overflow: 'visible' });
      const subW = ZH ? 300 : 420;
      const rr = h('rect', { x: 48, y: 98, width: subW + 18, height: 30, rx: 6, fill: 'none', stroke: '#4b8fe3', 'stroke-width': 2, 'stroke-dasharray': 1000, 'stroke-dashoffset': 1000 });
      ringSvg.append(rr); p.append(ringSvg);
      const read = div(p, { left: subW + 82, top: 96, display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderRadius: 7, background: '#232120',
        color: '#f7f5f3', fontFamily: 'var(--mono)', fontSize: 14, whiteSpace: 'nowrap', opacity: 0, boxShadow: '0 6px 18px rgba(0,0,0,.18)' });
      const sw1 = h('span'); st(sw1, { width: 12, height: 12, borderRadius: 3, background: '#9a9a9a', boxShadow: '0 0 0 1px rgba(255,255,255,.25)' });
      const ratio = h('span', { text: '2.8 : 1' });
      const mark = h('span', { text: '✓' }); st(mark, { color: '#8fd19f', display: 'none' });
      read.append(sw1, ratio, mark);
      const warn = div(p, { left: subW + 82, top: 132, fontFamily: 'var(--mono)', fontSize: 11.5, color: '#3d3936', whiteSpace: 'nowrap', opacity: 0, padding: '3px 8px',
        background: 'rgba(32,30,29,.06)', borderRadius: 5 }, 'low_contrast_warnings: 1');
      let last = '';
      ret.contrast = (t) => {
        const B0 = 44.6;
        const kr = tw(t, B0 + 0.05, B0 + 0.45, 'inOutQuad'), out = 1 - tw(t, 45.55, 45.8);
        rr.setAttribute('stroke-dashoffset', 1000 * (1 - kr));
        css(ringSvg, { opacity: out.toFixed(3) });
        const kread = tw(t, B0 + 0.3, B0 + 0.5, 'outCubic') * out;
        css(read, { opacity: kread.toFixed(3), transform: `translateY(${(1 - kread) * 6}px)` });
        css(warn, { opacity: (tw(t, B0 + 0.4, B0 + 0.6) * out).toFixed(3) });
        const kfix = tw(t, B0 + 0.55, B0 + 0.95, 'inOutQuad');
        const col = kfix >= 1 ? '#5c5c5c' : mix('#9a9a9a', '#5c5c5c', kfix);
        const v = lerp(2.8, 6.9, kfix);
        const roll = kfix > 0 && kfix < 1 ? (v + (F.hash(Math.floor(t * 30)) - 0.5) * 0.3).toFixed(1) : v.toFixed(1);
        const key = col + roll;
        if (key !== last) {
          last = key; sub.style.color = col; sw1.style.background = col; ratio.textContent = `${roll} : 1`;
          mark.style.display = kfix >= 1 ? '' : 'none';
          warn.textContent = kfix >= 1 ? 'low_contrast_warnings: 1 → 0   text_overlap_warnings: 0' : 'low_contrast_warnings: 1';
        }
      };
    },
    // 6 · concentrated in South China: horizontal bars by region
    (p, ctx) => {
      const { block } = ctx;
      const t = bt(5) + 0.35;
      const rows = [[L('华南', 'South'), 9.6, true], [L('华北', 'North'), 6.2], [L('西南', 'Southwest'), 5.9], [L('华东', 'East'), 4.9]];
      block(div(p, { left: 160, top: 150, width: 1, height: 248, background: TPL.gray }), 0.08, 0);
      rows.forEach(([lab, v, hi], j) => {
        const y = 160 + j * 60;
        block(tx(p, lab, 56, y + 3, { size: 18, color: TPL.body, w: 96 }), 0.1);
        const val = tx(p, `−${v.toFixed(1)}%`, 172 + v * 32, y + 3, { size: 18, weight: hi ? 500 : 400 });
        const b = hbar(p, ctx, 161, y, v * 32, 32, hi ? TPL.accent : TPL.bar, t + j * 0.08, val);
        if (hi) ctx.accent(b);
      });
      block(tx(p, L('区域同比 · 剔除重复入账后', 'YoY by region · after removing duplicates'), 56, 420, { size: 12, color: TPL.gray }), 0.2);
      block(div(p, { left: 600, top: 150, width: 1, height: 248, background: TPL.rule }), 0.1, 0);
      block(tx(p, '8 / 10', 630, 150, { size: 40, weight: 500 }), 0.2);
      block(tx(p, L('华南下滑集中的门店', 'South China stores carrying the decline'), 632, 204, { size: 12, color: TPL.gray }), 0.25);
    },
    // 7 · data correction: a quiet table (rules only, numbers right-aligned)
    (p, { block }) => {
      const hd = [L('项目', 'Item'), L('修正前', 'Before'), L('修正后', 'After')];
      const rows = [
        [L('华东 3 月重复入账', 'East China March duplicate rows'), '2,316', '0'],
        [L('华东 3 月销售额', 'East China March sales'), L('高估 ¥412 万', '+¥4.12M'), L('已剔除', 'removed')],
        [L('华东 12 个月同比', 'East China 12-month YoY'), '−11.3%', '−4.9%'],
        [L('下滑最大的区域', 'Steepest-decline region'), L('华东', 'East'), L('华南', 'South')],
      ];
      const X = [56, 660, 904];
      const row = (cells, y, o, dt) => {
        block(tx(p, cells[0], X[0], y, o), dt);
        block(tx(p, cells[1], X[1] - 200, y, { ...o, w: 200, align: 'right' }), dt);
        block(tx(p, cells[2], X[2] - 200, y, { ...o, w: 200, align: 'right', weight: o.size === 18 ? 500 : 400 }), dt);
      };
      row(hd, 150, { size: 12, color: TPL.gray }, 0.05);
      block(div(p, { left: 56, top: 176, width: 848, height: 1.5, background: TPL.ink }), 0.08, 0);
      rows.forEach((r, j) => {
        row(r, 192 + j * 56, { size: 18, color: TPL.body }, 0.12 + j * 0.06);
        block(div(p, { left: 56, top: 232 + j * 56, width: 848, height: 1, background: TPL.rule }), 0.12 + j * 0.06, 0);
      });
    },
    // 8 · proposals (the third one is the tender)
    (p, ctx) => {
      const { block } = ctx;
      const items = [
        [L('收紧折扣授权', 'Tighten discount authority'), L('家居品类折扣率已从 12% 升至 19%', 'Home discount rate has risen from 12% to 19%')],
        [L('调整家居品类陈列', 'Re-plan Home category displays'), L('先在华南 8 家门店试行', 'Start with the 8 South China stores')],
        [L('参与「华南连锁渠道」2027 年度供应招标', 'Bid for the South China retail-chain 2027 supply tender'), L('以新渠道对冲华南下滑', 'A new channel to offset the South China decline')],
      ];
      items.forEach(([a, b], j) => {
        const y = 150 + j * 104;
        block(ctx.accent(tx(p, String(j + 1).padStart(2, '0'), 56, y - 4, { size: 28, weight: 500, font: 'var(--sans)' }), 'color'), 0.1 + j * 0.1);
        block(tx(p, a, 130, y, { size: 18, weight: 500 }), 0.12 + j * 0.1);
        block(tx(p, b, 130, y + 34, { size: 12, color: TPL.gray }), 0.14 + j * 0.1);
        if (j < 2) block(div(p, { left: 130, top: y + 76, width: 774, height: 1, background: TPL.rule }), 0.14 + j * 0.1, 0);
      });
    },
    // 9 · next steps: a three-point timeline
    (p, ctx) => {
      const { block } = ctx;
      block(div(p, { left: 56, top: 290, width: 848, height: 1.5, background: TPL.rule }), 0.05, 0);
      const pts = [
        [L('2026 年 11 月', 'Nov 2026'), L('收紧家居品类折扣授权', 'Tighten Home discount authority')],
        [L('2026 年 12 月', 'Dec 2026'), L('华南 8 店调整陈列', 'Re-plan displays in 8 South China stores')],
        [L('2027 年 1 月', 'Jan 2027'), L('提交招标响应', 'Submit the tender response')],
      ];
      pts.forEach(([d, s], j) => {
        const x = 56 + j * 300;
        block(ctx.accent(div(p, { left: x, top: 284, width: 13, height: 13, borderRadius: 7 })), 0.1 + j * 0.1, 0);
        block(tx(p, d, x, 246, { size: 12, color: TPL.gray }), 0.12 + j * 0.1);
        block(tx(p, s, x, 316, { size: 18, color: TPL.body, w: 260, wrap: true, lh: 1.45 }), 0.14 + j * 0.1);
      });
    },
  ];

  // ---------- the workbook (canvas) ----------
  const WB = (() => {
    const r = F.rng(4242);
    const regions = [L('华东', 'East'), L('华南', 'South'), L('华北', 'North'), L('西南', 'Southwest')];
    const cats = [L('家居', 'Home'), L('服饰', 'Apparel'), L('食品', 'Food'), L('数码', 'Digital'), L('美妆', 'Beauty'), L('日用', 'Daily')];
    const months = ['2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09'];
    const detail = [];
    for (let k = 0; k < 96; k++) {
      const m = 1 + Math.floor(r() * 12), d = 1 + Math.floor(r() * 28);
      const reg = Math.floor(r() * 4), qty = 1 + Math.floor(r() * 9), price = 39 + Math.floor(r() * 860);
      detail.push([`SO-26${String(m).padStart(2, '0')}-${String(Math.floor(r() * 99999)).padStart(5, '0')}`, `2026-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`,
        `S-${String(1 + reg * 10 + Math.floor(r() * 10)).padStart(2, '0')}`, regions[reg], cats[Math.floor(r() * 6)], String(qty), price.toFixed(2), (qty * price).toLocaleString('en-US', { minimumFractionDigits: 2 }), `${10 + Math.floor(r() * 10)}%`]);
    }
    const reg = regions.map((n) => [n, ...months.map(() => (720 + r() * 170).toFixed(1))]);
    const catm = cats.map((n) => [n, ...months.slice(0, 9).map(() => (200 + r() * 420).toFixed(1))]);
    const yoy = [-9.6, -6.2, -5.9, -4.9];
    const stores = [];
    for (let k = 0; k < 40; k++) { const rg = Math.floor(k / 10); stores.push([`S-${String(k + 1).padStart(2, '0')}`, regions[rg], (yoy[[1, 2, 3, 0][rg]] + (r() - 0.5) * 7).toFixed(1)]); }
    stores.sort((a, b) => +a[2] - +b[2]);
    return { regions, cats, months, detail, reg, catm, stores };
  })();
  function drawWorkbookText() {
    return '清洗明细区域×月份品类×月份门店排名结论指标说明数值合计同比订单号日期门店品类数量单价金额折扣客流客单价剔除新店后家居折扣率销售额华东华南华北西南服饰食品数码美妆日用排名个月→的';
  }
  function drawWorkbook(cv, R, blurPx) {
    const ctx = cv.getContext('2d');
    let tgt = ctx, off = null;
    if (blurPx) { off = document.createElement('canvas'); off.width = cv.width; off.height = cv.height; tgt = off.getContext('2d'); }
    const g = tgt;
    g.setTransform(1, 0, 0, 1, 0, 0); g.clearRect(0, 0, cv.width, cv.height);
    g.setTransform(R.k, 0, 0, R.k, -R.x0 * R.k, -R.y0 * R.k);
    const MONO = '"IBM Plex Mono", "Noto Sans SC", monospace', SANS = '"IBM Plex Sans", "Noto Sans SC", sans-serif';
    const grid = 'rgba(247,245,243,0.075)', txt = 'rgba(161,157,155,0.8)', head = 'rgba(161,157,155,0.55)';
    function sheet(x, y, name, cols, rows, o = {}) {
      const rowH = o.rowH || 30, W = 44 + cols.reduce((a, c) => a + c.w, 0), H = 28 + (rows.length + 1) * rowH;
      g.fillStyle = 'rgba(247,245,243,0.028)'; g.fillRect(x, y, W, H);
      g.strokeStyle = 'rgba(247,245,243,0.13)'; g.lineWidth = 1; g.strokeRect(x + 0.5, y + 0.5, W, H);
      // tab
      g.font = `500 17px ${SANS}`; g.fillStyle = '#7fcf98'; g.textBaseline = 'alphabetic'; g.textAlign = 'left';
      g.fillText(name, x + 2, y - 14); g.fillRect(x, y - 6, g.measureText(name).width + 4, 2);
      if (o.formula) { g.font = `14px ${MONO}`; g.fillStyle = 'rgba(161,157,155,0.6)'; g.fillText(o.formula, x + g.measureText(name).width + 40, y - 14); }
      // column letters + row numbers
      g.font = `12px ${MONO}`; g.fillStyle = head; g.textAlign = 'center';
      let cx = x + 44;
      cols.forEach((c, j) => { g.fillText(String.fromCharCode(65 + j), cx + c.w / 2, y + 19); cx += c.w; });
      g.strokeStyle = grid;
      for (let k = 0; k <= rows.length + 1; k++) {
        const yy = y + 28 + k * rowH; g.beginPath(); g.moveTo(x, yy + 0.5); g.lineTo(x + W, yy + 0.5); g.stroke();
        if (k <= rows.length) { g.fillStyle = head; g.textAlign = 'center'; g.fillText(String(k + 1), x + 22, yy + rowH / 2 + 5); }
      }
      cx = x + 44;
      [0, ...cols.map((c) => c.w)].forEach((w) => { cx += w; g.beginPath(); g.moveTo(cx + 0.5 - (w ? 0 : 0), y); g.lineTo(cx + 0.5, y + H); g.stroke(); });
      // header row + data
      const all = [cols.map((c) => c.h), ...rows];
      all.forEach((row, k) => {
        let xx = x + 44;
        const yy = y + 28 + k * rowH + rowH / 2 + 5;
        row.forEach((v, j) => {
          const c = cols[j], num = /^[−\-+¥]?[\d,.]+%?$/.test(v) || /^−/.test(v);
          g.font = k === 0 ? `500 13px ${SANS}` : `${o.big ? 16 : 14}px ${MONO}`;
          g.fillStyle = k === 0 ? 'rgba(201,197,194,0.75)' : (o.color && o.color(k, j, v)) || txt;
          if (num && k > 0) { g.textAlign = 'right'; g.fillText(v, xx + c.w - 10, yy); } else { g.textAlign = 'left'; g.fillText(v, xx + 10, yy); }
          xx += c.w;
        });
      });
      return { W, H };
    }
    const ZHs = F.lang === 'zh';
    // 清洗明细 (long, dense)
    sheet(-560, -470, L('清洗明细', 'Cleaned'), [{ h: L('订单号', 'Order'), w: 156 }, { h: L('日期', 'Date'), w: 112 }, { h: L('门店', 'Store'), w: 62 }, { h: L('区域', 'Region'), w: 92 },
      { h: L('品类', 'Category'), w: 84 }, { h: L('数量', 'Qty'), w: 52 }, { h: L('单价', 'Price'), w: 82 }, { h: L('金额', 'Amount'), w: 100 }, { h: L('折扣', 'Disc.'), w: 58 }], WB.detail);
    // 区域×月份 (SUMIFS)
    const regRows = WB.reg.map((r) => [...r, (r.slice(1).reduce((a, b) => a + +b, 0)).toFixed(0)]);
    const sumRow = [L('合计', 'Total'), ...WB.months.map((_, j) => WB.reg.reduce((a, r) => a + +r[j + 1], 0).toFixed(1)), regRows.reduce((a, r) => a + +r[13], 0).toFixed(0)];
    const yoyRows = [[L('同比', 'YoY'), ...WB.months.map(() => ''), ''], ...WB.regions.map((n, j) => [n, ...WB.months.map((_, m) => '−' + (2 + ((j * 7 + m * 3) % 9) * 0.9).toFixed(1) + '%'), ['−4.9%', '−9.6%', '−6.2%', '−5.9%'][j]])];
    sheet(520, -470, L('区域×月份', 'Region×Month'), [{ h: '', w: 92 }, ...WB.months.map((m) => ({ h: m, w: 78 })), { h: L('合计', 'Total'), w: 92 }],
      [...regRows, sumRow, ...yoyRows], { formula: '=SUMIFS(清洗明细!$H:$H, 清洗明细!$D:$D, $A2, 清洗明细!$B:$B, B$1)' });
    // 品类×月份
    sheet(1830, 260, L('品类×月份', 'Category×Month'), [{ h: '', w: 80 }, ...WB.months.slice(0, 9).map((m) => ({ h: m, w: 78 }))], WB.catm);
    // 门店排名
    sheet(1830, 640, L('门店排名', 'Store ranking'), [{ h: '#', w: 46 }, { h: L('门店', 'Store'), w: 80 }, { h: L('区域', 'Region'), w: 96 }, { h: L('同比', 'YoY'), w: 90 }],
      WB.stores.map((s, k) => [String(k + 1), s[0], s[1], (+s[2] < 0 ? '−' : '+') + Math.abs(+s[2]).toFixed(1) + '%']));
    // 结论 (C4 = −6.8%)
    const concl = [
      [L('结论', 'Summary'), '', '', ''],
      [L('指标', 'Measure'), L('来源', 'Source'), L('数值', 'Value'), ''],
      [L('12 个月销售额', '12-month sales'), L('区域×月份!N6', 'Region×Month!N6'), L('¥3.84 亿', '¥384M'), ''],
      [L('同比', 'YoY'), 'yoy_total', '−6.8%', ''],
      [L('客流同比', 'Footfall YoY'), 'yoy_footfall', '−1.2%', ''],
      [L('客单价同比', 'Ticket YoY'), 'yoy_ticket', '−5.7%', ''],
      [L('剔除新店后同比', 'YoY w/o new stores'), 'yoy_like_for_like', '−6.1%', ''],
      [L('家居折扣率', 'Home discount rate'), L('品类×月份', 'Category×Month'), '12% → 19%', ''],
      [L('家居品类销售同比', 'Home sales YoY'), L('品类×月份', 'Category×Month'), '−18.4%', ''],
    ];
    sheet(520, 1250, L('结论', 'Summary'), [{ h: L('结论', 'Summary'), w: 240 }, { h: '', w: 300 }, { h: '', w: 180 }, { h: '', w: 160 }], concl.slice(1).map((r) => r), { rowH: 36, big: true,
      color: (k, j) => (j === 2 ? 'rgba(232,228,225,0.92)' : null) });
    g.setTransform(1, 0, 0, 1, 0, 0);
    if (off) {
      ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.filter = `blur(${blurPx}px)`; ctx.drawImage(off, 0, 0); ctx.filter = 'none';
    }
    void ZHs;
  }
})();
