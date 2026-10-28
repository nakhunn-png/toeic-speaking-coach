import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
STATIC = ROOT / "static"
IMAGES = DATA / "images"
RECORDINGS = DATA / "recordings"
HISTORY = DATA / "history"
TTS_CACHE = DATA / "tts_cache"
GRADER_WS = DATA / "grader_ws"

for d in (IMAGES, RECORDINGS, HISTORY, TTS_CACHE, GRADER_WS):
    d.mkdir(parents=True, exist_ok=True)

_DEFAULTS = {
    "port": 8765,
    "whisper_model": "small.en",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "claude_model": "sonnet",
    "grade_concurrency": 3,
}


def load() -> dict:
    cfg = dict(_DEFAULTS)
    p = ROOT / "config.json"
    if p.exists():
        cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


CFG = load()
