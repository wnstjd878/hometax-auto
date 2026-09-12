#!/bin/bash
# 매달 쓰는 순서를 한 번에 돌린다 (맥·리눅스). 더블클릭 또는 ./run.command
# 크롬 띄우기 → 로그인 → invoices.json 전부 발행. 한 단계라도 막히면 거기서 멈춘다.
cd "$(dirname "$0")" || exit 1

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "파이썬을 찾지 못했으니 설치한 뒤에 다시 실행해 주십시오."
  exit 1
fi

if [ ! -f invoices.json ]; then
  echo "invoices.json 이 없습니다. invoices.sample.json 을 복사해 발행할 내용을 적어 주십시오."
  exit 1
fi

chmod +x launch_chrome.sh 2>/dev/null
./launch_chrome.sh || exit 1
"$PY" login.py || exit 1
"$PY" issue.py all invoices.json --card || exit 1
echo "끝났습니다. 승인번호는 위에 있습니다."
