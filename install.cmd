@echo off
rem One-time installer: registers `ow` and `openwhisper` as global commands
rem by adding the repo's scripts folder to your user PATH (HKCU\Environment).
rem Logic lives in scripts\install.ps1 so it can use PowerShell's safe
rem registry APIs instead of cmd's `setx`, which silently truncates PATH at
rem 1024 chars and can duplicate System PATH entries into User PATH.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
