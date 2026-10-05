// The close (113.6–120.0).
//  113.6  the hard cut: on the dark depth only the blue line is left (s6 collapses into it).
//  S7-01  114.0–117.5  the surface comes back; the line falls into the empty input box and becomes
//                      its caret (lands on the 114.5 piano note), blinking. VO-09 subtitle 114.9–117.4.
//  S7-02  117.5–120.0  the coscribe wordmark (docs/index.html's brand: the page-with-caret mark and
//                      Source Serif 4 semibold) fades in 48 px below the box; fade to black from 119.4.
(function () {
  const { h, css, clamp, lerp, tw, ease, prog, T } = F;
  const T0 = 113.6, END = 120.0;
  const BACK0 = 114.0, BACK1 = 114.6;   // surface returns
  const LAND = 114.5;                   // the line becomes the caret
  const MARK = 117.5, URL = 118.1, FADE = 119.4;

  // the line on the dark (#4b8fe3) turns into the caret on the light surface (#2a78d6)
  const mix = (a, b, k) => {
    const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16)), pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
    return `rgb(${pa.map((v, i) => Math.round(lerp(v, pb[i], k))).join(',')})`;
  };

  F.scene({
    id: 's7', start: T0, end: END + 0.01, z: 30,
    build(layer) {
      K.depth(layer);
      const light = h('div', { class: 'abs' });
      css(light, { left: 0, top: 0, width: 1920, height: 1080 });
      layer.append(light);
      K.surface(light);
      const cam = K.camera(light);
      const box = K.inputBox(cam.world, { y: 540, placeholder: T('placeholder') });

      // the blue line (screen space)
      const line = h('div', { class: 'abs' });
      css(line, { left: 960 - 1.25, top: 0, width: 2.5, height: 1080, background: '#4b8fe3' });
      layer.append(line);

      // wordmark: the brand from docs/index.html (.brand): page-with-caret mark + "coscribe" in Source Serif 4 600
      const mark = h('div', { class: 'abs' });
      css(mark, { left: 0, width: 1920, top: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', pointerEvents: 'none' });
      const brand = h('div');
      css(brand, { display: 'flex', alignItems: 'center', gap: 12, color: 'var(--fg)' });
      const logo = h('svg', { width: 32, height: 32, viewBox: '0 0 24 24' });
      logo.innerHTML = '<path d="M6 3h8.5L19 7.5V21H6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>'
        + '<path d="M14 3v5h5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>'
        + '<path d="M9 12.5h7M9 16.5h3.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
        + '<rect x="13.6" y="14.6" width="1.6" height="3.8" rx=".4" fill="var(--accent)"/>';
      const logoCaret = logo.lastChild;
      const word = h('span', { text: 'coscribe' });
      css(word, { fontFamily: '"Source Serif 4", Georgia, serif', fontWeight: '600', fontSize: 38, letterSpacing: '-0.01em', lineHeight: '1' });
      brand.append(logo, word);
      const url = h('div', { text: 'oicws.github.io/coscribe' });
      css(url, { marginTop: 16, fontFamily: 'var(--mono)', fontSize: 15, letterSpacing: '.03em', color: 'var(--muted)' });
      mark.append(brand, url);
      layer.append(mark);

      const grainU = K.grain(layer, 0.035);
      const black = h('div', { class: 'abs' });
      css(black, { left: 0, top: 0, width: 1920, height: 1080, background: '#000', opacity: 0, pointerEvents: 'none' });
      layer.append(black);

      F.event(LAND, 'click', { soft: true, what: 'the line lands as the caret' });
      F.event(MARK, 'wordmark');
      F.event(FADE, 'fade', { dur: END - FADE });

      // caret position inside the empty box, in camera-world px (layout only, independent of the camera)
      let caret = null;
      function measure() {
        let e = box.text.children[1], x = 0, y = 0;
        while (e && e !== cam.world) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; }
        if (e !== cam.world) return null;
        let r = box.root, by = 0; while (r && r !== cam.world) { by += r.offsetTop; r = r.offsetParent; }
        return { x: x + 1, top: y, bot: y + 30, boxBottom: by + box.root.offsetHeight };
      }

      return (lt, t) => {
        if (!caret) caret = measure();
        const c = caret || { x: 543, top: 491, bot: 521, boxBottom: 610 };
        // camera: the surfaces' resting zoom, breathing in very slowly
        const s = lerp(1.2, 1.214, ease.inOutQuad(prog(t, BACK0, END)));
        cam.set({ x: 960, y: 540, s });
        const toScreen = (x, y) => [960 + (x - 960) * s, 540 + (y - 540) * s];

        // the surface comes back: it opens outward from the line, a soft-edged seam of light
        const kL = tw(t, BACK0, BACK1, 'inOutQuad');
        const kM = ease.inOutCubic(prog(t, BACK0, LAND));
        const [cx, ctop] = toScreen(c.x, c.top), [, cbot] = toScreen(c.x, c.bot);
        const lx = lerp(960, cx, kM);
        const r = 1500 * ease.inOutCubic(prog(t, BACK0, BACK1)), edge = 14;
        css(light, { display: kL > 0 ? '' : 'none' });
        if (kL > 0 && kL < 1) {
          const m = `linear-gradient(90deg, transparent ${(lx - r - edge).toFixed(1)}px, #000 ${(lx - r).toFixed(1)}px, #000 ${(lx + r).toFixed(1)}px, transparent ${(lx + r + edge).toFixed(1)}px)`;
          css(light, { webkitMaskImage: m, maskImage: m });
        } else if (light.style.maskImage !== 'none') css(light, { webkitMaskImage: 'none', maskImage: 'none' });
        const kB = tw(t, 114.15, 114.55, 'outQuad');
        css(box.root, { opacity: kB.toFixed(3), transform: `translateY(${((1 - kB) * 6).toFixed(2)}px)` });
        box.update({ str: '', k: 1, t, caretOn: t >= LAND });

        // the line shortens and glides into the caret
        if (t < LAND) {
          const x = lx, top = lerp(0, ctop, kM), bot = lerp(1080, cbot, kM), w = lerp(2.5, 2 * s, kM);
          css(line, { display: '', left: +(x - w / 2).toFixed(2), top: +top.toFixed(2), height: +(bot - top).toFixed(2), width: +w.toFixed(2), background: mix('#4b8fe3', '#2a78d6', kL) });
        } else if (line.style.display !== 'none') line.style.display = 'none';

        // wordmark, 48 px below the box; its caret blinks with the box's
        const [, bb] = toScreen(960, c.boxBottom);
        const kW = tw(t, MARK, MARK + 0.9, 'outCubic');
        css(mark, { top: +(bb + 48).toFixed(1), opacity: kW.toFixed(3), transform: `translateY(${((1 - kW) * 8).toFixed(2)}px)`, filter: kW < 0.99 ? `blur(${((1 - kW) * 4).toFixed(2)}px)` : 'none' });
        const kU = tw(t, URL, URL + 0.8, 'outQuad');
        css(url, { opacity: kU.toFixed(3) });
        logoCaret.setAttribute('opacity', Math.floor(t * 1.8) % 2 ? 0.15 : 1);

        css(black, { opacity: ease.inOutQuad(prog(t, FADE, END - 0.04)).toFixed(3) });
        grainU(t);
      };
    },
  });
})();
