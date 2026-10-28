# 토익스피킹 코치 (개인용 · 무료)

## 새 PC에 설치하기 (백업에서 복원)
1. **Python 3.12** 설치 — https://www.python.org/downloads/ (설치 화면에서 "Add python.exe to PATH" 체크)
2. **Claude Code** 설치 후 로그인 — PowerShell 에서:
   ```
   irm https://claude.ai/install.ps1 | iex
   claude
   ```
   `claude` 화면에서 `/login` → 구독 계정(Claude account with subscription) 선택 → 브라우저에서 승인 → `/exit`
3. 백업 폴더를 원하는 곳에 풀고 **`start.bat` 더블클릭**
   - 처음 한 번: 프로그램 설치(10분 내외) → Part 2 사진 자동 다운로드 → 바탕화면 아이콘 생성 → 앱 실행
   - 첫 채점 때 음성 인식 모델(약 3GB)을 자동으로 받는다
4. NVIDIA 그래픽카드가 없는 PC 는 자동으로 CPU 방식으로 동작한다 (조금 느림)

## 실행 / 종료
- 실행: 바탕화면 **'토익스피킹 코치'** 아이콘 (또는 `start.bat`) → 창 없이 서버가 뜨고 브라우저가 열린다
- 종료: 앱 오른쪽 위 **'⏻ 앱 종료'** (브라우저 탭만 닫으면 서버는 뒤에서 계속 돈다)
- 서버 로그: `data/server.log`

## 구성 (전부 무료, PC에서 실행)
| 역할 | 도구 |
|---|---|
| 음성 인식 + 더듬기·멈춤 측정 | faster-whisper large-v3, 그래픽카드 (`app/stt.py`, `app/fluency.py`) |
| 채점·피드백 | Claude Code 헤드리스 `claude -p` 경량 실행 (`app/grader.py`) — 구독 사용량 차감, 문항당 약 11~16초 |
| 원어민 음성 | edge-tts (`app/tts.py`) — `data/tts_cache/` 에 캐시 |

## 고칠 수 있는 파일
- `data/templates.json` — 만능문장 라이브러리 (채점 AI가 이 문장을 우선 사용)
- `data/questions.json` — 문제 은행 (Part 1·3·4·5)
- `data/images/` — 사진을 넣으면 Part 2 문제가 자동 생성 (파일 이름 순 2장 = 1세트). 출처는 `CREDITS.txt`
- `config.json` — 음성 인식: `large-v3` + 그래픽카드(`cuda`). GPU 가 안 되면 자동으로
  CPU + `small.en` 으로 대체. 강제로 CPU 를 쓰려면 `whisper_model: "small.en"`, `whisper_device: "cpu"`, `whisper_compute_type: "int8"`.

## 더듬기·지연 반영 방식
`app/fluency.py` 가 시작 지연, 2초 이상 멈춤, 필러 비율, 반복, 말하기 속도, 시간 활용률로 감점 포인트를 계산하고,
점수 상한(cap)을 정한다. AI가 내용만 보고 높은 점수를 줘도 이 상한을 넘을 수 없다.
문항별 등급(AL~NL)도 점수별 허용 범위 안에서만 나온다.

## 백업에 들어가지 않는 것
`.venv`(설치 시 재생성), `data/recordings`·`data/history`(개인 녹음·기록), `data/tts_cache`(자동 재생성), Part 2 사진 파일(첫 실행 때 자동 다운로드).
