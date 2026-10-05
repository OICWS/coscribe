"""Kokoro TTS worker. Runs inside the Kokoro venv (see vo.py), not the system python.

stdin : one JSON list of jobs per line {out, text, voice, speed, lang_code, repo}
stdout: one JSON line per finished job {out, dur}, then {"done": n} after each batch
Writes 24 kHz mono float32 WAVs. Pipelines are loaded once per (lang_code, repo).
"""
import json
import sys
import warnings

warnings.filterwarnings("ignore")


def main():
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    pipes = {}
    for line in sys.stdin:
        if not line.strip():
            continue
        jobs = json.loads(line)
        run(jobs, pipes, np, sf, KPipeline)
        print(json.dumps({"done": len(jobs)}), flush=True)


def run(jobs, pipes, np, sf, KPipeline):
    for j in jobs:
        k = (j["lang_code"], j["repo"])
        if k not in pipes:
            pipes[k] = KPipeline(lang_code=j["lang_code"], repo_id=j["repo"])
        pipe = pipes[k]
        parts = [r.audio.numpy() for r in pipe(j["text"], voice=j["voice"], speed=j["speed"], split_pattern=None)
                 if r.audio is not None]
        a = np.concatenate(parts).astype(np.float32) if parts else np.zeros(2400, np.float32)
        sf.write(j["out"], a, 24000, subtype="FLOAT")
        print(json.dumps({"out": j["out"], "dur": len(a) / 24000}), flush=True)


if __name__ == "__main__":
    main()
