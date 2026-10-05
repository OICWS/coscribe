"""Sound effects, placed on the film's sound-sync events (window.FILM_EVENTS).

One palette: everything is short, rounded and band-limited (no sound above ~9 kHz carries
energy), mostly low in level, sharing one small-room reverb. Every handler accepts optional
event data: dur, gain (dB offset), vol (linear), pitch / note / f, kind, i / n.

Events the animators register override the fallback cues taken from the script's SFX column
(FALLBACK below): a fallback cue is skipped as soon as an event of the same sound family
appears inside its shot window. So new events are picked up automatically on every build.
"""
import numpy as np

import instruments as I
from dsp import (N, SR, add, bandpass, butter_lp, convolve_stereo, db, envelope, fade, filt, highpass, lowpass,
                 make_ir, midi_hz, n, pan, peak, rng, shaped_noise)

_R = rng(4242)


def _r():
    return _R


def _norm(x):
    return x / (np.max(np.abs(x)) + 1e-12)


def _t(dur):
    return np.arange(int(dur * SR)) / SR


def _damped(f, tau, dur, phase=0.0):
    t = _t(dur)
    return np.sin(2 * np.pi * f * t + phase) * np.exp(-t / tau)


def _burst(dur, tau, fc, q=0.9, seed=None, kind="bp"):
    r = rng(seed) if seed is not None else _r()
    t = _t(dur)
    x = r.standard_normal(len(t)) * np.exp(-t / tau)
    if kind == "bp":
        x = filt(x, bandpass(fc, q))
    else:
        x = filt(x, lowpass(fc, q))
    return x


# ---------------------------------------------------------------- keyboard
def key(variant=None, light=False, space=False):
    """A soft mechanical key: top tap, plastic body (three pitches), bottom-out thump, release."""
    r = _r()
    v = int(r.integers(0, 3)) if variant is None else variant
    body_f = [235, 268, 302][v] * r.uniform(0.97, 1.03)
    if space:
        body_f *= 0.62
    dur = 0.16
    tap = _burst(0.02, 0.0016, 3000 * r.uniform(0.9, 1.1), 1.4)
    body = _damped(body_f, 0.011 if not space else 0.02, dur) + 0.35 * _damped(body_f * 2.7, 0.005, dur)
    thump = _damped(95 * r.uniform(0.95, 1.05), 0.008, dur)
    x = np.zeros(int(dur * SR))
    x[:len(tap)] += 0.45 * _norm(tap)
    x += 0.8 * body + 0.5 * thump
    if space:  # stabiliser rattle
        rat = _burst(0.05, 0.012, 1400, 2.0)
        i = int(0.004 * SR)
        x[i:i + len(rat)] += 0.25 * _norm(rat)
    x *= 1 - np.exp(-_t(dur) / 0.0004)
    x = _norm(filt(x, lowpass(6500, 0.6), highpass(70)))
    # release click 70-110 ms later, quieter and higher
    rel = 0.28 * _norm(_burst(0.03, 0.0012, 2400, 1.2) + 0.6 * _damped(body_f * 1.35, 0.004, 0.03))
    i = int(r.uniform(0.07, 0.11) * SR)
    y = np.zeros(max(len(x), i + len(rel)) + 10)
    y[:len(x)] += x
    y[i:i + len(rel)] += rel
    y *= db(r.uniform(-2.5, 1.0)) * (0.55 if light else 1.0)
    return fade(y, 0.0, 0.01)


def enter():
    """Enter: heavier, lower, a little longer, with the stabiliser."""
    t = _t(0.3)
    body = _damped(150, 0.025, 0.3) + 0.4 * _damped(320, 0.012, 0.3) + 0.6 * _damped(62, 0.03, 0.3)
    tap = np.zeros(len(t))
    b = _norm(_burst(0.025, 0.002, 2200, 1.1))
    tap[:len(b)] = b
    rat = np.zeros(len(t))
    rr = _norm(_burst(0.06, 0.01, 1100, 2.0))
    rat[int(0.006 * SR):int(0.006 * SR) + len(rr)] = rr
    x = body + 0.35 * tap + 0.2 * rat
    x *= 1 - np.exp(-t / 0.0006)
    x = _norm(filt(x, lowpass(5000, 0.6), highpass(45)))
    rel = 0.22 * _norm(_burst(0.03, 0.0015, 1800, 1.2))
    y = np.zeros(int(0.42 * SR))
    y[:len(x)] += x
    i = int(0.13 * SR)
    y[i:i + len(rel)] += rel
    return fade(y, 0.0, 0.02)


