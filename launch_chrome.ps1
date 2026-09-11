# 홈택스 전용 헤드풀 크롬 (Windows). 프로필 ~/.chrome-hometax, CDP 9260. 다른 크롬(9222 스레드 등)은 건드리지 않는다.
$port = 9260
$profile = Join-Path $env:USERPROFILE ".chrome-hometax"
New-Item -ItemType Directory -Force $profile | Out-Null
$listening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listening) { Write-Output "already listening on $port"; exit 0 }
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
Start-Process -FilePath $chrome -ArgumentList @(
  "--user-data-dir=$profile",
  "--remote-debugging-port=$port",
  "--no-first-run", "--no-default-browser-check",
  "--disable-blink-features=AutomationControlled",
  "--window-size=1440,1000",
  "https://hometax.go.kr"
)
for ($i = 0; $i -lt 20; $i++) {
  try { Invoke-RestMethod "http://127.0.0.1:$port/json/version" -TimeoutSec 2 | Out-Null; Write-Output "chrome up on $port"; exit 0 } catch { Start-Sleep 1 }
}
Write-Output "chrome did not come up"; exit 1
