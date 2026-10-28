"""채점·피드백 — 설치된 Claude Code 를 헤드리스(`claude -p`)로 호출한다 (API 비용 없음, 구독 사용량 차감).

유창성 상한(cap)은 fluency.py 가 계산한 값을 채점 후에 강제 적용한다.
"""
import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import content
from .config import CFG, GRADER_WS, IMAGES

RUBRIC = {
    1: """[Part 1 지문 읽기 · 0~3점] 발음, 억양·강세, 끊어 읽기만 평가한다.
3: 사소한 실수만 있고 매우 알아듣기 쉬움 / 2: 실수가 있지만 대체로 이해 가능 / 1: 실수가 잦아 이해가 어려움 / 0: 무응답·지문과 무관""",
    2: """[Part 2 사진 묘사 · 0~3점]
3: 사진의 핵심을 묘사, 적절한 어휘·문법, 알아듣기 쉬움 / 2: 묘사는 되지만 어휘·문법·발음 실수가 의미를 일부 흐림 / 1: 사진과 관련은 있으나 의미 전달이 제한적 / 0: 무응답·무관""",
    3: """[Part 3 질문에 답하기 · 0~3점]
3: 질문에 완전하고 적절하게 답함, 알아듣기 쉽고 문법·어휘 적절 / 2: 답은 하지만 일부 불명확·부적절 / 1: 질문에 제대로 답하지 못하거나 이해가 어려움 / 0: 무응답·무관""",
    4: """[Part 4 제공된 정보로 답하기 · 0~3점]
3: 표의 정보를 정확하고 완전하게 전달, 알아듣기 쉬움 / 2: 답은 하지만 정보가 일부 부정확·불완전 / 1: 필요한 정보 전달 실패 / 0: 무응답·무관
※ 표의 정보와 다른 사실(시간·날짜·장소·이름)을 말하면 반드시 감점한다.""",
    5: """[Part 5 의견 말하기 · 0~5점]
5: 의견이 명확하고 근거·예시로 잘 뒷받침, 유창하고 자연스러움, 문법·어휘 다양하고 정확
4: 의견 명확·뒷받침 충분하나 사소한 실수나 부자연스러움 / 3: 의견은 있으나 근거 전개가 제한적, 이해에 노력 필요
2: 의견은 있으나 뒷받침 부족, 이해 어려운 부분이 잦음 / 1: 질문 반복 수준·관련성 낮음 / 0: 무응답·무관""",
}

LEVELS = ["NL", "NM", "NH", "IL", "IM1", "IM2", "IM3", "IH", "AL"]
# 문항 점수 → 허용 등급 범위. 범위 안에서 AI 가 고르고, 벗어나면 가장 가까운 끝으로 맞춘다.
LEVEL_RANGE = {
    3: {0: ("NL", "NL"), 1: ("NH", "IL"), 2: ("IM1", "IM3"), 3: ("IH", "AL")},
    5: {0: ("NL", "NL"), 1: ("NM", "NH"), 2: ("NH", "IL"), 3: ("IM1", "IM2"), 4: ("IM3", "IH"), 5: ("IH", "AL")},
}


def clamp_level(level: str, score: int, max_score: int) -> str:
    lo, hi = LEVEL_RANGE[max_score][score]
    i_lo, i_hi = LEVELS.index(lo), LEVELS.index(hi)
    i = LEVELS.index(level) if level in LEVELS else i_lo
    return LEVELS[min(max(i, i_lo), i_hi)]


SYSTEM_PROMPT = ("You are an expert TOEIC Speaking rater and coach for Korean learners. "
                 "Follow the user's rubric and output rules exactly. Output exactly one JSON object and nothing else.")

WORDS_FOR_TIME = {15: "35~45", 30: "70~85", 45: "", 60: "130~150"}


def _question_block(item: dict) -> str:
    p = item["part"]
    if p == 1:
        return f"[읽어야 할 지문]\n{item['script']}"
    if p == 2:
        return "[사진] 이 메시지에 첨부된 사진을 직접 보고 채점한다."
    lines = []
    if item.get("intro"):
        lines.append(f"[상황 안내] {item['intro']}")
    if p == 4 and item.get("info"):
        info = item["info"]
        rows = "\n".join(" | ".join(r) for r in info["rows"])
        lines.append(f"[수험자가 보는 표]\n{info['title']}\n{info['subtitle']}\n{' | '.join(info['headers'])}\n{rows}")
        lines.append("(Part 4 질문은 화면에 보이지 않고 음성으로만 들려준다)")
    lines.append(f"[질문 Q{item['qnum']}] {item['q']}")
    return "\n".join(lines)


