#!/bin/bash
# 홈택스 전용 크롬을 띄운다. 프로필 ~/.chrome-hometax, 조종 포트 9260.
# 평소 쓰는 크롬은 건드리지 않으며, 이미 떠 있으면 그대로 쓴다.
PORT=9260
PROFILE="$HOME/.chrome-hometax"
mkdir -p "$PROFILE"

if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "already listening on $PORT"
  exit 0
fi

if [ "$(uname)" = "Darwin" ]; then
  open -na "Google Chrome" --args \
    --user-data-dir="$PROFILE" \
    --remote-debugging-port=$PORT \
    --no-first-run --no-default-browser-check \
    --disable-blink-features=AutomationControlled \
    --window-size=1440,1000 \
    "https://hometax.go.kr"
else
  CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
  if [ -z "$CHROME" ]; then
    echo "크롬을 찾지 못했으니 설치한 뒤에 다시 실행해 주십시오."
    exit 1
  fi
  "$CHROME" \
    --user-data-dir="$PROFILE" \
    --remote-debugging-port=$PORT \
    --no-first-run --no-default-browser-check \
    --disable-blink-features=AutomationControlled \
    --window-size=1440,1000 \
    "https://hometax.go.kr" >/dev/null 2>&1 &
fi

for _ in $(seq 1 20); do
  if curl -s "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "chrome up on $PORT"
    exit 0
  fi
  sleep 1
done
echo "chrome did not come up"
exit 1
