"""Synthesised instruments for the score: felt piano, string ensemble / pad, sub pulse,
soft toms and bass drum, finger percussion, glass, risers.

Every function returns a stereo (2, n) float64 array starting at the note onset.
All oscillators are band-limited (additive partials below 12 kHz, or PolyBLEP + low-pass)
and every sound has faded edges, so nothing clicks or aliases.
"""
import functools

import numpy as np

from dsp import (SR, bandpass, butter_lp, db, fade, filt, highpass, lowpass, midi_hz, pan, peak, polyblep_saw,
                 rng, shaped_noise, shelf, smooth_noise)


# ---------------------------------------------------------------- felt piano
@functools.lru_cache(maxsize=512)
def _piano_cached(m, vel_q, ring_q, seed, bright_q=100):
    return _piano(m, vel_q / 100.0, ring_q / 100.0, seed, bright_q / 100.0)


def piano(m, vel=0.5, ring=4.0, seed=0, bright=1.0):
    """Piano note. m: midi, vel 0..1, ring: seconds until the damper falls (key/pedal up).
    bright 1.0 = felt piano; ~1.8 = a bright grand (harder hammer: higher cut-off, flatter spectrum)."""
    return _piano_cached(int(m), int(round(vel * 100)), int(round(ring * 100)), seed, int(round(bright * 100)))


def _piano(m, vel, ring, seed, bright=1.0):
    r = rng(1000 + m * 7 + seed)
    f0 = float(midi_hz(m))
    # inharmonicity: wound bass strings ~1e-4, rising towards the treble
    B = float(np.clip(1.2e-4 * 2 ** ((m - 60) / 12 * 1.1), 4e-5, 1.5e-3))
    # fundamental decay time (aftersound), long in the bass
    T = 16.0 * 2 ** (-(m - 33) / 12 * 0.62)
    rel = 0.09 + 0.25 * np.clip((60 - m) / 36, 0, 1)  # damper release time constant
    length = ring + 8 * rel + 0.05
    L = int(length * SR)
    t = np.arange(L) / SR
    out = np.zeros(L)
    nstr = 1 if m < 30 else (2 if m < 42 else 3)
    slope = 2.9 - 1.3 * vel - 0.45 * (bright - 1)   # soft hammer -> steep spectral roll-off
    fc = (450 + 2600 * vel ** 1.6 + 2.0 * f0) * bright  # felt low-pass on the strike spectrum
    x0 = 1 / 7.6 + r.uniform(-0.004, 0.004)    # strike position (comb)
    nmax = 64
    for k in range(1, nmax + 1):
        fk = k * f0 * np.sqrt(1 + B * k * k)
        if fk > 11000:
            break
        a = k ** -slope * abs(np.sin(np.pi * k * x0)) ** 0.8 / (1 + (fk / fc) ** 2)
        if k == 1:
            a *= 0.85
        if a < 2e-4:
            continue
        tau = T / (1 + (fk / 1600) ** 1.4 + 0.08 * (k - 1))
        # partial only as long as it is audible (-80 dB)
        Lk = min(L, int(min(tau * 9.5, length) * SR))
        tk = t[:Lk]
        dec = 0.62 * np.exp(-tk / (tau * 0.22)) + 0.38 * np.exp(-tk / tau)
        part = np.zeros(Lk)
        for s in range(nstr):
            cents = 0.0 if nstr == 1 else (s - (nstr - 1) / 2) * r.uniform(0.5, 1.4)
            fs = fk * 2 ** (cents / 1200)
            part += np.sin(2 * np.pi * fs * tk + r.uniform(0, 2 * np.pi))
        out[:Lk] += a * dec * part / nstr
    # felt attack: a few ms of soft rise (slower when played softly)
    ra = int((0.003 + 0.006 * (1 - vel)) * SR)
    out[:ra] *= np.sin(np.linspace(0, np.pi / 2, ra)) ** 2
    # damper
    ir = int(ring * SR)
    if ir < L:
        out[ir:] *= np.exp(-(t[ir:] - ring) / rel)
    # hammer / felt thump and key mechanics: dark noise burst + low wooden knock
    nb = int(0.06 * SR)
    nz = r.standard_normal(nb) * np.exp(-np.arange(nb) / (0.009 * SR))
    nz = filt(nz, lowpass(min(900 + 1.5 * f0, 4000), 0.7), highpass(120))
    knock = np.sin(2 * np.pi * 105 * t[:nb]) * np.exp(-t[:nb] / 0.018) + 0.5 * np.sin(2 * np.pi * 240 * t[:nb]) * np.exp(-t[:nb] / 0.01)
    peak_amp = np.max(np.abs(out[: int(0.05 * SR)])) + 1e-9
    out[:nb] += peak_amp * (0.12 + 0.10 * vel) * (nz / (np.max(np.abs(nz)) + 1e-9)) + peak_amp * 0.05 * knock
    # damper falling (very soft felt noise at release)
    if ir + nb < L:
        dn = r.standard_normal(nb) * np.exp(-np.arange(nb) / (0.012 * SR))
        dn = filt(dn, lowpass(500), highpass(80))
        out[ir:ir + nb] += peak_amp * 0.012 * dn / (np.max(np.abs(dn)) + 1e-9)
    out = fade(out, 0.0, 0.02)
    # normalise so that vel maps to level (soft notes are also quieter)
    out = out / (peak_amp + 1e-9) * (0.08 + 0.92 * vel ** 1.5)
    p = float(np.clip((m - 62) / 30, -0.55, 0.55))
    st = pan(out, p)
    # tiny inter-channel delay for width (soundboard is big)
    d = int(0.0004 * SR)
    if p > 0:
        st[0] = np.concatenate([np.zeros(d), st[0][:-d]])
    else:
        st[1] = np.concatenate([np.zeros(d), st[1][:-d]])
    return st


