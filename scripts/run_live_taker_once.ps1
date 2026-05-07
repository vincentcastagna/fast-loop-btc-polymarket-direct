$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)
$env:DIRECT_LIVE_CONFIRM = "YES"
python -m direct_fastloop.main --live --yes-i-understand --mode taker

