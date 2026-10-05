"""The score, written against the cue sheet (script §5.2). D minor -> F major, 72 BPM.

Orchestration: felt piano (motif + harmony), warm pad and low strings (bed), cello / viola /
string-quartet lines, a very soft sub pulse, soft toms and finger percussion, risers
(filtered noise + string crescendo), glass crystallisation, one bass drum (110.0).

The "one sentence" motif is D-F-A-G with the G on the off-beat ("said, then half a sentence
more"). It is stated each time a deliverable surfaces, with one more voice each time:
  25.0 piano alone · 46.0 + viola · 69.5 + string quartet · 88.5 piano in octaves + strings ·
  100.4 piano alone, very slow · 106.0 tutti, in octaves.

The music is rendered in three independent "takes" separated by the two hard stops the cue
sheet asks for (100.0 vacuum, 113.6 scissor cut), so no reverb tail can leak across a cut.
"""
import numpy as np

import instruments as I
from dsp import (N, SR, add, convolve_stereo, db, envelope, fade, filt, highpass, make_ir, ms_width, n, peak, shelf)

BEAT = 60 / 72  # 0.8333 s
CUTS = [(100.0, 100.38), (113.6, 114.42)]  # (stop, restart) — hard music gates
TAKE_EDGES = [0.0, 100.0, 113.6, 120.0]


def take_of(t):
    return 0 if t < 100.0 - 1e-6 else (1 if t < 113.6 - 1e-6 else 2)


class Score:
    def __init__(self, seed=1):
        self.dry = [np.zeros((2, N), np.float32) for _ in range(3)]
        self.send = [np.zeros((2, N), np.float32) for _ in range(3)]
        self.r = np.random.default_rng(seed)
        t = np.arange(N) / SR
        self._t = t
        # bus automation (drops). 1 = normal.
        self.pad_auto = self._auto([
            (0, 0), (2.0, 0), (7.5, 1),
            (20.85, 1), (21.0, 0), (24.0, 0), (25.0, 1),
            (41.85, 1), (42.0, 0), (44.0, 0), (44.9, 1),
            (64.8, 1), (65.0, 0), (67.0, 0), (68.5, 0.55), (69.5, 1),
            (85.85, 1), (86.0, 0), (88.0, 0), (88.6, 1),
            (119.4, 1), (120.0, 0)])
        self.str_auto = self._auto([
            (0, 1), (20.9, 1), (21.0, 0), (24.0, 0), (24.6, 1),
            (41.9, 1), (42.0, 0), (44.0, 0), (44.6, 1),
            (64.85, 1), (65.0, 0), (67.95, 0), (68.0, 1),
            (85.9, 1), (86.0, 0), (88.4, 0), (88.5, 1),
            (119.4, 1), (120.0, 0)])

    def _auto(self, pts):
        return envelope(pts, N).astype(np.float32)

    def put(self, x, t, gain=1.0, send=0.3, auto=None, take=None):
        x = np.asarray(x, np.float32)
        k = take_of(t) if take is None else take
        if auto is not None:
            i = int(round(t * SR))
            a = auto[max(0, i):max(0, i) + x.shape[1]]
            x = x[:, :len(a)] * a[None, :]
        add(self.dry[k], x, t, gain)
        if send:
            add(self.send[k], x, t, gain * send)

    # ---- instruments
    def piano(self, t, note, vel, ring=3.0, gain=0.55, send=0.33, human=True):
        m = n(note) if isinstance(note, str) else note
        if human:
            t += self.r.uniform(-0.006, 0.006)
            vel = float(np.clip(vel + self.r.uniform(-0.025, 0.025), 0.05, 1))
        x = I.piano_bus_eq(I.piano(m, vel, ring, seed=int(self.r.integers(0, 3))))
        self.put(x, t, gain, send)

    def chord(self, t, notes, vel, ring=3.0, spread=0.012, **kw):
        for i, nt in enumerate(notes):
            self.piano(t + i * spread, nt, vel * (0.9 + 0.1 * (i == len(notes) - 1)), ring, **kw)

    def pad(self, t0, t1, notes, level=0.16, fi=1.2, fo=1.2, kind="pad", send=0.45, auto="pad", bright=1.0):
        dur = t1 - t0 + fo
        for i, nt in enumerate(notes):
            m = n(nt)
            env = [(0, 0), (fi, 1), (t1 - t0, 1), (dur, 0)]
            x = I.strings(m, dur, env, kind=kind, seed=int(self.r.integers(0, 1 << 20)), bright=bright)
            lv = level * (1.0 if m >= 40 else 0.8) * (0.8 if t0 < 103.0 else 1.0)  # the bed stays pp-mp until the climax
            self.put(x, t0, lv, send, self.pad_auto if auto == "pad" else self.str_auto)

    def bow(self, t0, t1, note, level, kind="vc", fi=0.6, fo=0.8, env=None, send=0.5, auto="str", bright=1.0, vib=1.0, trem=None):
        dur = t1 - t0 + fo
        env = env or [(0, 0), (fi, 1), (t1 - t0, 1), (dur, 0)]
        x = I.strings(n(note), dur, env, kind=kind, seed=int(self.r.integers(0, 1 << 20)), bright=bright, vib=vib, trem=trem)
        self.put(x, t0, level, send, self.str_auto if auto == "str" else (self.pad_auto if auto == "pad" else None))

    def sub(self, t, note, vel=0.5, gain=0.35):
        self.put(I.sub_pulse(float(440 * 2 ** ((n(note) - 69) / 12)), vel), t, gain, 0.0)

    def tom(self, t, f, vel, decay=0.7, gain=0.5, send=0.35, p=0.0):
        self.put(I.tom(f, vel, decay, seed=int(self.r.integers(0, 999)), p=p), t, gain, send)

    def finger(self, t, vel, p=0.0, tone=1.0, gain=0.22):
        self.put(I.finger(vel, seed=int(self.r.integers(0, 999)), p=p, tone=tone), t, gain, 0.25)

    def glass(self, t, note, vel, decay=1.6, p=0.0, gain=0.16, send=0.6):
        self.put(I.glass(n(note), vel, decay, seed=int(self.r.integers(0, 999)), p=p), t, gain, send)

    def riser(self, t0, t1, f_lo, f_hi, notes, level=0.22, noise=0.10, kinds=None, ring_mod=0.0):
        dur = t1 - t0
        x = I.riser(dur, f_lo, f_hi, seed=int(self.r.integers(0, 999)))
        self.put(x, t0, noise, 0.3)
        for i, nt in enumerate(notes):
            kind = (kinds or ["vc"] * len(notes))[i]
            env = [(0, 0), (dur * 0.35, 0.08), (dur * 0.8, 0.45), (dur - 0.03, 1.0), (dur, 0.0)]
            s = I.strings(n(nt), dur, env, kind=kind, seed=int(self.r.integers(0, 1 << 20)), bright=1.15,
                          trem=(np.linspace(6, 13, int(dur * SR)), 0.35))
            s = fade(s, 0.05, 0.03)
            if ring_mod:
                tt = np.arange(s.shape[1]) / SR
                rm = s * np.sin(2 * np.pi * 157 * tt) * ring_mod
                s = s + filt(rm, highpass(150))
            self.put(s, t0, level, 0.45)

    def pulse(self, t0, t1, step, roots, vel=0.5, accents=(1, .45, .7, .45, .85, .45, .7, .45), gain=0.33, soft=()):
        """Eighth-note sub pulse from t0 to t1. roots: [(t, note)] (changes at t). soft: [(a, b, factor)] --
        the pulse keeps going through a drop, but further away."""
        k = 0
        t = t0
        while t < t1 - 1e-6:
            root = [nt for (tt, nt) in roots if tt <= t + 1e-6][-1]
            f = next((g for a, b, g in soft if a - 1e-6 <= t < b), 1.0)
            self.sub(t, root, vel * f * accents[k % len(accents)], gain)
            k += 1
            t = t0 + k * step

    # ---- render
    def render(self, ir):
        out = np.zeros((2, N))
        for k in range(3):
            if not np.any(self.dry[k]):
                continue
            wet = convolve_stereo(self.send[k].astype(np.float64), ir)
            wet = ms_width(wet, 1.35)
            x = self.dry[k].astype(np.float64) + wet
            # gate to the take's own window: hard (6 ms) stop at its cut
            a, b = TAKE_EDGES[k], TAKE_EDGES[k + 1]
            g = np.zeros(N)
            ia = int(round((a - (0.02 if k else 0)) * SR)) if k else 0
            ib = int(round(b * SR))
            g[max(ia, 0):ib] = 1
            fl = int(0.006 * SR)
            if k < 2:
                g[ib - fl:ib] = np.cos(np.linspace(0, np.pi / 2, fl)) ** 2
            out += x * g
        return out


