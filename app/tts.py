"""원어민 음성 — edge-tts (MS Edge 낭독 음성, 무료). 한 번 만든 음성은 파일로 캐시한다."""
import hashlib
import uuid

import edge_tts

from .config import TTS_CACHE


def key_for(text: str, voice: str) -> str:
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()


async def synth(text: str, voice: str) -> str:
    key = key_for(text, voice)
    path = TTS_CACHE / f"{key}.mp3"
    if not path.exists() or path.stat().st_size == 0:
        # 같은 문장을 동시에 요청해도 부딪히지 않게 임시 파일 이름을 요청마다 다르게 한다
        tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.part")
        try:
            await edge_tts.Communicate(text, voice).save(str(tmp))
            try:
                tmp.replace(path)
            except OSError:
                if not path.exists():
                    raise  # 다른 요청이 먼저 만들어 둔 게 아니면 진짜 실패
        finally:
            tmp.unlink(missing_ok=True)  # 실패하면 빈 조각 파일을 남기지 않는다
    return key
