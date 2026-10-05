"""Build the film's soundtrack: score + SFX + VO, mixed and mastered.

  python3 film/audio/build.py [--lang en|zh|all] [--asr] [--fresh]      (default: en)

1. reads the sound-sync events from the live page (window.FILM_EVENTS, headless Chromium),
2. synthesises the score from the cue sheet (score.py; cached by source + relevant events),
3. places the SFX on the events (sfx.py), with the script's SFX column as fallback,
4. renders the VO with Kokoro (vo.py; cached per line) and fits it to the VO timeline -- only when
   content/vo.js has "speak": true; the current film has no narration ("speak": false), so the
   lines are on-screen text and the score treats them as musical moments (score.py),
5. (with VO only) ducks the music under the VO; masters to -16 LUFS integrated / <= -1 dBTP, 48 kHz stereo,
   exactly 120.0 s, and encodes out/mix_<lang>.m4a (AAC 256k).
Stems: out/music.wav, out/sfx_<lang>.wav, out/vo_<lang>.wav. Report: cache/report_<lang>.json.
--asr transcribes every VO line (dry and in the final mix) with faster-whisper and compares.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import dsp  # noqa: E402
import events as film_events  # noqa: E402
import score  # noqa: E402
import sfx  # noqa: E402
import vo as vomod  # noqa: E402
from dsp import N, SR, db  # noqa: E402

OUT = HERE / "out"
CACHE = HERE / "cache"
TARGET_LUFS = -16.0
CEIL_DBTP = -1.5      # limiter ceiling before AAC; the encoded file is verified to be <= -1.0 dBTP
MUSIC_LUFS = -19.0    # music stem level before ducking (relative balance)
VO_GAIN_DB = 6.0      # VO lines are processed to -23 LUFS each -> about -17 in the mix
DUCK_DB = -6.0


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- music (cached)
def music_key(evs, lines):
    h = hashlib.sha1()
    for f in ("score.py", "instruments.py", "dsp.py"):
        h.update((HERE / f).read_bytes())
    rel = [e for e in evs if sfx.family(e) in ("glass", "music")]
    h.update(json.dumps([rel, lines], sort_keys=True).encode())
    return h.hexdigest()[:16]


def build_music(evs, lines, fresh=False):
    CACHE.mkdir(exist_ok=True)
    p = CACHE / f"music_{music_key(evs, lines)}.npy"
    if p.exists() and not fresh:
        return np.load(p).astype(np.float64), True
    x = score.render(evs, lines)
    # level: fixed integrated loudness for the stem
    x *= db(MUSIC_LUFS - dsp.lufs(x))
    for old in CACHE.glob("music_*.npy"):
        old.unlink()
    np.save(p, x.astype(np.float32))
    return x, False


# ---------------------------------------------------------------- mix
def duck_curve(vo_st, depth_db):
    """Smooth sidechain gain (linear) from VO presence: 150 ms look-ahead, ~120 ms attack, ~600 ms release."""
    m = vo_st.mean(0)
    hop = SR // 1000
    k = N // hop
    r = np.sqrt((m[:k * hop].reshape(k, hop) ** 2).mean(1))
    lv = 20 * np.log10(r + 1e-9)
    pres = np.clip((lv + 52) / 10, 0, 1)                  # 0 below -52 dBFS, 1 above -42
    from scipy.ndimage import maximum_filter1d
    pres = maximum_filter1d(pres, 220)                    # hold through short gaps between words
    pres = np.concatenate([pres[150:], np.zeros(150)])    # look-ahead 150 ms
    out = np.empty(k)
    e = 0.0
    a, rl = np.exp(-1 / 120), np.exp(-1 / 600)
    for i in range(k):
        v = pres[i]
        g = a if v > e else rl
        e = g * e + (1 - g) * v
        out[i] = e
    g = np.interp(np.arange(N), np.arange(k) * hop, out)
    return db(depth_db * g), g


def master(x):
    """Gain to TARGET_LUFS, true-peak limit to CEIL_DBTP; iterate since limiting lowers loudness."""
    g = TARGET_LUFS - dsp.lufs(x)
    y = x
    for _ in range(3):
        y = dsp.limiter(x * db(g), CEIL_DBTP)
        L = dsp.lufs(y)
        if abs(L - TARGET_LUFS) < 0.1:
            break
        g += TARGET_LUFS - L
    # exact length, soft final 5 ms
    y = y[:, :N]
    y[:, -240:] *= np.linspace(1, 0, 240)
    return y


def ffmpeg_measure(path):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = p.stderr
    import re
    I = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", txt)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", txt)[-1])
    return I, tp


def encode(wav, m4a):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                    "-ac", "2", "-movflags", "+faststart", str(m4a)], check=True)
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(m4a)],
                             capture_output=True, text=True).stdout.strip())
    return d


# ---------------------------------------------------------------- ASR
def run_asr(jobs):
    py = vomod.TTS_PY
    env = dict(os.environ, SSL_CERT_FILE=vomod.CA, REQUESTS_CA_BUNDLE=vomod.CA)
    p = subprocess.run([py, str(HERE / "asr_worker.py")], input=json.dumps(jobs, ensure_ascii=False), capture_output=True,
                       text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def _norm_text(s, lang):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKC", s).lower()
    if lang == "zh":
        return re.sub(r"[^\w]", "", s).replace("_", "")
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", s).split())


def _edit(a, b):
    d = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(b) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (a[i - 1] != b[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(b)]


def err_rate(ref, hyp, lang):
    r, h = _norm_text(ref, lang), _norm_text(hyp, lang)
    if lang == "en":
        r, h = r.split(), h.split()
    return _edit(r, h) / max(1, len(r))


def asr_check(lang, lines, out_lines, mix):
    from scipy import signal
    d = CACHE / "asr"
    d.mkdir(parents=True, exist_ok=True)
    jobs = []
    for ln, x in out_lines:
        p = d / f"{lang}_{ln['id']}_dry.wav"
        dsp_write_mono16(p, x)
        jobs.append({"path": str(p), "lang": lang, "id": ln["id"], "src": "dry"})
        a, b = int((ln["at"] - 0.25) * SR), int((ln["end"] + 0.35) * SR)
        p2 = d / f"{lang}_{ln['id']}_mix.wav"
        dsp_write_mono16(p2, mix[:, max(0, a):min(N, b)].mean(0))
        jobs.append({"path": str(p2), "lang": lang, "id": ln["id"], "src": "mix"})
    res = run_asr(jobs)
    ref = {ln["id"]: ln["text"] for ln, _ in out_lines}
    out = []
    for r in res:
        rt = ref[r["id"]]
        if rt == "coscribe":
            ok = "scrib" in r["text"].lower() or "scribe" in r["text"].lower()
            e = 0.0 if ok else 1.0
        else:
            e = err_rate(rt, r["text"], lang)
        out.append({"id": r["id"], "src": r["src"], "ref": rt, "asr": r["text"], "err": round(e, 3)})
    return out


def dsp_write_mono16(p, x):
    from scipy import signal
    from scipy.io import wavfile
    y = signal.resample_poly(x, 1, 3)
    y = y / (np.max(np.abs(y)) + 1e-9) * 0.8
    wavfile.write(str(p), 16000, (y * 32767).astype(np.int16))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["zh", "en", "all"])
    ap.add_argument("--asr", action="store_true", help="ASR round-trip of every VO line (dry and in the mix)")
    ap.add_argument("--fresh", action="store_true", help="ignore the music cache")
    args = ap.parse_args()
    langs = ["zh", "en"] if args.lang == "all" else [args.lang]
    T0 = time.time()
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)

    log("events: loading the film in headless Chromium ...")
    evs, errs = film_events.extract_events(langs)
    for l in langs:
        from collections import Counter
        c = Counter(e["name"] for e in evs[l])
        log(f"  {l}: {len(evs[l])} events {dict(c)}" + (f"  PAGE ERRORS: {errs[l][:3]}" if errs[l] else ""))
    vo_data = film_events.load_vo()
    speak = bool(vo_data.get("speak", True))
    log(f"vo.js: speak={speak}" + ("" if speak else " -> no narration, no ducking; text lines are musical moments"))
    lines = [{k: ln.get(k) for k in ("id", "at", "in", "out")} for ln in vo_data["lines"]]

    t = time.time()
    music, cached = build_music(evs[langs[0]], lines, args.fresh)
    dsp.write_wav(OUT / "music.wav", music)
    log(f"music: {'cached' if cached else 'synthesised'} in {time.time() - t:.1f}s, {dsp.lufs(music):.1f} LUFS")

    summary = {}
    for lang in langs:
        t = time.time()
        fx, rep = sfx.render(evs[lang])
        dsp.write_wav(OUT / f"sfx_{lang}.wav", fx)
        log(f"sfx[{lang}]: {time.time() - t:.1f}s; fallback cues used: {rep['fallback_used']}")
        log(f"  event names handled ({len(rep['handled'])}): " + ", ".join(f"{k}->{v}" for k, v in rep["handled"].items()))
        if rep["guessed"]:
            log(f"  UNHANDLED names mapped by guess: {rep['guessed']}")
        log(f"  UNHANDLED names (soft tick used): {rep['unknown'] or 'none'}")

        if not speak:
            mix = master(music + fx)
            vo_lines, out_lines = [], []
        else:
            mix, vo_lines, out_lines = mix_with_vo(vo_data, lang, evs[lang], music, fx)
        wav = OUT / f"mix_{lang}.wav"
        dsp.write_wav(wav, mix)
        m4a = OUT / f"mix_{lang}.m4a"
        dur = encode(wav, m4a)
        I, tp = ffmpeg_measure(m4a)
        if tp > -1.0:  # AAC overshoot: pull down and re-encode once
            mix *= db(-1.0 - tp - 0.2)
            dsp.write_wav(wav, mix)
            dur = encode(wav, m4a)
            I, tp = ffmpeg_measure(m4a)
        log(f"mix[{lang}]: {m4a.name}  {dur:.3f}s  {I:.1f} LUFS  TP {tp:.1f} dBTP  (internal {dsp.lufs(mix):.1f} LUFS)")
        info = {"speak": speak, "lines": vo_lines, "sfx": rep, "lufs": I, "true_peak": tp, "duration": dur}
        if args.asr and speak:
            t = time.time()
            info["asr"] = asr_check(lang, vo_lines, out_lines, mix)
            for a in info["asr"]:
                log(f"  asr {a['id']} {a['src']:3s} err {a['err']:.2f}  '{a['asr']}'")
            log(f"  asr: {time.time() - t:.1f}s")
        (CACHE / f"report_{lang}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=str))
        summary[lang] = info
    vomod.close()
    log(f"done in {time.time() - T0:.1f}s")


def mix_with_vo(vo_data, lang, evs, music, fx):
    """Narrated version: VO stem, ~6 dB sidechain ducking of the music (and -3 dB of the SFX)."""
    t = time.time()
    vost, lines, out_lines = vomod.render(vo_data, lang, evs)
    vost *= db(VO_GAIN_DB)
    dsp.write_wav(OUT / f"vo_{lang}.wav", vost)
    log(f"vo[{lang}]: {time.time() - t:.1f}s")
    for ln in lines:
        flag = "ok " if ln["fits"] else "OVER"
        log(f"  {flag} {ln['id']} at {ln['at']:6.2f} -> {ln['end']:6.2f} (limit {ln['limit']:6.2f}) speed {ln['speed']:.3f}"
            f"{' tightened' if ln['tightened'] else ''}{'  over by %.2fs' % ln['over'] if not ln['fits'] else ''}  {ln['text']}"
            + (f"  [spoken: {ln['spoken']}]" if ln["spoken"] != ln["text"] else ""))

    gm, pres = duck_curve(vost, DUCK_DB)
    gs = db(-3.0 * pres)
    mix = master(music * gm[None] + fx * gs[None] + vost)
    return mix, lines, out_lines


if __name__ == "__main__":
    main()
