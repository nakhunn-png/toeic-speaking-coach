@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [오류] Python 이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.12 를 설치하세요. 설치 화면에서 "Add python.exe to PATH" 를 꼭 체크하세요.
    pause
    exit /b 1
  )
  echo [설치] 처음 실행이라 필요한 프로그램을 설치합니다. 10분 정도 걸릴 수 있습니다...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

rem Part 2 사진이 없으면 출처 목록(CREDITS.txt)에서 다시 받는다 (백업에는 사진 파일이 없음)
if not exist "data\images\p2_01.jpg" (
  echo [복원] Part 2 사진을 받는 중...
  ".venv\Scripts\python.exe" restore_images.py
)

rem 바탕화면 아이콘이 없으면 만든다
powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop'); $l=Join-Path $d '토익스피킹 코치.lnk'; if(-not(Test-Path $l)){ $s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); $s.TargetPath=(Resolve-Path '.venv\Scripts\pythonw.exe').Path; $s.Arguments='\"'+(Resolve-Path 'launcher.pyw').Path+'\"'; $s.WorkingDirectory=(Get-Location).Path; $s.Save(); Write-Host '[완료] 바탕화면에 토익스피킹 코치 아이콘을 만들었습니다.' }"

rem 창 없이 실행 — 종료는 앱 화면 오른쪽 위 '앱 종료' 버튼
start "" ".venv\Scripts\pythonw.exe" launcher.pyw
