"""ASR round-trip worker (faster-whisper 'small', CPU). Runs inside the Kokoro venv.

stdin : JSON list [{path, lang}]   stdout: JSON list [{path, lang, text}]
"""
import json
import sys
import warnings

warnings.filterwarnings("ignore")


def main():
    from faster_whisper import WhisperModel

    jobs = json.load(sys.stdin)
    model = WhisperModel("small", device="cpu", compute_type="int8")
    out = []
    for j in jobs:
        prompt = "以下是普通话的句子。" if j["lang"] == "zh" else None
        segs, _ = model.transcribe(j["path"], language=j["lang"], beam_size=5, initial_prompt=prompt,
                                   vad_filter=False, condition_on_previous_text=False)
        out.append({**j, "text": "".join(s.text for s in segs).strip()})
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
