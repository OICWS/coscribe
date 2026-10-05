"""Shared DSP helpers for the coscribe film audio build (numpy / scipy only)."""
import numpy as np
from scipy import signal

SR = 48000
DUR = 120.0
N = int(round(SR * DUR))  # exactly 5,760,000 samples


def rng(seed):
    return np.random.default_rng(seed)


def midi_hz(m):
    return 440.0 * 2.0 ** ((np.asarray(m, dtype=float) - 69.0) / 12.0)


NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def n(name):
    """'D3', 'Bb2', 'F#4' -> midi number."""
    letter, rest = name[0], name[1:]
    acc = 0
    while rest and rest[0] in "b#":
        acc += -1 if rest[0] == "b" else 1
        rest = rest[1:]
    return 12 * (int(rest) + 1) + NOTE[letter] + acc


def db(x):
    return 10 ** (x / 20.0)


def stereo(x):
    return np.stack([x, x]) if x.ndim == 1 else x


def pan(x, p):
    """Equal-power pan of a mono signal, p in [-1, 1] -> (2, n)."""
    a = (p + 1) * np.pi / 4
    return np.stack([x * np.cos(a), x * np.sin(a)]) * np.sqrt(2)


def add(buf, x, t, gain=1.0):
    """Mix x (mono or (2, n)) into stereo buf at time t seconds (clipped to buf)."""
    x = stereo(x)
    i = int(round(t * SR))
    j0 = max(0, -i)
    i0 = max(0, i)
    m = min(buf.shape[1] - i0, x.shape[1] - j0)
    if m > 0:
        buf[:, i0:i0 + m] += gain * x[:, j0:j0 + m]


def fade(x, fi=0.005, fo=0.005):
    x = x.copy()
    L = x.shape[-1]
    a, b = int(fi * SR), int(fo * SR)
    if a > 0:
        x[..., :a] *= np.sin(np.linspace(0, np.pi / 2, a)) ** 2
    if b > 0:
        x[..., L - b:] *= np.cos(np.linspace(0, np.pi / 2, b)) ** 2
    return x


def tvec(dur):
    return np.arange(int(round(dur * SR))) / SR


def envelope(points, length, curve="cos"):
    """Piecewise envelope from [(t, value), ...] sampled over `length` samples (raised-cosine segments)."""
    t = np.arange(length) / SR
    pts = sorted(points)
    out = np.full(length, float(pts[0][1]))
    for (t0, v0), (t1, v1) in zip(pts[:-1], pts[1:]):
        m = (t >= t0) & (t < t1)
        if not m.any():
            continue
        k = (t[m] - t0) / max(t1 - t0, 1e-9)
        if curve == "cos":
            k = 0.5 - 0.5 * np.cos(np.pi * k)
        out[m] = v0 + (v1 - v0) * k
    out[t >= pts[-1][0]] = pts[-1][1]
    out[t < pts[0][0]] = pts[0][1]
    return out


# ---------- filters (RBJ cookbook biquads as SOS) ----------
def _biquad(b, a):
    b = np.asarray(b, float) / a[0]
    a = np.asarray(a, float) / a[0]
    return np.concatenate([b, a])[None, :]


def lowpass(fc, q=0.707):
    w = 2 * np.pi * fc / SR
    al = np.sin(w) / (2 * q)
    c = np.cos(w)
    return _biquad([(1 - c) / 2, 1 - c, (1 - c) / 2], [1 + al, -2 * c, 1 - al])


def highpass(fc, q=0.707):
    w = 2 * np.pi * fc / SR
    al = np.sin(w) / (2 * q)
    c = np.cos(w)
    return _biquad([(1 + c) / 2, -(1 + c), (1 + c) / 2], [1 + al, -2 * c, 1 - al])


def bandpass(fc, q=1.0):
    w = 2 * np.pi * fc / SR
    al = np.sin(w) / (2 * q)
    c = np.cos(w)
    return _biquad([al, 0, -al], [1 + al, -2 * c, 1 - al])


def peak(fc, gain_db, q=1.0):
    A = 10 ** (gain_db / 40)
    w = 2 * np.pi * fc / SR
    al = np.sin(w) / (2 * q)
    c = np.cos(w)
    return _biquad([1 + al * A, -2 * c, 1 - al * A], [1 + al / A, -2 * c, 1 - al / A])


