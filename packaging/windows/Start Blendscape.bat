@echo off
rem Double-click to run Blendscape: starts the local server and opens it in your browser.
rem Close this window (or press Ctrl+C) to stop it.
cd /d "%~dp0"
if not exist ".venv\Scripts\blendscape.exe" (
  echo Blendscape is not set up yet: running the one-time setup first.
  call Install.bat
  if not exist ".venv\Scripts\blendscape.exe" exit /b 1
)
if exist "tools\rubberband.exe" set "RUBBERBAND=%~dp0tools\rubberband.exe"
".venv\Scripts\blendscape.exe" serve --open
if errorlevel 1 pause