def _level_hint(max_score: int) -> str:
    return ", ".join(f"{s}점: {lo}~{hi}" if lo != hi else f"{s}점: {lo}"
                     for s, (lo, hi) in LEVEL_RANGE[max_score].items())


def build_prompt(item: dict, analysis: dict) -> str:
    p = item["part"]
    tpl = content.templates().get(str(p), {}).get("items", [])
    tpl_txt = "\n".join(f"- {t['id']}: {t['en']}  ({t['ko']})" for t in tpl)
    m = analysis["metrics"]
    words_hint = WORDS_FOR_TIME.get(item["resp"], "")
    metrics_txt = json.dumps({k: m[k] for k in (
        "latency", "pauses", "filler_count", "filler_ratio", "repetitions", "wpm", "coverage",
        "word_count", "low_confidence", "script_diff") if k in m}, ensure_ascii=False)

    if p == 1:
        answer_rule = ('"model_answer" 에는 지문 원문을 그대로 넣고, "reading_guide" 에 끊어 읽을 곳은 " / ", '
                       '강세 단어는 대문자, 억양은 ↗↘ 로 표시한 지문을 넣는다.')
    else:
        answer_rule = (f'"model_answer" 는 수험자의 답변 내용(아이디어)을 최대한 살려서, 위 만능문장을 뼈대로 다시 쓴 '
                       f'모범답안이다. 답변시간 {item["resp"]}초 안에 말할 수 있는 분량'
                       f'{f"(약 {words_hint}단어)" if words_hint else ""}. 쉬운 단어 위주로, 외워서 다른 문제에도 쓸 수 있게. '
                       '"reading_guide" 는 빈 문자열.')

    return f"""너는 TOEIC Speaking 공식 채점 기준을 잘 아는 채점관이자 한국인 수험생을 가르치는 강사다.
아래 수험자 답변을 채점하고 JSON 하나만 출력하라. JSON 앞뒤에 다른 글자를 쓰지 마라.

{RUBRIC[p]}

{_question_block(item)}

[답변시간] {item['resp']}초

[수험자 답변 전사] — (um) 은 필러, [2.3s] 는 침묵 길이
{m['marked'] or '(무응답)'}

[자동 측정 유창성 지표]
{metrics_txt}
- latency: 비프음 후 첫 단어까지 초 / coverage: 답변시간 중 사용 비율 / wpm: 분당 단어 수

[유창성 상한] 이 답변은 더듬기·지연 때문에 최대 {m['cap']}점까지만 받을 수 있다 (만점 {item['max']}).
감점 사유: {'; '.join(m['issues']) or '없음'}
내용이 좋아도 이 상한을 넘기지 마라.

[만능문장 라이브러리 — 피드백과 모범답안은 반드시 여기 문장을 우선 사용]
{tpl_txt}

[출력 규칙]
- 설명은 모두 한국어, 영어 문장은 영어로.
- "corrections": 수험자 문장 중 틀리거나 어색한 것을 최대 5개. fixed 는 가능하면 만능문장으로 바꾼 문장.
- "fluency_feedback": 멈춤·필러·지연이 어디서(몇 초, 어떤 단어 뒤) 왜 생겼는지와 다음에 그 자리를 메울 만능문장. 문제 없으면 칭찬 한 줄.
- {answer_rule}
- "templates_used": 모범답안에 쓴 만능문장 id 와 이 문제에 어떻게 끼워 넣었는지.
- 전사에서 숫자는 30 %, 8am 처럼 숫자로 적힐 수 있다. 지문의 thirty percent, eight a.m. 과 같은 것으로 보고 감점하지 마라.
- 전사는 음성인식 결과라 사소한 철자 오류는 인식 오류일 수 있다. 발음은 low_confidence 단어를 참고해 추정한다.

{{
  "score": 정수,
  "level": "이 답변 수준을 시험 전체에서 유지한다면 받을 등급. NL/NM/NH/IL/IM1/IM2/IM3/IH/AL 중 하나. 점수별 허용 범위 — {_level_hint(item['max'])}",
  "subscores": {{"pronunciation": 1-5, "fluency": 1-5, "grammar": 1-5, "vocabulary": 1-5, "content": 1-5}},
  "summary": "총평 1~2문장",
  "fluency_feedback": "...",
  "corrections": [{{"original": "...", "fixed": "...", "reason": "..."}}],
  "templates_used": [{{"id": "P3-3", "how": "..."}}],
  "model_answer": "...",
  "model_answer_ko": "모범답안 한국어 해석",
  "reading_guide": "...",
  "next_tip": "다음 문제에서 바로 써먹을 한 가지"
}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"채점 결과에서 JSON을 찾지 못했습니다: {text[:300]}")
    return json.loads(text[start:end + 1])


def _find_claude() -> str:
    exe = shutil.which("claude")
    if not exe:  # npm 전역 폴더가 PATH 에 없는 경우 (start.bat 로 실행할 때 흔함)
        # Claude 데스크톱 앱(MSIX) 안에서 npm 으로 설치하면 AppData 가 가상화되어
        # 실제 파일은 Packages\Claude_*\LocalCache\Roaming\npm 에 있다
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        for cand in (Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
                     *sorted(local.glob("Packages/Claude_*/LocalCache/Roaming/npm/claude.cmd")),
                     Path.home() / ".local" / "bin" / "claude.exe"):
            if cand.exists():
                exe = str(cand)
                break
    if not exe:
        raise RuntimeError("claude 명령을 찾지 못했습니다. Claude Code가 설치되어 있어야 채점할 수 있습니다.")
    return exe


_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _run_claude(prompt: str, image: Path | None = None) -> dict:
    # 경량 시작: MCP 서버·플러그인·스킬·사용자 설정·기본 시스템 프롬프트를 건너뛴다 (측정상 45초 → 16초).
    # --bare 는 API 키 전용이라 구독 로그인으로는 못 쓴다.
    # stream-json 입력: Part 2 사진을 메시지에 직접 첨부 — Read 도구로 읽게 하면 왕복이 늘어 80초 → 19초.
    cmd = [_find_claude(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--model", CFG["claude_model"], "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--no-session-persistence", "--system-prompt", SYSTEM_PROMPT, "--tools", ""]
    blocks = []
    if image is not None:
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": _MEDIA.get(image.suffix.lower(), "image/jpeg"),
                                                   "data": base64.b64encode(image.read_bytes()).decode()}})
    blocks.append({"type": "text", "text": prompt})
    msg = json.dumps({"type": "user", "message": {"role": "user", "content": blocks}}, ensure_ascii=False) + "\n"
    env = dict(os.environ,
               MAX_THINKING_TOKENS="0",                          # 채점 JSON 에는 긴 사고가 필요 없다 (응답 19초 → 12초)
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",     # 업데이트 확인·텔레메트리 등 시작 지연 제거
               DISABLE_AUTOUPDATER="1")
    # CREATE_NO_WINDOW: 창 없이 실행 중일 때 채점마다 콘솔 창이 깜빡이지 않게
    proc = subprocess.run(cmd, input=msg.encode("utf-8"), capture_output=True, timeout=300, cwd=str(GRADER_WS),
                          env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = proc.stdout.decode("utf-8", "replace")
    wrapper = None
    for line in reversed(out.strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "result":
            wrapper = obj
            break
    if wrapper is None:
        err = proc.stderr.decode("utf-8", "replace") or out
        raise RuntimeError(f"claude 실행 실패: {err[-600:]}")
    if wrapper.get("is_error") or proc.returncode != 0:
        msg = str(wrapper.get("result", ""))
        if "Not logged in" in msg or "/login" in msg:
            raise RuntimeError("Claude Code 명령줄이 로그인되어 있지 않습니다. 터미널에서 `claude` 를 한 번 실행해 "
                               "/login 으로 로그인한 뒤 다시 채점하세요.")
        raise RuntimeError(f"claude 오류: {msg[:600]}")
    return _extract_json(wrapper.get("result", ""))


def grade(item: dict, analysis: dict) -> dict:
    m = analysis["metrics"]
    if m.get("no_response"):
        return {"score": 0, "subscores": {}, "summary": "무응답으로 0점입니다.",
                "fluency_feedback": "비프음이 울리면 바로 첫 만능문장으로 시작하세요.",
                "corrections": [], "templates_used": [], "model_answer": item.get("script") or "",
                "model_answer_ko": "", "reading_guide": "", "next_tip": "아는 만능문장으로라도 시간을 채우세요.",
                "max": item["max"], "cap": 0, "capped": False, "level": "NL"}
    res = _run_claude(build_prompt(item, analysis), image=IMAGES / item["image"] if item["part"] == 2 else None)
    raw = int(res.get("score", 0))
    score = max(0, min(raw, m["cap"], item["max"]))
    res.update({"score": score, "raw_score": raw, "max": item["max"], "cap": m["cap"], "capped": score < raw,
                "level": clamp_level(str(res.get("level", "")).upper().strip(), score, item["max"])})
    if item["part"] == 1:
        res["model_answer"] = item["script"]
    return res
