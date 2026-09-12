@echo off
chcp 65001 >nul
rem 매달 쓰는 순서를 한 번에 돌린다 (윈도우). 이 파일을 더블클릭하면 된다.
rem 크롬 띄우기 -> 로그인 -> invoices.json 전부 발행. 한 단계라도 막히면 거기서 멈춘다.
cd /d "%~dp0"

if not exist invoices.json (
  echo invoices.json 이 없습니다. invoices.sample.json 을 복사해 발행할 내용을 적어 주십시오.
  pause
  exit /b 1
)

powershell -ExecutionPolicy Bypass -File launch_chrome.ps1 || goto :fail
python login.py || goto :fail
python issue.py all invoices.json --card || goto :fail
echo 끝났습니다. 승인번호는 위에 있습니다.
pause
exit /b 0

:fail
echo.
echo 막혔습니다. 위에 찍힌 마지막 줄을 보고 원인을 확인해 주십시오.
echo 보안카드 인증이 거절됐다면 logs\card_fail.lock 파일이 생겼는지 먼저 보십시오.
pause
exit /b 1