# ---------------------------------------------------------------- air
def whoosh(dur=1.0, lo=180, hi=900, reverse=False, hum=False, seed=None):
    """Low-pass noise whoosh: a band that rises and falls with a swell; longer = lower."""
    s = int(_r().integers(0, 1 << 20)) if seed is None else seed

    def spec(t, f):
        k = t / dur
        bell = np.sin(np.pi * np.clip(k, 0, 1)) ** 1.6
        fc = lo * (hi / lo) ** (np.sin(np.pi * np.clip(k, 0, 1) * 0.9))
        g = 1 / (1 + (f / fc) ** 4) * (f / 40) ** 2 / (1 + (f / 40) ** 2)
        return g * (0.02 + bell)
    l = shaped_noise(dur, spec, s)
    rr = shaped_noise(dur, spec, s + 1)
    x = np.stack([l, rr])
    k = np.linspace(0, 1, x.shape[1])
    x *= (np.sin(np.pi * k) ** 1.4)[None]
    # a sense of passing: slight left -> right movement
    p = np.linspace(-0.35, 0.35, x.shape[1])
    x[0] *= np.cos((p + 1) * np.pi / 4) * np.sqrt(2)
    x[1] *= np.sin((p + 1) * np.pi / 4) * np.sqrt(2)
    if hum:
        t = np.arange(x.shape[1]) / SR
        h = (np.sin(2 * np.pi * 110 * t) + 0.5 * np.sin(2 * np.pi * 220.7 * t)) * np.sin(2 * np.pi * 31 * t)
        h *= np.sin(np.pi * k) ** 2 * 0.18
        x += h[None]
    x = filt(x, highpass(35))
    x = _norm(x)
    if reverse:
        x = x[:, ::-1]
        # reversed: swell that ends abruptly (the collapse), with a tiny release
        e = np.linspace(0, 1, x.shape[1]) ** 2.2
        x = x * e[None]
        x = fade(_norm(x), 0.05, 0.004)
    return fade(x, 0.02, 0.03)


# ---------------------------------------------------------------- small mechanics
def tick(pitch=1.0):
    """Cell tick: a 40 ms soft dot."""
    r = _r()
    f = 1900 * pitch * r.uniform(0.9, 1.12)
    x = _damped(f, 0.006, 0.04)
    b = _norm(_burst(0.008, 0.0009, f * 1.4, 2.0))
    x[:len(b)] += 0.35 * b
    x *= 1 - np.exp(-_t(0.04) / 0.0003)
    return fade(_norm(filt(x, lowpass(7000))) * db(r.uniform(-4, 0)), 0.0, 0.006)


def snap():
    """Column snaps into alignment: a soft plastic double click with a low body."""
    x = np.zeros(int(0.12 * SR))
    for j, (dt, g) in enumerate(((0.0, 1.0), (0.014, 0.6))):
        c = _norm(_burst(0.03, 0.002, 1500 - 300 * j, 1.5)) * g + 0.5 * g * _damped(420, 0.01, 0.03)
        i = int(dt * SR)
        x[i:i + len(c)] += c
    x += 0.4 * np.pad(_damped(130, 0.015, 0.06), (0, len(x) - int(0.06 * SR)))
    return fade(_norm(filt(x, lowpass(6000), highpass(80))), 0.0, 0.01)


def knock(pitch=1.0):
    """Short wooden knock (layout blocks landing): woodblock-ish but felted."""
    r = _r()
    f = 420 * pitch * r.uniform(0.92, 1.08)
    x = _damped(f, 0.018, 0.12) + 0.4 * _damped(f * 2.6, 0.007, 0.12) + 0.3 * _damped(f * 0.5, 0.02, 0.12)
    b = _norm(_burst(0.01, 0.001, 2000, 1.0))
    x[:len(b)] += 0.25 * b
    x *= 1 - np.exp(-_t(0.12) / 0.0005)
    return fade(_norm(filt(x, lowpass(5000))), 0.0, 0.01)


def settle():
    """Chip lands in the input box: a light wooden 'tok' with a soft low body."""
    x = _damped(330, 0.02, 0.25) + 0.5 * _damped(165, 0.04, 0.25) + 0.2 * _damped(860, 0.008, 0.25)
    b = _norm(_burst(0.012, 0.0012, 1500, 0.9))
    x[:len(b)] += 0.2 * b
    x *= 1 - np.exp(-_t(0.25) / 0.0008)
    return fade(_norm(filt(x, lowpass(4500))), 0.0, 0.03)


def click():
    """A clean UI click (press + release)."""
    x = np.zeros(int(0.1 * SR))
    for dt, f, g in ((0.0, 2600, 1.0), (0.055, 3100, 0.45)):
        c = _norm(_burst(0.012, 0.0009, f, 1.6)) * g + g * 0.6 * _damped(f * 0.32, 0.004, 0.012)
        i = int(dt * SR)
        x[i:i + len(c)] += c
    return fade(_norm(filt(x, lowpass(7500), highpass(200))), 0.0, 0.01)


def scratch(dur=0.6):
    """Pencil stroke left to right: grainy band-limited noise with stroke envelope."""
    r = _r()
    t = _t(dur)
    x = r.standard_normal(len(t))
    grains = np.clip(filt(r.standard_normal(len(t)), lowpass(60)) * 6 + 0.5, 0, None)
    x = filt(x * grains, bandpass(2600, 0.7), lowpass(6500))
    e = envelope([(0, 0), (0.04, 1), (dur * 0.7, 0.8), (dur, 0)], len(t))
    x = _norm(x * e)
    p = np.linspace(-0.4, 0.4, len(x))
    return fade(np.stack([x * np.cos((p + 1) * np.pi / 4), x * np.sin((p + 1) * np.pi / 4)]) * np.sqrt(2), 0.01, 0.02)


