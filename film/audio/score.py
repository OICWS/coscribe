"""The score (v3): one bright D-major track at a steady 120 BPM for the whole film.

Every picture hit sits on the beat grid, so the music never changes tempo, never breaks down and never
stops dead: the energy only steps up, sentence by sentence (soft pulse -> kick and claps -> hats ->
four on the floor), eases for three seconds at 100 s while the harmony carries on, builds into the peak
at 110 s and resolves on D at 117.5 s. The hook (F#5 E5 D5 E5 A4 | D5 F#5 A5) returns once per
sentence, gaining a layer each time (pluck, then glockenspiel, then piano octaves).
"""
import numpy as np
from scipy import signal

import instruments as I
from dsp import (N, SR, add, convolve_stereo, db, envelope, fade, filt, highpass, lowpass, make_ir, ms_width,
                 peak, shelf)

# ---------------------------------------------------------------- tempo map
# (t0, t1, beats, what)
BPM = 120.0
BT = 60.0 / BPM
# One tempo for the whole film. At 120 BPM every picture hit (13.0, 28.5, 34.5, 49.5, 72.5, 92.5, the
# montage cuts 94.0/95.5/97.0/98.5, 110.0, 117.5) already sits on a beat; 56.6 and 78.6 land 0.1 s
# after the downbeat at 56.5 / 78.5, which reads as the picture answering the music.
# (t0, t1, beats, what) -- energy only ever steps up until the build; no breakdowns, no dead stops.
_S = [(0.0, 13.0, "intro: piano, soft pulse from 4 s, riser into 13"), (13.0, 28.5, "groove 1"),
      (28.5, 34.5, "groove 1, lighter (typing 2)"), (34.5, 56.5, "groove 2"), (56.5, 78.5, "groove 3"),
      (78.5, 100.0, "groove 4 + montage"), (100.0, 103.0, "breath: drums out, harmony stays"),
      (103.0, 110.0, "build"), (110.0, 113.5, "peak"), (113.5, 117.5, "outro"), (117.5, 120.0, "resolution")]
SEGS = [(t0, t1, (t1 - t0) / BT, w) for t0, t1, w in _S]
TAKES = [(0.0, 120.0)]


class Seg:
    def __init__(self, i):
        self.t0, self.t1, self.beats, self.what = SEGS[i]
        self.bt = (self.t1 - self.t0) / self.beats

    def __call__(self, b):
        return self.t0 + b * self.bt


def beat_grid():
    """Every beat time of the map (for the report / alignment checks)."""
    out = []
    for i in range(len(SEGS)):
        s = Seg(i)
        out += [s(b) for b in np.arange(0, s.beats + 1e-9, 1.0)]
    return sorted(set(round(x, 4) for x in out))


def take_of(t):
    for k, (a, b) in enumerate(TAKES):
        if t < b - 1e-6:
            return k
    return len(TAKES) - 1


# ---------------------------------------------------------------- harmony
PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
QUAL = {"": [0, 4, 7], "m": [0, 3, 7], "sus": [0, 5, 7], "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10],
        "add9": [0, 4, 7, 14], "sus2": [0, 2, 7]}


def chord(sym):
    """'D', 'Bm7', 'A/C#', 'Gmaj7', 'Asus' -> (bass pitch class, [pitch classes])."""
    base, _, bass = sym.partition("/")
    root = PCS[base[0]] + (1 if base[1:2] == "#" else 0)
    q = base[2:] if base[1:2] == "#" else base[1:]
    pcs = [(root + i) % 12 for i in QUAL[q]]
    bp = root if not bass else (PCS[bass[0]] + (1 if bass[1:2] == "#" else 0)) % 12
    return bp, pcs


def voicing(sym, lo, hi):
    _, pcs = chord(sym)
    return [m for m in range(lo, hi + 1) if m % 12 in pcs]


def bass_midi(sym, lo=33):
    bp, _ = chord(sym)
    m = lo + ((bp - lo) % 12)
    return m


def at(changes, b):
    """Chord at beat b from [(beat, sym), ...]."""
    cur = changes[0][1]
    for bb, s in changes:
        if bb <= b + 1e-6:
            cur = s
    return cur


def nm(name):
    from dsp import n
    return n(name)


# ---------------------------------------------------------------- score / buses
BUSES = ["piano", "pianoverb", "pluck", "pad", "bass", "kick", "drums", "perc", "fx", "bell"]
GROOVE = ["pluck", "pad", "bass", "kick", "drums"]       # these follow the breakdown / build filter
HALL_SEND = {"piano": 0.22, "pianoverb": 0.45, "pluck": 0.25, "pad": 0.3, "bass": 0.0, "kick": 0.0, "drums": 0.06,
             "perc": 0.35, "fx": 0.25, "bell": 0.4}
ROOM_SEND = {"piano": 0.08, "pianoverb": 0.0, "pluck": 0.0, "pad": 0.0, "bass": 0.0, "kick": 0.05, "drums": 0.22,
             "perc": 0.15, "fx": 0.05, "bell": 0.0}


