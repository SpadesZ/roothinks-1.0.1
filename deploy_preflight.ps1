# Roothinks source maintenance contract
# 檔案路徑: deploy_preflight.ps1
# 模組定位: 部署前唯讀檢查層；在 GCP cutover 前驗證環境、檔案與資料恢復條件。
# 主要責任: 以 PowerShell 驗證部署必要檔案、Compose 設定與 SQLite 備份條件，輸出可供 cutover 判斷的唯讀結果。
# 上下游: 命令列參數/環境 -> 明確目標檔或 DB -> 可稽核輸出；不由一般 HTTP request 隱式觸發。
# 維護邊界: 任何資料變更都需明確目標、備份、idempotency 與失敗回滾；預設不得碰正式 data 或輸出秘密。
# 驗證: powershell -NoProfile -File deploy_preflight.ps1
Param(
    [string]$EnvFile = "env.prod",
    [switch]$AllowInsecureRedis,
    [switch]$NoDbDryRun
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$pythonCandidates = @(
    ".\.venv\Scripts\python.exe",
    "python",
    "py -3"
)

$pythonCmd = $null
foreach ($candidate in $pythonCandidates) {
    try {
        if ($candidate -eq ".\.venv\Scripts\python.exe") {
            if (Test-Path $candidate) {
                $pythonCmd = $candidate
                break
            }
        } else {
            & $candidate --version | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $pythonCmd = $candidate
                break
            }
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    Write-Error "No Python interpreter found. Please install Python or create .venv first."
    exit 2
}

$args = @("deploy_preflight.py", "--env-file", $EnvFile)
if ($AllowInsecureRedis) {
    $args += "--allow-insecure-redis"
}
if ($NoDbDryRun) {
    $args += "--no-db-dry-run"
}

if ($pythonCmd -eq "py -3") {
    & py -3 @args
} else {
    & $pythonCmd @args
}

exit $LASTEXITCODE
