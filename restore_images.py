"""Part 2 사진 복원 — data/images/CREDITS.txt 의 Unsplash 주소에서 빠진 사진을 다시 받는다.

백업에는 사진 파일 대신 출처 목록만 들어 있으므로, 새 PC 에서 start.bat 가 처음 실행될 때 호출된다.
"""
import re
import time
import urllib.request
from pathlib import Path

IMAGES = Path(__file__).resolve().parent / "data" / "images"


def main():
    credits = IMAGES / "CREDITS.txt"
    if not credits.exists():
        return
    # CREDITS.txt 형식:  p2_01.jpg  https://unsplash.com/photos/<id> ...
    #                       cdn: https://images.unsplash.com/...   ← 실제로 받는 주소 (unsplash.com 다운로드 페이지는
    #                                                               스크립트 요청을 401 로 막지만 이미지 CDN 은 열려 있다)
    name = None
    for line in credits.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"(p2_\d+\.jpg)\s", line)
        if m:
            name = m.group(1)
            continue
        m = re.match(r"\s+cdn:\s+(https://images\.unsplash\.com/\S+)", line)
        if not (m and name):
            continue
        out, url, name = IMAGES / name, m.group(1), None
        if out.exists() and out.stat().st_size > 0:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                out.write_bytes(r.read())
            print(f"  받음 {out.name}")
        except Exception as e:
            print(f"  실패 {out.name}: {e}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