def motif(S, t0, beat, oct_notes=("D4", "F4", "A4", "G4"), vel=(0.5, 0.46, 0.55, 0.44), ring_to=None, gain=0.55, **kw):
    """D-F-A-G, G on the off-beat after A (beat index 3.5). Returns onset times."""
    times = [t0, t0 + beat, t0 + 2 * beat, t0 + 3.5 * beat]
    for i, (tt, nt) in enumerate(zip(times, oct_notes)):
        ring = (times[i + 1] - tt + 0.35) if i < 3 else ((ring_to - tt) if ring_to else 2.5)
        if i == 2:  # A is held through the rest
            ring = times[3] - tt + 0.6
        notes = nt if isinstance(nt, (list, tuple)) else [nt]
        for j, x in enumerate(notes):
            S.piano(tt + j * 0.004, x, vel[i] * (1 - 0.12 * j), ring, gain=gain, **kw)
    return times


def compose(events=None, lines=None):
    S = Score()
    ev = events or []
    # the on-screen narrative lines (content/vo.js; no narration) are musical moments: small gestures are
    # placed relative to their times so they follow the picture if the lines move
    LN = {ln["id"]: ln for ln in (lines or [])}

    def lt(lid, key, default):
        v = LN.get(lid, {}).get(key)
        return float(v) if v else default

    # ---------------- M0 0.0–8.0: single notes, pad very slowly in ----------------
    S.piano(0.6, "D3", 0.40, ring=7.5, human=False)
    S.piano(0.6, "D2", 0.18, ring=7.5, human=False)
    S.pad(2.0, 12.4, ["D2", "A2", "F3", "E4"], level=0.15, fi=4.0, fo=0.6)
    S.piano(5.0, "A3", 0.36, ring=6.0, human=False)
    # the two opening lines: the motif as an embryo, one note per breath (D 0.6 · F · A 5.0 · G off the beat)
    S.piano(lt("VO-01", "in", 1.6) + 1.15, "F3", 0.26, ring=5.0, human=False, send=0.4)
    S.piano(lt("VO-02", "at", 5.2) + 1.25, "G3", 0.24, ring=4.2, human=False, send=0.45)
    S.piano(lt("VO-02", "at", 5.2) + 1.27, "D4", 0.13, ring=4.2, human=False, send=0.45)
    # ---------------- M1-a 8.0–11.2: pad only (keys are the rhythm) ----------------
    # ---------------- M1-b 11.2–13.0: riser, lands on low D2 at 13.0 ----------------
    S.riser(11.2, 13.0, 140, 2400, ["D3", "A3", "D4"], level=0.24, noise=0.11, kinds=["vc", "va", "vn"])
    S.piano(13.0, "D2", 0.62, ring=4.0, human=False)
    S.piano(13.0, "D1", 0.45, ring=4.0, human=False)
    S.tom(13.0, 58, 0.45, decay=0.9, gain=0.35)
    # ---------------- M1-c 13.0–21.0: sub pulse, low strings D–Bb, piano hint at 17 ----------------
    S.pulse(13.0, 28.5, BEAT / 2, [(13.0, "D2"), (17.0, "Bb1"), (21.0, "D2"), (25.0, "D2"), (26.75, "Bb1")], vel=0.55,
            soft=[(21.0, 24.0, 0.55), (24.0, 25.0, 0.75)])
    S.bow(13.0, 17.1, "D2", 0.13, kind="cb", fi=1.2, fo=0.5)
    S.bow(13.0, 17.1, "D3", 0.10, kind="vc", fi=1.5, fo=0.5)
    S.bow(17.0, 21.0, "Bb1", 0.13, kind="cb", fi=0.6, fo=0.2)
    S.bow(17.0, 21.0, "F2", 0.08, kind="vc", fi=0.8, fo=0.2)
    S.bow(17.0, 21.0, "D3", 0.08, kind="vc", fi=0.9, fo=0.2)
    S.pad(13.0, 17.0, ["A2", "D3", "F3"], level=0.12, fi=1.0, fo=0.8)
    S.pad(17.0, 21.0, ["Bb2", "D3", "F3"], level=0.12, fi=0.8, fo=0.3)
    for t0 in (17.0, 18.67, 19.5):  # the motif's first two notes, as an inner voice
        S.piano(t0, "D4", 0.30, ring=0.95)
        S.piano(t0 + BEAT, "F4", 0.27, ring=1.2)
    # ---------------- M1-d 21.0–25.0: drop (pulse + room); 24.0 pad back ----------------
    drop_note(S, 21.0, "D5", "A4")
    S.pad(24.0, 25.4, ["Bb2", "D3", "F3", "C4"], level=0.12, fi=0.8, fo=0.6)
    # ---------------- M1-e 25.0–30.0: motif #1, piano alone; strings follow; 28.5 hit ----------------
    b1 = 3.5 / 4
    motif(S, 25.0, b1, ring_to=30.2)
    for tt, nt, v in ((25.0, "D2", .34), (25.0 + b1 / 2, "A2", .26), (25.0 + b1, "F3", .24),
                      (25.0 + 2 * b1, "Bb1", .32), (25.0 + 2.5 * b1, "F2", .25), (25.0 + 3 * b1, "D3", .22)):
        S.piano(tt, nt, v, ring=28.45 - tt)
    S.bow(25.5, 26.75, "A3", 0.07, kind="va", fi=0.9, fo=0.5)
    S.bow(25.5, 26.75, "F3", 0.07, kind="vc", fi=0.9, fo=0.5)
    S.bow(26.75, 28.5, "Bb3", 0.08, kind="va", fi=0.5, fo=0.3)
    S.bow(26.75, 28.5, "D3", 0.08, kind="vc", fi=0.5, fo=0.3)
    S.pad(25.0, 28.5, ["D3", "F3", "A3"], level=0.10, fi=0.8, fo=0.4)
    t3 = max(26.75, lt("VO-03", "in", 26.4))
    S.bow(t3, 28.5, "D5", 0.045, kind="vn", env=[(0, 0), (0.9, 0.55), (28.5 - t3, 1.0), (28.5 - t3 + 1.4, 0)],
          fo=1.4, bright=0.75, vib=0.8, auto=None, send=0.6)
    hit(S, 28.5, ["D1", "D2"], tomf=62, vel=0.62)
    S.bow(28.5, 30.0, "D3", 0.10, kind="vc", fi=0.08, fo=1.6)
    S.bow(28.5, 30.0, "A3", 0.08, kind="va", fi=0.08, fo=1.6)

    # ---------------- M2-a 30.0–33.0: pad, no pulse ----------------
    S.pad(29.6, 33.9, ["Bb1", "F2", "D3", "C4"], level=0.15, fi=1.4, fo=0.6)
    # ---------------- M2-b 33.0–34.5: shorter, a little higher riser ----------------
    S.riser(33.0, 34.5, 180, 3000, ["G3", "D4", "G4"], level=0.24, noise=0.11, kinds=["vc", "va", "vn"])
    S.piano(34.5, "G1", 0.55, ring=3.4, human=False)
    S.piano(34.5, "G2", 0.45, ring=3.4, human=False)
    S.tom(34.5, 66, 0.40, decay=0.8, gain=0.32)
    # ---------------- M2-c 34.5–42.0: pulse + cello second voice ----------------
    S.pulse(34.5, 42.0, BEAT / 2, [(34.5, "G1"), (37.83, "Bb1"), (39.5, "A1")], vel=0.55)
    S.pad(34.5, 37.9, ["G2", "D3", "Bb3", "A3"], level=0.11, fi=0.6, fo=0.6)
    S.pad(37.83, 42.0, ["Bb2", "D3", "F3", "C4"], level=0.11, fi=0.6, fo=0.3)
    cello = [(34.5, 36.2, "D3"), (36.17, 37.0, "C3"), (37.0, 37.85, "Bb2"), (37.83, 39.55, "D3"),
             (39.5, 40.4, "E3"), (40.33, 42.0, "C#3")]
    for a, b, nt in cello:
        S.bow(a, b, nt, 0.12, kind="vc", fi=0.25, fo=0.35)
    S.bow(34.5, 42.0, "G1", 0.08, kind="cb", fi=1.0, fo=0.2)
    # ---------------- M2-d 42.0–46.0: drop; 44.0 pad back ----------------
    drop_note(S, 42.0, "E5", "A4")
    S.pulse(42.0, 46.0, BEAT / 2, [(42.0, "A1")], vel=0.30, soft=[(44.0, 46.0, 1.3)])
    S.pad(44.0, 46.3, ["A2", "E3", "G3", "C#4"], level=0.11, fi=0.9, fo=0.5)
    # ---------------- M2-e 46.0–52.0: motif #2, piano + viola; 49.5 hit ----------------
    b2 = 3.5 / 4
    ts = motif(S, 46.0, b2, ring_to=51.5, vel=(0.52, 0.48, 0.58, 0.46))
    viola = [(46.0, ts[1], "A3"), (ts[1], ts[2], "C4"), (ts[2], ts[3], "D4"), (ts[3], 49.5, "Bb3")]
    for a, b, nt in viola:
        S.bow(a + 0.05, b + 0.05, nt, 0.10, kind="va", fi=0.3, fo=0.3)
    for tt, nt, v in ((46.0, "D2", .34), (46.0 + b2 / 2, "A2", .26), (46.0 + b2, "F3", .22),
                      (46.0 + 2 * b2, "Bb1", .32), (46.0 + 2.5 * b2, "F2", .25), (ts[3], "G2", .24)):
        S.piano(tt, nt, v, ring=49.45 - tt)
    S.pad(46.0, 49.5, ["D3", "F3", "A3"], level=0.10, fi=0.6, fo=0.4)
    S.bow(ts[2], 49.5, "F5", 0.042, kind="vn", env=[(0, 0), (0.7, 0.6), (49.5 - ts[2], 1.0), (49.5 - ts[2] + 1.6, 0)],
          fo=1.6, bright=0.75, vib=0.8, auto=None, send=0.6)
    S.bow(ts[2], 49.5, "D5", 0.032, kind="vn", env=[(0, 0), (0.9, 0.5), (49.5 - ts[2], 0.9), (49.5 - ts[2] + 1.6, 0)],
          fo=1.6, bright=0.7, vib=0.8, auto=None, send=0.6)
    hit(S, 49.5, ["Bb0", "Bb1"], tomf=56, vel=0.6)
    S.bow(49.5, 51.8, "D3", 0.10, kind="vc", fi=0.08, fo=1.2)
    S.bow(49.5, 51.8, "F3", 0.08, kind="va", fi=0.08, fo=1.2)
    S.bow(49.5, 51.8, "Bb2", 0.08, kind="vc", fi=0.08, fo=1.2)

    # ---------------- M3-a 52.0–55.2: pad ----------------
    S.pad(51.5, 56.0, ["F2", "C3", "A3", "G4"], level=0.15, fi=1.2, fo=0.6)
    # ---------------- M3-b 55.2–56.6: lowest riser ----------------
    S.riser(54.9, 56.6, 90, 1500, ["D2", "A2", "D3"], level=0.27, noise=0.12, kinds=["cb", "vc", "vc"])
    S.piano(56.6, "D1", 0.62, ring=4.0, human=False)
    S.piano(56.6, "D2", 0.50, ring=4.0, human=False)
    S.tom(56.6, 52, 0.48, decay=1.0, gain=0.35)
    # ---------------- M3-c 56.6–65.0: pulse + high A5 held; 61 low strings ----------------
    S.pulse(56.6, 65.0, BEAT / 2, [(56.6, "D2"), (61.0, "Bb1")], vel=0.5)
    S.bow(56.6, 65.0, "A5", 0.045, kind="vn", fi=2.5, fo=0.2, bright=0.7, vib=0.6)
    S.bow(56.6, 65.0, "A4", 0.040, kind="vn", fi=3.0, fo=0.2, bright=0.7, vib=0.6)
    S.pad(56.6, 61.0, ["D3", "F3", "A3"], level=0.10, fi=1.5, fo=0.6)
    S.pad(61.0, 65.0, ["D3", "F3", "Bb3"], level=0.10, fi=0.6, fo=0.3)
    S.bow(61.0, 65.0, "Bb1", 0.14, kind="cb", fi=1.0, fo=0.15)
    S.bow(61.0, 65.0, "F2", 0.10, kind="vc", fi=1.2, fo=0.15)
    S.bow(61.0, 65.0, "D3", 0.08, kind="vc", fi=1.4, fo=0.15)
    for tt in (59.0, 62.33):  # a patient inner voice on the piano
        S.piano(tt, "A4", 0.22, ring=1.6)
        S.piano(tt + BEAT * 2, "F4", 0.2, ring=1.6)
    # ---------------- M3-d 65.0–69.5: longest drop; 67 pad very soft; 68 low string ----------------
    drop_note(S, 65.0, "F5", None, vel=0.17)
    S.pad(67.0, 69.7, ["A2", "E3", "G3"], level=0.07, fi=1.2, fo=0.4)
    S.bow(68.0, 69.5, "A1", 0.16, kind="cb", fi=0.5, fo=0.25, env=[(0, 0), (0.35, 1), (1.5, 0.8), (1.75, 0)], auto=None)
    S.bow(68.0, 69.5, "A2", 0.10, kind="vc", fi=0.5, fo=0.25, env=[(0, 0), (0.4, 1), (1.5, 0.8), (1.75, 0)], auto=None)
    # ---------------- M3-e 69.5–74.0: motif #3, piano + string quartet; 72.5 hit ----------------
    b3 = 0.75
    ts = motif(S, 69.5, b3, ring_to=74.0, vel=(0.55, 0.5, 0.6, 0.48))
    quartet = {  # (vc, va, vn2, vn1)
        ts[0]: ("D3", "F3", "A3", "D5"),
        ts[2]: ("Bb2", "F3", "D4", "D5"),
        ts[3]: ("C3", "G3", "E4", "C5"),
    }
    keys = sorted(quartet)
    for i, k in enumerate(keys):
        end = keys[i + 1] if i + 1 < len(keys) else 72.5
        for kind, nt, lv in zip(("vc", "va", "vn", "vn"), quartet[k], (0.11, 0.08, 0.06, 0.045)):
            S.bow(k, end + 0.05, nt, lv, kind=kind, fi=0.25 if i else 0.5, fo=0.25, bright=0.9)
    for tt, nt, v in ((69.5, "D2", .34), (69.5 + b3, "A2", .25), (ts[2], "Bb1", .32), (ts[3], "C2", .30)):
        S.piano(tt, nt, v, ring=72.45 - tt)
    hit(S, 72.5, ["F1", "F2"], tomf=60, vel=0.62)
    for kind, nt, lv in zip(("vc", "va", "vn", "vn"), ("F2", "C4", "F4", "A4"), (0.11, 0.08, 0.06, 0.045)):
        S.bow(72.5, 74.2, nt, lv, kind=kind, fi=0.08, fo=1.2, bright=0.9)

    # ---------------- M4-a 74.0–77.4: pad ----------------
    S.pad(73.6, 78.1, ["D2", "A2", "F3", "E4"], level=0.14, fi=1.2, fo=0.5)
    # ---------------- M4-b 77.4–78.6: riser with a faint digital hum ----------------
    S.riser(77.4, 78.6, 200, 3200, ["D3", "A3", "E4"], level=0.23, noise=0.11, kinds=["vc", "va", "vn"], ring_mod=0.35)
    S.piano(78.6, "D2", 0.5, ring=0.3, human=False)
    S.tom(78.6, 64, 0.4, decay=0.6, gain=0.3)
    # ---------------- M4-c 78.6–82.5: sixteenth finger clicks, staccato piano bass ----------------
    st16 = BEAT / 4
    k = 0
    while 78.6 + k * st16 < 82.45:
        t = 78.6 + k * st16
        acc = 0.55 if k % 4 == 0 else (0.32 if k % 2 == 0 else 0.2)
        S.finger(t, acc, p=0.35 if k % 2 else -0.25, tone=1.0 if k % 4 else 0.85)
        k += 1
    S.pulse(78.6, 82.5, BEAT, [(78.6, "D2")], vel=0.5, accents=(1, .6, .8, .6))
    for i, nt in enumerate(["D2", "D2", "F2", "D2", "A1", "D2", "C2", "D2", "Bb1", "D2"]):
        tt = 78.6 + i * BEAT / 2 * (1 if i % 3 else 1)
        if tt < 82.4:
            S.piano(tt, nt, 0.36 if i % 2 == 0 else 0.28, ring=0.16)
    S.pad(78.6, 82.5, ["D3", "A3"], level=0.09, fi=0.8, fo=0.3)
    # ---------------- M4-d 82.5 thud D1 / 84.0 chime A5 / 86.0 drop / 88.5 pulse back ----------------
    S.piano(82.5, "D1", 0.7, ring=1.8, human=False)
    S.tom(82.5, 46, 0.6, decay=0.9, gain=0.4)
    S.pad(82.5, 86.0, ["D3", "F3", "A3"], level=0.09, fi=0.5, fo=0.2)
    S.bow(82.5, 86.0, "D2", 0.10, kind="cb", fi=0.4, fo=0.15)
    S.pulse(82.5, 86.0, BEAT / 2, [(82.5, "D2")], vel=0.4)
    S.glass(84.0, "A5", 0.55, decay=1.6)
    S.piano(84.0, "A5", 0.3, ring=2.0, human=False)
    S.piano(84.0, "D5", 0.18, ring=2.0)
    # ---------------- M4-e 88.5–94.0: motif #4, piano in octaves + strings; 92.5 hit ----------------
    b4 = 4.0 / 4.5
    ts = motif(S, 88.5, b4, oct_notes=(("D4", "D5"), ("F4", "F5"), ("A4", "A5"), ("G4", "G5")), ring_to=94.0,
               vel=(0.56, 0.52, 0.62, 0.5))
    S.pulse(88.5, 92.5, BEAT / 2, [(88.5, "D2"), (ts[2], "Bb1"), (ts[3], "C2")], vel=0.5)
    for (a, b, chord) in ((ts[0], ts[2], ("D2", "D3", "A3", "F4")), (ts[2], ts[3], ("Bb1", "D3", "F3", "D4")),
                          (ts[3], 92.5, ("C2", "C3", "G3", "E4"))):
        for kind, nt, lv in zip(("cb", "vc", "va", "vn"), chord, (0.11, 0.10, 0.08, 0.05)):
            S.bow(a, b + 0.05, nt, lv, kind=kind, fi=0.35 if a == ts[0] else 0.2, fo=0.25)
    for tt, nt, v in ((88.5, "D2", .36), (ts[2], "Bb1", .34), (ts[3], "C2", .32)):
        S.piano(tt, nt, v, ring=92.45 - tt)
    S.bow(ts[2], ts[3] + 0.05, "A5", 0.035, kind="vn", fi=0.4, fo=0.3, bright=0.75, vib=0.9, auto=None, send=0.6)
    S.bow(ts[3], 92.5, "G5", 0.038, kind="vn", env=[(0, 0), (0.3, 0.8), (92.5 - ts[3], 1.0), (92.5 - ts[3] + 1.5, 0)],
          fo=1.5, bright=0.75, vib=0.9, auto=None, send=0.6)
    hit(S, 92.5, ["D1", "D2"], tomf=58, vel=0.76)
    for kind, nt, lv in zip(("cb", "vc", "va", "vn"), ("D2", "A2", "F3", "D4"), (0.1, 0.1, 0.08, 0.05)):
        S.bow(92.5, 94.0, nt, lv, kind=kind, fi=0.06, fo=0.6)

    # ---------------- M5 94.0–100.0: four percussion hits, riser, full stop ----------------
    for tt, nts, f in ((94.0, ("D1", "D2"), 60), (95.5, ("Bb0", "Bb1"), 54), (97.0, ("C1", "C2"), 57), (98.5, ("A0", "A1"), 50)):
        S.tom(tt, f, 0.7, decay=0.75, gain=0.45)
        S.tom(tt + 0.0, f * 1.5, 0.35, decay=0.4, gain=0.14, p=0.3)
        for nt in nts:
            S.piano(tt, nt, 0.56, ring=1.4, human=False)
        S.sub(tt, nts[1], 0.7, gain=0.32)
    k = 0
    while 94.0 + k * 0.375 < 99.95:
        t = 94.0 + k * 0.375
        if k % 4:
            S.finger(t, 0.22 + 0.08 * (k % 2), p=(-0.4 if k % 2 else 0.4), tone=0.9 + 0.05 * (k % 3))
        k += 1
    S.bow(94.0, 98.5, "D2", 0.07, kind="cb", fi=0.4, fo=0.3)
    S.pad(94.0, 98.5, ["D3", "A3"], level=0.06, fi=0.4, fo=0.3)
    S.riser(98.5, 100.0, 160, 3600, ["A2", "E3", "A3", "C#4"], level=0.26, noise=0.12, kinds=["vc", "va", "va", "vn"])

    # ================= take 2: 100.4–113.6 =================
    # ---------------- M6-a 100.4–103.2: piano alone, the motif very slowly ----------------
    b6 = 0.9
    motif(S, 100.4, b6, ring_to=104.6, vel=(0.4, 0.36, 0.42, 0.34))
    S.piano(100.4, "D3", 0.24, ring=2.0)
    S.piano(100.4 + 2 * b6, "Bb2", 0.22, ring=1.6)
    # ---------------- M6-b 103.2–106.0: strings flood in (pp→mp), low ostinato, 105.2 glass ----------------
    for kind, nt, lv in zip(("cb", "vc", "vc", "va", "vn"), ("Bb1", "F2", "D3", "F3", "Bb4"), (0.13, 0.11, 0.09, 0.08, 0.04)):
        S.bow(103.2, 104.85, nt, lv, kind=kind, env=[(0, 0.02), (1.65, 0.6), (1.85, 0)], fo=0.2)
    for kind, nt, lv in zip(("cb", "vc", "vc", "va", "vn"), ("C2", "G2", "E3", "G3", "C5"), (0.13, 0.11, 0.09, 0.08, 0.045)):
        S.bow(104.8, 106.05, nt, lv, kind=kind, env=[(0, 0.5), (1.2, 1.0), (1.4, 0)], fo=0.2)
    # the violins rise out of the flood: D5 over Bb, E5 over C, falling back to D as the motif starts at 106
    S.bow(103.2, 104.85, "D5", 0.040, kind="vn", env=[(0, 0.0), (1.65, 0.75), (1.85, 0)], fo=0.2, bright=0.85, vib=0.9)
    S.bow(104.8, 106.05, "E5", 0.046, kind="vn", env=[(0, 0.6), (1.2, 1.0), (1.4, 0)], fo=0.2, bright=0.9, vib=1.0)
    ost = [104.2, 105.0, 105.8] + [106.6 + 0.8 * i for i in range(5)]
    ost_root = {104.2: "Bb1", 105.0: "C2", 105.8: "C2"}
    for t in ost:
        root = ost_root.get(t, "D2" if t < 107.6 else ("Bb1" if t < 108.8 else "C2"))
        S.piano(t, root, 0.42 if t >= 106 else 0.32, ring=0.38)
        S.sub(t, root, 0.7 if t >= 106 else 0.45)
    # crystallisation: if the animators register 'glass' events here, the SFX layer plays them
    # (same instrument, same scale); otherwise the score plays its own cascade at 105.2.
    if not any(e["name"] in ("glass", "crystal") and 103.2 <= e["t"] <= 106.5 for e in ev):
        gnotes = ["D6", "A5", "F6", "E6", "A6", "C6", "D6", "G6"]
        for i in range(8):
            S.glass(105.2 + i * 0.075 + 0.01 * (i % 3), gnotes[i], 0.5 - 0.03 * i, decay=1.3, p=-0.5 + i / 7, gain=0.14)
    # ---------------- M6-c 106.0–110.0: tutti, quarter ostinato, motif in octaves, Monday toms ----------------
    b7 = 0.8
    ts = motif(S, 106.0, b7, oct_notes=(("D3", "D4", "D5"), ("F3", "F4", "F5"), ("A3", "A4", "A5"), ("G3", "G4", "G5")),
               ring_to=110.0, vel=(0.66, 0.6, 0.7, 0.62), gain=0.6)
    for (a, b, chord) in ((106.0, ts[2], ("D2", "A2", "D3", "F3", "A3", "D4")), (ts[2], ts[3], ("Bb1", "F2", "D3", "F3", "Bb3", "D4")),
                          (ts[3], 110.0, ("C2", "G2", "E3", "G3", "C4", "E4"))):
        for kind, nt, lv in zip(("cb", "vc", "vc", "va", "va", "vn"), chord, (0.15, 0.12, 0.1, 0.09, 0.08, 0.06)):
            S.bow(a, b + 0.04, nt, lv, kind=kind, fi=0.12, fo=0.15, bright=1.1)
    # the strings sing the motif with the piano: cellos two octaves down, violins on top (octaves, tutti)
    for i, (a, b) in enumerate(zip(ts, list(ts[1:]) + [110.0])):
        for kind, nt, lv in (("vc", ("D3", "F3", "A3", "G3")[i], 0.085), ("vn", ("D5", "F5", "A5", "G5")[i], 0.045)):
            S.bow(a, b + 0.05, nt, lv, kind=kind, fi=0.08, fo=0.2, bright=1.05, vib=1.0)
    S.pad(106.0, 110.0, ["D3", "A3", "E4"], level=0.12, fi=0.3, fo=0.2)
    S.pulse(106.2, 110.0, 0.4, [(106.2, "D2"), (ts[2], "Bb1"), (ts[3], "C2")], vel=0.5)
    for t in (106.6, 107.4, 108.2, 109.0, 109.8):
        S.tom(t, 70, 0.5, decay=0.55, gain=0.38)
    S.tom(106.0, 60, 0.6, decay=0.8, gain=0.4)
    for i, t in enumerate(np.arange(106.2, 109.9, 0.4)):
        if i % 2:
            S.finger(t, 0.28, p=0.4 if i % 4 == 1 else -0.4, tone=0.9)
    # ---------------- M6-d 110.0 biggest hit, Dm → Bb, scissor cut 113.6 ----------------
    # back on the 72 BPM grid (the records stack on its beats). Over the hit the violins and cellos sing the
    # motif once more, augmented, in octaves: D 110.0 · F 110.83 · A 111.67 (over Bb: the maj7) · G 112.92,
    # off the beat, over Bb (6th) -- and the cut at 113.6 takes it away before it can resolve.
    bt = BEAT
    mel = [(110.0, "D"), (110.0 + bt, "F"), (110.0 + 2 * bt, "A"), (110.0 + 3.5 * bt, "G")]
    chg = 110.0 + 2 * bt
    S.put(I.bass_drum(41.2, 1.0, decay=1.5), 110.0, 0.55, 0.3)
    S.tom(110.0, 55, 0.8, decay=1.2, gain=0.4)
    S.chord(110.0, ["D1", "D2", "A2", "D3", "F3", "A3", "D4", "F4", "A4"], 0.74, ring=chg - 110.0, spread=0.006, gain=0.5)
    S.chord(chg, ["Bb0", "Bb1", "F2", "D3", "F3", "Bb3", "D4"], 0.58, ring=3.0, spread=0.01, gain=0.48)
    for (a, b, chord) in ((110.0, chg, ("D2", "A2", "D3", "F3", "A3", "D4")),
                          (chg, 113.65, ("Bb1", "F2", "D3", "F3", "Bb3", "D4"))):
        for kind, nt, lv in zip(("cb", "vc", "vc", "va", "va", "vn"), chord, (0.16, 0.12, 0.10, 0.09, 0.08, 0.055)):
            env = [(0, 0.0), (0.05, 1.0), (0.5, 0.8), (b - a, 0.85), (b - a + 0.2, 0)] if a == 110.0 else [(0, 0.3), (0.5, 0.85), (b - a, 1.0), (b - a + 0.2, 0)]
            S.bow(a, b, nt, lv, kind=kind, env=env, fo=0.2, bright=1.1)
    for i, (a, nt) in enumerate(mel):
        b = mel[i + 1][0] if i + 1 < len(mel) else 113.65
        sw = [(0, 0.0), (0.06 if i == 0 else 0.12, 0.85), (b - a, 1.0 if i < 3 else 1.15), (b - a + 0.15, 0)]
        S.bow(a, b + 0.04, nt + "5", 0.060, kind="vn", env=sw, fo=0.15, bright=1.15, vib=1.15)
        S.bow(a, b + 0.04, nt + "6", 0.022, kind="vn", env=sw, fo=0.15, bright=0.9, vib=1.15)
        S.bow(a, b + 0.04, nt + "4", 0.040, kind="va", env=sw, fo=0.15, bright=1.1, vib=1.1)
        S.bow(a, b + 0.04, nt + "3", 0.070, kind="vc", env=sw, fo=0.15, bright=1.1, vib=1.1)
        S.piano(a, nt + "5", 0.5, ring=(b - a) + 0.1, gain=0.5)
        S.piano(a + 0.004, nt + "4", 0.42, ring=(b - a) + 0.1, gain=0.5)
    S.pad(110.0, chg + 0.2, ["D3", "A3", "E4"], level=0.13, fi=0.05, fo=0.3)
    S.pad(chg, 113.7, ["D3", "F3", "C4"], level=0.12, fi=0.3, fo=0.1)
    S.pulse(110.0, 113.6, bt / 2, [(110.0, "D2"), (chg, "Bb1")], vel=0.55)

    # ================= take 3: 114.4–120 =================
    # ---------------- M7: 114.5 piano F4; 117.5 pad resolves to F major ----------------
    # The last line ("You only have to say what you want.") gets the motif's answer: F (114.5) · A · G off the
    # beat, and at 117.5 the G finally falls to F, in F major -- the sentence the cut left hanging is finished.
    S.piano(114.5, "F4", 0.40, ring=5.5, human=False, send=0.45)
    S.piano(114.5, "F3", 0.16, ring=5.5, human=False, send=0.45)
    t9 = lt("VO-09", "in", 114.9)
    S.piano(t9 + 1.0, "A4", 0.27, ring=1.5, human=False, send=0.5)
    S.piano(t9 + 1.0 + 1.5 * BEAT, "G4", 0.24, ring=117.5 - (t9 + 1.0 + 1.5 * BEAT) + 0.15, human=False, send=0.5)
    S.pad(114.6, 117.6, ["D3", "A3", "E4"], level=0.07, fi=1.4, fo=0.6)
    S.pad(117.5, 120.0, ["F2", "C3", "A3", "F4"], level=0.12, fi=0.9, fo=0.3)
    S.bow(117.5, 120.0, "C5", 0.022, kind="vn", fi=1.2, fo=0.3, bright=0.7, vib=0.7, auto=None, send=0.6)
    S.piano(117.5, "F2", 0.24, ring=2.5, human=False, send=0.45)
    S.piano(117.5, "C4", 0.20, ring=2.5, send=0.45)
    S.piano(117.51, "F4", 0.26, ring=2.5, human=False, send=0.45)
    S.piano(117.53, "A4", 0.22, ring=2.5, send=0.45)
    # the wordmark: one high F, very soft
    S.piano(lt("VO-10", "at", 118.4), "F5", 0.16, ring=1.6, human=False, send=0.6)
    return S