def piano_bus_eq(x):
    """Felt piano colour: warm body, no glare."""
    return filt(x, highpass(32), peak(180, 2.0, 0.8), peak(2600, -2.5, 0.9), shelf(5500, -6.0, True))


# ---------------------------------------------------------------- strings / pad
def strings(m, dur, env_pts, kind="vc", seed=0, bright=1.0, vib=1.0, voices=None, trem=None):
    """Section string note. env_pts: [(t, amp)] over the note (0..dur). kind: cb vc va vn pad.
    Detuned PolyBLEP saw ensemble with slow drift and delayed vibrato, crossfaded between a dark
    and a brighter low-passed version according to the swell, plus body resonances and bow air."""
    from dsp import envelope
    r = rng(5000 + seed * 13 + m)
    L = int(dur * SR)
    f0 = float(midi_hz(m))
    nv = voices or {"cb": 4, "vc": 6, "va": 6, "vn": 7, "pad": 5}[kind]
    env = envelope(env_pts, L)
    tt = np.arange(L) / SR
    acc = np.zeros((2, L))
    vib_depth = {"cb": 6, "vc": 9, "va": 10, "vn": 11, "pad": 0}[kind] * vib
    for v in range(nv):
        det = r.uniform(-9, 9) if kind != "pad" else r.uniform(-14, 14)
        drift = 4 * smooth_noise(L, 0.35, int(r.integers(1 << 30)))
        rate = r.uniform(4.6, 5.6)
        vib_on = np.clip((tt - r.uniform(0.35, 0.8)) / 0.8, 0, 1)
        vibr = vib_depth * vib_on * np.sin(2 * np.pi * rate * tt + r.uniform(0, 6.28)) * (0.7 + 0.3 * smooth_noise(L, 0.5, v + seed))
        f = f0 * 2 ** ((det + drift + vibr) / 1200)
        y = polyblep_saw(f, r.random())
        # per-voice slight amplitude wander (bow pressure)
        y *= 1 + 0.12 * smooth_noise(L, 0.8, 77 + v + seed)
        acc += pan(y, r.uniform(-0.8, 0.8) if kind != "cb" else r.uniform(-0.3, 0.3))
    acc /= nv
    # spectral shaping: dark and bright versions mixed by the swell
    base = {"cb": 350, "vc": 650, "va": 900, "vn": 1300, "pad": 520}[kind]
    dark = butter_lp(acc, min(base + f0 * 1.2, 9000), 2)
    bright_x = butter_lp(acc, min((base * 2.8 + f0 * 2.5) * bright, 9000), 2)
    e = env / (np.max(env) + 1e-9)
    mixk = np.clip(e, 0, 1) ** 0.8
    y = dark * (1 - mixk) + bright_x * mixk
    if kind != "pad":
        y = filt(y, peak(290, 3.0, 1.1), peak(1050, 1.5, 1.4), peak(2700, 1.0, 2.0), shelf(4800, -9.0, True), highpass(max(f0 * 0.6, 30)))
        # bow air
        air = r.standard_normal(L)
        air = filt(air, bandpass(min(2200 + f0, 6000), 0.8))
        air = air / (np.std(air) + 1e-9) * 0.012
        y += np.stack([air, np.roll(air, 211)])
    else:
        y = filt(y, shelf(2500, -10.0, True), highpass(max(f0 * 0.5, 28)))
        # sine body an octave lower in the low register for warmth
        sub = np.sin(2 * np.pi * f0 * tt) * 0.25
        y += np.stack([sub, sub])
    if trem is not None:
        rate, depth = trem
        y *= 1 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(np.broadcast_to(rate, (L,))) / SR))
    y *= env
    return fade(y, 0.02, 0.05)


