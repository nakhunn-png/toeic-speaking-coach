"""창 없이 서버를 띄우고 브라우저를 연다. (.venv\\Scripts\\pythonw.exe launcher.pyw)

이미 실행 중이면 브라우저만 연다. 종료는 앱 화면의 '앱 종료' 버튼.
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# pythonw 에는 콘솔이 없어 stdout 이 None 이다 — 로그는 파일로
(ROOT / "data").mkdir(exist_ok=True)
_log = open(ROOT / "data" / "server.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = _log

from app.config import CFG  # noqa: E402

PORT = CFG["port"]
URL = f"http://localhost:{PORT}"


def running() -> bool:
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


if running():
    webbrowser.open(URL)
    sys.exit(0)


def open_when_ready():
    for _ in range(150):
        if running():
            webbrowser.open(URL)
            return
        time.sleep(0.2)


threading.Thread(target=open_when_ready, daemon=True).start()

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, log_config=None)