def paper(kind="flip", dur=None):
    """Paper: flip / peel / slide / fall / pull. Crinkle-modulated noise in 600 Hz - 6 kHz."""
    r = _r()
    d = dur or {"flip": 0.35, "peel": 0.7, "slide": 0.5, "fall": 0.3, "pull": 0.55, "lift": 0.55}.get(kind, 0.4)
    t = _t(d)
    x = r.standard_normal(len(t))
    crinkle = np.abs(filt(r.standard_normal(len(t)), lowpass(90 if kind in ("peel", "pull", "lift") else 40))) * 4
    fc = {"flip": 2200, "peel": 3000, "slide": 1500, "fall": 1800, "pull": 2600, "lift": 2600}.get(kind, 2000)
    x = filt(x * (0.4 + crinkle), bandpass(fc, 0.6), lowpass(6500), highpass(300))
    if kind == "flip":
        e = envelope([(0, 0), (0.06, 1), (0.12, 0.5), (d, 0)], len(t))
    elif kind == "fall":
        e = envelope([(0, 0), (d * 0.6, 0.4), (d * 0.85, 1), (d, 0)], len(t))
    elif kind == "slide":
        e = envelope([(0, 0), (d * 0.3, 1), (d * 0.75, 0.7), (d, 0)], len(t))
    else:
        e = envelope([(0, 0), (d * 0.5, 1), (d * 0.8, 0.6), (d, 0)], len(t))
    x = _norm(x * e)
    if kind == "fall":  # the soft slap of a page landing
        s = _norm(_burst(0.04, 0.006, 900, 0.8))
        i = int(d * 0.8 * SR)
        x[i:i + len(s)] += 0.35 * s[:len(x) - i]
    w = r.uniform(-0.3, 0.3)
    return fade(pan(_norm(x), w), 0.005, 0.02)


def flutter(dur=4.0):
    """Continuous low rustle of pages streaming past."""
    r = _r()
    t = _t(dur)
    x = r.standard_normal(len(t))
    am = 0.5 + 0.5 * np.sin(2 * np.pi * 7.5 * t + 2 * np.sin(2 * np.pi * 0.7 * t))
    x = filt(x * (0.3 + am), bandpass(900, 0.6), lowpass(3500))
    e = envelope([(0, 0), (0.5, 1), (dur - 0.6, 1), (dur, 0)], len(t))
    return fade(np.stack([_norm(x * e), _norm(np.roll(x, 777) * e)]), 0.05, 0.05)


def silk(dur=0.7, pitch=1.0):
    """A fine thread pulled taut: soft airy band gliding up, faint pitched core."""
    t = _t(dur)
    r = _r()
    f = 1200 * pitch * (1 + 0.5 * t / dur)
    core = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.15
    x = r.standard_normal(len(t))
    x = filt(x, bandpass(2400 * pitch, 1.4))
    e = envelope([(0, 0), (dur * 0.6, 1), (dur, 0)], len(t))
    return fade(pan(_norm((_norm(x) + core) * e), r.uniform(-0.3, 0.3)), 0.01, 0.03)


def chime(note="A5", vel=1.0):
    """Pass chime: sine + 2nd and 3rd harmonics, soft attack, long gentle tail."""
    f = float(midi_hz(n(note) if isinstance(note, str) else note))
    t = _t(2.5)
    x = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.9) + 0.3 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.45) \
        + 0.12 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / 0.25)
    x *= 1 - np.exp(-t / 0.002)
    return fade(_norm(x) * vel, 0.0, 0.2)


def thud():
    """Test failure: low, dull, felt."""
    t = _t(0.9)
    f = 58 * (1 + 0.6 * np.exp(-t / 0.04))
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.18)
    b = _norm(_burst(0.08, 0.012, 300, 0.7, kind="lp"))
    x[:len(b)] += 0.4 * b
    x *= 1 - np.exp(-t / 0.002)
    return fade(_norm(filt(x, lowpass(900))), 0.0, 0.05)


def stack():
    """Run records stacking: thick soft 'dum'."""
    t = _t(0.7)
    f = 72 * (1 + 0.4 * np.exp(-t / 0.03))
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.14) + 0.4 * _damped(190, 0.03, 0.7)
    b = _norm(_burst(0.05, 0.006, 700, 0.8, kind="lp"))
    x[:len(b)] += 0.3 * b
    x *= 1 - np.exp(-t / 0.0015)
    return fade(_norm(filt(x, lowpass(1500))), 0.0, 0.05)


def clock():
    """A clock 'tock' for each Monday."""
    x = _damped(1250, 0.006, 0.08) + 0.6 * _damped(640, 0.012, 0.08) + 0.3 * _damped(2900, 0.003, 0.08)
    x *= 1 - np.exp(-_t(0.08) / 0.0003)
    return fade(_norm(filt(x, lowpass(6000))), 0.0, 0.01)


def bell(note="A4"):
    """A small soft bell (amber clock: waiting for a person)."""
    f = float(midi_hz(n(note)))
    t = _t(3.0)
    x = np.zeros(len(t))
    for ratio, a, tau in ((1.0, 1.0, 1.4), (2.0, 0.35, 0.8), (2.4, 0.25, 0.6), (3.0, 0.12, 0.4), (4.07, 0.06, 0.25)):
        x += a * np.sin(2 * np.pi * f * ratio * t) * np.exp(-t / tau)
    x *= 1 - np.exp(-t / 0.002)
    return fade(_norm(filt(x, lowpass(6000))), 0.0, 0.3)