# ---------------------------------------------------------------- pulse / percussion
def sub_pulse(f, vel=0.5, length=0.42):
    L = int(length * SR)
    t = np.arange(L) / SR
    fr = f * (1 + 0.12 * np.exp(-t / 0.03))
    ph = 2 * np.pi * np.cumsum(fr) / SR
    y = np.sin(ph) + 0.22 * np.sin(2 * ph) + 0.06 * np.sin(3 * ph)
    e = (1 - np.exp(-t / 0.006)) * np.exp(-t / 0.13)
    y = fade(y * e, 0.0, 0.03)
    return np.stack([y, y]) * vel


def tom(f=92.0, vel=0.5, decay=0.65, seed=0, p=0.0):
    """Soft felt-mallet tom: pitch-gliding membrane with inharmonic modes + felt noise."""
    r = rng(9000 + seed)
    L = int((decay * 6 + 0.1) * SR)
    t = np.arange(L) / SR
    y = np.zeros(L)
    for ratio, a, dk in ((1.0, 1.0, 1.0), (1.59, 0.35, 0.5), (2.14, 0.18, 0.35), (2.30, 0.10, 0.3), (2.65, 0.05, 0.25)):
        fr = f * ratio * (1 + 0.25 * np.exp(-t / 0.04))
        y += a * np.sin(2 * np.pi * np.cumsum(fr) / SR + r.uniform(0, 6.28)) * np.exp(-t / (decay * dk))
    nb = int(0.05 * SR)
    nz = filt(r.standard_normal(nb) * np.exp(-np.arange(nb) / (0.008 * SR)), lowpass(700 + 600 * vel), highpass(60))
    y[:nb] += 0.35 * nz / (np.max(np.abs(nz)) + 1e-9)
    y *= 1 - np.exp(-t / 0.002)
    y = filt(y, lowpass(1800 + 1500 * vel, 0.6))
    y = fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.05) * (0.15 + 0.85 * vel)
    return pan(y, p)


def bass_drum(f=44.0, vel=1.0, decay=1.6, seed=1):
    r = rng(9500 + seed)
    L = int((decay * 3.5) * SR)
    t = np.arange(L) / SR
    y = np.zeros(L)
    for ratio, a, dk in ((1.0, 1.0, 1.0), (1.47, 0.4, 0.55), (1.99, 0.2, 0.35), (2.44, 0.12, 0.25), (2.9, 0.07, 0.2)):
        fr = f * ratio * (1 + 0.35 * np.exp(-t / 0.05))
        y += a * np.sin(2 * np.pi * np.cumsum(fr) / SR + r.uniform(0, 6.28)) * np.exp(-t / (decay * dk))
    nb = int(0.12 * SR)
    nz = filt(r.standard_normal(nb) * np.exp(-np.arange(nb) / (0.02 * SR)), lowpass(500), highpass(40))
    y[:nb] += 0.3 * nz / (np.max(np.abs(nz)) + 1e-9)
    y *= 1 - np.exp(-t / 0.003)
    y = filt(y, lowpass(1200, 0.6))
    y = fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.2) * vel
    # slight width from the room side of the head
    return np.stack([y, np.concatenate([np.zeros(24), y[:-24]])])


def finger(vel=0.5, seed=0, p=0.0, tone=1.0):
    """Finger percussion: fingertip on wood / frame drum edge. Short, rounded, no hiss."""
    r = rng(12000 + seed)
    L = int(0.12 * SR)
    t = np.arange(L) / SR
    f1 = (520 + r.uniform(-40, 40)) * tone
    body = np.sin(2 * np.pi * f1 * t) * np.exp(-t / 0.016) + 0.45 * np.sin(2 * np.pi * f1 * 2.31 * t) * np.exp(-t / 0.008)
    nz = r.standard_normal(L) * np.exp(-t / 0.004)
    nz = filt(nz, bandpass(2200 * tone, 1.2))
    y = body + 0.5 * nz / (np.max(np.abs(nz)) + 1e-9)
    y *= 1 - np.exp(-t / 0.0006)
    y = filt(y, lowpass(5000, 0.7))
    y = fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.02) * vel
    return pan(y, p)


