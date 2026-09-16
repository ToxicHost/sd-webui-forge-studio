@echo off
setlocal
cd /d "%~dp0"
"app\runtime\python.exe" -I -B app\scripts\portable_runtime.py --verify
set "CHECK_RESULT=%ERRORLEVEL%"
pause
exit /b %CHECK_RESULT%
