// Voiceover and its subtitles. Strict JSON after the "=" so the Python audio
// build can read it too (audio/build_vo.py). Times in seconds.
// at: VO start; in/out: subtitle visible window; bg: what the line sits on
// ("light" surface or "dark" depth); sub: false = no subtitle (text already on screen).
window.FILM_VO = {
  "voices": { "zh": "zm_010", "en": "am_michael" },
  "lines": [
    { "id": "VO-01", "at": 1.5,   "in": 1.6,   "out": 4.9,   "bg": "light", "zh": "有些工作，一句话就能说清楚。", "en": "Some work can be said in one sentence." },
    { "id": "VO-02", "at": 5.2,   "in": 5.2,   "out": 7.6,   "bg": "light", "zh": "却要花掉一个人好几天。", "en": "And still cost someone days." },
    { "id": "VO-03", "at": 26.5,  "in": 26.4,  "out": 28.6,  "bg": "dark",  "zh": "不只是做得快，是做得对。", "en": "Not just done fast. Done right." },
    { "id": "VO-04", "at": 49.0,  "in": 48.4,  "out": 49.9,  "bg": "dark",  "zh": "有观点，也有分寸。", "en": "An argument, and a sense of proportion." },
    { "id": "VO-05", "at": 71.0,  "in": 70.8,  "out": 72.8,  "bg": "dark",  "zh": "它会读到最后一页。", "en": "It reads to the last page." },
    { "id": "VO-06", "at": 90.5,  "in": 90.3,  "out": 92.8,  "bg": "dark",  "zh": "需要写代码的时候，它也会。", "en": "When the work needs code, it writes code." },
    { "id": "VO-07", "at": 100.6, "in": 0,     "out": 0,     "bg": "light", "sub": false, "zh": "以后每周一，都这样做一遍。", "en": "Every Monday, do this again." },
    { "id": "VO-08", "at": 110.5, "in": 110.4, "out": 113.5, "bg": "dark",  "zh": "把最好的一次，变成每一次。", "en": "Take the best run. Make it every run." },
    { "id": "VO-09", "at": 115.0, "in": 114.9, "out": 117.4, "bg": "light", "zh": "你只需要说清楚想要什么。", "en": "You only have to say what you want." },
    { "id": "VO-10", "at": 118.4, "in": 0,     "out": 0,     "bg": "light", "sub": false, "zh": "coscribe", "en": "coscribe" }
  ]
};