class Score:
    def __init__(self, seed=3):
        self.bus = {b: np.zeros((2, N), np.float32) for b in BUSES}
        self.r = np.random.default_rng(seed)
        self.kicks = []          # (t, depth) for the side-chain pump
        self.cut = [(0.0, 20000.0)]  # groove low-pass automation points (t, Hz)
        self.bright = 1.7            # bright grand, not felt

    # ---- placement: never ring over the take's end (6 ms fade there)
    def put(self, bus, x, t, gain=1.0, p=None):
        x = np.asarray(x, np.float64)
        if x.ndim == 1:
            x = I.pan(x, p or 0.0) if p is not None else np.stack([x, x])
        k = take_of(t)
        end = TAKES[k][1]
        L = int(round((end - t) * SR))
        if L <= 0:
            return
        if x.shape[1] > L:
            x = x[:, :L].copy()
            fl = min(int(0.006 * SR), L)
            x[:, L - fl:] *= np.cos(np.linspace(0, np.pi / 2, fl)) ** 2
        add(self.bus[bus], x.astype(np.float32), t, gain)

    # ---- instruments
    def piano(self, t, m, vel, ring=0.5, gain=0.5, bus="piano", human=True):
        if isinstance(m, str):
            m = nm(m)
        if human:
            t += self.r.uniform(-0.004, 0.004)
            vel += self.r.uniform(-0.02, 0.02)
        vel = float(np.clip(round(vel * 50) / 50, 0.05, 1.0))
        ring = max(0.1, round(ring * 20) / 20)
        x = I.piano(int(m), vel, ring, seed=int(self.r.integers(0, 3)), bright=self.bright)
        self.put(bus, x, t, gain)

    def stab(self, t, notes, vel, ring=0.5, gain=0.5, bus="piano", spread=0.004):
        for i, m in enumerate(notes):
            self.piano(t + i * spread, m, vel * (0.85 + 0.15 * (i == len(notes) - 1)), ring, gain, bus)

    def pluck(self, t, m, dur, vel, p=0.0, bright=1.0, gain=0.3):
        gain *= 1.35
        x = I.pluck(int(m), round(dur, 2), round(bright, 2), seed=int(self.r.integers(0, 2)))
        self.put("pluck", x * vel, t, gain, p)

    def glock(self, t, m, vel, decay=1.1, p=0.15, gain=0.16):
        self.put("bell", I.glock(int(m), decay) * vel, t, gain, p)

    def pad(self, t0, t1, sym, level=0.12, fi=0.3, fo=0.4, lo=50, hi=74, bright=1.0):
        dur = t1 - t0 + fo
        env = [(0, 0), (fi, 1), (t1 - t0, 1), (dur, 0)]
        notes = voicing(sym, lo, hi)
        for m in notes:
            x = I.synth_pad(m, dur, env, bright=bright, seed=int(self.r.integers(0, 1 << 16)))
            self.put("pad", x, t0, 1.5 * level / np.sqrt(len(notes)))

    def bass(self, t, m, dur, vel=1.0, gain=0.42, bright=1.0):
        self.put("bass", I.bass_note(int(m), round(dur, 2), bright) * vel, t, gain)

    def kick(self, t, vel=1.0, gain=0.55, pump=1.0, variant=0):
        self.put("kick", I.kick(variant) * vel, t, gain)
        if pump:
            self.kicks.append((t, pump * vel))

    def clap(self, t, vel=1.0, gain=0.2, bus="drums"):
        gain *= 1.3
        self.put(bus, I.clap(int(self.r.integers(0, 4))) * vel, t, gain)

    def snare(self, t, vel=1.0, gain=0.16, bus="drums", p=0.0):
        self.put(bus, I.snare(int(self.r.integers(0, 4))) * vel, t, gain, p)

    def shaker(self, t, vel=1.0, gain=0.10, p=0.25, accent=False):
        self.put("drums", I.shaker(int(self.r.integers(0, 6)), accent) * vel, t, gain, p)

    def hat(self, t, vel=1.0, open_=False, gain=0.06, p=-0.3):
        gain *= 1.3
        self.put("drums", I.hat(open_, int(self.r.integers(0, 3))) * vel, t, gain, p)

    def crash(self, t, vel=1.0, gain=0.10, dur=2.4):
        self.put("fx", I.crash(dur, int(self.r.integers(0, 3))) * vel, t, gain)

    def boom(self, t, vel=1.0, gain=0.30, f=41.2):
        self.put("fx", I.boom(f) * vel, t, gain)

    def sweep(self, t0, t1, f0, f1, gain=0.12):
        x = I.sweep(t1 - t0, f0, f1, seed=int(self.r.integers(0, 999)))
        self.put("fx", x, t0, gain)

    def filt_pts(self, pts):
        self.cut += list(pts)


# ---------------------------------------------------------------- phrases
HOOK = [(0.0, "F#5", 0.75), (0.75, "E5", 0.75), (1.5, "D5", 0.5), (2.0, "E5", 1.0), (3.0, "A4", 1.0),
        (4.0, "D5", 0.75), (4.75, "F#5", 0.75), (5.5, "A5", 1.0)]
HOOK_SHORT = [(0.0, "F#5", 0.75), (0.75, "E5", 0.75), (1.5, "D5", 0.5), (2.0, "E5", 1.0), (3.0, "A4", 0.5),
              (3.5, "D5", 0.5), (4.0, "F#5", 0.5), (4.5, "A5", 1.0)]
HOOK_PEAK = [(0.0, "F#5", 0.75), (0.75, "E5", 0.75), (1.5, "D5", 0.5), (2.0, "E5", 1.0), (3.0, "A5", 1.0),
             (4.0, "B5", 0.75), (4.75, "A5", 0.75), (5.5, "F#5", 0.5), (6.0, "A5", 0.5)]