def glass(m, vel=0.5, decay=1.8, seed=0, p=0.0):
    """Struck glass / crystal: near-harmonic partials with slow beating, soft attack, dark top."""
    r = rng(15000 + seed + m)
    f = float(midi_hz(m))
    L = int(decay * 5 * SR)
    t = np.arange(L) / SR
    y = np.zeros(L)
    for ratio, a, dk in ((1.0, 1.0, 1.0), (2.0, 0.18, 0.6), (2.76, 0.12, 0.45), (5.4, 0.035, 0.25)):
        fr = f * ratio
        if fr > 11000:
            continue
        beat = 1 + 0.12 * np.sin(2 * np.pi * r.uniform(0.6, 1.6) * t)
        y += a * beat * np.sin(2 * np.pi * fr * t + r.uniform(0, 6.28)) * np.exp(-t / (decay * dk))
    y *= 1 - np.exp(-t / 0.0025)
    y = filt(y, lowpass(7000, 0.7))
    y = fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.1) * vel
    return pan(y, p)


def riser(dur, f_lo, f_hi, seed=0, curve=2.2):
    """Filtered-noise riser: a soft band sweeping up in log-frequency, swelling, ending at dur."""
    def spec(t, f):
        k = (t / dur) ** curve
        fc = f_lo * (f_hi / f_lo) ** k
        bw = 0.9 - 0.3 * k
        g = np.exp(-0.5 * ((np.log2(np.maximum(f, 20)) - np.log2(fc)) / bw) ** 2)
        return g * (0.02 + k) * (f < 9000)
    y = shaped_noise(dur, spec, seed)
    L = len(y)
    e = np.linspace(0, 1, L) ** curve
    y = y * e
    a = shaped_noise(dur, spec, seed + 1) * e
    st = np.stack([y, a])
    st = filt(st, highpass(60), lowpass(8000))
    return fade(st / (np.max(np.abs(st)) + 1e-9), 0.05, 0.012)


# ================================================================ v2 (bright launch score) instruments
# All mono unless noted; the score pans / spreads them. Band-limited by construction (additive partials
# stop below 11 kHz; noise sources are low-passed); every sound has a soft onset ramp and faded tail.

def piano_bright_eq(x):
    """Bright grand colour: clean low end, a little presence, polished (not glassy) top."""
    return filt(x, highpass(55), peak(220, -1.5, 0.9), peak(2900, 1.8, 0.8), shelf(9000, -2.0, True))


@functools.lru_cache(maxsize=1024)
def pluck(m, dur=0.3, bright=1.0, seed=0):
    """Plucked synth: saw-like additive tone, higher partials die faster (a closing filter), two slightly
    detuned voices. dur: gate length in seconds (release 60 ms after)."""
    r = rng(20000 + m * 3 + seed)
    f0 = float(midi_hz(m))
    L = int((dur + 0.12) * SR)
    t = np.arange(L) / SR
    y = np.zeros(L)
    tau0 = 0.16 + 0.25 * bright
    for v, cents in enumerate((-6.0, 6.0)):
        f = f0 * 2 ** (cents / 1200)
        ph = r.uniform(0, 2 * np.pi)
        k = 1
        while k * f < 10500 and k <= 40:
            tau = tau0 / (1 + 0.55 * (k - 1) / bright)
            a = (1.0 / k) * np.exp(-((k * f) / (3800 * bright)) ** 2)
            y += a * np.sin(2 * np.pi * k * f * t + ph * k) * np.exp(-t / tau)
            k += 1
    y *= 1 - np.exp(-t / 0.0015)
    g = int(dur * SR)
    if g < L:
        y[g:] *= np.exp(-(t[g:] - dur) / 0.03)
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.02)


@functools.lru_cache(maxsize=256)
def glock(m, decay=1.1, seed=0):
    """Glockenspiel / celesta-ish bell: a few bright partials, quick attack, warm decay."""
    r = rng(21000 + m + seed)
    f = float(midi_hz(m))
    L = int(decay * 4 * SR)
    t = np.arange(L) / SR
    y = np.zeros(L)
    for ratio, a, dk in ((1.0, 1.0, 1.0), (2.0, 0.12, 0.5), (2.76, 0.22, 0.35), (5.40, 0.06, 0.18), (8.93, 0.025, 0.1)):
        if f * ratio > 10500:
            continue
        y += a * np.sin(2 * np.pi * f * ratio * t + r.uniform(0, 6.28)) * np.exp(-t / (decay * dk))
    y *= 1 - np.exp(-t / 0.0012)
    y = filt(y, lowpass(9000, 0.7))
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.05)


