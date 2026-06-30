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