def ostinato(S, g, b0, b1, changes, vel=0.45, lh=True, lh_vel=0.4, gain=0.42, lo=64, hi=81, accent=0.06,
             pattern=(0, 2, 1, 2, 3, 2, 1, 2), cresc=0.0):
    """Piano 8th-note ostinato (right hand) over the chord changes; left hand root (+fifth) on the bar."""
    b = b0
    k = 0
    while b < b1 - 1e-6:
        sym = at(changes, b)
        v = voicing(sym, lo, hi)
        m = v[pattern[k % len(pattern)] % len(v)]
        ve = vel + (accent if k % 2 == 0 else -accent * 0.5) + cresc * (b - b0) / max(b1 - b0, 1e-9)
        S.piano(g(b), m, ve, ring=g(b + 0.5) - g(b) + 0.05, gain=gain)
        if lh and (abs(b - round(b)) < 1e-6) and (int(round(b - b0)) % 4 == 0 or any(abs(bb - b) < 1e-6 for bb, _ in changes)):
            r = bass_midi(sym, 38)
            nxt = min([bb for bb, _ in changes if bb > b + 1e-6] + [b1, b + 4])
            S.piano(g(b), r, lh_vel, ring=g(nxt) - g(b) + 0.05, gain=gain)
            S.piano(g(b) + 0.003, r + 7, lh_vel * 0.75, ring=g(nxt) - g(b) + 0.05, gain=gain)
        b += 0.5
        k += 1


def arp(S, g, b0, b1, changes, vel=0.8, lo=62, hi=86, step=0.25, dur=0.16, bright=1.0, gain=0.3,
        pattern=(0, 1, 2, 3, 4, 3, 2, 1), cresc=0.0, width=0.55):
    """Plucked 16th arpeggio, ping-pong panned."""
    b = b0
    k = 0
    while b < b1 - 1e-6:
        sym = at(changes, b)
        v = voicing(sym, lo, hi)
        m = v[pattern[k % len(pattern)] % len(v)]
        acc = 1.0 if k % 4 == 0 else (0.8 if k % 2 == 0 else 0.68)
        c = 1 + cresc * (b - b0) / max(b1 - b0, 1e-9)
        S.pluck(g(b), m, dur * g.bt / 0.545, vel * acc * c, p=width if k % 2 else -width, bright=bright, gain=gain)
        b += step
        k += 1


def bassline(S, g, b0, b1, changes, vel=1.0, style="8ths", gain=0.42):
    b = b0
    k = 0
    while b < b1 - 1e-6:
        sym = at(changes, b)
        r = bass_midi(sym, 33)
        if r > 43:
            r -= 12
        if style == "8ths":
            v = vel * (0.78 if k % 2 == 0 else 1.0)
            S.bass(g(b), r, 0.42 * g.bt, v, gain)
            b += 0.5
        elif style == "quarters":
            S.bass(g(b), r, 0.6 * g.bt, vel, gain)
            b += 1.0
        else:  # "whole": one note per chord
            nxt = min([bb for bb, _ in changes if bb > b + 1e-6] + [b1])
            S.bass(g(b), r, (nxt - b) * g.bt - 0.05, vel, gain)
            b = nxt
        k += 1


def pads(S, g, b0, b1, changes, level=0.12, bright=1.0, lo=50, hi=74, fi=0.25):
    pts = [bb for bb, _ in changes if b0 - 1e-6 <= bb < b1 - 1e-6]
    if not pts or pts[0] > b0 + 1e-6:
        pts = [b0] + pts
    for i, bb in enumerate(pts):
        e = pts[i + 1] if i + 1 < len(pts) else b1
        S.pad(g(bb), g(e), at(changes, bb), level, fi=fi, fo=0.25, lo=lo, hi=hi, bright=bright)


def drums(S, g, b0, b1, level=1, clap_from=None, fill=True):
    """level 1: kick 1 & 3, shaker 16ths; 2: + 'and of 2' kick, claps 2 & 4, offbeat hats; 3: four on the
    floor + open hats on the offbeats."""
    nb = int(np.floor(b1 - b0 + 1e-6))
    clap_from = b0 if clap_from is None else clap_from
    for i in range(nb):
        b = b0 + i
        bar_pos = i % 4
        if level >= 3 or bar_pos in (0, 2):
            S.kick(g(b), 1.0 if bar_pos in (0, 2) else 0.85)
        if level >= 2 and bar_pos == 1 and i % 8 == 1:
            S.kick(g(b + 0.5), 0.6, pump=0.5)
        if b >= clap_from - 1e-6 and bar_pos in (1, 3):
            S.clap(g(b), 0.95)
        for s in range(4):
            tb = b + s * 0.25
            S.shaker(g(tb), 1.0 if s == 2 else 0.55, accent=(s == 2), p=0.3 if s % 2 else 0.15)
        if level >= 2:
            S.hat(g(b + 0.5), 0.7 if level == 2 else 0.6, open_=(level >= 3), gain=0.05 if level >= 3 else 0.055)
    # fill on the final beat(s) of a segment that leads somewhere
    if fill:
        bf = b0 + nb - 1
        for s in range(4):
            S.snare(g(bf + s * 0.25), 0.45 + 0.15 * s)


