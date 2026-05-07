Option Explicit

Dim shell, scriptPath, command

Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Dev\TradeBtc\polymarket-fast-loop-direct"

scriptPath = "C:\Dev\TradeBtc\polymarket-fast-loop-direct\scripts\run_live_taker_scheduled.ps1"
command = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File " & Chr(34) & scriptPath & Chr(34)

shell.Run command, 0, False
