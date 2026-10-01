@echo off
chcp 65001 > nul
cd /d "%~dp0"

set PY=
where py > nul 2>&1 && set PY=py -3
if not defined PY (
  python --version > nul 2>&1 && set PY=python
)
if not defined PY (
  echo Python이 없습니다. https://www.python.org/downloads/ 에서 설치하고
  echo 설치 첫 화면의 "Add python.exe to PATH"를 꼭 체크하세요.
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo 처음 실행: 가상환경 만드는 중...
  %PY% -m venv .venv || (echo 가상환경 생성 실패 & pause & exit /b 1)
)
if not exist .venv\installed.ok (
  echo 패키지 설치 중... 1~2분 걸립니다.
  .venv\Scripts\python -m pip install -q -r requirements.txt || (echo 패키지 설치 실패. 인터넷 연결을 확인하세요. & pause & exit /b 1)
  echo ok> .venv\installed.ok
)

.venv\Scripts\python -m dcrank
pause