def shelf(fc, gain_db, high=True, s=0.8):
    A = 10 ** (gain_db / 40)
    w = 2 * np.pi * fc / SR
    c, sn = np.cos(w), np.sin(w)
    al = sn / 2 * np.sqrt((A + 1 / A) * (1 / s - 1) + 2)
    sq = 2 * np.sqrt(A) * al
    if high:
        b = [A * ((A + 1) + (A - 1) * c + sq), -2 * A * ((A - 1) + (A + 1) * c), A * ((A + 1) + (A - 1) * c - sq)]
        a = [(A + 1) - (A - 1) * c + sq, 2 * ((A - 1) - (A + 1) * c), (A + 1) - (A - 1) * c - sq]
    else:
        b = [A * ((A + 1) - (A - 1) * c + sq), 2 * A * ((A - 1) - (A + 1) * c), A * ((A + 1) - (A - 1) * c - sq)]
        a = [(A + 1) + (A - 1) * c + sq, -2 * ((A - 1) + (A + 1) * c), (A + 1) + (A - 1) * c - sq]
    return _biquad(b, a)


def chain(*sos):
    return np.concatenate(sos, axis=0)


def filt(x, *sos):
    return signal.sosfilt(chain(*sos), x, axis=-1)


def filtfilt(x, *sos):
    return signal.sosfiltfilt(chain(*sos), x, axis=-1)


def butter_lp(x, fc, order=4):
    return signal.sosfilt(signal.butter(order, fc, "low", fs=SR, output="sos"), x, axis=-1)


def butter_hp(x, fc, order=2):
    return signal.sosfilt(signal.butter(order, fc, "high", fs=SR, output="sos"), x, axis=-1)


# ---------- oscillators ----------
def polyblep_saw(freq, phase0=0.0):
    """Band-limited sawtooth for a per-sample frequency array (PolyBLEP)."""
    dt = np.asarray(freq, float) / SR
    ph = (phase0 + np.cumsum(dt)) % 1.0
    y = 2 * ph - 1
    m = ph < dt
    t = ph[m] / dt[m]
    y[m] -= t + t - t * t - 1
    m = ph > 1 - dt
    t = (ph[m] - 1) / dt[m]
    y[m] -= t * t + t + t + 1
    return y


def smooth_noise(n_samples, rate_hz, seed):
    """Slowly varying random curve in about [-1, 1] (for drift/vibrato wander)."""
    r = rng(seed)
    k = max(4, int(n_samples / SR * rate_hz) + 4)
    pts = r.uniform(-1, 1, k)
    x = np.linspace(0, k - 3, n_samples)
    i = np.floor(x).astype(int)
    f = x - i
    f = f * f * (3 - 2 * f)
    return pts[i] * (1 - f) + pts[i + 1] * f


# ---------- noise shaped in the STFT domain (risers, whooshes, reverb tails) ----------
def shaped_noise(dur, spec_fn, seed, nfft=2048, hop=512):
    """Noise whose magnitude spectrum per frame is spec_fn(t_frame_array, f_array) -> (F, T).
    Smooth, band-limited by construction."""
    L = int(round(dur * SR))
    frames = L // hop + 4
    f = np.fft.rfftfreq(nfft, 1 / SR)
    tt = (np.arange(frames) * hop - nfft / 2) / SR
    tt = np.clip(tt, 0, dur)
    mag = spec_fn(tt[None, :], f[:, None])
    r = rng(seed)
    ph = np.exp(2j * np.pi * r.random(mag.shape))
    _, x = signal.istft(mag * ph, fs=SR, nperseg=nfft, noverlap=nfft - hop, boundary=True)
    x = x[:L]
    if len(x) < L:
        x = np.pad(x, (0, L - len(x)))
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


