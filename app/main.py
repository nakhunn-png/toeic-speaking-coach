import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import content, fluency, grader, stt, tts
from .config import CFG, HISTORY, IMAGES, RECORDINGS, STATIC, TTS_CACHE

app = FastAPI(title="TOEIC Speaking Coach")
_grade_sem = asyncio.Semaphore(CFG["grade_concurrency"])


@app.middleware("http")
async def _no_cache_ui(request, call_next):
    # 화면 파일(html/js/css)은 항상 새로 받게 한다 — 앱을 고친 뒤 예전 화면이 남는 것 방지
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.on_event("startup")
async def _warm():
    asyncio.get_event_loop().run_in_executor(None, stt.warmup)


@app.get("/api/questions")
def questions():
    return content.load()


@app.get("/api/templates")
def templates():
    return content.templates()


@app.post("/api/analyze")
async def analyze(audio: UploadFile = File(...), item_id: str = Form(...)):
    try:
        item = content.get_item(item_id)
    except KeyError:
        raise HTTPException(404, "unknown item")
    rid = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    path = RECORDINGS / f"{rid}.webm"
    path.write_bytes(await audio.read())
    t0 = time.time()
    # 질문 문장을 음성 인식 힌트로 (Part 3·4·5). Part 1 지문은 발음 확인을 위해 넣지 않는다.
    context = " ".join(x for x in (item.get("intro"), item.get("q")) if x) if item["part"] in (3, 4, 5) else None
    tr = await asyncio.to_thread(stt.transcribe, str(path), context)
    metrics = fluency.analyze(tr["words"], item["resp"], item["part"], item.get("script"), item["max"])
    result = {"rid": rid, "item_id": item_id, "transcript": tr["text"], "words": tr["words"],
              "metrics": metrics, "stt_seconds": round(time.time() - t0, 1)}
    (RECORDINGS / f"{rid}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


class GradeReq(BaseModel):
    rid: str
    mode: str = "practice"


@app.post("/api/grade")
async def grade(req: GradeReq):
    p = RECORDINGS / f"{req.rid}.json"
    if not p.exists():
        raise HTTPException(404, "analysis not found")
    analysis = json.loads(p.read_text(encoding="utf-8"))
    item = content.get_item(analysis["item_id"])
    async with _grade_sem:
        try:
            res = await asyncio.to_thread(grader.grade, item, analysis)
        except Exception as e:
            raise HTTPException(500, str(e))
    with open(HISTORY / f"{datetime.now():%Y%m}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "mode": req.mode,
                            "item_id": item["id"], "part": item["part"], "rid": req.rid,
                            "transcript": analysis["transcript"], "metrics": analysis["metrics"],
                            "grade": res}, ensure_ascii=False) + "\n")
    return res


class TTSReq(BaseModel):
    text: str
    voice: str = "en-US-JennyNeural"


@app.post("/api/tts")
async def make_tts(req: TTSReq):
    if not req.text.strip():
        raise HTTPException(400, "empty text")
    try:
        key = await tts.synth(req.text, req.voice)
    except Exception as e:
        raise HTTPException(502, f"TTS 실패: {e}")
    return {"url": f"/api/tts/{key}.mp3"}


@app.get("/api/tts/{name}")
def get_tts(name: str):
    path = TTS_CACHE / name
    if not path.exists() or path.parent != TTS_CACHE:
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")


@app.get("/api/history")
def history(limit: int = 200):
    rows = []
    for f in sorted(HISTORY.glob("*.jsonl"), reverse=True):
        for line in reversed(f.read_text(encoding="utf-8").splitlines()):
            rows.append(json.loads(line))
            if len(rows) >= limit:
                return rows
    return rows


@app.post("/api/shutdown")
def shutdown():
    # 응답을 먼저 보내고 프로세스를 끝낸다
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


app.mount("/images", StaticFiles(directory=IMAGES), name="images")
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
