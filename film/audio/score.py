"""The score (v2, bright launch track), written against the film's map (script §5.2 gives WHERE things
happen; the mood is new: D major, ~110 BPM, uplifting and confident).

Orchestration: bright grand piano (8th-note ostinato + the hook), plucked synth arpeggios (16ths, with a
dotted-8th ping-pong echo), warm detuned-saw pad and synth bass (both side-chained to the kick), a clean
kick / clap / shaker / hat groove that enters progressively, glockenspiel doubling the hook at the peaks,
noise risers, snare-roll builds, downlifters and impacts (kick + sub boom + soft crash) on section changes.

The hook: F#5 E5 D5 · E5 A4 | D5 F#5 A5 -> (hit on D). Teased on the piano while sentence 1 is typed,
stated in half-time over each "quality shot" breakdown, then in full after it (25.0 / 46.0 / 69.5 / 88.5),
gaining a layer every time; alone and quiet on the piano at 100.4; in octaves with everything at 110.0,
where it now leaps UP to A5-B5; and once more, gently, in the outro over G - A - D (117.5).

Timing: a tempo map. Each section of the film is a segment with a whole (or half) number of beats, so
every landing, hit and section change falls on the beat grid (local tempi 106-120 BPM, mostly 108-113;
the montage runs at 120 so its four cuts 94.0/95.5/97.0/98.5 are three beats apart). Hits marked
".5" in the map are anticipations (a push on the "and").

The music is rendered in three "takes" split at the two hard stops (100.0 the montage stops dead,
113.6 the scissor cut onto a single bright piano chord); a note never rings over its take's end and each
take has its own reverb tails, so nothing leaks across a cut.
"""
import numpy as np
from scipy import signal

import instruments as I
from dsp import (N, SR, add, convolve_stereo, db, envelope, fade, filt, highpass, lowpass, make_ir, ms_width,
                 peak, shelf)

