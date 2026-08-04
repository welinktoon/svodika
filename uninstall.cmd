@echo off
rem Removes `ow` and `openwhisper` from your user PATH. Does not delete the
rem venv, source code, or scripts/ folder -- only edits HKCU\Environment\Path.
rem Logic lives in scripts\uninstall.ps1.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\uninstall.ps1"
