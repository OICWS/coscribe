// Montage (94.0–100.0): four hard cuts on the music hits, 1.5 s each.
// S5-01 the desktop Browser panel, S5-02 Connectors settings, S5-03 the
// ModelPicker, S5-04 a corner of the surface; then the surface empties out.
// v2: each cut floats the app's light UI as white cards on a vivid colour field
// (browser → --field-doc, connectors → --field-flow, model picker → --field-slides);
// the last cut is the light surface itself, emptying out for 100.0.
// All UI is the app's light theme, rebuilt 1:1 from the real components
// (DesktopBrowserPanel.tsx, settings/SettingsModal.tsx + ConnectorsTab.tsx,
// ModelPicker.tsx) and framed by a slow 2.5D camera.
(function () {
  const { h, css, clamp, lerp, tw, ease, prog, T, L } = F;
  const S0 = 94.0, S1 = 100.0;
  const CUTS = [94.0, 95.5, 97.0, 98.5];
  const MONO = '"IBM Plex Mono", "Noto Sans SC", ui-monospace, monospace';

  // feather-style icons, as in components/icons.tsx
  const ICON = {
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3z"/>',
    close: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    more: '<circle cx="12" cy="5" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="12" cy="19" r="1.5" fill="currentColor" stroke="none"/>',
    expand: '<path d="M14 4h6v6"/><path d="M20 4l-6 6"/><path d="M10 20H4v-6"/><path d="M4 20l6-6"/>',
    left: '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    right: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    reload: '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    ext: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    pencil: '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
    pick: '<path d="M5 3l6.5 16 2.3-6.7L20.5 10z"/><path d="M14 14l5 5"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    chevD: '<path d="M6 9l6 6 6-6"/>',
    folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    key: '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
    tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    term: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    book: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  };
  const icon = (name, size = 16, color = 'currentColor', extra = '') =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none;${extra}">${ICON[name]}</svg>`;
  const iconBtn = (name, size = 16, extra = '') => `<span style="display:flex;width:28px;height:28px;align-items:center;justify-content:center;border-radius:6px;color:var(--muted);flex:none;${extra}">${icon(name, size)}</span>`;
  const POINTER = (fill, stroke) => `<svg width="30" height="42" viewBox="0 0 20 28"><path d="M2 2 L2 21 L6.6 16.8 L9.8 24.2 L13 22.8 L9.9 15.6 L16 15.6 Z" fill="${fill}" stroke="${stroke}" stroke-width="1.3" stroke-linejoin="round"/></svg>`;

  // sound-sync events
  CUTS.forEach((t, i) => F.event(t, 'cut', { i }));                 // the four percussion hits
  F.event(94.02, 'scroll', { dur: 0.45 });                          // page scroll: a light brush
  F.event(94.30, 'thread', { dur: 0.45 });                          // the blue line pulled taut (silk)
  F.event(94.76, 'tick', { what: 'element picked' });
  F.event(94.95, 'whoosh', { what: 'citation flies out', soft: true });
  F.event(95.92, 'tick', { what: 'connector connected' });
  F.event(96.22, 'paper', { what: 'custom connector row', soft: true });
  F.event(97.10, 'click', { what: 'model pill' });
  F.event(97.78, 'key', { ch: '3' });
  F.event(98.50, 'riser', { dur: 1.5 });
  F.event(100.0, 'stop', { what: 'everything stops' });

  F.scene({
    id: 's5_montage', start: S0, end: S1, z: 20,
    build(layer) {
      K.surface(layer);
      const SHADOW = '0 30px 80px rgba(16,24,64,.22), 0 2px 8px rgba(16,24,64,.10)';
      const FIELD = ['var(--field-doc)', 'var(--field-flow)', 'var(--field-slides)', null];
      const glows = [];
      const shots = [0, 1, 2, 3].map((i) => {
        const e = h('div', { class: 'abs' }); css(e, { inset: 0, display: 'none' }); layer.append(e);
        if (FIELD[i]) {
          const f = h('div', { class: 'abs' }); css(f, { inset: 0, background: FIELD[i] });
          const g = h('div', { class: 'abs' });
          css(g, { left: -600, top: -700, width: 1800, height: 1800, borderRadius: '50%', background: 'radial-gradient(closest-side, rgba(255,255,255,.26), rgba(255,255,255,.08) 55%, rgba(255,255,255,0))' });
          e.append(f, g); glows[i] = g;
        }
        return e;
      });
      const grainU = K.grain(layer, 0.035);
      const toStage = (r) => { const st = layer.getBoundingClientRect(), sc = st.width / 1920; return { x: (r.left - st.left) / sc, y: (r.top - st.top) / sc, w: r.width / sc, h: r.height / sc }; };
      // a corner caption per shot (bottom-left; no VO runs in the montage)
      function caption(parent, text) {
        const c = h('div', { class: 'abs', text });
        css(c, { left: 96, top: 984, fontFamily: 'var(--sans)', fontSize: 24, fontWeight: 500, color: 'var(--field-ink)', letterSpacing: '.02em', whiteSpace: 'nowrap', textShadow: '0 2px 16px rgba(16,24,64,.25)' });
        parent.append(c); return c;
      }

      // ================= S5-01 · Browser panel =================
      const b = shots[0];
      const bcam = K.camera(b, { perspective: 2600 });
      // a blurred conversation column behind the panel, for depth
      const chatBg = h('div', { class: 'abs' });
      css(chatBg, { left: 150, top: 230, width: 440, filter: 'blur(3px)', opacity: 0.55 });
      [[0.92, 'var(--card-bg)', 54], [0.7, null, 14], [0.86, null, 14], [0.6, null, 14], [0.8, null, 14], [0.4, null, 14], [0.72, 'var(--card-bg)', 40], [0.9, null, 14], [0.66, null, 14]].forEach(([w, bgc, hh], i) => {
        const r = h('div'); css(r, { width: `${w * 100}%`, height: hh, margin: bgc ? '26px 0 26px auto' : '12px 0', borderRadius: bgc ? 16 : 4, background: bgc ? 'rgba(255,255,255,.85)' : 'rgba(255,255,255,.45)' }); chatBg.append(r);
      });
      bcam.world.append(chatBg);
      const panel = h('div', { class: 'abs' });
      css(panel, { left: 640, top: 150, width: 820, height: 780, borderRadius: 12, border: '1px solid var(--border)', background: 'var(--bg)', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxShadow: SHADOW, fontFamily: 'var(--sans)', color: 'var(--fg)' });
      const tabs = h('div');
      css(tabs, { display: 'flex', height: 44, alignItems: 'center', gap: 4, padding: '0 8px', flex: 'none' });
      const tab = (title, active) => `<div style="display:flex;height:28px;max-width:200px;flex:1 1 200px;align-items:center;gap:6px;border-radius:8px;padding:0 6px;font-size:13px;border:1px solid ${active ? 'rgba(32,30,29,.26)' : 'transparent'};background:${active ? 'var(--bg)' : 'transparent'};color:${active ? 'var(--fg)' : 'var(--muted)'}">${icon('globe', 14)}<span style="flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis">${title}</span>${active ? `<span style="display:flex;width:20px;height:20px;align-items:center;justify-content:center;color:var(--muted)">${icon('close', 12)}</span>` : ''}</div>`;
      tabs.innerHTML = `<div style="display:flex;flex:1;min-width:0;gap:2px">${tab(L('2026 年 9 月零售行业月报', 'Retail monthly · Sep 2026'), true)}${tab(L('社会消费品零售总额', 'Retail sales, national'), false)}</div>${iconBtn('plus')}${iconBtn('more')}${iconBtn('expand')}${iconBtn('close')}`;
      const toolbar = h('div');
      css(toolbar, { display: 'flex', height: 40, alignItems: 'center', gap: 2, padding: '0 8px 6px', flex: 'none' });
      toolbar.innerHTML = `${iconBtn('left')}${iconBtn('right', 16, 'opacity:.35')}${iconBtn('reload', 15)}` +
        `<div style="margin:0 4px;display:flex;height:32px;min-width:0;flex:1;align-items:center;border-radius:8px;border:1px solid var(--border);background:var(--bg)"><span style="flex:1;text-align:center;font-size:13px;padding:0 10px">retail-report.example/2026-09</span><span style="display:flex;width:24px;height:24px;margin-right:4px;align-items:center;justify-content:center;color:var(--muted)">${icon('ext', 14)}</span></div>` +
        `${iconBtn('pencil', 15)}${iconBtn('pick', 16, 'background:rgba(42,120,214,.15);color:var(--accent)')}`;
      const agentBar = h('div');
      css(agentBar, { margin: '0 8px 6px', display: 'flex', alignItems: 'center', gap: 8, borderRadius: 8, background: 'rgba(42,120,214,.10)', padding: '6px 10px', fontSize: 12, flex: 'none' });
      const pulse = h('span'); css(pulse, { width: 8, height: 8, borderRadius: 4, background: 'var(--accent)', flex: 'none' });
      const agentTx = h('span', { text: 'coscribe is using the browser' }); css(agentTx, { flex: 1 });
      const stopB = h('span', { html: `${icon('stop', 12)} Stop` }); css(stopB, { display: 'flex', alignItems: 'center', gap: 4, fontWeight: 500, padding: '2px 6px' });
      agentBar.append(pulse, agentTx, stopB);
      const pageWrap = h('div'); css(pageWrap, { flex: 1, minHeight: 0, padding: '0 6px 6px', display: 'flex' });
      const pageArea = h('div'); css(pageArea, { position: 'relative', flex: 1, overflow: 'hidden', borderRadius: 8, background: '#ffffff', border: '1px solid rgba(32,30,29,.06)' });
      pageWrap.append(pageArea);
      panel.append(tabs, toolbar, agentBar, pageWrap);
      bcam.world.append(panel);
      // the web page itself (a monthly retail report; example content)
      const page = h('div', { class: 'abs' });
      css(page, { left: 0, top: 0, width: '100%', padding: '34px 46px', fontFamily: '"Source Serif 4", "Noto Serif SC", Georgia, serif', color: '#26231f' });
      const lines = (n, seed) => { const r = F.rng(seed); let s = ''; for (let i = 0; i < n; i++) s += `<div style="height:9px;border-radius:2px;background:rgba(38,35,31,.13);margin:11px 0;width:${i === n - 1 ? 40 + r() * 30 : 88 + r() * 12}%"></div>`; return s; };
      const REG = [[L('华东', 'East'), '412.6', '−2.1%'], [L('华南', 'South'), '298.3', '−6.8%'], [L('华北', 'North'), '265.0', '−1.4%'], [L('西南', 'Southwest'), '187.9', '+0.6%'], [L('华中', 'Central'), '160.2', '−3.3%']];
      const PICK = 1;
      page.innerHTML =
        `<div style="font-family:var(--sans);font-size:11px;letter-spacing:.14em;color:#8a847d;text-transform:uppercase">Retail Report · ${L('月度', 'Monthly')}</div>` +
        `<div style="font-size:34px;line-height:1.25;margin:12px 0 8px;font-weight:600">${L('2026 年 9 月零售行业月报', 'Retail Monthly, September 2026')}</div>` +
        `<div style="font-family:var(--sans);font-size:12.5px;color:#8a847d;margin-bottom:22px">${L('发布于 2026-10-02 · 阅读约 8 分钟', 'Published 2026-10-02 · 8 min read')}</div>` +
        lines(3, 3) +
        `<div style="font-size:19px;font-weight:600;margin:28px 0 12px">${L('表 3　分区域门店销售（9 月）', 'Table 3 · Store sales by region (Sep)')}</div>` +
        `<div data-t="tbl" style="font-family:var(--sans);font-size:15px">` +
        `<div style="display:grid;grid-template-columns:1.3fr 1fr 1fr;padding:9px 12px;color:#8a847d;font-size:12.5px;border-bottom:1px solid rgba(38,35,31,.25)"><span>${L('区域', 'Region')}</span><span style="text-align:right">${L('销售额（亿元）', 'Sales (¥100m)')}</span><span style="text-align:right">${L('同比', 'YoY')}</span></div>` +
        REG.map(([a, v, y], i) => `<div data-row="${i}" style="display:grid;grid-template-columns:1.3fr 1fr 1fr;padding:10px 12px;border-bottom:1px solid rgba(38,35,31,.09)"><span>${a}</span><span style="text-align:right;font-family:${MONO};font-size:14px">${v}</span><span style="text-align:right;font-family:${MONO};font-size:14px;color:${y.startsWith('−') ? '#b23a2c' : '#2f7d4a'}">${y}</span></div>`).join('') +
        `</div>` + lines(6, 9);
      pageArea.append(page);
      const pickBox = h('div', { class: 'abs' }); css(pickBox, { border: '2px solid var(--accent)', background: 'rgba(42,120,214,.10)', pointerEvents: 'none', opacity: 0 });
      const pickLab = h('div', { class: 'abs' }); css(pickLab, { borderRadius: 4, background: 'var(--accent)', color: '#fff', fontSize: 10, fontWeight: 500, padding: '2px 6px', whiteSpace: 'nowrap', opacity: 0 });
      pageArea.append(pickBox, pickLab);
      const bsvg = h('svg', { class: 'full' }); css(bsvg, { overflow: 'visible' }); b.append(bsvg);
      let bThread = null, bThreadD = '';
      // the citation the picked row becomes
      const cite = h('div', { class: 'abs' });
      css(cite, { left: 0, top: 0, width: 470, padding: '14px 18px 13px', borderRadius: 14, background: '#fff', border: '1px solid var(--border)', boxShadow: '0 18px 50px rgba(32,30,29,.16)', fontFamily: 'var(--sans)', transformOrigin: '0 0', opacity: 0 });
      cite.innerHTML = `<div style="font-family:var(--serif);font-size:21px;color:var(--fg);line-height:1.35">“${L('华南 · 9 月门店销售同比 <span style="font-family:' + MONO + ';color:var(--danger)">−6.8%</span>', 'South · Sep store sales YoY <span style="font-family:' + MONO + ';color:var(--danger)">−6.8%</span>')}”</div>` +
        `<div style="display:flex;align-items:center;gap:6px;margin-top:8px;font-size:12.5px;color:var(--muted)">${icon('globe', 13)}retail-report.example/2026-09 · ${L('表 3', 'Table 3')}</div>`;
      b.append(cite);
      const bCap = caption(b, L('内置浏览器 · 查资料', 'Built-in browser · look things up'));
      const bEx = h('div', { class: 'abs', text: T('example') }); css(bEx, { right: 56, bottom: 40, fontSize: 13, color: 'var(--field-ink)', opacity: 0.8, letterSpacing: '.08em' }); b.append(bEx);

      // ================= S5-02 · Connectors =================
      const c = shots[1];
      const ccam = K.camera(c, { perspective: 2600 });
      // the app behind the modal (sidebar, a conversation), dimmed by the modal's real bg-black/60 backdrop
      const app = h('div', { class: 'abs' });
      css(app, { left: -400, top: -400, width: 2720, height: 1880, background: 'var(--bg)', filter: 'blur(2.5px)' });
      {
        const side = h('div', { class: 'abs' }); css(side, { left: 400, top: 400, width: 300, height: 1080, background: 'var(--card-bg)', borderRight: '1px solid var(--border)' });
        const r = F.rng(52);
        for (let i = 0; i < 14; i++) { const e = h('div', { class: 'abs' }); css(e, { left: 24, top: 120 + i * 44, width: 140 + r() * 110, height: 12, borderRadius: 4, background: 'rgba(32,30,29,.16)' }); side.append(e); }
        app.append(side);
        let y = 330;
        for (let i = 0; i < 9; i++) {
          const user = i % 4 === 0, e = h('div', { class: 'abs' });
          const w = user ? 380 + r() * 200 : 560 + r() * 420;
          css(e, user ? { left: 1960 - w, top: y, width: w, height: 52, borderRadius: 16, background: 'var(--card-bg)' } : { left: 1020, top: y, width: w, height: 12, borderRadius: 4, background: 'rgba(32,30,29,.09)' });
          app.append(e); y += user ? 92 : 30;
        }
      }
      // v2: the settings modal floats on the field by itself (no dimmed app behind it)
      const modal = h('div', { class: 'abs' });
      css(modal, { left: 410, top: 210, width: 1100, height: 480, borderRadius: 16, border: '1px solid var(--border)', background: 'var(--bg)', boxShadow: SHADOW, display: 'flex', overflow: 'hidden', fontFamily: 'var(--sans)', color: 'var(--fg)' });
      const navItem = (ic, label, active) => `<div style="display:flex;height:36px;align-items:center;gap:12px;border-radius:8px;padding:0 12px;font-size:15px;${active ? 'background:var(--card-bg);font-weight:500' : 'color:rgba(32,30,29,.8)'}">${icon(ic, 18)}${label}</div>`;
      const nav = h('div');
      css(nav, { width: 240, flex: 'none', borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column' });
      nav.innerHTML = `<div style="padding:16px 16px 12px"><div style="display:flex;height:40px;align-items:center;gap:10px;border-radius:12px;border:1px solid var(--border);background:#fff;padding:0 12px;color:var(--muted);font-size:15px">${icon('search', 18)}Search</div></div>` +
        `<div style="display:flex;flex-direction:column;gap:20px;padding:8px 16px">` +
        `<div style="display:flex;flex-direction:column;gap:2px"><div style="margin-bottom:4px;padding:0 12px;font-size:13px;color:var(--muted)">Settings</div>${navItem('settings', 'General')}${navItem('folder', 'Workspace')}${navItem('key', 'Providers')}${navItem('tool', 'Tools')}${navItem('term', 'Environment')}</div>` +
        `<div style="display:flex;flex-direction:column;gap:2px"><div style="margin-bottom:4px;padding:0 12px;font-size:13px;color:var(--muted)">Customize</div>${navItem('book', 'Skills')}${navItem('link', 'Connectors', true)}</div></div>`;
      const main = h('div'); css(main, { flex: 1, minWidth: 0, padding: '56px 32px 0 40px', display: 'flex', flexDirection: 'column', gap: 20 });
      const headRow = h('div');
      css(headRow, { display: 'flex', alignItems: 'center', gap: 12 });
      headRow.innerHTML = `<div style="margin-right:4px;font-size:22px;font-weight:600">Connectors</div><div style="flex:1"></div>` +
        `<div style="display:flex;height:36px;width:240px;align-items:center;gap:8px;border-radius:8px;border:1px solid var(--border);background:#fff;padding:0 12px;color:var(--muted);font-size:14px">${icon('search', 16)}Search connectors</div>` +
        `<div style="display:flex;height:36px;align-items:center;gap:6px;border-radius:8px;background:#171614;color:#fcfcfb;padding:0 14px;font-size:14px;font-weight:500">${icon('plus', 16)} Add ${icon('chevD', 14)}</div>`;
      const GRIDC = 'minmax(0,1fr) 10rem 8rem';
      const thead = h('div'); css(thead, { display: 'grid', gridTemplateColumns: GRIDC, gap: 16, padding: '0 12px 8px', fontSize: 13, color: 'var(--muted)', borderBottom: '1px solid var(--border)' });
      thead.innerHTML = '<span>Connector</span><span>Type</span><span>Status</span>';
      const badge = (ch) => `<span style="display:flex;width:28px;height:28px;flex:none;align-items:center;justify-content:center;border-radius:8px;border:1px solid var(--border);background:#fff;font-size:12px;font-weight:500;color:var(--muted)">${ch}</span>`;
      const crow = (title, custom) => {
        const r = h('div'); css(r, { borderBottom: '1px solid var(--border)', padding: '4px 0' });
        const g = h('div'); css(g, { display: 'grid', gridTemplateColumns: GRIDC, gap: 16, alignItems: 'center', padding: '8px 12px', borderRadius: 8 });
        g.innerHTML = `<div style="display:flex;min-width:0;align-items:center;gap:12px">${badge(title[0])}<span style="font-size:15px">${title}</span></div>` +
          `<div style="display:flex;align-items:center;font-size:15px">Local${custom ? '<span style="margin-left:8px;border-radius:6px;background:var(--card-bg);padding:2px 6px;font-size:12px;color:var(--muted)">Custom</span>' : ''}</div>`;
        const st = h('div'); css(st, { display: 'flex', alignItems: 'center' });
        const chk = h('span', { html: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5" pathLength="1"/></svg>` });
        css(chk, { display: 'flex', color: 'var(--fg)' });
        const conn = h('span', { text: 'Connecting…' }); css(conn, { borderRadius: 6, padding: '4px 10px', fontSize: 14, boxShadow: 'inset 0 0 0 1.5px rgba(42,120,214,.5)', display: 'none' });
        st.append(chk, conn); g.append(st); r.append(g);
        return { r, g, chk, conn, path: chk.querySelector('path') };
      };
      const ms = crow('Microsoft 365', false), sl = crow('Slack', true);
      const ctable = h('div'); css(ctable, { display: 'flex', flexDirection: 'column' });
      ctable.append(thead, ms.r, sl.r);
      main.append(headRow, ctable);
      modal.append(nav, main);
      ccam.world.append(modal);
      const cCap = caption(c, L('连接常用工具', 'Connect the tools you already use'));

      // ================= S5-03 · ModelPicker =================
      const m = shots[2];
      const mcam = K.camera(m, { perspective: 2600 });
      const Z = K.Z;
      const mbox = K.inputBox(mcam.world, { y: 420, placeholder: T('placeholder') });
      css(mbox.bar, { color: 'var(--field-ink)' });   // the bar below the card sits on the field
      css(mbox.card, { boxShadow: SHADOW });
      const pill = mbox.modelChip;
      // ModelPicker.tsx trigger: the model name + chevron below the box, a ghost button
      css(pill, { display: 'inline-flex', alignItems: 'center', gap: 4 * Z, height: 32 * Z, padding: `0 ${8 * Z}px`, borderRadius: 8 * Z, fontFamily: 'var(--sans)', fontSize: 14 * Z });
      const pillTx = h('span', { text: 'deepseek-chat' });
      pill.textContent = ''; pill.append(pillTx); pill.insertAdjacentHTML('beforeend', icon('chevD', 14 * Z));
      const MODELS = ['deepseek-chat', 'kimi-k2', 'glm-4.6', 'qwen3:14b', 'claude-sonnet-4-5'];
      const menu = h('div', { class: 'abs' });
      css(menu, { minWidth: 192 * Z, padding: `${4 * Z}px 0`, borderRadius: 10 * Z, border: '1px solid var(--border)', background: 'var(--bg)', boxShadow: SHADOW, fontFamily: 'var(--sans)', overflow: 'hidden', transformOrigin: '100% 0', opacity: 0 });
      const mrows = MODELS.map((name, i) => {
        const r = h('div'); css(r, { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 * Z, padding: `${8 * Z}px ${12 * Z}px`, fontSize: 14 * Z, color: 'var(--fg)', margin: `0 ${4 * Z}px`, borderRadius: 6 * Z });
        const lab = h('span'); const num = h('span', { text: String(i + 1) }); css(num, { fontSize: 12 * Z, color: 'var(--muted)' });
        r.append(lab, num); menu.append(r); return { r, lab, name };
      });
      mcam.world.append(menu);
      const keycap = h('div', { class: 'abs', text: '3' });
      css(keycap, { width: 60, height: 60, borderRadius: 11, border: '1px solid rgba(32,30,29,.18)', background: '#fff', boxShadow: '0 3px 0 rgba(32,30,29,.16)', display: 'grid', placeItems: 'center', fontFamily: MONO, fontSize: 26, color: 'var(--fg)', opacity: 0 });
      mcam.world.append(keycap);
      const mptr = h('div', { class: 'abs', html: POINTER('#201e1d', '#fff') }); css(mptr, { left: 0, top: 0, opacity: 0, filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.25))' });
      m.append(mptr);
      caption(m, L('模型，随时换', 'Any model, switch any time'));

      // ================= S5-04 · the surface, a corner =================
      const s = shots[3];
      const scam = K.camera(s);
      const l1 = K.line(scam.world, { x: 180 + 750, y: 400, size: 52, align: 'left', maxW: 1500, color: 'var(--fg)' });
      const l2 = K.line(scam.world, { x: 180 + 750, y: 474, size: 32, align: 'left', maxW: 1500, color: 'var(--muted)' });
      const TX1 = L('在你自己的电脑上运行。', 'Runs on your own computer.');
      const TX2 = L('模型由你选，也可以完全离线。', 'Your choice of model — fully offline if you like.');
      const tree = h('div', { class: 'abs' });
      css(tree, { left: 184, top: 560, fontFamily: 'var(--sans)', fontSize: 20, color: 'var(--fg)' });
      const TREE = [['workspace', 0], ['reports', 1], [L('导出', 'exports'), 1], [L('模板', 'templates'), 1]];
      const trows = TREE.map(([name, lvl], i) => {
        const r = h('div', { html: `${icon('folder', 17, 'var(--muted)')}<span>${name}/</span>` });
        css(r, { display: 'flex', alignItems: 'center', gap: 10, height: 34, paddingLeft: lvl * 28, color: lvl ? 'var(--fg)' : 'var(--fg)', fontWeight: lvl ? 400 : 500 });
        tree.append(r); return r;
      });
      const guide = h('div', { class: 'abs' }); css(guide, { left: 8, top: 34, width: 1, height: 3 * 34 - 12, background: 'var(--border)' }); tree.append(guide);
      const status = h('div', { class: 'abs' });
      css(status, { left: 184, top: 730, display: 'flex', alignItems: 'center', gap: 10, fontFamily: MONO, fontSize: 15, color: 'var(--muted)' });
      status.innerHTML = `<span style="width:8px;height:8px;border-radius:4px;background:var(--success)"></span><span style="color:var(--fg)">127.0.0.1:8000</span><span>· ${L('本机', 'this computer')}</span>`;
      scam.world.append(tree, status);

      let shown = -1;
      return (lt, t) => {
        const idx = t < CUTS[1] ? 0 : t < CUTS[2] ? 1 : t < CUTS[3] ? 2 : 3;
        if (idx !== shown) { shots.forEach((e, i) => { e.style.display = i === idx ? '' : 'none'; }); shown = idx; }
        const u = t - CUTS[idx];
        if (glows[idx]) css(glows[idx], { transform: `translate(${(420 + 260 * Math.sin(t * 0.4 + idx)).toFixed(1)}px,${(80 + 90 * Math.cos(t * 0.33 + idx)).toFixed(1)}px)` });

        if (idx === 0) {
          bcam.set({ x: lerp(880, 930, ease.outQuad(prog(u, 0, 1.5))), y: lerp(500, 530, ease.inOutQuad(prog(u, 0, 1.5))), s: lerp(1.32, 1.45, ease.outCubic(prog(u, 0, 1.5))), ry: lerp(-6, -3.5, prog(u, 0, 1.5)), rx: 1.5 });
          css(chatBg, { transform: `translateX(${(-u * 30).toFixed(1)}px)` });
          css(page, { transform: `translateY(${((1 - ease.outCubic(prog(u, 0, 0.5))) * 190).toFixed(1)}px)` });
          css(pulse, { opacity: 0.45 + 0.55 * (0.5 + 0.5 * Math.cos(u * 6)) });
          const row = page.querySelector(`[data-row="${PICK}"]`);
          const pa = pageArea.getBoundingClientRect(), rr = row.getBoundingClientRect();
          const sc = pa.width / pageArea.offsetWidth || 1;
          // highlight box in the page area's own (unscaled) coordinates
          const hx = (rr.left - pa.left) / sc, hy = (rr.top - pa.top) / sc, hw = rr.width / sc, hh = rr.height / sc;
          const kp = tw(u, 0.72, 0.82, 'outQuad');
          css(pickBox, { left: hx - 2, top: hy - 2, width: hw + 4, height: hh + 4, opacity: kp });
          pickLab.textContent = `div ${Math.round(hw)}×${Math.round(hh)}`;
          css(pickLab, { left: hx - 2, top: hy - 22, opacity: kp });
          // the blue thread, in stage coordinates, into the row's left edge
          const R = toStage(rr);
          const ex = R.x + 6, ey = R.y + R.h / 2;
          const d = `M -40 ${ey + 210} C 300 ${ey + 200} ${ex - 260} ${ey} ${ex} ${ey}`;
          if (!bThread || Math.abs(ex - bThread.ex) > 0.5 || Math.abs(ey - bThread.ey) > 0.5) {
            bsvg.textContent = '';
            // a white halo under the brand-blue core reads on the field and on the page alike
            const halo = K.thread(bsvg, d, { color: '#fff', width: 7, glow: false });
            bThread = K.thread(bsvg, d, { color: '#3b5bfd', width: 2.8, glow: false }); bThread.halo = halo; bThread.ex = ex; bThread.ey = ey; bThreadD = d;
          }
          const kth = ease.inOutCubic(prog(u, 0.3, 0.76));
          bThread.halo.update(kth, { headOn: false }); bThread.update(kth);
          bThread.g.style.opacity = bThread.halo.g.style.opacity = 1 - tw(u, 1.0, 1.4);
          // citation flies out toward the viewer, back along the thread
          const kf = tw(u, 0.95, 1.45, 'inOutCubic');
          const x0 = R.x, y0 = R.y - 10, x1 = 120, y1 = 600;
          css(cite, { opacity: tw(u, 0.95, 1.08, 'outQuad'), transform: `translate(${lerp(x0, x1, kf).toFixed(1)}px, ${(lerp(y0, y1, kf) - Math.sin(Math.PI * kf) * 60).toFixed(1)}px) scale(${lerp(0.82, 1.12, kf).toFixed(4)}) rotate(${(-1.5 * Math.sin(Math.PI * kf)).toFixed(2)}deg)` });
          css(bCap, { opacity: tw(u, 0.05, 0.3, 'outQuad') });
        } else if (idx === 1) {
          // the whole modal in frame, settling in with a small 2.5D turn
          const kc = ease.outExpo(prog(u, 0, 0.6));
          ccam.set({ x: lerp(985, 970, ease.outQuad(prog(u, 0, 1.5))), y: 470, s: lerp(1.2, 1.3, kc) + 0.03 * prog(u, 0.6, 1.5), ry: lerp(7, 2.5, kc), rx: -1.5 });
          // Microsoft 365: Connecting… then the check
          const connected = u >= 0.42;
          ms.conn.style.display = connected ? 'none' : '';
          ms.chk.style.display = connected ? 'flex' : 'none';
          ms.path.setAttribute('stroke-dasharray', '1 1');
          ms.path.setAttribute('stroke-dashoffset', (1 - tw(u, 0.42, 0.62, 'outCubic')).toFixed(3));
          css(ms.conn, { boxShadow: `inset 0 0 0 1.5px rgba(42,120,214,${(0.3 + 0.3 * Math.sin(u * 14)).toFixed(3)})` });
          css(ms.g, { background: u > 0.42 && u < 0.9 ? `rgba(240,240,239,${(1 - tw(u, 0.5, 0.9)).toFixed(3)})` : 'transparent' });
          const ks = tw(u, 0.72, 1.1, 'outCubic');
          css(sl.r, { opacity: ks, transform: `translateY(${((1 - ks) * 16).toFixed(1)}px)` });
          sl.path.setAttribute('stroke-dasharray', '1 1');
          sl.path.setAttribute('stroke-dashoffset', (1 - tw(u, 0.95, 1.15, 'outCubic')).toFixed(3));
        } else if (idx === 2) {
          // anchor the menu to the pill (in world coordinates, camera-independent)
          // pill.offsetParent is the card (position: relative), the card's is the box root
          // pill position in camera-world px (layout only): it sits in the bar below the card
          let pillX = 0, pillY = 0;
          for (let e = pill; e && e !== mcam.world; e = e.offsetParent) { pillX += e.offsetLeft; pillY += e.offsetTop; }
          const pillR = pillX + pill.offsetWidth, pillB = pillY + pill.offsetHeight;
          // frame the box's right half and the space below it, where the menu opens
          const kc = ease.outExpo(prog(u, 0, 0.6));
          mcam.set({ x: lerp(1180, 1210, ease.outQuad(prog(u, 0, 1.5))), y: lerp(560, 575, kc), s: lerp(1.28, 1.36, kc) + 0.03 * prog(u, 0.6, 1.5), ry: lerp(-6, -3, kc), rx: 2 });
          // as in ModelPicker.tsx: a digit key picks that entry and closes the menu at once
          const PRESS = 0.78;
          const open = u >= 0.12 && u < PRESS + 0.1;
          const ko = tw(u, 0.12, 0.3, 'outBack') * (1 - tw(u, PRESS + 0.02, PRESS + 0.12, 'inQuad'));
          css(menu, { left: pillR - menu.offsetWidth, top: pillB + 6 * Z, opacity: clamp(ko * 1.5), transform: `translateY(${((1 - ko) * -8).toFixed(1)}px) scale(${(0.96 + 0.04 * ko).toFixed(4)})` });
          mrows.forEach((r, i) => {
            const txt = (i === 0 ? '✓ ' : '') + r.name;
            if (r.lab.textContent !== txt) r.lab.textContent = txt;
            css(r.r, { background: i === 2 && u >= PRESS && u < PRESS + 0.12 ? 'var(--card-bg)' : 'transparent' });
          });
          const label = u >= PRESS + 0.04 ? 'glm-4.6' : 'deepseek-chat';
          if (pillTx.textContent !== label) pillTx.textContent = label;
          const kl = tw(u, PRESS + 0.04, PRESS + 0.3, 'outCubic');
          css(pillTx, { display: 'inline-block', transform: `translateY(${u >= PRESS + 0.04 ? ((1 - kl) * 6).toFixed(1) : 0}px)`, opacity: u >= PRESS + 0.04 ? kl : 1 });
          css(pill, { background: open ? 'rgba(255,255,255,.22)' : 'transparent', color: 'var(--field-ink)' });
          mbox.update({ str: '', k: 1, t, caretOn: true });
          // the number key
          const kk = tw(u, 0.42, 0.6, 'outCubic') * (1 - tw(u, 0.98, 1.2));
          const press = u >= PRESS && u < PRESS + 0.1;
          css(keycap, { left: pillR - menu.offsetWidth - 60 - 28, top: pillB + 6 * Z + mrows[2].r.offsetTop + mrows[2].r.offsetHeight / 2 - 30, opacity: kk, transform: `translateY(${press ? 3 : (1 - kk) * 8}px)`, boxShadow: press ? '0 0 0 rgba(32,30,29,.16)' : '0 3px 0 rgba(32,30,29,.16)' });
          // pointer clicks the pill, then drifts away
          const pp = toStage(pill.getBoundingClientRect());
          const km = ease.outCubic(prog(u, -0.15, 0.1));
          const px = lerp(pp.x + pp.w * 0.5 + 120, pp.x + pp.w * 0.45, km) + tw(u, 0.35, 1.2) * 90, py = lerp(pp.y + pp.h + 120, pp.y + pp.h * 0.6, km) + tw(u, 0.35, 1.2) * 70;
          css(mptr, { opacity: 1 - tw(u, 0.5, 0.8), transform: `translate(${(px - 3).toFixed(1)}px, ${(py - 3).toFixed(1)}px) scale(${u >= 0.1 && u < 0.18 ? 0.9 : 1})` });
        } else {
          scam.set({ x: lerp(960, 930, ease.outQuad(prog(u, 0, 1.5))), y: 540, s: lerp(1.0, 1.03, prog(u, 0, 1.5)) });
          // in fast (on the hit), hold to read, then everything leaves by ~99.9: an empty surface at 100.0
          l1.update(TX1, prog(u, 0.0, 0.32), tw(u, 1.02, 1.36, 'inOutQuad'));
          l2.update(TX2, prog(u, 0.1, 0.46), tw(u, 1.08, 1.40, 'inOutQuad'));
          trows.forEach((r, i) => {
            const k = tw(u, 0.16 + i * 0.05, 0.42 + i * 0.05, 'outCubic') * (1 - tw(u, 0.98 + i * 0.03, 1.22 + i * 0.03, 'inQuad'));
            css(r, { opacity: k.toFixed(3), transform: `translateY(${((1 - k) * 10).toFixed(1)}px)` });
          });
          const kg = tw(u, 0.25, 0.45) * (1 - tw(u, 0.98, 1.2));
          css(guide, { opacity: kg });
          const kst = tw(u, 0.3, 0.55, 'outCubic') * (1 - tw(u, 1.1, 1.34, 'inQuad'));
          css(status, { opacity: kst.toFixed(3), transform: `translateY(${((1 - kst) * 8).toFixed(1)}px)` });
        }
        grainU(t);
      };
    },
  });
})();
