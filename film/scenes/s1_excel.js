// Sentence 1 · Excel analysis — script §3 S1-02 … S1-07, example data §4.1.
// The depth under the first sentence: 48 store exports standing in one long
// file, like cards in a drawer, that the blue thread stitches clean; three hypotheses tested
// and struck; the reviewer catching 2,316 double-posted rows; then the
// workbook floats up, assembles, and shrinks into the next box's chip.
// Layers (all under the surface's z 100): bg 5 · world 10 · hypotheses 12 ·
// UI fragments 13 · workbook 14 · grain + example tag 16.
(function () {
  const { h, css, clamp, lerp, tw, ease, prog, L, rng, hash } = F;
  const T0 = 11.2, T1 = 29.6;
  const PERSP = 1400;
  const SW = 1200, SH = 760;          // sheet size, world px
  const HERO = 5;                     // 华东_2026-03: the sheet we land on
  const RS = 1.6;                     // hero canvas resolution
  const DPI = 1;                      // shared texture resolution
  const smooth = (a, b, x) => { const k = clamp((x - a) / (b - a)); return k * k * (3 - 2 * k); };
  const bump = (k) => Math.sin(Math.PI * clamp(k));

  // ---------- example data (§4.1) ----------
  const REG = [L('华东', 'East'), L('华南', 'South'), L('华北', 'North'), L('西南', 'Southwest')];
  const REG_CODE = [L('HD', 'E'), L('HN', 'S'), L('HB', 'N'), L('XN', 'SW')];
  const MONTHS = ['2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09'];
  const CATS = [L('家居', 'Home'), L('服饰', 'Apparel'), L('食品', 'Food'), L('数码', 'Digital')];
  const CHAN = [L('门店', 'Store'), L('线上', 'Online')];
  const HEAD = { date: L('日期', 'Date'), store: L('门店', 'Store'), region: L('区域', 'Region'), cat: L('品类', 'Category'),
    order: L('订单号', 'Order no.'), amt: L('销售额', 'Sales'), disc: L('折扣', 'Discount'), qty: L('数量', 'Qty'), ch: L('渠道', 'Channel') };
  const FW = { 0: '０', 1: '１', 2: '２', 3: '３', 4: '４', 5: '５', 6: '６', 7: '７', 8: '８', 9: '９', ',': '，' };
  const fullWidth = (s) => Array.from(s).map((c) => FW[c] || c).join('');
  const sheetLabel = (i) => `${REG[Math.floor(i / 12)]}_${MONTHS[i % 12]}`;

  // one data row of a store export; n = 0-based data row, reg/mon = file
  function rowData(n, reg = 0, mon = 5, seed = 0) {
    const r = rng(7919 * (n + 1) + 104729 * (seed + 1) + 31 * reg + mon);
    const [y, m] = MONTHS[mon].split('-');
    const d = 1 + (Math.floor(n / 3) % 28);
    const cat = Math.floor(r() * 4);
    const amt = Math.round((900 + r() * 24000) / 10) * 10;
    const disc = cat === 0 && mon >= 4 ? 19 : 8 + Math.floor(r() * 6);
    const fmtDate = [`${y}-${m}-${String(d).padStart(2, '0')}`, `${y}/${+m}/${d}`, L(`${+m}月${d}日`, `${d} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m - 1]}`)];
    const df = r() < 0.42 ? 0 : r() < 0.5 ? 1 : 2;
    return {
      date: fmtDate[0], dateDirty: fmtDate[df], dateBad: df !== 0,
      store: `${REG_CODE[reg]}-${String(1 + Math.floor(r() * 10)).padStart(2, '0')}`,
      region: REG[reg], regionBad: r() < 0.34,
      cat: CATS[cat],
      order: `SO-${y.slice(2)}${m}-${String(417 + n).padStart(5, '0')}`,
      amt: K.fmt(amt, 2), amtDirty: r() < 0.4 ? fullWidth(String(amt)) : String(amt), amtBad: false,
      disc: `${disc}%`, qty: String(1 + Math.floor(r() * 12)), ch: CHAN[r() < 0.72 ? 0 : 1],
    };
  }
  // amounts typed as full-width text are the "bad" ones
  const isFW = (s) => /[０-９]/.test(s);

  // ---------- spreadsheet geometry (world px) ----------
  const GUT = 46, LET = 26, RH = 30, PADX = 9;
  const COLS = [ // key, width, numeric
    ['date', 132], ['store', 92], ['region', 92], ['cat', 76], ['order', 168], ['amt', 132, 1], ['disc', 80, 1], ['qty', 64, 1], ['ch', 76], [null, 80], [null, 80], [null, 82]];
  const COLX = []; { let x = GUT; for (const c of COLS) { COLX.push(x); x += c[1]; } }
  const C = { bgHero: 'rgb(33,31,30)', bgGlass: 'rgba(30,28,27,0.96)', grid: 'rgba(247,245,243,0.075)', strip: 'rgba(247,245,243,0.045)',
    letter: 'rgba(161,157,155,0.75)', head: 'rgba(247,245,243,0.70)', clean: 'rgba(247,245,243,0.86)', dirty: 'rgba(176,172,169,0.74)', bad: 'rgba(176,172,169,0.62)',
    accent: '#4b8fe3', danger: '#f09a9a' };
  const MONO = '"IBM Plex Mono", "Noto Sans SC", monospace';
  const SANS = '"IBM Plex Sans", "Noto Sans SC", sans-serif';

  // draw the sheet chrome: background, column letters, row gutter, grid
  function drawChrome(ctx, bg, rows, rowNum0 = 1, yOff = 0) {
    ctx.fillStyle = bg; ctx.fillRect(0, 0, SW, SH);
    ctx.fillStyle = C.strip; ctx.fillRect(0, 0, SW, LET); ctx.fillRect(0, 0, GUT, SH);
    ctx.strokeStyle = C.grid; ctx.lineWidth = 1; ctx.beginPath();
    for (let i = 0; i <= COLS.length; i++) { const x = i === COLS.length ? SW : COLX[i]; ctx.moveTo(x + 0.5, 0); ctx.lineTo(x + 0.5, SH); }
    for (let y = LET + (yOff % RH); y <= SH; y += RH) { ctx.moveTo(0, y + 0.5); ctx.lineTo(SW, y + 0.5); }
    ctx.moveTo(0, LET + 0.5); ctx.lineTo(SW, LET + 0.5);
    ctx.stroke();
    ctx.font = `12px ${MONO}`; ctx.fillStyle = C.letter; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    COLS.forEach((c, i) => ctx.fillText('ABCDEFGHIJKL'[i], COLX[i] + c[1] / 2, LET / 2 + 1));
  }
  function gutterNums(ctx, first, yOff, color = C.letter) {
    ctx.font = `12px ${MONO}`; ctx.fillStyle = color; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (let k = 0; ; k++) { const y = LET + yOff + k * RH; if (y > SH) break; if (y + RH < LET) continue; ctx.fillText(String(first + k), GUT - 8, y + RH / 2 + 1); }
  }
  // one row. st: { c: clean 0..1, a: align 0..1, sw: column swap 0..1, tint: red 0..1, flash }
  function drawRow(ctx, y, row, st, header = false) {
    const c = st.c ?? 1, a = st.a ?? 1, sw = st.sw ?? 1;
    const ym = y + RH / 2 + 1;
    if (st.tint) { ctx.fillStyle = `rgba(240,154,154,${0.17 * st.tint})`; ctx.fillRect(GUT, y, SW - GUT, RH); }
    ctx.textBaseline = 'middle';
    const colX = (key) => {
      // dirty files list region before store: the two columns slide past each other into place
      if (key === 'store') return lerp(COLX[2], COLX[1], ease.inOutCubic(sw));
      if (key === 'region') return lerp(COLX[1], COLX[2], ease.inOutCubic(sw));
      return COLX[COLS.findIndex((cc) => cc[0] === key)];
    };
    const cellW = (key) => COLS.find((cc) => cc[0] === key)[1];
    const text = (s, x, w, align, color, dy = 0, alpha = 1) => {
      if (alpha <= 0.01) return;
      ctx.globalAlpha = alpha; ctx.fillStyle = color;
      ctx.textAlign = 'left';
      const tw_ = ctx.measureText(s).width;
      const xl = x + PADX, xr = x + w - PADX - tw_;
      ctx.fillText(s, lerp(xl, xr, align), ym + dy);
      ctx.globalAlpha = 1;
    };
    if (header) {
      ctx.font = `500 14px ${SANS}`;
      for (const [key, w, num] of COLS) if (key) text(HEAD[key], colX(key), w, num ? 1 : 0, C.head);
      return;
    }
    const tint = st.tint || 0;
    const red = (base) => tint > 0.01 ? (tint > 0.5 ? C.danger : base) : base;
    ctx.font = `15px ${MONO}`;
    const fl = st.flash || 0;
    if (fl > 0.01) { ctx.fillStyle = `rgba(75,143,227,${0.20 * fl})`; ctx.fillRect(COLX[0] + 1, y + 1, COLS[0][1] - 1, RH - 1); }
    // date: dirty spellings resolve into ISO
    if (row.dateBad) {
      text(row.dateDirty, COLX[0], COLS[0][1], 0, C.dirty, -5 * c, 1 - c);
      text(row.date, COLX[0], COLS[0][1], 0, red(C.clean), 5 * (1 - c), c);
    } else text(row.date, COLX[0], COLS[0][1], 0, red(lerpColor(c)));
    text(row.store, colX('store'), cellW('store'), 0, red(lerpColor(c)));
    text(row.region, colX('region'), cellW('region'), 0, red(row.regionBad ? lerpColor(c, 0.35) : lerpColor(c)));
    if (row.regionBad && c < 1) { // the trailing space, shown as a faint open box
      ctx.font = `15px ${MONO}`;
      const w = ctx.measureText(row.region).width, x = colX('region') + PADX + w + 3;
      ctx.globalAlpha = 1 - c; ctx.strokeStyle = 'rgba(232,184,92,0.55)'; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(x, ym + 1); ctx.lineTo(x, ym + 5); ctx.lineTo(x + 8, ym + 5); ctx.lineTo(x + 8, ym + 1); ctx.stroke(); ctx.globalAlpha = 1;
    }
    ctx.font = `14px ${SANS}`;
    text(row.cat, COLX[3], COLS[3][1], 0, red(lerpColor(c)));
    ctx.font = `15px ${MONO}`;
    text(row.order, COLX[4], COLS[4][1], 0, tint > 0.5 ? C.danger : lerpColor(c, 0.25));
    // amounts: full-width text (left-aligned, grey) becomes a number that slides right
    const fw = isFW(row.amtDirty);
    if (fw) {
      text(row.amtDirty, COLX[5], COLS[5][1], 0, C.bad, -5 * c, 1 - c);
      text(row.amt, COLX[5], COLS[5][1], a, red(C.clean), 5 * (1 - c), c);
    } else {
      text(row.amtDirty, COLX[5], COLS[5][1], 1, C.dirty, 0, 1 - c);
      text(row.amt, COLX[5], COLS[5][1], 1, red(C.clean), 0, c);
    }
    // discount: stored as text '12%' (left) in the dirty files
    text(row.disc, COLX[6], COLS[6][1], a, red(lerpColor(c, 0.4)));
    text(row.qty, COLX[7], COLS[7][1], 1, red(lerpColor(c)));
    ctx.font = `14px ${SANS}`;
    text(row.ch, COLX[8], COLS[8][1], 0, red(lerpColor(c)));
  }
  // grey (dirty) -> paper white (clean); `bad` biases toward the dirtier grey
  function lerpColor(c, bad = 0) {
    const a = lerp(0.7 - bad * 0.25, 0.88, c), g = Math.round(lerp(176, 247, c)), g2 = Math.round(lerp(172, 245, c)), g3 = Math.round(lerp(169, 243, c));
    return `rgba(${g},${g2},${g3},${a.toFixed(3)})`;
  }

  // ---------- the 3D camera ----------
  // world: x,y as on screen, Z = depth away from the viewer. The camera looks
  // at (cx, cy, cZ) which sits on the screen plane at zoom s; ry/rx orbit.
  function camKeys(keys, t) {
    if (t <= keys[0].t) return keys[0];
    for (let i = 1; i < keys.length; i++) {
      const a = keys[i - 1], b = keys[i];
      if (t <= b.t) {
        const k = ease[b.e || 'inOutCubic'](prog(t, a.t, b.t));
        const o = {};
        for (const p of ['cx', 'cy', 'cZ', 'rx', 'ry', 's']) o[p] = p === 's' ? Math.exp(lerp(Math.log(a.s), Math.log(b.s), k)) : lerp(a[p], b[p], k);
        return o;
      }
    }
    return keys[keys.length - 1];
  }
  function camSpace(x, y, Z, c) {
    const X = (x - c.cx) * c.s, Y = (y - c.cy) * c.s, Zc = (c.cZ - Z) * c.s;
    const ay = c.ry * Math.PI / 180, ax = c.rx * Math.PI / 180;
    const x1 = X * Math.cos(ay) + Zc * Math.sin(ay), z1 = -X * Math.sin(ay) + Zc * Math.cos(ay);
    const y1 = Y * Math.cos(ax) - z1 * Math.sin(ax), z2 = Y * Math.sin(ax) + z1 * Math.cos(ax);
    return { x: x1, y: y1, z: z2 };
  }
  const project = (p) => ({ x: 960 + p.x * PERSP / (PERSP - p.z), y: 540 + p.y * PERSP / (PERSP - p.z), k: PERSP / (PERSP - p.z) });

  // layout: sheets 0..4 hang far in front (we fall through them on the way
  // down); the hero; then the other 42 files stand behind it in one long,
  // evenly spaced file, like cards in a drawer. The camera swings round to
  // the left and looks down the file at an angle, so every sheet shows its tab
  // (华东_2025-10 … 西南_2026-09) and its first columns, crisp, in depth.
  const DZ = 210;
  const ladder = (k) => ({ X: 600, Y: 560, Z: k * DZ });
  const stack = Array.from({ length: 48 }, (_, i) => {
    if (i < HERO) { const j = HERO - i; return { X: 560 + (hash(i + 3) - 0.5) * 260, Y: 540 + (hash(i + 9) - 0.5) * 160, Z: -1300 - (j - 1) * 260 }; }
    return ladder(i - HERO);
  });
  // where the thread goes through each sheet: in the date column, leaving the
  // hero's last row and easing up to mid-height over the first few files
  const pierce = (i) => ({ x: COLX[0] + 74, y: LET + RH * lerp(22.5, 12.5, smooth(0, 9, i - HERO)) });
  const H0 = stack[HERO];
  const KQ = 11;                         // the quality shot hangs at this depth in the file
  const FLY = ladder(KQ).Z;
  // look point on rung k: o.dx/o.dy from the sheet centre, o.dz along the file
  const camAt = (k, o) => { const p = ladder(k); return { cx: p.X + (o.dx || 0), cy: p.Y + (o.dy || 0), cZ: p.Z + (o.dz || 0) }; };
  const CAM = [
    { t: 11.2, cx: H0.X - 120, cy: H0.Y - 30, cZ: -2700, ry: 5, rx: 1, s: 1.15 },
    { t: 13.0, cx: H0.X - 110, cy: H0.Y - 70, cZ: 0, ry: -13, rx: 5, s: 1.2, e: 'outCubic' },
    { t: 14.6, cx: H0.X - 95, cy: H0.Y - 80, cZ: 60, ry: -14, rx: 4, s: 1.24, e: 'inOutQuad' },
    // orbit round the hero's left edge until we look down the file
    { t: 15.75, ...camAt(0.6, { dx: -300, dy: -40 }), ry: 40, rx: 3.5, s: 1.04, e: 'inOutCubic' },
    // then glide along it behind the thread
    { t: 17.3, ...camAt(7, { dx: -270, dy: -10 }), ry: 44, rx: 3, s: 1.0, e: 'inOutQuad' },
    { t: 20.2, ...camAt(8.5, { dx: -260, dy: -10 }), ry: 41, rx: 2.5, s: 1.0, e: 'inOutQuad' },
    { t: 21.15, ...camAt(KQ, { dx: -100, dy: -20, dz: -1100 }), ry: -12, rx: 4, s: 1.0, e: 'inOutCubic' },
    { t: 25.0, ...camAt(KQ, { dx: -80, dy: -20, dz: -1040 }), ry: -10, rx: 3.5, s: 1.0, e: 'inOutQuad' },
    { t: 26.0, ...camAt(KQ, { dx: -80, dy: 40, dz: -1040 }), ry: -9, rx: 10, s: 0.96, e: 'inQuad' },
  ];
  // the hero's own pose: in the stack, then pulled forward out of the depth for the review
  const CQ = CAM[5];
  const HERO_SHOW = { X: CQ.cx + 330, Y: CQ.cy + 20, Z: CQ.cZ + 120, ry: -6 };
  function heroPose(t) {
    if (t < 20.0) return { X: H0.X, Y: H0.Y, Z: H0.Z, ry: 0 };
    const k = tw(t, 20.3, 21.2, 'outCubic');
    return { X: HERO_SHOW.X + (1 - k) * 900, Y: HERO_SHOW.Y - (1 - k) * 200, Z: HERO_SHOW.Z + (1 - k) * 1500, ry: HERO_SHOW.ry };
  }
  const cam = (t) => camKeys(CAM, t);

  // ---------- timeline ----------
  const SCAN = [13.25, 14.45];          // thread runs down the hero sheet
  const ALIGN = [14.5, 14.95];          // columns slide into place
  const CASCADE_DUR = 0.4;
  // the thread reaches file k (1..42) a little faster each time
  const CAS = [15.0, 17.0];
  const casStart = (i) => { // invert an eased index(t) so the thread gathers speed, then settles
    const g = (i - HERO) / 42; let a = 0, b = 1;
    for (let n = 0; n < 24; n++) { const m = (a + b) / 2; if (ease.inOutQuad(m) < g) a = m; else b = m; }
    return CAS[0] + (CAS[1] - CAS[0]) * a;
  };
  const SCROLL = [20.25, 21.1];         // hero scrolls down to row 1,201
  const B0 = 1200 + 6, BN = 12;          // duplicate block: data rows B0..B0+BN
  const RED = [21.2, 21.75], LIFT = [22.15, 22.6], DUST = [22.65, 23.45], CLOSE = [23.45, 23.95];
  const HERO_ROWS = 23;
  const headY = (t) => LET + RH + tw(t, SCAN[0], SCAN[1], 'inOutQuad') * (HERO_ROWS - 1) * RH;

  // ---------- fonts: canvas text must wait for the faces ----------
  const sample = Object.values(HEAD).join('') + REG.join('') + CATS.join('') + CHAN.join('') + '0123456789０１２３４５６７８９，月日%-_/.SO' + MONTHS.join('');
  const fontsReady = Promise.all([
    document.fonts.load(`15px "IBM Plex Mono"`, sample), document.fonts.load(`15px "Noto Sans SC"`, sample),
    document.fonts.load(`500 14px "IBM Plex Sans"`, sample), document.fonts.load(`500 14px "Noto Sans SC"`, sample), document.fonts.load(`14px "IBM Plex Sans"`, sample),
  ]).catch(() => {});

  // ---------- event schedule (sound sync) ----------
  const rowPass = []; // time the thread head passes each hero row
  for (let r = 0; r < HERO_ROWS - 1; r++) {
    const yc = LET + RH * (r + 1) + RH / 2;
    for (let t = SCAN[0]; t <= SCAN[1] + 0.2; t += 1 / 240) if (headY(t) >= yc) { rowPass.push(t); break; }
  }
  rowPass.forEach((t, r) => F.event(t, 'tick', { kind: 'cell', row: r }));
  F.event(ALIGN[1] - 0.03, 'snap', { what: 'columns align' });
  for (let i = HERO + 1; i < 48; i++) F.event(casStart(i), 'tick', { kind: 'cell', soft: true, sheet: i });

  // =====================================================================
  // background
  F.scene({ id: 's1_bg', start: T0, end: T1, z: 5, build(layer) { K.depth(layer); return () => {}; } });

  // =====================================================================
  // the world: the stack, the hero sheet, the duplicate block
  F.scene({
    id: 's1_world', start: T0, end: 26.2, z: 10,
    build(layer) {
      const view = h('div', { class: 'abs' });
      css(view, { left: 0, top: 0, width: 1920, height: 1080, perspective: PERSP + 'px', perspectiveOrigin: '960px 540px' });
      const world = h('div', { class: 'abs' });
      css(world, { left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '0 0', transformStyle: 'preserve-3d' });
      view.append(world); layer.append(view);

      // shared textures: 4 dirty + 4 clean variants
      const tex = { dirty: [], clean: [] };
      // a file's sheet, dirty or clean (reg/mon pick its rows)
      function drawTex(clean, reg, mon, cv) {
        cv = cv || h('canvas', { width: SW * DPI, height: SH * DPI }); const ctx = cv.getContext('2d'); ctx.setTransform(DPI, 0, 0, DPI, 0, 0); ctx.clearRect(0, 0, SW, SH);
        drawChrome(ctx, C.bgGlass);
        gutterNums(ctx, 1, 0);
        drawRow(ctx, LET, null, { sw: clean ? 1 : 0 }, true);
        for (let r = 0; r < HERO_ROWS - 1; r++) drawRow(ctx, LET + RH * (r + 1), rowData(r, reg, mon, 7 + reg), { c: clean ? 1 : 0, a: clean ? 1 : 0, sw: clean ? 1 : 0 });
        return cv;
      }
      const OWN = 12; // files right after the hero get their own (exact) sheets
      const sheets = stack.map((p, i) => {
        const el = h('div', { class: 'abs' });
        css(el, { left: 0, top: 0, width: SW, height: SH, transformOrigin: '50% 50%', willChange: 'transform' });
        const face = h('div', { class: 'abs' }); css(face, { inset: 0 });
        el.append(face);
        const tab = h('div', { class: 'abs', text: sheetLabel(i) });
        css(tab, { left: 0, top: -31, height: 31, padding: '0 14px', lineHeight: '31px', fontFamily: 'var(--mono)', fontSize: 15, letterSpacing: '0.02em',
          color: i === HERO ? 'rgba(247,245,243,.92)' : 'rgba(161,157,155,.9)', background: i === HERO ? C.bgHero : C.bgGlass, borderRadius: '7px 7px 0 0',
          border: '1px solid rgba(247,245,243,.10)', borderBottom: 'none' });
        face.append(tab);
        const s = { el, face, tab, i, p };
        if (i !== HERO) {
          const own = i > HERO && i <= HERO + OWN;
          s.dirty = own ? h('canvas', { width: SW * DPI, height: SH * DPI }) : h('img'); css(s.dirty, { position: 'absolute', left: 0, top: 0, width: SW, height: SH });
          s.clean = own ? h('canvas', { width: SW * DPI, height: SH * DPI }) : h('img'); css(s.clean, { position: 'absolute', left: 0, top: 0, width: SW, height: SH, clipPath: 'inset(0 100% 0 0)' });
          s.own = own;
          // depth fog: darken toward the background instead of fading (fading stacks into murk)
          s.fog = h('div', { class: 'abs' }); css(s.fog, { left: -1, top: -32, right: -1, bottom: -1, background: '#141312', opacity: 0, borderRadius: '7px 7px 0 0' });
          face.append(s.dirty, s.clean, s.fog);
          css(face, { border: '1px solid rgba(247,245,243,.12)', boxShadow: '0 -1px 0 rgba(247,245,243,.06), 0 24px 60px rgba(0,0,0,.45)' });
        }
        world.append(el);
        return s;
      });

      // the hero: a live canvas at higher resolution, plus the duplicate block
      const hero = sheets[HERO];
      css(hero.el, { transformStyle: 'preserve-3d' });
      const hcv = h('canvas', { width: SW * RS, height: SH * RS }); css(hcv, { position: 'absolute', left: 0, top: 0, width: SW, height: SH });
      css(hero.face, { border: '1px solid rgba(247,245,243,.14)', boxShadow: '0 40px 120px rgba(0,0,0,.55)' });
      hero.face.append(hcv);
      const hctx = hcv.getContext('2d');
      const MX = 90, MT = 300, MB = 40;
      const BW = SW + 2 * MX, BH = BN * RH + MT + MB;
      const block = h('canvas', { width: BW * RS, height: BH * RS }); css(block, { position: 'absolute', left: -MX, top: 0, width: BW, height: BH, display: 'none', transformOrigin: '50% 80%' });
      hero.el.append(block);
      const bctx = block.getContext('2d');
      const blockImg = h('canvas', { width: BW * RS, height: BH * RS });
      let particles = [];

      const heroRows = Array.from({ length: HERO_ROWS - 1 }, (_, r) => rowData(r, 0, 5));
      const bigRow = (n) => (n >= B0 && n < B0 + BN ? heroRows[n - B0] : rowData(n, 0, 5, 3));
      const blockTopY = LET + RH * 6;   // where the block sits once the scroll lands (first visible = row 1,201)

      let ready = false;
      fontsReady.then(() => {
        for (let v = 0; v < 4; v++) { tex.dirty.push(drawTex(false, v, 4 + v * 2).toDataURL('image/png')); tex.clean.push(drawTex(true, v, 4 + v * 2).toDataURL('image/png')); }
        sheets.forEach((s) => {
          if (!s.dirty) return;
          if (s.own) { drawTex(false, Math.floor(s.i / 12), s.i % 12, s.dirty); drawTex(true, Math.floor(s.i / 12), s.i % 12, s.clean); }
          else { s.dirty.src = tex.dirty[Math.floor(s.i / 12)]; s.clean.src = tex.clean[Math.floor(s.i / 12)]; }
        });
        // the lifted block, pre-rendered, and its dust sampled from its own pixels
        const bc = blockImg.getContext('2d'); bc.scale(RS, RS);
        bc.translate(MX, MT);
        bc.fillStyle = C.bgHero; bc.fillRect(0, 0, SW, BN * RH);
        for (let j = 0; j < BN; j++) drawRow(bc, j * RH, heroRows[j], { tint: 1 });
        bc.strokeStyle = 'rgba(240,154,154,.35)'; bc.lineWidth = 1; bc.beginPath();
        for (let j = 0; j <= BN; j++) { bc.moveTo(GUT, j * RH + 0.5); bc.lineTo(SW, j * RH + 0.5); } bc.stroke();
        const img = bc.getImageData(0, 0, BW * RS, BH * RS).data, R = rng(4242);
        particles = [];
        const step = 3;
        for (let y = 0; y < BH * RS; y += step) for (let x = 0; x < BW * RS; x += step) {
          const a = img[(y * BW * RS + x) * 4 + 3];
          if (a > 90 && R() < 0.85) {
            const wx = x / RS - MX; // 0..SW across the sheet
            particles.push({ x: x / RS, y: y / RS, t0: DUST[0] + clamp(wx / SW) * (DUST[1] - DUST[0] - 0.35) + R() * 0.16,
              vx: 30 + R() * 90, vy: -(60 + R() * 170), life: 0.55 + R() * 0.55, ph: R() * 6.28, a: a / 255 });
          }
        }
        ready = true; lastKey = '';
      });

      // per-frame hero drawing (skipped when nothing changed)
      let lastKey = '';
      function drawHero(t) {
        const hy = headY(t), ka = tw(t, ALIGN[0], ALIGN[1], 'inOutCubic');
        const scroll = (B0 - 6) * tw(t, SCROLL[0], SCROLL[1], 'inOutCubic');
        const kred = prog(t, RED[0], RED[1]);
        const lifted = t >= LIFT[0];
        const kclose = tw(t, CLOSE[0], CLOSE[1], 'inOutCubic');
        const key = [ready, hy.toFixed(1), ka.toFixed(3), scroll.toFixed(2), kred.toFixed(3), lifted, kclose.toFixed(3)].join('|');
        if (key === lastKey) return; lastKey = key;
        const ctx = hctx; ctx.setTransform(RS, 0, 0, RS, 0, 0); ctx.clearRect(0, 0, SW, SH);
        const first = Math.floor(scroll), frac = scroll - first;
        const yOff = -frac * RH;
        drawChrome(ctx, C.bgHero, 0, 1, yOff);
        ctx.save(); ctx.beginPath(); ctx.rect(0, LET + RH, SW, SH - LET - RH); ctx.clip();
        // data rows (row 1 = header stays pinned, like a frozen pane)
        for (let k = -1; k < 24 + BN; k++) {
          const n = first + k; if (n < 0) continue;
          let y = LET + RH * (k + 1) + yOff;
          const inBlock = n >= B0 && n < B0 + BN;
          if (inBlock && lifted) continue;
          if (n >= B0 + BN) y -= kclose * BN * RH;
          if (y > SH || y + RH < LET + RH) continue;
          const row = scroll > 0.001 ? bigRow(n) : heroRows[n];
          if (!row) continue;
          if (scroll > 0.001) {
            const tint = inBlock ? clamp((kred * (BN + 4) - (n - B0)) / 3) : 0;
            drawRow(ctx, y, row, { c: 1, a: 1, sw: 1, tint });
          } else {
            const yc = y + RH / 2, c = clamp((hy - yc) / 46);
            const flash = c > 0 && c < 1 ? bump(c) : 0;
            drawRow(ctx, y, row, { c: ease.outQuad(c), a: ka, sw: ka, flash: row.dateBad ? flash : 0 });
          }
        }
        ctx.restore();
        // gutter numbers (rows after a deleted block renumber themselves)
        ctx.save(); ctx.beginPath(); ctx.rect(0, LET + RH, GUT, SH); ctx.clip();
        gutterNums(ctx, first + 2, RH + yOff);
        ctx.restore();
        ctx.fillStyle = C.bgHero; ctx.fillRect(1, LET + 1, GUT - 2, RH - 1); ctx.fillRect(GUT + 1, LET + 1, SW - GUT - 2, RH - 1);
        ctx.fillStyle = C.strip; ctx.fillRect(0, LET + 1, GUT, RH - 1);
        gutterNums(ctx, 1, 0);
        ctx.save(); ctx.beginPath(); ctx.rect(0, LET, SW, RH); ctx.clip();
        drawRow(ctx, LET, null, { sw: ka }, true);
        ctx.restore();
        ctx.strokeStyle = 'rgba(247,245,243,.14)'; ctx.beginPath(); ctx.moveTo(0, LET + RH + 0.5); ctx.lineTo(SW, LET + RH + 0.5); ctx.stroke();
      }

      // the blue thread, projected from 3D each frame and drawn over the stack
      const tcv = h('canvas', { width: 1920, height: 1080 }); css(tcv, { position: 'absolute', left: 0, top: 0, width: 1920, height: 1080 });
      layer.append(tcv);
      const tctx = tcv.getContext('2d');
      const local = (i, lx, ly) => { const p = stack[i]; return { x: p.X - SW / 2 + lx, y: p.Y - SH / 2 + ly, Z: p.Z }; };
      // [world point, time the head reaches it]
      const TPTS = [[local(HERO, GUT, -340), 12.75], [local(HERO, GUT, LET + RH), SCAN[0]], [local(HERO, GUT, SH - 6), SCAN[1]]];
      for (let i = HERO + 1; i < 48; i++) { const q = pierce(i); TPTS.push([local(i, q.x, q.y), casStart(i)]); }
      function drawThread(t, c, heroQuad) {
        const ctx = tctx; ctx.clearRect(0, 0, 1920, 1080);
        if (t < TPTS[0][1] || t > 17.9) return;
        const fadeOut = 1 - tw(t, 17.05, 17.65, 'inOutQuad');
        const near = PERSP * 0.8;
        const pts = TPTS.map(([p]) => { const q = camSpace(p.x, p.y, p.Z, c); return { ...project(q), z: q.z }; });
        // head: index of the last point reached, and how far along the next leg
        let n = 0, k = 0;
        for (let i = 1; i < TPTS.length; i++) {
          if (t >= TPTS[i][1]) { n = i; continue; }
          k = i === 2 ? (headY(t) - (LET + RH)) / (SH - 6 - LET - RH) : prog(t, TPTS[i - 1][1], TPTS[i][1]);
          break;
        }
        const last = n + 1 < pts.length ? n + 1 : n;
        const lp = (a, b, u) => ({ x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), k: lerp(a.k, b.k, u), z: lerp(a.z, b.z, u) });
        const head = n + 1 < pts.length ? lp(pts[n], pts[n + 1], k) : pts[n];
        const fog = (z) => Math.pow(clamp(1 - (-z - 300) / 4200), 1.1);
        ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.strokeStyle = C.accent;
        // pieces: straight on the hero, smoothed through midpoints in the depth (a thread, not a polygon)
        const piece = (from, ctrl, to, behind, fa = 1) => {
          if (fa <= 0.01 || from.z > near || to.z > near || (ctrl && ctrl.z > near)) return;
          const zm = (from.z + to.z) / 2;
          ctx.globalAlpha = fa * (1 - 0.85 * smooth(150, 900, zm)) * fadeOut * fog(zm) * (1 - smooth(PERSP * 0.3, PERSP * 0.62, Math.max(from.z, to.z)));
          ctx.lineWidth = Math.max(1, 2.2 * c.s * (from.k + to.k) / 2);
          if (behind && heroQuad) { ctx.save(); ctx.beginPath(); ctx.rect(0, 0, 1920, 1080); heroQuad.forEach((q, j) => (j ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y))); ctx.closePath(); ctx.clip('evenodd'); }
          ctx.beginPath(); ctx.moveTo(from.x, from.y);
          if (ctrl) ctx.quadraticCurveTo(ctrl.x, ctrl.y, to.x, to.y); else ctx.lineTo(to.x, to.y);
          ctx.stroke();
          if (behind && heroQuad) ctx.restore();
        };
        const mid = (a, b) => lp(a, b, 0.5);
        const P = pts.slice(0, n + 1).concat(n + 1 < pts.length ? [head] : []);
        for (let i = 1; i < P.length; i++) {
          // the run down the hero and the turn into the file let go once the camera has swung past
          const fa = i <= 4 ? 1 - tw(t, 16.2, 16.6, 'inOutQuad') : 1;
          if (i <= 2) { piece(P[i - 1], null, P[i], false, fa); continue; }
          const a = i === 3 ? P[2] : mid(P[i - 2], P[i - 1]);
          const b = i === P.length - 1 ? P[i] : mid(P[i - 1], P[i]);
          piece(a, P[i - 1], b, true, fa);
        }
        // a small ring where each sheet was just pierced
        for (let i = 3; i <= n; i++) {
          const age = t - TPTS[i][1]; if (age > 0.3) continue;
          const p = pts[i]; if (p.z > PERSP * 0.3) continue;
          ctx.globalAlpha = fadeOut * fog(p.z) * (1 - age / 0.3) * 0.6; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.arc(p.x, p.y, (3 + 12 * ease.outCubic(age / 0.3)) * p.k * c.s, 0, 7); ctx.stroke();
        }
        if (n + 1 < pts.length && head.z < near && !(n === 2 && heroQuad && k < 0.08)) {
          const r = 3.4 * Math.max(0.6, head.k * c.s);
          ctx.globalAlpha = fadeOut * fog(head.z) * 0.35; ctx.fillStyle = C.accent; ctx.beginPath(); ctx.arc(head.x, head.y, r * 2.2, 0, 7); ctx.fill();
          ctx.globalAlpha = fadeOut * fog(head.z); ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(head.x, head.y, r, 0, 7); ctx.fill();
        }
        ctx.globalAlpha = 1;
      }

      // dust: the intact block right of the front, particles left of it
      let lastDust = -1;
      function drawBlock(t) {
        const q = Math.round(t * 60);
        if (q === lastDust) return; lastDust = q;
        const ctx = bctx; ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, BW * RS, BH * RS);
        const front = MX + SW * prog(t, DUST[0], DUST[1] - 0.35) - 4;
        if (front < BW) ctx.drawImage(blockImg, front * RS, 0, (BW - front) * RS, BH * RS, front * RS, 0, (BW - front) * RS, BH * RS);
        ctx.fillStyle = C.danger;
        for (const p of particles) {
          if (p.x > front) continue;
          const a = t - p.t0;
          if (a < 0) { ctx.globalAlpha = p.a; ctx.fillRect(p.x * RS, p.y * RS, 2.2, 2.2); continue; }
          if (a > p.life) continue;
          const k = a / p.life;
          const x = p.x + p.vx * a + Math.sin(p.ph + a * 5) * 6 * k;
          const y = p.y + p.vy * a - 60 * a * a;
          ctx.globalAlpha = p.a * Math.pow(1 - k, 1.6);
          const sz = 2.2 * (1 - 0.5 * k);
          ctx.fillRect(x * RS, y * RS, sz, sz);
        }
        ctx.globalAlpha = 1;
      }

      return (lt, t) => {
        const c = cam(t);
        world.style.transform = `translate3d(960px,540px,0) rotateX(${c.rx}deg) rotateY(${c.ry}deg) scale3d(${c.s},${c.s},${c.s}) translate3d(${-c.cx}px,${-c.cy}px,${c.cZ}px)`;
        // dim under the hypotheses; sink away when the workbook rises
        const dim = 1 - 0.78 * tw(t, 16.9, 17.6, 'inOutQuad') * (1 - tw(t, 20.85, 21.4, 'inOutQuad'));
        const sink = tw(t, 25.0, 25.9, 'inCubic');
        css(layer, { opacity: dim * (1 - sink), transform: `translateY(${sink * 220}px)` });

        // focus plane: on the hero while it is the subject, on the stack between
        const hp = heroPose(t);
        const fw = tw(t, 15.35, 16.0, 'inOutQuad') * (1 - tw(t, 20.4, 21.1, 'inOutQuad'));
        const fz = lerp(camSpace(hp.X, hp.Y, hp.Z, c).z, 0, fw);
        for (const s of sheets) {
          const { X, Y, Z } = s.i === HERO ? hp : s.p;
          let zmax = -1e9, cz = 0;
          for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) zmax = Math.max(zmax, camSpace(X + dx * SW / 2, Y + dy * SH / 2, Z, c).z);
          cz = camSpace(X, Y, Z, c).z;
          let o = 1 - smooth(PERSP * 0.32, PERSP * 0.7, zmax);
          const fog = s.i > HERO ? 0.86 * smooth(500, 3600, -cz) : 0;
          if (s.i < HERO) o *= Math.pow(clamp(1 - (-cz - 300) / 3600), 1.2);
          if (s.i < HERO && t > 13.05) o = 0;               // passed on the way down
          if (s.i !== HERO && t > 20.4) {                   // the hero leads the quality shot
            o *= Z < HERO_SHOW.Z ? 1 - tw(t, 20.4, 21.0) : 1 - 0.6 * tw(t, 20.6, 21.3);
          }
          if (o < 0.01) { if (s._vis !== false) { s.el.style.display = 'none'; s._vis = false; } continue; }
          if (s._vis !== true) { s.el.style.display = ''; s._vis = true; }
          s.el.style.transform = `translate3d(${X - SW / 2}px,${Y - SH / 2}px,${-Z}px)` + (s.i === HERO && hp.ry ? ` rotateY(${hp.ry}deg)` : '');
          // depth of field: near layers blur fast, far layers soften slowly
          const dz = cz - fz;
          const b = dz > 0 ? Math.min(7, dz * 0.008) : Math.min(2.5, -dz * 0.0011);
          const bq = Math.round(b * 2) / 2;
          if (s.i !== HERO) o *= 1 - 0.45 * clamp(b / 7);   // out-of-focus foreground recedes
          css(s.face, { opacity: o.toFixed(3), filter: bq > 0.25 ? `blur(${bq}px)` : 'none' });
          if (s.fog) css(s.fog, { opacity: fog.toFixed(3) });
          if (s.i > HERO) {
            // where the thread pierces, the clean sheet opens sideways from that point
            const k = ease.outCubic(prog(t, casStart(s.i), casStart(s.i) + CASCADE_DUR));
            const px = pierce(s.i).x, l = Math.max(0, px - k * SW), r = Math.max(0, SW - px - k * SW);
            css(s.clean, { clipPath: k <= 0 ? 'inset(0 100% 0 0)' : `inset(0 ${r.toFixed(1)}px 0 ${l.toFixed(1)}px)` });
            s.dirty.style.display = k >= 1 ? 'none' : '';
          }
        }
        // the hero's outline on screen, so the thread can pass behind it
        let heroQuad = null;
        if (sheets[HERO]._vis && t < 15.6) {
          const qs = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([dx, dy]) => camSpace(hp.X + dx * SW / 2, hp.Y + dy * SH / 2, hp.Z, c));
          if (qs.every((q) => q.z < PERSP * 0.8)) heroQuad = qs.map(project);
        }
        drawThread(t, c, heroQuad);
        if (sheets[HERO]._vis) {
          drawHero(t);
          const showBlock = t >= LIFT[0] && t < DUST[1] + 0.9;
          block.style.display = showBlock ? '' : 'none';
          if (showBlock) {
            const kl = tw(t, LIFT[0], LIFT[1], 'outCubic');
            css(block, { top: blockTopY - MT, transform: `translate3d(${-6 * kl}px,${-14 * kl}px,${80 * kl}px) rotateX(${-6 * kl}deg)`,
              filter: `drop-shadow(0 ${18 * kl}px ${28 * kl}px rgba(0,0,0,${0.55 * kl}))` });
            drawBlock(t);
          }
        }
      };
    },
  });
  F.event(LIFT[0], 'paper', { what: 'duplicate rows lift' });
  F.event(DUST[0], 'dust', { dur: DUST[1] - DUST[0] + 0.4 });
  F.event(CLOSE[0], 'slide', { what: 'rows close the gap' });

  // =====================================================================
  // UI fragments, faithful to the app (dark theme tokens, ~1.5x scale)
  const UI = 1.5;
  const D = { bg: '#232120', fg: '#f7f5f3', muted: '#a19d9b', card: '#322f2e', border: 'rgba(247,245,243,.14)', borderHover: 'rgba(247,245,243,.26)', accent: '#4b8fe3', code: 'rgba(247,245,243,.12)' };
  const ICON = (d, size, color, sw = 2) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"><path d="${d}"/></svg>`;
  const CHECK = 'M20 6L9 17l-5-5', CHEV_DOWN = 'M6 9l6 6 6-6', CHEV_RIGHT = 'M9 6l6 6-6 6';

  // TaskPanel.tsx › Progress: StepDot row, "n of m done", step list
  function progressPanel(parent, { x, y, w = 320 * UI }) {
    const root = h('div', { class: 'abs' });
    css(root, { left: x, top: y, width: w, background: D.bg, border: `${UI}px solid ${D.border}`, borderRadius: 12 * UI, color: D.fg,
      fontFamily: 'var(--sans)', boxShadow: '0 30px 80px rgba(0,0,0,.5)', overflow: 'hidden' });
    const sec = h('div'); css(sec, { padding: `${12 * UI}px ${16 * UI}px ${14 * UI}px` });
    const title = h('div', { html: `<span>Progress</span>${ICON(CHEV_DOWN, 14 * UI, D.muted)}` });
    css(title, { display: 'flex', alignItems: 'center', gap: 6 * UI, fontSize: 14 * UI, fontWeight: '500' });
    const dots = h('div'); css(dots, { display: 'flex', alignItems: 'center', marginTop: 12 * UI });
    const count = h('div'); css(count, { marginTop: 10 * UI, fontSize: 12 * UI, color: D.muted, fontVariantNumeric: 'tabular-nums' });
    const list = h('div'); css(list, { marginTop: 8 * UI, display: 'flex', flexDirection: 'column', gap: 6 * UI });
    sec.append(title, dots, count, list); root.append(sec); parent.append(root);
    const dot = (st, size) => {
      const box = `width:${size}px;height:${size}px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex:none;box-sizing:border-box;`;
      if (st === 'done') return `<span style="${box}border:${UI}px solid ${D.borderHover}">${ICON(CHECK, size * 0.6, D.muted, 2.2)}</span>`;
      if (st === 'run') return `<span style="${box}border:${UI}px solid ${D.borderHover}"><span class="spin" style="width:${size * 0.6}px;height:${size * 0.6}px;border-radius:50%;border:${1.5 * UI}px solid ${D.border};border-top-color:${D.accent};box-sizing:border-box"></span></span>`;
      return `<span style="${box}background:${D.card}"></span>`;
    };
    let rows = [], dotEls = [], sig = '';
    return {
      root,
      // items: [{ text, st: 'done'|'run'|'todo', flip?: { to, k } }]
      update(items, t) {
        const s2 = items.map((it) => it.st + it.text).join('|');
        if (s2 !== sig) {
          sig = s2;
          dots.innerHTML = items.map((it, i) => (i ? `<span style="height:${UI}px;width:${10 * UI}px;background:${D.borderHover}"></span>` : '') + dot(it.st, 20 * UI)).join('');
          const done = items.filter((it) => it.st === 'done').length;
          count.textContent = `${done} of ${items.length} done`;
          list.innerHTML = '';
          rows = items.map((it) => {
            const li = h('div'); css(li, { display: 'flex', alignItems: 'flex-start', gap: 8 * UI, fontSize: 13 * UI, lineHeight: `${20 * UI}px` });
            const d = h('span', { html: dot(it.st, 16 * UI) }); css(d, { marginTop: 2 * UI, display: 'flex' });
            const tx = h('span'); css(tx, { position: 'relative', overflow: 'hidden', height: 20 * UI, display: 'block', flex: '1' });
            const a = h('span', { text: it.text }), b = h('span', { text: it.flip ? it.flip.to : '' });
            for (const e of [a, b]) css(e, { position: 'absolute', left: 0, top: 0, whiteSpace: 'nowrap' });
            css(a, { color: it.st === 'done' ? D.muted : D.fg, fontWeight: it.st === 'run' ? '500' : '400' });
            css(b, { color: D.muted });
            tx.append(a, b); li.append(d, tx); list.append(li);
            return { li, a, b };
          });
        }
        items.forEach((it, i) => {
          if (!it.flip) { css(rows[i].b, { display: 'none' }); return; }
          const k = ease.inOutCubic(clamp(it.flip.k));
          css(rows[i].a, { transform: `translateY(${-k * 20 * UI}px)`, opacity: 1 - k });
          css(rows[i].b, { display: '', transform: `translateY(${(1 - k) * 20 * UI}px)`, opacity: k });
        });
        root.querySelectorAll('.spin').forEach((e) => { e.style.transform = `rotate(${(t * 400) % 360}deg)`; });
      },
    };
  }
  const TASKS = [L('读取 48 张表', 'Read 48 sheets'), L('清洗与对齐', 'Clean and align'), L('按区域 / 品类 / 月份拆分', 'Split by region / category / month'),
    L('验证假设', 'Test hypotheses'), L('写入工作簿', 'Write workbook')];
  const REVIEW_STEP = L('复核', 'Review'), REVIEW_DONE = L('剔除重复入账，重算', 'Drop duplicate entries, recompute');

  // =====================================================================
  // S1-03 task panel, S1-05 review row + regional ranking + task flip
  F.scene({
    id: 's1_ui', start: 13.6, end: 25.4, z: 13,
    build(layer) {
      // S1-03: Task details › Progress, 2.6 s
      const p1 = progressPanel(layer, { x: 1392, y: 74 });
      F.event(15.75, 'click', { what: 'step done' });
      // S1-05: Progress again, one row flips
      const p2 = progressPanel(layer, { x: 1392, y: 74 });
      F.event(24.35, 'click', { what: 'review step flips to done' });

      // S1-05 ranking: the steepest regional decline, before and after
      const rank = h('div', { class: 'abs' }); css(rank, { left: 128, top: 150, width: 600, color: D.fg }); layer.append(rank);
      const cap = h('div', { text: L('区域销售同比 · 下滑最大', 'Regional sales YoY · steepest first') });
      css(cap, { fontFamily: 'var(--sans)', fontSize: 17, color: D.muted, letterSpacing: '0.04em', marginBottom: 14 });
      rank.append(cap);
      const RHt = 84;
      const rankBox = h('div'); css(rankBox, { position: 'relative', height: RHt * 2 }); rank.append(rankBox);
      for (let i = 0; i < 2; i++) { const n = h('div', { text: String(i + 1) }); css(n, { position: 'absolute', left: 0, top: i * RHt, height: RHt, lineHeight: RHt + 'px', fontFamily: 'var(--mono)', fontSize: 20, color: D.muted }); rankBox.append(n); }
      const DIGH = 76; // odometer slot height
      function regionRow(name) {
        const r = h('div'); css(r, { position: 'absolute', left: 44, top: 0, height: RHt, display: 'flex', alignItems: 'center', gap: 24 });
        const nm = h('div', { text: name }); css(nm, { fontFamily: 'var(--sans)', fontSize: 34, width: L(96, 120), color: D.fg });
        const val = h('div'); css(val, { position: 'relative', display: 'flex', alignItems: 'center', fontFamily: 'var(--mono)', fontSize: 62, height: DIGH, fontVariantNumeric: 'tabular-nums' });
        r.append(nm, val); rankBox.append(r);
        return { r, nm, val };
      }
      const east = regionRow(REG[0]), south = regionRow(REG[1]);
      // odometer: − [tens][ones] . [tenths] %
      const strip = (chars) => { const box = h('span'); css(box, { display: 'inline-block', height: DIGH, overflow: 'hidden', position: 'relative', width: '0.6em' });
        const col = h('span'); css(col, { position: 'absolute', left: 0, top: 0, display: 'flex', flexDirection: 'column' });
        chars.forEach((c) => { const d = h('span', { text: c }); css(d, { height: DIGH, lineHeight: DIGH + 'px', display: 'block', textAlign: 'center' }); col.append(d); });
        box.append(col); return { box, col }; };
      const minus = h('span', { text: '−' }); css(minus, { display: 'inline-block', width: '0.6em', textAlign: 'center' });
      const tens = strip([' ', '1', '2', '3', '4', '5', '6', '7', '8', '9', ' ']), ones = strip('01234567890'.split('')), tenths = strip('01234567890'.split(''));
      const dotEl = h('span', { text: '.' }), pct = h('span', { text: '%' });
      for (const e of [dotEl, pct]) css(e, { display: 'inline-block', width: '0.6em', textAlign: 'center' });
      east.val.append(minus, tens.box, ones.box, dotEl, tenths.box, pct);
      south.val.append(h('span', { text: '−9.6%' }));
      const ROLL = [23.45, 24.1], SWAP = [24.15, 24.7];
      for (let k = 0; k < 9; k++) F.event(ROLL[0] + 0.04 + k * 0.07, 'tick', { kind: 'roll' });
      F.event(SWAP[0], 'slide', { what: 'South China rises to first' });

      // S1-05 Review work: the tool row, opened (ChatLog ToolRunGroupView + ToolDetail)
      const rev = h('div', { class: 'abs' });
      css(rev, { left: 128, top: 420, width: 660, background: D.bg, borderRadius: 12 * UI, border: `${UI}px solid ${D.border}`, padding: `${12 * UI}px ${14 * UI}px ${14 * UI}px`,
        boxShadow: '0 30px 80px rgba(0,0,0,.5)', fontFamily: 'var(--sans)' });
      const sum = h('div'); css(sum, { display: 'flex', alignItems: 'center', gap: 4 * UI, color: D.muted, fontSize: 14 * UI });
      const chev = h('span', { html: ICON(CHEV_RIGHT, 14 * UI, D.muted) }); css(chev, { display: 'flex' });
      sum.append(h('span', { text: 'Asked a reviewer to check the work' }), chev);
      const box = h('div'); css(box, { marginTop: 8 * UI, borderRadius: 12 * UI, border: `${UI}px solid ${D.border}`, overflow: 'hidden' });
      const pre = h('div'); css(pre, { background: D.code, padding: `${8 * UI}px ${12 * UI}px`, fontFamily: 'var(--mono)', fontSize: 12 * UI, lineHeight: '1.65', color: D.fg, whiteSpace: 'pre-wrap' });
      const finding = L('华东 3 月：同一订单号出现 2,316 行重复入账（SO-2603-…），华东 3 月销售额被高估 ¥412 万。\n剔除后，下滑最大的区域不再是华东，而是华南。',
        'East China, March: 2,316 rows share an order number (SO-2603-…) — March sales overstated by ¥4.12M.\nWithout them the steepest decline is South China, not East.');
      const lines = finding.split('\n').map((s) => { const e = h('div', { text: s }); pre.append(e); return e; });
      box.append(pre); rev.append(sum, box); layer.append(rev);
      let boxH = 0;

      return (lt, t) => {
        // S1-03 panel
        const on1 = t < 16.8;
        p1.root.style.display = on1 ? '' : 'none';
        if (on1) {
          const k = tw(t, 13.9, 14.35, 'outCubic') * (1 - tw(t, 16.15, 16.55, 'inQuad'));
          css(p1.root, { opacity: k, transform: `translateY(${(1 - k) * 16}px)` });
          const st2 = t >= 15.75;
          p1.update(TASKS.map((x, i) => ({ text: x, st: i === 0 ? 'done' : i === 1 ? (st2 ? 'done' : 'run') : i === 2 && st2 ? 'run' : 'todo' })), t);
        }
        // S1-05 panel
        const on2 = t > 23.6;
        p2.root.style.display = on2 ? '' : 'none';
        if (on2) {
          const k = tw(t, 23.7, 24.1, 'outCubic') * (1 - tw(t, 24.95, 25.3, 'inQuad'));
          css(p2.root, { opacity: k, transform: `translateY(${(1 - k) * 16}px)` });
          const fk = prog(t, 24.3, 24.6);
          const items = [...TASKS.slice(0, 4).map((x) => ({ text: x, st: 'done' })),
            { text: REVIEW_STEP, st: fk >= 1 ? 'done' : 'run', flip: { to: REVIEW_DONE, k: fk } }, { text: TASKS[4], st: 'todo' }];
          if (fk >= 1) { items[4] = { text: REVIEW_DONE, st: 'done' }; }
          p2.update(items, t);
        }
        // ranking
        const onR = t > 21.3;
        rank.style.display = onR ? '' : 'none';
        if (onR) {
          const k = tw(t, 21.4, 21.95, 'outCubic') * (1 - tw(t, 24.95, 25.3, 'inQuad'));
          css(rank, { opacity: k, transform: `translateY(${(1 - k) * 18}px)` });
          const v = lerp(113, 49, tw(t, ROLL[0], ROLL[1], 'inOutCubic')); // tenths of a percent
          const pos0 = v % 10, pos1 = Math.floor(v / 10) % 10 + clamp((v % 10) - 9), pos2 = Math.floor(v / 100) + clamp((v % 100) - 99);
          css(tenths.col, { transform: `translateY(${-pos0 * DIGH}px)` });
          css(ones.col, { transform: `translateY(${-(pos1 % 10) * DIGH}px)` });
          css(tens.col, { transform: `translateY(${-pos2 * DIGH}px)` });
          // the minus closes up once the tens digit has rolled away
          css(minus, { transform: `translateX(${(1 - clamp(pos2)) * 0.6 * 62}px)` });
          css(tens.box, { opacity: clamp(pos2) });
          const sw = tw(t, SWAP[0], SWAP[1], 'inOutCubic');
          // they pass each other: South rises a little to the right, East dips back under it
          css(east.r, { transform: `translateY(${sw * RHt}px)`, opacity: 1 - smooth(0.05, 0.35, sw) + smooth(0.65, 0.95, sw) });
          css(south.r, { transform: `translate(${bump(sw) * 30}px,${(1 - sw) * RHt}px)` });
          css(east.val, { color: sw > 0.5 ? D.muted : D.fg }); css(east.nm, { color: sw > 0.5 ? D.muted : D.fg });
          css(south.val, { color: D.fg });
        }
        // review row
        const onV = t > 21.3 && t < 24.6;
        rev.style.display = onV ? '' : 'none';
        if (onV) {
          if (!boxH) boxH = pre.offsetHeight;
          const k = tw(t, 21.35, 21.85, 'outCubic') * (1 - tw(t, 24.1, 24.5, 'inQuad'));
          css(rev, { opacity: k, transform: `translateX(${(1 - k) * -28}px)` });
          const open = tw(t, 21.75, 22.2, 'inOutCubic');
          css(chev, { transform: `rotate(${90 * tw(t, 21.7, 21.95, 'inOutQuad')}deg)` });
          css(box, { height: open * (boxH + 2 * UI), opacity: open > 0 ? 1 : 0, marginTop: open * 8 * UI });
          lines.forEach((e, i) => css(e, { opacity: tw(t, 22.05 + i * 0.3, 22.4 + i * 0.3, 'outQuad') }));
        }
      };
    },
  });
  F.event(21.75, 'click', { what: 'review row opens' });

  // =====================================================================
  // S1-04 hypotheses: three lines along the thread; two struck by the data
  const HYP = [
    { lab: L('假设一', 'H1'), hyp: L('客流下降', 'Footfall fell'), res: L('客流同比 −1.2%，不足以解释', 'footfall −1.2% year on year: too small to explain it') },
    { lab: L('假设二', 'H2'), hyp: L('新店稀释', 'New-store dilution'), res: L('剔除 2026 年新开 6 店后仍 −6.1%', 'still −6.1% without the 6 stores opened in 2026') },
    { lab: L('假设三', 'H3'), hyp: L('折扣加深', 'Deeper discounts'), res: L('家居品类折扣率 <b>12% → 19%</b>，该品类销售 <b>−18.4%</b>', 'Home discount rate <b>12% → 19%</b>; Home sales <b>−18.4%</b>') },
  ];
  const HX = 404, HROW = [376, 500, 624];
  const HT = [ // [line in, result in, strike/brighten]
    [17.25, 17.75, 18.2], [18.4, 18.9, 19.35], [19.5, 20.0, 20.4]];
  HT.slice(0, 2).forEach((x) => F.event(x[2], 'pencil', { dur: 0.42 }));
  F.event(HT[2][2], 'chime', { soft: true, what: 'the third hypothesis holds' });
  F.scene({
    id: 's1_hypo', start: 16.85, end: 21.5, z: 12,
    build(layer) {
      const svg = h('svg', { class: 'full', viewBox: '0 0 1920 1080' }); layer.append(svg);
      const line = h('line', { x1: HX, x2: HX, y1: -20, y2: -20, stroke: D.accent, 'stroke-width': 2.2, 'stroke-linecap': 'round' });
      const halo = h('circle', { r: 7.5, fill: D.accent, opacity: 0.35 }), head = h('circle', { r: 3.4, fill: '#fff' });
      svg.append(line);
      const nodes = HROW.map(() => { const c = h('circle', { cx: HX, r: 6, fill: '#141312', stroke: D.accent, 'stroke-width': 1.8 }); svg.append(c); return c; });
      const core = h('circle', { cx: HX, cy: HROW[2], r: 0, fill: '#fff' }); svg.append(core, halo, head);
      const rows = HYP.map((d, i) => {
        const r = h('div', { class: 'abs' });
        css(r, { left: HX + 40, top: HROW[i], transform: 'translateY(-50%)', display: 'flex', alignItems: 'baseline', gap: 26, whiteSpace: 'nowrap', color: D.fg });
        const lab = h('span', { text: d.lab }); css(lab, { fontFamily: 'var(--sans)', fontSize: 18, letterSpacing: '0.12em', color: D.muted, width: L(64, 34) });
        const hyp = h('span'); css(hyp, { position: 'relative', fontFamily: 'var(--serif)', fontSize: 50, color: 'rgba(247,245,243,.9)', letterSpacing: '0.02em' });
        const chars = Array.from(d.hyp).map((ch) => { const e = h('span', { text: ch }); css(e, { display: 'inline-block', whiteSpace: 'pre' }); hyp.append(e); return e; });
        const res = h('span'); css(res, { fontFamily: 'var(--sans)', fontSize: 25, color: D.muted, display: 'flex', alignItems: 'baseline', gap: 14 });
        const arrow = h('span', { text: '→' }); css(arrow, { color: 'rgba(161,157,155,.7)' });
        const rt = h('span', { html: d.res });
        res.append(arrow, rt);
        r.append(lab, hyp, res); layer.append(r);
        // pencil strike: a slightly wavering graphite line across the hypothesis
        const R = rng(77 + i * 13);
        const pts = Array.from({ length: 9 }, (_, j) => [j * 12.5, 5 + (R() - 0.5) * 2.2 + (j / 8 - 0.5) * 1.4]);
        const d1 = 'M' + pts.map((p) => p.join(' ')).join(' L');
        const st = h('svg', { viewBox: '0 0 100 10', preserveAspectRatio: 'none' });
        css(st, { position: 'absolute', left: -8, top: '46%', width: 'calc(100% + 16px)', height: 12, overflow: 'visible', pointerEvents: 'none' });
        const s1 = h('path', { d: d1, fill: 'none', stroke: 'rgba(247,245,243,.82)', 'stroke-width': 2.4, 'vector-effect': 'non-scaling-stroke', 'stroke-linecap': 'round' });
        const s2 = h('path', { d: d1, fill: 'none', stroke: 'rgba(247,245,243,.35)', 'stroke-width': 1, transform: 'translate(0 1.4)', 'vector-effect': 'non-scaling-stroke' });
        st.append(s2, s1);
        // drawn left to right by a widening window (dash offsets ignore non-scaling strokes)
        const win = h('div'); css(win, { position: 'absolute', left: -8, top: '40%', width: 0, height: 26, overflow: 'hidden', pointerEvents: 'none' });
        const inner = h('div'); css(inner, { position: 'absolute', left: 0, top: 0, height: 26 }); inner.append(st);
        css(st, { left: 0, top: 6, width: '100%' });
        win.append(inner); if (i < 2) hyp.append(win);
        rt.querySelectorAll('b').forEach((b) => css(b, { fontWeight: '500' }));
        return { r, lab, hyp, chars, res, rt, win, inner };
      });
      // the head comes down the left margin and stops at each line
      const HEADK = [[16.9, -20], [17.3, HROW[0]], [18.4, HROW[0]], [18.75, HROW[1]], [19.5, HROW[1]], [19.85, HROW[2]]];
      const headAt = (t) => {
        for (let i = 1; i < HEADK.length; i++) if (t < HEADK[i][0]) return lerp(HEADK[i - 1][1], HEADK[i][1], ease.inOutCubic(prog(t, HEADK[i - 1][0], HEADK[i][0])));
        return HEADK[HEADK.length - 1][1];
      };
      return (lt, t) => {
        const out = tw(t, 20.95, 21.45, 'inOutQuad');
        css(layer, { opacity: 1 - out, filter: out > 0.01 ? `blur(${out * 6}px)` : 'none' });
        const hy = headAt(t);
        const top = lerp(-20, HROW[2], tw(t, 20.45, 21.0, 'inOutCubic')); // converge: the tail draws into the last node
        F.attr(line, { y1: top, y2: Math.max(top, hy) });
        const moving = t < 19.9;
        F.attr(head, { cx: HX, cy: hy, opacity: moving ? 1 : 0 }); F.attr(halo, { cx: HX, cy: hy, opacity: moving ? 0.35 : 0 });
        nodes.forEach((n, i) => {
          const k = ease.outBack(clamp((hy - HROW[i] + 40) / 40));
          F.attr(n, { cy: HROW[i], r: i === 2 ? 6 + 3 * tw(t, 20.4, 20.9, 'outCubic') : 6 * k, opacity: k > 0 ? 1 : 0, fill: i === 2 && t > 20.45 ? D.accent : '#141312' });
        });
        F.attr(core, { r: 3.2 * tw(t, 20.5, 20.9, 'outCubic') });
        rows.forEach((r, i) => {
          const [tin, tres, tx] = HT[i];
          const kin = prog(t, tin, tin + 0.55), n = r.chars.length;
          css(r.lab, { opacity: tw(t, tin - 0.1, tin + 0.3, 'outQuad') });
          r.chars.forEach((c, j) => {
            const ki = clamp((kin * (n + 3) - j) / 3), o = ease.outQuad(ki);
            css(c, { opacity: o, filter: o < 0.99 ? `blur(${(1 - o) * 6}px)` : 'none', transform: `translateY(${(1 - ease.outCubic(ki)) * 10}px)` });
          });
          const kr = tw(t, tres, tres + 0.45, 'outCubic');
          css(r.res, { opacity: kr, transform: `translateX(${(1 - kr) * -10}px)` });
          if (i < 2) {
            const ks = tw(t, tx, tx + 0.42, 'inOutQuad'), wd = r.hyp.offsetWidth + 16;
            css(r.inner, { width: wd }); css(r.win, { width: ks * wd });
            css(r.r, { opacity: 1 - 0.5 * tw(t, tx + 0.5, tx + 1.0) });
          } else {
            const kb = tw(t, tx, tx + 0.5, 'outQuad');
            css(r.hyp, { color: `rgba(255,255,255,${0.9 + 0.1 * kb})` });
            css(r.res, { color: kb > 0.5 ? D.fg : D.muted });
            r.rt.querySelectorAll('b').forEach((b) => css(b, { color: kb > 0.5 ? '#a9cbf5' : 'inherit' }));
          }
        });
      };
    },
  });

  // =====================================================================
  // S1-06 the workbook floats up and assembles; S1-07 it shrinks into the chip
  const BK = { x: 330, y: 118, w: 1120, h: 610 };     // the workbook window
  const FP = { x: 1022, y: 462, w: 600, h: 312 };     // the 结论 sheet, nearer the camera
  const GREEN = '#2f7d4a';
  const MON12 = L(['10月', '11月', '12月', '1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月'], ['Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep']);
  const SALES = [3420, 3390, 3510, 3460, 3180, 3240, 3150, 3090, 3060, 3010, 2960, 2940];   // 万, sums to ¥3.84 亿
  const YOY = [-2.1, -2.8, -3.5, -4.6, -6.2, -7.4, -7.9, -8.3, -8.8, -9.4, -9.9, -10.2];     // %, averages −6.8%
  const TABS = L(['清洗明细', '区域×月份', '品类×月份', '门店排名', '结论'], ['Cleaned', 'Region×Month', 'Category×Month', 'Store ranking', 'Findings']);
  const FINDINGS = L([
    '12 个月销售额 −6.8%，其中七成来自家居品类折扣加深。',
    '不是客流（−1.2%），不是新店（剔除后仍 −6.1%）。',
    '集中在华南 8 家门店；华东 3 月重复入账已剔除。',
  ], [
    '12-month sales −6.8%; about 70% of it from deeper discounts in Home.',
    'Not footfall (−1.2%), not new stores (still −6.1% without them).',
    'Concentrated in 8 South China stores; East China March double entries removed.',
  ]);
  const BOOK = { rise: [25.05, 25.35], trace: [25.35, 25.85], pane: [25.62, 26.0], tabs: 26.0, line: [26.2, 27.1], bars: 26.45, front: [26.85, 27.35], find: 27.05,
    collapse: [28.3, 28.6], morph: [28.4, 29.35] };
  F.event(BOOK.rise[0], 'thread', { dur: BOOK.trace[1] - BOOK.rise[0], what: 'thread pulls the workbook up' });
  TABS.forEach((_, i) => F.event(BOOK.tabs + i * 0.09, 'tick', { kind: 'tab' }));
  F.event(BOOK.line[0], 'grow', { dur: BOOK.line[1] - BOOK.line[0], what: 'line chart grows' });
  F.event(BOOK.front[0] + 0.05, 'paper', { what: 'findings sheet lands' });
  F.event(BOOK.morph[0], 'whoosh', { reverse: true, soft: true, dur: BOOK.morph[1] - BOOK.morph[0], what: 'workbook collapses into a chip' });
  F.event(BOOK.morph[1] - 0.02, 'drop', { wood: true, what: 'chip lands in the next box' });

  F.scene({
    id: 's1_book', start: 24.9, end: T1, z: 14,
    build(layer, { T }) {
      const persp = h('div', { class: 'abs' }); css(persp, { inset: 0, perspective: '2400px', perspectiveOrigin: `${BK.x + BK.w / 2}px ${BK.y + BK.h / 2}px` });
      const group = h('div', { class: 'abs' }); css(group, { inset: 0, transformStyle: 'preserve-3d', transformOrigin: `${BK.x + BK.w / 2}px ${BK.y + BK.h / 2}px` });
      persp.append(group); layer.append(persp);

      // ---- the back pane: the workbook window (morphs into the chip) ----
      const back = h('div', { class: 'abs' });
      css(back, { left: BK.x, top: BK.y, width: BK.w, height: BK.h, overflow: 'hidden', background: '#fcfcfb', border: '1px solid rgba(47,125,74,.55)', borderRadius: 10,
        boxShadow: '0 50px 120px rgba(0,0,0,.6)' });
      const inner = h('div', { class: 'abs' }); css(inner, { left: 0, top: 0, width: BK.w, height: BK.h, transformOrigin: '0 0', color: 'var(--fg)', fontFamily: 'var(--sans)' });
      back.append(inner); group.append(back);
      // title bar
      const tb = h('div', { class: 'abs' }); css(tb, { left: 0, top: 0, width: BK.w, height: 46, display: 'flex', alignItems: 'center', gap: 12, padding: '0 18px', borderTop: `3px solid ${GREEN}` });
      const badge = h('span', { text: 'XLSX' }); css(badge, { fontFamily: 'var(--mono)', fontSize: 11, fontWeight: '600', color: '#fff', background: GREEN, borderRadius: 5, padding: '3px 5px', letterSpacing: '.04em' });
      const fname = h('span', { text: T('s1_out') }); css(fname, { fontSize: 16, fontWeight: '500' });
      tb.append(badge, fname); inner.append(tb);
      // formula bar
      const fx = h('div', { class: 'abs' }); css(fx, { left: 0, top: 46, width: BK.w, height: 34, display: 'flex', alignItems: 'center', borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)', fontSize: 13.5 });
      const nameBox = h('span', { text: 'B3' }); css(nameBox, { width: 70, textAlign: 'center', fontFamily: 'var(--mono)', color: 'var(--muted)', borderRight: '1px solid var(--border)', lineHeight: '33px' });
      const fxl = h('span', { text: 'fx' }); css(fxl, { width: 42, textAlign: 'center', fontFamily: 'var(--serif)', fontStyle: 'italic', fontSize: 16, color: 'var(--muted)' });
      const formula = L('=SUMIFS(清洗明细!$F:$F,清洗明细!$C:$C,$A3,清洗明细!$M:$M,B$1)', '=SUMIFS(Cleaned!$F:$F,Cleaned!$C:$C,$A3,Cleaned!$M:$M,B$1)');
      const fxt = h('span'); css(fxt, { fontFamily: 'var(--mono)', color: 'var(--fg)', whiteSpace: 'pre' });
      fx.append(nameBox, fxl, fxt); inner.append(fx);
      // grid: column letters, gutter, the region × month table
      const G0 = 80, GUT2 = 34, CH = 22, RH2 = 26;
      const gcols = [74, ...Array(12).fill(66), 82, 90];
      const gx = []; { let x = GUT2; for (const w of gcols) { gx.push(x); x += w; } }
      const grid = h('canvas', { width: BK.w * 2, height: (BK.h - G0 - 40) * 2 }); css(grid, { position: 'absolute', left: 0, top: G0, width: BK.w, height: BK.h - G0 - 40 });
      inner.append(grid);
      const tableCv = h('canvas', { width: BK.w * 2, height: 7 * RH2 * 2 }); css(tableCv, { position: 'absolute', left: 0, top: G0 + CH, width: BK.w, height: 7 * RH2 });
      inner.append(tableCv);
      const SHARE = [0.31, 0.27, 0.23, 0.19];
      function drawGrid() {
        const g = grid.getContext('2d'); g.setTransform(2, 0, 0, 2, 0, 0); g.clearRect(0, 0, BK.w, BK.h);
        g.fillStyle = '#f3f3f1'; g.fillRect(0, 0, BK.w, CH); g.fillRect(0, 0, GUT2, BK.h);
        g.strokeStyle = 'rgba(32,30,29,.09)'; g.lineWidth = 1; g.beginPath();
        for (const x of [...gx, gx[gx.length - 1] + gcols[gcols.length - 1]]) { g.moveTo(x + 0.5, 0); g.lineTo(x + 0.5, BK.h); }
        for (let y = CH; y < BK.h; y += RH2) { g.moveTo(0, y + 0.5); g.lineTo(BK.w, y + 0.5); }
        g.stroke();
        g.font = `11.5px ${MONO}`; g.fillStyle = '#9a9693'; g.textAlign = 'center'; g.textBaseline = 'middle';
        gcols.forEach((w, i) => g.fillText('ABCDEFGHIJKLMNOP'[i], gx[i] + w / 2, CH / 2 + 1));
        for (let r = 0; CH + r * RH2 < BK.h; r++) g.fillText(String(r + 1), GUT2 / 2, CH + r * RH2 + RH2 / 2 + 1);
        // the summary table (SUMIFS results)
        const tc = tableCv.getContext('2d'); tc.setTransform(2, 0, 0, 2, 0, 0); tc.clearRect(0, 0, BK.w, 7 * RH2);
        tc.textBaseline = 'middle';
        const cell = (r, c, txt, right, bold, color) => { tc.font = `${bold ? '500 ' : ''}12.5px ${c === 0 || r === 0 ? SANS : MONO}`; tc.fillStyle = color || '#201e1d';
          tc.textAlign = right ? 'right' : 'left'; tc.fillText(txt, right ? gx[c] + gcols[c] - 7 : gx[c] + 7, r * RH2 + RH2 / 2 + 1); };
        cell(0, 0, L('区域', 'Region'), false, true);
        MON12.forEach((m, j) => cell(0, j + 1, m, true, true));
        cell(0, 13, L('合计', 'Total'), true, true);
        REG.forEach((rg, i) => {
          cell(i + 1, 0, rg);
          let sum = 0;
          SALES.forEach((v, j) => { const x = Math.round(v * SHARE[i] * (1 + (hash(i * 31 + j) - 0.5) * 0.04)); sum += x; cell(i + 1, j + 1, K.fmt(x), true); });
          cell(i + 1, 13, K.fmt(sum), true);
        });
        cell(5, 0, L('合计', 'Total'), false, true);
        SALES.forEach((v, j) => cell(5, j + 1, K.fmt(v), true, true));
        cell(5, 13, K.fmt(SALES.reduce((a, b) => a + b, 0)), true, true);
        tc.strokeStyle = 'rgba(32,30,29,.35)'; tc.beginPath(); tc.moveTo(GUT2, 5 * RH2 + 0.5); tc.lineTo(gx[13] + gcols[13], 5 * RH2 + 0.5); tc.stroke();
        tc.fillStyle = 'rgba(42,120,214,.10)'; tc.fillRect(gx[1] + 1, 2 * RH2 + 1, gcols[1] - 1, RH2 - 1);
        tc.strokeStyle = 'var(--accent)'; tc.strokeStyle = '#2a78d6'; tc.lineWidth = 2; tc.strokeRect(gx[1] + 1, 2 * RH2 + 1, gcols[1] - 2, RH2 - 2);
      }
      fontsReady.then(drawGrid);
      drawGrid();

      // embedded charts
      const chartBox = (x, y, w, hh) => { const e = h('div', { class: 'abs' }); css(e, { left: x, top: y, width: w, height: hh, background: '#fff', border: '1px solid rgba(32,30,29,.16)', borderRadius: 3, boxShadow: '0 2px 10px rgba(32,30,29,.06)' }); inner.append(e); return e; };
      const lc = chartBox(48, 286, 590, 272), bc = chartBox(656, 286, 440, 272);
      const lsvg = h('svg', { width: 590, height: 272, viewBox: '0 0 590 272' }); lc.append(lsvg);
      const X0 = 58, X1 = 566, Y0 = 72, Y1 = 232, SMIN = 2800, SMAX = 3600;
      const sx = (j) => X0 + (X1 - X0) * j / 11, sy = (v) => Y1 - (Y1 - Y0) * (v - SMIN) / (SMAX - SMIN), yy = (v) => Y1 - (Y1 - Y0) * (v + 12) / 12;
      const txt = (svg, x, y, s2, attrs) => { const e = h('text', { x, y, ...attrs }); e.textContent = s2; svg.append(e); return e; };
      txt(lsvg, 18, 30, L('12 个月销售额与同比', '12-month sales and YoY'), { 'font-size': 15, 'font-weight': 500, fill: '#201e1d', 'font-family': 'IBM Plex Sans, Noto Sans SC' });
      txt(lsvg, 572, 30, L('¥3.84 亿 · 同比 −6.8%', '¥384M · YoY −6.8%'), { 'font-size': 12.5, fill: '#7d7979', 'text-anchor': 'end', 'font-family': 'IBM Plex Mono, Noto Sans SC' });
      [2900, 3100, 3300, 3500].forEach((v) => {
        lsvg.append(h('line', { x1: X0, x2: X1, y1: sy(v), y2: sy(v), stroke: 'rgba(32,30,29,.08)' }));
        txt(lsvg, X0 - 8, sy(v) + 4, K.fmt(v), { 'font-size': 10.5, fill: '#9a9693', 'text-anchor': 'end', 'font-family': 'IBM Plex Mono' });
      });
      MON12.forEach((m, j) => txt(lsvg, sx(j), 254, m, { 'font-size': 11, fill: '#9a9693', 'text-anchor': 'middle', 'font-family': 'IBM Plex Sans, Noto Sans SC' }));
      const clipId = 's1lc' + Math.floor(hash(3) * 1e6);
      const clip = h('clipPath', { id: clipId }); const clipR = h('rect', { x: 0, y: 0, width: 0, height: 272 }); clip.append(clipR);
      const defs = h('defs'); defs.append(clip); lsvg.append(defs);
      const plot = h('g', { 'clip-path': `url(#${clipId})` }); lsvg.append(plot);
      plot.append(h('path', { d: 'M' + YOY.map((v, j) => `${sx(j)} ${yy(v)}`).join(' L'), fill: 'none', stroke: '#9a9693', 'stroke-width': 1.5, 'stroke-dasharray': '4 4' }));
      plot.append(h('path', { d: 'M' + SALES.map((v, j) => `${sx(j)} ${sy(v)}`).join(' L'), fill: 'none', stroke: GREEN, 'stroke-width': 2.6, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      SALES.forEach((v, j) => plot.append(h('circle', { cx: sx(j), cy: sy(v), r: 3.2, fill: '#fff', stroke: GREEN, 'stroke-width': 1.8 })));
      // legend
      const lg = h('g'); lsvg.append(lg);
      lg.append(h('line', { x1: 18, x2: 36, y1: 50, y2: 50, stroke: GREEN, 'stroke-width': 2.6 })); txt(lg, 42, 54, L('销售额（万元）', 'Sales (¥10k)'), { 'font-size': 11.5, fill: '#7d7979', 'font-family': 'IBM Plex Sans, Noto Sans SC' });
      lg.append(h('line', { x1: 170, x2: 188, y1: 50, y2: 50, stroke: '#9a9693', 'stroke-width': 1.5, 'stroke-dasharray': '4 4' })); txt(lg, 194, 54, L('同比', 'YoY'), { 'font-size': 11.5, fill: '#7d7979', 'font-family': 'IBM Plex Sans, Noto Sans SC' });
      // bars: regional contribution to the decline (South China leads)
      const bsvg = h('svg', { width: 440, height: 272, viewBox: '0 0 440 272' }); bc.append(bsvg);
      txt(bsvg, 18, 30, L('区域下滑贡献', 'Contribution to the decline'), { 'font-size': 15, 'font-weight': 500, fill: '#201e1d', 'font-family': 'IBM Plex Sans, Noto Sans SC' });
      const BARS = [[REG[1], 1.0, 1], [REG[0], 0.46, 0.5], [REG[2], 0.36, 0.5], [REG[3], 0.27, 0.5]];
      const BX = L(80, 108), BW2 = 300 - (L(80, 108) - 80);
      bsvg.append(h('line', { x1: BX, x2: BX, y1: 62, y2: 236, stroke: 'rgba(32,30,29,.25)' }));
      const bars = BARS.map(([nm, v, a], i) => {
        const y = 72 + i * 42;
        txt(bsvg, BX - 10, y + 15, nm, { 'font-size': 13.5, fill: '#201e1d', 'text-anchor': 'end', 'font-family': 'IBM Plex Sans, Noto Sans SC' });
        const r = h('rect', { x: BX, y, width: 0, height: 22, fill: GREEN, 'fill-opacity': a, rx: 2 }); bsvg.append(r);
        return { r, w: v * BW2 };
      });
      // sheet tabs (bottom, Excel style)
      const tabs = h('div', { class: 'abs' }); css(tabs, { left: 0, top: BK.h - 40, width: BK.w, height: 40, background: '#f0f0ef', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'stretch', paddingLeft: 34 });
      const tabEls = TABS.map((name, i) => {
        const e = h('span', { text: name }); const active = i === 1;
        css(e, { display: 'flex', alignItems: 'center', padding: '0 18px', fontSize: 14, color: active ? GREEN : 'var(--muted)', fontWeight: active ? '600' : '400',
          background: active ? '#fff' : 'transparent', borderBottom: active ? `3px solid ${GREEN}` : '3px solid transparent', borderRight: '1px solid rgba(32,30,29,.08)' });
        tabs.append(e); return e;
      });
      const plus = h('span', { text: '+' }); css(plus, { display: 'flex', alignItems: 'center', padding: '0 14px', fontSize: 18, color: 'var(--muted)' }); tabs.append(plus);
      inner.append(tabs);

      // ---- the front pane: the 结论 sheet ----
      const front = h('div', { class: 'abs' });
      css(front, { left: FP.x, top: FP.y, width: FP.w, height: FP.h, background: '#fcfcfb', border: '1px solid rgba(32,30,29,.14)', borderRadius: 10, overflow: 'hidden',
        boxShadow: '0 40px 100px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.2)', fontFamily: 'var(--sans)', color: 'var(--fg)' });
      const fh = h('div'); css(fh, { height: 44, display: 'flex', alignItems: 'stretch', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', background: '#f0f0ef' });
      const ftab = h('span', { text: TABS[4] }); css(ftab, { display: 'flex', alignItems: 'center', padding: '0 20px', fontSize: 14.5, fontWeight: '600', color: GREEN, background: '#fff', borderBottom: `3px solid ${GREEN}` });
      const fnm = h('span', { text: T('s1_out') }); css(fnm, { display: 'flex', alignItems: 'center', padding: '0 18px', fontSize: 12.5, color: 'var(--muted)', fontFamily: 'var(--mono)' });
      fh.append(ftab, fnm); front.append(fh);
      const flines = FINDINGS.map((s2, i) => {
        const r = h('div'); css(r, { display: 'flex', borderBottom: i < 2 ? '1px solid rgba(32,30,29,.08)' : 'none', minHeight: 84 });
        const n = h('span', { text: String(i + 1) }); css(n, { width: 40, flex: 'none', background: '#f3f3f1', color: '#9a9693', fontFamily: 'var(--mono)', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' });
        const tx = h('span', { text: s2 }); css(tx, { padding: '14px 22px', fontSize: 17.5, lineHeight: '1.55', display: 'flex', alignItems: 'center' });
        r.append(n, tx); front.append(r); return tx;
      });
      group.append(front);

      // ---- the thread that pulls it up and traces the window ----
      const svg = h('svg', { class: 'full', viewBox: '0 0 1920 1080' }); layer.append(svg);
      const cx0 = BK.x + BK.w / 2, by = BK.y + BK.h, R0 = 10;
      const dRise = `M ${cx0} 1100 L ${cx0} ${by}`;
      const dL = `M ${cx0} ${by} H ${BK.x + R0} Q ${BK.x} ${by} ${BK.x} ${by - R0} V ${BK.y + R0} Q ${BK.x} ${BK.y} ${BK.x + R0} ${BK.y} H ${cx0}`;
      const dR = `M ${cx0} ${by} H ${BK.x + BK.w - R0} Q ${BK.x + BK.w} ${by} ${BK.x + BK.w} ${by - R0} V ${BK.y + R0} Q ${BK.x + BK.w} ${BK.y} ${BK.x + BK.w - R0} ${BK.y} H ${cx0}`;
      const paths = [dRise, dL, dR].map((d) => { const p = h('path', { d, fill: 'none', stroke: D.accent, 'stroke-width': 2.2, 'stroke-linecap': 'round' }); svg.append(p); return p; });
      const lens = paths.map((p, i) => (p.getTotalLength ? p.getTotalLength() : [362, 1700, 1700][i]) || [362, 1700, 1700][i]);
      paths.forEach((p, i) => F.attr(p, { 'stroke-dasharray': `${lens[i]} ${lens[i]}`, 'stroke-dashoffset': lens[i] }));
      const heads = [0, 1].map(() => { const g = h('g'); g.append(h('circle', { r: 7.5, fill: D.accent, opacity: 0.35 }), h('circle', { r: 3.4, fill: '#fff' })); svg.append(g); return g; });

      // ---- the chip it becomes (same component as the surface's) ----
      const chipWrap = h('div', { class: 'abs' }); css(chipWrap, { left: 0, top: 0, transformOrigin: '0 0' });
      const chip = K.fileChip(T('s1_out'), 'sheet'); css(chip, { whiteSpace: 'nowrap' });
      chipWrap.append(chip); layer.append(chipWrap);

      return (lt, t) => {
        // thread: rise, then both halves round the window at once
        const kr = tw(t, BOOK.rise[0], BOOK.rise[1], 'inQuad'), kt = tw(t, BOOK.trace[0], BOOK.trace[1], 'outCubic');
        const fadeT = 1 - tw(t, 25.75, 26.15, 'inOutQuad');
        css(svg, { opacity: fadeT, display: fadeT > 0 ? '' : 'none' });
        if (fadeT > 0) {
          F.attr(paths[0], { 'stroke-dashoffset': lens[0] * (1 - kr) });
          F.attr(paths[1], { 'stroke-dashoffset': lens[1] * (1 - kt) }); F.attr(paths[2], { 'stroke-dashoffset': lens[2] * (1 - kt) });
          const hp1 = kt > 0 ? paths[1].getPointAtLength(lens[1] * kt) : paths[0].getPointAtLength(lens[0] * kr);
          const hp2 = kt > 0 ? paths[2].getPointAtLength(lens[2] * kt) : hp1;
          const on = t >= BOOK.rise[0] && kt < 1;
          F.attr(heads[0], { transform: `translate(${hp1.x} ${hp1.y})`, opacity: on ? 1 : 0 });
          F.attr(heads[1], { transform: `translate(${hp2.x} ${hp2.y})`, opacity: on && kt > 0 ? 1 : 0 });
        }
        // the window fills in behind its outline
        const kp = tw(t, BOOK.pane[0], BOOK.pane[1], 'inOutQuad');
        // slow orbit while it hangs there; settles flat for the shrink
        const flat = tw(t, BOOK.morph[0], BOOK.morph[0] + 0.45, 'inOutCubic');
        const ry = lerp(-7, 3, tw(t, 25.4, 28.4, 'inOutQuad')) * (1 - flat), rx = lerp(5, 1.5, tw(t, 25.4, 28.4, 'inOutQuad')) * (1 - flat);
        css(group, { transform: `translateY(${(1 - tw(t, 25.5, 26.3, 'outCubic')) * 50}px) rotateX(${rx}deg) rotateY(${ry}deg)` });
        css(back, { opacity: kp });
        css(tb, { opacity: tw(t, 25.75, 26.1) });
        const nf = Array.from(formula).length, kf = prog(t, 25.95, 26.5);
        const ftxt = Array.from(formula).slice(0, Math.round(nf * kf)).join('');
        if (fxt.textContent !== ftxt) fxt.textContent = ftxt;
        css(fx, { opacity: tw(t, 25.8, 26.1) });
        css(grid, { opacity: tw(t, 25.7, 26.1) });
        css(tableCv, { opacity: tw(t, 25.9, 26.4), transform: `translateY(${(1 - tw(t, 25.9, 26.4, 'outCubic')) * 6}px)` });
        tabEls.forEach((e, i) => { const k = tw(t, BOOK.tabs + i * 0.09, BOOK.tabs + i * 0.09 + 0.3, 'outCubic'); css(e, { opacity: k, transform: `translateY(${(1 - k) * 10}px)` }); });
        css(plus, { opacity: tw(t, BOOK.tabs + 0.5, BOOK.tabs + 0.8) });
        const kc1 = tw(t, BOOK.line[0] - 0.15, BOOK.line[0] + 0.2, 'outQuad');
        css(lc, { opacity: kc1, transform: `translateY(${(1 - kc1) * 10}px)` });
        F.attr(clipR, { width: 590 * tw(t, BOOK.line[0], BOOK.line[1], 'inOutSine' in ease ? 'inOutSine' : 'inOutQuad') });
        const kc2 = tw(t, BOOK.bars - 0.2, BOOK.bars + 0.15, 'outQuad');
        css(bc, { opacity: kc2, transform: `translateY(${(1 - kc2) * 10}px)` });
        bars.forEach((b, i) => F.attr(b.r, { width: b.w * tw(t, BOOK.bars + i * 0.1, BOOK.bars + i * 0.1 + 0.55, 'outCubic') }));
        // the findings sheet: lands with a little weight, nearer the camera
        const kfr = tw(t, BOOK.front[0], BOOK.front[1], 'outCubic');
        const kcol = tw(t, BOOK.collapse[0], BOOK.collapse[1], 'inOutCubic');
        css(front, { opacity: kfr * (1 - kcol), transform: `translate3d(${-kcol * 300}px,${(1 - kfr) * 46 - kcol * 160}px,${70 * (1 - kcol)}px) scale(${1 - 0.25 * kcol})` });
        flines.forEach((e, i) => { const k = tw(t, BOOK.find + i * 0.25, BOOK.find + i * 0.25 + 0.45, 'outCubic'); css(e, { opacity: k, transform: `translateY(${(1 - k) * 8}px)` }); });

        // ---- S1-07: the window collapses into the chip ----
        const S2 = 1.2 + 0.03 * prog(BOOK.morph[1], 28.5, 33.0); // the next surface's resting zoom then
        const cw = chip.offsetWidth || 230, chh = chip.offsetHeight || 37;
        const tx0 = 960 + (543 - 960) * S2, ty0 = 540 + (491 - 540) * S2;   // chip slot: box left + padding, first row (see surfaces.js / kit inputBox)
        const target = { x: tx0, y: ty0, w: cw * S2, h: chh * S2 };
        const kw = ease.inOutCubic(prog(t, BOOK.morph[0], BOOK.morph[1])), kh = ease.inOutCubic(prog(t, BOOK.morph[0], BOOK.morph[1] - 0.12));
        const kpos = ease.inOutQuad(prog(t, BOOK.morph[0], BOOK.morph[1]));
        const w = Math.exp(lerp(Math.log(BK.w), Math.log(target.w), kw)), hh = Math.exp(lerp(Math.log(BK.h), Math.log(target.h), kh));
        const cxm = lerp(BK.x + BK.w / 2, target.x + target.w / 2, kpos), cym = lerp(BK.y + BK.h / 2, target.y + target.h / 2, kpos);
        const morphing = t >= BOOK.morph[0];
        if (morphing) {
          const kshape = tw(t, BOOK.morph[0] + 0.3, BOOK.morph[1], 'inOutQuad');
          css(back, { left: cxm - w / 2, top: cym - hh / 2, width: w, height: hh, borderRadius: lerp(10, hh / 2, kshape),
            background: `rgb(${Math.round(lerp(252, 240, kshape))},${Math.round(lerp(252, 240, kshape))},${Math.round(lerp(251, 239, kshape))})`,
            borderColor: `rgba(${Math.round(lerp(47, 32, kshape))},${Math.round(lerp(125, 30, kshape))},${Math.round(lerp(74, 29, kshape))},${lerp(0.55, 0.14, kshape).toFixed(3)})`,
            boxShadow: `0 ${50 * (1 - kshape)}px ${120 * (1 - kshape)}px rgba(0,0,0,${(0.6 * (1 - kshape)).toFixed(3)})` });
          const sc = w / BK.w;
          css(inner, { transform: `translate(${(w - BK.w * sc) / 2}px,${(hh - BK.h * sc) / 2}px) scale(${sc})`, opacity: 1 - tw(t, BOOK.morph[0] + 0.12, BOOK.morph[0] + 0.5, 'inOutQuad') });
        } else {
          css(back, { left: BK.x, top: BK.y, width: BK.w, height: BK.h, borderRadius: 10 });
          css(inner, { transform: 'none', opacity: 1 });
        }
        const kchip = tw(t, BOOK.morph[0] + 0.32, BOOK.morph[1] - 0.12, 'inOutQuad');
        css(chipWrap, { display: kchip > 0 ? '' : 'none', opacity: kchip, transform: `translate(${cxm - (cw * S2) / 2}px,${cym - (chh * S2) / 2}px) scale(${S2})` });
      };
    },
  });

  // =====================================================================
  // film grain over the whole depth, and the "example" tag
  F.scene({
    id: 's1_fx', start: T0, end: T1, z: 16,
    build(layer, { T }) {
      css(layer, { pointerEvents: 'none' });
      const g = K.grain(layer, 0.04);
      const tag = h('div', { class: 'abs', text: T('example') });
      css(tag, { right: 40, bottom: 34, fontFamily: 'var(--sans)', fontSize: 13, letterSpacing: '0.08em', color: 'rgba(161,157,155,.7)', border: '1px solid rgba(161,157,155,.3)', borderRadius: 4, padding: '3px 8px' });
      layer.append(tag);
      return (lt, t) => { g(t); css(tag, { opacity: F.env(t, 13.0, 28.4, 0.6, 0.4) }); };
    },
  });
})();