def riser(S, t0, t1, g=None, roll_from=None, level=1.0, low=180, high=5000):
    """Noise riser + accelerating snare roll + reverse crash into t1."""
    S.sweep(t0, t1, low, high, gain=0.11 * level)
    if g is not None and roll_from is not None:
        b = roll_from
        bend = (t1 - g.t0) / g.bt
        while g(b) < t1 - 0.02:
            k = (g(b) - t0) / max(t1 - t0, 1e-9)
            S.snare(g(b), 0.25 + 0.6 * max(k, 0), gain=0.13 * level, bus="perc", p=0.1 * np.sin(b * 3))
            b += 0.5 if (bend - b) > 2 else (0.25 if (bend - b) > 1 else 0.125)
    rv = I.crash(1.4, 7)[:, ::-1]
    rv = fade(rv * np.linspace(0, 1, rv.shape[1]) ** 2, 0.05, 0.003)
    S.put("fx", rv, t1 - rv.shape[1] / SR, 0.06 * level)


def impact(S, t, sym, vel=1.0, piano=True, big=False):
    """Section-change impact: kick + sub boom + crash + a bright piano chord."""
    S.kick(t, 1.0, pump=1.2)
    r = bass_midi(sym, 26)
    S.boom(t, vel * (1.0 if big else 0.75), f=float(I.midi_hz(r)))
    S.crash(t, vel * (1.0 if big else 0.8), dur=3.0 if big else 2.2)
    S.clap(t, 0.7 * vel, gain=0.18, bus="perc")
    if piano:
        notes = [bass_midi(sym, 26), bass_midi(sym, 38)] + voicing(sym, 62, 86 if big else 81)
        S.stab(t, notes, 0.72 * vel, ring=1.6 if not big else 2.2, gain=0.42)


def hook(S, g, b0, notes, vel=0.66, octave=False, pluck=False, glock=False, gain=0.5, bus="piano", ring_tail=0.3):
    for i, (b, name, d) in enumerate(notes):
        m = nm(name)
        t = g(b0 + b)
        S.piano(t, m, vel + (0.05 if i % 4 == 0 else 0.0), ring=d * g.bt + ring_tail, gain=gain, bus=bus)
        if octave:
            S.piano(t + 0.003, m - 12, vel * 0.72, ring=d * g.bt + ring_tail, gain=gain, bus=bus)
        if pluck:
            S.pluck(t, m + 12, d * g.bt * 0.8, 0.7, p=0.2 * (1 if i % 2 else -1), bright=0.9, gain=0.12)
        if glock:
            S.glock(t, m + 12, 0.85, decay=0.9)


def breakdown(S, g, changes, hook_notes, build_beats=2.0, kick_from=0.0, depth=500.0):
    """Quality-shot breakdown: the groove drops behind a low-pass, half-time (kick on 1, clap on 3, with
    a big room), pad held, piano states the hook in augmentation over it; build back into the next downbeat."""
    t0, t1 = g.t0, g.t1
    S.filt_pts([(t0 - 0.01, 20000.0), (t0 + 0.22, depth), (g(g.beats - build_beats), depth * 1.2), (t1 - 0.02, 20000.0)])
    S.crash(t0, 0.55, dur=2.0)
    S.sweep(t0, t0 + 1.6, 3000, 200, gain=0.07)
    pads(S, g, 0, g.beats, changes, level=0.13, bright=1.1)
    nb = int(np.floor(g.beats + 1e-6))
    for i in range(nb):
        b = float(i)
        if b < kick_from - 1e-6:
            continue
        if i % 4 == 0:
            S.kick(g(b), 0.9, pump=1.0)
            S.bass(g(b), bass_midi(at(changes, b), 33), 3.9 * g.bt, 0.8)
        if i % 4 == 2:
            S.clap(g(b), 0.8, gain=0.12, bus="perc")
    # piano: hook in half-time, bright and open, with more reverb
    for (b, name, d) in hook_notes:
        bb = 2 * b
        if bb >= g.beats - build_beats - 1e-6:
            break
        S.piano(g(bb), nm(name), 0.5, ring=2 * d * g.bt + 0.6, gain=0.5, bus="pianoverb")
        S.piano(g(bb) + 0.003, nm(name) - 12, 0.32, ring=2 * d * g.bt + 0.6, gain=0.5, bus="pianoverb")
    # build: snare roll + riser into the next downbeat
    riser(S, g(g.beats - build_beats), t1, g, roll_from=g.beats - build_beats, level=0.9)
    arp(S, g, g.beats - build_beats, g.beats, changes, vel=0.7, step=0.25, cresc=0.6, gain=0.22)


def tail(S, t, t_end, sym, level=0.14):
    """After a hit: pad and soft piano ring out until the next section begins."""
    S.pad(t, t_end, sym, level, fi=0.05, fo=0.6, bright=1.15)