# ---------- reverb ----------
def make_ir(dur=3.2, t60_low=2.8, t60_high=1.0, predelay=0.02, early=True, seed=7, width=1.0, hp=120.0, lp=9000.0):
    """Synthetic stereo IR: decorrelated noise with frequency-dependent exponential decay
    (T60 interpolated in log-frequency between t60_low @200 Hz and t60_high @8 kHz), soft onset,
    a few early reflections, and band limiting."""
    f = np.fft.rfftfreq(2048, 1 / SR)
    lf = np.clip((np.log2(np.maximum(f, 50)) - np.log2(200)) / (np.log2(8000) - np.log2(200)), 0, 1)
    t60 = t60_low + (t60_high - t60_low) * lf

    def spec(t, ff):
        g = 10 ** (-3 * t / t60[:, None])
        onset = 1 - np.exp(-t / 0.012)
        tilt = 1.0 / (1 + (ff / lp) ** 4) * (ff / hp) ** 2 / (1 + (ff / hp) ** 2)
        return g * onset * tilt

    chans = []
    for c in range(2):
        x = shaped_noise(dur, spec, seed + 31 * c)
        chans.append(x)
    ir = np.stack(chans)
    # partial decorrelation control (width < 1 pulls towards mono)
    mid = ir.mean(0)
    ir = mid + width * (ir - mid)
    pd = int(predelay * SR)
    ir = np.pad(ir, ((0, 0), (pd, 0)))
    if early:
        r = rng(seed + 5)
        for k in range(10):
            tt = predelay * 0.4 + r.uniform(0.004, 0.07)
            i = int(tt * SR)
            g = 0.5 * np.exp(-tt / 0.05) * r.uniform(0.4, 1.0)
            ir[r.integers(0, 2), i] += g * 8.0 / np.sqrt(SR / 1000)
    ir = fade(ir, 0.0, 0.3)
    ir /= np.sqrt(np.sum(ir ** 2) / 2)
    return ir


def convolve_stereo(x, ir):
    """x: (2, n) or mono; ir: (2, m). Each output channel = sum of input L/R with IR channel (true stereo-ish)."""
    x = stereo(x)
    mono_in = 0.5 * (x[0] + x[1])
    side_in = 0.5 * (x[0] - x[1])
    out = np.zeros((2, x.shape[1] + ir.shape[1] - 1))
    for c in range(2):
        out[c] = signal.oaconvolve(mono_in, ir[c]) + signal.oaconvolve(side_in, ir[c] * (1 if c == 0 else -1)) * 0.6
    return out[:, :x.shape[1]]


def ms_width(x, w):
    m = 0.5 * (x[0] + x[1])
    s = 0.5 * (x[0] - x[1]) * w
    return np.stack([m + s, m - s])


# ---------- dynamics ----------
def env_follow(x, attack, release):
    """Peak-ish envelope follower on |x| (mono), one-pole attack/release, vectorised in blocks via lfilter trick."""
    a = np.abs(x)
    # downsample to 1 kHz for speed, then upsample
    hop = SR // 1000
    k = len(a) // hop
    blk = a[:k * hop].reshape(k, hop).max(1)
    out = np.empty(k)
    ga = np.exp(-1 / (attack * 1000))
    gr = np.exp(-1 / (release * 1000))
    e = 0.0
    for i in range(k):
        v = blk[i]
        g = ga if v > e else gr
        e = g * e + (1 - g) * v
        out[i] = e
    full = np.interp(np.arange(len(a)), np.arange(k) * hop + hop / 2, out)
    return full


def rms_env(x, win=0.05):
    w = int(win * SR)
    k = np.ones(w) / w
    return np.sqrt(signal.oaconvolve(x ** 2, k, mode="same").clip(0))


def compress(x, thresh_db=-20, ratio=3.0, attack=0.01, release=0.15, knee=6.0, makeup_db=0.0):
    """Feed-forward compressor on stereo/mono x with linked detector."""
    xs = stereo(x)
    det = np.maximum(np.abs(xs[0]), np.abs(xs[1]))
    e = env_follow(det, attack, release)
    lvl = 20 * np.log10(e + 1e-9)
    over = lvl - thresh_db
    gr = np.where(over <= -knee / 2, 0.0,
                  np.where(over >= knee / 2, over * (1 - 1 / ratio),
                           (1 - 1 / ratio) * (over + knee / 2) ** 2 / (2 * knee)))
    g = db(-gr + makeup_db)
    return x * g if x.ndim == 1 else xs * g


def true_peak(x, os=4):
    y = signal.resample_poly(x, os, 1, axis=-1)
    return float(np.max(np.abs(y)))