def synth_pad(m, dur, env_pts, bright=1.0, seed=0, voices=6):
    """Warm detuned-saw pad note (stereo): PolyBLEP saws spread across the field, slow drift, low-passed."""
    from dsp import envelope
    r = rng(22000 + m * 5 + seed)
    L = int(dur * SR)
    f0 = float(midi_hz(m))
    acc = np.zeros((2, L))
    for v in range(voices):
        det = (v - (voices - 1) / 2) / ((voices - 1) / 2) * 11 + r.uniform(-2, 2)
        drift = 3 * smooth_noise(L, 0.3, int(r.integers(1 << 30)))
        y = polyblep_saw(f0 * 2 ** ((det + drift) / 1200), r.random())
        acc += pan(y, (v - (voices - 1) / 2) / ((voices - 1) / 2) * 0.85)
    acc /= voices
    fc = min((1400 + 2.2 * f0) * bright, 7000)
    acc = butter_lp(acc, fc, 2)
    acc = filt(acc, highpass(max(f0 * 0.7, 80)), peak(fc * 0.8, 2.0, 1.2))
    acc *= envelope(env_pts, L)[None]
    return fade(acc, 0.01, 0.05)


@functools.lru_cache(maxsize=512)
def bass_note(m, dur=0.25, bright=1.0):
    """Synth bass: sine sub + low-passed saw with a short filter envelope. Mono."""
    f0 = float(midi_hz(m))
    L = int((dur + 0.08) * SR)
    t = np.arange(L) / SR
    sub = np.sin(2 * np.pi * f0 * t)
    saw = polyblep_saw(np.full(L, f0), 0.25) + 0.6 * polyblep_saw(np.full(L, f0 * 1.004), 0.6)
    # filter envelope approximated by mixing two fixed low-passes (no zipper)
    lo = butter_lp(saw, 420, 2)
    hi = butter_lp(saw, 1500 * bright, 2)
    k = np.exp(-t / 0.07)
    y = 0.6 * sub + 0.6 * (lo * (1 - k) + hi * k)
    e = (1 - np.exp(-t / 0.004))
    g = int(dur * SR)
    e[g:] *= np.exp(-(t[g:] - dur) / 0.025)
    y = filt(y * e, highpass(30))
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.01)


@functools.lru_cache(maxsize=8)
def kick(variant=0):
    """Clean, round kick: pitch-swept sine body, soft beater click (band-limited), gentle saturation."""
    L = int(0.45 * SR)
    t = np.arange(L) / SR
    f = 51 + 95 * np.exp(-t / 0.03) + 40 * np.exp(-t / 0.004)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (0.13 if variant == 0 else 0.24))
    r = rng(23000 + variant)
    nz = r.standard_normal(L) * np.exp(-t / 0.0025)
    click = butter_lp(filt(nz, bandpass(2800, 0.9)), 6000, 4)
    y = np.tanh(1.6 * body) / np.tanh(1.6) + 0.35 * click / (np.max(np.abs(click)) + 1e-9)
    y *= 1 - np.exp(-t / 0.0012)
    y = butter_lp(filt(y, highpass(32)), 8000, 4)
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.03)


@functools.lru_cache(maxsize=16)
def clap(seed=0):
    """Hand clap: four noise bursts a few ms apart, band-passed around 1.3 kHz, short diffuse tail. Stereo."""
    r = rng(24000 + seed)
    L = int(0.35 * SR)
    t = np.arange(L) / SR
    out = []
    for c in range(2):
        y = np.zeros(L)
        for j, dt in enumerate((0.0, 0.009, 0.018, 0.029)):
            i = max(0, int((dt + r.uniform(-0.0008, 0.0008)) * SR))
            b = r.standard_normal(L - i) * np.exp(-t[:L - i] / (0.004 if j < 3 else 0.075))
            y[i:] += b * (0.8 if j < 3 else 1.0)
        y = filt(y, bandpass(1250, 0.7), highpass(500), lowpass(7500))
        out.append(y)
    y = np.stack(out)
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.03)


