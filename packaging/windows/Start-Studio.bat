@echo off
setlocal
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_ALWAYS_INSTALL="
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
title Forge Studio
cd /d "%~dp0"
set "STUDIO_STATE_ROOT=%~dp0Studio-State"
:: Preserve the existing launch default. This option can change generated pixels.
set "STUDIO_ARGS=--fast-fp16"

if exist "app\venv\Scripts\python.exe" goto existing_environment
if defined PYTHON goto configured_python
:: An unsupported PATH Python can hand setup to an installed supported version.
python -c "" >nul 2>nul
if not errorlevel 1 goto path_python
py -3 -c "" >nul 2>nul
if not errorlevel 1 goto python_launcher
echo Studio needs Python 3.11 to 3.13.
echo Install Python 3.13 with its launcher, or set PYTHON to a supported executable.
goto failed

:path_python
python "app\scripts\bootstrap_environment.py"
if errorlevel 1 goto failed
goto launch

:python_launcher
py -3 "app\scripts\bootstrap_environment.py"
if errorlevel 1 goto failed
goto launch

:configured_python
"%PYTHON%" "app\scripts\bootstrap_environment.py"
if errorlevel 1 goto failed
goto launch

:existing_environment
:: Also repairs an interrupted setup or newly required dependencies.
"app\venv\Scripts\python.exe" "app\scripts\bootstrap_environment.py" --quiet
if errorlevel 1 goto failed

:launch
"app\venv\Scripts\python.exe" start_studio.py %STUDIO_ARGS% %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
pause
exit /b 1
