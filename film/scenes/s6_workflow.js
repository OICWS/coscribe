// Sentence 5 · the fixed workflow (100.0–113.6), the film's climax.
//  S6-01 100.0–103.2  the light surface, four deliverables in the box, the fifth sentence typed (and spoken).
//  S6-02 103.2–106.0  not a dive but a pull-back: the surface tilts back like a sheet of paper over the
//                     three depth worlds (sheet green / slides orange / code); the blue line runs through
//                     them, is pulled taut and crystallises into the workflow's vertical step list.
//  S6-03 106.0–110.0  saved as a scheduled task; five Mondays, each run turns the steps green; week three
//                     waits at the approval step; run rows pile up.
//  S6-04 110.0–113.6  identical runs tile into a fabric while the camera pulls back; hard cut at 113.6:
//                     everything collapses into one blue line (s7_close.js takes it from there).
// UI fragments follow office-agent/frontend/src/components/workflow/* (WorkflowDraftCard, StepSummary,
// WorkflowRunView, parts) and ScheduledTaskDetail / RunPanel / RunStatusIcon, in the app's dark theme.
(function () {
  const { h, css, attr, clamp, lerp, tw, ease, prog, env, L, T } = F;

  // ---------- timing ----------
  const T0 = 100.0, TEND = 113.6;
  const TYPE0 = 100.4, TYPEDUR = 2.6, ENTER = 103.1;
  const DRAW0 = 103.45, DRAW1 = 104.5;       // the line runs down through the strata
  const TAUT0 = 104.5, TAUT1 = 104.92;      // ... and is pulled taut
  const SWITCH = 105.15;                     // 3D line hands over to the 2D step list rail
  const CRYST = 105.2, CRYST_STEP = 0.034;   // rows crystallise top to bottom
  const CARD_IN = 105.45, SAVED = 106.0, OPEN = 106.36, TASK_IN = 106.5;
  const MONDAYS = [106.6, 107.4, 108.2, 109.0, 109.8];
  const SWEEP = 0.56;                        // one run, top to bottom
  const HIT = 110.0, BEAT = 60 / 72;         // 72 BPM
  const SQUEEZE0 = 113.4;

  // ---------- the app's dark theme (office-agent/frontend/src/index.css, [data-theme="dark"]) ----------
  const D = { bg: '#232120', fg: '#f7f5f3', muted: '#a19d9b', card: '#322f2e', border: 'rgba(247,245,243,.14)',
    borderHover: 'rgba(247,245,243,.26)', accent: '#4b8fe3', accentInk: '#9cc3f3', accentSoft: 'rgba(75,143,227,.16)',
    success: '#8fd19f', warning: '#e8b85c', sheet: '#7fcf98', slides: '#f0a076', depth: '#141312' };
  const KC = { tool: '#cbc5c2', script: '#8cb4f0', llm: '#ef92ab', check: '#8fd19f', approval: '#e8b85c', branch: '#c3adf7', loop: '#7fd2d6' };
  const rgba = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; };
  const SANS = '"IBM Plex Sans", "Noto Sans SC", sans-serif', MONO = '"IBM Plex Mono", "Noto Sans SC", monospace';

  // ---------- icons (components/icons.tsx, feather/lucide 24 viewBox), as path data so canvas can draw them too ----------
  const IC = {
    tool: ['M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z'],
    file: ['M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z', 'M14 3v6h6M8 13h8M8 17h5'],
    script: ['M4 17L10 11L4 5', 'M12 19H20'],
    llm: ['M12 3l1.9 5.6L19.5 10.5l-5.6 1.9L12 18l-1.9-5.6L4.5 10.5l5.6-1.9z', 'M19 16v4M17 18h4'],
    check: ['M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z', 'M8.5 12l2.5 2.5 4.5-5'],
    approval: ['M16 8a4 4 0 1 1-8 0a4 4 0 1 1 8 0', 'M4 21c0-4 3.6-7 8-7s8 3 8 7'],
    branch: ['M16 3h5v5M8 3H3v5M12 22v-8.3a4 4 0 0 0-1.172-2.872L3 3M15 9l6-6'],
    workflow: ['M5 3h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z', 'M7 11v4a2 2 0 0 0 2 2h4', 'M15 13h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-4a2 2 0 0 1 2-2z'],
    checkCircle: ['M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0', 'M8.5 12.5l2.5 2.5 4.5-5'],
    alert: ['M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0', 'M12 8v4', 'M12 16h.01'],
    clock: ['M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0', 'M12 7v5l3 3'],
    tick: ['M20 6L9 17l-5-5'],
    chevR: ['M9 6l6 6-6 6'],
  };
  const svgIcon = (name, size, color, sw = 2) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" style="display:block">${IC[name].map((d) => `<path d="${d}"/>`).join('')}</svg>`;
  const P2D = {};
  const path2d = (name) => P2D[name] || (P2D[name] = IC[name].map((d) => new Path2D(d)));

  // ---------- the workflow (script §4.5), summaries in StepSummary.tsx's own wording ----------
  // summary segments: l label (muted) · k arg key (muted) · m mono value · v value token · i input token · a arrow
  const STEPS = [
    { kind: 'tool', icon: 'tool', title: L('读取门店销售', 'Read store sales'), sum: [['l', 'Read Excel'], ['k', 'path'], ['m', L('门店销售_{{month}}.xlsx', 'stores_{{month}}.xlsx')], ['a'], ['v', 'sales']], d: '1.2s' },
    { kind: 'script', icon: 'script', title: L('清洗与对齐', 'Clean and align'), sum: [['l', 'Python · 48 lines'], ['v', 'sales'], ['a'], ['v', 'clean']], d: '4.8s' },
    { kind: 'check', icon: 'check', title: L('无重复入账', 'No duplicate orders'), sum: [['v', 'clean.duplicates'], ['l', '='], ['m', '0'], ['l', '· otherwise stop']], d: '0.1s' },
    { kind: 'script', icon: 'script', title: L('归因分析', 'Find the causes'), sum: [['l', 'Python · 112 lines'], ['v', 'clean'], ['a'], ['v', 'findings']], d: '6.3s' },
    { kind: 'tool', icon: 'file', title: L('写入分析工作簿', 'Write the workbook'), sum: [['l', 'Write Excel'], ['k', 'path'], ['m', L('销售下滑分析_{{month}}.xlsx', 'sales-decline_{{month}}.xlsx')]], d: '2.2s' },
    { kind: 'tool', icon: 'file', title: L('添加图表', 'Add the charts'), sum: [['l', 'Add Excel chart'], ['k', 'chart_type'], ['m', 'line'], ['k', 'sheet'], ['m', L('区域×月份', 'Region×Month')]], d: '1.4s' },
    { kind: 'llm', icon: 'llm', title: L('撰写结论页', 'Write the conclusions'), sum: [['l', 'Returns'], ['m', 'headline, points'], ['a'], ['v', 'summary']], d: '9.6s' },
    { kind: 'tool', icon: 'file', title: L('生成管理层汇报', 'Build the briefing'), sum: [['l', 'Write PowerPoint'], ['k', 'template_path'], ['m', L('公司模板.pptx', 'house-style.pptx')]], d: '5.1s' },
    { kind: 'tool', icon: 'tool', title: L('渲染预览', 'Render a preview'), sum: [['l', 'Render PowerPoint preview'], ['a'], ['v', 'preview']], d: '3.9s' },
    { kind: 'check', icon: 'check', title: L('对比度检查', 'Contrast check'), sum: [['v', 'preview.low_contrast_warnings'], ['l', '='], ['m', '0'], ['l', '· otherwise stop']], d: '0.1s' },
    { kind: 'script', icon: 'script', title: L('三系统日报校验', 'Check the daily exports'), sum: [['l', 'Python · 64 lines'], ['i', 'exports'], ['a'], ['v', 'anomalies']], d: '3.4s' },
    { kind: 'branch', icon: 'branch', title: L('有异常吗', 'Any anomalies?'), sum: [['l', 'If'], ['l', 'count of'], ['v', 'anomalies'], ['l', '>'], ['m', '0']], d: '0.1s' },
    { kind: 'approval', icon: 'approval', title: L('确认异常后再发送', 'Confirm before sending'), sum: [['l', 'Pauses for your approval']], d: '', nested: true },
    { kind: 'tool', icon: 'tool', title: L('发送邮件', 'Email the results'), sum: [['l', 'Microsoft 365 · Send mail'], ['k', 'subject'], ['m', L('销售周报 · {{month}}', 'Sales · {{month}}')]], d: '2.8s' },
  ];
  const NAME = L('每周一 · 销售分析与日报校验', 'Monday · sales analysis & daily-report check');
  const APPROVAL = 12, BRANCH = 11;

  // list geometry in product px (1x); the list is drawn at scale S
  const S = 1.7, COLW = 560, RAILX = 11;
  // the right-hand column (draft card / scheduled task / runs) is drawn at SR
  const SR = 1.55, RCOLW = 370;
  // rest layout on screen (f = 1): the list fills the left two thirds, the column sits right, the calendar below
  const LIST_SX = 148, LIST_SY = 118, RCOL_SX = 1772 - RCOLW * SR, RULER_SY = 968, RULER_W = 1624;
  const ROWY = STEPS.map((s, i) => (i <= BRANCH ? i * 28 : i === APPROVAL ? 368 : 430));
  const SECTION = { y: 336, hgt: 62, x: 36 }, OTHER_Y = 404, LIST_H = 452;
  // which depth world each step was made in (for the node positions on the strata)
  const STRATUM = [0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2];

  // ---------- canvas drawing of one list row (shared by the strata-free fabric copies) ----------
  function roundRect(c, x, y, w, hh, r) { c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + hh, r); c.arcTo(x + w, y + hh, x, y + hh, r); c.arcTo(x, y + hh, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }
  function drawIcon(c, name, x, y, size, color, sw = 2) {
    c.save(); c.translate(x, y); c.scale(size / 24, size / 24);
    c.strokeStyle = color; c.lineWidth = sw; c.lineCap = 'round'; c.lineJoin = 'round';
    for (const p of path2d(name)) c.stroke(p);
    c.restore();
  }

  F.scene({
    id: 's6', start: T0, end: TEND, z: 30,
    build(layer) {
      // ======================= layers =======================
      const bg = K.depth(layer);
      const P = 1800;
      const view = h('div', { class: 'abs' });
      css(view, { left: 0, top: 0, width: 1920, height: 1080, perspective: P + 'px', perspectiveOrigin: '960px 540px' });
      const rig = h('div', { class: 'abs' });
      css(rig, { left: 0, top: 0, width: 0, height: 0, transformOrigin: '0 0', transformStyle: 'preserve-3d' });
      view.append(rig); layer.append(view);

      // ---- the paper: the light surface with the input box ----
      const paper = h('div', { class: 'abs' });
      css(paper, { left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '0 0', overflow: 'hidden' });
      K.surface(paper);
      const box = K.inputBox(paper, { y: 540, placeholder: T('placeholder') });
      const chipDefs = [['s1_out', 'sheet'], ['s2_out', 'slides'], ['s3_out', 'doc'], ['s4_out', 'code']];
      const chips = chipDefs.map(([k, kind]) => box.addChip(T(k), kind));
      // four chips must sit on one row: tighten them a little (same chip, smaller set)
      // like Composer.tsx's attachment chips: max-w 220px, the name truncates (and they shrink to share one row)
      chips.forEach((c) => {
        css(c, { fontSize: 14, padding: '6px 12px 6px 7px', gap: 8, maxWidth: 220, minWidth: 0, flex: '0 1 auto', whiteSpace: 'nowrap' });
        css(c.firstChild, { flex: 'none' });
        css(c.lastChild, { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' });
      });
      css(box.chips, { flexWrap: 'nowrap', gap: 7 });
      rig.append(paper);

      // ---- the strata: three depth worlds as sheets beneath the paper ----
      const SW = 2000, SH = 820;
      const strata = [drawSheetWorld(), drawSlidesWorld(), drawCodeWorld()].map((cv) => {
        css(cv, { position: 'absolute', left: 0, top: 0, width: SW, height: SH, transformOrigin: '0 0' });
        rig.append(cv); return cv;
      });

      // ---- the 2D world: step list, cards, schedule, runs ----
      const squeeze = h('div', { class: 'abs' });
      css(squeeze, { left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '960px 540px' });
      const fabric = h('canvas', { width: 1920, height: 1080 });
      css(fabric, { position: 'absolute', left: 0, top: 0, width: 1920, height: 1080 });
      const fctx = fabric.getContext('2d');
      const world = h('div', { class: 'abs' });
      css(world, { left: 0, top: 0, width: 0, height: 0, transformOrigin: '0 0' });
      squeeze.append(fabric, world); layer.append(squeeze);

      // overlay for the 3D line (screen space)
      const ov = h('svg', { class: 'full', viewBox: '0 0 1920 1080' });
      const defs = h('defs');
      const mask = h('mask', { id: 's6mask', maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: 1920, height: 1080 });
      const maskPoly = h('polygon', { points: '0,0 0,0 0,0 0,0', fill: '#404040' });
      mask.append(h('rect', { x: 0, y: 0, width: 1920, height: 1080, fill: '#fff' }), maskPoly);
      defs.append(mask); ov.append(defs);
      const lineG = h('g', { mask: 'url(#s6mask)' });
      const lineHalo = h('path', { fill: 'none', stroke: D.accent, 'stroke-width': 9, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', opacity: 0.1 });
      const linePath = h('path', { fill: 'none', stroke: D.accent, 'stroke-width': 2.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
      lineG.append(lineHalo, linePath);
      const nodeEls = STEPS.map(() => {
        const g = h('g');
        g.append(h('circle', { r: 6.5, fill: D.depth, stroke: D.accent, 'stroke-width': 1.6 }), h('circle', { r: 2.4, fill: '#fff' }));
        return g;
      });
      const head = h('circle', { r: 5, fill: '#fff' });
      lineG.append(...nodeEls);
      ov.append(lineG, head);
      layer.append(ov);

      // subtitle scrim (VO-08 over the fabric) and the final collapse line
      const scrim = h('div', { class: 'abs' });
      css(scrim, { left: 0, top: 0, width: 1920, height: 1080, pointerEvents: 'none', opacity: 0,
        background: 'linear-gradient(180deg, rgba(13,12,12,0) 0px, rgba(13,12,12,0) 760px, rgba(13,12,12,.74) 872px, rgba(13,12,12,.74) 952px, rgba(13,12,12,.5) 1080px)' });
      const collapseLine = h('div', { class: 'abs' });
      css(collapseLine, { left: 960 - 1.25, top: 0, width: 2.5, height: 1080, background: D.accent, opacity: 0 });
      const exTag = h('div', { class: 'abs', text: T('example') });
      css(exTag, { left: 48, top: 40, fontFamily: 'var(--mono)', fontSize: 12, letterSpacing: '.08em', color: D.muted, opacity: 0 });
      layer.append(scrim, collapseLine, exTag);
      const grainU = K.grain(layer, 0.04);

      // ======================= the step list (DOM) =======================
      const list = h('div', { class: 'abs' });
      css(list, { left: 0, top: 0, width: COLW, height: LIST_H, transformOrigin: '0 0', fontFamily: SANS, color: D.fg });
      world.append(list);
      const rail = h('div', { class: 'abs' }); css(rail, { left: RAILX - 1, width: 2, background: D.accent, top: 0, height: 0 });
      const railLit = h('div', { class: 'abs' }); css(railLit, { left: RAILX - 1, width: 2, background: D.accent, top: 11, height: 0 });
      const runHead = h('div', { class: 'abs' }); css(runHead, { left: RAILX - 4.5, width: 9, height: 9, borderRadius: 5, background: '#fff', opacity: 0 });
      // the branch's "then" arm: the nested section (WorkflowRunView Nested / editor arms)
      const sect = h('div', { class: 'abs' });
      css(sect, { left: SECTION.x, top: SECTION.y, width: COLW - SECTION.x, height: SECTION.hgt, borderRadius: 8,
        borderLeft: `2px solid ${rgba(KC.branch, 0.55)}`, background: rgba(KC.branch, 0.04) });
      const sectLabel = h('div', { class: 'abs' });
      css(sectLabel, { left: 12, top: 8, display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 12, whiteSpace: 'nowrap' });
      const thenL = h('span', { text: 'Then' }); css(thenL, { fontWeight: '600', textTransform: 'uppercase', letterSpacing: '.08em', color: KC.branch });
      const thenN = h('span', { text: 'when it holds' }); css(thenN, { fontFamily: MONO, color: D.muted });
      sectLabel.append(thenL, thenN); sect.append(sectLabel);
      const other = h('div', { class: 'abs' });
      css(other, { left: SECTION.x + 14, top: OTHER_Y, display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 13, color: D.muted, whiteSpace: 'nowrap' });
      const otherL = h('span', { text: 'Otherwise' }); css(otherL, { fontSize: 12, fontWeight: '600', textTransform: 'uppercase', letterSpacing: '.08em' });
      other.append(otherL, h('span', { text: 'Nothing to do here.' }));
      const elbow = h('svg', { width: 60, height: 40, viewBox: '0 0 60 40' });
      css(elbow, { position: 'absolute', left: 0, top: 316, overflow: 'visible' });
      elbow.append(h('path', { d: 'M11 14 C11 26, 18 28, 37 28', fill: 'none', stroke: rgba(KC.branch, 0.55), 'stroke-width': 1.5 }));
      list.append(sect, elbow, other, rail, railLit);

      const rows = STEPS.map((s, i) => {
        const r = h('div', { class: 'abs' });
        const x0 = s.nested ? SECTION.x + 12 : 0;
        css(r, { left: x0, top: ROWY[i], width: COLW - x0 - (s.nested ? 10 : 0), height: 22, display: 'flex', alignItems: 'center', whiteSpace: 'nowrap' });
        // status dot (WorkflowRunView StatusDot) over an opaque disc so the rail passes behind it
        const dotWrap = h('span'); css(dotWrap, { position: 'relative', width: 22, height: 22, flex: 'none', borderRadius: 11, background: '#191817' });
        const dot = {
          nr: h('span'), done: h('span'), wait: h('span'), skip: h('span'),
        };
        css(dot.nr, { position: 'absolute', inset: 0, borderRadius: 11, border: `1.5px dashed ${D.borderHover}` });
        css(dot.done, { position: 'absolute', inset: 0, borderRadius: 11, background: rgba(D.success, 0.16), display: 'grid', placeItems: 'center' });
        dot.done.innerHTML = svgIcon('tick', 12, D.success, 3);
        css(dot.wait, { position: 'absolute', inset: 0, borderRadius: 11, background: rgba(D.warning, 0.16), display: 'grid', placeItems: 'center' });
        dot.wait.innerHTML = svgIcon('clock', 12, D.warning, 2.5);
        css(dot.skip, { position: 'absolute', inset: 0, borderRadius: 11, background: D.card, display: 'grid', placeItems: 'center' });
        dot.skip.innerHTML = `<span style="display:block;width:8px;height:1.5px;border-radius:1px;background:${D.muted}"></span>`;
        Object.values(dot).forEach((e) => { e.style.opacity = 0; dotWrap.append(e); });
        // kind tile (parts.tsx StepKindTile, size sm)
        const tile = h('span'); css(tile, { width: 20, height: 20, flex: 'none', borderRadius: 6, marginLeft: 14, display: 'grid', placeItems: 'center', background: rgba(KC[s.kind], 0.13) });
        tile.innerHTML = svgIcon(s.icon, 12, KC[s.kind], 2);
        const body = h('span'); css(body, { display: 'flex', alignItems: 'baseline', gap: 10, marginLeft: 10, minWidth: 0, flex: '1', overflow: 'hidden' });
        const title = h('span', { text: s.title }); css(title, { fontSize: 14, fontWeight: '500', flex: 'none' });
        const sum = h('span'); css(sum, { fontSize: 13, color: D.muted, display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' });
        for (const [k, v] of s.sum) sum.append(segEl(k, v));
        const dur = h('span', { text: s.d }); css(dur, { fontSize: 12, color: D.muted, marginLeft: 10, flex: 'none', fontVariantNumeric: 'tabular-nums', opacity: 0, minWidth: 30, textAlign: 'right' });
        body.append(title, sum);
        r.append(dotWrap, tile, body, dur);
        list.append(r);
        return { r, dotWrap, dot, tile, body, dur, state: '', x0 };
      });
      function segEl(k, v) {
        if (k === 'a') return h('span', { text: '→' });
        const e = h('span', { text: v });
        if (k === 'm') css(e, { fontFamily: MONO, fontSize: 12, color: D.fg, flex: 'none' });
        else if (k === 'v' || k === 'i') css(e, { fontFamily: MONO, fontSize: 12, lineHeight: '1.45', padding: '1px 6px', borderRadius: 5, flex: 'none',
          background: k === 'i' ? D.accentSoft : D.card, color: k === 'i' ? D.accentInk : D.fg });
        else css(e, { flex: 'none', marginRight: k === 'k' ? -2 : 0 });
        return e;
      }
      // date header shown above each run column in the fabric
      const colHead = h('div', { class: 'abs', text: '11-09' });
      css(colHead, { left: 0, top: -40, fontFamily: MONO, fontSize: 13, color: D.muted, letterSpacing: '.04em', opacity: 0 });
      list.append(colHead);

      // ======================= draft card (WorkflowDraftCard) =======================
      const card = h('div', { class: 'abs' });
      css(card, { left: 0, top: 0, width: RCOLW, padding: 16, borderRadius: 12, border: `1px solid ${D.border}`, background: D.bg,
        fontFamily: SANS, color: D.fg, transformOrigin: '0 0', boxShadow: '0 30px 80px rgba(0,0,0,.45)' });
      const cHead = h('div'); css(cHead, { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: 12, fontWeight: '500', letterSpacing: '.04em', textTransform: 'uppercase', color: D.muted });
      cHead.innerHTML = svgIcon('workflow', 14, D.muted, 2) + '<span>Fixed workflow draft</span>';
      const cName = h('div', { text: NAME }); css(cName, { fontSize: 15, fontWeight: '500', lineHeight: '1.4' });
      const cStatusBox = h('div'); css(cStatusBox, { position: 'relative', height: 20, marginTop: 4, perspective: '400px' });
      const statusLine = (icon, text, color) => {
        const e = h('div'); css(e, { position: 'absolute', left: 0, top: 0, right: 0, display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color, whiteSpace: 'nowrap', transformOrigin: '50% 50%', backfaceVisibility: 'hidden' });
        e.innerHTML = svgIcon(icon, 14, color, 2) + `<span style="min-width:0;overflow:hidden;text-overflow:ellipsis">${text}</span>`; return e;
      };
      const stTested = statusLine('checkCircle', 'Tested · every step passed in 41s', D.success);
      const stSaved = statusLine('checkCircle', `Saved to “${NAME}”`, D.success);
      cStatusBox.append(stTested, stSaved);
      const cSteps = h('div'); css(cSteps, { display: 'flex', alignItems: 'center', gap: 4, marginTop: 12, fontSize: 13, color: D.muted });
      cSteps.innerHTML = svgIcon('chevR', 14, D.muted, 2) + '<span>14 steps · 2 inputs asked each run</span>';
      const cBtns = h('div'); css(cBtns, { position: 'relative', height: 32, marginTop: 14 });
      const btn = (text, kind) => { const b = h('span', { text }); css(b, { display: 'inline-flex', alignItems: 'center', height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13, marginRight: 8,
        ...(kind === 'p' ? { background: D.fg, color: D.bg, fontWeight: '500' } : kind === 's' ? { border: `1px solid ${D.border}`, background: D.bg } : { color: D.muted, padding: '0 8px' }) }); return b; };
      const btnsA = h('div'); css(btnsA, { position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap' });
      btnsA.append(btn('Review and save', 'p'), btn('Test again', 's'), btn('Continue in chat', 'g'));
      const btnsB = h('div'); css(btnsB, { position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap', opacity: 0 });
      const openBtn = btn('Open task', 's'); css(openBtn, { transformOrigin: '50% 50%' }); btnsB.append(openBtn);
      cBtns.append(btnsA, btnsB);
      card.append(cHead, cName, cStatusBox, cSteps, cBtns);
      world.append(card);

      // ======================= runs (ScheduledTaskDetail RunsSection + RunStatusIcon) =======================
      const runs = h('div', { class: 'abs' });
      css(runs, { left: 0, top: 0, width: RCOLW, fontFamily: SANS, color: D.fg, transformOrigin: '0 0' });
      const runsLabel = h('div', { text: 'Runs' }); css(runsLabel, { fontSize: 14, color: D.muted, marginBottom: 4 });
      const runsBody = h('div'); css(runsBody, { position: 'relative', height: 5 * 36 });
      runs.append(runsLabel, runsBody);
      const RUNS = [['Oct 12', '41s', 'ok'], ['Oct 19', '39s', 'ok'], ['Oct 26', '', 'wait'], ['Nov 2', '40s', 'ok'], ['Nov 9', '42s', 'ok']];
      const runRows = RUNS.map(([day, secs, st]) => {
        const r = h('div'); css(r, { position: 'absolute', left: -8, top: 0, width: RCOLW + 16, height: 36, padding: '0 8px', display: 'flex', alignItems: 'center', gap: 12, fontSize: 14, whiteSpace: 'nowrap', opacity: 0 });
        const ic = h('span'); ic.innerHTML = st === 'ok' ? svgIcon('checkCircle', 14, D.success, 2) : svgIcon('alert', 14, D.warning, 2);
        const tx = h('span'); css(tx, { flex: '1', minWidth: 0 });
        tx.append(`${day} at 9:00 AM`); const m = h('span', { text: ` · Scheduled${secs ? ' · ' + secs : ''}` }); css(m, { color: D.muted }); tx.append(m);
        r.append(ic, tx);
        if (st !== 'ok') { const n = h('span', { text: 'Needs approval' }); css(n, { fontSize: 12, color: D.muted }); r.append(n); }
        runsBody.append(r); return r;
      });
      world.append(runs);

      // ======================= scheduled task header (ScheduledTaskDetail / RunPanel) =======================
      const task = h('div', { class: 'abs' });
      css(task, { left: 0, top: 0, width: RCOLW, fontFamily: SANS, color: D.fg, transformOrigin: '0 0' });
      // the title wraps before "daily-report check", never at its hyphen
      const tName = h('div');
      { const k = NAME.indexOf('daily-report'); if (k < 0) tName.textContent = NAME; else { const nb = h('span', { text: NAME.slice(k) }); css(nb, { whiteSpace: 'nowrap' }); tName.append(NAME.slice(0, k), nb); } } css(tName, { fontSize: 22, fontWeight: '600', lineHeight: '1.3' });
      const tRow = h('div'); css(tRow, { display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 });
      const toggle = h('span'); css(toggle, { position: 'relative', width: 36, height: 20, borderRadius: 10, background: D.accent, flex: 'none' });
      const knob = h('span'); css(knob, { position: 'absolute', left: 18, top: 2, width: 16, height: 16, borderRadius: 8, background: D.bg }); toggle.append(knob);
      const active = h('span', { text: 'Active' }); css(active, { borderRadius: 999, padding: '2px 8px', fontSize: 12, fontWeight: '500', background: rgba(D.success, 0.15), color: D.success });
      tRow.append(toggle, active);
      const sched = h('div'); css(sched, { display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 14, borderRadius: 6, padding: '4px 8px', fontSize: 12, fontWeight: '500', background: rgba(D.success, 0.15), color: D.success });
      sched.innerHTML = svgIcon('clock', 13, D.success, 2) + '<span>Weekly on Monday at 9:00 AM</span>';
      const tMeta = h('div', { text: 'Workflow · 14 steps · 1 uses a model' }); css(tMeta, { fontSize: 14, color: D.muted, marginTop: 10 });
      task.append(tName, tRow, sched, tMeta);
      world.append(task);

      // ======================= the calendar ruler (five Mondays) =======================
      const ruler = h('div', { class: 'abs' });
      css(ruler, { left: 0, top: 0, width: RULER_W, height: 60, fontFamily: MONO, color: D.muted });
      const DAY = RULER_W / 46; // 10-05 .. 11-19
      const rbase = h('div', { class: 'abs' }); css(rbase, { left: 0, top: 0, width: RULER_W, height: 1, background: D.border });
      ruler.append(rbase);
      const MON_LABELS = ['10-05', '10-12', '10-19', '10-26', '11-02', '11-09', '11-16'];
      for (let d = 0; d <= 46; d++) {
        const mon = d % 7 === 0;
        const tk = h('div', { class: 'abs' }); css(tk, { left: d * DAY, top: 0, width: 1, height: mon ? 10 : 4, background: mon ? D.borderHover : D.border });
        ruler.append(tk);
      }
      const monEls = MON_LABELS.map((s, i) => {
        const e = h('div', { class: 'abs', text: s }); css(e, { left: i * 7 * DAY - 50, top: 20, width: 100, textAlign: 'center', fontSize: 17, letterSpacing: '.04em', color: D.muted, opacity: 0.55 });
        ruler.append(e); return e;
      });
      const marker = h('div', { class: 'abs' }); css(marker, { left: 0, top: -16, width: 2, height: 16, background: D.accent });
      ruler.append(marker);
      world.append(ruler);

      // ======================= geometry (anchor = the caret after the sentence) =======================
      // X0, Y0: the caret on the paper (paper px). Measured from layout once the sentence is typed.
      let X0 = 960 - 440 + 22 + 340, Y0 = 560, measured = false;
      function measure() {
        let e = box.text.children[1], x = 0, y = 0;
        while (e && e !== paper) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
        if (e === paper) { X0 = x + 1; Y0 = y + 15; measured = true; }
      }
      const V0 = 60;                             // first row top, in depth units below the paper
      const nodeV = STEPS.map((s, i) => V0 + (ROWY[i] + 11) * S);
      const V_END = 1500;
      // rest camera for the 2D world (screen = 960 + (u - cx) f, 540 + (v - vc) f)
      // the 3D line hands over to the 2D list with the rail at screen x 640, list top at y 270 (SW0);
      // the camera then eases to the rest layout (REST) while the rows crystallise
      const SW0 = () => ({ cx: X0 + 320, vc: V0 + 270 });
      const REST = () => ({ cx: X0 - RAILX * S + 960 - LIST_SX, vc: V0 + 540 - LIST_SY, f: 1 });
      // a rest-layout screen point -> world
      const RS = (r, x, y) => [r.cx - 960 + x, r.vc - 540 + y];

      // node positions on the strata (in-plane offsets from the caret axis)
      const OFF = [[-300, -150], [-110, -230], [150, -120], [320, 60], [90, 190], [-190, 120],
        [-250, -60], [30, -190], [270, 10], [40, 170],
        [-230, -110], [110, -40], [-60, 110], [220, 170]];
      const zA = [-440, -950, -1560];            // strata depths while the paper tilts
      const groupV = [0, 1, 2].map((g) => { const vs = nodeV.filter((v, i) => STRATUM[i] === g); return vs.reduce((a, b) => a + b, 0) / vs.length; });

      // ---------- 3D camera ----------
      function spline(keys, t, p) {
        const n = keys.length;
        if (t <= keys[0].t) return keys[0][p];
        if (t >= keys[n - 1].t) return keys[n - 1][p];
        let i = 0; while (t > keys[i + 1].t) i++;
        const k0 = keys[i], k1 = keys[i + 1], dt = k1.t - k0.t, s = (t - k0.t) / dt;
        const sl = (a, b) => (b[p] - a[p]) / (b.t - a.t);
        const m = (j) => { if (j === 0 || j === n - 1) return 0; const d0 = sl(keys[j - 1], keys[j]), d1 = sl(keys[j], keys[j + 1]); return d0 * d1 <= 0 ? 0 : 2 / (1 / d0 + 1 / d1); };
        const m0 = m(i) * dt, m1 = m(i + 1) * dt, s2 = s * s, s3 = s2 * s;
        return (2 * s3 - 3 * s2 + 1) * k0[p] + (s3 - 2 * s2 + s) * m0 + (-2 * s3 + 3 * s2) * k1[p] + (s3 - s2) * m1;
      }
      let KEYS3 = null;
      function keys3() {
        const r = SW0();
        return [
          { t: T0, th: 0, Dz: 0, cx: 960, cy: 540, cz: -300 },
          { t: ENTER + 0.08, th: 0, Dz: -36, cx: 960, cy: 540, cz: -300 },
          { t: 104.25, th: 64, Dz: 1150, cx: X0, cy: Y0 + 40, cz: -820 },
          { t: 104.55, th: 68, Dz: 1080, cx: X0 + 40, cy: Y0 + 30, cz: -760 },
          { t: SWITCH - 0.05, th: 90, Dz: 0, cx: r.cx, cy: Y0, cz: -r.vc },
        ];
      }
      function cam3(t) {
        const o = {};
        for (const p of ['th', 'Dz', 'cx', 'cy', 'cz']) o[p] = spline(KEYS3, t, p);
        return o;
      }
      function proj(c, x, y, z) {
        const th = c.th * Math.PI / 180, co = Math.cos(th), si = Math.sin(th);
        const ax = x - c.cx, ay = y - c.cy, az = z - c.cz;
        const by = ay * co - az * si, bz = ay * si + az * co - c.Dz;
        const f = P / (P - bz);
        return [960 + ax * f, 540 + by * f];
      }

      // ---------- the line through the strata ----------
      // control points: caret -> 14 work nodes -> far below; Catmull-Rom between them
      const SEG = 14;
      function ctrlPoints(zs, kT) {
        const pts = [[X0, Y0, 0, 0]];
        STEPS.forEach((s, i) => {
          const g = STRATUM[i];
          const slack = [X0 + OFF[i][0], Y0 + OFF[i][1], zs[g]];
          pts.push([lerp(slack[0], X0, kT), lerp(slack[1], Y0, kT), lerp(slack[2], -nodeV[i], kT), nodeV[i]]);
        });
        pts.push([X0, Y0, lerp(zs[2] - 500, -V_END, kT), V_END]);
        return pts;
      }
      function samples(pts) {
        const out = [];
        for (let i = 0; i < pts.length - 1; i++) {
          const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
          for (let j = 0; j < SEG; j++) {
            const s = j / SEG, s2 = s * s, s3 = s2 * s;
            const q = [0, 1, 2, 3].map((d) => 0.5 * ((2 * p1[d]) + (-p0[d] + p2[d]) * s + (2 * p0[d] - 5 * p1[d] + 4 * p2[d] - p3[d]) * s2 + (-p0[d] + 3 * p1[d] - 3 * p2[d] + p3[d]) * s3));
            out.push(q);
          }
        }
        out.push(pts[pts.length - 1]);
        return out;
      }
      const NS = (STEPS.length + 1) * SEG + 1;
      const drawK = (t) => ease.inOutQuad(prog(t, DRAW0, DRAW1));
      // the line reaches node i at sample (i+1)*SEG
      const nodeTimes = STEPS.map((s, i) => {
        const target = ((i + 1) * SEG) / (NS - 1) * 0.86;
        let lo = DRAW0, hi = DRAW1; for (let n = 0; n < 30; n++) { const mid = (lo + hi) / 2; if (drawK(mid) < target) lo = mid; else hi = mid; }
        return hi;
      });
      const lineK = (t) => drawK(t) * 0.86 + 0.14 * prog(t, TAUT0, TAUT1);
      nodeTimes.forEach((tt, i) => F.event(tt, 'tick', { soft: true, node: i }));

      // ======================= timeline: states =======================
      const keyTimes = K.typing(T('s5'), TYPE0, TYPEDUR, { seed: 55 });
      F.event(ENTER, 'enter');
      F.event(103.2, 'pullback', { dur: 2.0 });
      F.event(TAUT1, 'taut');
      STEPS.forEach((s, i) => F.event(CRYST + i * CRYST_STEP, 'glass', { i }));
      F.event(CARD_IN, 'paper');
      F.event(SAVED, 'chime');
      F.event(OPEN, 'click', { what: 'Open task' });
      F.event(TASK_IN, 'snap');
      MONDAYS.forEach((tm, w) => {
        F.event(tm, 'clock', { week: w });
        const stopAt = w === 2 ? APPROVAL : STEPS.length;
        for (let i = 0; i < stopAt; i++) if (!(w !== 2 && i === APPROVAL)) F.event(tm + rowT(i) * SWEEP, 'tick', { week: w, i });
        if (w === 2) F.event(tm + rowT(APPROVAL) * SWEEP, 'bell');
      });
      [0, 1, 2, 3].forEach((r) => F.event(HIT + r * BEAT, 'drop', { ring: r + 1, big: r === 0 }));
      F.event(113.0, 'rewhoosh', { dur: 0.6 });
      F.event(TEND, 'cut');
      // fraction of the sweep at which the head reaches row i (by its depth along the rail)
      function rowT(i) { return (ROWY[i] + 11 - 11) / (ROWY[STEPS.length - 1]); }

      // status of row i at time t (after crystallisation): '', nr, done, wait, skip
      function rowState(i, t) {
        if (t < CRYST + i * CRYST_STEP) return '';
        let w = -1; for (let k = 0; k < MONDAYS.length; k++) if (t >= MONDAYS[k]) w = k;
        if (w < 0) return 'nr';
        const lt = t - MONDAYS[w];
        if (lt < 0.07) return 'nr';                         // reset at the start of each run
        const at = rowT(i) * SWEEP;
        if (w === 2) {
          if (i === APPROVAL) return lt >= at ? 'wait' : 'nr';
          if (i > APPROVAL) return 'nr';
          return lt >= at ? 'done' : 'nr';
        }
        if (i === APPROVAL) return lt >= at ? 'skip' : 'nr';
        return lt >= at ? 'done' : 'nr';
      }
      // the fabric's copies are all finished runs
      const FINAL = STEPS.map((s, i) => (i === APPROVAL ? 'skip' : 'done'));

      // ======================= fabric sprite =======================
      const PW = COLW * S + 44, PH = (LIST_H + 50) * S + 26;   // tiling pitch in world px
      let sprites = null;
      function buildSprites() {
        const pad = 20, w = Math.ceil(COLW * S + pad * 2), hh = Math.ceil((LIST_H + 50) * S + pad * 2);
        const chain = (abstract) => {
          const base = h('canvas', { width: w, height: hh }), c = base.getContext('2d');
          c.translate(pad, pad + 50 * S); c.scale(S, S);
          drawList(c, abstract);
          const mips = [{ cv: base, s: 1 }];
          for (let k = 1; k <= 4; k++) {
            const prev = mips[k - 1].cv, cv = h('canvas', { width: Math.max(1, Math.round(prev.width / 2)), height: Math.max(1, Math.round(prev.height / 2)) });
            const cc = cv.getContext('2d'); cc.imageSmoothingQuality = 'high'; cc.drawImage(prev, 0, 0, cv.width, cv.height);
            mips.push({ cv, s: 1 / 2 ** k });
          }
          return mips;
        };
        return { mips: chain(false), abs: chain(true), pad, ox: pad + RAILX * S, oy: pad + 50 * S };
      }
      function drawList(c, abstract) {
        if (abstract) { drawAbstract(c); return; }
        // rail
        c.fillStyle = rgba(D.accent, 0.45); c.fillRect(RAILX - 1, 11, 2, ROWY[13] - 0);
        // nested section + labels
        roundRect(c, SECTION.x, SECTION.y, COLW - SECTION.x, SECTION.hgt, 8); c.fillStyle = rgba(KC.branch, 0.04); c.fill();
        c.fillStyle = rgba(KC.branch, 0.55); c.fillRect(SECTION.x, SECTION.y + 2, 2, SECTION.hgt - 4);
        c.font = `600 12px ${SANS}`; c.fillStyle = KC.branch; c.textBaseline = 'middle';
        c.fillText('THEN', SECTION.x + 12, SECTION.y + 16);
        c.font = `400 12px ${MONO}`; c.fillStyle = D.muted; c.fillText('when it holds', SECTION.x + 12 + 44, SECTION.y + 16);
        c.font = `600 12px ${SANS}`; c.fillText('OTHERWISE', SECTION.x + 14, OTHER_Y + 9);
        c.font = `400 13px ${SANS}`; c.fillText('Nothing to do here.', SECTION.x + 14 + 88, OTHER_Y + 9);
        c.strokeStyle = rgba(KC.branch, 0.55); c.lineWidth = 1.5; c.beginPath(); c.moveTo(11, 330); c.bezierCurveTo(11, 342, 18, 344, 37, 344); c.stroke();
        STEPS.forEach((s, i) => {
          const x0 = s.nested ? SECTION.x + 12 : 0, y = ROWY[i], cy = y + 11;
          const st = FINAL[i];
          c.fillStyle = '#191817'; c.beginPath(); c.arc(x0 + 11, cy, 11, 0, Math.PI * 2); c.fill();
          if (st === 'done') {
            c.fillStyle = rgba(D.success, 0.16); c.beginPath(); c.arc(x0 + 11, cy, 11, 0, Math.PI * 2); c.fill();
            drawIcon(c, 'tick', x0 + 5, cy - 6, 12, D.success, 3);
          } else { c.fillStyle = D.card; c.beginPath(); c.arc(x0 + 11, cy, 11, 0, Math.PI * 2); c.fill(); c.fillStyle = D.muted; c.fillRect(x0 + 7, cy - 0.75, 8, 1.5); }
          roundRect(c, x0 + 36, cy - 10, 20, 20, 6); c.fillStyle = rgba(KC[s.kind], 0.13); c.fill();
          drawIcon(c, s.icon, x0 + 40, cy - 6, 12, KC[s.kind], 2);
          let x = x0 + 66; const maxX = COLW - (s.nested ? 10 : 0) - 44;
          c.textBaseline = 'middle';
          c.font = `500 14px ${SANS}`; c.fillStyle = D.fg; c.fillText(s.title, x, cy); x += c.measureText(s.title).width + 10;
          c.save(); c.beginPath(); c.rect(x, y - 4, maxX - x, 30); c.clip();
          for (const [k, v] of s.sum) {
            const txt = k === 'a' ? '→' : v;
            if (k === 'm' || k === 'v' || k === 'i') c.font = `400 12px ${MONO}`; else c.font = `400 13px ${SANS}`;
            const tw_ = c.measureText(txt).width;
            if (k === 'v' || k === 'i') {
              roundRect(c, x, cy - 9, tw_ + 12, 18, 5); c.fillStyle = k === 'i' ? D.accentSoft : D.card; c.fill();
              c.fillStyle = k === 'i' ? D.accentInk : D.fg; c.fillText(txt, x + 6, cy + 0.5); x += tw_ + 12 + 6;
            } else { c.fillStyle = k === 'm' ? D.fg : D.muted; c.fillText(txt, x, cy + 0.5); x += tw_ + (k === 'k' ? 4 : 6); }
          }
          c.restore();
          if (s.d && st === 'done') { c.font = `400 12px ${SANS}`; c.fillStyle = D.muted; c.textAlign = 'right'; c.fillText(s.d, COLW - (s.nested ? 10 : 0), cy); c.textAlign = 'left'; }
        });
      }

      // the same column seen from far away: its threads only (rail, status, kinds, text as bars)
      function drawAbstract(c) {
        c.fillStyle = rgba(D.accent, 0.95); c.fillRect(RAILX - 5, -20, 10, ROWY[13] + 62);
        c.fillStyle = rgba(KC.branch, 0.5); c.fillRect(SECTION.x, SECTION.y + 2, 4, SECTION.hgt - 4);
        c.fillStyle = rgba(KC.branch, 0.06); c.fillRect(SECTION.x, SECTION.y, COLW - SECTION.x, SECTION.hgt);
        c.fillStyle = 'rgba(161,157,155,.35)'; c.fillRect(SECTION.x + 14, OTHER_Y + 6, 200, 5);
        c.font = `500 14px ${SANS}`;
        STEPS.forEach((s, i) => {
          const x0 = s.nested ? SECTION.x + 12 : 0, cy = ROWY[i] + 11;
          c.fillStyle = s.nested ? D.card : D.success; c.beginPath(); c.arc(x0 + 11, cy, 12, 0, Math.PI * 2); c.fill();
          roundRect(c, x0 + 35, cy - 11, 22, 22, 5); c.fillStyle = KC[s.kind]; c.fill();
          const tw1 = c.measureText(s.title).width;
          c.fillStyle = 'rgba(247,245,243,.62)'; c.fillRect(x0 + 66, cy - 4, tw1, 8);
          const sw1 = Math.min(COLW - x0 - 66 - tw1 - 60, 120 + (i * 53) % 160);
          c.fillStyle = 'rgba(161,157,155,.34)'; c.fillRect(x0 + 66 + tw1 + 10, cy - 3, sw1, 6);
          if (s.d) { c.fillStyle = 'rgba(161,157,155,.34)'; c.fillRect(COLW - 26, cy - 3, 26, 6); }
        });
      }

      // ======================= update =======================
      let lastCryst = null;
      return (lt, t) => {
        if (!measured && t >= ENTER - 0.3) { measure(); KEYS3 = null; }
        if (!KEYS3) KEYS3 = keys3();
        grainU(t);

        // ---------- S6-01: the surface, typing ----------
        const n = K.typedCount(keyTimes, t);
        const str = Array.from(T('s5')).slice(0, n).join('');
        const sent = prog(t, ENTER, ENTER + 0.35);
        box.update({ str, k: 1, t, sent: sent > 0 && sent < 1 ? sent : 0, caretOn: t < DRAW0 - 0.02, elapsed: t >= ENTER ? t - ENTER : null });
        const boxIn = tw(t, T0, T0 + 0.45, 'outCubic');
        css(box.root, { opacity: boxIn, transform: `translateY(${(1 - boxIn) * 8}px)` });
        // after Enter the four deliverables light up, faintly, one after another
        chips.forEach((c, i) => {
          const g = Math.sin(Math.PI * prog(t, ENTER + 0.05 + i * 0.07, ENTER + 0.65 + i * 0.07));
          css(c, { borderColor: g > 0.01 ? `rgba(42,120,214,${(0.14 + 0.5 * g).toFixed(3)})` : 'var(--border)', background: g > 0.01 ? `color-mix(in srgb, var(--accent-soft) ${Math.round(g * 100)}%, var(--card-bg))` : 'var(--card-bg)' });
        });

        // ---------- 3D phase: paper tilts back over the strata ----------
        const in3D = t < SWITCH + 0.6;
        view.style.display = in3D ? '' : 'none';
        ov.style.display = t < SWITCH + 0.05 && t >= DRAW0 - 0.05 ? '' : 'none';
        const c3 = cam3(Math.min(t, SWITCH));
        if (in3D) {
          rig.style.transform = `translate3d(960px,540px,${-c3.Dz}px) rotateX(${c3.th}deg) translate3d(${-c3.cx}px,${-c3.cy}px,${-c3.cz}px)`;
          const paperA = 1 - tw(t, 104.45, 104.8, 'inOutQuad');
          css(paper, { opacity: paperA, display: paperA > 0 ? '' : 'none' });
          // strata: compress toward their step groups as the line is pulled taut
          const kT = ease.inOutCubic(prog(t, TAUT0, TAUT1));
          const kC = ease.inOutCubic(prog(t, TAUT0 - 0.1, SWITCH));
          const zs = zA.map((z, g) => lerp(z, -groupV[g], kC));
          const stA = Math.min(tw(t, 103.3, 103.9, 'outQuad'), 1 - tw(t, 104.55, 104.9, 'inOutQuad'));
          strata.forEach((cv, g) => {
            css(cv, { transform: `translate3d(${960 - SW / 2}px,${Y0 + 60 - SH / 2}px,${zs[g]}px)`, opacity: stA, display: stA > 0 ? '' : 'none' });
          });
          if (t >= DRAW0 - 0.05 && t < SWITCH + 0.05) {
            // the line
            const pluck = t > TAUT1 ? 9 * Math.sin(2 * Math.PI * 11 * (t - TAUT1)) * Math.exp(-(t - TAUT1) / 0.09) : 0;
            const pts = ctrlPoints(zs, kT);
            const sm = samples(pts);
            const k = lineK(t), last = k * (NS - 1);
            let d = '';
            const P2 = [];
            for (let i = 0; i < sm.length; i++) {
              const s = sm[i];
              const wob = pluck * Math.sin(Math.PI * clamp(s[3] / 700));
              P2.push(proj(c3, s[0] + wob, s[1], s[2]));
            }
            const iLast = Math.min(sm.length - 1, Math.floor(last));
            for (let i = 0; i <= iLast; i++) d += (i ? 'L' : 'M') + P2[i][0].toFixed(1) + ' ' + P2[i][1].toFixed(1);
            let hx = P2[iLast][0], hy = P2[iLast][1];
            if (iLast < sm.length - 1) { const fr = last - iLast; hx = lerp(P2[iLast][0], P2[iLast + 1][0], fr); hy = lerp(P2[iLast][1], P2[iLast + 1][1], fr); d += `L${hx.toFixed(1)} ${hy.toFixed(1)}`; }
            attr(linePath, { d }); attr(lineHalo, { d });
            attr(head, { cx: hx, cy: hy, opacity: k > 0 && k < 0.999 && t < TAUT0 ? 1 : 0 });
            // nodes
            STEPS.forEach((s, i) => {
              const p = P2[(i + 1) * SEG];
              const on = tw(t, nodeTimes[i], nodeTimes[i] + 0.18, 'outCubic') * (1 - prog(t, CRYST + i * CRYST_STEP, CRYST + i * CRYST_STEP + 0.12));
              attr(nodeEls[i], { transform: `translate(${p[0].toFixed(1)},${p[1].toFixed(1)}) scale(${(0.5 + 0.5 * on).toFixed(3)})`, opacity: on.toFixed(3) });
            });
            // the paper hides what is beneath it
            const cs = [[0, 0], [1920, 0], [1920, 1080], [0, 1080]].map(([x, y]) => proj(c3, x, y, 0));
            const g = Math.round(lerp(64, 255, 1 - paperA));
            attr(maskPoly, { points: cs.map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' '), fill: `rgb(${g},${g},${g})` });
          }
        }

        // ---------- 2D phase ----------
        const in2D = t >= SWITCH;
        world.style.display = in2D ? '' : 'none';
        if (!in2D) { fabric.style.display = 'none'; css(exTag, { opacity: 0.6 * tw(t, 103.6, 104.2) }); scrim.style.opacity = 0; collapseLine.style.opacity = 0; return; }
        const r0 = REST(), sw0 = SW0();
        // after the hand-over the camera settles into the rest layout as the list crystallises
        const kSet = ease.inOutCubic(prog(t, SWITCH, SWITCH + 0.95));
        const rcx = lerp(sw0.cx, r0.cx, kSet), rvc = lerp(sw0.vc, r0.vc, kSet);
        // camera: rest after crystallisation, a slow push through the Mondays, then the long pull-back
        const kPull = prog(t, HIT, TEND);
        const pullE = 1 - Math.pow(1 - kPull, 1.7);
        const fPush = lerp(1, 1.035, ease.inOutQuad(prog(t, SAVED, HIT)));
        const f = Math.exp(lerp(Math.log(fPush), Math.log(0.105), pullE));
        const listMidV = V0 + (LIST_H * S) / 2 - 10;
        const kRe = ease.inOutCubic(prog(t, HIT, HIT + 2.2));
        const cxF = lerp(lerp(rcx, X0 + COLW * S / 2 - 11 * S, kRe), X0, ease.inOutQuad(prog(t, 112.2, 113.35)));
        const vc = lerp(rvc, listMidV, kRe);
        world.style.transform = `translate(960px,540px) scale(${f}) translate(${-cxF}px,${-vc}px)`;

        // list placement (rail on the caret axis)
        css(list, { transform: `translate(${X0 - RAILX * S}px,${V0}px) scale(${S})` });
        // rail: from the paper level down past the frame, then retracts to the first and last step
        const kR = ease.inOutCubic(prog(t, SWITCH, SWITCH + 0.55));
        const railTop = lerp(-V0 / S, 11, kR), railBot = lerp((V_END - V0) / S, ROWY[13] + 11, kR);
        const railDim = lerp(1, 0.5, tw(t, 105.9, 106.5));
        css(rail, { top: railTop, height: railBot - railTop, opacity: railDim });
        // crystallised rows
        rows.forEach((row, i) => {
          const tc = CRYST + i * CRYST_STEP;
          const k = tw(t, tc, tc + 0.38, 'outCubic');
          const st = rowState(i, t);
          if (st !== row.state) {
            row.state = st;
            for (const [key, e] of Object.entries(row.dot)) e.style.opacity = key === st ? 1 : 0;
            row.dur.style.opacity = st === 'done' ? 1 : 0;
          }
          css(row.dotWrap, { transform: `scale(${(0.55 + 0.45 * ease.outBack(clamp(k))).toFixed(3)})`, opacity: k.toFixed(3) });
          const kt = tw(t, tc + 0.04, tc + 0.44, 'outCubic');
          css(row.tile, { opacity: kt.toFixed(3), transform: `translateX(${((1 - kt) * -14).toFixed(2)}px)` });
          css(row.body, { opacity: kt.toFixed(3), transform: `translateX(${((1 - kt) * -22).toFixed(2)}px)` });
        });
        const kSec = tw(t, CRYST + BRANCH * CRYST_STEP + 0.05, CRYST + BRANCH * CRYST_STEP + 0.45, 'outCubic');
        css(sect, { opacity: kSec, transform: `translateX(${(1 - kSec) * -16}px)` });
        css(elbow, { opacity: kSec }); css(other, { opacity: kSec * 0.9 });

        // the run: a bright head travels the rail each Monday
        let w = -1; for (let k = 0; k < MONDAYS.length; k++) if (t >= MONDAYS[k]) w = k;
        if (w >= 0 && t < HIT + 0.35) {
          const ltw = t - MONDAYS[w];
          const stop = w === 2 ? rowT(APPROVAL) : 1;
          const kk = clamp(ltw / SWEEP);
          const pos = Math.min(kk, stop);
          const yHead = 11 + pos * ROWY[13];
          const fadeLit = 1 - prog(ltw, 0.62, 0.8) * (w === 2 ? 0 : 1);
          css(railLit, { top: 11, height: Math.max(0, yHead - 11), opacity: (ltw < 0.04 ? ltw / 0.04 : 1) * fadeLit });
          css(runHead, { top: yHead - 4.5, opacity: kk > 0 && kk < stop ? 1 : 0 });
        } else { css(railLit, { opacity: 0 }); css(runHead, { opacity: 0 }); }

        // draft card, top right; Tested flips to Saved
        const kCard = tw(t, CARD_IN, CARD_IN + 0.5, 'outCubic');
        const outR = 1 - tw(t, HIT - 0.12, HIT + 0.22, 'inOutQuad');
        // "Open task" is pressed; the card gives way to the task it saved
        const kOpen = Math.sin(Math.PI * prog(t, OPEN - 0.08, OPEN + 0.12));
        const kGone = tw(t, OPEN + 0.04, OPEN + 0.34, 'inOutCubic');
        const [cwx, cwy] = RS(r0, RCOL_SX, LIST_SY);
        css(card, { transform: `translate(${cwx}px,${cwy - kGone * 26}px) scale(${SR}) translateY(${(1 - kCard) * 18}px)`, opacity: kCard * (1 - kGone), display: kGone >= 1 ? 'none' : '' });
        css(openBtn, { background: kOpen > 0.01 ? `rgba(247,245,243,${(0.1 * kOpen).toFixed(3)})` : D.bg, transform: `scale(${(1 - 0.05 * kOpen).toFixed(3)})` });
        const kFlip = tw(t, SAVED, SAVED + 0.4, 'inOutCubic');
        css(stTested, { transform: `rotateX(${kFlip * 90}deg)`, opacity: kFlip < 0.5 ? 1 : 0 });
        css(stSaved, { transform: `rotateX(${(kFlip - 1) * 90}deg)`, opacity: kFlip >= 0.5 ? 1 : 0 });
        css(btnsA, { opacity: 1 - tw(t, SAVED, SAVED + 0.25) });
        css(btnsB, { opacity: tw(t, SAVED + 0.08, SAVED + 0.3) });

        // runs list under the card: newest first
        const kRuns = tw(t, MONDAYS[0] - 0.2, MONDAYS[0] + 0.2, 'outCubic');
        const [rwx, rwy] = RS(r0, RCOL_SX, LIST_SY + 300);
        css(runs, { transform: `translate(${rwx}px,${rwy}px) scale(${SR})`, opacity: kRuns * outR });
        runRows.forEach((r, k) => {
          const tin = MONDAYS[k] + (k === 2 ? rowT(APPROVAL) * SWEEP + 0.05 : SWEEP + 0.03);
          const a = tw(t, tin, tin + 0.3, 'outCubic');
          // rows that arrived later push this one down
          let slot = 0; for (let j = k + 1; j < RUNS.length; j++) { const tj = MONDAYS[j] + (j === 2 ? rowT(APPROVAL) * SWEEP + 0.05 : SWEEP + 0.03); slot += tw(t, tj - 0.3, tj + 0.02, 'inOutCubic'); }   // make room first, then the new run lands
          css(r, { opacity: a, transform: `translateY(${(slot * 36 - (1 - a) * 10).toFixed(2)}px)` });
        });

        // the scheduled task, left
        const kTask = tw(t, TASK_IN, TASK_IN + 0.5, 'outCubic');
        const [twx, twy] = RS(r0, RCOL_SX, LIST_SY);
        css(task, { transform: `translate(${twx}px,${twy + (1 - kTask) * 16}px) scale(${SR})`, opacity: kTask * outR });
        // the calendar ruler, bottom: the marker steps from Monday to Monday
        const kRul = tw(t, TASK_IN - 0.1, TASK_IN + 0.4, 'outCubic');
        const [uwx, uwy] = RS(r0, LIST_SX, RULER_SY);
        css(ruler, { transform: `translate(${uwx}px,${uwy}px)`, opacity: kRul * outR });
        let mx = 4 * DAY; // the day the task was saved (Fri 10-09)
        for (let k = 0; k < MONDAYS.length; k++) mx = lerp(mx, (k + 1) * 7 * DAY, ease.inOutCubic(prog(t, MONDAYS[k] - 0.42, MONDAYS[k])));
        css(marker, { left: mx - 1 });
        monEls.forEach((e, k) => {
          const hit = k >= 1 && k <= 5 ? tw(t, MONDAYS[k - 1] - 0.05, MONDAYS[k - 1] + 0.1) : 0;
          css(e, { opacity: (0.45 + 0.55 * hit).toFixed(3), color: hit > 0.5 ? D.fg : D.muted });
        });

        // ---------- S6-04: identical runs tile into a fabric ----------
        list.style.opacity = t >= HIT ? clamp((f - 0.36) / 0.14).toFixed(3) : '1';
        const kHead = tw(t, HIT, HIT + 0.4, 'outCubic');
        css(colHead, { opacity: kHead });
        fabric.style.display = t >= HIT - 0.02 ? '' : 'none';
        if (t >= HIT - 0.02) {
          if (!sprites || (sprites.fontsPending && document.fonts.status === 'loaded')) { sprites = buildSprites(); sprites.fontsPending = document.fonts.status !== 'loaded'; }
          fctx.setTransform(1, 0, 0, 1, 0, 0);
          fctx.clearRect(0, 0, 1920, 1080);
          const lvl = Math.min(4, Math.max(0, Math.floor(Math.log2(1 / Math.max(f, 1e-3)))));
          const mip = sprites.mips[lvl], amip = sprites.abs[lvl];
          const kAbs = clamp((0.28 - f) / 0.12);                          // far away: only the threads remain
          const sw = mip.cv.width / mip.s, sh = mip.cv.height / mip.s;  // world size of the sprite
          const live = clamp((f - 0.36) / 0.14);                          // the live DOM column hands over to the sprite
          // the first three rings stamp out on the beat; beyond that a soft elliptical wave fills the frame
          const ringT = (i, j, r) => {
            if (r <= 3) return HIT + (r - 1) * BEAT;
            const d = Math.hypot(i * PW / 1920, j * PH / 1080) * 2;
            return HIT + 2 * BEAT + 0.35 + (d - 1.2) * 0.42;
          };
          const fillR = lerp(0.3, 1.75, ease.inOutQuad(prog(t, HIT + 1.6, 113.05)));
          const ci = Math.ceil(960 / (PW * f)) + 1, cj = Math.ceil(540 / (PH * f)) + 1;
          for (let j = -cj; j <= cj; j++) for (let i = -ci; i <= ci; i++) {
            if (!i && !j && live >= 1) continue;
            const ring = Math.max(Math.abs(i), Math.abs(j));
            let a;
            if (!ring) a = 1 - live;
            else if (ring <= 3) { const t0 = ringT(i, j, ring) + 0.06 * F.hash(i * 31 + j * 17); a = tw(t, t0, t0 + 0.42, 'outCubic'); }
            else { // beyond: the weave spreads to the frame's edges, measured on screen
              const sd = Math.hypot(i * PW * f / 960, j * PH * f / 540) + 0.08 * F.hash(i * 31 + j * 17);
              a = ease.outCubic(clamp((fillR - sd) / 0.3));
            }
            if (a <= 0) continue;
            const pull = ring <= 3 ? (1 - a) * 0.12 : 0;   // the first rings stamp in; the outer weave only fades in, in register
            const wx = X0 + (i * PW) * (1 - pull) - sprites.ox, wy = V0 + (j * PH) * (1 - pull) - sprites.oy;
            const sx = 960 + (wx - cxF) * f, sy = 540 + (wy - vc) * f;
            if (sx > 1920 || sy > 1080 || sx + sw * f < 0 || sy + sh * f < 0) continue;
            const base = a * (ring ? 0.92 - 0.3 * clamp((ring - 1) / 9) : 1);
            if (kAbs < 1) { fctx.globalAlpha = base * (1 - kAbs); fctx.drawImage(mip.cv, sx, sy, sw * f, sh * f); }
            if (kAbs > 0) { fctx.globalAlpha = base * kAbs * 0.85; fctx.drawImage(amip.cv, sx, sy, sw * f, sh * f); }
            fctx.globalAlpha = base;
            if (f > 0.22 && ring) { // each copy is a Monday: its date above the column
              const dd = new Date(Date.UTC(2026, 10, 9 + 7 * (i + 21 * j)));
              fctx.globalAlpha *= clamp((f - 0.22) / 0.15);
              fctx.font = `400 ${13 * S * f}px ${MONO}`; fctx.fillStyle = D.muted; fctx.textBaseline = 'middle';
              fctx.fillText(`${String(dd.getUTCMonth() + 1).padStart(2, '0')}-${String(dd.getUTCDate()).padStart(2, '0')}`, 960 + (X0 + i * PW * (1 - pull) - RAILX * S - cxF) * f, 540 + (V0 + j * PH * (1 - pull) - 32 * S - vc) * f);
            }
          }
          fctx.globalAlpha = 1;
        }
        // subtitle band stays calm
        scrim.style.opacity = (env(t, 110.25, TEND, 0.4, 0.2)).toFixed(3);

        // ---------- 113.4–113.6: everything collapses into one blue line ----------
        const q = ease.inCubic(prog(t, SQUEEZE0, TEND));
        css(squeeze, { transform: q > 0 ? `scaleX(${Math.max(0.0005, 1 - q)})` : 'none', opacity: (1 - q * q).toFixed(3) });
        css(collapseLine, { opacity: q.toFixed(3) });
        css(exTag, { opacity: (0.6 * (1 - tw(t, HIT, HIT + 0.4))).toFixed(3) });
      };

      // ======================= the three depth worlds (canvas textures) =======================
      function sheetLabel(c, kind, text, color) {
        // a file chip label in the sheet's corner: [XLSX] name
        c.font = `600 15px ${MONO}`;
        const ext = { sheet: 'XLSX', slides: 'PPTX', code: 'PY' }[kind];
        const ew = c.measureText(ext).width + 14;
        roundRect(c, 36, 30, ew, 26, 5); c.fillStyle = color; c.fill();
        c.fillStyle = '#141312'; c.textBaseline = 'middle'; c.fillText(ext, 43, 44);
        c.font = `500 20px ${SANS}`; c.fillStyle = 'rgba(247,245,243,.86)'; c.fillText(text, 36 + ew + 12, 44);
      }
      function frame(c, color) {
        c.strokeStyle = rgba(color, 0.55); c.lineWidth = 2; c.strokeRect(1, 1, SW - 2, SH - 2);
      }
      function drawSheetWorld() {
        const cv = h('canvas', { width: SW, height: SH }), c = cv.getContext('2d'), r = F.rng(501);
        c.fillStyle = 'rgba(22,21,20,.9)'; c.fillRect(0, 0, SW, SH);
        const top = 80, cw = 128, rh = 36, x0 = 70;
        c.fillStyle = rgba(D.sheet, 0.1); c.fillRect(x0, top, SW - x0 - 30, rh);
        c.textBaseline = 'middle';
        for (let ci = 0; ci < 15; ci++) { c.font = `500 15px ${MONO}`; c.fillStyle = rgba(D.sheet, 0.85); c.fillText(String.fromCharCode(65 + ci), x0 + ci * cw + cw / 2 - 5, top + rh / 2); }
        const heads = L(['区域', '门店', '月份', '销售额', '同比', '客流', '客单价', '折扣率', '品类', '订单数', '退款', '毛利', '库存', '新店', '备注'], ['Region', 'Store', 'Month', 'Sales', 'YoY', 'Footfall', 'Basket', 'Discount', 'Category', 'Orders', 'Refunds', 'Margin', 'Stock', 'New', 'Note']);
        const regions = L(['华东', '华南', '华北', '西南'], ['East', 'South', 'North', 'Southwest']);
        for (let ri = 1; ri < 30; ri++) {
          const y = top + ri * rh;
          if (y > SH - 70) break;
          c.font = `400 13px ${MONO}`; c.fillStyle = 'rgba(161,157,155,.6)'; c.fillText(String(ri), 30, y + rh / 2);
          for (let ci = 0; ci < 15; ci++) {
            const x = x0 + ci * cw;
            let s;
            if (ri === 1) { s = heads[ci]; c.font = `500 15px ${SANS}`; c.fillStyle = 'rgba(247,245,243,.75)'; }
            else {
              c.font = `400 15px ${MONO}`; c.fillStyle = 'rgba(247,245,243,.5)';
              if (ci === 0) s = regions[ri % 4]; else if (ci === 1) s = `S${String(10 + (ri * 7) % 40).padStart(2, '0')}`;
              else if (ci === 2) s = `2026-${String(1 + (ri % 9)).padStart(2, '0')}`;
              else if (ci === 4) { const v = -(r() * 12).toFixed(1); s = `${v}%`; c.fillStyle = v < -8 ? rgba(D.sheet, 0.95) : 'rgba(247,245,243,.5)'; }
              else if (ci === 7) s = `${(12 + r() * 8).toFixed(0)}%`;
              else s = K.fmt(Math.round(r() * (ci === 3 ? 1800000 : 9000)));
            }
            c.fillText(s, x + 10, y + rh / 2);
          }
        }
        c.strokeStyle = 'rgba(247,245,243,.07)'; c.lineWidth = 1;
        for (let ci = 0; ci <= 15; ci++) { c.beginPath(); c.moveTo(x0 + ci * cw + 0.5, top); c.lineTo(x0 + ci * cw + 0.5, SH - 70); c.stroke(); }
        for (let ri = 0; ri <= 29; ri++) { const y = top + ri * rh; if (y > SH - 70) break; c.beginPath(); c.moveTo(x0, y + 0.5); c.lineTo(SW - 30, y + 0.5); c.stroke(); }
        c.strokeStyle = D.sheet; c.lineWidth = 2.5; c.strokeRect(x0 + 4 * cw, top + 6 * rh, cw, rh);
        const tabs = L(['清洗明细', '区域×月份', '品类×月份', '门店排名', '结论'], ['Clean data', 'Region×Month', 'Category×Month', 'Store ranking', 'Findings']);
        let tx = x0; c.font = `500 16px ${SANS}`;
        tabs.forEach((s, i) => { const w = c.measureText(s).width + 32; c.fillStyle = i === 1 ? rgba(D.sheet, 0.18) : 'rgba(247,245,243,.04)'; c.fillRect(tx, SH - 56, w, 36); c.fillStyle = i === 1 ? D.sheet : 'rgba(247,245,243,.55)'; c.fillText(s, tx + 16, SH - 38); tx += w + 4; });
        sheetLabel(c, 'sheet', T('s1_out'), D.sheet);
        frame(c, D.sheet);
        return cv;
      }
      function drawSlidesWorld() {
        const cv = h('canvas', { width: SW, height: SH }), c = cv.getContext('2d'), r = F.rng(702);
        c.fillStyle = 'rgba(22,21,20,.9)'; c.fillRect(0, 0, SW, SH);
        const titles = L(['40 家门店销售下滑：原因与建议', '结论先行：下滑 6.8%', '不是客流：客流 −1.2%', '不是新店：剔除后仍 −6.1%', '是折扣：12% → 19%', '集中在华南 8 家门店', '数据修正说明', '建议', '下一步与时间表', '附：区域明细', '附：品类明细', '附：方法'],
          ['Sales decline across 40 stores', 'Bottom line: down 6.8%', 'Not footfall: −1.2%', 'Not new stores: still −6.1%', 'Discounts: 12% → 19%', 'Concentrated in South China', 'Data correction', 'Recommendations', 'Next steps', 'Appendix: regions', 'Appendix: categories', 'Appendix: method']);
        const sw = 430, shh = 242, gx = 46, gy = 52, ox = 66, oy = 100;
        for (let k = 0; k < 12; k++) {
          const col = k % 4, row = Math.floor(k / 4), x = ox + col * (sw + gx), y = oy + row * (shh + gy);
          c.fillStyle = 'rgba(247,245,243,.035)'; c.fillRect(x, y, sw, shh);
          c.strokeStyle = rgba(D.slides, 0.55); c.lineWidth = 1.5; c.strokeRect(x + 0.5, y + 0.5, sw, shh);
          c.fillStyle = rgba(D.slides, 0.8); c.fillRect(x + 22, y + 22, 26, 4);
          c.font = `500 19px ${SANS}`; c.fillStyle = 'rgba(247,245,243,.82)'; c.textBaseline = 'alphabetic'; c.fillText(titles[k], x + 22, y + 56);
          if (k % 3 === 1) { // bars
            for (let b = 0; b < 6; b++) { const bh = 30 + r() * 100; c.fillStyle = b === 3 ? rgba(D.slides, 0.85) : rgba(D.slides, 0.32); c.fillRect(x + 30 + b * 60, y + shh - 26 - bh, 36, bh); }
          } else if (k % 3 === 2) { // KPI cards
            for (let b = 0; b < 3; b++) { c.strokeStyle = 'rgba(247,245,243,.14)'; c.strokeRect(x + 22 + b * 130, y + 90, 116, 110); c.font = `600 28px ${MONO}`; c.fillStyle = b === 0 ? D.slides : 'rgba(247,245,243,.7)'; c.fillText(['−6.8%', '−1.2%', '19%'][b], x + 32 + b * 130, y + 140); c.fillStyle = 'rgba(161,157,155,.6)'; c.fillRect(x + 32 + b * 130, y + 160, 80, 4); c.fillRect(x + 32 + b * 130, y + 172, 56, 4); }
          } else { // text lines
            for (let l = 0; l < 5; l++) { c.fillStyle = 'rgba(247,245,243,.18)'; c.fillRect(x + 22, y + 92 + l * 26, 180 + r() * 200, 6); }
          }
        }
        sheetLabel(c, 'slides', T('s2_out'), D.slides);
        frame(c, D.slides);
        return cv;
      }
      function drawCodeWorld() {
        const cv = h('canvas', { width: SW, height: SH }), c = cv.getContext('2d');
        c.fillStyle = 'rgba(35,33,32,.94)'; c.fillRect(0, 0, SW, SH);
        const code = [
          'def merge_daily(erp: pd.DataFrame, pos: pd.DataFrame, shop: pd.DataFrame) -> pd.DataFrame:',
          '    frames = [normalize(df, src) for df, src in ((erp, "ERP"), (pos, "POS"), (shop, "' + L('电商', 'Shop') + '"))]',
          '    merged = pd.concat(frames, ignore_index=True)',
          '    merged["date"] = pd.to_datetime(merged["date"], errors="coerce")',
          '    merged = merged.dropna(subset=["date", "amount"])',
          '    return merged.drop_duplicates(["order_id", "source"])',
          '',
          'def flag_anomalies(daily: pd.DataFrame, window: int = 28, k: float = 2.5) -> pd.DataFrame:',
          '    by_store = daily.groupby(["store", "date"])["amount"].sum().unstack("store")',
          '    baseline = by_store.rolling(window, min_periods=7).median()',
          '    return by_store[(by_store / baseline > k) | (by_store < 0)].stack().rename("anomaly").reset_index()',
          '',
          'def test_merge_drops_duplicate_orders():',
          '    assert len(merge_daily(*fixtures("dup"))) == 4790',
          '',
          'def test_dates_in_two_formats_align():',
          '    assert merge_daily(*fixtures("dates"))["date"].notna().all()',
          '',
          'def test_anomaly_flagged_at_3x_baseline():',
          '    assert len(flag_anomalies(daily_with_spike())) == 1',
          '',
          '$ pytest -q',
          '.....                                                    [100%]',
          '5 passed in 0.41s',
        ];
        c.textBaseline = 'middle';
        code.forEach((ln, i) => {
          const y = 110 + i * 40;
          if (y > SH - 30) return;
          c.font = `400 15px ${MONO}`; c.fillStyle = 'rgba(161,157,155,.45)'; c.fillText(String(i + 1).padStart(2, ' '), 36, y);
          c.font = `400 19px ${MONO}`;
          let x = 90;
          const parts = ln.split(/(\bdef\b|\breturn\b|\bfor\b|\bin\b|\bassert\b|"[^"]*"|#.*$)/);
          for (const p of parts) {
            if (!p) continue;
            c.fillStyle = /^(def|return|for|in|assert)$/.test(p) ? '#a9cbf5' : p.startsWith('"') ? 'rgba(143,209,159,.85)' : ln.startsWith('5 passed') ? D.success : 'rgba(247,245,243,.72)';
            c.fillText(p, x, y); x += c.measureText(p).width;
          }
        });
        sheetLabel(c, 'code', T('s4_out'), '#8cb4f0');
        frame(c, '#8cb4f0');
        return cv;
      }
    },
  });
})();