def drop_note(S, t, note, low=None, vel=0.2):
    """A quality-shot drop: as the bed falls away, one soft high piano note is left hanging in the room
    (heavy reverb send), with an optional quieter fifth/fourth below."""
    S.piano(t + 0.02, note, vel, ring=3.2, human=False, send=0.75, gain=0.5)
    if low:
        S.piano(t + 0.03, low, vel * 0.55, ring=3.2, human=False, send=0.75, gain=0.5)


def hit(S, t, notes, tomf=60, vel=0.6):
    for i, nt in enumerate(notes):
        S.piano(t, nt, vel * (1 - 0.15 * i), ring=1.6, human=False)
    S.tom(t, tomf, vel * 0.9, decay=0.9, gain=0.42)
    S.sub(t, notes[-1], 0.7, gain=0.35)


def render(events=None, lines=None):
    S = compose(events, lines)
    ir = make_ir(dur=3.6, t60_low=3.0, t60_high=1.1, predelay=0.025, seed=11, width=1.0, hp=90, lp=7000)
    x = S.render(ir)
    # master music colour: no harsh top, no rumble
    x = filt(x, highpass(28), shelf(7000, -3.0, True), peak(3200, -1.5, 1.0))
    # end: fade with the picture from 119.4 to 120.0
    t = np.arange(N) / SR
    x *= np.clip((120.0 - t) / 0.6, 0, 1) ** 1.5
    return x