def light(S, g, b0, b1, changes, vel=0.5, pulse=True, shaker_from=None, lvl=1.0):
    """Typing sections: light and curious -- piano ostinato, soft pulse (short bass + muted kick on the
    beat), soft shaker, pad underneath."""
    ostinato(S, g, b0, b1, changes, vel=vel, lh_vel=0.34)
    pads(S, g, b0, b1, changes, level=0.095 * lvl, bright=1.0)
    if pulse:
        b = b0
        while b < b1 - 1e-6:
            S.bass(g(b), bass_midi(at(changes, b), 33) + 12, 0.35 * g.bt, 0.5 * lvl, gain=0.42, bright=0.6)
            if abs(b - round(b)) < 1e-6:
                S.kick(g(b), 0.4 * lvl, pump=0.35)
            b += 1.0
    if shaker_from is not None:
        b = max(b0, shaker_from)
        while b < b1 - 1e-6:
            S.shaker(g(b), 0.45 if (b * 2) % 2 else 0.3, gain=0.08 * lvl, p=0.35)
            b += 0.5


# ---------------------------------------------------------------- the cue sheet
class Grid:
    """Absolute beat grid: beat b sits at b * BT seconds."""
    t0, bt = 0.0, BT

    def __call__(self, b):
        return b * BT


PROG = ["D", "A", "Bm", "G"]          # one chord per bar, the whole film


def prog(b0, b1, cyc=PROG):
    first = int(np.floor(b0 / 4))
    return [(4.0 * k, cyc[k % len(cyc)]) for k in range(first, int(np.ceil(b1 / 4)) + 1)]


def groove(S, g, b0, b1, level):
    """Drums on the absolute bar, so the meter never hiccups across sections.
    1: kick on 1 & 3, shaker 16ths, clap on 4.  2: + clap 2 & 4, offbeat hats.  3: four on the floor,
    open hats."""
    b = float(b0)
    while b < b1 - 1e-6:
        pos = int(round(b)) % 4
        if level >= 3 or pos in (0, 2):
            S.kick(g(b), 1.0 if pos in (0, 2) else 0.85)
        if level >= 2 and pos == 2:
            S.kick(g(b - 0.5), 0.5, pump=0.4)
        if (level >= 2 and pos in (1, 3)) or (level == 1 and pos == 3):
            S.clap(g(b), 0.9 if level >= 2 else 0.7)
        for s in range(4):
            S.shaker(g(b + s * 0.25), 1.0 if s == 2 else 0.55, accent=(s == 2), p=0.3 if s % 2 else 0.15,
                     gain=0.08 if level == 1 else 0.1)
        if level >= 2:
            S.hat(g(b + 0.5), 0.6, open_=(level >= 3), gain=0.05)
        b += 1.0


def fill(S, g, b_end, n=2):
    """A short snare fill into beat b_end (instead of a full riser)."""
    k = int(n * 4)
    for s in range(k):
        S.snare(g(b_end - n + s * 0.25), 0.3 + 0.4 * s / k, gain=0.13)


def accent(S, t, sym, vel=0.8, crash=True):
    """A hit on the beat without stopping the groove: crash + bright piano chord."""
    if crash:
        S.crash(t, vel, dur=2.2)
    S.stab(t, [bass_midi(sym, 38)] + voicing(sym, 62, 81), 0.62 * vel, ring=1.2, gain=0.4)


