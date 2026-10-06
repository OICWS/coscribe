// The SURFACE scenes: the one coscribe input box where each sentence is typed,
// then the dive through a glyph (or chip) into that sentence's depth world.
// Depth scenes (s1..s4) sit underneath (lower z) and are revealed by the iris.
(function () {
  const { h, css, clamp, lerp, tw, ease, prog, T, L } = F;

  // chip positions inside the box (approx), exported for depth scenes that
  // fly their deliverable up into the next box: first chip's centre-left.
  // (screen coordinates while the surface camera sits at its resting 1.2 zoom)
  K.CHIP_SLOT = { x: 400, y: 541 };

  function sentenceScene(o) {
    // o: { id, start, end, fadeIn:[a,b] | null, fromBlack, key, type:[t0,dur], enter, dive:[a,b], target:{glyph:i}|{chip:i}|{text:'..'},
    //      chips:[{key|label, kind, at}], seed }
    F.scene({
      id: o.id, start: o.start, end: o.end, z: 100,
      build(layer, { T }) {
        const bg = K.surface(layer);
        const cam = K.camera(layer);
        const box = K.inputBox(cam.world, { y: o.greeting ? 600 : 540, placeholder: T('placeholder'), greeting: o.greeting || null });
        const chips = (o.chips || []).map((c) => {
          const el = box.addChip(c.label || T(c.key), c.kind);
          return { el, at: c.at };
        });
        const sentence = T(o.key);
        const keyTimes = K.typing(sentence, o.type[0], o.type[1], { seed: o.seed || 3 });
        F.event(o.enter, 'enter');
        if (o.dive) F.event(o.dive[0], 'dive', { dur: o.dive[1] - o.dive[0] });
        const grainU = K.grain(layer, 0.035);
        const black = h('div', { class: 'abs' }); css(black, { inset: 0, background: '#0b0a0a', pointerEvents: 'none' }); layer.append(black);

        // where to dive: measure the target in world coordinates once text is laid out
        function targetPoint() {
          let rect;
          if (o.target.chip != null) rect = chips[o.target.chip].el.getBoundingClientRect();
          else {
            const node = box.text.firstChild.firstChild; // words span's text node
            const chars = Array.from(sentence);
            let idx = o.target.glyph != null ? o.target.glyph : sentence.indexOf(o.target.text);
            if (o.target.text && idx < 0) idx = chars.length - 1;
            if (idx < 0) idx += chars.length;
            const off = Array.from(sentence).slice(0, idx).join('').length;
            const len = (o.target.text && sentence.includes(o.target.text)) ? o.target.text.length : chars[idx].length;
            const rg = document.createRange();
            rg.setStart(node, off); rg.setEnd(node, Math.min(node.length, off + len));
            rect = rg.getBoundingClientRect();
          }
          const st = layer.getBoundingClientRect(), sc = st.width / 1920;
          // screen (stage px) -> world, given the camera at the moment of measuring
          const sx = (rect.left + rect.width / 2 - st.left) / sc, sy = (rect.top + rect.height / 2 - st.top) / sc;
          const c = cur;
          return { x: (sx - 960) / c.s + c.x, y: (sy - 540) / c.s + c.y };
        }
        let cur = { x: 960, y: 540, s: 1 }, tp = null;

        return (lt, t) => {
          // typing
          const n = K.typedCount(keyTimes, t);
          const str = Array.from(sentence).slice(0, n).join('');
          const sent = prog(t, o.enter, o.enter + 0.35);
          box.update({ str, k: 1, t, sent: sent > 0 && sent < 1 ? sent : 0, caretOn: t < o.enter + 0.1 || !o.dive, elapsed: t >= o.enter ? t - o.enter : null });
          chips.forEach(({ el, at }) => {
            const k = tw(t, at, at + 0.45, 'outCubic');
            css(el, { opacity: k, transform: `translateY(${(1 - k) * 10}px) scale(${0.96 + 0.04 * k})` });
          });

          // camera: a slow breathing push, then the dive
          let c = { x: 960, y: 540, s: lerp(1.2, 1.23, prog(t, o.start, o.dive ? o.dive[0] : o.end)) };
          let hole = 0, hx = 960, hy = 540;
          if (o.dive && t >= o.dive[0]) {
            if (!tp) { cur = c; K.cam = cam; cam.set(c); tp = targetPoint(); }
            const k = prog(t, o.dive[0], o.dive[1]);
            const kz = ease.inCubic(k);
            c = { x: lerp(c.x, tp.x, ease.inOutCubic(Math.min(1, k * 1.6))), y: lerp(c.y, tp.y, ease.inOutCubic(Math.min(1, k * 1.6))), s: Math.exp(lerp(Math.log(c.s), Math.log(14), kz)) };
            hx = (tp.x - c.x) * c.s + 960; hy = (tp.y - c.y) * c.s + 540;
            hole = ease.inQuad(prog(k, 0.35, 1)) * 1500;
          } else tp = null;
          cur = c; cam.set(c);
          if (hole > 0) {
            const m = `radial-gradient(circle at ${hx}px ${hy}px, transparent ${hole}px, #000 ${hole + 1.5}px)`;
            css(layer, { webkitMaskImage: m, maskImage: m });
          } else if (layer.style.maskImage) css(layer, { webkitMaskImage: 'none', maskImage: 'none' });

          // fade in (from the depth below, or from black at the film start)
          const fin = o.fadeIn ? tw(t, o.fadeIn[0], o.fadeIn[1], 'inOutQuad') : 1;
          if (o.fromBlack) { css(black, { opacity: 1 - fin, display: '' }); css(layer, { opacity: 1 }); }
          else { css(black, { display: 'none' }); css(layer, { opacity: fin }); }
          grainU(t);
        };
      },
    });
  }
  K.sentenceScene = sentenceScene;

  // Sentence 1 (also the film's opening: empty box, then typing at 8.0)
  sentenceScene({ id: 'surf1', start: 0, end: 13.0, fromBlack: true, greeting: 'Good morning!', fadeIn: [0.6, 2.0], key: 's1', type: [8.0, 3.0], enter: 11.1,
    dive: [11.2, 13.0], target: { text: L('。', 'months.') }, chips: [{ key: 's1_chip', kind: 'sheet', at: 9.2 }], seed: 11 });
  // Sentence 2
  sentenceScene({ id: 'surf2', start: 28.5, end: 34.5, fadeIn: [28.5, 29.5], key: 's2', type: [30.0, 2.8], enter: 32.9,
    dive: [33.0, 34.5], target: { text: L('讲', 'briefing') }, chips: [{ key: 's1_out', kind: 'sheet', at: 29.2 }, { key: 's2_chip', kind: 'slides', at: 30.6 }], seed: 22 });
  // Sentence 3
  sentenceScene({ id: 'surf3', start: 49.5, end: 56.6, fadeIn: [49.5, 50.5], key: 's3', type: [52.0, 3.0], enter: 55.1,
    dive: [55.2, 56.6], target: { chip: 1 }, chips: [{ key: 's2_out', kind: 'slides', at: 50.2 }, { key: 's3_chip', kind: 'pdf', at: 50.9 }, { key: 's3_chip2', kind: 'folder', at: 52.3 }], seed: 33 });
  // Sentence 4
  sentenceScene({ id: 'surf4', start: 72.5, end: 78.6, fadeIn: [72.5, 73.5], key: 's4', type: [74.0, 3.2], enter: 77.3,
    dive: [77.4, 78.6], target: { text: L('异常', 'odd') }, chips: [{ key: 's3_out', kind: 'doc', at: 73.2 },
      ...[0, 1, 2].map((i) => ({ label: F.T('s4_chips')[i], kind: 'folder', at: 74.2 + i * 0.25 }))], seed: 44 });
})();