# ---------------------------------------------------------------- tempo map
# (t0, t1, beats, what)
SEGS = [
    (0.0, 13.0, 24, "intro: piano + soft pulse, typing 1, riser 11.2 -> 13.0"),
    (13.0, 21.0, 15, "groove 1"),
    (21.0, 25.0, 8, "breakdown 1 (half-time, filtered)"),
    (25.0, 28.5, 6.5, "hook 1 -> hit 28.5"),
    (28.5, 34.5, 11, "tail, typing 2 (30.0), riser 33.0 -> 34.5"),
    (34.5, 42.0, 14, "groove 2"),
    (42.0, 46.0, 8, "breakdown 2"),
    (46.0, 49.5, 6.5, "hook 2 -> hit 49.5"),
    (49.5, 56.6, 13, "tail, typing 3 (52.0), riser 55.2 -> 56.6"),
    (56.6, 65.0, 15, "groove 3"),
    (65.0, 69.5, 8, "breakdown 3 (longest)"),
    (69.5, 72.5, 5.5, "hook 3 -> hit 72.5"),
    (72.5, 78.6, 11, "tail, typing 4 (74.0), riser 77.4 -> 78.6"),
    (78.6, 86.0, 14, "groove 4 (tight 16ths)"),
    (86.0, 88.5, 5, "breakdown 4"),
    (88.5, 92.5, 7.5, "hook 4 -> hit 92.5"),
    (92.5, 100.0, 15, "tail, montage hits 94.0/95.5/97.0/98.5, riser -> 100.0 stop"),
    (100.4, 103.2, 5, "breath: piano hook alone"),
    (103.2, 110.0, 12, "build"),
    (110.0, 113.6, 6.5, "peak -> scissor cut 113.6"),
    (113.6, 117.5, 7, "outro: piano tail, hook over G - A"),
    (117.5, 120.0, 4.5, "resolution: D major"),
]
TAKES = [(0.0, 100.0), (100.0, 113.6), (113.6, 120.0)]


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
HALL_SEND = {"piano": 0.22, "pianoverb": 0.6, "pluck": 0.25, "pad": 0.3, "bass": 0.0, "kick": 0.0, "drums": 0.06,
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
def compose(events=None, lines=None):
    S = Score()
    G = [Seg(i) for i in range(len(SEGS))]

    # ---------------- 0.0–13.0 intro: light and curious ----------------
    g = G[0]
    ch = [(0, "D"), (4, "Bm7"), (8, "Gmaj7"), (12, "Asus"), (14, "A"), (16, "D"), (20, "Asus"), (22, "A")]
    # bar 1: a few notes, a question; then the ostinato
    for b, name, v in ((1, "A4", 0.40), (1.5, "D5", 0.42), (2, "F#5", 0.46), (3, "E5", 0.40)):
        S.piano(g(b), nm(name), v, ring=g(b + 1) - g(b) + 0.3, gain=0.45, human=False)
    S.piano(g(1), nm("D3"), 0.36, ring=g(4) - g(1), gain=0.45, human=False)
    ostinato(S, g, 4, 24, ch, vel=0.40, lh_vel=0.32, cresc=0.2)
    pads(S, g, 4, 20.5, ch, level=0.07, bright=0.85)
    for b in np.arange(8, 20.5, 1.0):     # soft pulse from bar 3
        S.bass(g(b), bass_midi(at(ch, b), 33) + 12, 0.35 * g.bt, 0.45 + 0.15 * (b > 14), gain=0.42, bright=0.6)
        S.kick(g(b), 0.28 + 0.06 * (b >= 14), gain=0.62, pump=0.3)
    for b in np.arange(14, 20.5, 0.5):    # typing starts (8.0): soft shaker
        S.shaker(g(b), 0.4 if (b * 2) % 2 else 0.25, gain=0.06, p=0.35)
    # 4.3–6.5: the hook teased high on the piano (while "And still cost someone days." is on screen)
    for b, name, d in HOOK[:5]:
        S.piano(g(8 + b), nm(name) + 12, 0.30, ring=d * g.bt + 0.4, gain=0.42, bus="pianoverb")
    # riser 11.2 -> 13.0 (beat 20.67 -> 24)
    riser(S, 11.2, 13.0, g, roll_from=21, level=0.9)
    arp(S, g, 21, 24, ch, vel=0.65, step=0.25, cresc=0.8, gain=0.24)
    S.filt_pts([(11.1, 20000.0), (11.4, 2000.0), (12.98, 20000.0)])
    pads(S, g, 20.5, 24, ch, level=0.085, bright=1.0)

    # ---------------- 13.0–21.0 groove 1 ----------------
    g = G[1]
    ch = [(0, "D"), (4, "A/C#"), (8, "Bm"), (12, "G")]
    impact(S, g.t0, "D", 0.85)
    drums(S, g, 0, g.beats, level=1, clap_from=8)
    bassline(S, g, 0, g.beats, ch, vel=0.9)
    pads(S, g, 0, g.beats, ch, level=0.11)
    ostinato(S, g, 0, g.beats, ch, vel=0.5)
    arp(S, g, 4, g.beats, ch, vel=0.75, gain=0.25)

    # ---------------- 21.0–25.0 breakdown 1 ----------------
    g = G[2]
    breakdown(S, g, [(0, "Bm7"), (4, "G"), (6, "Asus"), (7, "A")], HOOK, build_beats=2)

    # ---------------- 25.0–28.5 hook 1 -> hit 28.5 ----------------
    g = G[3]
    ch = [(0, "D"), (2, "A"), (4, "G")]
    S.crash(g.t0, 0.7)
    drums(S, g, 0, 6, level=1, fill=False)
    for s in range(4):
        S.snare(g(5.5 + s * 0.25), 0.4 + 0.15 * s)
    bassline(S, g, 0, 6.5, ch, vel=0.9)
    pads(S, g, 0, 6.5, ch, level=0.11)
    ostinato(S, g, 0, 6.5, ch, vel=0.38, lh=True, lo=57, hi=72)
    arp(S, g, 0, 6.5, ch, vel=0.6, gain=0.22)
    hook(S, g, 0, HOOK, vel=0.66)
    impact(S, 28.5, "D", 0.9)
    S.piano(28.5, nm("D6"), 0.55, ring=1.4, gain=0.45, bus="pianoverb")

    # ---------------- 28.5–34.5 tail, typing 2, riser ----------------
    g = G[4]
    tail(S, 28.5, g(2), "D")
    ch = [(2, "G"), (7, "Asus"), (8, "A")]
    light(S, g, 2, 11, ch, shaker_from=3)
    riser(S, 33.0, 34.5, g, roll_from=8.5, level=0.9)
    arp(S, g, 8.25, 11, [(0, "A")], vel=0.6, cresc=0.8, gain=0.24)
    S.filt_pts([(32.9, 20000.0), (33.2, 2000.0), (34.48, 20000.0)])

    # ---------------- 34.5–42.0 groove 2 ----------------
    g = G[5]
    ch = [(0, "D"), (4, "Bm"), (8, "G"), (12, "A")]
    impact(S, g.t0, "D", 0.9)
    drums(S, g, 0, g.beats, level=2)
    bassline(S, g, 0, g.beats, ch, vel=0.95)
    pads(S, g, 0, g.beats, ch, level=0.115)
    ostinato(S, g, 0, g.beats, ch, vel=0.5)
    arp(S, g, 0, g.beats, ch, vel=0.78, gain=0.26)
    # counter-line on the pluck, bars 3-4 (answers the hook)
    for b, name, d in ((8, "B5", 0.5), (8.5, "A5", 0.5), (9, "G5", 1.0), (10.5, "F#5", 0.5), (11, "G5", 1.0),
                       (12, "A5", 1.5), (13.5, "E5", 0.5)):
        S.pluck(g(b), nm(name), d * g.bt * 0.85, 0.75, p=0.15, bright=1.0, gain=0.15)

    # ---------------- 42.0–46.0 breakdown 2 ----------------
    g = G[6]
    breakdown(S, g, [(0, "G"), (4, "Em7"), (6, "Asus"), (7, "A")], HOOK, build_beats=2)

    # ---------------- 46.0–49.5 hook 2 (+ pluck an octave up) -> hit 49.5 ----------------
    g = G[7]
    ch = [(0, "D"), (2, "A"), (4, "G")]
    S.crash(g.t0, 0.75)
    drums(S, g, 0, 6, level=2, fill=False)
    for s in range(4):
        S.snare(g(5.5 + s * 0.25), 0.4 + 0.15 * s)
    bassline(S, g, 0, 6.5, ch, vel=0.95)
    pads(S, g, 0, 6.5, ch, level=0.115)
    ostinato(S, g, 0, 6.5, ch, vel=0.38, lo=57, hi=72)
    arp(S, g, 0, 6.5, ch, vel=0.62, gain=0.22)
    hook(S, g, 0, HOOK, vel=0.68, pluck=True)
    impact(S, 49.5, "D", 0.95)
    S.piano(49.5, nm("D6"), 0.58, ring=1.4, gain=0.45, bus="pianoverb")

    # ---------------- 49.5–56.6 tail, typing 3, riser (lowest, longest) ----------------
    g = G[8]
    tail(S, 49.5, g(3), "D")
    ch = [(3, "Bm7"), (7, "G"), (10.5, "Asus"), (11.5, "A")]
    light(S, g, 3, 13, ch, shaker_from=4.5)
    riser(S, 55.2, 56.6, g, roll_from=10.5, level=1.0, low=120, high=4500)
    arp(S, g, 10.5, 13, ch, vel=0.6, cresc=0.8, gain=0.24)
    S.filt_pts([(55.1, 20000.0), (55.4, 1600.0), (56.58, 20000.0)])

    # ---------------- 56.6–65.0 groove 3 (four on the floor, open hats, high pad) ----------------
    g = G[9]
    ch = [(0, "G"), (4, "D/F#"), (8, "Em7"), (12, "A")]
    impact(S, g.t0, "G", 0.95)
    drums(S, g, 0, g.beats, level=3)
    bassline(S, g, 0, g.beats, ch, vel=1.0)
    pads(S, g, 0, g.beats, ch, level=0.12, bright=1.1)
    S.pad(g(0), g(g.beats), "A", 0.035, fi=2.0, fo=0.3, lo=81, hi=82, bright=0.9)   # long high A5 (patient "reading")
    ostinato(S, g, 0, g.beats, ch, vel=0.52)
    arp(S, g, 0, g.beats, ch, vel=0.8, gain=0.26, lo=66)

    # ---------------- 65.0–69.5 breakdown 3 (longest: no kick for the first bar) ----------------
    g = G[10]
    breakdown(S, g, [(0, "Bm7"), (4, "G"), (6, "Asus"), (7, "A")], HOOK, build_beats=2, kick_from=4, depth=420.0)
    S.bass(68.0, nm("A1"), 0.9, 0.9)   # the line pulls taut (68.0)

    # ---------------- 69.5–72.5 hook 3 (+ glock) -> hit 72.5 ----------------
    g = G[11]
    ch = [(0, "D"), (2, "A"), (4, "G")]
    S.crash(g.t0, 0.8)
    drums(S, g, 0, 5, level=3, fill=False)
    for s in range(4):
        S.snare(g(4.5 + s * 0.25), 0.4 + 0.15 * s)
    bassline(S, g, 0, 5.5, ch, vel=1.0)
    pads(S, g, 0, 5.5, ch, level=0.12, bright=1.1)
    ostinato(S, g, 0, 5.5, ch, vel=0.38, lo=57, hi=72)
    arp(S, g, 0, 5.5, ch, vel=0.62, gain=0.22, lo=66)
    hook(S, g, 0, HOOK_SHORT, vel=0.7, pluck=True, glock=True)
    impact(S, 72.5, "D", 1.0)
    S.piano(72.5, nm("D6"), 0.6, ring=1.4, gain=0.45, bus="pianoverb")
    S.glock(72.5, nm("D6"), 0.8)

    # ---------------- 72.5–78.6 tail, typing 4, riser (with a digital edge) ----------------
    g = G[12]
    tail(S, 72.5, g(2), "D")
    ch = [(2, "Em7"), (6, "G"), (8.5, "Asus"), (9.5, "A")]
    light(S, g, 2, 11, ch, shaker_from=3)
    riser(S, 77.4, 78.6, g, roll_from=8.5, level=0.9, low=250, high=6000)
    arp(S, g, 8.5, 11, ch, vel=0.6, step=0.125, dur=0.08, cresc=0.8, gain=0.2)   # 32nds: a digital flicker
    S.filt_pts([(77.3, 20000.0), (77.6, 2000.0), (78.58, 20000.0)])

    # ---------------- 78.6–86.0 groove 4 (tight 16ths: code) ----------------
    g = G[13]
    ch = [(0, "D"), (4, "Bm"), (8, "G"), (12, "A")]
    impact(S, g.t0, "D", 0.95)
    drums(S, g, 0, g.beats, level=3)
    bassline(S, g, 0, g.beats, ch, vel=1.0)
    pads(S, g, 0, g.beats, ch, level=0.11, bright=1.05)
    ostinato(S, g, 0, g.beats, ch, vel=0.5)
    arp(S, g, 0, g.beats, ch, vel=0.82, dur=0.09, gain=0.27, pattern=(0, 2, 1, 3, 2, 4, 3, 1), lo=66)

    # ---------------- 86.0–88.5 breakdown 4 ----------------
    g = G[14]
    breakdown(S, g, [(0, "G"), (3, "Asus"), (4, "A")], HOOK[:3], build_beats=1.5)

    # ---------------- 88.5–92.5 hook 4 (piano in octaves + pluck + glock) -> hit 92.5 ----------------
    g = G[15]
    ch = [(0, "D"), (2, "A"), (4, "Bm"), (6, "G")]
    S.crash(g.t0, 0.85)
    drums(S, g, 0, 7, level=3, fill=False)
    for s in range(6):
        S.snare(g(6.0 + s * 0.25), 0.35 + 0.12 * s)
    bassline(S, g, 0, 7.5, ch, vel=1.0)
    pads(S, g, 0, 7.5, ch, level=0.125, bright=1.15)
    ostinato(S, g, 0, 7.5, ch, vel=0.38, lo=57, hi=72)
    arp(S, g, 0, 7.5, ch, vel=0.62, gain=0.22, lo=66)
    hook4 = HOOK[:5] + [(4.0, "D5", 0.75), (4.75, "F#5", 0.75), (5.5, "A5", 0.5), (6.0, "B5", 0.75), (6.75, "A5", 0.75)]
    hook(S, g, 0, hook4, vel=0.7, octave=True, pluck=True, glock=True)
    impact(S, 92.5, "D", 1.0)
    S.piano(92.5, nm("D6"), 0.6, ring=1.2, gain=0.45, bus="pianoverb")

    # ---------------- 92.5–100.0 tail + montage: four punchy hits, riser, stop ----------------
    g = G[16]
    tail(S, 92.5, 94.0, "D", 0.09)
    mch = [(3, "G"), (6, "A"), (9, "Bm"), (12, "A")]
    for b, sym in mch:
        t = g(b)
        S.kick(t, 1.0, pump=1.2)
        S.boom(t, 0.6, f=float(I.midi_hz(bass_midi(sym, 26))))
        S.clap(t, 0.9, gain=0.2)
        S.crash(t, 0.55, dur=1.4)
        S.stab(t, [bass_midi(sym, 38)] + voicing(sym, 62, 81), 0.7, ring=1.2, gain=0.42)
        S.pluck(t, voicing(sym, 74, 86)[-1], 0.5, 0.9, p=0.0, gain=0.15)
        # driving 8ths between the hits
        for s in np.arange(0.5, 3.0, 0.5):
            if b + s < 15 - 1.6 or b < 12:
                S.bass(g(b + s), bass_midi(sym, 33) + (12 if s % 1 else 0), 0.4 * g.bt, 0.85, gain=0.4)
        for s in np.arange(0, 3.0, 0.25):
            S.shaker(g(b + s), 0.9 if (s * 4) % 2 else 0.5, gain=0.07, p=0.3)
        S.kick(g(b + 2), 0.75, pump=0.6)
        S.clap(g(b + 1), 0.6, gain=0.13)
        S.pad(t, g(b + 3), sym, 0.10, fi=0.02, fo=0.2, bright=1.1)
    riser(S, 98.5, 100.0, g, roll_from=12.5, level=1.0, low=200, high=6500)
    arp(S, g, 12, 15, [(0, "A")], vel=0.65, cresc=0.9, gain=0.22, lo=69)

    # ================= take 2: 100.4–113.6 =================
    # ---------------- 100.4–103.2 breath: the hook alone on the piano ----------------
    g = G[17]
    bch = [(0, "D"), (2, "A/C#"), (4, "G")]
    for b, name, d in HOOK[:5]:
        S.piano(g(b), nm(name), 0.46, ring=d * g.bt + 0.5, gain=0.48, bus="pianoverb")
    S.piano(g(4.0), nm("D5"), 0.42, ring=0.9, gain=0.48, bus="pianoverb")
    S.piano(g(4.5), nm("B4"), 0.40, ring=0.9, gain=0.48, bus="pianoverb")
    for b, sym in bch:
        S.piano(g(b), bass_midi(sym, 38), 0.3, ring=2 * g.bt + 0.2, gain=0.45, bus="pianoverb")
        S.piano(g(b) + 0.004, voicing(sym, 57, 66)[0], 0.24, ring=2 * g.bt + 0.2, gain=0.45, bus="pianoverb")

    # ---------------- 103.2–110.0 build ----------------
    g = G[18]
    ch = [(0, "G"), (4, "Em7"), (8, "Asus"), (10, "A")]
    S.filt_pts([(103.19, 20000.0), (103.2, 520.0), (107.7, 2400.0), (109.98, 20000.0)])
    pads(S, g, 0, 12, ch, level=0.13, bright=1.15)
    ostinato(S, g, 0, 12, ch, vel=0.42, cresc=0.2)
    arp(S, g, 2, 12, ch, vel=0.6, cresc=0.6, gain=0.24, lo=66)
    bassline(S, g, 4, 12, ch, vel=0.9)
    for b in range(4, 12):
        S.kick(g(b), 0.6 + 0.4 * (b - 4) / 7, pump=0.8)
        S.shaker(g(b + 0.5), 0.8, gain=0.07, accent=True)
    for b in range(8, 12):
        if b % 2:
            S.clap(g(b), 0.85)
    riser(S, g(6), 110.0, g, roll_from=8, level=1.15, low=150, high=7000)
    S.sweep(g(8), 110.0, 90, 600, gain=0.06)   # low swell under the riser

    # ---------------- 110.0–113.6 peak: the biggest, happiest moment ----------------
    g = G[19]
    ch = [(0, "D"), (2, "A/C#"), (4, "Bm"), (6, "G")]
    impact(S, 110.0, "D", 1.15, big=True)
    S.glock(110.0, nm("D6"), 1.0)
    S.glock(110.0, nm("A6"), 0.6)
    drums(S, g, 0, 6.5, level=3, fill=False)
    S.kick(g(6), 1.0)
    bassline(S, g, 0, 6.5, ch, vel=1.05)
    pads(S, g, 0, 6.5, ch, level=0.135, bright=1.25, hi=79)
    ostinato(S, g, 0, 6.5, ch, vel=0.5, lo=57, hi=72)
    arp(S, g, 0, 6.5, ch, vel=0.7, gain=0.24, lo=69, hi=93)
    hook(S, g, 0, HOOK_PEAK, vel=0.76, octave=True, pluck=True, glock=True, gain=0.52)
    pads(S, g, 0, 6.5, ch, level=0.07, bright=1.2, lo=74, hi=88)      # a high, wide layer only here
    for b in (1, 3, 5):
        S.snare(g(b), 0.55, gain=0.12)
    S.crash(g(4), 0.6, dur=2.0)

    # ================= take 3: 113.6–120.0 =================
    # ---------------- 113.6 the cut: one bright piano chord rings on ----------------
    g = G[20]
    S.stab(113.6, [nm("G2"), nm("D3"), nm("B3"), nm("F#4"), nm("A4"), nm("D5"), nm("F#5")], 0.62, ring=2.6, gain=0.44,
           bus="pianoverb", spread=0.006)
    S.glock(113.6, nm("B5"), 0.55, decay=1.4)
    # ---------------- 114–117.5 outro: the hook, gently, over G - A ----------------
    och = [(0, "Gmaj7"), (4, "Asus"), (5.5, "A")]
    S.pad(g(1), g(4), "Gmaj7", 0.08, fi=1.0, fo=0.3, bright=0.95)
    S.pad(g(4), g(7), "A", 0.085, fi=0.4, fo=0.3, bright=0.95)
    for b, name, d in ((2.0, "F#5", 0.75), (2.75, "E5", 0.75), (3.5, "D5", 0.5), (4.0, "E5", 1.0), (5.0, "A4", 0.75),
                       (5.75, "C#5", 0.75), (6.5, "E5", 0.5)):
        S.piano(g(b), nm(name), 0.44, ring=d * g.bt + 0.35, gain=0.46, bus="pianoverb")
    S.piano(g(4), nm("A2"), 0.32, ring=3 * g.bt, gain=0.45, bus="pianoverb")
    S.piano(g(4), nm("E3"), 0.26, ring=3 * g.bt, gain=0.45, bus="pianoverb")
    for b in np.arange(2, 7, 1.0):   # the soft pulse returns, warm
        S.bass(g(b), bass_midi(at(och, b), 33) + 12, 0.4 * g.bt, 0.4, gain=0.42, bright=0.6)
        S.kick(g(b), 0.24, pump=0.25)
    # ---------------- 117.5 resolution: D major, rings to the end ----------------
    g = G[21]
    S.stab(117.5, [nm("D2"), nm("A2"), nm("D3"), nm("F#3"), nm("A3"), nm("E4"), nm("F#4"), nm("A4"), nm("D5")], 0.55,
           ring=2.6, gain=0.44, bus="pianoverb", spread=0.008)
    S.piano(117.5, nm("F#5"), 0.5, ring=2.6, gain=0.46, bus="pianoverb")
    S.pad(117.5, 120.0, "Dadd9", 0.10, fi=0.6, fo=0.2, bright=1.0)
    S.bass(117.5, nm("D2"), 2.3, 0.55, gain=0.4, bright=0.5)
    S.glock(118.4, nm("A5"), 0.5, decay=1.2)   # the wordmark
    S.glock(118.4 + 0.27, nm("D6"), 0.45, decay=1.2)
    return S


# ---------------------------------------------------------------- mixing
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
    (13.0, 21.0, -1.5), (25.0, 28.5, -1.0), (34.5, 42.0, -1.0), (46.0, 49.5, -0.5), (56.6, 65.0, -0.5),
    (103.2, 110.0, -0.5), (110.0, 113.6, 2.0)]


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
    wet = filt(ms_width(wet, 1.3), highpass(150))
    x = dry + wet

    # fader rides: each sentence's groove a little bigger than the last; the peak clearly the biggest
    x *= db(ride_curve())[None]

    # glue + master colour: controlled lows (mono below ~120 Hz), no harsh highs
    m, s = 0.5 * (x[0] + x[1]), 0.5 * (x[0] - x[1])
    s = filt(s, highpass(120))
    x = np.stack([m + s, m - s])
    x = filt(x, highpass(32), shelf(110, -2.5, False), peak(320, -2.5, 0.8), shelf(2600, 3.0, True, 0.7),
             peak(4200, -1.0, 1.2), shelf(10500, -2.5, True))
    from dsp import compress
    x = compress(x, thresh_db=20 * np.log10(np.max(np.abs(x)) + 1e-9) - 14, ratio=2.0, attack=0.02, release=0.2, knee=8)
    # the end: fade with the picture 119.4 -> 120.0
    t = np.arange(N) / SR
    x *= np.clip((120.0 - t) / 0.6, 0, 1) ** 1.5
    # start: tiny fade so nothing clicks at 0
    x[:, :240] *= np.linspace(0, 1, 240)
    S.buses_out = B
    return x
