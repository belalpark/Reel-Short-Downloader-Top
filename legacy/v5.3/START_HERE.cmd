@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 (
  echo ERROR: Could not open the tool folder.
  pause
  exit /b 20
)
title Drama Archiver v5 - Persistent Menu
if not exist "%~dp0video_archiver.ps1" (
  echo ERROR: video_archiver.ps1 is missing.
  echo Extract ALL files from ZIP before running.
  pause
  exit /b 21
)
:MAIN_MENU
echo.
echo ----------------------------------------------
echo       DRAMA ARCHIVER v5 - WINDOWS
echo ----------------------------------------------
echo Stays open after every action or error.
echo Select 0 in the tool to exit, or close this window.
echo.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0video_archiver.ps1"
set "RC=%ERRORLEVEL%"
if "%RC%"=="77" goto USER_EXIT
echo.
echo ----------------------------------------------
echo Previous operation exit code: %RC%
if not "%RC%"=="0" (
  echo If something failed, inspect Logs or the error above.
)
echo Returning to menu without closing this CMD window.
echo.
goto MAIN_MENU
:USER_EXIT
endlocal
exit /b 0
