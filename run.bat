@echo off
chcp 65001 > nul
cd /d "%~dp0"
if not exist .venv (
  echo 처음 실행: 가상환경 만들고 패키지 설치 중...
  python -m venv .venv || (echo Python 3.10 이상을 먼저 설치하세요. & pause & exit /b 1)
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python -m dcrank
pause
