@echo off
setlocal
title Forge Studio Portable
cd /d "%~dp0"
set "PYTHONHOME="
set "PYTHONPATH="
set "GIT_PYTHON_REFRESH=quiet"
set "STUDIO_STATE_ROOT=%~dp0Studio-State"
if not exist "app\runtime\python.exe" (
  echo The private Studio runtime is missing. Extract the complete portable ZIP again.
  goto failed
)
"app\runtime\python.exe" -I -B start_studio.py --fast-fp16 %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
pause
exit /b 1
