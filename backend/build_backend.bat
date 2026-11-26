@echo off
rem Builds backend\dist\backend\backend.exe (run from anywhere).
rem Python: set PY_CMD to force one (CI does); otherwise prefers 3.12 / 3.11 / 3.13. Python 3.14+ is NOT supported yet
rem (some dependencies, e.g. onnxruntime for faster-whisper, publish no wheels for it).
setlocal
cd /d "%~dp0"

set "PY="
if defined PY_CMD set "PY=%PY_CMD%"
if not defined PY (
  for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
      py -%%V -c "import sys" >nul 2>nul
      if not errorlevel 1 set "PY=py -%%V"
    )
  )
)
if not defined PY (
  where python >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3.10-3.13 was not found. Install Python 3.12 from https://www.python.org/downloads/ and tick "Add to PATH".
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)"
if errorlevel 1 (
  echo [ERROR] Unsupported Python version:
  %PY% --version
  echo Use Python 3.10-3.13 ^(3.12 recommended^). Set PY_CMD to pick one, e.g.  set PY_CMD=py -3.12
  exit /b 1
)
echo [backend] Using:
%PY% --version

if exist ".venv" if not exist ".venv\Scripts\python.exe" rmdir /s /q ".venv"
if not exist ".venv\Scripts\python.exe" (
  echo [backend] Creating virtual environment...
  %PY% -m venv .venv || exit /b 1
)
call ".venv\Scripts\activate.bat" || exit /b 1

echo [backend] Installing dependencies...
python -m pip install --upgrade pip || exit /b 1
python -m pip install -r requirements.txt pyinstaller || exit /b 1

echo [backend] Running PyInstaller...
pyinstaller --noconfirm --clean --distpath dist --workpath build backend.spec || exit /b 1

if not exist "dist\backend\backend.exe" (
  echo [ERROR] dist\backend\backend.exe was not produced.
  exit /b 1
)
echo [backend] OK: %CD%\dist\backend\backend.exe
endlocal
