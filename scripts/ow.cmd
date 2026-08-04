@echo off
rem Short alias for `openwhisper`. Delegates to the main launcher in the same folder
rem so all logic stays in one place.
call "%~dp0openwhisper.cmd" %*