def compose(events=None, lines=None):
    S = Score()
    g = Grid()
    B = lambda t: round(t / BT, 6)  # noqa: E731

    # ---------------- 0–13 intro: piano, then a soft pulse; nothing drops after this ----------------
    for b, name, v in ((0, "A4", 0.40), (0.5, "D5", 0.42), (1, "F#5", 0.46), (2, "E5", 0.40)):
        S.piano(g(b), nm(name), v, ring=0.8, gain=0.45, human=False)
    S.piano(g(0), nm("D3"), 0.36, ring=2.0, gain=0.45, human=False)
    ch = prog(4, 26)
    ostinato(S, g, 4, 26, ch, vel=0.40, lh_vel=0.32, cresc=0.15)
    pads(S, g, 4, 26, ch, level=0.08, bright=0.9)
    for b, name, d in HOOK[:5]:               # the hook teased high, while "And still cost someone days." is up
        S.piano(g(9 + b), nm(name) + 12, 0.30, ring=d * BT + 0.4, gain=0.42, bus="pianoverb")
    for b in np.arange(8, 26, 1.0):           # soft pulse from 4 s
        S.bass(g(b), bass_midi(at(ch, b), 33) + 12, 0.35 * BT, 0.5, gain=0.42, bright=0.6)
        S.kick(g(b), 0.3 + 0.15 * (b >= 16), gain=0.62, pump=0.3)
    for b in np.arange(16, 26, 0.5):          # typing (8.0): shaker
        S.shaker(g(b), 0.4 if (b * 2) % 2 else 0.25, gain=0.07, p=0.35)
    arp(S, g, 18, 26, ch, vel=0.55, cresc=0.5, gain=0.22)
    riser(S, 11.0, 13.0, None, level=0.7)
    fill(S, g, 26, 1.5)

    # ---------------- 13–34.5 groove 1 (sentence 1, then typing 2 slightly lighter) ----------------
    b0, b1 = B(13.0), B(34.5)
    ch = prog(b0, b1)
    S.crash(13.0, 0.75)
    S.boom(13.0, 0.6, f=float(I.midi_hz(bass_midi("A", 26))))
    groove(S, g, b0, b1, 1)
    bassline(S, g, b0, b1, ch, vel=0.9)
    pads(S, g, b0, b1, ch, level=0.11)
    ostinato(S, g, b0, b1, ch, vel=0.48)
    arp(S, g, B(17.0), B(28.5), ch, vel=0.72, gain=0.24)
    hook(S, g, B(24.0), HOOK, vel=0.64)
    accent(S, 28.5, "A", 0.7)
    fill(S, g, b1, 1)

    # ---------------- 34.5–56.5 groove 2 (PPT, then typing 3) ----------------
    b0, b1 = B(34.5), B(56.5)
    ch = prog(b0, b1)
    S.crash(34.5, 0.8)
    groove(S, g, b0, b1, 2)
    bassline(S, g, b0, b1, ch, vel=0.95)
    pads(S, g, b0, b1, ch, level=0.115)
    ostinato(S, g, b0, b1, ch, vel=0.48)
    arp(S, g, b0, B(49.5), ch, vel=0.76, gain=0.25)
    arp(S, g, B(49.5), b1, ch, vel=0.6, gain=0.2)
    hook(S, g, B(46.0), HOOK, vel=0.66, pluck=True)
    accent(S, 49.5, "G", 0.75)
    fill(S, g, b1, 1)

    # ---------------- 56.5–78.5 groove 3 (Word, then typing 4): four on the floor ----------------
    b0, b1 = B(56.5), B(78.5)
    ch = prog(b0, b1)
    S.crash(56.5, 0.85)
    S.boom(56.5, 0.55, f=float(I.midi_hz(bass_midi("D", 26))))
    groove(S, g, b0, b1, 3)
    bassline(S, g, b0, b1, ch, vel=1.0)
    pads(S, g, b0, b1, ch, level=0.12, bright=1.1)
    ostinato(S, g, b0, b1, ch, vel=0.5)
    arp(S, g, b0, b1, ch, vel=0.78, gain=0.25, lo=66)
    hook(S, g, B(70.0), HOOK, vel=0.68, pluck=True, glock=True)
    accent(S, 72.5, "A", 0.8)
    fill(S, g, b1, 1)

    # ---------------- 78.5–100 groove 4 (code) + montage hits ----------------
    b0, b1 = B(78.5), B(100.0)
    ch = prog(b0, b1)
    S.crash(78.5, 0.85)
    groove(S, g, b0, b1, 3)
    bassline(S, g, b0, b1, ch, vel=1.0)
    pads(S, g, b0, b1, ch, level=0.12, bright=1.15)
    ostinato(S, g, b0, b1, ch, vel=0.5)
    arp(S, g, b0, b1, ch, vel=0.8, dur=0.1, gain=0.26, pattern=(0, 2, 1, 3, 2, 4, 3, 1), lo=66)
    hook4 = HOOK[:5] + [(4.0, "D5", 0.75), (4.75, "F#5", 0.75), (5.5, "A5", 0.5), (6.0, "B5", 0.75), (6.75, "A5", 0.75)]
    hook(S, g, B(88.0), hook4, vel=0.68, octave=True, pluck=True, glock=True)
    accent(S, 92.5, "A", 0.85)
    for t in (94.0, 95.5, 97.0, 98.5):
        sym = at(ch, B(t))
        accent(S, t, sym, 0.7)
        S.pluck(t, voicing(sym, 74, 86)[-1], 0.5, 0.85, p=0.0, gain=0.14)
    fill(S, g, b1, 1)

    # ---------------- 100–103 breath: drums out, harmony and the hook carry on ----------------
    b0, b1 = B(100.0), B(103.0)
    ch = [(b0, "G"), (b0 + 4, "A")]
    S.crash(100.0, 0.5, dur=2.5)
    pads(S, g, b0, b1, ch, level=0.12, bright=1.05)
    bassline(S, g, b0, b1, ch, vel=0.7, style="whole")
    for b, name, d in HOOK[:5]:
        S.piano(g(b0 + b), nm(name), 0.48, ring=d * BT + 0.5, gain=0.48, bus="pianoverb")
    arp(S, g, b0, b1, ch, vel=0.5, gain=0.18, lo=66)

    # ---------------- 103–110 build ----------------
    b0, b1 = B(103.0), B(110.0)
    ch = prog(b0, b1)
    S.filt_pts([(102.99, 20000.0), (103.0, 900.0), (108.0, 3000.0), (109.98, 20000.0)])
    pads(S, g, b0, b1, ch, level=0.13, bright=1.15)
    ostinato(S, g, b0, b1, ch, vel=0.44, cresc=0.2)
    arp(S, g, b0, b1, ch, vel=0.62, cresc=0.6, gain=0.24, lo=66)
    bassline(S, g, b0, b1, ch, vel=0.9)
    for b in np.arange(b0, b1, 1.0):
        S.kick(g(b), 0.6 + 0.4 * (b - b0) / (b1 - b0), pump=0.8)
        S.shaker(g(b + 0.5), 0.8, gain=0.08, accent=True)
        if int(b) % 2:
            S.clap(g(b), 0.8)
    riser(S, 106.0, 110.0, g, roll_from=b1 - 6, level=1.0, low=150, high=7000)

    # ---------------- 110–113.5 peak ----------------
    b0, b1 = B(110.0), B(113.5)
    ch = prog(b0, b1)
    impact(S, 110.0, "D", 1.1, big=True)
    S.glock(110.0, nm("D6"), 1.0)
    groove(S, g, b0, b1, 3)
    bassline(S, g, b0, b1, ch, vel=1.05)
    pads(S, g, b0, b1, ch, level=0.135, bright=1.25, hi=79)
    pads(S, g, b0, b1, ch, level=0.06, bright=1.2, lo=74, hi=88)
    ostinato(S, g, b0, b1, ch, vel=0.5, lo=57, hi=72)
    arp(S, g, b0, b1, ch, vel=0.72, gain=0.25, lo=69, hi=93)
    hook(S, g, b0, HOOK_PEAK, vel=0.74, octave=True, pluck=True, glock=True, gain=0.52)

    # ---------------- 113.5–117.5 outro: the groove eases, the hook once more ----------------
    b0, b1 = B(113.5), B(117.5)
    och = [(b0, "Gmaj7"), (b0 + 4, "Asus"), (b0 + 6, "A")]
    S.stab(113.5, [nm("G2"), nm("D3"), nm("B3"), nm("F#4"), nm("A4"), nm("D5")], 0.5, ring=2.0, gain=0.42,
           bus="pianoverb", spread=0.006)
    pads(S, g, b0, b1, och, level=0.1, bright=1.0)
    for b in np.arange(b0, b1, 1.0):
        S.kick(g(b), 0.4, pump=0.3)
        S.bass(g(b), bass_midi(at(och, b), 33) + 12, 0.4 * BT, 0.45, gain=0.42, bright=0.6)
        S.shaker(g(b + 0.5), 0.5, gain=0.06)
    for b, name, d in ((1.0, "F#5", 0.75), (1.75, "E5", 0.75), (2.5, "D5", 0.5), (3.0, "E5", 1.0), (4.0, "A4", 0.75),
                       (4.75, "C#5", 0.75), (5.5, "E5", 0.5)):
        S.piano(g(b0 + b), nm(name), 0.44, ring=d * BT + 0.35, gain=0.46, bus="pianoverb")

    # ---------------- 117.5 resolution ----------------
    S.stab(117.5, [nm("D2"), nm("A2"), nm("D3"), nm("F#3"), nm("A3"), nm("E4"), nm("F#4"), nm("A4"), nm("D5")], 0.55,
           ring=2.6, gain=0.44, bus="pianoverb", spread=0.008)
    S.piano(117.5, nm("F#5"), 0.5, ring=2.6, gain=0.46, bus="pianoverb")
    S.pad(117.5, 120.0, "Dadd9", 0.10, fi=0.3, fo=0.2, bright=1.0)
    S.bass(117.5, nm("D2"), 2.3, 0.55, gain=0.4, bright=0.5)
    S.glock(118.5, nm("A5"), 0.5, decay=1.2)
    S.glock(119.0, nm("D6"), 0.45, decay=1.2)
    return S