def pop(i=0):
    """Rows floating up: a light rounded 'bloop', higher each time."""
    f = 520 * 2 ** (i * 4 / 12)
    t = _t(0.25)
    fr = f * (1 + 0.15 * (1 - np.exp(-t / 0.03)))
    x = np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.exp(-t / 0.06) * (1 - np.exp(-t / 0.004))
    return fade(_norm(x), 0.0, 0.03)


def gliss(dur=1.2, lo="D4", hi="A4"):
    """Chart growing: a thin string glissando (soft harmonic, sul tasto)."""
    m0, m1 = n(lo), n(hi)
    t = _t(dur)
    m = m0 + (m1 - m0) * (0.5 - 0.5 * np.cos(np.pi * t / dur))
    f = midi_hz(m)
    ph = 2 * np.pi * np.cumsum(f * (1 + 0.004 * np.sin(2 * np.pi * 5.3 * t))) / SR
    x = np.sin(ph) + 0.3 * np.sin(2 * ph) + 0.1 * np.sin(3 * ph)
    e = envelope([(0, 0), (0.25, 1), (dur - 0.3, 0.8), (dur, 0)], len(t))
    return fade(pan(_norm(x * e), 0.2), 0.02, 0.05)


def chord(notes=("D5", "A5", "F5")):
    """A soft consonance (a line lights up): glass + sine dyad/triad."""
    y = None
    for i, nt in enumerate(notes):
        g = I.glass(n(nt), 0.6, decay=1.0, seed=i)
        y = g if y is None else (np.pad(y, ((0, 0), (0, max(0, g.shape[1] - y.shape[1])))) + np.pad(g, ((0, 0), (0, max(0, y.shape[1] - g.shape[1])))))
    return _norm(y)


def roll(dur=1.0, rate=22):
    """Numbers rolling: a quick train of tiny ticks slowing down."""
    L = int((dur + 0.1) * SR)
    y = np.zeros(L)
    t = 0.0
    k = 0
    while t < dur:
        tk = tick(1.25)
        i = int(t * SR)
        y[i:i + len(tk)] += tk[:L - i] * (0.8 - 0.5 * t / dur)
        k += 1
        t += 1 / (rate * (1 - 0.6 * t / dur))
    return fade(_norm(y), 0.0, 0.02)


def ticks_run(dur=0.6, count=12):
    L = int((dur + 0.1) * SR)
    y = np.zeros(L)
    for j in range(count):
        tk = tick(1.35 + 0.02 * j)
        i = int(j * dur / count * SR)
        y[i:i + len(tk)] += tk[:L - i] * 0.7
    return _norm(y)


def taut(note="D2"):
    """The line pulled taut: a short low bowed tension, slightly sul ponticello."""
    x = I.strings(n(note), 1.3, [(0, 0), (0.25, 1), (0.9, 0.7), (1.3, 0)], kind="vc", seed=7, bright=1.6)
    return _norm(x)


def ui(dur=0.28):
    """A UI fragment rises / a tool row passes: a very soft airy swish."""
    r = _r()
    t = _t(dur)
    x = filt(r.standard_normal(len(t)), bandpass(1800, 0.8), lowpass(5000))
    e = envelope([(0, 0), (dur * 0.4, 1), (dur, 0)], len(t))
    return fade(pan(_norm(x * e), r.uniform(-0.3, 0.3)), 0.005, 0.02)


def dust(dur=1.2):
    """Rows dissolve into fine dust: a sparse, soft, dark granular shimmer that thins out."""
    r = _r()
    L = int((dur + 0.1) * SR)
    y = np.zeros((2, L))
    count = int(60 * dur)
    for j in range(count):
        tt = dur * (j / count) ** 1.6
        g = _norm(_burst(0.012, 0.002, r.uniform(1500, 3800), 2.5)) * (1 - tt / dur) ** 1.5 * r.uniform(0.3, 1)
        i = int(tt * SR)
        y[:, i:i + len(g)] += pan(g, r.uniform(-0.7, 0.7))[:, :L - i]
    body = filt(r.standard_normal(L), lowpass(500)) * envelope([(0, 0), (0.1, 1), (dur, 0)], L)
    y += 0.6 * _norm(body)[None]
    return fade(_norm(y), 0.005, 0.05)


def hum(dur=1.2):
    """Faint digital hum (ring-modulated) under the code dive."""
    t = _t(dur)
    h = (np.sin(2 * np.pi * 110 * t) + 0.5 * np.sin(2 * np.pi * 220.7 * t) + 0.2 * np.sin(2 * np.pi * 330 * t))
    h *= 0.6 + 0.4 * np.sin(2 * np.pi * 31 * t)
    e = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.5
    h = filt(h * e, lowpass(2500))
    return fade(np.stack([h, np.roll(h, 37)]) / (np.max(np.abs(h)) + 1e-9), 0.02, 0.05)


def room(level_pts):
    """Room tone for the whole film: soft, dark, slowly breathing. level_pts: [(t, dB)]."""
    r = rng(99)
    x = r.standard_normal((2, N))
    x = filt(x, lowpass(1400, 0.5), highpass(45), peak(200, 3, 0.7))
    x /= np.std(x)
    br = 1 + 0.15 * np.sin(2 * np.pi * np.arange(N) / SR / 9.0)
    lv = db(envelope(level_pts, N))
    return x * (br * lv)[None]


