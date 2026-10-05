"""Objective checks of the audio build (development tool; needs matplotlib).

  python3 audio/analyze.py spec  <wav> [t0 t1] [-o out.png]   spectrogram + RMS envelope
  python3 audio/analyze.py report [--lang zh]                  full report: stems vs cue sheet, drops, peaks, VO fit
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

HITS = [0.6, 5.0, 11.1, 13.0, 17.0, 21.0, 24.0, 25.0, 28.5, 32.9, 34.5, 38.0, 42.0, 44.0, 46.0, 49.5, 55.1, 56.6, 61.0,
        65.0, 67.0, 68.0, 69.5, 72.5, 77.3, 78.6, 82.5, 84.0, 86.0, 88.5, 92.5, 94.0, 95.5, 97.0, 98.5, 100.0, 100.4,
        103.2, 105.2, 106.0, 110.0, 113.6, 114.5, 117.5]


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


if __name__ == "__main__":
    main()
