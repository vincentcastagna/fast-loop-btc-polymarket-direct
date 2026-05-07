$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
$logPath = Join-Path $logDir "direct_live_taker_task.log"
$lockPath = Join-Path $logDir "direct_live_taker.lock"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Add-LogLine {
    param([string]$Line)
    [System.IO.File]::AppendAllText($logPath, $Line + [Environment]::NewLine, $utf8NoBom)
}

try {
    New-Item -ItemType Directory -Path $lockPath -ErrorAction Stop | Out-Null
} catch {
    Add-LogLine "==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') skip: previous run still active ===="
    exit 0
}

function Invoke-DirectLiveCycle {
    param([string]$Label)
    Add-LogLine "==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') $Label ===="
    $output = & python -m direct_fastloop.main --live --yes-i-understand --mode taker 2>&1
    foreach ($line in $output) {
        Add-LogLine ([string]$line)
    }
    Add-LogLine "exit_code=$LASTEXITCODE"
}

try {
    Set-Location -Path $root
    $env:DIRECT_LIVE_CONFIRM = "YES"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"

    Invoke-DirectLiveCycle -Label "pass1"
    Start-Sleep -Seconds 30
    Invoke-DirectLiveCycle -Label "pass2"
} finally {
    Remove-Item -LiteralPath $lockPath -Recurse -Force -ErrorAction SilentlyContinue
}