# ---------------------------------------------------------------- dispatcher
def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _note(e, default):
    """A pitch from event data, only if it really is a note name (animators use 'note' for comments)."""
    import re
    for k in ("pitch_note", "note"):
        v = e.get(k)
        if isinstance(v, str) and re.fullmatch(r"[A-G][b#]?-?\d", v.strip()):
            return v.strip()
    return default


def _pitch(e, default=1.0):
    v = e.get("pitch", default)
    if isinstance(v, str):
        return {"low": 0.8, "lower": 0.75, "mid": 1.0, "high": 1.25}.get(v.lower(), default)
    return _num(v, default)


def _g(e, base_db):
    """Event level: base dB for the family, then the animators' optional gain (linear 0..3, or dB if
    negative / larger), vol (linear), soft (-6 dB) and big (+4 dB)."""
    g = db(base_db)
    if "gain" in e:
        v = _num(e["gain"], 1.0)
        g *= v if 0 < v <= 3 else db(v)
    g *= _num(e.get("vol", 1.0), 1.0)
    if e.get("soft"):
        g *= db(-6)
    if e.get("big"):
        g *= db(4)
    return g


def _key_is_surface(e, events):
    """Keys typed into the input box (followed by an Enter with no dive in between) vs keys inside a depth."""
    for f in events:
        if f["t"] <= e["t"]:
            continue
        if f["t"] - e["t"] > 6.0:
            return False
        if f["name"] == "enter":
            return True
        if f["name"] == "dive":
            return False
    return False


FAMILY = {  # event name -> sound family (synonyms the animators use or might use)
    "key": "key", "type": "key",
    "keys": "keyburst", "backspace": "keyburst", "typing": "keyburst",
    "enter": "enter", "return": "enter", "send": "enter",
    "dive": "dive", "whoosh": "whoosh", "swoosh": "whoosh", "frames": "ui", "pullback": "pullback",
    "rewhoosh": "rewhoosh", "reverse": "rewhoosh", "collapse": "rewhoosh", "fold": "rewhoosh",
    "tick": "tick", "cell": "tick", "tick_low": "tick", "ticks": "ticks", "steps": "ticks",
    "snap": "snap", "align": "snap",
    "knock": "knock", "wood": "knock", "layout": "knock", "word": "word",
    "softkey": "softkey", "title": "softkey",
    "drop": "settle", "land": "settle", "settle": "settle",
    "stack": "stack", "thump": "stack",
    "click": "click", "approve": "click",
    "scratch": "scratch", "pencil": "scratch", "strike": "scratch",
    "paper": "paper", "page": "paper", "flip": "paper", "peel": "paper", "slide": "paper", "lift": "paper",
    "paper_lift": "paper", "paper_slide": "paper", "paper_fan": "paper",
    "flutter": "flutter", "pages": "flutter", "paper_flow": "flutter",
    "silk": "silk", "thread": "silk",
    "chime": "chime", "check": "check", "ding": "check",
    "thud": "thud", "fail": "thud",
    "clock": "clock", "bell": "bell",
    "pop": "pop",
    "gliss": "gliss", "grow": "gliss", "chart": "gliss",
    "chord": "chord", "harmony": "chord",
    "roll": "roll", "scroll": "scroll",
    "tap": "tap",
    "taut": "taut", "string": "taut",
    "glass": "glass", "crystal": "glass",
    "ui": "ui", "row": "ui", "card": "ui",
    "dust": "dust", "hum": "hum",
    "cut": "music", "stop": "music", "silence": "music", "riser": "music", "hit": "music",
    # picture-only events: the score already plays these moments (117.5 F-major pad, 119.4 fade)
    "wordmark": "none", "logo": "none", "fade": "none", "fadeout": "none", "fade_out": "none", "black": "none",
}
# substring heuristics for names nobody has mapped yet (checked in order); the rest get a soft tick
GUESS = [("whoosh", "whoosh"), ("swish", "whoosh"), ("dive", "dive"), ("key", "key"), ("type", "keyburst"),
         ("enter", "enter"), ("paper", "paper"), ("page", "paper"), ("glass", "glass"), ("crystal", "glass"),
         ("chime", "chime"), ("bell", "bell"), ("ding", "check"), ("tick", "tick"), ("click", "click"),
         ("snap", "snap"), ("knock", "knock"), ("wood", "knock"), ("thud", "thud"), ("drop", "settle"),
         ("land", "settle"), ("silk", "silk"), ("thread", "silk"), ("line", "silk"), ("pop", "pop"),
         ("tap", "tap"), ("ui", "ui"), ("card", "ui"), ("row", "ui"), ("dust", "dust"), ("hum", "hum"),
         ("stack", "stack"), ("clock", "clock"), ("scroll", "scroll"), ("riser", "music"), ("hit", "music")]
# events that belong to the score (montage hits, the riser, the stops): no SFX for them
SKIP_GROUP = {"ticks": "tick", "tap": "tick"}  # for fallback skipping: these families count as one

GLASS_NOTES = ["D6", "A5", "F6", "E6", "A6", "C6", "D6", "G6", "F6", "A5", "E6", "D6", "C6", "A5", "F5", "D5"]


def family(e):
    nm = e["name"]
    f = FAMILY.get(nm) or FAMILY.get(nm.lower()) or FAMILY.get(nm.lower().split("_")[0])
    if f is None:
        f = next((fam for sub, fam in GUESS if sub in nm.lower()), None)
    if f == "settle" and ("ring" in e or e.get("big")):
        return "stack"  # records stacking in the climax are 'drop' events with a ring index
    if f == "whoosh" and e.get("reverse"):
        return "rewhoosh"
    return f


