@echo off
rem Builds backend\dist\backend\backend.exe (run from anywhere).
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo [ERROR] Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
  exit /b 1
)

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