@functools.lru_cache(maxsize=16)
def snare(seed=0):
    """Light snare for fills: short tone + band-passed noise."""
    r = rng(25000 + seed)
    L = int(0.25 * SR)
    t = np.arange(L) / SR
    tone = np.sin(2 * np.pi * 196 * t) * np.exp(-t / 0.03) + 0.4 * np.sin(2 * np.pi * 330 * t) * np.exp(-t / 0.02)
    nz = filt(r.standard_normal(L), bandpass(2600, 0.6), lowpass(8000)) * np.exp(-t / 0.06)
    y = 0.6 * tone + nz / (np.max(np.abs(nz)) + 1e-9)
    y *= 1 - np.exp(-t / 0.0006)
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.02)


@functools.lru_cache(maxsize=16)
def shaker(seed=0, accent=False):
    """Shaker: soft high noise grain, rounded (no fizz above ~10 kHz)."""
    r = rng(26000 + seed)
    L = int(0.12 * SR)
    t = np.arange(L) / SR
    e = (1 - np.exp(-t / (0.006 if accent else 0.009))) * np.exp(-t / (0.035 if accent else 0.028))
    y = filt(r.standard_normal(L), highpass(4200, 0.7), lowpass(9500, 0.7), peak(6500, 2.0, 1.0)) * e
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.01)


@functools.lru_cache(maxsize=8)
def hat(open_=False, seed=0):
    """Hi-hat: inharmonic metallic partials + noise, band-limited, softened top."""
    r = rng(27000 + seed)
    L = int((0.35 if open_ else 0.08) * SR)
    t = np.arange(L) / SR
    y = np.zeros(L)
    for fr in (3150, 4470, 5210, 6040, 7330, 8120):
        y += np.sin(2 * np.pi * fr * r.uniform(0.99, 1.01) * t + r.uniform(0, 6.28))
    y = 0.35 * y / 6 + filt(r.standard_normal(L), highpass(6000))
    y *= np.exp(-t / (0.11 if open_ else 0.018)) * (1 - np.exp(-t / 0.0008))
    y = filt(y, highpass(5000, 0.7), lowpass(10000, 0.7))
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.02)


@functools.lru_cache(maxsize=8)
def crash(dur=2.4, seed=0):
    """Soft crash / ride swell for impacts: decorrelated stereo noise + metallic partials, dark top. Stereo."""
    r = rng(28000 + seed)
    L = int(dur * SR)
    t = np.arange(L) / SR
    chans = []
    for c in range(2):
        y = r.standard_normal(L)
        for fr in (3520, 4130, 5270, 6680):
            y += 0.3 * np.sin(2 * np.pi * fr * r.uniform(0.98, 1.02) * t + r.uniform(0, 6.28))
        y = filt(y, highpass(700, 0.7), lowpass(8500, 0.6), peak(5000, -2.0, 0.8))
        e = np.exp(-t / (dur * 0.28)) * (1 - np.exp(-t / 0.002))
        chans.append(y * e)
    y = np.stack(chans)
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.3)


@functools.lru_cache(maxsize=8)
def boom(f_end=41.2, dur=1.6):
    """Sub impact: a sine dropping into the root, with a felted thump. Mono."""
    L = int(dur * SR)
    t = np.arange(L) / SR
    f = f_end * (1 + 1.4 * np.exp(-t / 0.06))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (dur * 0.33))
    y += 0.3 * np.sin(2 * np.pi * 2 * np.cumsum(f) / SR) * np.exp(-t / 0.15)
    y *= 1 - np.exp(-t / 0.002)
    y = filt(y, lowpass(400), highpass(28))
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.0, 0.2)


def sweep(dur, f0, f1, seed=0, q=1.0):
    """Noise sweep (riser if f1 > f0, downlifter if f1 < f0), loudest where the motion ends (riser) or
    begins (downlifter). Stereo, band-limited below 9 kHz."""
    up = f1 > f0

    def spec(t, f):
        k = np.clip(t / dur, 0, 1)
        fc = f0 * (f1 / f0) ** (k ** (1.6 if up else 0.6))
        g = np.exp(-0.5 * ((np.log2(np.maximum(f, 20)) - np.log2(fc)) / (0.8 * q)) ** 2)
        lvl = (0.05 + k ** 1.8) if up else (1 - k) ** 1.4 + 0.02
        return g * lvl * (f < 9500)
    y = np.stack([shaped_noise(dur, spec, seed), shaped_noise(dur, spec, seed + 1)])
    y = filt(y, highpass(80))
    return fade(y / (np.max(np.abs(y)) + 1e-9), 0.03 if up else 0.004, 0.02 if up else 0.2)
