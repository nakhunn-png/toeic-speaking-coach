"""문제 은행 로딩 — data/questions.json + data/images/ (Part 2 사진 자동 등록)."""
import json

from .config import DATA, IMAGES

# part: (시작 문항 번호, [(준비, 답변), ...], 문항당 만점)
PARTS = {
    1: {"name": "Read a text aloud", "ko": "지문 읽기", "first": 1, "timing": [(45, 45), (45, 45)], "max": 3},
    2: {"name": "Describe a picture", "ko": "사진 묘사", "first": 3, "timing": [(45, 30), (45, 30)], "max": 3},
    3: {"name": "Respond to questions", "ko": "질문에 답하기", "first": 5, "timing": [(3, 15), (3, 15), (3, 30)], "max": 3},
    4: {"name": "Respond to questions using information provided", "ko": "제공된 정보로 답하기",
        "first": 8, "timing": [(3, 15), (3, 15), (3, 30)], "max": 3},
    5: {"name": "Express an opinion", "ko": "의견 말하기", "first": 11, "timing": [(45, 60)], "max": 5},
}

VOICES = ["en-US-GuyNeural", "en-US-JennyNeural", "en-GB-RyanNeural", "en-GB-SoniaNeural",
          "en-AU-WilliamNeural", "en-AU-NatashaNeural", "en-US-AriaNeural", "en-US-ChristopherNeural"]
NARRATOR = "en-US-JennyNeural"

_items: dict[str, dict] = {}


def _image_sets() -> list[dict]:
    files = sorted(p for p in IMAGES.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    sets = []
    for i in range(0, len(files), 2):
        pair = files[i:i + 2]
        sets.append({"id": f"p2-{pair[0].stem}", "items": [{"image": f.name} for f in pair]})
    return sets


def load() -> dict:
    raw = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    raw["part2"] = _image_sets()
    _items.clear()
    out = {}
    for part, meta in PARTS.items():
        sets = []
        for si, s in enumerate(raw.get(f"part{part}", [])):
            voice = VOICES[(si + part) % len(VOICES)]
            items = []
            for i, it in enumerate(s["items"]):
                prep, resp = meta["timing"][min(i, len(meta["timing"]) - 1)]
                item = {
                    "id": f"{s['id']}-{i + 1}",
                    "set_id": s["id"],
                    "part": part,
                    "index": i,
                    "qnum": meta["first"] + i,
                    "prep": prep,
                    "resp": resp,
                    "max": meta["max"],
                    "voice": voice,
                    "q": it.get("q"),
                    "script": it.get("script"),
                    "image": it.get("image"),
                    "intro": s.get("intro"),
                    "info": s.get("info"),
                    # Part 4 의 마지막 질문은 실제 시험처럼 두 번 들려준다
                    "play_twice": part == 4 and i == 2,
                }
                items.append(item)
                _items[item["id"]] = item
            sets.append({"id": s["id"], "part": part, "voice": voice, "items": items})
        out[part] = {"meta": {**meta, "timing": meta["timing"]}, "sets": sets}
    return {"parts": out, "narrator": NARRATOR}


def get_item(item_id: str) -> dict:
    if item_id not in _items:
        load()
    return _items[item_id]


def templates() -> dict:
    return json.loads((DATA / "templates.json").read_text(encoding="utf-8"))