def _density(e, events, fam, win=0.5):
    """Level compensation for dense clusters of the same family (many ticks/glass notes at once)."""
    k = sum(1 for f in events if abs(f["t"] - e["t"]) < win / 2 and family(f) == fam)
    return 1 / np.sqrt(max(1, k / 3))


def place(buf, e, events, unknown):
    t = float(e["t"])
    fam = family(e)
    r = _r()
    if fam == "key":
        ch = e.get("ch", "")
        surf = _key_is_surface(e, events)
        x = key(space=(ch == " "), light=not surf)
        add(buf, pan(x, r.uniform(-0.12, 0.12)), t, _g(e, -27 if surf else -33))
    elif fam == "keyburst":
        d = _num(e.get("dur"), 0.3)
        cnt = int(_num(e.get("n", e.get("count")), 3))
        if e["name"] == "backspace":
            d = max(d, 0.09 * cnt)
        k = max(1, min(cnt, int(round(d * 26)) + 1))
        for j in range(k):
            tj = t + d * (j + r.uniform(-0.25, 0.25)) / k
            add(buf, pan(key(light=True), r.uniform(-0.15, 0.15)), max(t, tj), _g(e, -36) * db(r.uniform(-3, 0)))
    elif fam == "enter":
        add(buf, pan(enter(), 0.0), t, _g(e, -19))
    elif fam == "dive":
        d = _num(e.get("dur"), 1.0)
        has_hum = any(f["name"] == "hum" and abs(f["t"] - t) < 1.0 for f in events)
        hm = bool(e.get("hum", (76 < t < 79) and not has_hum))
        lo = 150 * (1.2 / max(d, 0.6)) ** 0.8
        add(buf, whoosh(d + 0.25, lo, lo * 5.5, hum=hm), t - 0.05, _g(e, -17))
    elif fam == "whoosh":
        d = _num(e.get("dur"), 0.7)
        add(buf, whoosh(d, 220, 1400), t, _g(e, -26))
    elif fam == "pullback":
        d = _num(e.get("dur"), 2.0)
        add(buf, whoosh(d, 110, 520), t, _g(e, -24))
    elif fam == "rewhoosh":
        d = _num(e.get("dur"), 0.7)
        add(buf, whoosh(d, 160, 1600, reverse=True), t, _g(e, -22 if d > 0.55 else -25))
    elif fam == "tick":
        p = _pitch(e, 0.75 if e["name"] == "tick_low" else 1.0)
        add(buf, pan(tick(p), r.uniform(-0.5, 0.5)), t, _g(e, -37) * _density(e, events, fam))
    elif fam == "ticks":
        d = _num(e.get("dur"), 0.6)
        cnt = int(_num(e.get("n", e.get("count")), 12))
        cnt = max(2, min(cnt, int(d * 30)))
        add(buf, pan(ticks_run(d, cnt), r.uniform(-0.2, 0.2)), t, _g(e, -37))
    elif fam == "snap":
        add(buf, pan(snap(), r.uniform(-0.2, 0.2)), t, _g(e, -29))
    elif fam == "knock":
        add(buf, pan(knock(_pitch(e)), r.uniform(-0.4, 0.4)), t, _g(e, -31))
    elif fam == "word":
        add(buf, pan(knock(0.62), r.uniform(-0.2, 0.2)), t, _g(e, -30))
    elif fam == "softkey":
        add(buf, pan(key(light=True), r.uniform(-0.3, 0.3)), t, _g(e, -32))
    elif fam == "settle":
        add(buf, pan(settle(), -0.1), t, _g(e, -25))
    elif fam == "stack":
        add(buf, pan(stack(), r.uniform(-0.2, 0.2)), t, _g(e, -25))
    elif fam == "click":
        add(buf, pan(click(), 0.15), t, _g(e, -22))
    elif fam == "scratch":
        add(buf, scratch(_num(e.get("dur"), 0.6)), t, _g(e, -32))
    elif fam == "paper":
        nm = e["name"]
        kind = e.get("kind") if e.get("kind") in ("flip", "peel", "slide", "fall", "pull", "lift") else \
            {"slide": "slide", "paper_slide": "slide", "peel": "peel", "lift": "lift", "paper_lift": "pull", "paper_fan": "slide"}.get(nm, "fall" if "i" in e else "flip")
        add(buf, paper(kind, e.get("dur")), t, _g(e, -31) * _density(e, events, fam, 0.8))
    elif fam == "flutter":
        add(buf, flutter(_num(e.get("dur"), 4.0)), t, _g(e, -38))
    elif fam == "silk":
        add(buf, silk(_num(e.get("dur"), 0.6), _pitch(e)), t, _g(e, -35) * _density(e, events, fam))
    elif fam == "chime":
        add(buf, pan(chime(_note(e, "A5")), 0.1), t, _g(e, -26))
    elif fam == "check":
        add(buf, pan(chime(_note(e, "D6"), 0.6), 0.2), t, _g(e, -35 if e["name"] == "ding" else -33))
    elif fam == "thud":
        add(buf, pan(thud(), 0.0), t, _g(e, -20))
    elif fam == "clock":
        add(buf, pan(clock(), -0.25), t, _g(e, -29))
    elif fam == "bell":
        add(buf, pan(bell(_note(e, "A4")), 0.2), t, _g(e, -28))
    elif fam == "pop":
        add(buf, pan(pop(int(_num(e.get("i", e.get("step")), 0))), 0.1), t, _g(e, -31))
    elif fam == "gliss":
        add(buf, gliss(_num(e.get("dur"), 1.2)), t, _g(e, -33))
    elif fam == "chord":
        add(buf, chord(), t, _g(e, -31))
    elif fam == "roll":
        add(buf, pan(roll(_num(e.get("dur"), 1.0)), 0.25), t, _g(e, -35))
    elif fam == "scroll":
        add(buf, paper("slide", _num(e.get("dur"), 0.8)), t, _g(e, -33))
    elif fam == "tap":
        add(buf, pan(knock(1.6), r.uniform(-0.3, 0.3)), t, _g(e, -34))
    elif fam == "taut":
        add(buf, taut(_note(e, "A2" if t < 90 else "D2")), t, _g(e, -31))
    elif fam == "glass":
        i = int(_num(e.get("i"), sum(1 for f in events if f["name"] == e["name"] and f["t"] < t)))
        nt = _note(e, GLASS_NOTES[i % len(GLASS_NOTES)])
        add(buf, I.glass(n(nt), 0.6, decay=1.2, seed=i, p=float(np.clip(-0.5 + 0.08 * i, -0.6, 0.6))), t,
            _g(e, -27) * _density(e, events, fam))
    elif fam == "ui":
        add(buf, ui(), t, _g(e, -36))
    elif fam == "dust":
        add(buf, dust(_num(e.get("dur"), 1.2)), t, _g(e, -34))
    elif fam == "hum":
        add(buf, hum(_num(e.get("dur"), 1.2)), t, _g(e, -34))
    elif fam in ("music", "none"):
        pass  # the score plays these (montage hits, riser, wordmark) or they are silences (stop / cut / fade)
    else:
        unknown.add(e["name"])
        add(buf, pan(tick(0.8), 0.0), t, _g(e, -38))
    return fam