def pump_curve(kicks, depth, release=0.16):
    """Side-chain gain from the kick times: dips to (1 - depth*k) in 4 ms, recovers with a smooth curve."""
    hop = 48
    k = N // hop + 1
    g = np.ones(k)
    tt = np.arange(k) * hop / SR
    for t, amt in kicks:
        a = int(t * SR / hop)
        bL = int((release * 3.5) * SR / hop)
        seg = tt[a:a + bL] - t
        d = min(1.0, depth * amt)
        shape = np.where(seg < 0.004, seg / 0.004, 1.0) * np.exp(-np.maximum(seg - 0.004, 0) / release) ** 1.0
        shape = np.clip(shape, 0, 1)
        g[a:a + bL] = np.minimum(g[a:a + bL], 1 - d * shape)
    return np.interp(np.arange(N), np.arange(k) * hop, g)


def cutoff_curve(pts):
    pts = sorted(pts)
    t = np.arange(N) / SR
    xs = [p[0] for p in pts]
    ys = [np.log(p[1]) for p in pts]
    return np.exp(np.interp(t, xs, ys))


def tv_lowpass(x, fc, q=0.75, block=64):
    """Time-varying 12 dB/oct low-pass (RBJ biquad, coefficients updated every `block` samples), applied
    only where fc < 19 kHz (with 30 ms of run-in / crossfade either side)."""
    act = fc < 19000
    if not act.any():
        return x
    out = x.copy()
    idx = np.nonzero(act)[0]
    # contiguous regions
    breaks = np.nonzero(np.diff(idx) > SR * 0.1)[0]
    starts = np.concatenate([[idx[0]], idx[breaks + 1]])
    ends = np.concatenate([idx[breaks], [idx[-1]]])
    pad = int(0.03 * SR)
    for s, e in zip(starts, ends):
        a, b = max(0, s - pad), min(N, e + pad)
        seg = x[:, a:b]
        y = np.zeros_like(seg)
        zi = np.zeros((1, 2, 2))
        for i in range(0, b - a, block):
            f = float(min(fc[a + i], 19500))
            w = 2 * np.pi * f / SR
            al = np.sin(w) / (2 * q)
            c = np.cos(w)
            b0 = (1 - c) / 2
            sos = np.array([[b0, 1 - c, b0, 1 + al, -2 * c, 1 - al]]) / (1 + al)
            sos[0, 3] = 1.0
            yy, zi = signal.sosfilt(sos, seg[:, i:i + block], axis=-1, zi=zi)
            y[:, i:i + block] = yy
        # crossfade in/out over the run-in pads (both nearly identical there: fc ~ 19.5 kHz)
        w = np.ones(b - a)
        fl = min(pad, (b - a) // 2)
        w[:fl] = np.linspace(0, 1, fl)
        w[-fl:] = np.linspace(1, 0, fl)
        out[:, a:b] = seg * (1 - w) + y * w
    return out


def echo(x, delay=0.409, fb=0.32, mix=0.22, lp=4500.0):
    """Ping-pong dotted-8th echo (feedback via repeated shifted adds; 6 repeats)."""
    d = int(delay * SR)
    wet = np.zeros_like(x)
    src = filt(x, lowpass(lp), highpass(250))
    g = mix
    cur = src
    for k in range(1, 7):
        sh = np.zeros_like(x)
        ch = k % 2
        sh[ch, d * k:] = (cur[0, :-d * k] + cur[1, :-d * k]) * 0.5
        wet += g * sh
        g *= fb
    return x + filt(wet, lowpass(lp))


RIDES = [  # (t0, t1, dB)
    (0.0, 34.5, -1.0), (110.0, 113.5, 1.0)]


def ride_curve():
    g = np.zeros(N)
    for a, b, d in RIDES:
        g[int(a * SR):int(b * SR)] = d
    from scipy.ndimage import uniform_filter1d
    return uniform_filter1d(g, int(0.03 * SR))


def render(events=None, lines=None):
    S = compose(events, lines)
    B = {k: v.astype(np.float64) for k, v in S.bus.items()}

    # bus colour
    B["piano"] = I.piano_bright_eq(B["piano"])
    B["pianoverb"] = I.piano_bright_eq(B["pianoverb"])
    B["pluck"] = filt(B["pluck"], highpass(180), shelf(7000, -3.0, True))
    pl = np.zeros((2, N))
    for a, b in TAKES:    # the echo, like the reverb, never crosses a cut
        ia, ib = int(round(a * SR)), int(round(b * SR))
        pl[:, ia:ib] = fade(echo(B["pluck"][:, ia:ib]), 0.0, 0.006)
    B["pluck"] = pl
    B["pad"] = filt(B["pad"], highpass(110), peak(350, -2.0, 0.8), shelf(6000, -3.0, True))
    B["bass"] = filt(B["bass"], highpass(32), lowpass(2500))
    B["kick"] = filt(B["kick"], highpass(30))
    B["drums"] = filt(B["drums"], highpass(200), shelf(9000, -2.0, True))
    B["perc"] = filt(B["perc"], highpass(180), shelf(9000, -2.0, True))
    B["bell"] = filt(B["bell"], highpass(400), shelf(8000, -3.0, True))

    # side-chain pump (kick -> pad, bass, pluck)
    for name, depth in (("pad", 0.55), ("bass", 0.65), ("pluck", 0.3)):
        B[name] = B[name] * pump_curve(S.kicks, depth)[None]
    B["bass"] *= db(-1.5)

    # groove filter (breakdowns / builds / riser dips)
    fc = cutoff_curve(S.cut)
    for name in GROOVE:
        B[name] = tv_lowpass(B[name], fc)

    # reverbs: a bright hall and a short room, rendered per take (no tail crosses a cut)
    hall = make_ir(dur=2.6, t60_low=2.0, t60_high=1.2, predelay=0.03, seed=11, width=1.0, hp=180, lp=9000)
    room = make_ir(dur=0.9, t60_low=0.55, t60_high=0.35, predelay=0.01, seed=5, width=0.9, hp=200, lp=9000)
    dry = sum(B.values())
    wet = np.zeros((2, N))
    for ir, sends, g in ((hall, HALL_SEND, 0.55), (room, ROOM_SEND, 0.5)):
        snd = sum(B[k] * v for k, v in sends.items() if v)
        for k, (a, b) in enumerate(TAKES):
            ia, ib = int(round(a * SR)), int(round(b * SR))
            w = convolve_stereo(snd[:, ia:ib], ir)
            if k < len(TAKES) - 1:
                fl = int(0.006 * SR)
                w[:, -fl:] *= np.cos(np.linspace(0, np.pi / 2, fl)) ** 2
            wet[:, ia:ib] += g * w
    wet = filt(ms_width(wet, 1.05), highpass(150))
    x = dry + wet

    # fader rides: each sentence's groove a little bigger than the last; the peak clearly the biggest
    x *= db(ride_curve())[None]

    # glue + master colour: controlled lows (mono below ~120 Hz), no harsh highs
    m, s = 0.5 * (x[0] + x[1]), 0.5 * (x[0] - x[1])
    s = filt(s, highpass(120))
    x = np.stack([m + s, m - s])
    x = filt(x, highpass(32), shelf(110, -3.5, False), peak(320, -2.5, 0.8), shelf(2600, 3.0, True, 0.7),
             peak(4200, -1.0, 1.2), shelf(10500, -2.5, True))
    from dsp import compress
    x = compress(x, thresh_db=20 * np.log10(np.max(np.abs(x)) + 1e-9) - 14, ratio=2.0, attack=0.02, release=0.2, knee=8)
    # the end: fade with the picture 119.4 -> 120.0
    t = np.arange(N) / SR
    x *= np.clip((120.0 - t) / 0.6, 0, 1) ** 1.5
    # start: tiny fade so nothing clicks at 0
    x[:, :240] *= np.linspace(0, 1, 240)
    global LAST_BUSES
    LAST_BUSES = {k: B[k] for k in ("kick", "drums")}
    return x
