"""유창성 지표 (규칙 기반) — 더듬기·지연을 숫자로 만들고, 점수 상한(cap)을 정한다.

LLM 이 내용만 보고 후하게 주는 것을 막기 위해, 상한은 여기서 결정적으로 계산하고
채점 후에 강제 적용한다.
"""
import difflib
import re

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "hm", "mm", "mmm", "eh", "m"}  # "m": 짧은 um 이 "M" 으로 적히는 경우
REPEAT_OK = {"very", "that", "had", "so", "bye", "no"}

PAUSE_MIN = 1.0   # 이 이상은 '멈춤'으로 표시
LONG_PAUSE = 2.0  # 감점 대상 멈춤
VERY_LONG = 3.0   # 심각한 멈춤


def norm(w: str) -> str:
    return re.sub(r"[^a-z0-9'\-]", "", w.lower()).strip("-'")


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _num_words(n: int) -> list[str]:
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")]
    if n < 1000:
        return [_ONES[n // 100], "hundred"] + (_num_words(n % 100) if n % 100 else [])
    if n < 1_000_000:
        return _num_words(n // 1000) + ["thousand"] + (_num_words(n % 1000) if n % 1000 else [])
    return [str(n)]


def spoken(word: str) -> list[str]:
    """지문 비교용으로 '읽는 소리' 단위로 펼친다. 음성 인식은 thirty percent 를 "30 %" 로,
    eight a.m. 을 "8am" 으로 적기 때문에, 숫자 표기 차이를 잘못 읽은 것으로 세지 않게 한다."""
    w = word.lower().replace("%", " percent ").replace("$", " ")
    w = re.sub(r"(\d),(\d)", r"\1\2", w)
    w = re.sub(r"(\d)([a-z])", r"\1 \2", w)
    out = []
    for t in w.split():
        t = norm(t)
        if not t:
            continue
        out += _num_words(int(t)) if t.isdigit() else [t]
    # "a .m." → a, m → am
    merged = []
    for t in out:
        if t == "m" and merged and merged[-1] in ("a", "p"):
            merged[-1] += "m"
        else:
            merged.append(t)
    return merged


def _script_diff(said_words: list[str], script: str) -> dict:
    ref = [s for t in script.split() for s in spoken(t)]
    said = [s for t in said_words for s in spoken(t)]
    sm = difflib.SequenceMatcher(a=ref, b=said, autojunk=False)
    missing, extra, substituted = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "delete":
            missing.extend(ref[i1:i2])
        elif op == "insert":
            extra.extend(said[j1:j2])
        elif op == "replace":
            substituted.append({"expected": " ".join(ref[i1:i2]), "heard": " ".join(said[j1:j2])})
    acc = sm.ratio()
    return {"accuracy": round(acc * 100), "missing": missing, "extra": extra, "substituted": substituted}


def analyze(words: list[dict], response_time: int, part: int, script: str | None = None, max_score: int = 3) -> dict:
    toks = [dict(w, n=norm(w["w"])) for w in words]
    toks = [t for t in toks if t["n"]]
    if not toks:
        return {"no_response": True, "latency": None, "pauses": [], "filler_count": 0,
                "filler_ratio": 0, "repetitions": 0, "wpm": 0, "coverage": 0, "word_count": 0,
                "issues": ["무응답 — 녹음에서 말소리를 찾지 못했습니다."], "penalty_points": 99,
                "cap": 0, "marked": "", "low_confidence": [], "script_diff": None}

    latency = toks[0]["s"]
    pauses = []
    for a, b in zip(toks, toks[1:]):
        gap = b["s"] - a["e"]
        if gap >= PAUSE_MIN:
            pauses.append({"at": round(a["e"], 1), "dur": round(gap, 1), "after": a["w"]})
    fillers = [t for t in toks if t["n"] in FILLERS]
    content = [t for t in toks if t["n"] not in FILLERS]
    reps = 0
    for a, b in zip(content, content[1:]):
        if a["n"] == b["n"] and a["n"] not in REPEAT_OK:
            reps += 1
    reps += sum(1 for t in toks if t["w"].endswith("-"))  # 끊긴 단어 "I- I"

    span = max(toks[-1]["e"] - toks[0]["s"], 1.0)
    wpm = round(len(content) / span * 60)
    coverage = min(toks[-1]["e"] / response_time, 1.0) if response_time else 1.0
    filler_ratio = len(fillers) / len(toks)
    long_pauses = [p for p in pauses if p["dur"] >= LONG_PAUSE]
    very_long = [p for p in pauses if p["dur"] >= VERY_LONG]

    # ── 감점 포인트: 심각 2점, 보통 1점 ──
    issues, pp = [], 0

    def add(points, msg):
        nonlocal pp
        pp += points
        issues.append(msg)

    if latency >= 5:
        add(2, f"답변 시작이 {latency:.1f}초 늦었습니다 (실전에서는 비프음 후 1~2초 안에 시작).")
    elif latency >= 3:
        add(1, f"답변 시작까지 {latency:.1f}초 걸렸습니다.")
    if len(very_long) >= 2:
        add(2, f"3초 이상 침묵이 {len(very_long)}번 있었습니다.")
    elif len(long_pauses) >= 2:
        add(1, f"2초 이상 멈춤이 {len(long_pauses)}번 있었습니다.")
    if filler_ratio > 0.15:
        add(2, f"um/uh 같은 필러가 {len(fillers)}회 (전체의 {filler_ratio:.0%}) — 매우 잦습니다.")
    elif filler_ratio > 0.08:
        add(1, f"um/uh 같은 필러가 {len(fillers)}회 나왔습니다.")
    if reps >= 3:
        add(1, f"같은 단어 반복·말 끊김이 {reps}회 있었습니다.")
    if part != 1:
        if coverage < 0.4 and response_time >= 15:
            add(2, f"답변 시간의 {coverage:.0%}만 사용했습니다 — 분량이 크게 부족합니다.")
        elif coverage < 0.65 and response_time >= 15:
            add(1, f"답변 시간의 {coverage:.0%}만 사용했습니다.")
        if wpm < 70:
            add(1, f"말하기 속도가 분당 {wpm}단어로 느립니다 (목표 110~150).")

    diff = None
    if script:
        # 원본 단어 목록에서 뽑는다 — toks 는 norm 이 빈 "%" 같은 토큰을 이미 버렸다
        diff = _script_diff([w["w"] for w in words if norm(w["w"]) not in FILLERS], script)
        if diff["accuracy"] < 75:
            add(2, f"지문 일치율 {diff['accuracy']}% — 빠뜨리거나 잘못 읽은 부분이 많습니다.")
        elif diff["accuracy"] < 90:
            add(1, f"지문 일치율 {diff['accuracy']}%.")
        if wpm < 100:
            add(1, f"읽기 속도가 분당 {wpm}단어로 느립니다 (목표 130~160).")

    if max_score >= 5:
        cap = 5 if pp == 0 else 4 if pp <= 2 else 3 if pp <= 4 else 2
    else:
        cap = 3 if pp <= 1 else 2 if pp <= 3 else 1

    # 멈춤·필러를 표시한 전사문: (um) [2.3s]
    marked = []
    prev = None
    for t in toks:
        if prev is not None:
            gap = t["s"] - prev["e"]
            if gap >= PAUSE_MIN:
                marked.append(f"[{gap:.1f}s]")
        marked.append(f"({t['w']})" if t["n"] in FILLERS else t["w"])
        prev = t
    low_conf = sorted({t["w"].strip(",.?!") for t in content if t["p"] < 0.5})

    return {
        "no_response": False,
        "latency": round(latency, 1),
        "pauses": pauses,
        "filler_count": len(fillers),
        "filler_ratio": round(filler_ratio, 3),
        "repetitions": reps,
        "wpm": wpm,
        "coverage": round(coverage, 2),
        "word_count": len(content),
        "issues": issues,
        "penalty_points": pp,
        "cap": cap,
        "marked": " ".join(marked),
        "low_confidence": low_conf,
        "script_diff": diff,
    }
