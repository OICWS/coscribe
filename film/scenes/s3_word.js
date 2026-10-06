// Sentence 3 · the Word bid response (script S3-02 … S3-07, §4.3).
// 55.2  dive through the "招标文件.pdf · 203 页" chip: 203 pages rush past
// 56.6  the tender becomes three columns of text flowing upward (parallax);
//       the blue thread reads, requirement lines lift out, turn, and become
//       table rows; the count runs to 187
// 61.0  comparison: the company's own documents slide in, rows connect;
//       the outline of the response grows on the left
// 65.0  the quality shot: near-silence, two lines held, a taut blue line,
//       a Word comment, then the QuestionCard; the cursor does not click
// 69.5  the document assembles: TOC with leaders, numbered headings, a
//       response table, annex references; 86 pages
// 72.4  it shrinks into the .docx chip that the next surface picks up
(function () {
  const { h, attr, clamp, lerp, tw, ease, prog, L } = F;
  // F.css appends px to bare numbers; keep unitless properties unitless
  const UNITLESS = /^(fontWeight|lineHeight|flex|order)$/;
  const css = (e, p) => { for (const k in p) if (UNITLESS.test(k) && typeof p[k] === 'number') p[k] = String(p[k]); F.css(e, p); };
  const S = 55.2, E = 73.6;
  const ZH = F.lang === 'zh';

  // ---------------------------------------------------------------- copy
  const SERIF = '"Source Serif 4", "Noto Serif SC", serif';
  const SANS = '"IBM Plex Sans", "Noto Sans SC", sans-serif';
  const MONO = '"IBM Plex Mono", "Noto Sans SC", monospace';
  const CAT = { tec: L('技术', 'Technical'), com: L('商务', 'Commercial'), eli: L('资格', 'Eligibility') };

  // tender body text (filler for the flowing columns)
  const POOL = ZH ? [
    '投标人应仔细阅读招标文件的全部内容，并按要求提供相应的证明材料。',
    '本项目采用综合评分法，技术部分满分 60 分，商务部分满分 30 分，价格部分满分 10 分。',
    '投标文件应包括投标函、资格证明文件、技术响应文件、商务响应文件及附件。',
    '带“★”的条款为实质性要求，投标人须逐条响应，否则投标无效。',
    '供货范围包括华南区域 126 家门店的家居类商品，详见附件 A《供货清单》。',
    '投标人所投产品须为全新、未使用过的合格产品，并符合国家现行标准。',
    '中标人应在每批货物交付时提供出厂检验报告及合格证明。',
    '采购人有权对交付产品进行抽样检测，检测费用由中标人承担。',
    '如投标人提供的产品与样品不一致，采购人有权拒收并要求更换。',
    '投标报价应包括货物、包装、运输、保险、装卸及税费等全部费用。',
    '投标有效期为自投标截止之日起 90 日。',
    '联合体投标的，联合体各方均应满足本章第 2.1 条规定的资格条件。',
    '评标委员会将对投标文件进行符合性审查，未通过审查的投标将被否决。',
    '中标人不得将合同项下的供货义务转包或违法分包。',
    '合同履行期间，中标人应指定专人负责本项目的协调与沟通。',
    '因中标人原因延迟交付的，每延迟一日按合同金额的千分之五支付违约金。',
    '售后服务期内，中标人应在接到通知后 48 小时内到达现场处理。',
    '产品质保期自验收合格之日起计算。',
    '投标人应对所提供资料的真实性负责，如有虚假，取消其中标资格。',
    '所有包装须标注产品名称、规格、批号、生产日期及环保等级。',
    '招标人保留在授标前对供货数量进行调整的权利，调整幅度不超过百分之十五。',
    '投标文件须用中文编写，正本一份、副本四份，并提供电子版。',
    '如对招标文件有疑问，应于投标截止日 10 日前以书面形式提出。',
    '各门店收货时间为每日 8:00 至 11:00，节假日顺延。',
  ] : [
    'Bidders shall read the whole of this tender and supply the supporting documents it requires.',
    'Bids are scored on a combined basis: technical 60 points, commercial 30, price 10.',
    'A bid comprises the letter of bid, eligibility documents, the technical and commercial responses, and annexes.',
    'Clauses marked with a star are substantive and must be answered one by one, or the bid is void.',
    'The scope covers home-goods supply to 126 stores in South China; see Annex A, Supply List.',
    'All goods offered shall be new, unused and compliant with current national standards.',
    'Each delivery shall be accompanied by a factory inspection report and certificate of conformity.',
    'The purchaser may sample and test delivered goods at the supplier’s expense.',
    'Goods that differ from the approved samples may be rejected and shall be replaced.',
    'Prices shall include goods, packaging, freight, insurance, handling and all taxes.',
    'Bids remain valid for 90 days from the closing date.',
    'In a joint bid, every party shall meet the eligibility conditions of clause 2.1.',
    'The evaluation committee will check each bid for compliance; non-compliant bids are rejected.',
    'The supplier shall not subcontract its supply obligations under the contract.',
    'The supplier shall name one coordinator for the duration of the contract.',
    'Late delivery attributable to the supplier incurs liquidated damages of 0.5‰ per day.',
    'During the service period the supplier shall be on site within 48 hours of notice.',
    'The warranty period runs from the date of acceptance.',
    'Bidders are responsible for the accuracy of all documents; false statements void the award.',
    'Each carton shall show product name, specification, batch, date of manufacture and emission class.',
    'The purchaser may adjust quantities by up to 15% before award.',
    'Bids shall be written in Chinese, one original and four copies, plus an electronic copy.',
    'Questions about this tender shall be raised in writing no later than 10 days before closing.',
    'Stores receive goods daily between 8:00 and 11:00.',
  ];
  // one-line clauses around the starred requirements in the focus column
  const SHORT = ZH ? ['3.1.3　产品尺寸公差 ±2 mm。', '3.1.4　五金件盐雾试验不少于 48 h。', '2.6　近三年无重大违法记录。', '3.2.3　色差 ΔE 不大于 1.5。',
    '4.1.3　报价以人民币计。', '3.3.1　标签须注明环保等级。', '3.4.1　提供产品使用说明书。', '4.2.1　按批次开具发票。', '3.5.3　样品须加封标识。', '2.7　提供社保缴纳证明。'] :
    ['3.1.3  Dimensional tolerance ±2 mm.', '3.1.4  Salt-spray test of 48 h or more.', '2.6  No major violations in 3 years.', '3.2.3  Colour difference ΔE ≤ 1.5.',
      '4.1.3  Prices quoted in CNY.', '3.3.1  Labels show the emission class.', '3.4.1  Product manuals supplied.', '4.2.1  One invoice per batch.', '3.5.3  Samples sealed and labelled.', '2.7  Social-insurance records.'];
  const CHAPTERS = ZH
    ? ['第一章　投标人须知', '第二章　资格要求', '第三章　技术要求', '第四章　商务条款', '第五章　售后服务', '附件 A　供货清单', '附件 B　供货计划']
    : ['Chapter 1  Instructions to bidders', 'Chapter 2  Eligibility', 'Chapter 3  Technical requirements', 'Chapter 4  Commercial terms', 'Chapter 5  After-sales service', 'Annex A  Supply list', 'Annex B  Delivery schedule'];

  // requirement lines the thread lifts out of the text (S3-03)
  const FLOWN = [
    { c: '1.4', zh: '投标有效期为 90 日', en: 'Bids valid for 90 days', cat: 'com' },
    { c: '2.1', zh: '投标人须具有独立法人资格', en: 'Bidder must be a legal entity', cat: 'eli' },
    { c: '3.1.1', zh: '产品须符合 GB 18580 标准', en: 'Products comply with GB 18580', cat: 'tec' },
    { c: '2.4', zh: '须通过 ISO 9001 质量体系认证', en: 'ISO 9001 certification required', cat: 'eli' },
    { c: '3.2.1', zh: '提供第三方 CMA 检测报告', en: 'Third-party CMA test reports', cat: 'tec' },
    { c: '4.1.2', zh: '投标保证金人民币 20 万元', en: 'Bid bond of CNY 200,000', cat: 'com' },
    { c: '3.3.2', zh: '每批次附出厂检验报告', en: 'Inspection report with each batch', cat: 'tec' },
    { c: '3.5.2', zh: '开标前提交样品 3 套', en: 'Three sample sets before opening', cat: 'tec' },
  ];
  // the last rows of the 187, the ones checked against the company's documents (S3-04)
  const FINAL = [
    { c: '2.2', zh: '具有一般纳税人资格', en: 'Registered VAT taxpayer', cat: 'eli', to: [0, 0], st: 'ok' },
    { c: '2.3', zh: '近三年同类供货业绩 ≥ 5 项', en: '≥ 5 similar contracts in 3 years', cat: 'eli', to: [1, 0], st: 'ok' },
    { c: '2.5', zh: '具有中国环境标志认证', en: 'China Environmental Label', cat: 'eli', to: [0, 2], st: 'ok' },
    { c: '3.1.2', zh: '板材环保等级不低于 E0 级', en: 'Board emission class E0 or better', cat: 'tec', to: [2, 0], st: 'ok' },
    { c: '3.1.5', zh: '甲醛释放量 ≤ 0.050 mg/m³', en: 'Formaldehyde ≤ 0.050 mg/m³', cat: 'tec', to: [2, 1], st: 'ok' },
    { c: '3.4.2', zh: '整品质保期不少于 5 年', en: 'Warranty of at least 5 years', cat: 'tec', to: [2, 2], st: 'dev' },
    { c: '3.5.1', zh: '包装须为五层瓦楞纸箱', en: 'Five-ply corrugated cartons', cat: 'tec', to: [2, 3], st: 'dev' },
    { c: '4.1.1', zh: '报价含税、含运至各门店', en: 'Prices include tax and delivery', cat: 'com', to: [3, 2], st: 'ok' },
    { c: '4.3', zh: '交付期限：合同签订后 30 日内', en: 'Delivery within 30 days of signing', cat: 'com', to: null, st: 'q' },
    { c: '5.2', zh: '质量问题 7 日内退换', en: 'Defects replaced within 7 days', cat: 'com', to: [3, 0], st: 'ok' },
    { c: '5.3', zh: '48 小时内上门处理', en: 'On site within 48 hours', cat: 'com', to: [3, 1], st: 'ok' },
    { c: '5.4', zh: '华南区域设有仓储网点', en: 'Warehouse in South China', cat: 'com', to: [3, 2], st: 'ok' },
  ];
  const Q_ROW = FINAL.findIndex((r) => r.st === 'q');
  // filler requirements for the rows that stream past (only seen in motion)
  const MID_POOL = {
    tec: ZH ? ['产品尺寸公差 ±2 mm', '五金件盐雾试验 ≥ 48 h', '面料耐磨 ≥ 20000 次', '提供产品说明书', '标签含环保等级', '色差 ΔE ≤ 1.5', '承重测试报告', '阻燃等级 B1'] :
      ['Dimensional tolerance ±2 mm', 'Salt-spray test ≥ 48 h', 'Fabric abrasion ≥ 20,000 cycles', 'Product manuals supplied', 'Labels show emission class', 'Colour difference ΔE ≤ 1.5', 'Load test report', 'Flame rating B1'],
    com: ZH ? ['报价以人民币计', '分批开具增值税专用发票', '运输保险由中标人承担', '验收不合格当批退回', '月度对账', '不得转包'] :
      ['Prices in CNY', 'VAT invoices per batch', 'Freight insurance by supplier', 'Failed batches returned', 'Monthly reconciliation', 'No subcontracting'],
    eli: ZH ? ['近三年无重大违法记录', '提供财务审计报告', '提供社保缴纳证明', '非失信被执行人'] :
      ['No major violations in 3 years', 'Audited financial statements', 'Social-insurance records', 'Not on the defaulter list'],
  };
  const DOCS = [
    { kind: 'pdf', name: L('资质证书', 'Certificates'), items: [L('营业执照 · 一般纳税人', 'Business licence · VAT taxpayer'), 'ISO 9001:2015', L('中国环境标志认证', 'China Environmental Label')] },
    { kind: 'sheet', name: L('业绩表', 'Track record'), items: [L('近三年连锁渠道供货 14 项', '14 retail-chain contracts in 3 yrs'), L('单项合同额 ≥ 500 万元 · 6 项', '6 contracts ≥ CNY 5M')] },
    { kind: 'sheet', name: L('产品参数表', 'Product specifications'), items: [L('板材 ENF 级', 'Boards: ENF class'), L('甲醛 ≤ 0.025 mg/m³', 'Formaldehyde ≤ 0.025 mg/m³'), L('整品质保 3 年', '3-year warranty'), L('三层瓦楞纸箱', 'Three-ply cartons')] },
    { kind: 'doc', name: L('售后制度', 'After-sales policy'), items: [L('质量问题 7 日内退换', 'Defects replaced in 7 days'), L('48 小时上门响应', 'On site within 48 hours'), L('华南仓 · 广州 / 东莞', 'Warehouses: Guangzhou, Dongguan')] },
  ];
  // the response's outline = the document's TOC (§4.3)
  const OUTLINE = [
    [1, '1', L('投标函', 'Letter of bid'), 1], [1, '2', L('资格证明', 'Eligibility'), 3],
    [1, '3', L('技术响应', 'Technical response'), 12], [2, '3.1', L('产品参数', 'Product specifications'), 12], [2, '3.2', L('检测报告', 'Test reports'), 21],
    [2, '3.3', L('包装与标识', 'Packaging and labelling'), 26], [2, '3.4', L('质保期', 'Warranty'), 30], [2, '3.5', L('样品', 'Samples'), 33], [2, '3.6', L('质量保证', 'Quality assurance'), 35],
    [1, '4', L('商务响应', 'Commercial response'), 41], [2, '4.1', L('报价', 'Pricing'), 41], [2, '4.2', L('付款方式', 'Payment terms'), 45], [2, '4.3', L('交付期限', 'Delivery schedule'), 49],
    [1, '5', L('服务承诺', 'Service commitments'), 55], [1, '6', L('附件', 'Annexes'), 63],
  ];
  // the quality shot (S3-05)
  // each line is held with a little of its own page around it (ctx), so the
  // pair reads as two pieces of evidence, not two captions
  const QA = {
    label: L('招标文件.pdf · 第 147 页 · 4.3', 'tender.pdf · p. 147 · §4.3'),
    pre: L('交付期限：合同签订后 ', 'Delivery deadline: within '), key: L('30 日', '30 days'), post: L('内', ' of signing'),
    ctx: ZH ? ['第四章　商务条款', '4.2　付款方式：验收合格后 60 日内支付货款，按批次开具发票。'] :
      ['Chapter 4  Commercial terms', '4.2  Payment within 60 days of acceptance, invoiced per batch.'],
  };
  const QB = {
    label: L('招标文件.pdf · 附件 B · 第 2 页', 'tender.pdf · Annex B · p. 2'),
    pre: L('供货计划：合同签订后 ', 'Delivery schedule: within '), key: L('45 日', '45 days'), post: L('内交付', ' of signing'),
    ctx: ZH ? ['首批到货后 15 日内完成验收，后续批次见表 B-2。', '各门店收货时间为每日 8:00 至 11:00。'] :
      ['Acceptance within 15 days of first delivery; later batches in Table B-2.', 'Stores receive goods daily between 8:00 and 11:00.'],
  };
  const COMMENT = L('两处交付期限矛盾（第 147 页 30 日 / 附件 B 45 日），请确认按哪一个响应。',
    'The two delivery deadlines conflict (30 / 45 days). Please confirm which one to respond to.');
  const QCARD = {
    header: L('交付期限', 'Delivery deadline'),
    question: L('按哪个期限响应？', 'Which deadline should the response follow?'),
    options: [L('30 日（第 147 页）', '30 days (p. 147)'), L('45 日（附件 B）', '45 days (Annex B)'), L('先向招标方澄清', 'Ask the issuer first')],
  };

  // ---------------------------------------------------------------- timing
  // flights: requirement line lifted at tl, lands as a table row at tl + FL
  const FL = 0.9;
  const FLIGHT_T = [57.4, 57.88, 58.3, 58.66, 58.96, 59.2, 59.42, 59.6];
  const RUSH = [60.15, 61.0];             // rows 9 … 187 stream in
  const TOTAL = 187, WIN = 12;             // table window: 12 rows
  const CONN0 = 62.05, CONN_DT = 0.2, CONN_DUR = 0.45;
  const Q0 = 65.0;                         // quality shot
  const PAGE_T = [70.0, 70.22, 70.46], WIPE = 0.62;
  const Q_FS = ZH ? 44 : 40;   // the two held lines
  const TAUT = 68.0, CMT = 68.45, QC = 68.72, DOC0 = 69.8, OUT0 = 72.32;

  // scroll position of the tender text (px, focus-column units): a numeric
  // integral of a smooth velocity, plus the 203-page rush
  const POS = (() => {
    const t0 = 55.6, dt = 1 / 120, n = Math.ceil((E - t0) / dt) + 2, a = new Float64Array(n);
    const ss = (x, e0, e1) => { const k = clamp((x - e0) / (e1 - e0)); return k * k * (3 - 2 * k); };
    for (let i = 1; i < n; i++) {
      const x = t0 + i * dt;
      const v = 150 * ss(x, 55.6, 56.3) - 125 * ss(x, 60.8, 61.8) - 17 * ss(x, 64.6, 65.6);
      a[i] = a[i - 1] + v * dt;
    }
    return (t) => {
      const f = clamp((t - t0) / dt, 0, n - 1.001), i = Math.floor(f);
      return lerp(a[i], a[i + 1], f - i) + 2600 * ease.inOutCubic(prog(t, RUSH[0] - 0.3, RUSH[1] + 0.1));
    };
  })();
  const pageAt = (t) => 1 + 202 * clamp((POS(t) - POS(56.6)) / (POS(61.0) - POS(56.6)));

  // the table's row count (continuous during the rush)
  const streamN = (t) => (TOTAL - FLIGHT_T.length) * ease.inOutCubic(prog(t, RUSH[0], RUSH[1]));
  const rowsAt = (t) => {
    if (t < RUSH[0]) { let n = 0; FLIGHT_T.forEach((ft) => { if (t >= ft + FL) n++; }); return n; }
    return FLIGHT_T.length + streamN(t);
  };
  const rowVisible = (idx, t) => idx < FLIGHT_T.length ? t >= FLIGHT_T[idx] + FL : idx - FLIGHT_T.length < Math.floor(streamN(t) + 1e-6);

  // row list: 8 flown, 167 streamed, 12 final; categories total 92 / 61 / 34
  const ROWS = (() => {
    const r = F.rng(303), rows = FLOWN.map((x) => ({ ...x }));
    const need = { tec: 92, com: 61, eli: 34 };
    [...FLOWN, ...FINAL].forEach((x) => need[x.cat]--);
    const cats = [];
    for (const k of ['tec', 'com', 'eli']) for (let i = 0; i < need[k]; i++) cats.push(k);
    for (let i = cats.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [cats[i], cats[j]] = [cats[j], cats[i]]; }
    const sec = { tec: 3, com: 4, eli: 2 }, cnt = { tec: 1, com: 1, eli: 6 };
    cats.forEach((k) => {
      const pool = MID_POOL[k];
      cnt[k]++;
      const c = `${sec[k]}.${1 + (cnt[k] % 9)}.${1 + Math.floor(r() * 6)}`;
      rows.push({ c, txt: pool[Math.floor(r() * pool.length)], cat: k });
    });
    FINAL.forEach((x) => rows.push({ ...x }));
    rows.forEach((x) => { if (!x.txt) x.txt = ZH ? x.zh : x.en; });
    return rows;
  })();
  const CUM = (() => { const c = [{ tec: 0, com: 0, eli: 0 }]; ROWS.forEach((x, i) => { const n = { ...c[i] }; n[x.cat]++; c.push(n); }); return c; })();

  // ---------------------------------------------------------------- sound
  F.event(55.75, 'pages', { dur: 1.0, note: 'pages rush past under the iris' });
  F.event(56.6, 'paper_flow', { dur: 4.4, note: 'continuous low page-turn bed while the tender flows' });
  FLIGHT_T.forEach((ft, i) => { F.event(ft, 'lift', { i }); F.event(ft + FL, 'tick', { i, note: 'requirement lands as a table row' }); });
  F.event(RUSH[0], 'ticks', { dur: RUSH[1] - RUSH[0], count: TOTAL - FLIGHT_T.length, note: 'rows stream in' });
  F.event(57.95, 'ui', { note: 'tool row: Searching PDF' });
  F.event(61.25, 'slide', { note: 'company documents column slides in' });

  // ---------------------------------------------------------------- helpers
  const div = (style, text) => { const e = h('div', text != null ? { text } : {}); css(e, style); return e; };
  const span = (style, text) => { const e = h('span', text != null ? { text } : {}); css(e, style); return e; };
  const ICON = {
    close: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>',
    pencil: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
    cursor: '<svg width="26" height="34" viewBox="0 0 26 34"><path d="M2 2 L2 27 L8.2 21.4 L12.3 31 L16.6 29.2 L12.6 19.8 L21 19.6 Z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  };
  const badge = (kind, size = 11) => {
    const [color, ext] = K.KIND[kind];
    return span({ display: 'inline-block', flex: 'none', fontFamily: 'var(--mono)', fontSize: size, fontWeight: 500, color: '#fff', background: color, borderRadius: 4, padding: '2px 5px 1px', letterSpacing: '.04em', lineHeight: `${size + 3}px` }, ext);
  };
  const smooth = (k) => k * k * (3 - 2 * k);
  // line breaking: latin/digit runs stay whole; CJK closing punctuation never starts a line
  const NOSTART = /^[，。、；：！？）》」』”’%‰·]/;
  function tokens(text) {
    if (!ZH) return text.split(/(\s+)/).filter(Boolean);
    return text.match(/[A-Za-z0-9.,:/%‰±≤≥~\-–]+\s?|\s+|./gu) || [];
  }
  function wrapText(g, text, width) {
    const out = [];
    let cur = '';
    for (const tk of tokens(text)) {
      const nx = cur + tk;
      if (g.measureText(nx).width > width && cur.trim() && !NOSTART.test(tk)) { out.push(cur.trimEnd()); cur = tk.trimStart(); }
      else cur = nx;
    }
    if (cur.trim()) out.push(cur.trimEnd());
    return out;
  }

  // ======================================================================
  F.scene({
    id: 's3_word', start: S, end: E, z: 10,
    build(layer, { T }) {
      // v2: the Word world is a bright blue colour field with one soft,
      // slowly drifting light; the work floats on it as white paper
      const field = div({ position: 'absolute', inset: 0, background: 'var(--field-doc)' });
      const sheen = div({ position: 'absolute', left: 0, top: 0, width: 1500, height: 1500, borderRadius: '50%', pointerEvents: 'none',
        background: 'radial-gradient(closest-side, rgba(255,255,255,.22), rgba(255,255,255,.07) 55%, rgba(255,255,255,0))' });
      layer.append(field, sheen);

      // ---------- canvas: page tunnel + flowing tender columns ----------
      const cv = h('canvas', { width: 1920, height: 1080 });
      css(cv, { position: 'absolute', left: 0, top: 0, width: 1920, height: 1080 });
      layer.append(cv);
      const ctx = cv.getContext('2d');

      // columns (world coords; pf = parallax factor, sp = scroll speed)
      const COLS = [
        { id: 'near', x: -70, w: 420, size: 27, lh: 46, sp: 1.55, pf: 1.45, alpha: 0.2, blur: 2.4, seed: 7, page0: 3 },
        { id: 'focus', x: 432, w: 372, size: 18, lh: 32, sp: 1, pf: 1, alpha: 0.7, blur: 0, seed: 1, page0: 1 },
        { id: 'far', x: 900, w: 270, size: 14, lh: 25, sp: 0.62, pf: 0.62, alpha: 0.34, blur: 0.6, seed: 4, page0: 2 },
      ];
      const COL_TOP = 120, HEAD_Y = 478;
      const fontOf = (c, w = 400) => `${w} ${c.size}px ${SERIF}`;

      function wrapLines(c, need) {
        // a stream of paragraphs wrapped to the column width, with chapter
        // headings and page footers, deterministic per column
        ctx.font = fontOf(c);
        const r = F.rng(c.seed * 97 + 5), out = [];
        let para = 0, page = c.page0 * 7, sinceFoot = 0, chapter = c.seed % CHAPTERS.length;
        const perPage = Math.round(26 * 18 / c.size);
        while (out.length < need) {
          if (para % 11 === 0) { out.push({ k: 'blank' }); out.push({ k: 'head', s: CHAPTERS[chapter++ % CHAPTERS.length] }); out.push({ k: 'blank' }); }
          const s1 = POOL[Math.floor(r() * POOL.length)], s2 = r() < 0.45 ? POOL[Math.floor(r() * POOL.length)] : '';
          const num = `${1 + (chapter % 5)}.${1 + Math.floor(r() * 6)}.${1 + Math.floor(r() * 9)}`;
          const text = `${num}${ZH ? '　' : '  '}${s1}${s2 ? (ZH ? '' : ' ') + s2 : ''}`;
          for (const ln of wrapText(ctx, text, c.w)) { out.push({ k: 'body', s: ln, p: para }); sinceFoot++; }
          para++;
          if (sinceFoot > perPage) { out.push({ k: 'blank' }); out.push({ k: 'foot', s: `— ${++page} —` }); out.push({ k: 'blank' }); sinceFoot = 0; }
        }
        return out;
      }
      // the focus column needs exact requirement lines where the thread will be
      const focus = COLS[1];
      const lineIndexAt = (c, t, screenY) => Math.round((screenY - COL_TOP + POS(t) * c.sp) / c.lh - 0.5);
      let atlases = null, fontsReady = false;
      const fontLoads = [];
      function prepare() {
        COLS.forEach((c) => {
          c.lines = wrapLines(c, Math.ceil((POS(E) * c.sp + 1400) / c.lh));
        });
        FLIGHT_T.forEach((ft, i) => {
          const j = lineIndexAt(focus, ft, HEAD_Y);
          const f = FLOWN[i];
          focus.lines[j] = { k: 'req', s: ZH ? `★${f.c}　${f.zh}` : `${f.c}  ${f.en}`, flight: i };
          // keep the neighbours as plain text, not a footer or heading
          for (const d of [-1, 1]) { const nb = focus.lines[j + d]; if (nb && (nb.k === 'head' || nb.k === 'foot')) focus.lines[j + d] = { k: 'blank' }; }
          FLOWN[i].line = j;
        });
        // the starred requirements sit in a run of short one-line clauses, so
        // no paragraph is cut mid-sentence around them
        {
          const L2 = focus.lines, js = FLOWN.map((f) => f.line);
          let a = Math.min(...js), b = Math.max(...js);
          const pa = L2[a - 1] && L2[a - 1].p, pb = L2[b + 1] && L2[b + 1].p;
          while (a > 0 && pa != null && L2[a - 1].p === pa) a--;
          while (b < L2.length - 1 && pb != null && L2[b + 1].p === pb) b++;
          let n = 0;
          for (let j = a; j <= b; j++) if (L2[j].k !== 'req') L2[j] = { k: 'body', s: SHORT[n++ % SHORT.length] };
        }
        // the annex B line (pulled out in S3-05) lives in the far column
        // the near and far columns are pre-rendered (blurred) into atlases
        atlases = COLS.map((c) => {
          if (c.id === 'focus') return null;
          const a = document.createElement('canvas');
          a.width = c.w + 40; a.height = c.lines.length * c.lh + 20;
          const g = a.getContext('2d');
          g.filter = c.blur ? `blur(${c.blur}px)` : 'none';
          g.textBaseline = 'middle';
          c.lines.forEach((ln, j) => drawLine(g, c, ln, 20, j * c.lh + c.lh / 2 + 10, 1));
          return a;
        });
      }
      function drawLine(g, c, ln, x, y, a) {
        if (ln.k === 'blank') return;
        if (ln.k === 'head') { g.font = `600 ${Math.round(c.size * 1.12)}px ${SERIF}`; g.fillStyle = `rgba(247,245,243,${a})`; g.fillText(ln.s, x, y); return; }
        if (ln.k === 'foot') { g.font = `400 ${Math.round(c.size * 0.72)}px ${MONO}`; g.fillStyle = `rgba(161,157,155,${a * 0.9})`; const w = g.measureText(ln.s).width; g.fillText(ln.s, x + c.w / 2 - w / 2, y); return; }
        g.font = fontOf(c);
        g.fillStyle = `rgba(247,245,243,${a * (ln.k === 'req' ? 1 : 0.92)})`;
        g.fillText(ln.s, x, y);
      }
      // make sure the canvas faces (and the CJK chunks it needs) are loaded
      {
        const sample = POOL.join('') + CHAPTERS.join('') + FLOWN.map((f) => f.zh + f.en).join('') + '★—0123456789';
        for (const w of [400, 600]) fontLoads.push(document.fonts.load(`${w} 18px "Noto Serif SC"`, sample), document.fonts.load(`${w} 18px "Source Serif 4"`, sample));
        fontLoads.push(document.fonts.load(`400 12px "IBM Plex Mono"`, '— 0123456789'));
        Promise.all(fontLoads).then(() => { fontsReady = true; atlases = null; }, () => { fontsReady = true; atlases = null; });
      }

      // page tunnel textures (S3-02): dark-mode pages of the tender
      const PAGE_W = 300, PAGE_H = 424;
      let pageTex = null;
      function preparePages() {
        pageTex = [0, 1, 2, 3, 4].map((s) => {
          const a = document.createElement('canvas'); a.width = PAGE_W; a.height = PAGE_H;
          const g = a.getContext('2d'), r = F.rng(s * 31 + 9);
          g.fillStyle = '#211f1e'; g.fillRect(0, 0, PAGE_W, PAGE_H);
          g.strokeStyle = 'rgba(247,245,243,.16)'; g.lineWidth = 1; g.strokeRect(0.5, 0.5, PAGE_W - 1, PAGE_H - 1);
          g.textBaseline = 'middle';
          g.font = `400 7px ${MONO}`; g.fillStyle = 'rgba(161,157,155,.8)';
          g.fillText(L('华南连锁渠道 2027 年度供应项目 · 招标文件', 'South China retail chain · 2027 supply tender'), 28, 22);
          g.fillRect(28, 30, PAGE_W - 56, 0.6);
          let y = 50;
          if (s % 2 === 0) { g.font = `600 12px ${SERIF}`; g.fillStyle = 'rgba(247,245,243,.9)'; g.fillText(CHAPTERS[s % CHAPTERS.length], 28, y + 6); y += 30; }
          g.font = `400 8.5px ${SERIF}`;
          g.fillStyle = 'rgba(247,245,243,.62)';
          while (y < PAGE_H - 50) {
            const num = `${1 + (s % 5)}.${1 + Math.floor(r() * 6)}.${1 + Math.floor(r() * 9)}`;
            const para = `${num}${ZH ? '　' : '  '}${POOL[Math.floor(r() * POOL.length)]}${r() < 0.5 ? (ZH ? '' : ' ') + POOL[Math.floor(r() * POOL.length)] : ''}`;
            for (const ln of wrapText(g, para, PAGE_W - 56)) { if (y >= PAGE_H - 50) break; g.fillText(ln, 28, y); y += 13; }
            y += 6;
            if (r() < 0.18) { // a little table
              g.strokeStyle = 'rgba(247,245,243,.25)';
              for (let rr = 0; rr < 4; rr++) g.strokeRect(28.5, y + rr * 13 + 0.5, PAGE_W - 57, 13);
              g.beginPath(); g.moveTo(80.5, y); g.lineTo(80.5, y + 52); g.stroke();
              y += 62;
            }
          }
          return a;
        });
      }
      const TUN_N = 96, TUN_DZ = 0.42;
      const TUN = Array.from({ length: TUN_N }, (_, k) => {
        const axis = k % 5 === 0, th = k * 2.39996, R = axis ? 60 + 90 * F.hash(k + 3) : 520 + 620 * F.hash(k + 3);
        return { k, z: k * TUN_DZ, tex: k % 5, page: Math.round(1 + k * 202 / (TUN_N - 1)), ox: Math.cos(th) * R * 1.25, oy: Math.sin(th) * R * 0.8, rot: (F.hash(k + 21) - 0.5) * 10 };
      });

      // ---------- world (DOM, 2.5D camera) ----------
      const cam = K.camera(layer, { perspective: 2000 });
      const W = cam.world;
      const svg = h('svg', { class: 'full', width: 1920, height: 1080, viewBox: '0 0 1920 1080' });
      css(svg, { overflow: 'visible' });

      // reading thread (trail scrolls with the text it has read)
      const trailG = h('g');
      const trailHalo = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 12, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', opacity: 0.1 });
      const trail = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 2.5, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
      const trailHead = h('circle', { r: 5.5, fill: '#fff' });
      trailG.append(trailHalo, trail, trailHead);

      // --- requirement table ---
      const tbl = div({ position: 'absolute', left: 0, top: 0, width: 600, color: 'var(--d-fg)', fontFamily: SANS });
      const capNum = span({ fontFamily: 'var(--mono)', fontSize: 46, fontWeight: 500, letterSpacing: '-.01em', fontVariantNumeric: 'tabular-nums', color: 'var(--d-fg)' }, '0');
      const capLbl = span({ fontSize: 17, color: 'var(--d-muted)', marginLeft: 12 }, L('条要求', 'requirements'));
      const capBrk = div({ fontFamily: 'var(--mono)', fontSize: 14, color: 'var(--d-muted)', marginTop: 6, fontVariantNumeric: 'tabular-nums', whiteSpace: 'pre' });
      const cap = div({ position: 'absolute', left: 0, top: -112, display: 'block', whiteSpace: 'nowrap' });
      const capSrc = div({ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--d-muted)', marginBottom: 10 });
      capSrc.append(badge('pdf', 10), span({}, T('s3_chip')));
      const capRow = div({ display: 'flex', alignItems: 'baseline' }); capRow.append(capNum, capLbl);
      cap.append(capRow, capBrk);
      const thead = div({ display: 'grid', alignItems: 'center', height: 36, borderBottom: '1px solid var(--d-border)', fontSize: 13, color: 'var(--d-muted)', letterSpacing: '.02em' });
      const TH = [L('条款', 'Clause'), L('要求', 'Requirement'), L('类别', 'Category'), L('响应', 'Response')].map((s, i) => {
        const c = span({ padding: '0 12px', whiteSpace: 'nowrap', overflow: 'hidden' }, s); thead.append(c); return c;
      });
      const body = div({ position: 'relative', height: WIN * 36 + 4, overflow: 'hidden', marginTop: 4 });
      const SLOTS = Array.from({ length: WIN + 1 }, () => {
        const r = div({ position: 'absolute', left: 0, right: 0, height: 36, display: 'grid', alignItems: 'center', fontSize: 15, borderBottom: '1px solid rgba(247,245,243,.07)', borderRadius: 4 });
        const cells = [0, 1, 2, 3].map((i) => {
          const c = span({ padding: '0 12px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' });
          r.append(c); return c;
        });
        css(cells[0], { fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--d-muted)' });
        css(cells[2], { fontSize: 13, color: 'var(--d-muted)' });
        css(cells[3], { fontSize: 14, display: 'flex', alignItems: 'center', gap: 6 });
        body.append(r);
        return { r, cells, idx: -1, st: '' };
      });
      const tblBack = div({ position: 'absolute', left: -22, right: -22, top: -166, height: WIN * 36 + 224, background: 'rgba(20,19,18,.86)', borderRadius: 12, border: '1px solid rgba(247,245,243,.06)' });
      tbl.append(tblBack, cap, thead, body);
      const capTop = div({ position: 'absolute', left: 0, top: -150 }); capTop.append(capSrc); tbl.append(capTop);

      // --- company documents column (S3-04) ---
      const comp = div({ position: 'absolute', left: 1395, top: 0, width: 420, color: 'var(--d-fg)', fontFamily: SANS });
      const compHead = div({ position: 'absolute', top: 128, display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, color: 'var(--d-muted)' });
      compHead.append(badge('folder', 10), span({}, T('s3_chip2')));
      comp.append(compHead);
      const ITEM_POS = [];
      const compEls = [];
      {
        let y = 182;
        DOCS.forEach((d, gi) => {
          const head = div({ position: 'absolute', top: y, left: 0, display: 'flex', alignItems: 'center', gap: 9, fontSize: 16, fontWeight: 500, height: 30 });
          head.append(badge(d.kind, 10), span({}, d.name));
          comp.append(head); compEls.push({ el: head, gi, k: -1 });
          y += 34;
          ITEM_POS[gi] = [];
          d.items.forEach((it, k) => {
            const e = div({ position: 'absolute', top: y, left: 18, height: 28, display: 'flex', alignItems: 'center', fontSize: 14, color: 'rgba(247,245,243,.78)', paddingLeft: 12, borderLeft: '1px solid var(--d-border)', whiteSpace: 'nowrap' }, it);
            comp.append(e); compEls.push({ el: e, gi, k });
            ITEM_POS[gi][k] = { y: y + 14, el: e };
            y += 30;
          });
          y += 20;
        });
      }

      // --- outline (S3-04, left) ---
      const outl = div({ position: 'absolute', left: 118, top: 0, width: 380, color: 'var(--d-fg)' });
      const outlHead = div({ position: 'absolute', top: 128, display: 'flex', alignItems: 'center', gap: 8, fontSize: 15, color: 'var(--d-muted)', whiteSpace: 'nowrap' });
      outlHead.append(badge('doc', 10), span({}, T('s3_out')));
      outl.append(outlHead);
      const OUT_ELS = [];
      {
        let y = 180;
        OUTLINE.forEach(([lv, n, name]) => {
          const e = div({ position: 'absolute', top: y, left: lv === 1 ? 0 : 30, display: 'flex', gap: 12, alignItems: 'baseline', whiteSpace: 'nowrap',
            fontFamily: SERIF, fontSize: lv === 1 ? 19 : 15.5, fontWeight: lv === 1 ? 600 : 400, color: lv === 1 ? 'var(--d-fg)' : 'rgba(247,245,243,.72)' });
          e.append(span({ fontFamily: 'var(--mono)', fontSize: lv === 1 ? 15 : 13, fontWeight: 400, color: lv === 1 ? 'var(--d-doc)' : 'var(--d-muted)', minWidth: lv === 1 ? 16 : 28 }, n), span({}, name));
          outl.append(e); OUT_ELS.push(e);
          y += lv === 1 ? 38 : 30;
        });
      }
      const outlRule = div({ position: 'absolute', left: -18, top: 184, width: 1, background: 'var(--d-border)' });
      outl.append(outlRule);

      // connection lines (S3-04) are drawn in the svg
      const connG = h('g');
      const CONNS = [];
      let ci = 0;
      FINAL.forEach((row, ri) => {
        if (!row.to) return;
        const p = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 1.4, 'stroke-linecap': 'round', opacity: 0.85 });
        const dot = h('circle', { r: 3, fill: 'var(--d-accent)' });
        connG.append(p, dot);
        const t0 = CONN0 + ci * CONN_DT;
        CONNS.push({ p, dot, ri, to: row.to, t0, dev: row.st === 'dev' });
        F.event(t0, 'silk', { pitch: row.st === 'dev' ? 'low' : 'mid', note: 'connection line drawn' });
        F.event(t0 + CONN_DUR, row.st === 'dev' ? 'tick_low' : 'tick', { note: row.st === 'dev' ? 'deviation (△)' : 'full compliance' });
        ci++;
      });
      const Q_CONN_T = CONN0 + ci * CONN_DT - 0.05;    // the 4.3 row starts checking last
      OUTLINE.forEach((_, i) => F.event(61.45 + i * 0.205, 'tap', { soft: true, note: 'outline heading appears' }));

      svg.append(connG, trailG);

      // flights (S3-03): line lifts, turns 90° and lands as a row
      const flights = FLOWN.map((f, i) => {
        const el = div({ position: 'absolute', left: 0, top: 0, transformOrigin: '0 50%', backfaceVisibility: 'hidden', whiteSpace: 'nowrap', pointerEvents: 'none' });
        const a = div({ position: 'absolute', left: 0, top: 0, height: 32, lineHeight: '32px', fontFamily: SERIF, fontSize: 18, color: 'var(--d-fg)' }, ZH ? `★${f.c}　${f.zh}` : `${f.c}  ${f.en}`);
        const b = div({ position: 'absolute', left: 0, top: 0, height: 36, width: 596, display: 'grid', gridTemplateColumns: `78px 1fr ${ZH ? 72 : 106}px`, alignItems: 'center', fontSize: 15, fontFamily: SANS, color: 'var(--d-fg)', background: 'rgba(75,143,227,.16)', borderRadius: 4, boxShadow: '0 10px 30px rgba(0,0,0,.35)' });
        b.append(span({ padding: '0 12px', fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--d-accent-2)' }, f.c), span({ padding: '0 12px', whiteSpace: 'nowrap', overflow: 'hidden' }, ZH ? f.zh : f.en), span({ padding: '0 12px', fontSize: 13, color: 'var(--d-muted)' }, CAT[f.cat]));
        el.append(a, b);
        W.append(el);
        return { el, a, b };
      });

      W.append(outl, tbl, comp, svg);
      // svg must paint above the table but under flights
      flights.forEach((f) => W.append(f.el));

      // --- quality shot elements (S3-05) ---
      const qLayer = div({ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080 });
      W.append(qLayer);
      function qLine(q, below = false) {
        const wrap = div({ position: 'absolute', left: 0, top: 0, transformOrigin: '0 0', whiteSpace: 'nowrap' });
        const lab = div({ display: 'flex', alignItems: 'center', gap: 9, fontFamily: 'var(--mono)', fontSize: 14, color: 'var(--d-muted)', letterSpacing: '.02em', marginBottom: 12 });
        lab.append(badge('pdf', 10), span({}, q.label));
        const ln = div({ fontFamily: SERIF, fontSize: Q_FS, color: 'var(--d-fg)', lineHeight: 1.25, position: 'relative' });
        const hl = div({ position: 'absolute', left: -8, top: -2, bottom: -5, background: 'rgba(140,180,240,.20)', borderRadius: 3, transformOrigin: '0 50%' });
        const pre = span({}, q.pre), key = span({ position: 'relative' }, q.key), post = span({}, q.post);
        const ul = div({ position: 'absolute', left: 0, right: 0, bottom: -6, height: 2, background: 'var(--d-accent)', transformOrigin: '0 50%' });
        key.append(ul);
        ln.append(hl, pre, key, post);
        // a little of the page around the line: dim serif, fading away from the line
        const ctx = div({ fontFamily: SERIF, fontSize: 20, lineHeight: '34px', color: 'rgba(247,245,243,.42)' });
        q.ctx.forEach((s2, i) => ctx.append(div(i === 0 && !below ? { fontFamily: SANS, fontWeight: 600, fontSize: 18, color: 'rgba(247,245,243,.5)' } : {}, s2)));
        const fade = below ? 'linear-gradient(180deg,#000 0%,#000 30%,rgba(0,0,0,.15) 100%)' : 'linear-gradient(0deg,#000 0%,#000 30%,rgba(0,0,0,.15) 100%)';
        css(ctx, { webkitMaskImage: fade, maskImage: fade });
        // a hairline rule down the left edge, like a quoted excerpt
        const rule = div({ position: 'absolute', left: -28, width: 1, background: 'rgba(247,245,243,.16)', transformOrigin: '50% 0' });
        if (below) { css(lab, { marginBottom: 0, marginTop: 14 }); css(ctx, { marginTop: 18 }); wrap.append(ln, lab, ctx); }
        else { css(ctx, { marginBottom: 16 }); wrap.append(ctx, lab, ln); }
        wrap.append(rule);
        qLayer.append(wrap);
        return { wrap, lab, ln, hl, pre, key, post, ul, ctx, rule, below, m: null };
      }
      const qa = qLine(QA), qb = qLine(QB, true);
      css(qb.hl, { display: 'none' });
      const qSvg = h('svg', { class: 'full', width: 1920, height: 1080 }); css(qSvg, { overflow: 'visible' });
      const qHalo = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 12, 'stroke-linecap': 'round', opacity: 0.12 });
      const qPath = h('path', { fill: 'none', stroke: 'var(--d-accent)', 'stroke-width': 2.5, 'stroke-linecap': 'round' });
      const qHead = h('circle', { r: 5.5, fill: '#fff' });
      const qEnd = [h('circle', { r: 3.5, fill: 'var(--d-accent)' }), h('circle', { r: 3.5, fill: 'var(--d-accent)' })];
      const cLink = h('path', { fill: 'none', stroke: 'var(--file-doc)', 'stroke-width': 1.5, 'stroke-dasharray': '4 4', opacity: 0.9 });
      qSvg.append(cLink, qHalo, qPath, qHead, ...qEnd);
      qLayer.append(qSvg);

      // Word comment card (light, as Word draws it)
      function commentCard(scale = 1) {
        const c = div({ position: 'absolute', left: 0, top: 0, width: 420, padding: '14px 16px 15px', background: '#ffffff', color: '#201e1d', borderRadius: 8,
          border: '1px solid #e1dfdd', borderLeft: '4px solid var(--file-doc)', boxShadow: '0 12px 40px rgba(0,0,0,.45)', fontFamily: SANS, transformOrigin: '0 0' });
        const hd = div({ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 });
        const av = span({ width: 28, height: 28, borderRadius: 14, background: 'var(--file-doc)', color: '#fff', display: 'grid', placeItems: 'center', fontSize: 13, fontWeight: 600 }, 'C');
        hd.append(av, span({ fontSize: 14, fontWeight: 600 }, 'Coscribe'), span({ fontSize: 12.5, color: '#7d7979', marginLeft: 'auto' }, L('批注', 'Comment')));
        const tx = div({ fontSize: 15, lineHeight: 1.6, color: '#201e1d' }, COMMENT);
        c.append(hd, tx);
        return c;
      }
      const cmt = commentCard();
      qLayer.append(cmt);

      // QuestionCard (dark theme, built 1:1 from QuestionCard.tsx, scaled)
      const qc = div({ position: 'absolute', left: 0, top: 0, width: 420, borderRadius: 14, border: '1px solid var(--d-border)', background: 'var(--d-card)',
        boxShadow: '0 4px 24px rgba(0,0,0,.4)', color: 'var(--d-fg)', fontFamily: SANS, transformOrigin: '0 0', overflow: 'hidden' });
      {
        const top = div({ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, padding: '14px 16px 8px' });
        const left = div({ minWidth: 0 });
        left.append(div({ marginBottom: 2, fontSize: 12, lineHeight: '16px', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '.025em', color: 'var(--d-muted)' }, QCARD.header),
          div({ fontSize: 14, lineHeight: '20px', fontWeight: 500 }, QCARD.question));
        const x = span({ flex: 'none', borderRadius: 6, padding: 4, color: 'var(--d-muted)', display: 'grid' }); x.innerHTML = ICON.close;
        top.append(left, x);
        const opts = div({ borderTop: '1px solid var(--d-border)' });
        QCARD.options.forEach((o, i) => {
          const r = div({ display: 'flex', alignItems: 'center', gap: 12, borderBottom: '1px solid var(--d-border)', padding: '10px 16px', fontSize: 14, lineHeight: '20px' });
          r.append(span({ width: 20, height: 20, flex: 'none', display: 'grid', placeItems: 'center', borderRadius: 4, background: 'var(--d-bg)', fontSize: 12, color: 'var(--d-muted)' }, String(i + 1)), span({ whiteSpace: 'nowrap' }, o));
          opts.append(r);
        });
        const se = div({ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', fontSize: 14, lineHeight: '20px', color: 'var(--d-muted)' });
        const pe = span({ display: 'grid', flex: 'none' }); pe.innerHTML = ICON.pencil;
        se.append(pe, span({}, 'Something else'));
        opts.append(se);
        qc.append(top, opts);
      }
      qLayer.append(qc);

      // ---------- screen-space overlays ----------
      const ovl = div({ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, pointerEvents: 'none' });
      layer.append(ovl);
      // page ticker at the right edge
      const ticker = div({ position: 'absolute', left: 1846, top: 0, width: 50, height: 1080 });
      const TICKS = Array.from({ length: 11 }, () => { const e = div({ position: 'absolute', right: 0, fontFamily: 'var(--mono)', fontSize: 13, color: 'var(--d-fg)', fontVariantNumeric: 'tabular-nums', textAlign: 'right' }); ticker.append(e); return e; });
      const tickMark = div({ position: 'absolute', right: 40, width: 12, height: 1.5, background: 'var(--d-accent)', top: HEAD_Y });
      const tickOf = div({ position: 'absolute', right: 0, fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--d-muted)', top: HEAD_Y + 16 }, '/ 203');
      ticker.append(tickMark);
      ovl.append(ticker);
      // the tool row (ChatLog run header: "Searched PDF: `…`, read `…`")
      const tool = div({ position: 'absolute', left: 432, top: 938, transformOrigin: '0 50%', fontFamily: SANS, fontSize: 14, lineHeight: '20px', color: 'var(--d-muted)', whiteSpace: 'nowrap',
        padding: '9px 14px', background: 'rgba(35,33,32,.92)', border: '1px solid var(--d-border)', borderRadius: 10, display: 'flex', alignItems: 'center', gap: 4 });
      const toolTxt = span({});
      const chev = span({ display: 'grid' }); chev.innerHTML = ICON.chevron;
      tool.append(toolTxt, chev);
      ovl.append(tool);
      const q1 = L('交付期限', 'delivery'), pdfName = L('招标文件.pdf', 'tender.pdf');
      const code = (s) => `<code style="border-radius:4px;background:rgba(247,245,243,.12);padding:2px 4px;font-family:var(--mono);font-size:.85em;color:var(--d-fg)">${s}</code>`;
      const shimmer = (s, t) => { const p = (1 - ((t / 1.8) % 1)) * 100; return `<span style="background:linear-gradient(90deg,var(--d-muted) 0%,var(--d-muted) 35%,var(--d-fg) 50%,var(--d-muted) 65%,var(--d-muted) 100%) ${p.toFixed(1)}% 0 / 250% 100%;-webkit-background-clip:text;background-clip:text;color:transparent">${s}</span>`; };
      // cursor
      const cursor = div({ position: 'absolute', left: 0, top: 0, width: 26, height: 34 }); cursor.innerHTML = ICON.cursor;
      css(cursor, { filter: 'drop-shadow(0 2px 4px rgba(0,0,0,.5))' });
      // example tag
      const ex = div({ position: 'absolute', left: 40, top: 1034, fontFamily: 'var(--mono)', fontSize: 12, letterSpacing: '.08em', color: 'var(--d-muted)', opacity: 0.55 }, T('example'));
      ovl.append(ex);

      // ---------- the document (S3-06 / S3-07) ----------
      const docCam = K.camera(layer, { perspective: 2400 });
      const D = docCam.world;
      docCam.view.style.visibility = 'hidden';   // laid out from the start (no first-show layout spike), shown at DOC0
      const PW = 440, PH = 622, PT = 132, PX = [190, 666, 1142];
      const ink = '#201e1d', docBlue = '#2d62b8';
      const BODY = ZH ? `400 10.5px/1.75 "Noto Serif SC", serif` : `400 10.5px/1.6 "Source Serif 4", serif`;
      const HFONT = ZH ? '"Noto Sans SC", sans-serif' : '"IBM Plex Sans", sans-serif';
      const pages = [];
      function page(i) {
        const pg = div({ position: 'absolute', left: PX[i], top: PT, width: PW, height: PH, background: '#ffffff', borderRadius: 2, overflow: 'hidden',
          boxShadow: '0 30px 80px rgba(0,0,0,.5), 0 0 0 1px rgba(0,0,0,.25)', color: ink, transformOrigin: '50% 50%' });
        const inner = div({ position: 'absolute', left: 0, top: 0, width: PW, height: PH, padding: '34px 44px 0', transformOrigin: '0 0' });
        // the agent's line, writing the page top-down (light-surface accent)
        const wl = div({ position: 'absolute', left: 0, right: 0, top: 0, height: 2, background: 'var(--accent)', opacity: 0 });
        pg.append(inner, wl);
        const blocks = [];
        const add = (e) => { inner.append(e); blocks.push(e); return e; };
        // running header
        const hd = add(div({ display: 'flex', justifyContent: 'space-between', fontFamily: HFONT, fontSize: 7.5, color: '#8a8685', borderBottom: '0.75px solid #cfcccb', paddingBottom: 4, marginBottom: 16 }));
        hd.append(span({}, L('华南连锁渠道 2027 年度供应项目', 'South China retail chain · 2027 supply')), span({}, L('投标文件', 'Bid response')));
        return { pg, inner, blocks, add, wl };
      }
      const H1 = (txt, num) => { const e = div({ fontFamily: HFONT, fontSize: 15, fontWeight: 700, color: docBlue, margin: '4px 0 9px', letterSpacing: ZH ? '.02em' : 0 }); e.append(span({ marginRight: 10 }, num), span({}, txt)); return e; };
      const H2 = (txt, num) => { const e = div({ fontFamily: HFONT, fontSize: 12, fontWeight: ZH ? 700 : 600, color: ink, margin: '10px 0 6px' }); e.append(span({ marginRight: 8 }, num), span({}, txt)); return e; };
      const P = (txt) => div({ font: BODY, color: '#2b2928', textAlign: 'justify', textIndent: ZH ? '2em' : 0, marginBottom: 6 }, txt);
      const foot = (n) => div({ position: 'absolute', left: 0, right: 0, bottom: 22, textAlign: 'center', fontFamily: HFONT, fontSize: 8, color: '#8a8685' }, n);

      // page 1: title + TOC with dot leaders
      {
        const p = page(0);
        p.add(div({ fontFamily: HFONT, fontSize: 20, fontWeight: 700, textAlign: 'center', marginTop: 4, letterSpacing: ZH ? '.3em' : '.02em' }, L('投标文件', 'Bid Response')));
        p.add(div({ fontFamily: HFONT, fontSize: 8.5, textAlign: 'center', color: '#6f6b6a', margin: '6px 0 18px' }, L('项目编号：HNLS-2027-GY-018', 'Tender ref. HNLS-2027-GY-018')));
        p.add(div({ fontFamily: HFONT, fontSize: 13, fontWeight: 700, color: docBlue, marginBottom: 10 }, L('目录', 'Contents')));
        OUTLINE.forEach(([lv, n, name, pg]) => {
          const r = div({ display: 'flex', alignItems: 'baseline', fontFamily: lv === 1 ? HFONT : (ZH ? '"Noto Serif SC", serif' : '"Source Serif 4", serif'), fontSize: lv === 1 ? 10.5 : 10, fontWeight: lv === 1 ? (ZH ? 700 : 600) : 400,
            paddingLeft: lv === 1 ? 0 : 18, marginBottom: lv === 1 ? 5 : 3.5, marginTop: lv === 1 ? 5 : 0, color: ink });
          const lead = span({ flex: 1, height: 8, margin: '0 5px', alignSelf: 'flex-end', marginBottom: 3,
            backgroundImage: 'radial-gradient(circle, #6f6b6a 0.7px, transparent 0.9px)', backgroundSize: '4px 3px', backgroundRepeat: 'repeat-x', backgroundPosition: '0 100%' });
          r.append(span({ minWidth: lv === 1 ? 16 : 24 }, n), span({}, name), lead, span({ fontFamily: '"IBM Plex Sans", sans-serif', fontWeight: 400, fontVariantNumeric: 'tabular-nums' }, pg));
          p.add(r);
        });
        p.inner.append(foot('i'));
        pages.push(p);
      }
      // page 12: 3 技术响应 / 3.1 with the response table
      {
        const p = page(1);
        p.add(H1(L('技术响应', 'Technical response'), '3'));
        p.add(P(L('我方已逐条阅读招标文件第三章技术要求，对全部 92 条技术要求逐条作出响应；偏离项汇总于附件 7《偏离表》。',
          'We have read every clause of Chapter 3 and respond to all 92 technical requirements below; deviations are listed in Annex 7.')));
        p.add(H2(L('产品参数', 'Product specifications'), '3.1'));
        p.add(P(L('产品参数响应见表 3-1，证明材料见附件 4。', 'Specification responses are given in Table 3-1; evidence in Annex 4.')));
        const cols = ZH ? ['条款', '招标要求', '投标响应', '偏离', '证明材料'] : ['Clause', 'Requirement', 'Response', 'Deviation', 'Evidence'];
        const rows = [
          ['3.1.1', L('符合 GB 18580', 'GB 18580'), L('符合', 'Complies'), L('无', 'None'), L('附件 4-1', 'A4-1')],
          ['3.1.2', L('环保等级不低于 E0', 'E0 or better'), L('ENF 级', 'ENF class'), L('正偏离', 'Exceeds'), L('附件 4-2', 'A4-2')],
          ['3.1.5', L('甲醛 ≤ 0.050 mg/m³', 'HCHO ≤ 0.050'), L('≤ 0.025 mg/m³', '≤ 0.025'), L('正偏离', 'Exceeds'), L('附件 4-3', 'A4-3')],
          ['3.1.7', L('含水率 8%–12%', 'Moisture 8–12%'), '9.6%', L('无', 'None'), L('附件 4-3', 'A4-3')],
          ['3.2.1', L('CMA 检测报告', 'CMA test report'), L('已提供', 'Provided'), L('无', 'None'), L('附件 4-4', 'A4-4')],
          ['3.3.2', L('出厂检验报告', 'Inspection report'), L('每批随货', 'Every batch'), L('无', 'None'), L('附件 4-5', 'A4-5')],
          ['3.4.2', L('质保期 ≥ 5 年', 'Warranty ≥ 5 yrs'), L('3 年', '3 years'), L('负偏离', 'Below'), L('附件 7', 'A7')],
          ['3.5.1', L('五层瓦楞纸箱', 'Five-ply cartons'), L('三层瓦楞', 'Three-ply'), L('负偏离', 'Below'), L('附件 7', 'A7')],
          ['3.5.2', L('样品 3 套', '3 sample sets'), L('开标前送达', 'Before opening'), L('无', 'None'), '—'],
        ];
        p.add(div({ fontFamily: HFONT, fontSize: 8.5, textAlign: 'center', color: '#4a4645', margin: '6px 0 4px' }, L('表 3-1　产品参数响应表', 'Table 3-1  Specification responses')));
        const tb = h('table');
        css(tb, { width: '100%', borderCollapse: 'collapse', fontSize: 8.6, fontFamily: ZH ? '"Noto Sans SC", sans-serif' : '"IBM Plex Sans", sans-serif', color: ink, tableLayout: 'fixed' });
        const cw = ['13%', '29%', '22%', '15%', '21%'];
        const tr0 = h('tr');
        cols.forEach((c, i) => { const th = h('th', { text: c }); css(th, { width: cw[i], background: '#e9eef7', border: '0.75px solid #9fa9bb', padding: '3px 4px', fontWeight: 600, textAlign: 'left', color: '#1d3f78' }); tr0.append(th); });
        tb.append(tr0);
        const trs = [];
        rows.forEach((r) => {
          const tr = h('tr');
          r.forEach((c, i) => { const td = h('td', { text: c }); css(td, { border: '0.75px solid #b9b6b5', padding: '3px 4px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'clip', fontFamily: i === 0 ? '"IBM Plex Mono", monospace' : 'inherit', fontSize: i === 0 ? 8 : 8.6, color: (c === '负偏离' || c === 'Below') ? '#93600f' : ink }); tr.append(td); });
          tb.append(tr); trs.push(tr);
        });
        p.add(tb); p.table = tb;
        p.add(div({ font: BODY, fontSize: 9, color: '#4a4645', marginTop: 8 }, L('注：负偏离项的原因与替代方案见附件 7《偏离表》。', 'Note: reasons and alternatives for each deviation are given in Annex 7.')));
        p.inner.append(foot('12'));
        p.rows = trs;
        pages.push(p);
      }
      // page 49: 4.3 交付期限 (with the comment) + 4.4
      {
        const p = page(2);
        p.add(H2(L('付款方式', 'Payment terms'), '4.2'));
        p.add(P(L('我方接受招标文件第 4.2.3 条：验收合格后 60 日内支付货款，按批次开具增值税专用发票。', 'We accept clause 4.2.3: payment within 60 days of acceptance, with a VAT invoice per batch.')));
        const h43 = H2(L('交付期限', 'Delivery schedule'), '4.3');
        css(h43, { position: 'relative' });
        const h43hl = div({ position: 'absolute', left: -3, right: ZH ? 250 : 210, top: -1, bottom: -1, background: 'rgba(45,98,184,.16)', borderBottom: '1.5px solid ' + docBlue });
        h43.prepend(h43hl);
        p.add(h43);
        p.add(P(L('交付期限以招标方澄清为准（见批注）。我方可按第 147 页第 4.3 条或附件 B 的期限组织供货，分批供货计划见附件 5。',
          'The delivery period follows the issuer’s clarification (see comment). We can supply on either the p. 147 §4.3 or the Annex B schedule; the batch plan is in Annex 5.')));
        p.add(div({ fontFamily: HFONT, fontSize: 8.5, textAlign: 'center', color: '#4a4645', margin: '6px 0 4px' }, L('表 4-3　分批供货计划', 'Table 4-3  Batch plan')));
        const tb = h('table');
        css(tb, { width: '100%', borderCollapse: 'collapse', fontSize: 8.6, fontFamily: HFONT, tableLayout: 'fixed' });
        [[L('批次', 'Batch'), L('门店', 'Stores'), L('品类', 'Category'), L('交付', 'Delivery')],
          ['1', '42', L('家居收纳', 'Storage'), L('T + 30 / 45 日', 'T + 30 / 45 d')],
          ['2', '46', L('床品布艺', 'Textiles'), L('T + 60 日', 'T + 60 d')],
          ['3', '38', L('厨房餐具', 'Kitchen'), L('T + 90 日', 'T + 90 d')]].forEach((r, ri) => {
          const tr = h('tr');
          r.forEach((c) => { const td = h(ri ? 'td' : 'th', { text: c }); css(td, { border: '0.75px solid ' + (ri ? '#b9b6b5' : '#9fa9bb'), padding: '3px 4px', textAlign: 'left', fontWeight: ri ? 400 : 600, background: ri ? 'transparent' : '#e9eef7', color: ri ? ink : '#1d3f78' }); tr.append(td); });
          tb.append(tr);
        });
        p.add(tb);
        p.add(H2(L('违约责任', 'Liability for delay'), '4.4'));
        p.add(P(L('因我方原因延迟交付的，按招标文件第 4.4.1 条承担违约金；不可抗力情形依合同约定处理。', 'Delay attributable to us incurs liquidated damages under clause 4.4.1; force majeure is handled per the contract.')));
        p.add(P(L('附：附件 5《分批供货计划》、附件 6《售后服务制度》。', 'Attached: Annex 5, Batch plan; Annex 6, After-sales policy.')));
        p.inner.append(foot('49'));
        p.h43 = h43;
        pages.push(p);
      }
      // stack of further pages behind page 49, page count, file label
      const stack = [1, 2, 3, 4].map((k) => div({ position: 'absolute', left: PX[2] + k * 7, top: PT + k * 7, width: PW, height: PH, background: '#f4f3f2', borderRadius: 2, boxShadow: '0 20px 60px rgba(0,0,0,.4), 0 0 0 1px rgba(0,0,0,.2)' }));
      stack.reverse().forEach((s) => D.append(s));
      pages.forEach((p) => D.append(p.pg));
      css(pages[1].pg, { zIndex: 3 }); css(pages[0].pg, { zIndex: 2 }); css(pages[2].pg, { zIndex: 1 });
      const pcount = div({ position: 'absolute', left: PX[2] + PW + 28, top: PT + PH + 46, fontFamily: 'var(--mono)', fontSize: 15, color: 'var(--d-fg)', whiteSpace: 'nowrap', transform: 'translateX(-100%)', fontVariantNumeric: 'tabular-nums' });
      const flabel = div({ position: 'absolute', left: PX[0], top: PT - 40, display: 'flex', alignItems: 'center', gap: 9, fontSize: 15, color: 'var(--d-muted)', fontFamily: SANS, whiteSpace: 'nowrap' });
      flabel.append(badge('doc', 10), span({}, T('s3_out')));
      D.append(pcount, flabel);
      // the comment, docked in the margin of page 49
      const dcmt = commentCard();
      D.append(dcmt);
      // screen-space top layer: the comment in flight, the QuestionCard, the cursor
      const topL = div({ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, pointerEvents: 'none' });
      layer.append(topL);
      topL.append(cmt, qc, cursor);
      // the deliverable chip the document becomes (S3-07)
      const chipWrap = div({ position: 'absolute', left: 0, top: 0, transformOrigin: '50% 50%' });
      const chip = K.fileChip(T('s3_out'), 'doc');
      chipWrap.append(chip);
      layer.append(chipWrap);
      PAGE_T.forEach((tp, i) => F.event(tp, 'paper', { gain: [1, 0.7, 0.5][i], dur: WIPE, note: 'a page of the bid is written top-down (accent line sweeps it)' }));
      F.event(PAGE_T[2] + WIPE + 0.05, 'paper_fan', { note: 'the remaining pages fan out behind page 49' });
      F.event(TAUT, 'string', { note: 'the blue line pulls taut between 30 and 45' });
      F.event(65.05, 'paper_lift', { note: 'p.147 line lifted out' });
      F.event(65.3, 'paper_lift', { note: 'Annex B line lifted out' });
      F.event(65.95, 'silk', { pitch: 'mid', note: 'thread drawn between the two lines (slack)' });
      F.event(CMT, 'ding', { note: 'Word comment lands (very soft)' });
      F.event(QC, 'card', { note: 'QuestionCard rises' });
      F.event(OUT0, 'whoosh', { dur: 0.9, reverse: true, note: 'document folds into a chip' });
      F.event(73.22, 'drop', { note: 'docx chip settles in the next box' });

      const grainU = K.grain(layer, 0.04);

      // ---------- per-frame ----------
      // quality-shot layout (world coords)
      const PAIRX = 640, PAIR_AY = 446, PAIR_GAP = 216, CMT_Y = 334, QC_Y = 500, CS = 1.32, QS = 1.32;
      let CARD_X = 1080, qFinal = false;
      const CAM2 = { x: 952 };
      const camKeys = [
        { t: 56.3, x: 960, y: 540, s: 1.22 },
        { t: 57.4, x: 962, y: 540, s: 1.035, e: 'outCubic' },
        { t: 61.0, x: 985, y: 532, s: 1.0, e: 'inOutQuad' },
        { t: 62.0, x: 960, y: 520, s: 0.985, e: 'inOutCubic' },
        { t: 65.0, x: 962, y: 524, s: 0.995, e: 'inOutQuad' },
        { t: 66.4, x: PAIRX, y: 580, s: 1.1, e: 'inOutCubic' },
        { t: 68.1, x: PAIRX + 6, y: 580, s: 1.125, e: 'inOutQuad' },
        { t: 69.05, x: 952, y: 568, s: 1.0, e: 'inOutCubic', ph2: true, dx: 0 },
        { t: 70.0, x: 958, y: 568, s: 1.012, e: 'inOutQuad', ph2: true, dx: 6 },
      ];
      const docKeys = [
        { t: 69.7, x: 1000, y: 596, s: 0.95 },
        { t: 70.9, x: 1000, y: 556, s: 1.0, e: 'outCubic' },
        { t: 72.4, x: 1004, y: 554, s: 1.035, e: 'inOutQuad' },
      ];
      const camAt = (t) => K.camPath(camKeys, t);
      const eff = (c, pf) => ({ x: 960 + (c.x - 960) * pf, y: 540 + (c.y - 540) * pf, s: 1 + (c.s - 1) * pf });

      const trailPts = (t) => {
        // head path in screen space of the focus column, trail rides the scroll
        const t0 = 56.95, pts = [];
        const xh = (u) => focus.x + focus.w * 0.48 + Math.sin((u - 57.1) * 3.4) * 150 * smooth(prog(u, 56.95, 57.6));
        const yh = (u) => lerp(-60, HEAD_Y, ease.outCubic(prog(u, t0, 57.4)));
        const tEnd = Math.min(t, 61.05);
        for (let u = Math.max(t0, tEnd - 3.2); u <= tEnd + 1e-6; u += 1 / 60) pts.push([xh(u), yh(u) - (POS(t) - POS(u))]);
        return pts;
      };
      const pathD = (pts) => pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join('');

      let lastRowsKey = '';
      return (lt, t) => {
        if (fontsReady && !atlases) { prepare(); preparePages(); }
        if (!pageTex) preparePages();
        if (!atlases) prepare();

        const c = camAt(t);
        cam.set(c);
        const inQ = tw(t, Q0, Q0 + 0.7, 'inOutQuad');            // background dims for the quality shot
        const outQ = tw(t, DOC0 - 0.15, DOC0 + 0.4, 'inOutQuad'); // everything leaves for the document

        // ======== canvas ========
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, 1920, 1080);
        // --- page tunnel (S3-02) ---
        const tunA = 1 - tw(t, 56.35, 56.95, 'inOutQuad');
        if (tunA > 0 && t < 57) {
          const cz = lerp(-3, TUN_N * TUN_DZ - 4.5, ease.inOutQuad(prog(t, 55.25, 56.8)));
          for (let i = TUN_N - 1; i >= 0; i--) {
            const p = TUN[i], dz = p.z - cz;
            if (dz < 0.1 || dz > 30) continue;
            const sc = 1.1 / dz;
            const a = smooth(clamp((sc - 0.04) / 0.14)) * (1 - smooth(clamp((sc - 1.6) / 1.6))) * tunA;
            if (a <= 0.003) continue;
            ctx.setTransform(sc, 0, 0, sc, 960 + p.ox * sc, 540 + p.oy * sc);
            ctx.rotate(p.rot * Math.PI / 180);
            ctx.globalAlpha = a;
            ctx.drawImage(pageTex[p.tex], -PAGE_W / 2, -PAGE_H / 2);
            ctx.font = `400 9px ${MONO}`; ctx.fillStyle = 'rgba(161,157,155,.95)'; ctx.textBaseline = 'middle';
            const lbl = `— ${p.page} —`; ctx.fillText(lbl, -ctx.measureText(lbl).width / 2, PAGE_H / 2 - 18);
          }
          ctx.globalAlpha = 1;
        }
        // --- tender columns (S3-03 …) ---
        const colIn = tw(t, 56.35, 57.1, 'inOutQuad');
        const colA = colIn * (1 - 0.8 * tw(t, 61.0, 62.0, 'inOutQuad')) * (1 - 0.82 * inQ) * (1 - outQ);
        if (colA > 0.002) {
          const zoomIn = lerp(1.0, 1.0, 1);
          const vel = (POS(t + 1 / 60) - POS(t - 1 / 60)) * 30; // px/s
          const smear = Math.max(0, vel - 400) / 30 * 0.5; // px of travel during a half-frame shutter
          COLS.forEach((col, ci2) => {
            const ce = eff(c, col.pf);
            const s = ce.s * zoomIn;
            ctx.setTransform(s, 0, 0, s, 960 - ce.x * s, 540 - ce.y * s);
            const scroll = POS(t) * col.sp;
            const y0 = ce.y - 540 / s - 60, y1 = ce.y + 540 / s + 60;
            const a = col.alpha * colA * (col.id === 'focus' ? lerp(1, 0.55, tw(t, 61.0, 62.0)) : 1);
            if (col.id !== 'focus') {
              const at = atlases[ci2];
              const ay0 = Math.max(0, Math.floor(y0 - COL_TOP + scroll)), ay1 = Math.min(at.height, Math.ceil(y1 - COL_TOP + scroll));
              if (ay1 > ay0) {
                const reps = smear > 1 ? 8 : 1, sm = smear * col.sp;
                for (let r2 = 0; r2 < reps; r2++) {
                  ctx.globalAlpha = reps > 1 ? a * 1.5 / reps : a;
                  ctx.drawImage(at, 0, ay0, at.width, ay1 - ay0, col.x - 20, COL_TOP - scroll + ay0 - 10 + (reps > 1 ? (r2 / (reps - 1) - 0.5) * sm : 0), at.width, ay1 - ay0);
                }
              }
            } else {
              ctx.textBaseline = 'middle';
              const j0 = Math.max(0, Math.floor((y0 - COL_TOP + scroll) / col.lh)), j1 = Math.min(col.lines.length - 1, Math.ceil((y1 - COL_TOP + scroll) / col.lh));
              const reps = smear > 1 ? 8 : 1;
              for (let j = j0; j <= j1; j++) {
                const ln = col.lines[j];
                const y = COL_TOP + j * col.lh + col.lh / 2 - scroll;
                let la = a;
                if (ln.flight != null) {
                  const ft = FLIGHT_T[ln.flight];
                  if (t >= ft) continue;                       // lifted out: the DOM copy carries it
                  const hk = tw(t, ft - 0.35, ft, 'inOutQuad');
                  if (hk > 0) {
                    ctx.globalAlpha = colA * (0.7 + 0.3 * hk);
                    ctx.font = fontOf(col); ctx.fillStyle = `rgba(169,203,245,1)`;
                    ctx.fillText(ln.s, col.x, y);
                    continue;
                  }
                }
                for (let r2 = 0; r2 < reps; r2++) {
                  ctx.globalAlpha = reps > 1 ? la * 1.5 / reps : la;
                  drawLine(ctx, col, ln, col.x, y + (reps > 1 ? (r2 / (reps - 1) - 0.5) * smear : 0), 1);
                }
              }
            }
          });
          ctx.globalAlpha = 1;
        }
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        // depth of field: the text goes soft behind the quality shot
        const blurPx = 7 * inQ;
        css(cv, { filter: blurPx > 0.05 ? `blur(${blurPx.toFixed(2)}px)` : 'none' });

        // ======== reading thread ========
        const thrA = tw(t, 56.9, 57.2) * (1 - tw(t, 61.0, 61.8, 'inOutQuad'));
        const pts = thrA > 0.002 ? trailPts(t) : [];
        if (pts.length > 1) {
          const d = pathD(pts);
          attr(trail, { d, opacity: thrA }); attr(trailHalo, { d, opacity: 0.1 * thrA });
          const hp = pts[pts.length - 1];
          attr(trailHead, { cx: hp[0], cy: hp[1], opacity: t < 61.05 ? thrA : 0 });
          trailG.style.display = '';
        } else trailG.style.display = 'none';

        // ======== table ========
        const kMove = tw(t, 61.0, 61.9, 'inOutCubic');
        const tblX = lerp(1226, 548, kMove), tblW = lerp(596, 706, kMove), respW = lerp(0, 120, kMove);
        const tblA = tw(t, 57.0, 57.6, 'outQuad') * (1 - 0.93 * inQ) * (1 - outQ);
        css(tbl, { left: tblX, top: 296, width: tblW, opacity: tblA, display: tblA > 0.002 ? '' : 'none', filter: inQ > 0.01 ? `blur(${(6 * inQ).toFixed(2)}px)` : 'none' });
        const grid = `78px 1fr ${ZH ? 72 : 106}px ${respW.toFixed(1)}px`;
        css(thead, { gridTemplateColumns: grid, position: 'relative' });
        css(body, { position: 'relative' });
        css(tblBack, { opacity: kMove });
        css(TH[3], { opacity: tw(t, 61.4, 61.9) });
        const nRows = rowsAt(t);
        const nInt = Math.floor(nRows + 1e-6);
        const scrollRows = Math.max(0, nRows - WIN);
        const cnt = CUM[Math.min(TOTAL, nInt)];
        const numTxt = String(Math.min(TOTAL, nInt));
        if (capNum.textContent !== numTxt) { capNum.textContent = numTxt; capLbl.textContent = L('条要求', numTxt === '1' ? 'requirement' : 'requirements'); }
        const brk = `${CAT.tec} ${cnt.tec} · ${CAT.com} ${cnt.com} · ${CAT.eli} ${cnt.eli}`;
        if (capBrk.textContent !== brk) capBrk.textContent = brk;
        const rushV = (rowsAt(t + 0.02) - rowsAt(t - 0.02)) / 0.04; // rows per second
        css(body, { filter: rushV > 20 ? `blur(${Math.min(2.2, rushV / 120).toFixed(2)}px)` : 'none' });
        const base = Math.floor(scrollRows);
        SLOTS.forEach((sl, k) => {
          const idx = base + k;
          const vis = idx < TOTAL && rowVisible(idx, t);
          if (!vis) { if (sl.r.style.display !== 'none') sl.r.style.display = 'none'; return; }
          if (sl.r.style.display !== 'grid') sl.r.style.display = 'grid';
          const row = ROWS[idx];
          if (sl.idx !== idx) {
            sl.idx = idx;
            sl.cells[0].textContent = row.c; sl.cells[1].textContent = row.txt; sl.cells[2].textContent = CAT[row.cat];
            sl.st = null;
          }
          css(sl.r, { top: (idx - scrollRows) * 36, gridTemplateColumns: grid });
          // freshly landed flown rows glow briefly
          let glow = 0;
          if (idx < FLIGHT_T.length) glow = 1 - tw(t, FLIGHT_T[idx] + FL, FLIGHT_T[idx] + FL + 0.8, 'outQuad');
          // status column (S3-04)
          const fi = idx - (TOTAL - FINAL.length);
          let st = '';
          const fr = fi >= 0 ? FINAL[fi] : null;
          if (fr && fr.to) {
            const cn = CONNS.find((x) => x.ri === fi);
            if (t >= cn.t0 + CONN_DUR) st = fr.st;
          } else if (fr && fr.st === 'q' && t >= Q_CONN_T) st = 'run';
          if (st !== sl.st) {
            sl.st = st;
            const cell = sl.cells[3];
            cell.innerHTML = '';
            if (st === 'ok') { cell.append(span({ color: 'var(--d-success)' }, L('完全响应', 'Compliant'))); }
            else if (st === 'dev') { cell.append(span({ color: 'var(--d-warning)' }, L('△ 偏离', '△ Deviation'))); }
            else if (st === 'run') { cell.innerHTML = `<svg width="16" height="16" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" stroke="var(--d-muted)" stroke-opacity=".3" stroke-width="2" fill="none"/><path d="M10 2a8 8 0 0 1 8 8" stroke="var(--d-accent)" stroke-width="2" fill="none" stroke-linecap="round"/></svg>`; }
          }
          if (st === 'run') sl.cells[3].firstChild.style.transform = `rotate(${(t * 360) % 360}deg)`;
          const stK = fr && fr.to ? tw(t, CONNS.find((x) => x.ri === fi).t0 + CONN_DUR, CONNS.find((x) => x.ri === fi).t0 + CONN_DUR + 0.3) : 1;
          css(sl.cells[3], { opacity: stK });
          // the 4.3 row: its requirement leaves with line A
          const isQ = fr && fr.st === 'q';
          const qHi = isQ ? tw(t, Q_CONN_T, Q_CONN_T + 0.4) : 0;
          css(sl.r, { background: glow > 0.01 ? `rgba(75,143,227,${(0.16 * glow).toFixed(3)})` : (qHi > 0 ? `rgba(232,184,92,${(0.08 * qHi).toFixed(3)})` : 'transparent') });
          css(sl.cells[1], { opacity: isQ ? 1 - tw(t, Q0, Q0 + 0.25) : 1, color: 'var(--d-fg)' });
        });
        // the very first flown row appears exactly where its flight lands
        const slotTop = (idx) => 296 + 36 + 4 + (idx - scrollRows) * 36;

        // ======== flights ========
        flights.forEach((f, i) => {
          const ft = FLIGHT_T[i], k = prog(t, ft, ft + FL);
          if (t < ft || t >= ft + FL + 0.02) { if (f.el.style.display !== 'none') f.el.style.display = 'none'; return; }
          f.el.style.display = '';
          const j = FLOWN[i].line;
          const sx = focus.x, sy = COL_TOP + j * focus.lh + focus.lh / 2 - POS(ft) * focus.sp - 16;
          // the line keeps riding the scroll until it lets go
          const ride = POS(t) - POS(ft);
          const ex2 = tblX, ey = slotTop(i);
          const km = ease.inOutCubic(k);
          const lift = ease.outCubic(clamp(k / 0.25));
          const x = lerp(sx, ex2, km);
          const y = lerp(sy - ride * (1 - km) - 6 * lift, ey, km) - Math.sin(Math.PI * km) * 70;
          // turn: 0 → 90° (line), then −90° → 0 (row)
          const half = km < 0.5;
          const ang = half ? 90 * ease.inQuad(km / 0.5) : -90 * (1 - ease.outQuad((km - 0.5) / 0.5));
          css(f.a, { display: half ? '' : 'none', color: lift > 0 ? `rgba(247,245,243,${0.85 + 0.15 * lift})` : 'var(--d-fg)' });
          css(f.b, { display: half ? 'none' : '' });
          const z = Math.sin(Math.PI * km) * 120 + lift * 20;
          css(f.el, { transform: `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,${z.toFixed(1)}px) rotateY(${ang.toFixed(2)}deg)`, opacity: k > 0.985 ? 0 : 1 });
        });

        // ======== company documents (S3-04) ========
        const compK = tw(t, 61.2, 62.1, 'outCubic');
        const compA = compK * (1 - 0.93 * inQ) * (1 - outQ);
        css(comp, { display: compA > 0.002 ? '' : 'none', opacity: compA, transform: `translateX(${(1 - compK) * 320}px)`, filter: inQ > 0.01 ? `blur(${(6 * inQ).toFixed(2)}px)` : 'none' });
        compEls.forEach((ce2, n) => { const kk = tw(t, 61.35 + n * 0.035, 61.75 + n * 0.035, 'outCubic'); css(ce2.el, { opacity: kk, transform: `translateX(${(1 - kk) * 24}px)` }); });
        // ======== outline (S3-04, left) ========
        const outA = tw(t, 61.15, 61.5) * (1 - 0.93 * inQ) * (1 - outQ);
        css(outl, { display: outA > 0.002 ? '' : 'none', opacity: outA, filter: inQ > 0.01 ? `blur(${(6 * inQ).toFixed(2)}px)` : 'none' });
        let lastY = 184;
        OUT_ELS.forEach((e, i) => {
          const t0 = 61.45 + i * 0.205, kk = tw(t, t0, t0 + 0.35, 'outCubic');
          css(e, { opacity: kk, transform: `translateY(${(1 - kk) * 8}px)` });
          if (kk > 0) lastY = parseFloat(e.style.top) + 22 * kk;
        });
        css(outlRule, { height: Math.max(0, lastY - 184) });
        // ======== connections ========
        CONNS.forEach((cn) => {
          const k = tw(t, cn.t0, cn.t0 + CONN_DUR, 'inOutCubic');
          if (k <= 0 || compA < 0.01) { cn.p.style.display = 'none'; cn.dot.style.display = 'none'; return; }
          cn.p.style.display = ''; cn.dot.style.display = '';
          const fi = TOTAL - FINAL.length + cn.ri;
          const y0 = 296 + 36 + 4 + (fi - scrollRows) * 36 + 18;
          const x0 = tblX + tblW + 4;
          const it = ITEM_POS[cn.to[0]][cn.to[1]];
          const x1 = 1395 + 18 - 6 + (1 - compK) * 320, y1 = it.y;
          const d = `M${x0} ${y0} C${x0 + 70} ${y0} ${x1 - 70} ${y1} ${x1} ${y1}`;
          if (cn.d !== d) { cn.d = d; cn.p.setAttribute('d', d); cn.len = cn.p.getTotalLength(); cn.p.setAttribute('stroke-dasharray', cn.len); }
          cn.p.setAttribute('stroke-dashoffset', cn.len * (1 - k));
          cn.p.setAttribute('stroke', cn.dev && k >= 1 ? 'var(--d-warning)' : 'var(--d-accent)');
          const pt = cn.p.getPointAtLength(cn.len * k);
          attr(cn.dot, { cx: pt.x, cy: pt.y, r: k < 1 ? 3 : 2.5, fill: cn.dev && k >= 1 ? 'var(--d-warning)' : 'var(--d-accent)' });
          css(cn.p, { opacity: (0.75 * (1 - 0.96 * inQ) * (1 - outQ)).toFixed(3) });
          css(cn.dot, { opacity: ((1 - 0.96 * inQ) * (1 - outQ)).toFixed(3) });
        });

        // ======== quality shot (S3-05) ========
        // composition: first the pair alone, centred, each line held with a
        // little of its own page (65.0-68.2); after the snap the camera pulls
        // back and the frame opens to the right for the comment and the
        // question (68.2-69.8): evidence left, the decision right.
        const qOn = t >= Q0 - 0.05 && t < DOC0 + 1.4;
        qLayer.style.display = qOn ? '' : 'none';
        topL.style.display = qOn ? '' : 'none';
        if (qOn) {
          // measure once the lines have laid out
          if (!qa.m || !qa.m.w || !qFinal) {
            for (const q of [qa, qb]) q.m = { w: q.ln.offsetWidth, h: q.ln.offsetHeight, kx: q.key.offsetLeft + q.key.offsetWidth / 2, lw: q.ln.offsetTop, H: q.wrap.offsetHeight };
            qFinal = document.fonts.status === 'loaded';
            // phase 2 framing: pair's left edge and the cards' right edge get equal margins
            const wmax = Math.max(qa.m.w, qb.m.w), lx = PAIRX - wmax / 2, cardsW = 420 * QS;
            const room = 1920 - wmax - cardsW, gap = Math.min(140, Math.max(70, room * 0.3)), margin = (room - gap) / 2;
            CAM2.x = lx - margin + 960;            // camera x putting lx at screen x = margin (s = 1)
            CARD_X = lx + wmax + gap;
            camKeys.forEach((k) => { if (k.ph2) k.x = CAM2.x + k.dx; });
          }
          // one shared left edge (one excerpt rule); the group is centred on PAIRX
          const LX = PAIRX - Math.max(qa.m.w, qb.m.w) / 2, AY = PAIR_AY, BY = PAIR_AY + PAIR_GAP;
          const ax = LX, bx = LX, KA = LX + qa.m.kx, KB = LX + qb.m.kx;
          const qOut = tw(t, DOC0 - 0.3, DOC0 + 0.2, 'inOutQuad');
          // A rises out of the 4.3 row; B out of the text flow
          const qi = TOTAL - FINAL.length + Q_ROW;
          const aFrom = { x: tblX + 78 + 12, y: 296 + 36 + 4 + (qi - scrollRows) * 36 + 2, s: 15 / Q_FS };
          const bFrom = { x: focus.x + 30, y: 760, s: 18 / Q_FS };
          const ka = tw(t, Q0 + 0.05, Q0 + 1.15, 'inOutCubic'), kb = tw(t, Q0 + 0.3, Q0 + 1.4, 'inOutCubic');
          const recede = 1 - 0.06 * qOut;
          const place = (q, from, x, y, k, kl) => {
            const s = lerp(from.s, 1, k) * recede;
            const yy = lerp(from.y - q.m.lw * s, y - q.m.lw, k);
            css(q.wrap, { transform: `translate(${lerp(from.x, x, k).toFixed(1)}px,${yy.toFixed(1)}px) scale(${s.toFixed(4)})`, opacity: Math.min(1, k * 3) * (1 - qOut),
              filter: k < 0.6 ? `blur(${((1 - k / 0.6) * 2).toFixed(2)}px)` : qOut > 0.01 ? `blur(${(qOut * 3).toFixed(2)}px)` : 'none' });
            css(q.lab, { opacity: kl });
            css(q.ctx, { opacity: kl, transform: `translateY(${((1 - kl) * (q.below ? -8 : 8)).toFixed(1)}px)` });
            css(q.rule, { top: 4, height: q.m.H - 8, opacity: kl, transform: `scaleY(${ease.outCubic(kl).toFixed(3)})`, transformOrigin: q.below ? '50% 0' : '50% 100%' });
          };
          place(qa, aFrom, ax, AY, ka, tw(t, Q0 + 1.0, Q0 + 1.7, 'inOutQuad'));
          place(qb, bFrom, bx, BY, kb, tw(t, Q0 + 1.2, Q0 + 1.9, 'inOutQuad'));
          // underline the two numbers
          css(qa.ul, { transform: `scaleX(${tw(t, 65.8, 66.25, 'outCubic')})` });
          css(qb.ul, { transform: `scaleX(${tw(t, 65.9, 66.35, 'outCubic')})` });
          // the thread between them: drawn slack, then pulled taut at 68.0
          const pA = { x: KA, y: AY + qa.m.h + 8 }, pB = { x: KB, y: BY - 8 };
          const draw = tw(t, 66.0, 66.95, 'inOutQuad');
          let sag = lerp(70, 50, tw(t, 66.95, 67.85, 'inOutQuad'));
          if (t >= 67.85) { const u = t - 67.85; sag = u < 0.15 ? lerp(50, 0, ease.inCubic(u / 0.15)) : -11 * Math.exp(-(u - 0.15) * 7) * Math.sin((u - 0.15) * 34); }
          const mid = { x: (pA.x + pB.x) / 2 - sag * 2.0, y: (pA.y + pB.y) / 2 };
          const dq = `M${pA.x.toFixed(1)} ${pA.y.toFixed(1)} Q${mid.x.toFixed(1)} ${mid.y.toFixed(1)} ${pB.x.toFixed(1)} ${pB.y.toFixed(1)}`;
          attr(qPath, { d: dq }); attr(qHalo, { d: dq });
          const Lq = qPath.getTotalLength();
          for (const e of [qPath, qHalo]) attr(e, { 'stroke-dasharray': Lq, 'stroke-dashoffset': Lq * (1 - draw) });
          const hp = qPath.getPointAtLength(Lq * draw);
          const taut = tw(t, 67.95, 68.15);
          attr(qHead, { cx: hp.x, cy: hp.y, opacity: draw > 0 && draw < 1 ? 1 - qOut : 0 });
          attr(qEnd[0], { cx: pA.x, cy: pA.y, opacity: (draw > 0 ? 1 : 0) * (1 - qOut) });
          attr(qEnd[1], { cx: pB.x, cy: pB.y, opacity: (draw >= 1 ? 1 : 0) * (1 - qOut) });
          attr(qPath, { opacity: 1 - qOut, 'stroke-width': lerp(2.5, 2.2, taut) });
          attr(qHalo, { opacity: (0.1 + 0.1 * taut * Math.exp(-Math.max(0, t - 68.1) * 2.5)) * (1 - qOut) });
          // Word comment: highlight on line A, then the card
          const hk = tw(t, CMT - 0.3, CMT + 0.15, 'outCubic');
          css(qa.hl, { opacity: hk, transform: `scaleX(${hk})`, background: 'rgba(140,180,240,.22)' });
          const ck = tw(t, CMT, CMT + 0.55, 'outCubic');
          // the comment card later docks into the document's margin
          const dock = tw(t, DOC0 + 0.15, DOC0 + 1.05, 'inOutCubic');
          const from = scr(c, CARD_X + (1 - ck) * 24, CMT_Y), dockTo = docTarget(t);
          const ccx = lerp(from.x, dockTo.x, dock), ccy = lerp(from.y, dockTo.y, dock), ccs = lerp(CS * c.s, dockTo.s, dock);
          css(cmt, { transform: `translate(${ccx.toFixed(1)}px,${ccy.toFixed(1)}px) scale(${ccs.toFixed(4)})`, opacity: ck * (dock >= 1 ? 0 : 1) });
          // dashed link from the highlighted text to the card
          const ax2 = ax + qa.m.w + 10, ay2 = AY + qa.m.h / 2;
          const lk = tw(t, CMT + 0.1, CMT + 0.5) * (1 - qOut) * (1 - dock);
          attr(cLink, { d: `M${ax2.toFixed(1)} ${ay2.toFixed(1)} L${(ax2 + 36).toFixed(1)} ${ay2.toFixed(1)} L${CARD_X - 2} ${(CMT_Y + 34).toFixed(1)}`, opacity: 0.9 * lk });
          // QuestionCard
          const qk = tw(t, QC, QC + 0.6, 'outCubic');
          const qcOut = tw(t, DOC0, DOC0 + 0.35, 'inOutQuad');
          const qp = scr(c, CARD_X + (1 - qk) * 0, QC_Y + (1 - qk) * 30 + qcOut * 18);
          css(qc, { transform: `translate(${qp.x.toFixed(1)}px,${qp.y.toFixed(1)}px) scale(${(QS * c.s).toFixed(4)})`, opacity: qk * (1 - qcOut) });
          // the cursor comes to the edge of the card and does not click
          const curK = tw(t, 69.1, 69.9, 'inOutCubic');
          const cur0 = { x: 1900, y: 1010 }, cur1 = { x: CARD_X + 420 * QS + 16, y: QC_Y + 132 * QS };
          const cxp = lerp(cur0.x, cur1.x, curK) + Math.sin(curK * Math.PI) * 30, cyp = lerp(cur0.y, cur1.y, curK);
          const curA = tw(t, 69.05, 69.3) * (1 - qcOut);
          const cp = scr(c, cxp, cyp);
          css(cursor, { transform: `translate(${cp.x.toFixed(1)}px,${cp.y.toFixed(1)}px)`, opacity: curA, display: curA > 0.002 ? '' : 'none' });
        }

        // ======== tool row ========
        const toolA = tw(t, 57.9, 58.25, 'outCubic') * (1 - tw(t, 59.9, 60.3, 'inQuad'));
        css(tool, { display: toolA > 0.002 ? 'flex' : 'none', opacity: toolA, transform: `translateY(${(1 - toolA) * 10}px) scale(1.3)` });
        if (toolA > 0.002) {
          let html;
          if (t < 58.55) html = shimmer(`Searching PDF: ${q1}`, t);
          else if (t < 59.3) html = `Searched PDF: ${code(q1)}, ${shimmer(`reading ${pdfName}`, t)}`;
          else html = `Searched PDF: ${code(q1)}, read ${code(pdfName)}`;
          if (tool._h !== html) { tool._h = html; toolTxt.innerHTML = html; }
        }
        // ======== page ticker ========
        const tkA = tw(t, 56.8, 57.3) * (1 - tw(t, 61.1, 61.7));
        css(ticker, { display: tkA > 0.002 ? '' : 'none', opacity: tkA });
        if (tkA > 0.002) {
          const pg = pageAt(t), base2 = Math.floor(pg), fr = pg - base2;
          TICKS.forEach((e, k) => {
            const n = base2 + k - 5;
            const y = HEAD_Y + (k - 5 - fr) * 26;
            const d = Math.abs(k - 5 - fr);
            const txt = n >= 1 && n <= 203 ? String(n).padStart(3, '0') : '';
            if (e.textContent !== txt) e.textContent = txt;
            css(e, { top: y - 9, opacity: (Math.max(0, 1 - d / 5.5) * (d < 0.5 ? 1 : 0.45)).toFixed(3), color: d < 0.5 ? 'var(--d-fg)' : 'var(--d-muted)' });
          });
        }

        // ======== the document (S3-06) ========
        // each page is written top-down: a thin accent line sweeps the page
        // and the text settles in just behind it
        const docOn = t >= DOC0 - 0.2;
        if (docCam.view.style.visibility !== (docOn ? 'visible' : 'hidden')) docCam.view.style.visibility = docOn ? 'visible' : 'hidden';
        if (docOn) {
          const dc = K.camPath(docKeys, t);
          docCam.set(dc);
          const fold = tw(t, OUT0, OUT0 + 0.38, 'inOutCubic');         // pages slide onto page 12
          const shrink = tw(t, OUT0 + 0.22, 73.22, 'inOutCubic');       // and fly into the chip slot
          pages.forEach((p, i) => {
            const t0 = PAGE_T[i];
            const k = tw(t, t0, t0 + 0.75, 'outCubic');
            const wk = tw(t, t0 + 0.05, t0 + 0.05 + WIPE, 'inOutQuad');
            const x = lerp(PX[i], PX[1], fold);
            const op = (t >= t0 ? Math.min(1, (t - t0) / 0.08) : 0) * (i === 1 ? 1 : 1 - tw(t, OUT0 + 0.25, OUT0 + 0.45));
            const clip = wk >= 1 ? 'none' : `inset(-140px -140px ${((1 - wk) * PH).toFixed(1)}px -140px)`;
            css(p.pg, { left: x, opacity: op, clipPath: clip, transform: shrink > 0 && i === 1 ? 'none' : `translate3d(0,${((1 - k) * 34).toFixed(1)}px,${((1 - k) * -180).toFixed(1)}px) rotateX(${((1 - k) * 6).toFixed(2)}deg)` });
            css(p.wl, { top: (wk * PH - 1).toFixed(1), opacity: wk > 0 && wk < 1 ? Math.min(1, (1 - wk) * 8) : 0 });
            if (p.tops == null && p.pg.offsetHeight) p.tops = p.blocks.map((b2) => b2.offsetTop);
            p.blocks.forEach((b2, bi) => {
              const tb = t0 + 0.05 + WIPE * clamp(((p.tops ? p.tops[bi] : bi * 30) + 8) / PH) * 0.92;
              const kb = tw(t, tb, tb + 0.3, 'outCubic');
              css(b2, { opacity: kb, transform: `translateY(${((1 - kb) * 5).toFixed(1)}px)` });
            });
            if (p.rows) {
              const tt = t0 + 0.05 + WIPE * clamp((p.tops ? p.tops[p.blocks.indexOf(p.table)] : 300) / PH) * 0.92;
              p.rows.forEach((r, ri) => { const kr = tw(t, tt + 0.06 + ri * 0.035, tt + 0.3 + ri * 0.035); css(r, { opacity: kr }); });
            }
          });
          // the rest of the 86 pages fan out from behind page 49 once it is written
          stack.forEach((s2, k) => {
            const ks = tw(t, PAGE_T[2] + WIPE + 0.05 + (3 - k) * 0.06, PAGE_T[2] + WIPE + 0.5 + (3 - k) * 0.06, 'outCubic');
            const off = (4 - k) * 7 * ks;
            css(s2, { opacity: ks > 0 ? 1 - tw(t, OUT0 + 0.1, OUT0 + 0.3) : 0, left: lerp(PX[2] + off, PX[1], fold), top: PT + off });
          });
          const pc = Math.round(1 + 85 * ease.inOutQuad(prog(t, PAGE_T[0] + 0.1, 71.7)));
          const pcs = L(`${pc} 页`, `${pc} pages`);
          if (pcount.textContent !== pcs) pcount.textContent = pcs;
          css(pcount, { opacity: tw(t, PAGE_T[0] + 0.1, PAGE_T[0] + 0.5) * (1 - tw(t, OUT0, OUT0 + 0.2)) });
          css(flabel, { opacity: tw(t, PAGE_T[0], PAGE_T[0] + 0.5) * (1 - tw(t, OUT0, OUT0 + 0.2)) });
          // docked comment in page 49's margin
          const dk = tw(t, DOC0 + 0.9, DOC0 + 1.1);
          const dt2 = docTargetLocal();
          css(dcmt, { transform: `translate(${dt2.x}px,${dt2.y}px) scale(${dt2.s})`, opacity: dk * (1 - tw(t, OUT0, OUT0 + 0.2)) });
          // S3-07: page 12 becomes the chip
          const P2 = pages[1];
          const fp = flightPoint(dc, shrink, t);
          if (shrink > 0) {
            const cw = chip.offsetWidth * 1.2, ch = chip.offsetHeight * 1.2;
            const w = Math.exp(lerp(Math.log(PW), Math.log(cw / dc.s), shrink)), hh = Math.exp(lerp(Math.log(PH), Math.log(ch / dc.s), shrink));
            css(P2.pg, { left: fp.x - w / 2, top: fp.y - hh / 2, width: w, height: hh, borderRadius: lerp(2, hh / 2, smooth(clamp((shrink - 0.55) / 0.4))),
              background: shrink > 0.6 ? 'var(--card-bg)' : '#ffffff' });
            css(P2.inner, { transform: `scale(${(w / PW).toFixed(4)})`, opacity: 1 - tw(t, OUT0 + 0.3, OUT0 + 0.6) });
          } else {
            css(P2.pg, { top: PT, width: PW, height: PH, borderRadius: 2, background: '#ffffff' });
            css(P2.inner, { transform: 'none', opacity: 1 });
          }
          // the chip itself (screen space) takes over from the shrinking page
          const chipK = tw(t, 72.86, 73.1, 'inOutQuad');
          if (chipK > 0) {
            const sp = docToScreen(dc, fp.x, fp.y);
            css(chipWrap, { opacity: chipK, transform: `translate(${(sp.x - chip.offsetWidth / 2).toFixed(1)}px,${(sp.y - chip.offsetHeight / 2).toFixed(1)}px) scale(1.2)` });
            css(P2.pg, { opacity: 1 - chipK });
          } else css(chipWrap, { opacity: 0 });
        } else css(chipWrap, { opacity: 0 });

        grainU(t);
      };

      // ---------- coordinate helpers ----------
      // centre of the page-becoming-chip (doc coords). It lands 12 px low and
      // rises with the next surface's own chip (which eases up 10 px x 1.2 zoom
      // over 73.2-73.65), so the two coincide instead of doubling.
      function flightPoint(dc, ks, t) {
        const settle = 12 * (1 - ease.outCubic(prog(t, 73.2, 73.65)));
        const tg = toDoc(dc, K.CHIP_SLOT.x - 4, K.CHIP_SLOT.y + 1 + settle);
        const cx0 = PX[1] + PW / 2, cy0 = PT + PH / 2;
        return { x: lerp(cx0, tg.x, ks), y: lerp(cy0, tg.y, ks) - Math.sin(Math.PI * ks) * 40 };
      }
      function toDoc(dc, sx, sy) { return { x: (sx - 960) / dc.s + dc.x, y: (sy - 540) / dc.s + dc.y }; }
      function docToScreen(dc, x, y) { return { x: (x - dc.x) * dc.s + 960, y: (y - dc.y) * dc.s + 540 }; }
      // where the comment docks: right margin of page 49, beside the 4.3 heading (doc coords)
      function docTargetLocal() {
        const top = PT + (pages[2].h43 ? pages[2].h43.offsetTop : 120) - 8;
        return { x: PX[2] + PW + 18, y: top, s: 0.5 };
      }
      // the same point on screen (for the flight of the comment)
      function docTarget(t) {
        const dc = K.camPath(docKeys, t), loc = docTargetLocal();
        const sp = docToScreen(dc, loc.x, loc.y);
        return { x: sp.x, y: sp.y, s: loc.s * dc.s };
      }
      function scr(c, x, y) { return { x: (x - c.x) * c.s + 960, y: (y - c.y) * c.s + 540 }; }
    },
  });
})();