# ---------------------------------------------------------------- fallback cues from the script's SFX column
def _keys(t0, dur, count, seed):
    r = rng(seed)
    gaps = 0.75 + r.random(count) * 0.5
    acc = np.cumsum(gaps) / gaps.sum()
    return [{"t": float(t0 + a * dur), "name": "key", "ch": "x"} for a in acc]


def fallback_cues():
    """(window_a, window_b, family, [events]). Skipped when that family already has events in the window."""
    r = rng(17)
    cues = []
    cues.append((13.0, 17.0, "tick", [{"t": float(t), "name": "tick", "pitch": float(r.uniform(0.85, 1.2))}
                                      for t in np.sort(r.uniform(13.4, 16.8, 34))]))
    cues.append((13.0, 17.0, "snap", [{"t": 14.6, "name": "snap"}, {"t": 15.7, "name": "snap"}]))
    cues.append((17.0, 21.0, "scratch", [{"t": 17.9, "name": "scratch", "dur": 0.7}, {"t": 19.1, "name": "scratch", "dur": 0.7}]))
    cues.append((17.0, 21.0, "chord", [{"t": 20.2, "name": "chord"}]))
    cues.append((21.0, 25.0, "paper", [{"t": 22.2, "name": "paper", "kind": "peel", "gain": -4}]))
    cues.append((21.0, 25.0, "roll", [{"t": 23.1, "name": "roll", "dur": 1.0}]))
    cues.append((25.0, 28.5, "gliss", [{"t": 25.6, "name": "gliss", "dur": 1.6}]))
    cues.append((28.5, 30.0, "settle", [{"t": 29.5, "name": "drop"}]))
    cues.append((34.5, 38.0, "softkey", [{"t": float(t), "name": "softkey"} for t in np.linspace(35.1, 37.7, 9)]))
    cues.append((38.0, 42.0, "knock", [{"t": float(t), "name": "knock", "pitch": float(p)} for t, p in
                                       zip((38.25, 38.7, 39.05, 39.6, 40.1, 40.75), (1.0, 1.15, 0.9, 1.05, 1.2, 0.95))]))
    cues.append((38.0, 42.0, "chord", [{"t": 41.0, "name": "chord", "gain": -3}]))
    cues.append((42.0, 44.0, "silk", [{"t": 42.3, "name": "silk", "dur": 1.1}]))
    cues.append((44.0, 46.0, "roll", [{"t": 44.2, "name": "roll", "dur": 0.8}]))
    cues.append((44.0, 46.0, "check", [{"t": 45.3, "name": "check"}]))
    cues.append((46.0, 49.5, "paper", [{"t": float(t), "name": "paper", "kind": "fall", "gain": -1.5 * i}
                                       for i, t in enumerate(np.linspace(46.3, 48.9, 9))]))
    cues.append((49.5, 52.0, "paper", [{"t": 50.6, "name": "paper", "kind": "slide"}]))
    cues.append((56.6, 61.0, "flutter", [{"t": 56.8, "name": "flutter", "dur": 4.2}]))
    cues.append((56.6, 61.0, "tap", [{"t": float(t), "name": "tap"} for t in (57.6, 58.3, 58.9, 59.6, 60.3)]))
    cues.append((61.0, 65.0, "silk", [{"t": 61.6, "name": "silk"}, {"t": 62.4, "name": "silk"}, {"t": 63.1, "name": "silk"},
                                      {"t": 63.8, "name": "silk", "pitch": 0.8}]))
    cues.append((65.0, 67.0, "paper", [{"t": 65.3, "name": "paper", "kind": "pull", "gain": -2}]))
    cues.append((67.0, 69.5, "check", [{"t": 67.4, "name": "check", "note": "A5", "gain": -3}]))
    cues.append((69.5, 72.5, "paper", [{"t": t, "name": "paper", "kind": "flip", "gain": g}
                                       for t, g in ((69.9, 0), (70.35, -3), (70.8, -6))]))
    cues.append((72.5, 74.0, "settle", [{"t": 73.4, "name": "drop"}]))
    cues.append((78.6, 82.5, "tap", [{"t": t, "name": "tap"} for t in (79.6, 80.4, 81.1, 81.8)]))
    cues.append((82.0, 83.0, "thud", [{"t": 82.5, "name": "thud"}]))
    cues.append((83.5, 84.5, "chime", [{"t": 84.0, "name": "chime"}]))
    cues.append((86.0, 88.5, "click", [{"t": 87.9, "name": "click"}]))
    cues.append((88.5, 92.5, "pop", [{"t": t, "name": "pop", "i": i} for i, t in enumerate((90.0, 90.45, 90.9))]))
    cues.append((92.5, 94.0, "settle", [{"t": 93.4, "name": "drop"}]))
    cues.append((94.0, 95.5, "scroll", [{"t": 94.2, "name": "scroll", "dur": 0.9}]))
    cues.append((97.0, 98.5, "key", [{"t": 97.75, "name": "key", "ch": "3"}]))
    cues.append((100.0, 103.2, "key", _keys(100.4, 2.6, 12, 5)))
    cues.append((102.5, 103.5, "enter", [{"t": 103.1, "name": "enter"}]))
    cues.append((103.2, 106.0, "taut", [{"t": 104.9, "name": "taut"}]))
    cues.append((106.0, 110.0, "clock", [{"t": t, "name": "clock"} for t in (106.6, 107.4, 108.2, 109.0, 109.8)]))
    cues.append((106.0, 110.0, "ticks", [{"t": t + 0.05, "name": "ticks", "dur": 0.55, "n": 12}
                                         for t in (106.6, 107.4, 109.0, 109.8)]))
    cues.append((106.0, 110.0, "bell", [{"t": 108.3, "name": "bell"}]))
    cues.append((110.0, 113.6, "stack", [{"t": t, "name": "stack", "gain": g} for t, g in ((110.9, -2), (111.8, -3), (112.7, -4))]))
    cues.append((112.0, 113.7, "rewhoosh", [{"t": 113.6, "name": "rewhoosh", "dur": 1.3}]))
    return cues


