"""Objective checks of the audio build (development tool; needs matplotlib).

  python3 audio/analyze.py spec  <wav> [t0 t1] [-o out.png]   spectrogram + RMS envelope
  python3 audio/analyze.py report [--lang en]                  stems vs cue sheet: hits, drops, clicks, peaks, loudness
  python3 audio/analyze.py grid [<wav>]                         per-section tempo measured from the audio vs the tempo map
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dsp  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

HITS = [0, 8.0, 11.2, 13.0, 21.0, 25.0, 28.5, 30.0, 33.0, 34.5, 42.0, 46.0, 49.5, 52.0, 55.2, 56.6, 65.0, 69.5, 72.5,
        74.0, 77.4, 78.6, 86.0, 88.5, 92.5, 94.0, 95.5, 97.0, 98.5, 100.0, 100.4, 103.2, 106.0, 110.0, 113.6, 114.0,
        117.5]


def load(p):
    sr, x = dsp.read_wav(p)
    assert sr == dsp.SR, sr
    return dsp.stereo(x)


def spec_plot(x, t0, t1, out, title="", marks=HITS):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy import signal
    m = x.mean(0)[int(t0 * dsp.SR):int(t1 * dsp.SR)]
    fig, ax = plt.subplots(2, 1, figsize=(16, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]})
    f, t, S = signal.spectrogram(m, dsp.SR, nperseg=4096, noverlap=3584)
    S = 10 * np.log10(S + 1e-14)
    ax[0].pcolormesh(t + t0, f, S, vmin=S.max() - 90, vmax=S.max(), shading="auto", cmap="magma")
    ax[0].set_yscale("symlog", linthresh=200)
    ax[0].set_ylim(30, 20000)
    ax[0].set_title(title)
    hop = int(0.02 * dsp.SR)
    k = len(m) // hop
    r = np.sqrt((m[:k * hop].reshape(k, hop) ** 2).mean(1))
    tt = t0 + (np.arange(k) + 0.5) * hop / dsp.SR
    ax[1].plot(tt, 20 * np.log10(r + 1e-9), lw=0.7)
    pk = np.abs(x[:, int(t0 * dsp.SR):int(t1 * dsp.SR)]).max(0)[:k * hop].reshape(k, hop).max(1)
    ax[1].plot(tt, 20 * np.log10(pk + 1e-9), lw=0.5, alpha=0.6)
    ax[1].set_ylim(-80, 0)
    ax[1].grid(alpha=0.3)
    for h in marks:
        if t0 <= h <= t1:
            for a in ax:
                a.axvline(h, color="c", lw=0.6, alpha=0.7)
            ax[1].text(h, -78, f"{h:g}", fontsize=7, rotation=90)
    ax[1].set_xlim(t0, t1)
    fig.tight_layout()
    fig.savefig(out, dpi=80)
    plt.close(fig)
    print(out)


DROPS = [  # (label, window, reference window before it) -- v2: quality-shot breakdowns (filtered, half-time,
    # expected a few dB down, not silent), the 100.0 stop (silent) and the 113.6 cut (one piano chord left)
    ("breakdown 21.0", (21.3, 23.8), (18.5, 20.8)), ("breakdown 42.0", (42.3, 43.8), (39.5, 41.8)),
    ("breakdown 65.0", (65.3, 66.9), (62.5, 64.8)), ("breakdown 86.0", (86.4, 87.8), (83.5, 85.8)),
    ("stop 100.0", (100.03, 100.36), (98.0, 99.9)), ("cut 113.6", (113.63, 114.38), (111.5, 113.5))]
CUE_HITS = [13.0, 25.0, 28.5, 34.5, 46.0, 49.5, 56.6, 69.5, 72.5, 78.6, 88.5, 92.5, 94.0, 95.5, 97.0, 98.5, 110.0]


def _rms_db(x, a, b):
    seg = x[:, int(a * dsp.SR):int(b * dsp.SR)]
    return 10 * np.log10(np.mean(seg ** 2) + 1e-20)


def clicks(x, thr_db=24.0):
    """Clicks = broadband HF bursts (> 9 kHz, 2 ms frames) that jump thr_db above the local median and are
    not quiet. Returns [(t, jump_db, level_db)]."""
    from scipy import signal
    from scipy.ndimage import median_filter
    hp = signal.sosfilt(signal.butter(6, 9000, "high", fs=dsp.SR, output="sos"), x.mean(0))
    hop = int(0.002 * dsp.SR)
    k = len(hp) // hop
    e = 10 * np.log10((hp[:k * hop].reshape(k, hop) ** 2).mean(1) + 1e-20)
    med = median_filter(e, 101)
    out = []
    for i in np.nonzero((e - med > thr_db) & (e > -75))[0]:
        if not out or i * hop / dsp.SR - out[-1][0] > 0.05:
            out.append((round(i * hop / dsp.SR, 3), round(float(e[i] - med[i]), 1), round(float(e[i]), 1)))
    return out


def report(lang):
    music = load(OUT / "music.wav")
    fx = load(OUT / f"sfx_{lang}.wav")
    mix = load(OUT / f"mix_{lang}.wav")
    print(f"mix_{lang}: {mix.shape[1] / dsp.SR:.4f} s, {dsp.lufs(mix):.2f} LUFS, true peak "
          f"{20 * np.log10(dsp.true_peak(mix)):.2f} dBTP, sample peak {20 * np.log10(np.abs(mix).max()):.2f} dBFS")
    print("drops (music stem; quiet window vs the 2 s before):")
    for lab, (a, b), (c, d) in DROPS:
        q, r = _rms_db(music, a, b), _rms_db(music, c, d)
        print(f"  {lab:14s} {q:7.1f} dB vs {r:6.1f} dB  -> {q - r:6.1f} dB   (mix {_rms_db(mix, a, b):6.1f} dB)")
    print("hits (music stem; 150 ms after vs 300 ms before):")
    for h in CUE_HITS:
        print(f"  {h:6.1f}  +{_rms_db(music, h, h + 0.15) - _rms_db(music, h - 0.3, h - 0.01):5.1f} dB   "
              f"level {_rms_db(music, h, h + 0.15):6.1f} dB")
    for nm, x in (("music", music), (f"sfx_{lang}", fx), (f"mix_{lang}", mix)):
        c = clicks(x)
        print(f"click candidates in {nm}: {len(c)} {c[:12]}")
    tl, L = dsp.short_term(mix)
    print("short-term loudness (3 s) per section:")
    for a, b, lab in ((0, 8, "open"), (8, 30, "sentence 1"), (30, 52, "sentence 2"), (52, 74, "sentence 3"),
                      (74, 94, "sentence 4"), (94, 100, "montage"), (100.4, 106, "M6-a/b"), (106, 113.6, "climax"),
                      (114.4, 120, "close")):
        m = (tl >= a) & (tl < b)
        print(f"  {lab:11s} {a:6.1f}-{b:6.1f}  max {L[m].max():6.1f}  median {np.median(L[m]):6.1f} LUFS")


def grid(path):
    """Tempo of each section measured from the audio (onset-flux autocorrelation) vs the score's tempo map."""
    import score
    from scipy import signal
    x = load(path).mean(0)
    hop, nfft = 128, 1024
    _, t, Z = signal.stft(x, dsp.SR, nperseg=nfft, noverlap=nfft - hop, boundary=None)
    M = np.log1p(100 * np.abs(Z))
    d = np.maximum(np.diff(M, axis=1), 0).sum(0)
    t = t[1:]
    fr = 1 / (t[1] - t[0])
    print("section tempo: map vs measured (onset autocorrelation)")
    for a, b, beats, what in score.SEGS:
        if b - a < 3.4:
            continue
        m = (t >= a + 0.1) & (t < b - 0.1)
        e = d[m] - d[m].mean()
        ac = np.correlate(e, e, "full")[len(e) - 1:]
        lags = np.arange(len(ac)) / fr
        r = (lags > 60 / 140) & (lags < 60 / 90)
        L = lags[r][np.argmax(ac[r])]
        print(f"  {a:6.1f}-{b:6.1f}  map {60 * beats / (b - a):6.1f}  measured {60 / L:6.1f} BPM   {what}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("-o")
    a = ap.parse_args()
    if a.cmd == "spec":
        x = load(a.args[0])
        t0 = float(a.args[1]) if len(a.args) > 1 else 0
        t1 = float(a.args[2]) if len(a.args) > 2 else x.shape[1] / dsp.SR
        spec_plot(x, t0, t1, a.o or str(Path(a.args[0]).with_suffix(".png")), Path(a.args[0]).name)
    elif a.cmd == "report":
        report(a.args[0] if a.args else "en")
    elif a.cmd == "grid":
        grid(a.args[0] if a.args else str(OUT / "music.wav"))


if __name__ == "__main__":
    main()
