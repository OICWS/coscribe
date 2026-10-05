// pipeline smoke test (replaced by the real opening)
F.scene({ id: 'test', start: 0, end: 6, build(layer, { T }) {
  K.surface(layer);
  const cam = K.camera(layer);
  const box = K.inputBox(cam.world, { y: 560, placeholder: '' });
  box.addChip('门店销售_2025.xlsx', 'sheet');
  const ln = K.line(layer, { y: 250, size: 56 });
  return (lt) => {
    cam.set(K.camPath([{ t: 0, s: 1 }, { t: 6, s: 1.3, y: 560 }], lt));
    box.update({ str: '找出过去 12 个月、40 家门店销售下滑的真正原因。', k: F.prog(lt, 1, 4), t: lt });
    ln.update('有些工作，一句话就能说清楚。', F.prog(lt, 0.2, 1.6), F.prog(lt, 5, 6));
  };
} });