def resolve(events):
    """Live events + fallback cues for families that have no events in that shot window."""
    fams = [(e["t"], SKIP_GROUP.get(family(e), family(e))) for e in events]
    out = list(events)
    used = []
    for a, b, fam, evs in fallback_cues():
        if any(a - 1e-6 <= t <= b + 1e-6 and f == SKIP_GROUP.get(fam, fam) for t, f in fams):
            continue
        out.extend(evs)
        used.append((a, b, fam, len(evs)))
    out.sort(key=lambda e: e["t"])
    return out, used


def render(events):
    """SFX stem for one language: (2, N) float64, plus a report."""
    global _R
    _R = rng(4242)
    evs, used = resolve(events)
    dry = np.zeros((2, N))
    unknown = set()
    counts = {}
    for e in evs:
        if not (0 <= e["t"] < 120):
            continue
        fam = place(dry, e, evs, unknown)
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    ir = make_ir(dur=1.2, t60_low=0.7, t60_high=0.35, predelay=0.008, seed=23, width=0.8, hp=150, lp=7000)
    wet = convolve_stereo(dry, ir)
    x = dry + db(-15) * wet
    # room tone, a little more present in the drops ("only the room is left")
    x += room([(0, -90), (0.6, -90), (2.0, -66), (20.8, -66), (21.3, -63), (24.0, -63), (24.5, -66),
               (64.8, -66), (65.2, -62), (67.0, -62), (67.6, -66), (99.95, -66), (100.0, -120), (100.38, -120),
               (100.6, -66), (119.4, -66), (120, -90)])
    # vacuum 100.0–100.38 and the scissor cut at 113.6 (everything but the room stops)
    t = np.arange(N) / SR
    g = np.ones(N)
    g[(t >= 100.0) & (t < 100.38)] = 0
    g[(t >= 113.6) & (t < 114.0)] = 0
    from scipy.ndimage import uniform_filter1d
    g = uniform_filter1d(g, int(0.004 * SR))
    x *= g[None]
    x = filt(x, highpass(35), lowpass(12000))
    names = sorted({e["name"] for e in events})
    explicit = lambda nm: nm in FAMILY or nm.lower() in FAMILY or nm.lower().split("_")[0] in FAMILY
    guessed = {nm: family({"name": nm}) for nm in names if not explicit(nm) and nm not in unknown}
    handled = {nm: family({"name": nm}) for nm in names if explicit(nm)}
    return x, {"counts": counts, "fallback_used": used, "unknown": sorted(unknown), "guessed": guessed,
               "handled": handled}
