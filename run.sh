#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "처음 실행: 가상환경 만들고 패키지 설치 중..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python -m dcrank "$@"
