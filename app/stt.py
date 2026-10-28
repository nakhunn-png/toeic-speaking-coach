"""음성 → 단어 단위 타임스탬프 (faster-whisper, 로컬 무료).

Whisper 는 기본적으로 um/uh 같은 필러를 지워버린다. 필러가 섞인 initial_prompt 를 주면
그 스타일을 따라가 필러를 남기는 경향이 있어 이를 이용한다.
"""
import os
import sys
import threading
from pathlib import Path


def _add_cuda_dlls():
    """pip 로 설치한 nvidia-cublas / nvidia-cudnn 의 DLL 을 찾을 수 있게 등록 (Windows)."""
    for sp in map(Path, sys.path):
        nv = sp / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


if os.name == "nt":
    _add_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402

from .config import CFG  # noqa: E402

FILLER_PROMPT = "Umm, let me think. Uh, so I, I mean,"

_model = None
_on_gpu = False
_lock = threading.Lock()


def _load_cpu():
    global _model, _on_gpu
    # GPU 를 못 쓰면 큰 모델은 CPU 에서 너무 느리다 — CPU 전용 대체 모델로
    _model = WhisperModel(CFG.get("whisper_cpu_fallback_model", "small.en"), device="cpu", compute_type="int8")
    _on_gpu = False


def _get_model():
    global _model, _on_gpu
    if _model is None:
        if CFG["whisper_device"] == "cpu":
            _model = WhisperModel(CFG["whisper_model"], device="cpu", compute_type=CFG["whisper_compute_type"])
        else:
            try:
                _model = WhisperModel(CFG["whisper_model"], device=CFG["whisper_device"],
                                      compute_type=CFG["whisper_compute_type"])
                _on_gpu = True
            except Exception as e:
                print(f"[stt] GPU 로딩 실패 → CPU 대체: {e}")
                _load_cpu()
    return _model


def build_prompt(context: str | None = None) -> str:
    """질문(상황 안내 + 질문 문장)을 앞에 붙여 주제 단어를 잘 알아듣게 하고, 필러 예시는 뒤에 짧게 둔다.
    Part 1 은 지문을 넣지 않는다 — 넣으면 잘못 읽어도 지문대로 받아적어 발음 확인이 무의미해진다."""
    return f"{context} {FILLER_PROMPT}" if context else FILLER_PROMPT


def _run(model, path, prompt):
    segments, _info = model.transcribe(
        path,
        language="en",
        word_timestamps=True,
        initial_prompt=prompt,
        condition_on_previous_text=False,
        vad_filter=False,
        beam_size=5,
        # 기본값은 확신이 낮으면 무작위 재시도(temperature fallback)를 해서 같은 녹음도 결과가 달라졌다
        temperature=0.0,
    )
    return list(segments)  # 제너레이터라 여기서 실제 추론이 돈다


def transcribe(path: str, context: str | None = None) -> dict:
    prompt = build_prompt(context)
    with _lock:  # 모델 하나를 여러 요청이 동시에 쓰지 않게
        model = _get_model()
        try:
            segments = _run(model, path, prompt)
        except Exception as e:  # cuDNN 누락 등은 로딩이 아니라 첫 추론에서 터진다
            if not _on_gpu:
                raise
            print(f"[stt] GPU 추론 실패 → CPU 대체: {e}")
            _load_cpu()
            segments = _run(_model, path, prompt)
        words = []
        for seg in segments:
            # 무음 구간 환각("Thank you." 등) 제거
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.8:
                continue
            for w in seg.words or []:
                t = w.word.strip()
                if t:
                    words.append({"w": t, "s": round(w.start, 2), "e": round(w.end, 2),
                                  "p": round(w.probability, 2)})
    return {"text": " ".join(w["w"] for w in words), "words": words}


def warmup():
    _get_model()
