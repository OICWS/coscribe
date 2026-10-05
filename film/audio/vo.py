"""Voice-over: Kokoro TTS (run in its own venv as a subprocess), fitted to the VO timeline in
content/vo.js, then lightly EQ'd, compressed and given a touch of room so it sits in the mix.

Fitting rule: each line starts (first phoneme) exactly at its `at` and must end before its
subtitle `out` (minus a small margin). Speed starts at BASE_SPEED and may go 0.85-1.05; if the
line still does not fit at 1.05, long internal pauses are tightened; if it still does not fit,
it is reported (and kept at the fastest natural version).
VO-07 (the typed sentence, no subtitle) is fitted to the typing itself (first to last key).
VO-10 ("coscribe") is soft and optional; it is spoken by the English G2P with a pronunciation
override so it is "co-scribe" (for zh with the Chinese voice on the zh model).

Renders are cached in audio/cache/vo/ by a hash of (text, voice, speed, pipeline), so
rebuilds only call Kokoro when a line, voice or speed changes.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np
from scipy import signal

from dsp import (N, SR, compress, db, fade, filt, highpass, lufs, make_ir, peak, read_wav, shelf, convolve_stereo)

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "vo"
TTS_PY = os.environ.get("FILM_TTS_PYTHON", "/tmp/claude-0/ttsenv/bin/python")
CA = os.environ.get("FILM_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")
REPO = {"zh": "hexgrad/Kokoro-82M-v1.1-zh", "en": "hexgrad/Kokoro-82M"}
LANG_CODE = {"zh": "z", "en": "a"}
BASE_SPEED = 0.92
SPEED_MIN, SPEED_MAX = 0.85, 1.05
MARGIN = 0.08            # end this much before the subtitle disappears
COSCRIBE = "[coscribe](/kˈOskɹˌIb/)"   # "co-scribe" (misaki would otherwise say "kahs-crib")

# What is actually sent to the TTS when it must differ from the subtitle text (pronunciation or
# phrasing fixes found by the ASR round-trip). Keys: (lang, id).
SPOKEN = {}

# level offsets (dB) relative to the normal VO level
SOFT = {"VO-07": -5.0, "VO-10": -6.0}
INCLUDE_VO10 = True


def _key(job):
    s = json.dumps([job["text"], job["voice"], round(job["speed"], 3), job["lang_code"], job["repo"], 2], ensure_ascii=False)
    return hashlib.sha1(s.encode()).hexdigest()[:16]


_WORKER = None


def _worker():
    global _WORKER
    if _WORKER is None or _WORKER.poll() is not None:
        env = dict(os.environ, SSL_CERT_FILE=CA, REQUESTS_CA_BUNDLE=CA, TOKENIZERS_PARALLELISM="false",
                   HF_HUB_OFFLINE=os.environ.get("HF_HUB_OFFLINE", "0"))
        CACHE.mkdir(parents=True, exist_ok=True)
        log = open(CACHE / "worker.log", "w")
        _WORKER = subprocess.Popen([TTS_PY, str(HERE / "tts_worker.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=log, text=True, env=env, bufsize=1)
    return _WORKER


def close():
    global _WORKER
    if _WORKER is not None and _WORKER.poll() is None:
        _WORKER.stdin.close()
        _WORKER.wait(timeout=60)
    _WORKER = None


def tts(jobs):
    """Render (cached) and return {path: 24 kHz mono array}. One persistent Kokoro worker per build."""
    CACHE.mkdir(parents=True, exist_ok=True)
    todo = []
    for j in jobs:
        j["out"] = str(CACHE / f"{_key(j)}.wav")
        if not Path(j["out"]).exists():
            todo.append(j)
    if todo:
        w = _worker()
        w.stdin.write(json.dumps(todo, ensure_ascii=False) + "\n")
        w.stdin.flush()
        while True:
            line = w.stdout.readline()
            if not line:
                raise RuntimeError("Kokoro worker died:\n" + (CACHE / "worker.log").read_text()[-3000:])
            if '"done"' in line:
                break
    out = {}
    for j in jobs:
        sr, x = read_wav(j["out"])
        out[j["out"]] = x
    return out


def _trim(x, sr=24000):
    """Indices of the first and last speech sample (RMS gate), with a little pre/post roll."""
    w = int(0.01 * sr)
    e = np.sqrt(np.convolve(x ** 2, np.ones(w) / w, mode="same"))
    thr = max(10 ** (-50 / 20), e.max() * 10 ** (-38 / 20))
    idx = np.nonzero(e > thr)[0]
    if not len(idx):
        return 0, len(x)
    a = max(0, idx[0] - int(0.015 * sr))
    b = min(len(x), idx[-1] + int(0.06 * sr))
    return a, b


def _tighten(x, sr=24000, max_gap=0.13):
    """Shorten internal pauses longer than max_gap (keeps natural breath at the edges)."""
    w = int(0.01 * sr)
    e = np.sqrt(np.convolve(x ** 2, np.ones(w) / w, mode="same"))
    quiet = e < e.max() * 10 ** (-36 / 20)
    out, i, L = [], 0, len(x)
    segs = []
    k = 0
    while k < L:
        if quiet[k]:
            j = k
            while j < L and quiet[j]:
                j += 1
            segs.append((k, j))
            k = j
        else:
            k += 1
    cur = 0
    for a, b in segs:
        if a == 0 or b == L or (b - a) / sr <= max_gap:
            continue
        keep = int(max_gap * sr)
        out.append(x[cur:a + keep // 2])
        cur = b - keep // 2
    out.append(x[cur:])
    # tiny crossfades at the joins
    y = out[0]
    for seg in out[1:]:
        f = min(120, len(y), len(seg))
        r = np.linspace(0, 1, f)
        y = np.concatenate([y[:-f], y[-f:] * (1 - r) + seg[:f] * r, seg[f:]])
    return y


def _job(lang, text, voice, speed):
    if text == "coscribe":
        return {"text": COSCRIBE, "voice": voice, "speed": speed, "lang_code": "a", "repo": REPO[lang]}
    return {"text": text, "voice": voice, "speed": speed, "lang_code": LANG_CODE[lang], "repo": REPO[lang]}


def _render_line(lang, text, voice, speed):
    j = _job(lang, text, voice, speed)
    x = list(tts([j]).values())[0]
    a, b = _trim(x)
    return x[a:b]


def plan(vo, lang, events):
    """Choose speed per line; returns list of dicts with the 24 kHz take and fit info."""
    voice = vo["voices"][lang]
    keys = [e["t"] for e in events if e["name"] == "key" and 100.0 <= e["t"] <= 104.0]
    res = []
    # batch the first pass so Kokoro loads once
    lines = [ln for ln in vo["lines"] if ln.get(lang)]
    first = [_job(lang, SPOKEN.get((lang, ln["id"]), ln[lang]), voice, BASE_SPEED) for ln in lines]
    tts(first)
    for ln in lines:
        lid, at = ln["id"], float(ln["at"])
        text = SPOKEN.get((lang, lid), ln[lang])
        if lid == "VO-10" and not INCLUDE_VO10:
            continue
        if ln.get("out"):
            limit = float(ln["out"]) - MARGIN
            target = None
        elif lid == "VO-07":
            span = (keys[-1] - keys[0]) if len(keys) > 3 else 2.5
            limit = (keys[-1] + 0.35) if keys else at + 2.8
            target = min(span, limit - at)
        else:
            limit = 119.6
            target = None
        avail = limit - at
        speed = BASE_SPEED
        x = _render_line(lang, text, voice, speed)
        d = len(x) / 24000
        tight = False
        if target is not None:   # sync with typing: aim at the typing span
            for _ in range(3):
                s2 = float(np.clip(speed * d / target, SPEED_MIN, SPEED_MAX))
                if abs(s2 - speed) < 0.01:
                    break
                speed = s2
                x = _render_line(lang, text, voice, speed)
                d = len(x) / 24000
        for _ in range(4):
            if d <= avail:
                break
            s2 = float(np.clip(speed * d / avail * 1.01, SPEED_MIN, SPEED_MAX))
            if s2 <= speed + 1e-3:
                break
            speed = s2
            x = _render_line(lang, text, voice, speed)
            d = len(x) / 24000
        if d > avail:
            x = _tighten(x)
            d = len(x) / 24000
            tight = True
        res.append({"id": lid, "text": text, "at": at, "speed": round(speed, 3), "dur": round(d, 3),
                    "end": round(at + d, 3), "limit": round(limit, 3), "fits": d <= avail + 1e-3,
                    "over": round(max(0.0, d - avail), 3), "tightened": tight, "x24": x,
                    "soft": SOFT.get(lid, 0.0)})
    return res


def process(x24, soft_db=0.0):
    """24 kHz take -> 48 kHz processed mono: EQ, gentle compression, level."""
    x = signal.resample_poly(x24, 2, 1)
    x = filt(x, highpass(85, 0.7), peak(240, -2.0, 1.0), peak(3300, 1.5, 0.9), shelf(9000, -3.0, True))
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.5
    x = compress(x, thresh_db=-18, ratio=2.5, attack=0.006, release=0.12, knee=8)
    x = fade(x, 0.008, 0.03)
    # equal loudness for every line (soft lines below)
    L = lufs(np.stack([x, x]))
    x = x * db(-23.0 - L + soft_db)
    return x


def render(vo, lang, events):
    """VO stem (2, N) for lang, the per-line report, and the processed mono lines (for ASR)."""
    lines = plan(vo, lang, events)
    dry = np.zeros(N)
    out_lines = []
    for ln in lines:
        x = process(ln["x24"], ln["soft"])
        i = int(round(ln["at"] * SR))
        m = min(len(x), N - i)
        dry[i:i + m] += x[:m]
        out_lines.append((ln, x))
    ir = make_ir(dur=0.9, t60_low=0.5, t60_high=0.25, predelay=0.006, seed=31, width=0.7, hp=180, lp=6500)
    wet = convolve_stereo(dry, ir)
    st = np.stack([dry, dry]) + db(-16) * wet
    for ln in lines:
        ln.pop("x24", None)
    return st, lines, out_lines