def limiter(x, ceiling_db=-1.5, lookahead=0.005, release=0.08, os=4):
    """True-peak-aware lookahead limiter (gain computed on 4x oversampled peaks)."""
    ceil = db(ceiling_db)
    up = signal.resample_poly(x, os, 1, axis=-1)
    pk = np.max(np.abs(up), axis=0)
    pk = pk[: (len(pk) // os) * os].reshape(-1, os).max(1)
    pk = np.pad(pk, (0, x.shape[-1] - len(pk)))
    need = np.minimum(1.0, ceil / np.maximum(pk, 1e-9))
    la = int(lookahead * SR)
    # running min over lookahead window (so gain is down before the peak arrives)
    from scipy.ndimage import minimum_filter1d
    g = minimum_filter1d(need, size=2 * la + 1, origin=0)
    # smooth: instant attack already handled by the window; release one-pole
    gs = np.empty_like(g)
    hop = 48
    k = len(g) // hop + 1
    blk = np.array([g[i * hop:(i + 1) * hop].min() if i * hop < len(g) else 1.0 for i in range(k)])
    rel = np.exp(-hop / (release * SR))
    e = 1.0
    sm = np.empty(k)
    for i in range(k):
        v = blk[i]
        e = v if v < e else rel * e + (1 - rel) * v
        sm[i] = e
    gs = np.interp(np.arange(len(g)), np.arange(k) * hop, sm)
    gs = np.minimum(gs, g)
    # smooth the attack edge a little (2 ms raised cosine)
    w = np.hanning(int(0.002 * SR) | 1)
    w /= w.sum()
    gs = np.minimum(np.convolve(gs, w, mode="same"), 1.0)
    gs = np.minimum(gs, minimum_filter1d(need, size=int(0.0005 * SR) | 1))
    return x * gs


# ---------- loudness (ITU-R BS.1770-4) ----------
def _k_weight(x, fs=SR):
    # pre-filter (high shelf) + RLB high-pass, coefficients from the spec, recomputed for fs
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = np.tan(np.pi * f0 / fs)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = np.tan(np.pi * f0 / fs)
    a2 = [1, 2 * (K * K - 1) / (1 + K / Q + K * K), (1 - K / Q + K * K) / (1 + K / Q + K * K)]
    b2 = [1, -2, 1]
    sos = np.array([b1 + a1, b2 + a2])
    return signal.sosfilt(sos, x, axis=-1)


def lufs(x):
    """Integrated loudness of stereo x (2, n)."""
    y = _k_weight(stereo(x))
    blk, hop = int(0.4 * SR), int(0.1 * SR)
    p = y ** 2
    cs = np.concatenate([np.zeros((2, 1)), np.cumsum(p, axis=1)], axis=1)
    starts = np.arange(0, p.shape[1] - blk + 1, hop)
    z = (cs[:, starts + blk] - cs[:, starts]) / blk
    ms = z.sum(0)
    l = -0.691 + 10 * np.log10(ms + 1e-20)
    g = ms[l > -70]
    if not len(g):
        return -70.0
    rel = -0.691 + 10 * np.log10(g.mean()) - 10
    g2 = ms[(l > -70) & (l > rel)]
    return float(-0.691 + 10 * np.log10(g2.mean()))


def short_term(x, win=3.0, hop=0.1):
    """Short-term loudness curve (LUFS) sampled every hop seconds; returns (t, L)."""
    y = _k_weight(stereo(x))
    blk, h = int(win * SR), int(hop * SR)
    p = (y ** 2).sum(0)
    cs = np.concatenate([[0], np.cumsum(p)])
    centers = np.arange(0, p.shape[0], h)
    a = np.clip(centers - blk // 2, 0, len(p))
    b = np.clip(centers + blk // 2, 0, len(p))
    ms = (cs[b] - cs[a]) / np.maximum(b - a, 1)
    return centers / SR, -0.691 + 10 * np.log10(ms + 1e-20)


def write_wav(path, x, sr=SR):
    from scipy.io import wavfile
    x = stereo(x).T.astype(np.float32)
    wavfile.write(str(path), sr, x)


def read_wav(path):
    from scipy.io import wavfile
    sr, x = wavfile.read(str(path))
    if x.dtype.kind == "i":
        x = x.astype(np.float64) / 32768.0
    x = x.astype(np.float64)
    return sr, x.T if x.ndim == 2 else x
