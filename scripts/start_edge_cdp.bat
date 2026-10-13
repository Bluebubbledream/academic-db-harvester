@echo off
rem Double-click launcher: start Edge with CDP for the Academic DB Harvester
setlocal
set "PS1=%~dp0start_edge_cdp.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
echo.
pause
