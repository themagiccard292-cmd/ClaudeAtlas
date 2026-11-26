@echo off
rem ===========================================================================
rem  Claude OS Agent - one-click Windows build
rem
rem  Produces (in .\release):
rem     Claude-OS-Agent-Setup.exe      NSIS installer
rem     Claude-OS-Agent-Portable.exe   single-file portable build
rem
rem  Build machine needs: Windows 10/11 x64, Node.js 22+, Python 3.10+ (both on PATH).
rem  The END USER needs neither - nor an API key (they sign in with Claude Pro via Claude Code).
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Claude OS Agent - build

echo.
echo ============================================================
echo   Claude OS Agent - Windows build
echo ============================================================

rem ---- prerequisites -------------------------------------------------------
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js was not found. Install Node 22 or newer from https://nodejs.org/ and re-run.
  goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm was not found on PATH.
  goto :fail
)
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3.10+ was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
  goto :fail
)

rem ---- 1/4  Python sidecar -> backend\dist\backend\backend.exe ---------------
echo.
echo [1/4] Building the OS-control engine ^(backend.exe^) with PyInstaller...
call "%~dp0backend\build_backend.bat"
if errorlevel 1 goto :fail

rem ---- 2/4  icons (only if missing) ---------------------------------------------
if not exist "build\icon.ico" (
  echo.
  echo [2/4] Generating app icons...
  "backend\.venv\Scripts\python.exe" scripts\make_icons.py
  if errorlevel 1 goto :fail
) else (
  echo.
  echo [2/4] Icons present.
)

rem ---- 3/4  JavaScript dependencies -----------------------------------------------
echo.
echo [3/4] Installing JavaScript dependencies...
if exist package-lock.json (
  call npm ci
) else (
  call npm install
)
if errorlevel 1 goto :fail

rem ---- 4/4  React UI + Electron + installer ------------------------------------
echo.
echo [4/4] Building the UI and packaging the installer ^(electron-builder^)...
set "CSC_IDENTITY_AUTO_DISCOVERY=false"
call npm run dist
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   SUCCESS. Output in: %CD%\release
echo ============================================================
dir /b "release\*.exe" 2>nul
echo.
echo Installer : release\Claude-OS-Agent-Setup.exe
echo Portable  : release\Claude-OS-Agent-Portable.exe
echo.
echo On first launch the app asks the user to sign in with Claude Pro ^(via Claude Code^).
endlocal
exit /b 0

:fail
echo.
echo ============================================================
echo   BUILD FAILED - see the messages above.
echo   Tip: if electron-builder reports "Cannot create symbolic link", enable
echo        Windows Developer Mode ^(Settings ^> For developers^) or run this as Administrator.
echo ============================================================
endlocal
exit /b 1
